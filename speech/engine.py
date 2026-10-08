"""CosyVoice 3 adapter. Import only in the isolated speech environment."""

import io
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import sys
import time
import warnings

from rag.config import ROOT

PROMPT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'


class SpeechEngine:
    def __init__(self, *, fp16: bool = True, quality_tuned: bool = True, cuda_graph: bool = True):
        os.environ.update({'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                           'DO_NOT_TRACK': '1', 'TQDM_DISABLE': '1'})
        # Upstream logs synthesis text even at WARNING for short input. Disable
        # its logging before importing it; application errors use fixed messages.
        logging.disable(logging.CRITICAL)
        warnings.filterwarnings('ignore', category=FutureWarning)
        runtime = ROOT / 'models/CosyVoice-runtime'
        sys.path.insert(0, str(runtime))
        sys.path.insert(0, str(runtime / 'third_party/Matcha-TTS'))
        import torch
        import soundfile
        import torchaudio
        import cosyvoice.utils.file_utils as file_utils

        if not torch.cuda.is_available():
            raise RuntimeError('Speech synthesis requires host GPU access.')
        torch.set_num_threads(4)
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

        # Preserve the upstream soundfile loader on Torchaudio 2.10, which
        # otherwise ignores backend='soundfile' and requires TorchCodec.
        def load_wav(path: str | Path, target_sr: int, min_sr: int = 16000):
            data, rate = soundfile.read(path, dtype='float32', always_2d=True)
            data = torch.from_numpy(data.T.copy()).mean(dim=0, keepdim=True)
            if rate != target_sr:
                if rate < min_sr:
                    raise ValueError('Reference sample rate is too low.')
                data = torchaudio.functional.resample(data, rate, target_sr)
            return data

        file_utils.load_wav = load_wav
        from cosyvoice.cli.cosyvoice import CosyVoice3
        self.torch = torch
        self.soundfile = soundfile
        import wetext
        rules = json.loads((ROOT / 'config/models.lock.json').read_text())['speech']['normalizer']
        rule_dir = ROOT / rules['local_path']
        for name, digest in rules['sha256'].items():
            if hashlib.sha256((rule_dir / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError('Speech normalization rules failed verification.')
        # WeText's constructor otherwise downloads an unpinned snapshot even
        # in offline mode. Resolve only its pinned local rule files at startup.
        normalizer_module = importlib.import_module(wetext.Normalizer.__module__)
        original_download = normalizer_module.snapshot_download
        def local_rules(repo_id):
            if repo_id != rules['repo_id']:
                raise RuntimeError('Unexpected normalization resource.')
            return str(rule_dir)
        normalizer_module.snapshot_download = local_rules
        try:
            self.model = CosyVoice3(str(ROOT / 'models/cosyvoice'), fp16=fp16)
        finally:
            normalizer_module.snapshot_download = original_download
        if self.model.frontend.text_frontend != 'wetext':
            raise RuntimeError('Pinned speech normalization rules could not be loaded.')
        if quality_tuned:
            weights = torch.load(ROOT / 'models/cosyvoice/llm.rl.pt', map_location='cpu', weights_only=True)
            self.model.model.llm.load_state_dict(weights, strict=True)
            del weights
        self.model.add_zero_shot_spk(PROMPT, str(runtime / 'asset/zero_shot_prompt.wav'), 'course')
        self.sample_rate = self.model.sample_rate
        self._token_failure = False
        original_job = self.model.model.llm_job

        def guarded_job(*args, **kwargs):
            try:
                original_job(*args, **kwargs)
            except Exception:
                # The upstream token generator runs in a thread. Do not let a
                # traceback expose text or leave the request waiting forever.
                self._token_failure = True
                request_id = kwargs.get('uuid', args[-1] if args else None)
                self.model.model.llm_end_dict[request_id] = True

        self.model.model.llm_job = guarded_job
        if cuda_graph:
            from speech.cuda_graph import CudaGraphDecoder
            decoder = CudaGraphDecoder(self.model.model.llm.llm)
            decoder.validate()
            self.model.model.llm.llm.forward_one_step = decoder

    def synthesize(self, text: str) -> tuple[bytes, float, float]:
        started = time.perf_counter()
        self._token_failure = False
        outputs = self.model.inference_zero_shot(text, '', '', zero_shot_spk_id='course', stream=False, speed=1.0)
        try:
            parts = [part['tts_speech'].float().cpu() for part in outputs]
        finally:
            for name in ('tts_speech_token_dict', 'llm_end_dict', 'hift_cache_dict',
                         'mel_overlap_dict', 'flow_cache_dict'):
                getattr(self.model.model, name, {}).clear()
        if self._token_failure:
            raise RuntimeError('Speech token generation failed.')
        if not parts:
            raise RuntimeError('Speech synthesis produced no audio.')
        samples = self.torch.cat(parts, dim=1).numpy().reshape(-1)
        if not self.torch.isfinite(self.torch.from_numpy(samples)).all():
            raise RuntimeError('Speech synthesis produced invalid samples.')
        buffer = io.BytesIO()
        self.soundfile.write(buffer, samples, self.sample_rate, format='WAV', subtype='PCM_16')
        return buffer.getvalue(), time.perf_counter() - started, len(samples) / self.sample_rate
