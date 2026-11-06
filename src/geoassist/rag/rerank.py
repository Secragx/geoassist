"""Reranking con cross-encoder ONNX.

El reranker consume pares (consulta, pasaje), obtiene el logit del modelo y
lo transforma con una sigmoide numéricamente estable para devolver una puntuación
entre 0 y 1 por pasaje.

La sesión se inspecciona para saber qué entradas espera: siempre se envían
``input_ids`` y ``attention_mask``, y ``token_type_ids`` se incluye únicamente
cuando el modelo la declara.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from tokenizers import Tokenizer

_MODEL_FILENAME_INT8 = "model_int8.onnx"
_MODEL_FILENAME = "model.onnx"
_TOKENIZER_FILENAME = "tokenizer.json"
_PAD_TOKEN = "<pad>"
_DEFAULT_BATCH_SIZE = 16


class Reranker(Protocol):
    """Contrato mínimo que consumen el recuperador y el servicio de chat."""

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Devuelve una puntuación por cada pasaje respecto a la consulta."""

        ...


class _Encoding(Protocol):
    ids: list[int]
    attention_mask: list[int]
    type_ids: list[int]


class _Tokenizer(Protocol):
    def enable_truncation(self, max_length: int, **kwargs: Any) -> None: ...

    def enable_padding(self, **kwargs: Any) -> None: ...

    def encode_batch(self, pairs: Sequence[tuple[str, str]]) -> Sequence[_Encoding]: ...


class _Session(Protocol):
    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, np.ndarray],
    ) -> Sequence[Any]: ...


def sigmoid(x: np.ndarray) -> np.ndarray:
    """Sigmoide numéricamente estable, con salida en [0, 1]."""

    arr = np.asarray(x, dtype=np.float32)
    positive = arr >= 0
    negative = ~positive

    out = np.empty_like(arr, dtype=np.float32)

    z_pos = np.exp(-arr[positive])
    out[positive] = 1.0 / (1.0 + z_pos)

    z_neg = np.exp(arr[negative])
    out[negative] = z_neg / (1.0 + z_neg)

    return out


def _resolve_pad_id(tokenizer: _Tokenizer) -> int:
    """Obtiene el id de ``<pad>`` del vocabulario del tokenizer.

    Si el tokenizer no expone el token de relleno se lanza un error en lugar de
    asumir ``0``, que podría ser un id válido para otro token.
    """

    token_to_id = getattr(tokenizer, "token_to_id", None)
    if not callable(token_to_id):
        raise ValueError("el tokenizer no expone token_to_id; no se puede resolver el pad token")

    pad_id = token_to_id(_PAD_TOKEN)
    if not isinstance(pad_id, int):
        raise ValueError(f"el tokenizer no contiene el pad token '{_PAD_TOKEN}'")
    return int(pad_id)


def _resolve_model_path(model_dir: Path) -> Path:
    """Prefiere el modelo cuantizado int8 si existe; si no, usa ``model.onnx``."""

    int8_path = model_dir / _MODEL_FILENAME_INT8
    if int8_path.exists():
        return int8_path
    return model_dir / _MODEL_FILENAME


def _create_session(model_path: Path, threads: int) -> ort.InferenceSession:
    """Crea una sesión de ONNX Runtime en CPU con los hilos indicados."""

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    return ort.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


def _input_names(session: _Session) -> set[str]:
    """Devuelve los nombres de entrada declarados por la sesión ONNX."""

    get_inputs = getattr(session, "get_inputs", None)
    if callable(get_inputs):
        return {str(item.name) for item in get_inputs()}
    return {"input_ids", "attention_mask"}


class OnnxCrossEncoderReranker:
    """Reranker cross-encoder ONNX: un score por pasaje."""

    def __init__(
        self,
        model_dir: Path | str | None = None,
        *,
        session: _Session | None = None,
        tokenizer: _Tokenizer | None = None,
        max_length: int = 384,
        threads: int = 4,
    ) -> None:
        if max_length < 1:
            raise ValueError("max_length debe ser mayor que cero")
        if threads < 1:
            raise ValueError("threads debe ser mayor que cero")

        if model_dir is None and (session is None or tokenizer is None):
            raise ValueError("model_dir es obligatorio cuando no se inyectan session y tokenizer")

        model_path = Path(model_dir) if model_dir is not None else None

        if tokenizer is None:
            assert model_path is not None
            tokenizer = cast(_Tokenizer, Tokenizer.from_file(str(model_path / _TOKENIZER_FILENAME)))
        elif isinstance(tokenizer, Tokenizer):
            # Evita mutar un tokenizer inyectado trabajando sobre un clon.
            tokenizer = cast(_Tokenizer, Tokenizer.from_str(tokenizer.to_str()))

        if session is None:
            assert model_path is not None
            session = cast(_Session, _create_session(_resolve_model_path(model_path), threads))

        self._session = session
        self._tokenizer = tokenizer
        self._batch_size = _DEFAULT_BATCH_SIZE
        self._input_names = _input_names(session)

        pad_id = _resolve_pad_id(tokenizer)
        tokenizer.enable_truncation(max_length=max_length)
        tokenizer.enable_padding(pad_id=pad_id, pad_token=_PAD_TOKEN)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Devuelve la puntuación sigmoide de cada pasaje respecto a la consulta."""

        pending = list(passages)
        if not pending:
            return []

        scores: list[float] = []
        for start in range(0, len(pending), self._batch_size):
            batch = pending[start : start + self._batch_size]
            scores.extend(self._score_batch(query, batch))

        return scores

    def _score_batch(self, query: str, passages: list[str]) -> list[float]:
        pairs = [(query, passage) for passage in passages]
        encodings = self._tokenizer.encode_batch(pairs)

        input_ids = np.asarray([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.asarray(
            [_attention_mask(encoding) for encoding in encodings], dtype=np.int64
        )

        input_feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self._input_names:
            input_feed["token_type_ids"] = np.asarray(
                [_token_type_ids(encoding) for encoding in encodings], dtype=np.int64
            )

        outputs = self._session.run(None, input_feed)
        logits = np.asarray(outputs[0], dtype=np.float32)

        if logits.ndim == 2 and logits.shape[1] == 1:
            logits = logits.reshape(-1)

        return sigmoid(logits).tolist()


def _attention_mask(encoding: _Encoding) -> list[int]:
    """Devuelve la máscara del tokenizer; los dobles de prueba usan todo unos."""

    mask = getattr(encoding, "attention_mask", None)
    if mask is None:
        return [1] * len(encoding.ids)
    return list(mask)


def _token_type_ids(encoding: _Encoding) -> list[int]:
    """Devuelve los segment ids del tokenizer; los dobles sin ellos usan ceros."""

    type_ids = getattr(encoding, "type_ids", None)
    if type_ids is None:
        return [0] * len(encoding.ids)
    return list(type_ids)
