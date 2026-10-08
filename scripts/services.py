"""Manage only the services started by this project, with persistent ownership records."""

import argparse
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

QDRANT_IMAGE = "qdrant/qdrant@sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10"
CONTAINER = "course-rag-qdrant"
OWNER = str(ROOT)
SERVICES = ("qdrant", "embedding", "reranker", "vlm", "speech", "api")


def process_start(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def record_path(name: str) -> Path:
    return ROOT / "run" / f"{name}.json"


def owned_process(name: str) -> dict | None:
    path = record_path(name)
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if record.get("owner") != OWNER:
        raise RuntimeError(f"Refusing foreign service record: {path}")
    observed_start = process_start(record["pid"])
    if record["start"] is None or observed_start is None or observed_start != record["start"]:
        return None
    return record


def port_available(port: int) -> bool:
    with socket.socket() as sock:
        # Match Uvicorn's bind behavior after a graceful restart (TIME_WAIT).
        # Active listeners still prevent binding; SO_REUSEPORT is never set.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def docker_state() -> dict | None:
    result = subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, text=True)
    if result.returncode:
        if "no such" in result.stderr.lower():
            return None
        raise RuntimeError("Docker inspection failed; check host Docker access")
    state = json.loads(result.stdout)[0]
    if state["Config"].get("Labels", {}).get("course-rag.owner") != OWNER:
        raise RuntimeError(f"Refusing to manage pre-existing unowned container {CONTAINER}")
    return state


def command(name: str, settings: Settings) -> tuple[list[str], int, str]:
    if name == "api":
        return [str(ROOT / ".venv/bin/python"), "-m", "api.main"], settings.api_port, "/health"
    if name == "speech":
        required = ["cosyvoice3.yaml", "llm.pt", "llm.rl.pt", "flow.pt", "hift.pt",
                    "campplus.onnx", "speech_tokenizer_v3.onnx", "CosyVoice-BlankEN/model.safetensors"]
        if (not (ROOT / ".venv-tts/bin/python").is_file()
                or not (ROOT / "models/CosyVoice-runtime/cosyvoice/cli/cosyvoice.py").is_file()
                or any(not (ROOT / "models/cosyvoice" / path).is_file() for path in required)):
            raise RuntimeError("Speech is not installed; see the README speech setup instructions")
        return [str(ROOT / ".venv-tts/bin/python"), "-m", "speech.server"], 8104, "/health"
    lock = json.loads(settings.model_lock_path.read_text())
    model = lock[name]
    model_path = ROOT / model["local_path"]
    if not (model_path / "config.json").exists() or not list(model_path.glob("*.safetensors")):
        raise RuntimeError(f"Model {name} is not downloaded; run scripts/download_models.py")
    index_path = model_path / "model.safetensors.index.json"
    if index_path.exists():
        shards = set(json.loads(index_path.read_text())["weight_map"].values())
        if any(not (model_path / shard).is_file() for shard in shards):
            raise RuntimeError(f"Model {name} is still downloading; wait for all weight shards")
    elif list(model_path.glob("model-*.safetensors")):
        raise RuntimeError(f"Model {name} is still downloading; its weight index is missing")
    port = {"embedding": 8101, "reranker": 8102, "vlm": 8103}[name]
    args = [str(ROOT / ".venv-gpu/bin/vllm"), "serve", str(model_path),
            "--served-model-name", model["repo_id"], "--revision", model["revision"],
            "--dtype", "bfloat16", "--host", "127.0.0.1", "--port", str(port),
            "--seed", "0", "--enforce-eager", "--no-enable-log-requests", "--disable-log-stats",
            "--no-enable-log-outputs", "--disable-uvicorn-access-log",
            "--gpu-memory-utilization", "0.90" if name == "vlm" else "0.40",
            "--max-model-len", "16384" if name == "vlm" else "32768",
            "--max-num-seqs", "16" if name == "vlm" else "4",
            "--max-num-batched-tokens", "32768"]
    if name in {"embedding", "reranker"}:
        args.extend(["--runner", "pooling"])
    if name == "reranker":
        args.extend(["--hf-overrides", json.dumps({
            "architectures": ["Qwen3ForSequenceClassification"],
            "classifier_from_token": ["no", "yes"], "is_original_qwen3_reranker": True,
        }), "--chat-template", str(ROOT / "config/qwen3_reranker.jinja")])
    if name == "vlm":
        args.extend(["--limit-mm-per-prompt", '{"image":1,"video":0}'])
    return args, port, "/health"


def start(name: str, settings: Settings) -> None:
    (ROOT / "run").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    if name == "qdrant":
        state = docker_state()
        if state:
            if state["State"]["Running"]:
                print("qdrant already running")
                return
            subprocess.run(["docker", "start", CONTAINER], check=True)
        else:
            if not port_available(6333):
                raise RuntimeError("Port 6333 is occupied; refusing to stop or reuse its service")
            storage = ROOT / "data/qdrant"
            storage.mkdir(parents=True, exist_ok=True)
            subprocess.run([
                "docker", "run", "-d", "--name", CONTAINER,
                "--label", f"course-rag.owner={OWNER}",
                "--restart", "unless-stopped", "-p", "127.0.0.1:6333:6333",
                "-e", "QDRANT__TELEMETRY_DISABLED=true",
                "-v", f"{storage}:/qdrant/storage", QDRANT_IMAGE,
            ], check=True)
        return
    if owned_process(name):
        print(f"{name} already running")
        return
    if name in ("vlm", "embedding", "reranker", "speech") and owned_process("parser"):
        raise RuntimeError("Wait for the parser batch to exit before starting another GPU model")
    if name == "vlm" and any(owned_process(n) for n in ("embedding", "reranker", "speech")):
        raise RuntimeError("Stop this project's embedding and reranker before starting ingestion VLM")
    if name in ("embedding", "reranker", "speech") and owned_process("vlm"):
        raise RuntimeError("Stop this project's VLM before starting serving models")
    args, port, _ = command(name, settings)
    if not port_available(port):
        raise RuntimeError(f"Port {port} is occupied; refusing to stop its existing service")
    env = dict(os.environ)
    env.update({"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
                "VLLM_NO_USAGE_STATS": "1", "PYTHONUNBUFFERED": "1"})
    with (ROOT / "logs" / f"{name}.log").open("ab") as log:
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    record_path(name).write_text(json.dumps({"owner": OWNER, "pid": process.pid,
                                            "start": process_start(process.pid), "port": port}))
    print(f"Started {name}: pid {process.pid}, 127.0.0.1:{port}; log: logs/{name}.log")


def stop(name: str) -> None:
    if name == "qdrant":
        state = docker_state()
        if state and state["State"]["Running"]:
            subprocess.run(["docker", "stop", CONTAINER], check=True)
        return
    record = owned_process(name)
    if not record:
        print(f"{name} is not running under this project's ownership")
        return
    os.killpg(record["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 30
    while process_start(record["pid"]) == record["start"] and time.monotonic() < deadline:
        time.sleep(0.25)
    if process_start(record["pid"]) == record["start"]:
        raise RuntimeError(f"{name} has not exited after SIGTERM; inspect logs before retrying")
    print(f"Stopped {name}")


def status(name: str, settings: Settings) -> None:
    if name == "qdrant":
        state = docker_state()
        running = bool(state and state["State"]["Running"])
        port = 6333
    else:
        record = owned_process(name)
        running = bool(record)
        port = record["port"] if record else None
    print(f"{name}: {'running' if running else 'stopped'}" + (f" (127.0.0.1:{port})" if running else ""))


def wait_ready(url: str, service: str, timeout: float = 900) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=5, trust_env=False) as client:
        while time.monotonic() < deadline:
            if service != "qdrant" and not owned_process(service):
                raise RuntimeError(f"{service} exited; inspect logs/{service}.log")
            try:
                if client.get(url).status_code == 200:
                    print(f"{service} ready", flush=True)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2)
    raise RuntimeError(f"{service} did not become ready; inspect its log")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("services", nargs="+", choices=[*SERVICES, "serving", "all"])
    args = parser.parse_args()
    names = []
    for name in args.services:
        names.extend(["qdrant", "embedding", "reranker", "api"] if name == "serving" else SERVICES if name == "all" else [name])
    if args.action == "start" and "vlm" in names and any(n in names for n in ("embedding", "reranker", "speech")):
        parser.error("Ingestion and serving models must run in separate phases; use start serving or start vlm")
    settings = Settings.load()
    try:
        for name in dict.fromkeys(reversed(names) if args.action == "stop" else names):
            if args.action == "start":
                start(name, settings)
                if name in ("embedding", "reranker", "vlm", "speech"):
                    # Each profiler assumes the other GPU models' memory is stable.
                    base_url = getattr(settings, f"{name}_url").rstrip("/").removesuffix("/v1")
                    wait_ready(base_url + "/health", name)
            elif args.action == "stop":
                stop(name)
            else:
                status(name, settings)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
