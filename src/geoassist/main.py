"""Punto de entrada ASGI de GeoAssist."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from geoassist.api.app import AppDependencies, create_app
from geoassist.api.readiness import ReadinessProbe
from geoassist.config import Settings, get_settings
from geoassist.logging import configure_logging, get_logger
from geoassist.rag.embeddings import Embedder, OnnxBgeM3Embedder
from geoassist.rag.rerank import OnnxCrossEncoderReranker, Reranker
from geoassist.rag.retriever import Retriever
from geoassist.rag.vector_store import VectorStore
from geoassist.services.chat import ChatService
from geoassist.storage.corpus_read import CorpusReader
from geoassist.storage.state import StateStore
from geoassist.storage.versions import VersionManager
from geoassist.teams.outgoing_webhook import OutgoingWebhookAdapter

logger = get_logger(__name__)

_models_warm = False


def _build_dependencies(settings: Settings) -> AppDependencies:
    """Construye las dependencias reales a partir de la configuración."""

    from geoassist.ingest.rules import CorpusRules
    from geoassist.llm.openai_compatible import OpenAICompatibleClient
    from geoassist.rag.apps import build_app_detector

    adapter = OutgoingWebhookAdapter(
        settings.teams_security_token.get_secret_value(),
        settings.teams_bot_name,
    )
    state = StateStore(
        settings.data_dir / "state.db",
        session_ttl_minutes=settings.session_ttl_minutes,
    )

    versions = VersionManager(settings.data_dir / "corpus")
    version = versions.refresh()

    reader = CorpusReader(version.db_path)
    store = VectorStore(version.index_path)
    embedder: Embedder = OnnxBgeM3Embedder(Path(settings.embedding_model_path))
    reranker: Reranker = OnnxCrossEncoderReranker(Path(settings.rerank_model_path))

    _warmup_models(embedder, reranker)

    rules_path = Path(__file__).resolve().parents[2] / "config" / "corpus_rules.yml"
    rules = CorpusRules.load(rules_path)
    detector = build_app_detector(rules)

    retriever = Retriever(reader, store, embedder, reranker)

    llm = OpenAICompatibleClient(
        settings.llm_base_url,
        settings.llm_api_key.get_secret_value(),
        settings.llm_model,
    )
    chat = ChatService(
        state,
        retriever,
        llm,
        detector,
        session_ttl_minutes=settings.session_ttl_minutes,
    )

    return AppDependencies(
        settings=settings,
        adapter=adapter,
        chat=chat,
        retriever=retriever,
        versions=versions,
        state=state,
    )


def _warmup_models(embedder: Embedder, reranker: Reranker) -> None:
    """Ejecuta una pasada ficticia para calentar los modelos ONNX."""

    global _models_warm
    sample = "warmup"
    embedder.embed_query(sample)
    reranker.score(sample, [sample])
    _models_warm = True
    logger.info("models_warmup_complete")


def _build_probe(settings: Settings, deps: AppDependencies) -> ReadinessProbe:
    """Sonda de readiness con corpus, estado, modelos calientes y LLM."""

    def corpus_ready() -> bool:
        return deps.versions is not None and deps.versions.current() is not None

    def state_ready() -> bool:
        if deps.state is None:
            return False
        try:
            with deps.state._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:  # noqa: BLE001 - la sonda debe degradar, no romper
            return False

    def models_ready() -> bool:
        return _models_warm

    def llm_ready() -> bool:
        api_key = settings.llm_api_key.get_secret_value().strip()
        return bool(settings.llm_base_url.strip() and api_key)

    return ReadinessProbe(
        {
            "corpus": corpus_ready,
            "state": state_ready,
            "models": models_ready,
            "llm": llm_ready,
        }
    )


_settings = get_settings()
configure_logging(_settings.log_level)
_dependencies = _build_dependencies(_settings)
app: FastAPI = create_app(
    _settings, probe=_build_probe(_settings, _dependencies), dependencies=_dependencies
)
