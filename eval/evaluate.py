"""CLI de evaluación del set dorado de GeoAssist."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geoassist.config import get_settings  # noqa: E402
from geoassist.eval.metrics import GoldenCase, evaluate  # noqa: E402
from geoassist.rag.embeddings import OnnxBgeM3Embedder  # noqa: E402
from geoassist.rag.rerank import OnnxCrossEncoderReranker  # noqa: E402
from geoassist.rag.retriever import Retriever  # noqa: E402
from geoassist.rag.vector_store import VectorStore  # noqa: E402
from geoassist.storage.corpus_read import CorpusReader  # noqa: E402
from geoassist.storage.versions import VersionManager  # noqa: E402


def _load_golden(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            GoldenCase(
                id=raw.get("id", ""),
                question=raw.get("question", ""),
                app=raw.get("app"),
                expected_docs=list(raw.get("expected_docs", [])),
                expected_facts=list(raw.get("expected_facts", [])),
                must_abstain=bool(raw.get("must_abstain", False)),
                notes=raw.get("notes", ""),
            )
        )
    return cases


def _build_retriever() -> Retriever:
    settings = get_settings()
    corpus_dir = settings.data_dir / "corpus"
    version = VersionManager(corpus_dir).refresh()
    reader = CorpusReader(version.db_path)
    store = VectorStore(version.index_path)
    embedder = OnnxBgeM3Embedder(Path(settings.embedding_model_path))
    try:
        reranker = OnnxCrossEncoderReranker(Path(settings.rerank_model_path))
    except Exception:  # noqa: BLE001
        reranker = None
    return Retriever(reader, store, embedder, reranker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evalúa el set dorado de GeoAssist.")
    parser.add_argument(
        "--golden",
        type=Path,
        default=ROOT / "eval" / "golden.jsonl",
        help="Ruta al archivo JSONL del set dorado.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "eval" / "baseline.json",
        help="Ruta al baseline JSON.",
    )
    args = parser.parse_args()

    golden = _load_golden(args.golden)
    retriever = _build_retriever()
    metrics = evaluate(golden, retriever)

    payload = {
        "recall_at_5": metrics.recall_at_5,
        "mrr_at_10": metrics.mrr_at_10,
        "ndcg_at_10": metrics.ndcg_at_10,
        "app_accuracy": metrics.app_accuracy,
        "abstention_accuracy": metrics.abstention_accuracy,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    baseline_recall = float(baseline.get("recall_at_5", 0.0))
    if metrics.recall_at_5 < baseline_recall - 0.02:
        print(
            "recall_at_5 "
            f"{metrics.recall_at_5:.4f} cayó más de 0.02 respecto del baseline "
            f"{baseline_recall:.4f}",
            file=sys.stderr,
        )
        return 1
    if metrics.abstention_accuracy < 1.0:
        print(
            f"abstention_accuracy {metrics.abstention_accuracy:.4f} < 1.0",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
