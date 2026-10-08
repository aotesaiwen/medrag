"""Exercise lifecycle failures without opening sockets or launching processes."""

import json
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import httpx
import pytest

from rag.config import Settings
from scripts import build, parser_stage, services


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    lock_path = tmp_path / "models.lock.json"
    lock_path.write_text(json.dumps({
        "mineru": {"local_path": "models/mineru", "revision": "parser-revision"},
        "pipeline": {"local_path": "models/pipeline", "revision": "pipeline-revision"},
        "vlm": {"local_path": "models/vlm", "revision": "vlm-revision"},
        "embedding": {"local_path": "models/embedding"},
    }))
    settings = Settings(model_lock_path=lock_path)
    for module in (parser_stage, services, build):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(services, "OWNER", str(tmp_path))
    monkeypatch.setattr(Settings, "load", lambda: settings)

    process = Mock(pid=32123)
    process.poll.return_value = None
    process.wait.side_effect = lambda **kwargs: setattr(process.poll, "return_value", 0) or 0
    launch = Mock(return_value=process)
    monkeypatch.setattr(parser_stage.subprocess, "Popen", launch)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(parser_stage.subprocess, "run", run)
    kill = Mock()
    monkeypatch.setattr(parser_stage.os, "killpg", kill)
    monkeypatch.setattr(services, "process_start",
                        lambda pid: "owned-start" if process.poll.return_value is None else None)

    socket_factory = MagicMock()
    socket_factory.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 48123)
    monkeypatch.setattr(parser_stage.socket, "socket", socket_factory)

    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = SimpleNamespace(status_code=200)
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(parser_stage.httpx, "Client", client_factory)

    clock = SimpleNamespace(now=0.0, sleep_step=1.0)
    monkeypatch.setattr(parser_stage.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(parser_stage.time, "sleep", lambda seconds: setattr(clock, "now", clock.now + clock.sleep_step))
    return SimpleNamespace(root=tmp_path, settings=settings, process=process, launch=launch,
                           run=run, kill=kill, client=client, clock=clock)


def test_ready_parser_is_owned_for_whole_batch_and_released_on_exit(runtime):
    with parser_stage.mineru_api(runtime.settings) as url:
        assert url == "http://127.0.0.1:48123"
        assert services.owned_process("parser")["pid"] == runtime.process.pid
        assert runtime.launch.call_args.kwargs["start_new_session"] is True
        assert runtime.launch.call_args.kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert runtime.launch.call_args.kwargs["env"]["MINERU_API_DISABLE_ACCESS_LOG"] == "1"
        runtime.kill.assert_not_called()
    runtime.kill.assert_called_once_with(runtime.process.pid, signal.SIGTERM)
    runtime.process.wait.assert_called_once_with(timeout=30)
    assert services.owned_process("parser") is None


def test_health_retries_connection_errors_and_unready_responses(runtime):
    runtime.client.get.side_effect = [httpx.ConnectError("not started"),
                                      SimpleNamespace(status_code=503),
                                      SimpleNamespace(status_code=200)]
    with parser_stage.mineru_api(runtime.settings):
        assert runtime.client.get.call_count == 3
        runtime.kill.assert_not_called()
    runtime.kill.assert_called_once_with(runtime.process.pid, signal.SIGTERM)


def test_startup_deadline_failure_never_enters_batch_and_stops_owned_child(runtime):
    runtime.client.get.return_value = SimpleNamespace(status_code=503)
    runtime.clock.sleep_step = 121.0
    with pytest.raises(RuntimeError, match="did not become ready"):
        with parser_stage.mineru_api(runtime.settings):
            pytest.fail("An unhealthy parser must not receive the batch")
    runtime.kill.assert_called_once_with(runtime.process.pid, signal.SIGTERM)
    runtime.process.wait.assert_called_once_with(timeout=30)


def test_exited_child_reports_startup_failure_and_tolerates_missing_group(runtime):
    runtime.process.poll.return_value = 2
    runtime.kill.side_effect = ProcessLookupError()
    with pytest.raises(RuntimeError, match="MinerU exited"):
        with parser_stage.mineru_api(runtime.settings):
            pytest.fail("An exited parser cannot be ready")
    runtime.client.get.assert_not_called()
    runtime.kill.assert_called_once_with(runtime.process.pid, signal.SIGTERM)


def test_batch_error_propagates_after_parser_cleanup(runtime):
    failure = subprocess.CalledProcessError(7, ["ingest", "parse-slides"])
    with pytest.raises(subprocess.CalledProcessError) as captured:
        with parser_stage.mineru_api(runtime.settings):
            raise failure
    assert captured.value is failure
    runtime.kill.assert_called_once_with(runtime.process.pid, signal.SIGTERM)
    runtime.process.wait.assert_called_once_with(timeout=30)


def test_cleanup_escalates_only_its_spawned_group_when_grace_period_expires(runtime):
    runtime.process.wait.side_effect = [subprocess.TimeoutExpired("mineru-api", 30), 0]
    with parser_stage.mineru_api(runtime.settings):
        pass
    assert runtime.kill.call_args_list == [call(runtime.process.pid, signal.SIGTERM),
                                           call(runtime.process.pid, signal.SIGKILL)]
    assert runtime.process.wait.call_args_list == [call(timeout=30), call(timeout=10)]


def test_exit_between_timeout_and_kill_does_not_mask_batch_failure(runtime):
    runtime.process.wait.side_effect = [subprocess.TimeoutExpired("mineru-api", 30), 0]
    runtime.kill.side_effect = [None, ProcessLookupError()]
    failure = ValueError("Parsing failed")
    with pytest.raises(ValueError, match="Parsing failed") as captured:
        with parser_stage.mineru_api(runtime.settings):
            raise failure
    assert captured.value is failure
    assert all(args.args[0] == runtime.process.pid for args in runtime.kill.call_args_list)


def test_launch_failure_never_signals_a_process(runtime):
    runtime.launch.side_effect = FileNotFoundError("mineru-api is missing")
    with pytest.raises(FileNotFoundError):
        with parser_stage.mineru_api(runtime.settings):
            pytest.fail("Launch failed")
    runtime.kill.assert_not_called()


def test_failed_ownership_record_write_releases_new_child(runtime, monkeypatch):
    write_text = Path.write_text

    def fail_parser_record(path, *args, **kwargs):
        if path == runtime.root / "run/parser.json":
            raise OSError("Cannot write ownership record")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_parser_record)
    with pytest.raises(OSError, match="ownership record"):
        with parser_stage.mineru_api(runtime.settings):
            pytest.fail("The process must be registered before accepting a batch")
    runtime.kill.assert_called_once_with(runtime.process.pid, signal.SIGTERM)
    runtime.process.wait.assert_called_once_with(timeout=30)


def test_duplicate_parser_batch_is_rejected_without_touching_existing_process(runtime, monkeypatch):
    monkeypatch.setattr(services, "owned_process", lambda name: {"pid": 99999} if name == "parser" else None)
    with pytest.raises(RuntimeError, match="already running"):
        with parser_stage.mineru_api(runtime.settings):
            pytest.fail("The existing batch owns the parser phase")
    runtime.launch.assert_not_called()
    runtime.kill.assert_not_called()


def test_main_propagates_ingestion_subprocess_failure_after_cleanup(runtime):
    failure = subprocess.CalledProcessError(4, ["ingest", "parse-slides"])
    runtime.run.side_effect = failure
    with pytest.raises(subprocess.CalledProcessError) as captured:
        parser_stage.main()
    assert captured.value is failure
    command = runtime.run.call_args.args[0]
    assert "parse-slides" in command
    assert command[command.index("--mineru-api-url") + 1] == "http://127.0.0.1:48123"
    assert runtime.run.call_args.kwargs["check"] is True
    runtime.kill.assert_called_once_with(runtime.process.pid, signal.SIGTERM)


@pytest.mark.parametrize("active", ["vlm", "embedding", "reranker"])
def test_main_rejects_gpu_overlap_before_launching_parser(runtime, monkeypatch, active):
    monkeypatch.setattr(services, "owned_process", lambda name: {"pid": 99999} if name == active else None)
    with pytest.raises(SystemExit, match="before parsing slides"):
        parser_stage.main()
    runtime.launch.assert_not_called()
    runtime.kill.assert_not_called()
    runtime.run.assert_not_called()


@pytest.mark.parametrize("service", ["vlm", "embedding", "reranker"])
def test_model_start_rejects_live_parser_without_stopping_it(runtime, monkeypatch, service):
    monkeypatch.setattr(services, "owned_process", lambda name: {"pid": 99999} if name == "parser" else None)
    with pytest.raises(RuntimeError, match="parser"):
        services.start(service, runtime.settings)
    runtime.launch.assert_not_called()
    runtime.kill.assert_not_called()


@pytest.mark.parametrize("recorded_start, actual_start", [("original", "reused"), (None, None)])
def test_stale_service_records_never_authorize_signals(runtime, monkeypatch, recorded_start, actual_start):
    (runtime.root / "run").mkdir()
    services.record_path("vlm").write_text(json.dumps({
        "owner": str(runtime.root), "pid": 99999, "start": recorded_start, "port": 8103,
    }))
    monkeypatch.setattr(services, "process_start", lambda pid: actual_start)
    services.stop("vlm")
    runtime.kill.assert_not_called()


def test_foreign_ownership_record_never_authorizes_signals(runtime):
    (runtime.root / "run").mkdir()
    services.record_path("vlm").write_text(json.dumps({
        "owner": "/some/other/project", "pid": 99999, "start": "owned-start", "port": 8103,
    }))
    with pytest.raises(RuntimeError, match="foreign service record"):
        services.stop("vlm")
    runtime.kill.assert_not_called()


def test_build_readiness_failure_releases_vlm_before_propagating(runtime, monkeypatch):
    slides = runtime.root / "data/raw/slides"
    slides.mkdir(parents=True)
    (slides / "lecture1.pdf").touch()
    monkeypatch.setattr(build.sys, "argv", ["build.py"])
    started, stopped = [], []
    monkeypatch.setattr(build, "start", lambda name, settings: started.append(name))
    monkeypatch.setattr(build, "stop", stopped.append)
    monkeypatch.setattr(build, "run", Mock())

    def readiness(url, service):
        if service == "vlm":
            raise RuntimeError("VLM failed to become ready")

    monkeypatch.setattr(build, "wait_ready", readiness)
    with pytest.raises(RuntimeError, match="failed to become ready"):
        build.main()
    assert started == ["qdrant", "vlm"]
    assert stopped[-1] == "vlm"
    assert stopped.count("vlm") == 2  # Existing phase first; newly started VLM on failure.
