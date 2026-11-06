import numpy as np
import pytest

from geoassist.rag.vector_store import VectorStore, VectorStoreError


def test_build_and_search_returns_ids_and_scores(tmp_path):
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    path = tmp_path / "faiss.index"
    VectorStore.build(path, vectors, ids=[10, 20])
    hits = VectorStore(path).search([1.0, 0.0], limit=2)
    assert [hit.chunk_row_id for hit in hits] == [10, 20]
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)


def test_limit_larger_than_index_is_safe(tmp_path):
    path = tmp_path / "faiss.index"
    VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[1])
    assert len(VectorStore(path).search([1.0, 0.0], limit=10)) == 1


def test_dimension_mismatch_raises(tmp_path):
    path = tmp_path / "faiss.index"
    VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[1])
    with pytest.raises(VectorStoreError):
        VectorStore(path).search([1.0, 0.0, 0.0], limit=1)


def test_build_normalizes_rows_and_orders_hits_by_descending_score(tmp_path):
    path = tmp_path / "faiss.index"
    vectors = np.array([[2.0, 0.0], [0.0, 3.0], [1.0, 1.0]], dtype=np.float32)
    VectorStore.build(path, vectors, ids=[10, 20, 30])

    hits = VectorStore(path).search([1.0, 0.0], limit=3)

    assert [hit.chunk_row_id for hit in hits] == [10, 30, 20]
    assert [hit.score for hit in hits] == pytest.approx([1.0, 2**-0.5, 0.0], abs=1e-6)


def test_search_normalizes_the_query(tmp_path):
    path = tmp_path / "faiss.index"
    VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[7])

    hits = VectorStore(path).search([10.0, 0.0], limit=1)

    assert hits[0].score == pytest.approx(1.0, abs=1e-6)


def test_limit_zero_and_empty_index_return_no_hits(tmp_path):
    path = tmp_path / "faiss.index"
    VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[1])
    store = VectorStore(path)

    assert store.search([1.0, 0.0], limit=0) == []

    empty_path = tmp_path / "empty.index"
    VectorStore.build(empty_path, np.zeros((0, 2), dtype=np.float32), ids=[])
    empty_store = VectorStore(empty_path)

    assert empty_store.dimension == 2
    assert empty_store.search([1.0, 0.0], limit=5) == []


def test_negative_limit_raises(tmp_path):
    path = tmp_path / "faiss.index"
    VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[1])

    with pytest.raises(VectorStoreError):
        VectorStore(path).search([1.0, 0.0], limit=-1)


def test_missing_and_corrupt_index_raise(tmp_path):
    with pytest.raises(VectorStoreError):
        VectorStore(tmp_path / "missing.index")

    corrupt = tmp_path / "corrupt.index"
    corrupt.write_bytes(b"not a faiss index")

    with pytest.raises(VectorStoreError):
        VectorStore(corrupt)


def test_build_validates_vectors_and_ids(tmp_path):
    path = tmp_path / "faiss.index"

    with pytest.raises(VectorStoreError):
        VectorStore.build(path, np.array([1.0, 0.0], dtype=np.float32), ids=[1])
    with pytest.raises(VectorStoreError):
        VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[1, 2])
    with pytest.raises(VectorStoreError):
        VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[-1])
    with pytest.raises(VectorStoreError):
        VectorStore.build(path, np.array([[np.nan, 0.0]], dtype=np.float32), ids=[1])


def test_failed_write_leaves_no_partial_or_temporary_index(tmp_path, monkeypatch):
    import geoassist.rag.vector_store as vector_store

    path = tmp_path / "faiss.index"

    def failing_write(index, filename):
        with open(filename, "wb") as handle:
            handle.write(b"parcial")
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(vector_store.faiss, "write_index", failing_write)

    with pytest.raises(VectorStoreError):
        VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[1])

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_build_replaces_an_existing_index(tmp_path):
    path = tmp_path / "faiss.index"
    VectorStore.build(path, np.array([[1.0, 0.0]], dtype=np.float32), ids=[1])
    VectorStore.build(path, np.array([[0.0, 1.0]], dtype=np.float32), ids=[2])

    hits = VectorStore(path).search([0.0, 1.0], limit=1)

    assert [hit.chunk_row_id for hit in hits] == [2]
