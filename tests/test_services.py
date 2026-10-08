"""Model completeness, readiness, and CLI sequencing without real services."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from rag.config import Settings
from scripts import services


@pytest.fixture
def launcher(monkeypatch):
    settings = Settings()
    events = []
    monkeypatch.setattr(Settings, "load", lambda: settings)
    monkeypatch.setattr(services, "start", lambda name, config: events.append(("start", name)))
    monkeypatch.setattr(services, "wait_ready", lambda url, name: events.append(("ready", name)))
    return events


def test_serving_start_waits_for_each_model_before_launching_the_next(launcher, monkeypatch):
    monkeypatch.setattr(services.sys, "argv", ["services.py", "start", "serving"])
    services.main()
    assert launcher == [
        ("start", "qdrant"),
        ("start", "embedding"), ("ready", "embedding"),
        ("start", "reranker"), ("ready", "reranker"),
        ("start", "api"),
    ]


@pytest.mark.parametrize("name, port", [("embedding", 8101), ("reranker", 8102), ("vlm", 8103)])
def test_single_model_start_also_waits_for_health(launcher, monkeypatch, name, port):
    monkeypatch.setattr(services.sys, "argv", ["services.py", "start", name])
    ready = Mock(side_effect=lambda url, service: launcher.append(("ready", service)))
    monkeypatch.setattr(services, "wait_ready", ready)
    services.main()
    assert launcher == [("start", name), ("ready", name)]
    ready.assert_called_once_with(f"http://127.0.0.1:{port}/health", name)


@pytest.mark.parametrize("failed_model", ["embedding", "reranker"])
def test_readiness_failure_aborts_later_starts(launcher, monkeypatch, failed_model):
    monkeypatch.setattr(services.sys, "argv", ["services.py", "start", "serving"])

    def readiness(url, name):
        launcher.append(("ready", name))
        if name == failed_model:
            raise RuntimeError(f"{name} failed initialization")

    monkeypatch.setattr(services, "wait_ready", readiness)
    with pytest.raises(SystemExit, match=f"{failed_model} failed initialization"):
        services.main()
    assert launcher[-1] == ("ready", failed_model)
    assert ("start", "api") not in launcher
    if failed_model == "embedding":
        assert ("start", "reranker") not in launcher


def test_repeated_service_names_do_not_duplicate_initialization(launcher, monkeypatch):
    monkeypatch.setattr(services.sys, "argv", ["services.py", "start", "embedding", "serving"])
    services.main()
    assert launcher.count(("start", "embedding")) == 1
    assert launcher.count(("ready", "embedding")) == 1
    assert launcher.index(("ready", "embedding")) < launcher.index(("start", "reranker"))


@pytest.fixture
def readiness(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = SimpleNamespace(status_code=200)
    factory = Mock(return_value=client)
    monkeypatch.setattr(services.httpx, "Client", factory)
    owned = Mock(return_value={"pid": 12345})
    monkeypatch.setattr(services, "owned_process", owned)
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(services.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(services.time, "sleep", lambda seconds: setattr(clock, "now", clock.now + seconds))
    return SimpleNamespace(client=client, factory=factory, owned=owned, clock=clock)


def test_wait_ready_retries_until_server_is_ready(readiness):
    readiness.client.get.side_effect = [httpx.ConnectError("not listening"),
                                         SimpleNamespace(status_code=503),
                                         SimpleNamespace(status_code=200)]
    services.wait_ready("http://127.0.0.1:8101/health", "embedding", timeout=10)
    assert readiness.client.get.call_count == 3
    assert readiness.owned.call_count == 3
    readiness.factory.assert_called_once_with(timeout=5, trust_env=False)


def test_wait_ready_fails_when_owned_process_exits(readiness):
    readiness.owned.side_effect = [{"pid": 12345}, None]
    readiness.client.get.return_value = SimpleNamespace(status_code=503)
    with pytest.raises(RuntimeError, match="embedding exited"):
        services.wait_ready("http://127.0.0.1:8101/health", "embedding", timeout=10)
    assert readiness.client.get.call_count == 1


def test_wait_ready_has_a_bounded_deadline(readiness):
    readiness.client.get.side_effect = httpx.ConnectError("not listening")
    with pytest.raises(RuntimeError, match="did not become ready"):
        services.wait_ready("http://127.0.0.1:8102/health", "reranker", timeout=3)
    assert readiness.client.get.call_count == 2
    assert readiness.clock.now == 4


def test_qdrant_readiness_does_not_require_a_process_record(readiness):
    readiness.owned.side_effect = AssertionError("Docker uses container ownership")
    services.wait_ready("http://127.0.0.1:6333/healthz", "qdrant")
    readiness.owned.assert_not_called()


@pytest.fixture
def downloaded_model(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "ROOT", tmp_path)
    model = tmp_path / "models/embedding"
    model.mkdir(parents=True)
    lock_path = tmp_path / "models.lock.json"
    identity = {"repo_id": "Qwen/Qwen3-Embedding-8B", "revision": "a" * 40,
                "dtype": "bfloat16", "local_path": "models/embedding"}
    lock_path.write_text(json.dumps({"embedding": identity}))
    return model, Settings(model_lock_path=lock_path), identity


@pytest.mark.parametrize("missing", ["config", "weights"])
def test_model_command_rejects_missing_config_or_weights(downloaded_model, missing):
    model, settings, _ = downloaded_model
    if missing != "config":
        (model / "config.json").write_text("{}")
    if missing != "weights":
        (model / "model.safetensors").touch()
    with pytest.raises(RuntimeError, match="not downloaded"):
        services.command("embedding", settings)


def test_model_command_rejects_sharded_weights_without_index(downloaded_model):
    model, settings, _ = downloaded_model
    (model / "config.json").write_text("{}")
    (model / "model-00001-of-00002.safetensors").touch()
    with pytest.raises(RuntimeError, match="weight index is missing"):
        services.command("embedding", settings)


def test_model_command_rejects_index_with_missing_weight_shard(downloaded_model):
    model, settings, _ = downloaded_model
    (model / "config.json").write_text("{}")
    (model / "model-00001-of-00002.safetensors").touch()
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {
        "model.layer.0": "model-00001-of-00002.safetensors",
        "model.layer.1": "model-00002-of-00002.safetensors",
    }}))
    with pytest.raises(RuntimeError, match="wait for all weight shards"):
        services.command("embedding", settings)


def test_complete_model_command_preserves_precision_pin_and_private_logging(downloaded_model):
    model, settings, identity = downloaded_model
    (model / "config.json").write_text("{}")
    shards = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
    for shard in shards:
        (model / shard).touch()
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {
        f"model.layer.{index}": shard for index, shard in enumerate(shards)
    }}))
    command, port, health_path = services.command("embedding", settings)
    assert command[command.index("--dtype") + 1] == "bfloat16"
    assert command[command.index("--revision") + 1] == identity["revision"]
    assert command[command.index("--served-model-name") + 1] == identity["repo_id"]
    assert command[command.index("--runner") + 1] == "pooling"
    assert {"--no-enable-log-requests", "--no-enable-log-outputs", "--disable-uvicorn-access-log"} <= set(command)
    assert port == 8101 and health_path == "/health"
