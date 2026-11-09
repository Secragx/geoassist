"""Cliente compatible con la API de chat completions de OpenAI."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from geoassist.llm.base import LLMError

_RETRY_DELAY_SECONDS = 0.3
_RETRYABLE_STATUS_CODES = frozenset({429})


def _is_retryable_status(status: int) -> bool:
    return status in _RETRYABLE_STATUS_CODES or status >= 500


class OpenAICompatibleClient:
    """Cliente síncrono usando ``httpx`` sin streaming.

    Reintenta una vez únicamente cuando el servidor responde con ``429`` o
    cualquier código ``5xx``. Los errores de timeout o de conexión no se
    reintentan.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], Any] = time.sleep,
    ) -> None:
        self._model = model
        self._sleeper = sleeper
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        timeout_s: float,
        max_tokens: int = 500,
        temperature: float = 0.1,
        max_attempts: int = 2,
    ) -> str:
        """Envía los mensajes y devuelve el texto generado.

        ``max_attempts`` acota los reintentos ante 429/5xx (1 = sin reintentar,
        para llamadas con presupuesto estricto como la reescritura).
        """

        body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.post(
                    "/v1/chat/completions",
                    json=body,
                    timeout=timeout_s,
                )
            except httpx.TimeoutException as exc:
                raise LLMError("timeout", "LLM request timed out") from exc
            except httpx.NetworkError as exc:
                raise LLMError("http", f"LLM connection error: {exc}") from exc
            except httpx.HTTPError as exc:
                raise LLMError("http", f"LLM request failed: {exc}") from exc

            if response.is_success:
                return self._parse_content(response)

            status = response.status_code
            if attempt < max_attempts and _is_retryable_status(status):
                self._sleeper(_RETRY_DELAY_SECONDS)
                continue

            raise LLMError("http", f"LLM returned status {status}")

        # El bucle siempre termina con ``return`` o ``raise``; este punto es
        # inalcanzable pero mantiene a mypy satisfecho.
        raise LLMError("http", "LLM request failed after retries")

    def _parse_content(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMError("invalid_response", "LLM response is not valid JSON") from exc

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("invalid_response", "LLM response has no choices")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise LLMError("invalid_response", "LLM response choice has no message")

        content = message.get("content")
        if not isinstance(content, str):
            raise LLMError("invalid_response", "LLM response message has no content")

        return content
