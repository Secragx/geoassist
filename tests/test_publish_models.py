import hashlib
import tarfile
from pathlib import Path

import zstandard

from scripts.publish_models import artifact_filename, build_bundle, sha256_file


def test_sha256_file(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"geoassist")
    assert sha256_file(path) == hashlib.sha256(b"geoassist").hexdigest()


def test_build_bundle_contains_models_and_checksum(tmp_path):
    bge = tmp_path / "bge-m3-int8"
    rerank = tmp_path / "reranker-int8"
    bge.mkdir(); rerank.mkdir()
    (bge / "model.onnx").write_bytes(b"bge")
    (rerank / "model.onnx").write_bytes(b"rerank")
    out = tmp_path / "dist"
    bundle = build_bundle({"bge-m3-int8": bge, "reranker-int8": rerank}, "1.0.0", out)
    assert bundle.name == artifact_filename("1.0.0")
    checksum = bundle.with_suffix(bundle.suffix + ".sha256").read_text().split()[0]
    assert checksum == sha256_file(bundle)
    with bundle.open("rb") as raw:
        with zstandard.ZstdDecompressor().stream_reader(raw) as decompressed:
            with tarfile.open(fileobj=decompressed, mode="r:") as tar:
                names = tar.getnames()
    assert any(name.endswith("bge-m3-int8/model.onnx") for name in names)
    assert any(name.endswith("reranker-int8/model.onnx") for name in names)
