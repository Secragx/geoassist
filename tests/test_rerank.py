from types import SimpleNamespace

import numpy as np
import pytest

from geoassist.rag.rerank import OnnxCrossEncoderReranker, sigmoid


def test_sigmoid_is_stable_for_large_values():
    values = sigmoid(np.array([-1000.0, 0.0, 1000.0]))
    assert values.tolist() == [0.0, 0.5, 1.0]


class FakeInput:
    def __init__(self, name):
        self.name = name


class FakeEncoding:
    def __init__(self, ids, attention_mask=None, type_ids=None):
        self.ids = ids
        self.attention_mask = attention_mask or [1] * len(ids)
        self.type_ids = type_ids


class FakeTokenizer:
    def __init__(self):
        self.calls = []

    def enable_truncation(self, **kwargs):
        self.truncation_kwargs = kwargs

    def enable_padding(self, **kwargs):
        self.padding_kwargs = kwargs

    def encode_batch(self, pairs):
        self.calls.append(list(pairs))
        return [FakeEncoding([1, 2, 3]) for _ in pairs]

    def token_to_id(self, token):
        return 1


class FakeSession:
    def __init__(self, logits, input_names=("input_ids", "attention_mask")):
        self.logits = np.array(logits, dtype=np.float32)
        self.inputs = None
        self._input_names = input_names

    def get_inputs(self):
        return [FakeInput(name) for name in self._input_names]

    def run(self, output_names, inputs):
        self.inputs = inputs
        batch = len(inputs["input_ids"])
        return [self.logits[:batch]]


def test_reranker_returns_sigmoid_of_logits_for_passages():
    tokenizer = FakeTokenizer()
    session = FakeSession([2.0, -2.0])
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=tokenizer)

    scores = reranker.score("la consulta", ["pasaje uno", "pasaje dos"])

    assert np.allclose(scores, sigmoid(np.array([2.0, -2.0])))
    assert tokenizer.calls[0] == [
        ("la consulta", "pasaje uno"),
        ("la consulta", "pasaje dos"),
    ]


def test_reranker_sends_input_ids_and_attention_mask():
    session = FakeSession([0.0])
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=FakeTokenizer())

    reranker.score("q", ["p"])

    assert session.inputs is not None
    assert session.inputs["input_ids"].dtype == np.int64
    assert session.inputs["attention_mask"].dtype == np.int64
    assert session.inputs["input_ids"].tolist() == [[1, 2, 3]]
    assert session.inputs["attention_mask"].tolist() == [[1, 1, 1]]


def test_reranker_sends_token_type_ids_when_model_declares_them():
    class TypeIdTokenizer(FakeTokenizer):
        def encode_batch(self, pairs):
            self.calls.append(list(pairs))
            return [FakeEncoding([1, 2, 3], type_ids=[0, 0, 1]) for _ in pairs]

    session = FakeSession([0.0], input_names=("input_ids", "attention_mask", "token_type_ids"))
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=TypeIdTokenizer())

    reranker.score("q", ["p"])

    assert "token_type_ids" in session.inputs
    assert session.inputs["token_type_ids"].dtype == np.int64
    assert session.inputs["token_type_ids"].tolist() == [[0, 0, 1]]


def test_reranker_sends_zero_token_type_ids_when_tokenizer_omits_them():
    session = FakeSession([0.0], input_names=("input_ids", "attention_mask", "token_type_ids"))
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=FakeTokenizer())

    reranker.score("q", ["p"])

    assert session.inputs["token_type_ids"].tolist() == [[0, 0, 0]]


def test_reranker_omits_token_type_ids_when_model_does_not_declare_them():
    session = FakeSession([0.0], input_names=("input_ids", "attention_mask"))
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=FakeTokenizer())

    reranker.score("q", ["p"])

    assert "token_type_ids" not in session.inputs


def test_reranker_handles_column_vector_logits():
    session = SimpleNamespace(
        inputs=None,
        _input_names=("input_ids", "attention_mask"),
        get_inputs=lambda: [FakeInput(name) for name in session._input_names],
        run=lambda _names, inputs: (
            setattr(session, "inputs", inputs) or [np.array([[1.0], [-1.0]], dtype=np.float32)]
        ),
    )
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=FakeTokenizer())

    scores = reranker.score("q", ["a", "b"])

    assert np.allclose(scores, sigmoid(np.array([1.0, -1.0])))


def test_reranker_processes_passages_in_batches():
    tokenizer = FakeTokenizer()
    session = FakeSession([0.0] * 5)
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=tokenizer)
    reranker._batch_size = 2

    scores = reranker.score("q", ["p1", "p2", "p3", "p4", "p5"])

    assert len(scores) == 5
    assert len(tokenizer.calls) == 3
    assert [len(call) for call in tokenizer.calls] == [2, 2, 1]


def test_reranker_empty_passages_returns_empty():
    session = FakeSession([0.0])
    reranker = OnnxCrossEncoderReranker(session=session, tokenizer=FakeTokenizer())

    assert reranker.score("q", []) == []
    assert session.inputs is None


def test_reranker_requires_model_dir_when_components_are_not_injected():
    with pytest.raises(ValueError, match="model_dir"):
        OnnxCrossEncoderReranker()


def test_reranker_prefers_int8_model(tmp_path, monkeypatch):
    import geoassist.rag.rerank as rerank

    (tmp_path / "model_int8.onnx").write_bytes(b"int8")
    (tmp_path / "model.onnx").write_bytes(b"model")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")

    seen = {}

    def fake_from_file(path):
        seen["tokenizer_path"] = path
        return FakeTokenizer()

    def fake_inference_session(path, *, sess_options, providers):
        seen["model_path"] = path
        seen["threads"] = sess_options.intra_op_num_threads
        seen["providers"] = providers
        return FakeSession([0.0])

    monkeypatch.setattr(rerank.Tokenizer, "from_file", staticmethod(fake_from_file))
    monkeypatch.setattr(rerank.ort, "InferenceSession", fake_inference_session)

    OnnxCrossEncoderReranker(tmp_path, threads=7)

    assert seen["model_path"] == str(tmp_path / "model_int8.onnx")
    assert seen["tokenizer_path"] == str(tmp_path / "tokenizer.json")
    assert seen["threads"] == 7
    assert seen["providers"] == ["CPUExecutionProvider"]


def test_reranker_falls_back_to_model_onnx_when_int8_missing(tmp_path, monkeypatch):
    import geoassist.rag.rerank as rerank

    (tmp_path / "model.onnx").write_bytes(b"model")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")

    seen = {}

    def fake_from_file(path):
        seen["tokenizer_path"] = path
        return FakeTokenizer()

    def fake_inference_session(path, *, sess_options, providers):
        seen["model_path"] = path
        return FakeSession([0.0])

    monkeypatch.setattr(rerank.Tokenizer, "from_file", staticmethod(fake_from_file))
    monkeypatch.setattr(rerank.ort, "InferenceSession", fake_inference_session)

    OnnxCrossEncoderReranker(tmp_path)

    assert seen["model_path"] == str(tmp_path / "model.onnx")


def test_reranker_raises_when_tokenizer_cannot_resolve_pad_token():
    class PadlessTokenizer(FakeTokenizer):
        def token_to_id(self, token):
            return None

    with pytest.raises(ValueError, match="pad token"):
        OnnxCrossEncoderReranker(session=FakeSession([0.0]), tokenizer=PadlessTokenizer())
