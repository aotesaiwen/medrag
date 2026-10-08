"""Measure complete local synthesis using synthetic passages, never conversation data."""

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES = [
    '个人数据保护的核心是尊重个人的自主权。收集和使用信息之前，需要明确处理目的，遵循最小必要原则，并采取适当的安全措施。数据主体可以了解自己的信息如何被使用，也可以在符合法定条件时申请删除。',
    '删除权并不是绝对的。根据通用数据保护条例第十七条，如果个人数据已经不再是实现收集目的所必需的，或者个人撤回了同意且不存在其他法律依据，可以请求删除数据。控制者通常应当及时处理这类请求。不过，在履行法定义务、保护公共卫生、进行符合要求的科学研究，或者提出和维护法律请求时，也可能存在保留数据的例外。判断时需要同时查看删除条件和例外规定，不能仅仅因为收到请求就立即删除全部记录。医疗场景尤其需要考虑病历保存义务。',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--base', action='store_true', help='Benchmark the base instead of RL checkpoint')
    parser.add_argument('--eager', action='store_true', help='Disable CUDA graph acceleration')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/processed/speech_benchmark.json')
    args = parser.parse_args()
    from speech.engine import SpeechEngine
    started = time.perf_counter()
    engine = SpeechEngine(quality_tuned=not args.base, cuda_graph=not args.eager)
    engine.synthesize('你好，语音服务已经准备就绪。')
    report = {'model': 'Fun-CosyVoice3-0.5B-2512', 'checkpoint': 'base' if args.base else 'RL',
              'precision': 'FP16', 'decoder_steps': 10, 'sample_rate': 24000, 'cuda_graph': not args.eager,
              'load_and_warmup_seconds': round(time.perf_counter() - started, 3), 'measurements': []}
    print(json.dumps({key: value for key, value in report.items() if key != 'measurements'}), flush=True)
    for index, text in enumerate(SAMPLES):
        for repeat in range(args.repeats):
            audio, elapsed, duration = engine.synthesize(text)
            record = {'sample': index, 'repeat': repeat + 1, 'characters': len(text),
                      'synthesis_seconds': round(elapsed, 3), 'audio_seconds': round(duration, 3),
                      'rtf': round(elapsed / duration, 4), 'within_ten_seconds': elapsed <= 10}
            report['measurements'].append(record)
            print(json.dumps(record), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
