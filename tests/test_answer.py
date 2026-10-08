import json
import logging
from types import SimpleNamespace

import pytest

from rag.answer import (Answerer, AnswerError, ConfigurationError, HistoryTurn,
                        citation_label, configure_private_logging, normalize_history, render_citations)
from rag.config import Settings
from rag.types import Chunk, Hit, RetrievalResult


def chunk(chunk_id, text="Source text"):
    return Chunk(chunk_id, text, ["English source heading"])


class FakeCompletions:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=next(self.answers)))])


class FakeClient:
    def __init__(self, answers):
        self.chat = SimpleNamespace(completions=FakeCompletions(answers))


class FakeRetriever:
    def __init__(self, chunks=None):
        self.questions = []
        self.chunks = chunks or [chunk("gdpr:art17:p1")]

    def retrieve(self, question):
        self.questions.append(question)
        hits = [Hit(c, 0.9 - i / 100) for i, c in enumerate(self.chunks)]
        return RetrievalResult(dense=hits, reranked=hits)


def test_citations_allow_only_current_sources_and_preserve_slide_numbers():
    chunks = [chunk("gdpr:art17:p1"), chunk("slides:lecture6:s7")]
    text, citations = render_citations(
        "幻灯片写着 [42]。删除权[gdpr:art17:p1]。幻灯片[slides:lecture6:s7]。"
        "重复[gdpr:art17:p1]，错误[gdpr:art99:p9][unknown:source]。", chunks)
    assert text == "幻灯片写着 [42]。删除权[1]。幻灯片[2]。重复[1]，错误。"
    assert [c["number"] for c in citations] == [1, 2]
    assert [c["chunk_id"] for c in citations] == [c.id for c in chunks]
    assert citations[1]["label"] == "Lecture 6, Slide 7"


@pytest.mark.parametrize("source_id, expected", [
    ("gdpr:art17:p1", "GDPR Article 17(1)"),
    ("gdpr:art4:5", "GDPR Article 4, point 5"),
    ("gdpr:rec65", "GDPR Recital 65"),
    ("hipaa:164.526.a", "HIPAA 45 CFR § 164.526(a)"),
    ("hipaa:160.103:business-associate", "HIPAA 45 CFR § 160.103, business associate"),
])
def test_english_labels(source_id, expected):
    assert citation_label(chunk(source_id)) == expected


def test_history_last_five_turns_are_clean_copies():
    history = [HistoryTurn(f"问题{i}[1]", f"回答{i}[gdpr:art17:p1][2]") for i in range(7)]
    recent = normalize_history(history)
    assert len(recent) == 5
    assert recent[0] == HistoryTurn("问题2", "回答2")
    assert history[-1].answer.endswith("[2]")


def test_first_turn_skips_rewriting_and_sends_only_five_chunks():
    retriever = FakeRetriever([chunk(f"gdpr:art17:p{i}") for i in range(1, 8)])
    client = FakeClient(["可以请求删除[gdpr:art17:p1]。错误引用[gdpr:art17:p6]。"])
    result = Answerer(Settings(llm_model="deepseek-chat"), retriever, client).ask("删除权是什么？")
    assert retriever.questions == ["删除权是什么？"]
    assert result["rewritten_question"] == "删除权是什么？"
    assert result["rag_enabled"] is True
    assert result["answer"] == "可以请求删除[1]。错误引用。"
    assert len(result["sources"]) == 5
    calls = client.chat.completions.calls
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert [message["role"] for message in calls[0]["messages"]] == ["system", "user"]
    payload = json.loads(calls[0]["messages"][1]["content"])
    assert len(payload["chunks"]) == 5
    assert "history" not in payload


def test_followup_rewrites_before_retrieval_and_uses_only_its_own_citations():
    retriever = FakeRetriever([chunk("gdpr:art17:p3")])
    client = FakeClient(["GDPR 删除权有哪些例外？", "存在例外[gdpr:art17:p3]。旧引文[gdpr:art17:p1]。"])
    history = [HistoryTurn("删除权是什么？", "可以请求删除[1][gdpr:art17:p1]。")]
    result = Answerer(Settings(llm_model="deepseek-chat"), retriever, client).ask("有哪些例外？", history)
    assert retriever.questions == ["GDPR 删除权有哪些例外？"]
    assert result["rewritten_question"] == "GDPR 删除权有哪些例外？"
    assert result["answer"] == "存在例外[1]。旧引文。"
    calls = client.chat.completions.calls
    assert len(calls) == 2
    for call in calls:
        assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert [message["role"] for message in calls[0]["messages"]] == ["system", "user"]
    rewrite_payload = json.loads(calls[0]["messages"][1]["content"])
    assert rewrite_payload == {"question": "有哪些例外？", "history": [
        {"question": "删除权是什么？", "answer": "可以请求删除。"},
    ]}
    messages = calls[1]["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[1:3] == [
        {"role": "user", "content": "删除权是什么？"},
        {"role": "assistant", "content": "可以请求删除。"},
    ]
    answer_payload = json.loads(messages[-1]["content"])
    assert answer_payload["question"] == "有哪些例外？"
    assert answer_payload["rewritten_question"] == "GDPR 删除权有哪些例外？"
    assert "history" not in answer_payload
    assert history[0].answer == "可以请求删除[1][gdpr:art17:p1]。"


def test_missing_deepseek_does_not_block_first_turn_retrieval():
    answerer = Answerer(Settings(), FakeRetriever())
    assert answerer.retrieve("问题")["reranked"][0]["id"] == "gdpr:art17:p1"
    with pytest.raises(ConfigurationError):
        answerer.ask("问题")
    with pytest.raises(ConfigurationError):
        answerer.retrieve("后续问题", [HistoryTurn("前文", "回答")])


def test_empty_provider_answer_is_an_explicit_failure():
    answerer = Answerer(Settings(), FakeRetriever(), FakeClient([" "]))
    with pytest.raises(AnswerError):
        answerer.ask("问题")


def test_inherited_sdk_debug_logging_cannot_record_private_payloads(caplog, monkeypatch):
    monkeypatch.setenv("OPENAI_LOG", "debug")
    from openai._utils._logs import setup_logging
    setup_logging()
    names = ["openai", "openai._base_client", "httpx", "httpcore.connection"]
    for name in names:
        caplog.set_level(logging.DEBUG, logger=name)
    configure_private_logging()
    for name in names:
        logger = logging.getLogger(name)
        logger.debug("Private question, history, and source contents")
        assert logger.getEffectiveLevel() >= logging.WARNING
    assert "Private question" not in caplog.text


def test_direct_answer_skips_rewrite_and_retrieval_and_cleans_history(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Direct answering must not rewrite or retrieve")

    retriever = FakeRetriever()
    client = FakeClient(["直接回答[gdpr:art17:p1][1]，并说明不确定性[2, 3][unknown:source]。"])
    answerer = Answerer(Settings(llm_model="deepseek-chat"), retriever, client)
    monkeypatch.setattr(answerer, "rewrite", forbidden)
    monkeypatch.setattr(retriever, "retrieve", forbidden)
    history = [HistoryTurn(f"问题{i}[1]", f"回答{i}[gdpr:art17:p1][2]") for i in range(7)]
    result = answerer.ask("那它呢？", history, rag_enabled=False)
    assert result == {"question": "那它呢？", "rewritten_question": "那它呢？",
                      "answer": "直接回答，并说明不确定性。", "citations": [], "sources": [],
                      "rag_enabled": False}
    calls = client.chat.completions.calls
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    messages = calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", *["user", "assistant"] * 5, "user"]
    expected_history = [message for i in range(2, 7) for message in (
        {"role": "user", "content": f"问题{i}"},
        {"role": "assistant", "content": f"回答{i}"},
    )]
    assert messages[1:-1] == expected_history
    assert messages[-1] == {"role": "user", "content": "那它呢？"}
    assert history[0].answer.endswith("[gdpr:art17:p1][2]")


def test_direct_answer_does_not_initialize_unavailable_retriever(monkeypatch):
    def unavailable(*args, **kwargs):
        pytest.fail("Direct answering must not initialize the retriever")

    monkeypatch.setattr("rag.retrieval.Retriever", unavailable)
    client = FakeClient(["根据前文，这是直接回答。"])
    answerer = Answerer(Settings(), client=client)
    result = answerer.ask("请解释前文", [HistoryTurn("原问题", "原回答[1]")], rag_enabled=False)
    assert result["rag_enabled"] is False
    assert len(client.chat.completions.calls) == 1


def test_rag_toggle_is_per_request_and_preserves_earlier_citations():
    retriever = FakeRetriever([chunk("gdpr:art17:p1")])
    client = FakeClient(["可以请求删除[gdpr:art17:p1]。", "直接解释[1]。",
                         "GDPR 删除权有哪些例外？", "存在例外[gdpr:art17:p3]。"])
    answerer = Answerer(Settings(), retriever, client)
    first = answerer.ask("删除权是什么？")
    history = [HistoryTurn(first["question"], first["answer"])]
    direct = answerer.ask("解释一下", history, rag_enabled=False)
    history.append(HistoryTurn(direct["question"], direct["answer"]))
    retriever.chunks = [chunk("gdpr:art17:p3")]
    last = answerer.ask("有哪些例外？", history, rag_enabled=True)
    assert retriever.questions == ["删除权是什么？", "GDPR 删除权有哪些例外？"]
    assert first["rag_enabled"] is True and last["rag_enabled"] is True
    assert direct["rag_enabled"] is False
    assert first["answer"] == history[0].answer == "可以请求删除[1]。"
    assert first["citations"][0]["chunk_id"] == "gdpr:art17:p1"
    assert last["citations"][0]["chunk_id"] == "gdpr:art17:p3"
    assert last["citations"][0]["number"] == 1
    assert direct["answer"] == "直接解释。" and direct["citations"] == []
    calls = client.chat.completions.calls
    assert len(calls) == 4
    assert all(call["extra_body"] == {"thinking": {"type": "disabled"}} for call in calls)
    assert calls[1]["messages"][1:] == [
        {"role": "user", "content": "删除权是什么？"},
        {"role": "assistant", "content": "可以请求删除。"},
        {"role": "user", "content": "解释一下"},
    ]
    assert [message["role"] for message in calls[2]["messages"]] == ["system", "user"]
    assert json.loads(calls[2]["messages"][1]["content"])["history"] == [
        {"question": "删除权是什么？", "answer": "可以请求删除。"},
        {"question": "解释一下", "answer": "直接解释。"},
    ]
    assert [message["role"] for message in calls[3]["messages"]] == [
        "system", "user", "assistant", "user", "assistant", "user",
    ]
    assert calls[3]["messages"][1:-1] == [
        {"role": "user", "content": "删除权是什么？"},
        {"role": "assistant", "content": "可以请求删除。"},
        {"role": "user", "content": "解释一下"},
        {"role": "assistant", "content": "直接解释。"},
    ]
    final_payload = json.loads(calls[3]["messages"][-1]["content"])
    assert "history" not in final_payload
    assert final_payload["question"] == "有哪些例外？"
    assert [source["id"] for source in final_payload["chunks"]] == ["gdpr:art17:p3"]


def test_direct_answer_still_requires_deepseek_configuration():
    with pytest.raises(ConfigurationError):
        Answerer(Settings()).ask("问题", rag_enabled=False)


def test_direct_answer_with_only_citation_markers_is_unusable():
    answerer = Answerer(Settings(), client=FakeClient(["[1][gdpr:art17:p1]"]))
    with pytest.raises(AnswerError):
        answerer.ask("问题", rag_enabled=False)


def test_rag_answers_keep_only_five_chronological_native_history_turns():
    history = [HistoryTurn(f"问题{i}[1]", f"回答{i}[gdpr:art17:p1][2]") for i in range(7)]
    retriever = FakeRetriever([chunk("gdpr:art17:p3")])
    client = FakeClient(["GDPR 删除权的例外是什么？", "存在例外[gdpr:art17:p3]。"])
    result = Answerer(Settings(), retriever, client).ask("再说明例外", history)
    assert result["answer"] == "存在例外[1]。"
    calls = client.chat.completions.calls
    assert len(calls) == 2
    rewrite = json.loads(calls[0]["messages"][-1]["content"])
    assert rewrite["history"] == [
        {"question": f"问题{i}", "answer": f"回答{i}"} for i in range(2, 7)
    ]
    messages = calls[1]["messages"]
    assert [message["role"] for message in messages] == ["system", *["user", "assistant"] * 5, "user"]
    assert messages[1:-1] == [message for i in range(2, 7) for message in (
        {"role": "user", "content": f"问题{i}"},
        {"role": "assistant", "content": f"回答{i}"},
    )]
    payload = json.loads(messages[-1]["content"])
    assert set(payload) == {"question", "rewritten_question", "chunks"}
    assert payload["question"] == "再说明例外"
    assert history[-1] == HistoryTurn("问题6[1]", "回答6[gdpr:art17:p1][2]")


@pytest.mark.parametrize("rag_enabled", [False, True])
def test_short_correction_retains_original_question_and_assistant_misunderstanding(rag_enabled):
    original_question = "个人要求删除数据时，有哪些例外？"
    mistaken_answer = "你问的是访问数据的流程，需要先确认身份。"
    correction = "不是访问，是删除。"
    history = [HistoryTurn(original_question, mistaken_answer + "[1]")]
    responses = (["个人请求删除数据有哪些例外？"] if rag_enabled else []) + ["明白了，这里讨论删除请求的例外。"]
    retriever = FakeRetriever()
    client = FakeClient(responses)
    Answerer(Settings(), retriever, client).ask(correction, history, rag_enabled=rag_enabled)
    calls = client.chat.completions.calls
    assert len(calls) == (2 if rag_enabled else 1)
    messages = calls[-1]["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert messages[1:3] == [
        {"role": "user", "content": original_question},
        {"role": "assistant", "content": mistaken_answer},
    ]
    if rag_enabled:
        assert json.loads(messages[-1]["content"])["question"] == correction
        assert retriever.questions == ["个人请求删除数据有哪些例外？"]
    else:
        assert messages[-1] == {"role": "user", "content": correction}
        assert retriever.questions == []
    assert all(call["temperature"] == 0 for call in calls)
    assert all(call["extra_body"] == {"thinking": {"type": "disabled"}} for call in calls)
    assert history[0].answer == mistaken_answer + "[1]"
