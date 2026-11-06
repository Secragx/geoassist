"""Tipos compartidos del módulo RAG para evitar imports circulares."""

from __future__ import annotations

from geoassist.rag.vector_store import VectorHit
from geoassist.storage.corpus_read import LexicalHit

__all__ = ["LexicalHit", "VectorHit"]
