"""Own one MinerU process for a batch, then release its GPU before the VLM stage."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from rag.config import Settings


@contextmanager
def mineru_api(settings: Settings):
    from scripts.services import OWNER, owned_process, process_start, record_path
    if owned_process("parser"):
        raise RuntimeError("Another project parser batch is already running")
    lock = json.loads(settings.model_lock_path.read_text())
    cache = ROOT / "data/cache/slides"
    cache.mkdir(parents=True, exist_ok=True)
    config = cache / "mineru.json"
    config.write_text(json.dumps({"config_version": "1.3.2", "model-source": "local", "models-dir": {
        "vlm": str(ROOT / lock["mineru"]["local_path"]),
        "pipeline": str(ROOT / lock["pipeline"]["local_path"]),
    }}, indent=2) + "\n")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.update({
        "MINERU_MODEL_SOURCE": "local", "MINERU_TOOLS_CONFIG_JSON": str(config),
        "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1",
        "TOKENIZERS_PARALLELISM": "false", "PYTHONHASHSEED": "0",
        "MINERU_FORMULA_CH_SUPPORT": "false", "MINERU_API_OUTPUT_ROOT": str(cache / "api"),
        "MINERU_API_MAX_CONCURRENT_REQUESTS": "1", "MINERU_API_DISABLE_ACCESS_LOG": "1",
    })
    (ROOT / "logs").mkdir(exist_ok=True)
    with (ROOT / "logs/mineru-api.log").open("ab") as log:
        process = subprocess.Popen([
            str(ROOT / ".venv-parser/bin/mineru-api"), "--host", "127.0.0.1",
            "--port", str(port), "--gpu-memory-utilization", "0.5",
        ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        (ROOT / "run").mkdir(exist_ok=True)
        record_path("parser").write_text(json.dumps({
            "owner": OWNER, "pid": process.pid, "start": process_start(process.pid), "port": port,
        }))
        deadline = time.monotonic() + 120
        with httpx.Client(timeout=3, trust_env=False) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("MinerU exited; inspect logs/mineru-api.log")
                try:
                    if client.get(url + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("MinerU API did not become ready; inspect logs/mineru-api.log")
        print(f"MinerU batch API ready at {url}", flush=True)
        yield url
    finally:
        # This process group was created above; never target an unrelated model.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)


def main() -> None:
    settings = Settings.load()
    lock = json.loads(settings.model_lock_path.read_text())
    from scripts.services import owned_process
    if any(owned_process(name) for name in ("vlm", "embedding", "reranker", "speech")):
        raise SystemExit("Stop this project's GPU model services before parsing slides")
    with mineru_api(settings) as url:
        subprocess.run([
            str(ROOT / ".venv/bin/python"), "-m", "ingest", "parse-slides",
            "--mineru-model-dir", str(ROOT / lock["mineru"]["local_path"]),
            "--revision", lock["mineru"]["revision"],
            "--pipeline-model-dir", str(ROOT / lock["pipeline"]["local_path"]),
            "--pipeline-revision", lock["pipeline"]["revision"],
            "--mineru-executable", str(ROOT / ".venv-parser/bin/mineru"),
            "--mineru-api-url", url,
        ], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
