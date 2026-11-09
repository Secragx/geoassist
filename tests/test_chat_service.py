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
        self.seen_question = None

    def retrieve(self, question, app_filter=None):
        self.calls += 1
        self.seen_question = question
        return self.result


class FakeLLM:
    def __init__(self, answer="respuesta", error=None):
        self.answer = answer
        self.error = error
        self.calls = 0
        self.messages = None
        self.last_timeout_s = None
        self.last_max_tokens = None

    def complete(self, messages, *, timeout_s, max_tokens=500, temperature=0.1):
        self.calls += 1
        self.messages = messages
        self.last_timeout_s = timeout_s
        self.last_max_tokens = max_tokens
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


def test_llm_timeout_follows_service_configuration(tmp_path):
    from geoassist.services.chat import ChatService

    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        FakeRetriever(result),
        llm,
        FakeDetector(),
        max_llm_ms=1200,
    )
    service.handle(MESSAGE)
    assert llm.last_timeout_s is not None
    assert llm.last_timeout_s <= 1.2


class RecordingInterpreter:
    def __init__(self, out):
        self.out, self.seen = out, None

    def interpret(self, text):
        self.seen = text
        return self.out


class RecordingRewriter:
    def __init__(self, out):
        self.out, self.seen = out, None

    def rewrite(self, query, history):
        self.seen = query
        return self.out


def test_interpreter_and_rewriter_feed_retrieval(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    retriever = FakeRetriever(result)
    interpreter = RecordingInterpreter("geopos no carga")
    rewriter = RecordingRewriter("geopos no abre ni carga")
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        retriever,
        llm,
        FakeDetector(),
        interpreter=interpreter,
        rewriter=rewriter,
    )
    service.handle(MESSAGE)
    assert interpreter.seen == MESSAGE.text
    assert rewriter.seen == "geopos no carga"
    assert retriever.seen_question == "geopos no abre ni carga"


def test_generation_receives_original_question_not_rewrite(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    rewriter = RecordingRewriter("consulta inyectada")
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        FakeRetriever(result),
        llm,
        FakeDetector(),
        rewriter=rewriter,
    )
    service.handle(MESSAGE)
    user_msgs = [m["content"] for m in llm.messages if m["role"] == "user"]
    assert any(MESSAGE.text in m for m in user_msgs)
    assert not any("consulta inyectada" in m for m in user_msgs)


def test_budget_split_follows_total_budget_ms(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        FakeRetriever(result),
        llm,
        FakeDetector(),
        max_llm_ms=100_000,
        total_budget_ms=2000,
    )
    service.handle(MESSAGE)
    # generación = 65 % de 2000 = 1300 ms
    assert llm.last_timeout_s <= 1.3


def test_generation_asks_for_long_enriched_answers(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    make_service(tmp_path, result, llm).handle(MESSAGE)
    assert llm.last_max_tokens == 500


def test_generation_receives_interpreted_question(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    interpreter = RecordingInterpreter("geopos no carga")
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        FakeRetriever(result),
        llm,
        FakeDetector(),
        interpreter=interpreter,
    )
    service.handle(MESSAGE)
    user_msgs = [m["content"] for m in llm.messages if m["role"] == "user"]
    assert any("geopos no carga" in m for m in user_msgs)
