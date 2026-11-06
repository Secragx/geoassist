import hashlib
import json
import sys
import tarfile
import types
from pathlib import Path

import pytest
import zstandard

from scripts import publish_models
from scripts.publish_models import artifact_filename, build_bundle, sha256_file


def _tar_names(bundle):
    with (
        bundle.open("rb") as raw,
        zstandard.ZstdDecompressor().stream_reader(raw) as decompressed,
        tarfile.open(fileobj=decompressed, mode="r:") as tar,
    ):
        return tar.getnames()


def _write_embedding_source(root):
    onnx_dir = root / "onnx"
    onnx_dir.mkdir(parents=True)
    (onnx_dir / "model.onnx").write_bytes(b"fp32")
    (onnx_dir / "model.onnx_data").write_bytes(b"weights")
    for name in publish_models.EMBEDDING_FILES:
        (root / name).write_bytes(name.encode())


def _install_fake_export_modules(monkeypatch, source_dir, *, fail_quantization=False):
    calls = {}

    class FakeGraph:
        input = [types.SimpleNamespace(name="input_ids")]
        output = [types.SimpleNamespace(name="logits")]

    class FakeModel:
        graph = FakeGraph()

    huggingface_hub = types.ModuleType("huggingface_hub")

    def snapshot_download(model, *, revision, allow_patterns):
        calls["model"] = model
        calls["revision"] = revision
        calls["allow_patterns"] = allow_patterns
        return str(source_dir)

    huggingface_hub.snapshot_download = snapshot_download

    onnx = types.ModuleType("onnx")

    def load(path):
        calls["probed"] = Path(path)
        return FakeModel()

    onnx.load = load

    onnxruntime = types.ModuleType("onnxruntime")
    quantization = types.ModuleType("onnxruntime.quantization")

    class QuantType:
        QInt8 = "qint8"

    def quantize_dynamic(*, model_input, model_output, weight_type, per_channel, reduce_range):
        calls["quantized_input"] = Path(model_input)
        calls["quantized_output"] = Path(model_output)
        Path(model_output).write_bytes(b"partial")
        if fail_quantization:
            raise RuntimeError("quantization failed")
        Path(model_output).write_bytes(b"new-int8")

    quantization.QuantType = QuantType
    quantization.quantize_dynamic = quantize_dynamic
    onnxruntime.quantization = quantization

    monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)
    monkeypatch.setitem(sys.modules, "onnx", onnx)
    monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime)
    monkeypatch.setitem(sys.modules, "onnxruntime.quantization", quantization)
    return calls


def test_sha256_file(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"geoassist")
    assert sha256_file(path) == hashlib.sha256(b"geoassist").hexdigest()


def test_build_bundle_contains_models_and_checksum(tmp_path):
    bge = tmp_path / "bge-m3-int8"
    rerank = tmp_path / "reranker-int8"
    bge.mkdir()
    rerank.mkdir()
    (bge / "model.onnx").write_bytes(b"bge")
    (rerank / "model.onnx").write_bytes(b"rerank")
    out = tmp_path / "dist"
    bundle = build_bundle({"bge-m3-int8": bge, "reranker-int8": rerank}, "1.0.0", out)
    assert bundle.name == artifact_filename("1.0.0")
    checksum = bundle.with_suffix(bundle.suffix + ".sha256").read_text().split()[0]
    assert checksum == sha256_file(bundle)
    names = _tar_names(bundle)
    assert any(name.endswith("bge-m3-int8/model.onnx") for name in names)
    assert any(name.endswith("reranker-int8/model.onnx") for name in names)


def test_export_bge_m3_replaces_stale_model_and_excludes_intermediates(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _write_embedding_source(source)

    models_dir = tmp_path / "models"
    stale = models_dir / publish_models.EMBEDDING_DIR
    stale.mkdir(parents=True)
    (stale / "model.onnx").write_bytes(b"stale")
    (stale / "model_fp32.onnx").write_bytes(b"stale-fp32")
    (stale / "model.onnx_data").write_bytes(b"stale-data")
    (stale / publish_models.MARKER_NAME).write_text(
        json.dumps(
            {
                "format": 1,
                "model": publish_models.EMBEDDING_MODEL,
                "revision": "old-revision",
            }
        )
    )

    calls = _install_fake_export_modules(monkeypatch, source)
    out = publish_models.export_bge_m3(models_dir)

    assert out == stale
    assert (out / "model.onnx").read_bytes() == b"new-int8"
    assert calls["model"] == publish_models.EMBEDDING_MODEL
    assert calls["revision"] == publish_models.EMBEDDING_REVISION
    assert not (out / "model_fp32.onnx").exists()
    assert not (out / "model.onnx_data").exists()
    marker = json.loads((out / publish_models.MARKER_NAME).read_text())
    assert marker["model"] == publish_models.EMBEDDING_MODEL
    assert marker["revision"] == publish_models.EMBEDDING_REVISION

    bundle = build_bundle({publish_models.EMBEDDING_DIR: out}, "1.0.0", tmp_path / "dist")
    names = _tar_names(bundle)
    assert any(name.endswith("bge-m3-int8/model.onnx") for name in names)
    assert not any(name.endswith(("model_fp32.onnx", "model.onnx_data")) for name in names)


def test_export_bge_m3_does_not_publish_partial_output(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _write_embedding_source(source)

    models_dir = tmp_path / "models"
    previous = models_dir / publish_models.EMBEDDING_DIR
    previous.mkdir(parents=True)
    (previous / "model.onnx").write_bytes(b"previous")

    _install_fake_export_modules(monkeypatch, source, fail_quantization=True)
    with pytest.raises(RuntimeError, match="quantization failed"):
        publish_models.export_bge_m3(models_dir)

    assert (previous / "model.onnx").read_bytes() == b"previous"
    assert not list(models_dir.glob(f".{publish_models.EMBEDDING_DIR}.tmp-*"))
