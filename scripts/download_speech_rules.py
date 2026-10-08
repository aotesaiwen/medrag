"""Fetch only commit-pinned, checksum-verified text normalization rules."""
import hashlib
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    rules = json.loads((ROOT / 'config/models.lock.json').read_text())['speech']['normalizer']
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for name, digest in rules['sha256'].items():
            path = ROOT / rules['local_path'] / name
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                continue
            response = client.get(f"https://modelscope.cn/api/v1/models/{rules['repo_id']}/repo",
                                  params={'Revision': rules['revision'], 'FilePath': name})
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != digest:
                raise RuntimeError(f'Checksum mismatch for {name}')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.download')
            temporary.write_bytes(response.content)
            temporary.replace(path)
    print('Speech normalization rules ready.')


if __name__ == '__main__':
    main()
