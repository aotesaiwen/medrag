# Installation

[Back to README](../README.md)

Run these commands from the repository root on the GPU host. See the [requirements](../README.md#requirements) before starting. Downloads require network access and substantial disk space; keep the downloaded `models/` directory for later runs.

## Install the environments

```bash
uv sync --frozen --dev --python 3.10.12
source .venv/bin/activate
uv venv --python 3.10.12 .venv-gpu
uv pip install --python .venv-gpu/bin/python -r requirements-gpu.lock
uv venv --python 3.10.12 .venv-parser
uv pip install --python .venv-parser/bin/python -r requirements-parser.lock
npm --prefix frontend ci
npm --prefix frontend run build
```

The application, GPU serving, and PDF parser use separate environments because their dependency requirements differ. Keep `.venv` activated for the commands below; the launch scripts select the other environments automatically. Dependencies are pinned in [uv.lock](../uv.lock), [requirements-gpu.lock](../requirements-gpu.lock), and [requirements-parser.lock](../requirements-parser.lock).

The API serves the built frontend itself. Node is needed for building and development, but no Node process is required to serve the app.

## Configure keys

Create `.env` from the template if it does not exist:

```bash
python - <<'PYCONFIG'
from pathlib import Path

path = Path('.env')
if not path.exists():
    path.write_text(Path('.env.example').read_text())
    path.chmod(0o600)
PYCONFIG
```

Edit `.env` and set the three hosted-model fields:

| Setting | Value |
| --- | --- |
| `LLM_BASE_URL` | Your DeepSeek OpenAI-compatible API base URL. |
| `LLM_MODEL` | The model name accepted by that endpoint. |
| `LLM_API_KEY` | Your DeepSeek API credential. |

Generate the separate local application key. This preserves an existing key and does not print it:

```bash
python - <<'PYKEY'
from pathlib import Path
import secrets
from dotenv import dotenv_values

path = Path('.env')
if not dotenv_values(path).get('API_KEY'):
    lines = [line for line in path.read_text().splitlines()
             if not line.startswith('API_KEY=')]
    path.write_text('\n'.join([*lines, 'API_KEY=' + secrets.token_urlsafe(32)]) + '\n')
path.chmod(0o600)
PYKEY
```

Enter this `API_KEY` when connecting in the browser. Without it, protected endpoints reject requests. Without the `LLM_*` settings, retrieval without history still works, but answer generation and follow-up rewriting do not. See the [configuration reference](operations.md#configuration) for other settings.

## Download models and build the corpus

```bash
python scripts/download_models.py
mkdir -p data/raw/slides
```

The downloader uses the revisions and local paths in [config/models.lock.json](../config/models.lock.json). Serving loads these downloaded files offline. Optional speech models are installed separately using the [speech guide](speech.md).

Copy your lecture PDFs into `data/raw/slides/`. Each filename must contain a unique lecture number, for example `Lecture 6.pdf`. The number determines its source IDs and citation labels. The build requires at least one PDF.

```bash
python scripts/build.py
```

The build downloads legal sources, parses the PDFs with MinerU, describes slide images, assembles the corpus, and updates Qdrant. The GPU stages run in sequence; a successful build leaves the API, Qdrant, embedding model, and reranker running.

```bash
curl --silent http://127.0.0.1:8000/health
```

Wait for `"ready": true`, then [open and use the app](../README.md#start-the-app). If setup fails, check [troubleshooting](operations.md#troubleshooting) and the service logs in `logs/`.
