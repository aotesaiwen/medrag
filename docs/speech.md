# Local speech playback

[Back to README](../README.md)

Speech is optional. The local worker uses **Fun-CosyVoice3-0.5B-2512**, the RL checkpoint, FP16 inference, and 24 kHz mono PCM WAV output. Speech text stays on the host.

## Install

From the repository root, install the separate speech environment and pinned runtime:

```bash
uv venv --python 3.10.12 .venv-tts
uv pip install --python .venv-tts/bin/python -r requirements-speech.lock --index-strategy unsafe-best-match
git clone https://github.com/FunAudioLLM/CosyVoice.git models/CosyVoice-runtime
git -C models/CosyVoice-runtime checkout 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
git -C models/CosyVoice-runtime submodule update --init --recursive
.venv/bin/python scripts/download_models.py speech
.venv/bin/python scripts/download_speech_rules.py
```

Skip the clone if that checkout already exists. Model, runtime, Matcha, and normalization-rule revisions are recorded in [config/models.lock.json](../config/models.lock.json). The worker checks normalization-rule checksums and warms the model before accepting requests. Serving performs no downloads. The reference voice comes from the pinned runtime's `asset/zero_shot_prompt.wav`.

## Start and use

```bash
.venv/bin/python scripts/services.py start speech
.venv/bin/python scripts/services.py status speech
curl --silent http://127.0.0.1:8104/health
```

`start serving` does not include speech. `stop all` does include it. To stop just speech:

```bash
.venv/bin/python scripts/services.py stop speech
```

In the browser, click the speaker button on a completed answer. Select part of an answer to listen only to that passage. Pause, resume, stop, cancel, and replay controls appear as needed. Only one answer plays at a time; switching chats or disconnecting stops playback.

Audio plays after the complete result is ready. Long passages take longer; after ten seconds the interface displays “Still preparing…”. Answers are not shortened or sped up to fit a time limit. Cancelling stops the browser waiting, but an active synthesis can finish on the worker.

The browser keeps up to eight audio results, bounded to 64 MiB total, in page memory for replay. Leaving the chat discards them. Ordinary requests do not save audio in session storage or on disk.

## Endpoints and limits

The browser calls authenticated `POST /speech` on the application API. The gateway calls the worker's authenticated `POST /synthesize` on port 8104 using the same `API_KEY`. Worker `GET /health` is public. Audio responses use `Cache-Control: no-store`.

- Maximum request text: 6,000 characters.
- Maximum WAV response accepted by the gateway: 64 MiB.
- Default request deadline: 600 seconds, set by `SPEECH_TIMEOUT`.
- Concurrent synthesis requests receive a retryable busy response.

`SPEECH_URL` changes the gateway's destination; the provided worker launcher binds port 8104.

## Measure performance

The benchmark loads its own model, so stop a running speech worker first. It uses synthetic passages and writes timings to `data/processed/speech_benchmark.json`:

```bash
.venv/bin/python scripts/services.py stop speech
.venv-tts/bin/python scripts/benchmark_speech.py
.venv-tts/bin/python scripts/benchmark_speech.py --eager --output data/processed/speech_benchmark_eager.json
```

Restart the worker afterward if needed. Compare complete synthesis time separately from cold startup and warmup, and listen to representative course answers to assess pronunciation.

The CUDA graph adapter in [speech/cuda_graph.py](../speech/cuda_graph.py) accelerates the text decoder while retaining the checkpoint, sampling algorithm, and acoustic renderer. Startup compares graph and eager hidden states before enabling it; sequences exceeding the static cache use the original decoder. `--eager` benchmarks without that adapter. See [development checks](development.md#tests) for unit and live browser tests.
