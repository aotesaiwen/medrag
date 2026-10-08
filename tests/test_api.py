from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from rag.answer import ConfigurationError
from rag.config import Settings


class FakeAnswerer:
    def __init__(self):
        self.calls = []
        self.modes = []
        self.retrieval_calls = []
        self.error = None
        self.retriever = SimpleNamespace(health=lambda: {"ok": True, "index": {"ok": True, "points": 10}})

    def ask(self, question, history, *, rag_enabled=True):
        self.calls.append((question, history))
        self.modes.append(rag_enabled)
        if self.error:
            raise self.error
        return {"question": question, "rewritten_question": question, "answer": "答案",
                "citations": [], "sources": [], "rag_enabled": rag_enabled}

    def retrieve(self, question, history):
        self.calls.append((question, history))
        self.retrieval_calls.append((question, history))
        if self.error:
            raise self.error
        return {"question": question, "rewritten_question": question, "dense": [], "reranked": []}


@pytest.fixture
def service():
    fake = FakeAnswerer()
    settings = Settings(api_key="test-secret", llm_model="deepseek-chat",
                        llm_base_url="https://example.invalid", llm_api_key="test-llm-key")
    return TestClient(create_app(settings, fake)), fake


def test_health_is_public_and_reports_readiness(service):
    client, _ = service
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["retrieval"]["index"]["points"] == 10
    assert "test-secret" not in response.text


@pytest.mark.parametrize("path", ["/ask", "/retrieve"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                      {"Authorization": "Basic test-secret"}])
def test_auth_rejects_missing_wrong_and_wrong_scheme(service, path, headers):
    client, fake = service
    response = client.post(path, json={"question": "问题"}, headers=headers)
    assert response.status_code == 401
    assert fake.calls == []


def test_auth_fails_closed_when_no_key_configured():
    client = TestClient(create_app(Settings(api_key=""), FakeAnswerer()))
    response = client.post("/ask", json={"question": "问题"},
                           headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert client.get("/health").json()["ready"] is False


def test_authorized_request_trims_history_before_dispatch(service):
    client, fake = service
    response = client.post("/ask", headers={"Authorization": "Bearer test-secret"}, json={
        "question": "  问题  ", "history": [{"question": str(i), "answer": "答案"} for i in range(7)],
    })
    assert response.status_code == 200
    question, history = fake.calls[0]
    assert question == "问题"
    assert len(history) == 5
    assert history[0].question == "2"


@pytest.mark.parametrize("question", ["", "   ", "x" * 20001])
def test_invalid_question_is_rejected(service, question):
    client, fake = service
    response = client.post("/ask", headers={"Authorization": "Bearer test-secret"},
                           json={"question": question})
    assert response.status_code == 422
    assert not fake.calls


def test_upstream_errors_do_not_expose_questions_or_secrets(service, caplog):
    client, fake = service
    fake.error = RuntimeError("sensitive question and secret credential")
    response = client.post("/ask", headers={"Authorization": "Bearer test-secret"},
                           json={"question": "private question"})
    assert response.status_code == 502
    assert "sensitive" not in response.text
    assert "private question" not in caplog.text
    assert "secret credential" not in caplog.text


def test_missing_llm_settings_returns_clear_service_error(service):
    client, fake = service
    fake.error = ConfigurationError("missing settings")
    response = client.post("/ask", headers={"Authorization": "Bearer test-secret"},
                           json={"question": "问题"})
    assert response.status_code == 503
    assert response.json()["detail"] == "DeepSeek settings are incomplete."


def test_schema_routes_are_not_public(service):
    client, _ = service
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer test-secret"}).status_code == 404


@pytest.mark.parametrize("method, path", [
    ("OPTIONS", "/ask"), ("HEAD", "/health"), ("GET", "/unknown"),
    ("POST", "/ask/"), ("GET", "/health/"), ("POST", "/health"), ("GET", "/ask"),
])
def test_only_exact_get_health_bypasses_authentication(service, method, path):
    client, fake = service
    response = client.request(method, path, follow_redirects=False)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert not fake.calls


@pytest.mark.parametrize("method, path, status", [
    ("OPTIONS", "/ask", 405), ("GET", "/unknown", 404), ("GET", "/ask", 405),
    ("POST", "/ask/", 307),
])
def test_authenticated_requests_receive_normal_routing_responses(service, method, path, status):
    client, _ = service
    response = client.request(method, path, headers={"Authorization": "Bearer test-secret"},
                              follow_redirects=False)
    assert response.status_code == status


def test_authentication_precedes_parsing_private_request_bodies(service):
    client, fake = service
    response = client.post("/ask", content="not valid JSON: private question",
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 401
    assert "private question" not in response.text
    assert not fake.calls


def test_ask_defaults_to_rag_and_mode_is_selected_per_request(service):
    client, fake = service
    bodies = [{"question": "默认"}, {"question": "关闭", "rag_enabled": False},
              {"question": "开启", "rag_enabled": True}, {"question": "再次默认"}]
    results = [client.post("/ask", headers={"Authorization": "Bearer test-secret"}, json=body)
               for body in bodies]
    assert all(response.status_code == 200 for response in results)
    assert [response.json()["rag_enabled"] for response in results] == [True, False, True, True]
    assert fake.modes == [True, False, True, True]


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_ask_rag_enabled_accepts_only_json_booleans(service, value):
    client, fake = service
    response = client.post("/ask", headers={"Authorization": "Bearer test-secret"},
                           json={"question": "问题", "rag_enabled": value})
    assert response.status_code == 422
    assert fake.calls == []


def test_retrieve_remains_retrieval_even_if_ask_toggle_is_supplied(service):
    client, fake = service
    response = client.post("/retrieve", headers={"Authorization": "Bearer test-secret"},
                           json={"question": "问题", "rag_enabled": False})
    assert response.status_code == 200
    assert len(fake.retrieval_calls) == 1
    assert fake.modes == []
    assert "rag_enabled" not in response.json()


def test_direct_mode_authentication_is_unchanged(service):
    client, fake = service
    response = client.post("/ask", json={"question": "问题", "rag_enabled": False})
    assert response.status_code == 401
    assert not fake.calls


def test_direct_http_answer_works_when_retriever_cannot_be_initialized(monkeypatch):
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="直接回答[1]。"))])

    def unavailable(*args, **kwargs):
        raise RuntimeError("Retrieval is unavailable")

    provider = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
    monkeypatch.setattr("rag.answer.OpenAI", lambda **kwargs: provider)
    monkeypatch.setattr("rag.retrieval.Retriever", unavailable)
    settings = Settings(api_key="test-secret", llm_model="test-model", llm_api_key="test-llm-key",
                        llm_base_url="https://example.invalid")
    client = TestClient(create_app(settings))
    body = {"question": "那它呢？", "rag_enabled": False,
            "history": [{"question": f"前文{i}[2]", "answer": f"此前回答{i}[1][gdpr:art17:p1]"}
                        for i in range(7)]}
    headers = {"Authorization": "Bearer test-secret"}
    response = client.post("/ask", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json() == {"question": "那它呢？", "rewritten_question": "那它呢？",
                               "answer": "直接回答。", "citations": [], "sources": [],
                               "rag_enabled": False}
    assert len(calls) == 1
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    messages = calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", *["user", "assistant"] * 5, "user"]
    assert messages[1:-1] == [message for i in range(2, 7) for message in (
        {"role": "user", "content": f"前文{i}"},
        {"role": "assistant", "content": f"此前回答{i}"},
    )]
    assert messages[-1] == {"role": "user", "content": "那它呢？"}
    assert body["history"][-1]["answer"] == "此前回答6[1][gdpr:art17:p1]"
    assert client.post("/ask", headers=headers, json={"question": "检索"}).status_code == 502
    assert client.post("/ask", headers=headers, json=body).status_code == 200
    assert len(calls) == 2
