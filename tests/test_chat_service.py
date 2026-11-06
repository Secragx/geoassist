from datetime import datetime, timezone

from geoassist.models import InboundMessage
from geoassist.rag.retriever import RetrievedChunk, RetrievalResult
from geoassist.services.chat import ChatService
from geoassist.storage.state import StateStore

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
CHUNK = RetrievedChunk(
    1, "d:0", "d", "H", "Texto útil", "Manual", "1.0", "approved", "geopos", "a.md", 0.9, 1, 1, 0.8
)
MESSAGE = InboundMessage("act-1", "¿Cómo hago?", "u1", "c1", "t1", None)


class FakeRetriever:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def retrieve(self, question, app_filter=None):
        self.calls += 1
        return self.result


class FakeLLM:
    def __init__(self, answer="respuesta", error=None):
        self.answer = answer
        self.error = error
        self.calls = 0
        self.messages = None

    def complete(self, messages, *, timeout_s, max_tokens=500, temperature=0.1):
        self.calls += 1
        self.messages = messages
        if self.error:
            raise self.error
        return self.answer


class FakeDetector:
    def detect(self, question):
        return None


def make_service(tmp_path, result, llm):
    return ChatService(
        StateStore(tmp_path / "state.db"), FakeRetriever(result), llm, FakeDetector()
    )


def test_abstention_does_not_call_llm(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((), False, "no_candidates", {})
    outcome = make_service(tmp_path, result, llm).handle(MESSAGE)
    assert llm.calls == 0
    assert "no encontr" in outcome.text.lower()
    assert outcome.degraded is False


def test_llm_timeout_returns_fallback(tmp_path):
    from geoassist.llm.base import LLMError

    llm = FakeLLM(error=LLMError("timeout", "boom"))
    result = RetrievalResult((CHUNK,), True, None, {})
    outcome = make_service(tmp_path, result, llm).handle(MESSAGE)
    assert outcome.degraded is True
    assert "Manual" in outcome.text


def test_second_turn_sends_history_to_llm(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    service = make_service(tmp_path, result, llm)
    service.handle(MESSAGE)
    service.handle(InboundMessage("act-2", "otra", "u1", "c1", "t1", None))
    roles = [m["role"] for m in llm.messages]
    assert roles[1:3] == ["user", "assistant"]
