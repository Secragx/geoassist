import hashlib
import json

from geoassist.sync.orchestrator import SyncOrchestrator


class FakeIngestor:
    def build(self, version_id, output_dir, git_commit=None):
        output_dir.mkdir(parents=True)
        db = b"db"
        index = b"index"
        (output_dir / "corpus.db").write_bytes(db)
        (output_dir / "faiss.index").write_bytes(index)
        (output_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "version_id": version_id,
                    "doc_count": 3,
                    "chunk_count": 9,
                    "db_sha256": hashlib.sha256(db).hexdigest(),
                    "index_sha256": hashlib.sha256(index).hexdigest(),
                    "git_commit": git_commit or version_id,
                }
            )
        )

        class Report:
            doc_count = 3
            chunk_count = 9
            warnings = ()
            skipped = ()

        return Report()


def test_success_writes_report_and_publishes(tmp_path, monkeypatch):
    monkeypatch.setattr("geoassist.sync.orchestrator.update_repo", lambda *a, **k: "abc123")
    orch = SyncOrchestrator(tmp_path / "repo", "url", tmp_path / "corpus", FakeIngestor())
    report = orch.run(no_pdf=True)
    assert report.exit_code == 0
    assert (tmp_path / "corpus" / "current.json").exists()
    assert (
        json.loads((tmp_path / "corpus" / "sync_report.json").read_text())["git_commit"] == "abc123"
    )
