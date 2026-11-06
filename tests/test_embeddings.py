from types import SimpleNamespace

import numpy as np
import pytest

from geoassist.rag.embeddings import (
    EmbeddingDimensionError,
    OnnxBgeM3Embedder,
    pool_cls_normalize,
)


def test_cls_pooling_and_l2_normalization():
    hidden = np.array([[[3.0, 4.0], [99.0, 99.0]]], dtype=np.float32)
    mask = np.array([[1, 0]], dtype=np.int64)
    vector = pool_cls_normalize(hidden, mask)
    assert vector.shape == (1, 2)
    assert np.allclose(vector[0], [0.6, 0.8])
    assert np.isclose(np.linalg.norm(vector[0]), 1.0)


class FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class FakeTokenizer:
    def enable_truncation(self, **kwargs):
        pass

    def enable_padding(self, **kwargs):
        pass

    def encode_batch(self, texts):
        return [FakeEncoding([1, 2, 3]) for _ in texts]


class FakeSession:
    def run(self, output_names, inputs):
        batch = len(inputs["input_ids"])
        hidden = np.ones((batch, 3, 2), dtype=np.float32)
        return [hidden]


def test_batches_documents_and_returns_normalized_vectors():
    embedder = OnnxBgeM3Embedder(session=FakeSession(), tokenizer=FakeTokenizer(), batch_size=2)
    vectors = embedder.embed_documents(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(np.isclose(np.linalg.norm(v), 1.0) for v in vectors)


class RecordingTokenizer:
    def __init__(self, encodings):
        self._pending = list(encodings)
        self.truncation_kwargs = None
        self.padding_kwargs = None
        self.batch_sizes = []

    def enable_truncation(self, **kwargs):
        self.truncation_kwargs = kwargs

    def enable_padding(self, **kwargs):
        self.padding_kwargs = kwargs

    def encode_batch(self, texts):
        self.batch_sizes.append(len(texts))
        return [self._pending.pop(0) for _ in texts]


class RecordingSession:
    def __init__(self, dimension=2):
        self.dimension = dimension
        self.batch_sizes = []
        self.inputs = None

    def run(self, output_names, inputs):
        self.batch_sizes.append(len(inputs["input_ids"]))
        self.inputs = inputs
        hidden = np.ones((len(inputs["input_ids"]), 3, self.dimension), dtype=np.float32)
        return [hidden]


class DeclaredDimensionSession(RecordingSession):
    def __init__(self, declared, actual):
        super().__init__(dimension=actual)
        self._declared = declared

    def get_outputs(self):
        return [SimpleNamespace(shape=["batch", "sequence", self._declared])]


class MaskedEncoding:
    def __init__(self, ids, attention_mask):
        self.ids = ids
        self.attention_mask = attention_mask


def test_encodes_in_batches_and_sends_ids_and_mask():
    encodings = [
        MaskedEncoding([1, 5, 6, 2], [1, 1, 1, 1]),
        MaskedEncoding([1, 7, 1, 1], [1, 1, 0, 0]),
        MaskedEncoding([1, 8, 2], [1, 1, 1]),
    ]
    tokenizer = RecordingTokenizer(encodings)
    session = RecordingSession(dimension=4)
    embedder = OnnxBgeM3Embedder(session=session, tokenizer=tokenizer, batch_size=2)

    vectors = embedder.embed_documents(["uno", "dos", "tres"])

    assert tokenizer.batch_sizes == [2, 1]
    assert session.batch_sizes == [2, 1]
    assert len(vectors) == 3
    # El último lote es el que queda registrado en `inputs`.
    assert session.inputs["input_ids"].dtype == np.int64
    assert session.inputs["attention_mask"].dtype == np.int64
    assert session.inputs["input_ids"].tolist() == [[1, 8, 2]]
    assert session.inputs["attention_mask"].tolist() == [[1, 1, 1]]


class InputCapturingSession(RecordingSession):
    def __init__(self, dimension):
        super().__init__(dimension=dimension)
        self.captured = []

    def run(self, output_names, inputs):
        self.captured.append((inputs["input_ids"].copy(), inputs["attention_mask"].copy()))
        return super().run(output_names, inputs)


def test_attention_mask_excludes_padding_from_the_batch():
    encodings = [
        MaskedEncoding([1, 5, 6, 2], [1, 1, 1, 1]),
        MaskedEncoding([1, 7, 1, 1], [1, 1, 0, 0]),
    ]
    session = InputCapturingSession(4)
    embedder = OnnxBgeM3Embedder(
        session=session, tokenizer=RecordingTokenizer(encodings), batch_size=8
    )

    embedder.embed_documents(["uno", "dos"])

    ids, mask = session.captured[0]
    assert ids.tolist() == [[1, 5, 6, 2], [1, 7, 1, 1]]
    assert mask.tolist() == [[1, 1, 1, 1], [1, 1, 0, 0]]


def test_configures_truncation_and_padding():
    tokenizer = RecordingTokenizer([MaskedEncoding([1, 2], [1, 1])])
    OnnxBgeM3Embedder(session=RecordingSession(), tokenizer=tokenizer, max_length=256, batch_size=4)

    assert tokenizer.truncation_kwargs == {"max_length": 256}
    assert tokenizer.padding_kwargs is not None
    assert "pad_id" in tokenizer.padding_kwargs


class VocabTokenizer(RecordingTokenizer):
    def token_to_id(self, token):
        assert token == "<pad>"
        return 1


def test_uses_the_vocabulary_pad_id():
    tokenizer = VocabTokenizer([MaskedEncoding([1, 2], [1, 1])])

    OnnxBgeM3Embedder(session=RecordingSession(), tokenizer=tokenizer)

    assert tokenizer.padding_kwargs["pad_id"] == 1


def test_embed_query_reuses_document_embedding():
    embedder = OnnxBgeM3Embedder(session=FakeSession(), tokenizer=FakeTokenizer())

    assert embedder.embed_query("hola") == embedder.embed_documents(["hola"])[0]


def test_empty_document_list_does_not_invoke_the_model():
    session = RecordingSession()
    embedder = OnnxBgeM3Embedder(session=session, tokenizer=FakeTokenizer())

    assert embedder.embed_documents([]) == []
    assert session.batch_sizes == []


def test_declared_model_dimension_mismatch_raises():
    session = DeclaredDimensionSession(declared=768, actual=768)

    with pytest.raises(EmbeddingDimensionError, match="1024"):
        OnnxBgeM3Embedder(session=session, tokenizer=FakeTokenizer())


def test_runtime_vector_dimension_mismatch_raises_when_declared():
    session = DeclaredDimensionSession(declared=1024, actual=2)
    embedder = OnnxBgeM3Embedder(session=session, tokenizer=FakeTokenizer())

    with pytest.raises(EmbeddingDimensionError, match="1024"):
        embedder.embed_documents(["a"])


def test_requires_model_dir_when_components_are_not_injected():
    with pytest.raises(ValueError, match="model_dir"):
        OnnxBgeM3Embedder()


def test_zero_vector_is_kept_at_zero():
    hidden = np.zeros((1, 2, 3), dtype=np.float32)

    vector = pool_cls_normalize(hidden, np.ones((1, 2), dtype=np.int64))

    assert vector.dtype == np.float32
    assert np.all(vector == 0.0)


def test_pooling_rejects_mismatched_batches():
    hidden = np.ones((2, 3, 4), dtype=np.float32)

    with pytest.raises(ValueError):
        pool_cls_normalize(hidden, np.ones((1, 3), dtype=np.int64))


def test_runtime_dimension_mismatch_raises_for_disk_model(tmp_path, monkeypatch):
    import geoassist.rag.embeddings as embeddings

    (tmp_path / "model.onnx").write_bytes(b"model")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        embeddings.Tokenizer,
        "from_file",
        staticmethod(lambda path: FakeTokenizer()),
    )
    monkeypatch.setattr(
        embeddings.ort,
        "InferenceSession",
        lambda *args, **kwargs: FakeSession(),
    )

    embedder = OnnxBgeM3Embedder(tmp_path)

    with pytest.raises(EmbeddingDimensionError, match="1024"):
        embedder.embed_documents(["a"])


def test_loads_artifacts_and_configures_threads(tmp_path, monkeypatch):
    import geoassist.rag.embeddings as embeddings

    model_path = tmp_path / "model.onnx"
    tokenizer_path = tmp_path / "tokenizer.json"
    model_path.write_bytes(b"model")
    tokenizer_path.write_text("{}", encoding="utf-8")

    seen = {}

    def fake_from_file(path):
        seen["tokenizer_path"] = path
        return FakeTokenizer()

    def fake_inference_session(path, *, sess_options, providers):
        seen["model_path"] = path
        seen["threads"] = sess_options.intra_op_num_threads
        seen["providers"] = providers
        return FakeSession()

    monkeypatch.setattr(embeddings.Tokenizer, "from_file", staticmethod(fake_from_file))
    monkeypatch.setattr(embeddings.ort, "InferenceSession", fake_inference_session)

    embedder = OnnxBgeM3Embedder(tmp_path, threads=7)

    assert embedder.dimension == 1024
    assert seen["model_path"] == str(model_path)
    assert seen["tokenizer_path"] == str(tokenizer_path)
    assert seen["threads"] == 7
    assert seen["providers"] == ["CPUExecutionProvider"]
