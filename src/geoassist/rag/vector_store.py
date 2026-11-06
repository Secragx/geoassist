"""Índice vectorial FAISS con identificadores estables de fila.

El índice usa producto interno sobre vectores normalizados en L2, de modo que
la similitud equivale al coseno. Cada vector se asocia al ``chunk_row_id`` de
la base de datos mediante ``IndexIDMap2``, que FAISS persiste junto con el
índice. La escritura es atómica: se publica un temporal del mismo directorio
con ``os.replace`` para que un fallo a mitad de la ingesta nunca deje un índice
parcial visible.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import faiss  # type: ignore[import-untyped]
import numpy as np

_L2_EPSILON = np.float32(1e-12)


class VectorStoreError(RuntimeError):
    """Error al construir, leer o consultar el índice vectorial."""


@dataclass(frozen=True, slots=True)
class VectorHit:
    """Coincidencia del índice: fila del chunk y similitud coseno."""

    chunk_row_id: int
    score: float


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """Normaliza cada fila en L2; los vectores nulos permanecen en cero."""

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.asarray(matrix / np.maximum(norms, _L2_EPSILON), dtype=np.float32)


def _normalize_vector(vector: np.ndarray) -> np.ndarray:
    """Normaliza una consulta en L2; un vector nulo permanece en cero."""

    norm = np.linalg.norm(vector)
    return np.asarray(vector / max(float(norm), float(_L2_EPSILON)), dtype=np.float32)


def _validated_matrix(vectors: np.ndarray) -> np.ndarray:
    """Convierte ``vectors`` en una matriz float32 finita de dos dimensiones."""

    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2:
        raise VectorStoreError("vectors debe tener forma (n, dimension)")
    if matrix.shape[1] == 0:
        raise VectorStoreError("vectors debe tener al menos una columna de dimensión")
    if not np.all(np.isfinite(matrix)):
        raise VectorStoreError("vectors contiene valores no finitos")
    return matrix


def _validated_ids(ids: Sequence[int], expected: int) -> np.ndarray:
    """Valida que los ids sean enteros no negativos, uno por vector."""

    id_array = np.asarray(ids, dtype=np.int64)
    if id_array.ndim != 1:
        raise VectorStoreError("ids debe ser un vector de una dimensión")
    if id_array.shape[0] != expected:
        raise VectorStoreError(f"se esperaban {expected} ids, se recibieron {id_array.shape[0]}")
    if np.any(id_array < 0):
        raise VectorStoreError("los ids deben ser enteros no negativos")
    return id_array


class VectorStore:
    """Índice FAISS persistido en disco, consultable por similitud coseno."""

    def __init__(self, index_path: Path) -> None:
        self._path = Path(index_path)
        if not self._path.is_file():
            raise VectorStoreError(f"no existe el índice vectorial: {self._path}")
        try:
            index = faiss.read_index(str(self._path))
        except Exception as exc:  # FAISS no expone un tipo de error estable.
            raise VectorStoreError(f"no se pudo leer el índice vectorial: {self._path}") from exc
        self._index = index
        self._dimension = int(index.d)

    @property
    def dimension(self) -> int:
        """Dimensión de los vectores almacenados."""

        return self._dimension

    @staticmethod
    def build(index_path: Path, vectors: np.ndarray, ids: Sequence[int]) -> None:
        """Construye y publica atómicamente un índice de producto interno."""

        path = Path(index_path)
        matrix = _validated_matrix(vectors)
        id_array = _validated_ids(ids, matrix.shape[0])

        index = faiss.IndexIDMap2(faiss.IndexFlatIP(matrix.shape[1]))
        index.add_with_ids(_normalize_rows(matrix), id_array)

        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        os.close(descriptor)
        try:
            faiss.write_index(index, temporary_name)
            os.replace(temporary_name, path)
        except Exception as exc:  # FAISS usa RuntimeError para errores de E/S.
            raise VectorStoreError(f"no se pudo escribir el índice: {path}") from exc
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def search(self, query: Sequence[float], limit: int) -> list[VectorHit]:
        """Devuelve hasta ``limit`` coincidencias ordenadas por score descendente."""

        if limit < 0:
            raise VectorStoreError("limit no puede ser negativo")

        vector = np.asarray(query, dtype=np.float32)
        if vector.ndim != 1:
            raise VectorStoreError("query debe ser un vector de una dimensión")
        if vector.shape[0] != self._dimension:
            raise VectorStoreError(
                f"se esperaba una consulta de {self._dimension} dimensiones, "
                f"se recibieron {vector.shape[0]}"
            )
        if not np.all(np.isfinite(vector)):
            raise VectorStoreError("la consulta contiene valores no finitos")

        if limit == 0 or self._index.ntotal == 0:
            return []

        # Se acota ``k`` al tamaño del índice para no reservar resultados de
        # relleno; aun así se descartan los ids ``-1`` que FAISS usa como padding.
        k = min(limit, int(self._index.ntotal))
        scores, row_ids = self._index.search(_normalize_vector(vector).reshape(1, -1), k)
        hits = [
            VectorHit(chunk_row_id=int(row_id), score=float(score))
            for score, row_id in zip(scores[0], row_ids[0], strict=True)
            if int(row_id) != -1
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits
