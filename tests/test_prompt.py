from geoassist.models import ChatMessage
from geoassist.rag.prompt import build_fallback, build_messages, format_sources
from geoassist.rag.retriever import RetrievedChunk

CHUNK = RetrievedChunk(
    1,
    "d:0",
    "d",
    "Sección",
    "Texto útil",
    "Manual",
    "1.0",
    "pending",
    "geopos",
    "a.md",
    0.9,
    1,
    1,
    0.8,
)


def test_context_is_numbered_and_marks_pending():
    messages = build_messages("¿Cómo hago?", [ChatMessage("user", "antes", "t")], [CHUNK])
    assert messages[0]["role"] == "system"
    assert "no confiable" in messages[0]["content"].lower()
    user = messages[-1]["content"]
    assert "[1]" in user and "Manual" in user and "por aprobar" in user
    assert "¿Cómo hago?" in user


def test_history_keeps_roles_and_order():
    messages = build_messages(
        "ahora", [ChatMessage("user", "antes", "t"), ChatMessage("assistant", "ok", "t")], [CHUNK]
    )
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_sources_include_version_and_state():
    assert format_sources([CHUNK]) == "[1] Manual — v1.0 (por aprobar)"


def test_fallback_mentions_reason_and_top_chunk():
    text = build_fallback([CHUNK], "low_score")
    assert "no pude generar" in text.lower()
    assert "Manual" in text


def test_system_prompt_requests_complete_enriched_procedures():
    from geoassist.rag.prompt import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "completo" in lowered
    assert "todos los pasos" in lowered
    assert "4-5 oraciones" not in lowered


def test_system_prompt_maps_coloquial_names_and_answers_partial_context():
    from geoassist.rag.prompt import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "coloquial" in lowered
    assert "parcial" in lowered
