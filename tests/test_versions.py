import hashlib
import json

import pytest

from geoassist.storage.versions import (
    CorpusIntegrityError,
    VersionManager,
    publish_version,
    read_current,
    write_current_atomic,
)


def make_build(tmp_path, name="v1", db=b"db", index=b"index"):
    build = tmp_path / f"build-{name}"
    build.mkdir()
    (build / "corpus.db").write_bytes(db)
    (build / "faiss.index").write_bytes(index)
    (build / "manifest.json").write_text(
        json.dumps(
            {
                "version_id": name,
                "doc_count": 3,
                "chunk_count": 10,
                "db_sha256": hashlib.sha256(db).hexdigest(),
                "index_sha256": hashlib.sha256(index).hexdigest(),
            }
        )
    )
    return build


def test_publish_moves_build_and_writes_current(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    build = make_build(tmp_path)
    version = publish_version(build, corpus / "versions", "v1")
    write_current_atomic(corpus, version)
    assert version.db_path.exists()
    assert read_current(corpus).version_id == "v1"
    assert not list(corpus.glob("*.tmp"))


def test_only_last_three_versions_are_kept(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("v1", "v2", "v3", "v4"):
        build = make_build(tmp_path, name=name, db=name.encode(), index=name.encode())
        published = publish_version(build, corpus / "versions", name)
        write_current_atomic(corpus, published)
    assert sorted(path.name for path in (corpus / "versions").iterdir()) == ["v2", "v3", "v4"]


def test_activate_rolls_back_to_an_existing_version(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    manager = VersionManager(corpus)
    for name in ("v1", "v2"):
        build = make_build(tmp_path, name=name, db=name.encode(), index=name.encode())
        published = publish_version(build, corpus / "versions", name)
        write_current_atomic(corpus, published)
    activated = manager.activate("v1")
    assert activated.version_id == "v1"
    assert read_current(corpus).version_id == "v1"


def test_integrity_failure_keeps_previous_version(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    manager = VersionManager(corpus)
    build = make_build(tmp_path, db=b"db")
    version = publish_version(build, corpus / "versions", "v1")
    write_current_atomic(corpus, version)
    assert manager.refresh(force=True).version_id == "v1"
    version.db_path.write_bytes(b"corrupt")
    with pytest.raises(CorpusIntegrityError):
        manager.refresh(force=True)
    assert manager.current().version_id == "v1"
