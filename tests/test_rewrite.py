"""Pruebas de la interpretación local de consultas (jerga y typos)."""

from geoassist.models import ChatMessage
from geoassist.rag.rewrite import LocalQueryInterpreter, QueryRewriter

ALIASES = {"geoconsola": "geopos", "consola": "geopos", "inventario": "geoinventarios"}
VOCAB = ["geopos", "geoinventarios", "devolucion", "cedis", "impresora", "remesa"]


def test_jerga_maps_to_canonical_app():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("la geoconsola no carga")
    assert "geopos" in out
    assert "geoconsola" not in out


def test_typo_in_product_name_is_corrected():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("geopcos no abre")
    assert "geopos" in out


def test_typo_in_case_term_is_corrected():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("no puedo hacer la devoLucion")
    assert "devolucion" in out


def test_valid_uncommon_words_are_not_mangled():
    original = "error en la impresora del local grande"
    assert LocalQueryInterpreter(ALIASES, VOCAB).interpret(original) == original


def test_short_tokens_are_untouched():
    original = "el pos no va"
    assert LocalQueryInterpreter(ALIASES, VOCAB).interpret(original) == original


class RecordingLLM:
    def __init__(self, answer="geopos no carga", error=None):
        self.answer, self.error, self.calls = answer, error, 0
        self.last = None

    def complete(self, messages, *, timeout_s, max_tokens=500, temperature=0.1, max_attempts=2):
        self.calls += 1
        self.last = {
            "messages": messages,
            "timeout_s": timeout_s,
            "max_tokens": max_tokens,
            "max_attempts": max_attempts,
        }
        if self.error:
            raise self.error
        return self.answer


def test_rewrite_uses_llm_with_short_output_and_timeout():
    llm = RecordingLLM()
    out = QueryRewriter(llm).rewrite("geoconsola no crga", [])
    assert out == "geopos no carga"
    assert llm.last["max_tokens"] <= 96
    assert llm.last["timeout_s"] <= 1.2


def test_rewrite_falls_back_to_original_on_error():
    from geoassist.llm.base import LLMError

    llm = RecordingLLM(error=LLMError("timeout", "boom"))
    assert QueryRewriter(llm).rewrite("hola", []) == "hola"


def test_rewrite_falls_back_on_empty_or_oversized():
    assert QueryRewriter(RecordingLLM(answer="")).rewrite("hola", []) == "hola"
    assert QueryRewriter(RecordingLLM(answer="x" * 400)).rewrite("hola", []) == "hola"


def test_cache_avoids_second_call():
    llm = RecordingLLM()
    rewriter = QueryRewriter(llm)
    rewriter.rewrite("hola", [])
    rewriter.rewrite("hola", [])
    assert llm.calls == 1


def test_disabled_rewriter_returns_original_without_llm():
    llm = RecordingLLM()
    assert QueryRewriter(llm, enabled=False).rewrite("hola", []) == "hola"
    assert llm.calls == 0


def test_rewrite_sends_only_last_two_history_turns():
    llm = RecordingLLM()
    history = [
        ChatMessage(role="user", content=f"msg-{i}", created_at="2026-01-01T00:00:00Z")
        for i in range(4)
    ]
    QueryRewriter(llm).rewrite("hola", history)
    contents = [m["content"] for m in llm.last["messages"]]
    assert "msg-2" in contents
    assert "msg-3" in contents
    assert "msg-0" not in contents
    assert "msg-1" not in contents


def test_real_words_near_aliases_are_not_mangled():
    interpreter = LocalQueryInterpreter(ALIASES, VOCAB)
    assert interpreter.interpret("el corte no consolida") == "el corte no consolida"
    assert interpreter.interpret("voy a inventariar la tienda") == "voy a inventariar la tienda"


def test_rewrite_uses_single_attempt():
    llm = RecordingLLM()
    QueryRewriter(llm).rewrite("hola", [])
    assert llm.last["max_attempts"] == 1


def test_rewrite_prompt_translates_business_jerga():
    from geoassist.rag.rewrite import REWRITE_PROMPT

    lowered = REWRITE_PROMPT.lower()
    assert "cajero" in lowered
    assert "tienda" in lowered
    assert "usuario" in lowered
