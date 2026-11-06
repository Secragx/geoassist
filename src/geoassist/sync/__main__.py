"""CLI de sincronización y activación/rollback del corpus."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from geoassist.config import get_settings
from geoassist.storage.versions import CorpusIntegrityError, VersionManager


def _rules_path() -> Path:
    """Resuelve la ruta de las reglas del corpus en la imagen o en desarrollo."""

    image_path = Path("/app/config/corpus_rules.yml")
    if image_path.is_file():
        return image_path

    src_relative = Path(__file__).resolve().parents[3] / "config" / "corpus_rules.yml"
    if src_relative.is_file():
        return src_relative

    raise FileNotFoundError("No se encontró config/corpus_rules.yml")


def _build_ingestor(repo_dir: Path) -> Any:
    # Importaciones diferidas: solo se necesitan durante la sincronización.
    from geoassist.ingest.chunker import Chunker
    from geoassist.ingest.loader import ManualLoader
    from geoassist.ingest.metadata import MetadataResolver
    from geoassist.ingest.pipeline import Ingestor
    from geoassist.ingest.rules import CorpusRules
    from geoassist.rag.embeddings import OnnxBgeM3Embedder
    from geoassist.rag.tokenization import XlmRobertaTokenCounter

    settings = get_settings()
    rules = CorpusRules.load(_rules_path())
    model_dir = Path(settings.embedding_model_path)
    counter = XlmRobertaTokenCounter(model_dir / "tokenizer.json")

    return Ingestor(
        loader=ManualLoader(
            repo_dir,
            settings.manuals_root_rel,
            rules,
            assets_root_rel=settings.assets_root_rel,
        ),
        chunker=Chunker(counter),
        resolver=MetadataResolver(rules),
        embedder=OnnxBgeM3Embedder(model_dir=model_dir, threads=4),
        counter=counter,
    )


def _sync(args: argparse.Namespace) -> int:
    from geoassist.sync.orchestrator import SyncOrchestrator

    settings = get_settings()
    repo_dir = settings.manuals_dir
    corpus_dir = settings.data_dir / "corpus"
    ingestor = _build_ingestor(repo_dir)

    orchestrator = SyncOrchestrator(
        repo_dir=repo_dir,
        repo_url=settings.sync_repo_url,
        corpus_dir=corpus_dir,
        ingestor=ingestor,
        clock=time.monotonic,
    )
    report = orchestrator.run(no_pdf=args.no_pdf)
    return report.exit_code


def _activate(args: argparse.Namespace) -> int:
    settings = get_settings()
    corpus_dir = settings.data_dir / "corpus"

    try:
        version = VersionManager(corpus_dir).activate(args.version_id)
    except CorpusIntegrityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"activada {version.version_id} ({version.git_commit})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza y activa versiones del corpus.")
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Omite la generación de PDFs al finalizar la sincronización.",
    )
    subparsers = parser.add_subparsers(dest="command")

    activate_parser = subparsers.add_parser("activate", help="Activa una versión publicada.")
    activate_parser.add_argument("version_id", help="Identificador de la versión a activar.")

    args = parser.parse_args()

    if args.command == "activate":
        return _activate(args)
    return _sync(args)


if __name__ == "__main__":
    sys.exit(main())
