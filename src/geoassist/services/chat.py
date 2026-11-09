"""Servicio de chat: orquesta recuperación, generación y persistencia."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from geoassist.llm.base import LLMClient, LLMError
from geoassist.logging import get_logger
from geoassist.models import InboundMessage
from geoassist.rag.apps import AppDetector
from geoassist.rag.prompt import build_fallback, build_messages
from geoassist.rag.retriever import Retriever
from geoassist.rag.rewrite import LocalQueryInterpreter, QueryRewriter
from geoassist.storage.state import StateStore
from geoassist.storage.state import session_key as _build_session_key

logger = get_logger(__name__)

_TOTAL_BUDGET_MS = 8000
_RETRIEVAL_SHARE = 0.20
_GENERATION_SHARE = 0.65
_STATE_SHARE = 0.10

_MAX_LLM_MS = 5000


def _completion_tokens_for(llm_timeout_s: float) -> int:
    """Ajusta el tope de generación al timeout disponible.

    Con ~130-150 tok/s y ~0.8 s de primer token, el presupuesto útil es
    (timeout - 0.8) s; 120 tok/s deja margen. Así los perfiles de
    presupuesto reducido reciben respuestas más cortas en vez de timeouts.
    """
    usable = max(0.0, llm_timeout_s - 0.8) * 120
    return max(150, min(_MAX_COMPLETION_TOKENS, int(usable)))


# Generación acotada para respuestas largas: el prompt pide el procedimiento
# completo y 500 tokens es el techo que DeepSeek cierra dentro del presupuesto
# de generación con TOTAL_BUDGET_MS=8000.
_MAX_COMPLETION_TOKENS = 500

_ABSTENTION_MESSAGE = (
    "No encontré información relevante para responder. "
    "Intenta reformular la pregunta o contacta a soporte."
)


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    """Resultado de procesar un mensaje entrante."""

    text: str
    degraded: bool
    sources: tuple[dict[str, Any], ...]
    latency_ms: int


@dataclass(frozen=True, slots=True)
class Deadline:
    """Presupuesto de tiempo para una fase del pipeline."""

    budget_ms: int
    clock: Callable[[], float] = time.monotonic
    _start: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_start", self.clock())

    def remaining_ms(self) -> int:
        """Milisegundos que quedan antes de agotar el presupuesto."""

        elapsed_ms = int((self.clock() - self._start) * 1000)
        remaining = self.budget_ms - elapsed_ms
        return remaining if remaining > 0 else 0

    def expired(self) -> bool:
        """Indica si el presupuesto ya se agotó."""

        return self.remaining_ms() == 0


class ChatService:
    """Orquesta recuperación, generación y persistencia de una conversación."""

    def __init__(
        self,
        state: StateStore,
        retriever: Retriever,
        llm: LLMClient,
        detector: AppDetector | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        session_ttl_minutes: int = 120,
        max_llm_ms: int = _MAX_LLM_MS,
        interpreter: LocalQueryInterpreter | None = None,
        rewriter: QueryRewriter | None = None,
        total_budget_ms: int = _TOTAL_BUDGET_MS,
    ) -> None:
        self._state = state
        self._retriever = retriever
        self._llm = llm
        self._detector = detector
        self._clock = clock
        self._session_ttl_minutes = session_ttl_minutes
        self._max_llm_ms = max_llm_ms
        self._interpreter = interpreter
        self._rewriter = rewriter
        self._retrieval_budget_ms = int(total_budget_ms * _RETRIEVAL_SHARE)
        self._generation_budget_ms = int(total_budget_ms * _GENERATION_SHARE)
        self._state_budget_ms = int(total_budget_ms * _STATE_SHARE)

    def handle(self, message: InboundMessage) -> ChatOutcome:
        """Resuelve una pregunta y devuelve la respuesta con metadatos."""

        start = self._clock()
        now = datetime.now(UTC)

        session_key = _build_session_key(
            message.tenant_id, message.conversation_id, message.user_id
        )
        session = self._state.get_session(session_key, now)

        interpreted = (
            self._interpreter.interpret(message.text)
            if self._interpreter is not None
            else message.text
        )
        query = (
            self._rewriter.rewrite(interpreted, session.history)
            if self._rewriter is not None
            else interpreted
        )

        app_filter = None
        if self._detector is not None:
            app_filter = self._detector.detect(interpreted)
        if app_filter is None:
            app_filter = session.app_hint

        retrieval_deadline = Deadline(self._retrieval_budget_ms, self._clock)
        result = self._retriever.retrieve(query, app_filter=app_filter)
        if retrieval_deadline.expired():
            logger.warning(
                "retrieval_exceeded_budget",
                session_key=session_key,
                elapsed_ms=retrieval_deadline.budget_ms - retrieval_deadline.remaining_ms(),
            )

        if not result.sufficient:
            sources = _chunks_to_sources(result.chunks)
            answer = _ABSTENTION_MESSAGE
            self._persist_turn(
                session_key=session_key,
                message=message,
                answer=answer,
                sources=sources,
                app_hint=app_filter,
                now=now,
            )
            return ChatOutcome(
                text=answer,
                degraded=False,
                sources=sources,
                latency_ms=_elapsed_ms(start, self._clock()),
            )

        messages = build_messages(interpreted, session.history, result.chunks)
        generation_deadline = Deadline(self._generation_budget_ms, self._clock)
        llm_timeout_ms = min(self._max_llm_ms, generation_deadline.remaining_ms())
        llm_timeout_s = max(0.0, llm_timeout_ms / 1000.0)

        try:
            answer = self._llm.complete(
                messages,
                timeout_s=llm_timeout_s,
                max_tokens=_completion_tokens_for(llm_timeout_s),
                temperature=0.1,
            )
            degraded = False
        except LLMError as exc:
            logger.warning(
                "llm_generation_failed",
                session_key=session_key,
                error_kind=exc.kind,
                error_message=exc.message,
            )
            answer = build_fallback(result.chunks, f"error del modelo: {exc.kind}")
            degraded = True

        sources = _chunks_to_sources(result.chunks)
        state_deadline = Deadline(self._state_budget_ms, self._clock)
        self._persist_turn(
            session_key=session_key,
            message=message,
            answer=answer,
            sources=sources,
            app_hint=app_filter,
            now=now,
        )
        if state_deadline.expired():
            logger.warning(
                "state_persistence_exceeded_budget",
                session_key=session_key,
            )

        return ChatOutcome(
            text=answer,
            degraded=degraded,
            sources=sources,
            latency_ms=_elapsed_ms(start, self._clock()),
        )

    def _persist_turn(
        self,
        *,
        session_key: str,
        message: InboundMessage,
        answer: str,
        sources: tuple[dict[str, Any], ...],
        app_hint: str | None,
        now: datetime,
    ) -> None:
        self._state.append_turn(
            session_key=session_key,
            tenant_id=message.tenant_id,
            conversation_id=message.conversation_id,
            user_id=message.user_id,
            question=message.text,
            answer=answer,
            sources=sources,
            app_hint=app_hint,
            now=now,
        )


def _chunks_to_sources(chunks: tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
    """Convierte chunks recuperados en fuentes serializables."""

    return tuple(asdict(chunk) for chunk in chunks)


def _elapsed_ms(start: float, end: float) -> int:
    """Diferencia entre dos marcas de tiempo en milisegundos enteros."""

    return int((end - start) * 1000)
