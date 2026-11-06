"""Renderizado de manuales Markdown a PDF."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from geoassist.render.manifest import PdfManifest, PdfManifestEntry, _sha256_file, _utc_now

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(?P<src>(?:[^()\\]|\\.|\([^()]*\))+?)\s*\)")
_IMAGE_TITLE_SUFFIX_RE = re.compile(r"""\s+(?:"[^"]*"|'[^']*')\s*$""")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def output_path(output_dir: Path | str, rel_path: str) -> Path:
    """Calcula la ruta de salida del PDF a partir de la ruta relativa del manual."""

    return Path(output_dir) / Path(rel_path).with_suffix(".pdf")


def render_key(
    rel_path: str, md_bytes: bytes, template_bytes: bytes, images: Mapping[str, bytes]
) -> str:
    """Clave de renderizado que cambia con contenido, plantilla e imágenes."""

    hasher = hashlib.sha256()
    hasher.update(rel_path.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(md_bytes)
    hasher.update(b"\x00")
    hasher.update(template_bytes)
    hasher.update(b"\x00")
    for name in sorted(images):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(images[name])
        hasher.update(b"\x00")
    return hasher.hexdigest()


class PdfResult:
    """Resultado de renderizar un manual a PDF."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Soporta ambos estilos de construcción:
        # - PdfResult(rel_path, pdf_path, status, error)  (brief)
        # - PdfResult(path, status, reason, source_sha256) (resolución)
        rel_path = kwargs.get("rel_path", kwargs.get("path"))
        pdf_path = kwargs.get("pdf_path")
        status = kwargs.get("status")
        error = kwargs.get("error", kwargs.get("reason"))
        source_sha256 = kwargs.get("source_sha256")
        path = kwargs.get("path", rel_path)

        if len(args) == 1:
            # PdfResult(path) o similar
            path = args[0]
            rel_path = rel_path if rel_path is not None else path
        elif len(args) == 2:
            # (path, status) o (rel_path, pdf_path)
            if args[1] in {"generated", "skipped", "failed"}:
                path = args[0]
                rel_path = rel_path if rel_path is not None else path
                status = args[1]
            else:
                rel_path = args[0]
                path = path if path is not None else args[0]
                pdf_path = args[1]
        elif len(args) == 3:
            # (path, status, reason) o (rel_path, pdf_path, status)
            if args[1] in {"generated", "skipped", "failed"}:
                path = args[0]
                rel_path = rel_path if rel_path is not None else path
                status = args[1]
                error = args[2]
            else:
                rel_path = args[0]
                path = path if path is not None else args[0]
                pdf_path = args[1]
                status = args[2]
        elif len(args) == 4:
            if args[1] in {"generated", "skipped", "failed"}:
                # (path, status, reason, source_sha256)
                path = args[0]
                rel_path = rel_path if rel_path is not None else path
                status = args[1]
                error = args[2]
                source_sha256 = args[3]
            else:
                # (rel_path, pdf_path, status, error)
                rel_path = args[0]
                path = path if path is not None else args[0]
                pdf_path = args[1]
                status = args[2]
                error = args[3]

        if rel_path is None:
            rel_path = path
        if path is None:
            path = rel_path

        self.rel_path = rel_path
        self.path = path
        self.pdf_path = pdf_path
        self.status = status
        self.reason = error
        self.error = error
        self.source_sha256 = source_sha256
        # conveniencia
        self.pdf = pdf_path

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PdfResult):
            return False
        return (
            self.rel_path == other.rel_path
            and self.path == other.path
            and self.pdf_path == other.pdf_path
            and self.status == other.status
            and self.reason == other.reason
            and self.source_sha256 == other.source_sha256
        )

    def __repr__(self) -> str:
        return (
            f"PdfResult(rel_path={self.rel_path!r}, pdf_path={self.pdf_path!r}, "
            f"status={self.status!r}, reason={self.reason!r}, source_sha256={self.source_sha256!r})"
        )


def _find_asset(assets_dir: Path, name: str) -> Path | None:
    if not assets_dir.is_dir():
        return None
    direct = assets_dir / name
    if direct.is_file():
        return direct
    for candidate in assets_dir.rglob(name):
        if candidate.is_file():
            return candidate
    # búsqueda por nombre base
    for candidate in assets_dir.rglob("*"):
        if candidate.is_file() and candidate.name == name:
            return candidate
    return None


def prepare_markdown(markdown: str, assets_dir: Path | str) -> str:
    """Resuelve imágenes existentes a rutas absolutas y reemplaza las ausentes por alt text."""

    assets = Path(assets_dir)

    def replacer(match: re.Match[str]) -> str:
        # extraer alt
        full = match.group(0)
        # parse alt
        alt_match = re.match(r"!\[(?P<alt>[^\]]*)\]", full)
        alt_text = alt_match.group("alt") if alt_match else ""
        raw_src = match.group("src")
        src = _IMAGE_TITLE_SUFFIX_RE.sub("", raw_src.strip()).strip()
        if src.startswith("<") and src.endswith(">"):
            src = src[1:-1].strip()
        if not src:
            return alt_text
        from urllib.parse import urlparse

        if urlparse(src).scheme.lower() in {"data", "http", "https"}:
            return full
        clean = src.split("#", 1)[0].split("?", 1)[0]
        name = Path(clean.replace("\\", "/")).name
        if not name:
            return alt_text
        found = _find_asset(assets, name)
        if found is not None:
            return f"![{alt_text}]({found})"
        return alt_text

    return _IMAGE_RE.sub(replacer, markdown)


class PdfRenderer:
    """Renderiza todos los manuales a PDF y mantiene el manifiesto."""

    def __init__(
        self,
        *args: Any,
        repo_root: Path | str | None = None,
        manuals_root_rel: str = "data_source/manuales",
        assets_root_rel: str = "data_source/assets",
        template_path: Path | str | None = None,
        output_dir: Path | str | None = None,
        manifest_path: Path | str | None = None,
        manuals_dir: Path | str | None = None,
        **kwargs: Any,
    ) -> None:
        # Soporta estilo brief:
        # PdfRenderer(repo_root, manuals_root_rel, assets_root_rel,
        #             template_path, output_dir, manifest_path)
        if len(args) >= 1 and repo_root is None and manuals_dir is None:
            repo_root = args[0]
        if len(args) >= 2 and "manuals_root_rel" not in kwargs:
            manuals_root_rel = str(args[1])
        if len(args) >= 3:
            assets_root_rel = str(args[2])
        if len(args) >= 4:
            template_path = args[3]
        if len(args) >= 5 and output_dir is None:
            output_dir = args[4]
        if len(args) >= 6 and manifest_path is None:
            manifest_path = args[5]

        # Soporta estilo resolución: PdfRenderer(manuals_dir, output_dir, ...)
        if manuals_dir is not None and repo_root is None and len(args) <= 2:
            repo_root = None
        if len(args) == 2 and repo_root is None and manuals_dir is None and output_dir is None:
            # PdfRenderer(manuals_dir, output_dir)
            manuals_dir = args[0]
            output_dir = args[1]

        self._repo_root = Path(repo_root).resolve() if repo_root is not None else None
        self._manuals_root_rel = manuals_root_rel
        self._assets_root_rel = assets_root_rel
        self._template_path = Path(template_path) if template_path is not None else None
        self._output_dir = Path(output_dir) if output_dir is not None else None
        self._manifest_path = Path(manifest_path) if manifest_path is not None else None
        self._manuals_dir_override = Path(manuals_dir) if manuals_dir is not None else None

        if self._repo_root is not None:
            self._manuals_root: Path | None = self._repo_root / manuals_root_rel
            self._assets_root: Path | None = self._repo_root / assets_root_rel
        elif self._manuals_dir_override is not None:
            self._manuals_root = self._manuals_dir_override
            self._assets_root = self._manuals_dir_override.parent / "assets"
        else:
            self._manuals_root = None
            self._assets_root = None

        # template bytes cache
        self._template_bytes = b""
        if self._template_path is not None and self._template_path.is_file():
            self._template_bytes = self._template_path.read_bytes()

    @property
    def manuals_root(self) -> Path | None:
        return self._manuals_root

    def _load_manifest(self, manifest_path: Path) -> dict[str, Any]:
        if not manifest_path.is_file():
            return {"entries": [], "generated_at": ""}
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"entries": [], "generated_at": ""}

    def _save_manifest(self, manifest_path: Path, manifest: dict[str, Any]) -> None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, manifest_path)

    def _entry_for(self, manifest: dict[str, Any], manual_path: str) -> dict[str, Any] | None:
        for entry in manifest.get("entries", []):
            if entry.get("manual_path") == manual_path:
                return entry
        return None

    def _convert(self, rel_path: str, markdown: str, output: Path, meta: dict[str, Any]) -> None:
        """Convierte Markdown a PDF usando pandoc/xelatex."""
        output.parent.mkdir(parents=True, exist_ok=True)
        # preparar markdown
        assets_root = meta.get("assets_root")
        if assets_root is not None:
            prepared = prepare_markdown(markdown, Path(assets_root))
        else:
            prepared = markdown

        # escribir markdown temporal
        fd, tmp_md = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        Path(tmp_md).write_text(prepared, encoding="utf-8")

        engine = "xelatex" if shutil.which("xelatex") else "pdflatex"

        cmd = [
            "pandoc",
            tmp_md,
            "-o",
            str(output),
            f"--pdf-engine={engine}",
            "-V",
            "geometry:margin=1in",
        ]
        template_path = meta.get("template_path")
        if template_path is not None and Path(template_path).is_file():
            cmd.extend(["--template", str(template_path)])
        resource_path = meta.get("resource_path")
        if resource_path is not None:
            cmd.extend(["--resource-path", str(resource_path)])
        # frontmatter metadata
        if meta.get("title"):
            cmd.extend(["-V", f"title={meta['title']}"])
        # timeout 120s
        try:
            subprocess.run(cmd, check=True, timeout=120, capture_output=True, text=True)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_md)

    def render(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Compatibilidad con el orquestador (devuelve lista de dicts)."""
        results = self.render_all(*args, **kwargs)
        out: list[dict[str, Any]] = []
        for r in results:
            ok = r.status in {"generated", "skipped"}
            out.append(
                {
                    "ok": ok,
                    "rel_path": r.rel_path,
                    "path": r.path,
                    "status": r.status,
                    "reason": r.reason,
                    "error": r.reason,
                    "source_sha256": r.source_sha256,
                }
            )
        return out

    def render_all(self, *args: Any, **kwargs: Any) -> list[PdfResult]:
        # Parse arguments
        skip_existing = kwargs.get("skip_existing", True)
        only_changed = kwargs.get("only_changed")
        if only_changed is not None:
            skip_existing = bool(only_changed)

        manuals_dir = kwargs.get("manuals_dir", kwargs.get("manuals_root"))
        output_dir = kwargs.get("output_dir")
        if len(args) >= 1 and manuals_dir is None:
            manuals_dir = args[0]
        if len(args) >= 2 and output_dir is None:
            output_dir = args[1]

        # brief style: no dirs provided
        brief_mode = manuals_dir is None
        if brief_mode:
            manuals_root = self._manuals_root
            out_dir = self._output_dir
            assets_root = self._assets_root
            template_path = self._template_path
            manifest_path = self._manifest_path
        else:
            manuals_root = Path(str(manuals_dir))
            out_dir = Path(output_dir) if output_dir is not None else self._output_dir
            # assets: sibling assets or same override
            assets_root = (
                self._assets_root
                if self._assets_root is not None
                else manuals_root.parent / "assets"
            )
            template_path = self._template_path
            manifest_path = (
                self._manifest_path
                if self._manifest_path is not None
                else ((out_dir / "manifest.json") if out_dir is not None else Path("manifest.json"))
            )

        if manuals_root is None or out_dir is None:
            raise ValueError("manuals_dir y output_dir son obligatorios")

        manuals_root = Path(manuals_root)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if manifest_path is None:
            manifest_path = out_dir / "manifest.json"
        manifest_path = Path(manifest_path)

        manifest = self._load_manifest(manifest_path)
        # normalize entries list
        if "entries" not in manifest:
            manifest["entries"] = []

        results: list[PdfResult] = []

        md_files: list[Path] = []
        if manuals_root.is_dir():
            md_files = sorted(manuals_root.rglob("*.md"))

        for md_path in md_files:
            if brief_mode:
                rel_path = (
                    md_path.relative_to(self._repo_root).as_posix()
                    if self._repo_root is not None
                    else md_path.relative_to(manuals_root).as_posix()
                )
                manual_rel_for_manifest = md_path.relative_to(manuals_root).as_posix()
            else:
                rel_path = md_path.relative_to(manuals_root).as_posix()
                manual_rel_for_manifest = rel_path

            pdf_path = output_path(
                out_dir,
                manual_rel_for_manifest
                if not brief_mode
                else md_path.relative_to(manuals_root).as_posix(),
            )
            # Actually output_path uses rel; use manual_rel for output structure
            pdf_path = out_dir / Path(manual_rel_for_manifest).with_suffix(".pdf")

            source_bytes = md_path.read_bytes()
            source_sha = _sha256_bytes(source_bytes)

            # skip check
            existing_entry = self._entry_for(manifest, manual_rel_for_manifest)
            skip = False
            if (
                skip_existing
                and existing_entry is not None
                and existing_entry.get("source_sha256") == source_sha
                and Path(pdf_path).is_file()
            ):
                skip = True

            if skip:
                results.append(
                    PdfResult(
                        rel_path=rel_path,
                        pdf_path=pdf_path,
                        status="skipped",
                        error=None,
                        source_sha256=source_sha,
                    )
                )
                continue

            # convert
            try:
                markdown_text = source_bytes.decode("utf-8")
                # parse frontmatter? pass full text; _convert handles prepare
                frontmatter_meta: dict[str, Any] = {}
                try:
                    import frontmatter

                    post = frontmatter.loads(markdown_text)
                    if isinstance(post.metadata, Mapping):
                        frontmatter_meta = dict(post.metadata)
                    markdown_text = post.content
                except Exception:
                    pass

                meta = {
                    "assets_root": assets_root,
                    "template_path": template_path,
                    "resource_path": assets_root,
                    "title": frontmatter_meta.get("title"),
                }
                self._convert(rel_path, markdown_text, pdf_path, meta)
                pdf_sha = _sha256_file(pdf_path) if Path(pdf_path).is_file() else ""
                # update manifest entry
                new_entry = {
                    "manual_path": manual_rel_for_manifest,
                    "pdf_path": Path(pdf_path).relative_to(out_dir).as_posix(),
                    "source_sha256": source_sha,
                    "pdf_sha256": pdf_sha,
                }
                # replace or append
                entries = manifest["entries"]
                replaced = False
                for i, e in enumerate(entries):
                    if e.get("manual_path") == manual_rel_for_manifest:
                        entries[i] = new_entry
                        replaced = True
                        break
                if not replaced:
                    entries.append(new_entry)

                results.append(
                    PdfResult(
                        rel_path=rel_path,
                        pdf_path=pdf_path,
                        status="generated",
                        error=None,
                        source_sha256=source_sha,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    PdfResult(
                        rel_path=rel_path,
                        pdf_path=pdf_path,
                        status="failed",
                        error=str(exc),
                        source_sha256=source_sha,
                    )
                )
                # register failure in manifest? keep source hash
                entries = manifest["entries"]
                new_entry = {
                    "manual_path": manual_rel_for_manifest,
                    "pdf_path": Path(pdf_path).relative_to(out_dir).as_posix()
                    if Path(pdf_path).is_relative_to(out_dir)
                    else str(pdf_path),
                    "source_sha256": source_sha,
                    "pdf_sha256": "",
                }
                replaced = False
                for i, e in enumerate(entries):
                    if e.get("manual_path") == manual_rel_for_manifest:
                        entries[i] = new_entry
                        replaced = True
                        break
                if not replaced:
                    entries.append(new_entry)

        # write manifest
        manifest["generated_at"] = _utc_now()
        self._save_manifest(manifest_path, manifest)

        # also build PdfManifest? not needed
        return results


# Re-export manifest classes
PdfManifest = PdfManifest
PdfManifestEntry = PdfManifestEntry
