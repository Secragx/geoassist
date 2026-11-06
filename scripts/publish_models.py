"""Exporta, empaqueta y verifica el artefacto versionado de modelos ONNX.

El artefacto contiene los directorios de modelos bajo el prefijo ``models/`` y
se publica como ``geoassist-models-<version>.tar.zst`` junto a su checksum
``.sha256`` en formato ``sha256sum``.

Uso:
    python scripts/publish_models.py --version 1.0.0 --output-dir dist
    python scripts/publish_models.py --version 1.0.0 --output-dir dist --export

Sin ``--export`` solo se empaquetan directorios ya existentes. Con ``--export``
se descargan BGE-M3 y el reranker desde revisiones fijas de Hugging Face y se
cuantizan a int8, reutilizando la lógica del script de referencia.

La publicación del release es responsabilidad del operador (por ejemplo con
``gh release upload``); este script no publica ni inventa credenciales.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import zstandard

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"

BUNDLE_PREFIX = "models"
COMPRESSION_LEVEL = 10
MARKER_NAME = ".geoassist-model.json"
INTERMEDIATE_NAMES = ("model_fp32.onnx", "model.onnx_data")

EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBEDDING_DIR = "bge-m3-int8"
EMBEDDING_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
)

RERANK_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANK_REVISION = "1427fd652930e4ba29e8149678df786c240d8825"
RERANK_DIR = "mmarco-mMiniLMv2-L12-H384-v1-int8"

DEFAULT_RELEASE_REPOSITORY = "Secragx/geoassist"


def sha256_file(path: Path) -> str:
    """Devuelve el SHA-256 hexadecimal de un archivo, leído por bloques."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_filename(version: str) -> str:
    """Nombre canónico del artefacto para una versión dada."""
    return f"geoassist-models-{version}.tar.zst"


def _validate_version(version: str) -> None:
    if not version or version in {".", ".."} or "/" in version or "\\" in version:
        raise ValueError(f"versión inválida: {version!r}")


def _model_prefix(model_name: str) -> str:
    relative = Path(model_name)
    if (
        not model_name
        or relative.is_absolute()
        or not relative.parts
        or any(part in {".", ".."} for part in relative.parts)
        or "\\" in model_name
    ):
        raise ValueError(f"nombre de modelo inválido: {model_name!r}")
    return f"{BUNDLE_PREFIX}/{relative.as_posix()}"


def _collect_entries(model_dirs: Mapping[str, Path]) -> list[tuple[str, Path]]:
    """Lista las entradas del tar ordenadas por nombre de archivo."""
    entries: list[tuple[str, Path]] = []
    for model_name in sorted(model_dirs):
        model_dir = Path(model_dirs[model_name])
        if model_dir.is_symlink():
            raise ValueError(f"no se admiten enlaces simbólicos: {model_dir}")
        if not model_dir.is_dir():
            raise FileNotFoundError(f"no existe el directorio de modelo: {model_dir}")

        prefix = _model_prefix(model_name)
        entries.append((f"{prefix}/", model_dir))
        for current, dirnames, filenames in os.walk(model_dir, followlinks=False):
            dirnames.sort()
            filenames.sort()
            current_path = Path(current)
            for dirname in list(dirnames):
                candidate = current_path / dirname
                if candidate.is_symlink():
                    raise ValueError(f"no se admiten enlaces simbólicos: {candidate}")
            relative_dir = current_path.relative_to(model_dir)
            if relative_dir.parts:
                entries.append((f"{prefix}/{relative_dir.as_posix().rstrip('/')}/", current_path))
            for filename in filenames:
                path = current_path / filename
                if path.is_symlink():
                    raise ValueError(f"no se admiten enlaces simbólicos: {path}")
                relative = path.relative_to(model_dir).as_posix()
                entries.append((f"{prefix}/{relative}", path))

    entries.sort(key=lambda item: item[0])
    return entries


def _tarinfo(name: str, path: Path, *, is_dir: bool) -> tarfile.TarInfo:
    """Construye una cabecera tar reproducible (mtime y propietario fijos)."""
    info = tarfile.TarInfo(name)
    info.mtime = 0
    info.mode = 0o755 if is_dir else 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.size = 0 if is_dir else path.stat().st_size
    if is_dir:
        info.type = tarfile.DIRTYPE
    return info


def build_bundle(model_dirs: Mapping[str, Path], version: str, output_dir: Path) -> Path:
    """Escribe el ``.tar.zst`` reproducible y su checksum ``.sha256``."""
    _validate_version(version)
    if not model_dirs:
        raise ValueError("se requiere al menos un directorio de modelo")

    entries = _collect_entries(model_dirs)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bundle = output_dir / artifact_filename(version)
    checksum_path = bundle.with_suffix(bundle.suffix + ".sha256")
    temporary = bundle.with_name(bundle.name + ".tmp")

    compressor = zstandard.ZstdCompressor(level=COMPRESSION_LEVEL)
    try:
        with (
            temporary.open("wb") as raw,
            compressor.stream_writer(raw, closefd=False) as compressed,
            tarfile.open(fileobj=compressed, mode="w|", format=tarfile.GNU_FORMAT) as tar,
        ):
            for arcname, path in entries:
                is_dir = path.is_dir()
                info = _tarinfo(arcname, path, is_dir=is_dir)
                if is_dir:
                    tar.addfile(info)
                else:
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
        temporary.replace(bundle)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    digest = sha256_file(bundle)
    checksum_path.write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    return bundle


def _probe_onnx(path: Path) -> None:
    import onnx

    model = onnx.load(str(path))
    inputs = [item.name for item in model.graph.input]
    outputs = [item.name for item in model.graph.output]
    print(
        f"ONNX {path.name}: inputs={inputs} outputs={outputs} "
        f"file={path.stat().st_size / 1e6:.1f} MB"
    )


def _quantize_fp32_onnx(src: Path, dst: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    temporary = dst.with_name(dst.name + ".tmp")
    temporary.unlink(missing_ok=True)
    print(f"Cuantizando {src.name} -> {dst.name} (int8 dynamic)...")
    try:
        quantize_dynamic(
            model_input=str(src),
            model_output=str(temporary),
            weight_type=QuantType.QInt8,
            per_channel=True,
            reduce_range=True,
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"la cuantización no produjo {dst.name}")
        temporary.replace(dst)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f"OK: {dst.stat().st_size / 1e6:.1f} MB")


def _read_marker(directory: Path) -> dict[str, object] | None:
    try:
        data = json.loads((directory / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_marker(directory: Path, model: str, revision: str) -> None:
    marker = {"format": 1, "model": model, "revision": revision}
    (directory / MARKER_NAME).write_text(
        json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8"
    )


def _marker_matches(
    directory: Path, model: str, revision: str, required_files: Sequence[str]
) -> bool:
    marker = _read_marker(directory)
    if marker is None:
        return False
    if marker.get("model") != model or marker.get("revision") != revision:
        return False
    return all((directory / name).is_file() for name in required_files)


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _remove_intermediates(directory: Path) -> None:
    for name in INTERMEDIATE_NAMES:
        (directory / name).unlink(missing_ok=True)
    for path in directory.glob("*.tmp"):
        path.unlink(missing_ok=True)


def _new_staging(models_dir: Path, name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f".{name}.tmp-", dir=models_dir))


def _replace_directory(staging: Path, destination: Path) -> None:
    """Sustituye el directorio final solo cuando el staging está completo."""
    backup = destination.with_name(f".{destination.name}.previous")
    _remove_tree(backup)
    if destination.exists():
        destination.replace(backup)
    try:
        staging.replace(destination)
    except BaseException:
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    _remove_tree(backup)


def export_bge_m3(models_dir: Path = MODELS_DIR) -> Path:
    """Exporta y cuantiza BGE-M3 desde la revisión fijada."""
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    out_dir = models_dir / EMBEDDING_DIR

    if _marker_matches(
        out_dir,
        EMBEDDING_MODEL,
        EMBEDDING_REVISION,
        ("model.onnx", *EMBEDDING_FILES),
    ):
        _remove_intermediates(out_dir)
        print(f"bge-m3 ya está en la revisión {EMBEDDING_REVISION}: {out_dir}")
        return out_dir

    import huggingface_hub

    staging = _new_staging(models_dir, EMBEDDING_DIR)
    try:
        source_dir = Path(
            huggingface_hub.snapshot_download(
                EMBEDDING_MODEL,
                revision=EMBEDDING_REVISION,
                allow_patterns=["onnx/*", *EMBEDDING_FILES],
            )
        )
        onnx_source = source_dir / "onnx" / "model.onnx"
        onnx_data_source = source_dir / "onnx" / "model.onnx_data"
        fp32_path = staging / "model_fp32.onnx"

        print("Copiando modelo ONNX fp32 de bge-m3...")
        shutil.copy(onnx_source, fp32_path)
        if onnx_data_source.exists():
            shutil.copy(onnx_data_source, staging / "model.onnx_data")
        _probe_onnx(fp32_path)
        _quantize_fp32_onnx(fp32_path, staging / "model.onnx")

        for name in EMBEDDING_FILES:
            shutil.copy(source_dir / name, staging / name)

        _remove_intermediates(staging)
        _write_marker(staging, EMBEDDING_MODEL, EMBEDDING_REVISION)
        _replace_directory(staging, out_dir)
    finally:
        _remove_tree(staging)

    print(f"bge-m3 listo en {out_dir}")
    return out_dir


def export_reranker(models_dir: Path = MODELS_DIR) -> Path:
    """Exporta y cuantiza el reranker desde la revisión fijada."""
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    out_dir = models_dir / RERANK_DIR

    if _marker_matches(
        out_dir, RERANK_MODEL, RERANK_REVISION, ("model_int8.onnx", "tokenizer.json")
    ):
        _remove_intermediates(out_dir)
        print(f"reranker ya está en la revisión {RERANK_REVISION}: {out_dir}")
        return out_dir

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    staging = _new_staging(models_dir, RERANK_DIR)
    try:
        print(f"Descargando {RERANK_MODEL} (torch)...")
        tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL, revision=RERANK_REVISION)
        model = AutoModelForSequenceClassification.from_pretrained(
            RERANK_MODEL, revision=RERANK_REVISION
        )
        model.eval()
        tokenizer.save_pretrained(staging)

        dummy = tokenizer(
            [("consulta de prueba", "documento de prueba")],
            padding="max_length",
            truncation=True,
            max_length=32,
            return_tensors="pt",
        )

        fp32_path = staging / "model_fp32.onnx"
        fp32_temporary = fp32_path.with_name(fp32_path.name + ".tmp")
        print("Exportando reranker a ONNX fp32...")
        with torch.no_grad():
            torch.onnx.export(
                model,
                (dummy["input_ids"], dummy["attention_mask"]),
                str(fp32_temporary),
                input_names=["input_ids", "attention_mask"],
                output_names=["logits"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "seq"},
                    "attention_mask": {0: "batch", 1: "seq"},
                    "logits": {0: "batch"},
                },
                opset_version=17,
                do_constant_folding=True,
                dynamo=False,
            )
        if not fp32_temporary.is_file() or fp32_temporary.stat().st_size == 0:
            raise RuntimeError("la exportación ONNX no produjo model_fp32.onnx")
        fp32_temporary.replace(fp32_path)

        _probe_onnx(fp32_path)
        _quantize_fp32_onnx(fp32_path, staging / "model_int8.onnx")
        _remove_intermediates(staging)
        _write_marker(staging, RERANK_MODEL, RERANK_REVISION)
        _replace_directory(staging, out_dir)
    finally:
        _remove_tree(staging)

    print(f"reranker listo en {out_dir}")
    return out_dir


def export_models(models_dir: Path = MODELS_DIR) -> dict[str, Path]:
    """Exporta ambos modelos con sus revisiones fijadas."""
    return {
        EMBEDDING_DIR: export_bge_m3(models_dir),
        RERANK_DIR: export_reranker(models_dir),
    }


def default_model_dirs(models_dir: Path = MODELS_DIR) -> dict[str, Path]:
    """Directorios que espera el runtime dentro del bundle."""
    base = Path(models_dir)
    return {
        EMBEDDING_DIR: base / EMBEDDING_DIR,
        RERANK_DIR: base / RERANK_DIR,
    }


def _release_url(bundle: Path, version: str) -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_RELEASE_REPOSITORY).strip("/")
    tag = os.environ.get("MODELS_RELEASE_TAG", f"models-v{version}")
    return f"https://github.com/{repository}/releases/download/{tag}/{bundle.name}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Empaqueta los modelos ONNX en un artefacto versionado."
    )
    parser.add_argument("--version", required=True, help="Versión del artefacto.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directorio donde escribir el .tar.zst y su .sha256.",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=MODELS_DIR,
        help="Directorio base de los modelos (por defecto ./models).",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Exporta y cuantiza los modelos antes de empaquetar.",
    )
    args = parser.parse_args(argv)

    try:
        if args.export:
            model_dirs = export_models(args.models_dir)
        else:
            model_dirs = default_model_dirs(args.models_dir)
        bundle = build_bundle(model_dirs, args.version, args.output_dir)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    except ImportError as exc:
        parser.error(f"--export requiere el extra 'tools': {exc}")

    digest = sha256_file(bundle)
    print(f"Artefacto: {bundle}")
    print(f"MODELS_ARTIFACT_URL={_release_url(bundle, args.version)}")
    print(f"MODELS_ARTIFACT_SHA256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
