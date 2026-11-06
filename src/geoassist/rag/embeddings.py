"""Embeddings densos con BGE-M3 exportado a ONNX.

El modelo se ejecuta en lotes con ``onnxruntime`` y el tokenizer XLM-R se carga
desde el ``tokenizer.json`` del artefacto. El pooling es CLS (primer token) y la
salida se normaliza en L2, de modo que el índice FAISS pueda usar producto
interno como similitud coseno.

La sesión y el tokenizer se pueden inyectar para pruebas o para reutilizar
instancias ya cargadas. En producción, sin inyección, se cargan ambos desde
``model_dir``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from tokenizers import Tokenizer

_MODEL_FILENAME = "model.onnx"
_TOKENIZER_FILENAME = "tokenizer.json"
_PAD_TOKEN = "<pad>"
_L2_EPSILON = np.float32(1e-12)


class EmbeddingDimensionError(ValueError):
    """Los vectores no tienen la dimensión esperada por el índice."""


class Embedder(Protocol):
    """Contrato mínimo que consumen la ingesta y el recuperador."""

    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embebe una secuencia de textos y devuelve un vector por texto."""

        ...

    def embed_query(self, text: str) -> list[float]:
        """Embebe una consulta y devuelve su vector."""

        ...


class _Encoding(Protocol):
    ids: list[int]


class _Tokenizer(Protocol):
    def enable_truncation(self, max_length: int, **kwargs: Any) -> None: ...

    def enable_padding(self, **kwargs: Any) -> None: ...

    def encode_batch(self, texts: Sequence[str]) -> Sequence[_Encoding]: ...


class _Session(Protocol):
    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, np.ndarray],
    ) -> Sequence[Any]: ...


def pool_cls_normalize(hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Aplica pooling CLS y normaliza cada vector en L2.

    ``attention_mask`` se recibe por simetría con el modelo y se valida contra
    el lote de ``hidden_state``; el token CLS nunca está enmascarado.
    """

    hidden = np.asarray(hidden_state, dtype=np.float32)
    if hidden.ndim != 3:
        raise ValueError("hidden_state debe tener forma (batch, tokens, dimension)")

    mask = np.asarray(attention_mask)
    if mask.ndim != 2 or mask.shape[0] != hidden.shape[0]:
        raise ValueError("attention_mask debe tener forma (batch, tokens)")

    cls_vectors = hidden[:, 0, :]
    norms = np.linalg.norm(cls_vectors, axis=1, keepdims=True)
    normalized = cls_vectors / np.maximum(norms, _L2_EPSILON)
    return np.asarray(normalized, dtype=np.float32)


def _declared_output_dimension(session: _Session) -> int | None:
    """Devuelve la dimensión declarada por el modelo ONNX, si es concreta."""

    get_outputs = getattr(session, "get_outputs", None)
    if not callable(get_outputs):
        return None

    outputs = get_outputs()
    if not outputs:
        return None

    shape = getattr(outputs[0], "shape", None)
    if not shape:
        return None

    last = shape[-1]
    if isinstance(last, int):
        return int(last)
    return None


def _resolve_pad_id(tokenizer: _Tokenizer) -> int:
    """Obtiene el id de ``<pad>`` del vocabulario del tokenizer."""

    token_to_id = getattr(tokenizer, "token_to_id", None)
    if callable(token_to_id):
        pad_id = token_to_id(_PAD_TOKEN)
        if isinstance(pad_id, int):
            return int(pad_id)
    return 0


def _create_session(model_path: Path, threads: int) -> ort.InferenceSession:
    """Crea una sesión de ONNX Runtime en CPU con los hilos indicados."""

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    return ort.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


class OnnxBgeM3Embedder:
    """Embedder BGE-M3 ONNX de 1024 dimensiones (pooling CLS + L2)."""

    dimension = 1024

    def __init__(
        self,
        model_dir: Path | None = None,
        *,
        session: _Session | None = None,
        tokenizer: _Tokenizer | None = None,
        max_length: int = 512,
        batch_size: int = 16,
        threads: int = 4,
    ) -> None:
        if max_length < 1:
            raise ValueError("max_length debe ser mayor que cero")
        if batch_size < 1:
            raise ValueError("batch_size debe ser mayor que cero")
        if threads < 1:
            raise ValueError("threads debe ser mayor que cero")

        if model_dir is None and (session is None or tokenizer is None):
            raise ValueError("model_dir es obligatorio cuando no se inyectan session y tokenizer")

        model_path = Path(model_dir) if model_dir is not None else None
        loaded_from_disk = session is None

        if tokenizer is None:
            assert model_path is not None
            tokenizer = cast(_Tokenizer, Tokenizer.from_file(str(model_path / _TOKENIZER_FILENAME)))
        if session is None:
            assert model_path is not None
            session = cast(_Session, _create_session(model_path / _MODEL_FILENAME, threads))

        self._session = session
        self._tokenizer = tokenizer
        self._batch_size = batch_size

        pad_id = _resolve_pad_id(tokenizer)
        tokenizer.enable_truncation(max_length=max_length)
        tokenizer.enable_padding(pad_id=pad_id, pad_token=_PAD_TOKEN)

        declared_dimension = _declared_output_dimension(session)
        if declared_dimension is not None and declared_dimension != self.dimension:
            raise EmbeddingDimensionError(
                f"se esperaban vectores de {self.dimension} dimensiones, "
                f"el modelo declara {declared_dimension}"
            )

        # Las sesiones falsas de las pruebas pueden usar dimensiones reducidas
        # y no exponen metadatos de salida; no se valida su ancho por vector.
        self._enforce_dimension = loaded_from_disk or declared_dimension is not None

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embebe los textos en lotes y devuelve un vector normalizado por texto."""

        pending = list(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(pending), self._batch_size):
            batch = pending[start : start + self._batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embebe una consulta reutilizando el camino de documentos."""

        return self.embed_documents([text])[0]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.asarray([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.asarray(
            [_attention_mask(encoding) for encoding in encodings], dtype=np.int64
        )

        outputs = self._session.run(
            None,
            {"input_ids": input_ids, "attention_mask": attention_mask},
        )
        hidden_state = np.asarray(outputs[0], dtype=np.float32)
        pooled = pool_cls_normalize(hidden_state, attention_mask)

        if self._enforce_dimension and pooled.shape[1] != self.dimension:
            raise EmbeddingDimensionError(
                f"se esperaban vectores de {self.dimension} dimensiones, "
                f"se obtuvieron {pooled.shape[1]}"
            )

        result: list[list[float]] = pooled.tolist()
        return result


def _attention_mask(encoding: _Encoding) -> list[int]:
    """Devuelve la máscara del tokenizer; los dobles de prueba usan todo unos."""

    mask = getattr(encoding, "attention_mask", None)
    if mask is None:
        return [1] * len(encoding.ids)
    return list(mask)
