# GeoAssist RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir GeoAssist, un asistente RAG de soporte L2 que responde en un canal de Microsoft Teams con fuentes citadas, sincroniza manuales Markdown bajo demanda y genera sus PDF, todo portable con Docker desde un clon limpio.

**Architecture:** Monolito modular Python 3.12 + FastAPI con SQLite/FTS5 y FAISS. La API solo lee versiones inmutables del corpus; un contenedor one-shot `sync` actualiza Git, indexa, publica la versión atómicamente y genera PDF con Pandoc/TeX. Teams entra por webhook saliente detrás de una interfaz de adaptador.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, pydantic-settings, structlog, httpx, SQLite (stdlib `sqlite3`), FTS5, FAISS (`faiss-cpu`), ONNX Runtime, tokenizers, PyYAML, python-frontmatter, pypandoc (solo imagen `sync`), pytest, ruff, mypy, Docker Compose, Caddy.

**Spec:** `docs/superpowers/specs/2026-11-06-geoassist-rag-design.md`

## Global Constraints

- Python 3.12; dependencias con lockfile y hashes; imágenes base fijadas por digest.
- Corpus: solo archivos `.md`; `model_manuales.md` excluido; imágenes solo para PDF.
- Estados: `approved`, `pending`, `archived`, `unknown`. Recuperación por defecto: `approved` + `pending`.
- Chunking: objetivo 450 tokens, máximo 600, mínimo 80, solape 12 %, conteo con tokenizer XLM-R.
- Embeddings: BGE-M3 ONNX int8, 1024 dimensiones, CLS pooling + L2, `max_length=512`.
- Recuperación: denso top-20, léxico top-20, RRF `k=60`, rerank top-8, máximo 3 chunks por documento, 5 fragmentos al prompt.
- Presupuesto: respuesta Teams p95 < 4,5 s; retrieval ≤ 800 ms; rerank ≤ 500 ms; LLM ≤ 2500 ms.
- Límites: cuerpo 64 KB, pregunta 2000 caracteres, 6 preguntas/minuto por usuario.
- Sesiones: TTL 120 min, últimos 6 turnos; idempotencia por `activity.id` 24 h; conservar 3 versiones de corpus.
- Teams: webhook saliente con HMAC-SHA256 obligatorio, sin fail-open; `/health` liveness y `/ready` readiness real.
- Seguridad: usuario no root `10001`, `read_only`, `cap_drop: ALL`, sin `docker.sock`, sin `sudo`, sin `systemd`, sin cron del host.
- Modelos: artefacto versionado con URL y SHA-256 fijos; nunca descarga en runtime.
- Fechas ISO-8601 en UTC; rutas POSIX relativas; UTF-8; `LANG=C.UTF-8`.
- PDFs: todos los `.md`, salida espejo en `/data/pdfs/<rel_path>.pdf`, no se publican por HTTP.

## Review Focus

Estas son las entradas o fallas que el spec implica y que más probablemente rompan el sistema. Cada una tiene su prueba en la tarea indicada.

1. **Nombres con espacios, acentos, paréntesis, CRLF/BOM y una imagen faltante** (`OK_SopPro_confirmaci_n_remesas_cajero_sin_total_img1.png`): deben indexarse o advertirse sin romper el lote. Pruebas en tareas 5, 6 y 19.
2. **Reintento de Teams y deadline cercano a 5 s:** un `activity.id` repetido no debe ejecutar dos veces el pipeline y el pipeline debe degradar antes de que Teams corte. Pruebas en tarea 18.
3. **Fallo a mitad de indexación o dos sincronizaciones a la vez:** la versión activa debe quedar intacta y el lock debe rechazar la segunda. Pruebas en tareas 9, 10 y 11.
4. **Pregunta sin app o sin contexto suficiente:** no debe llamarse al LLM ni inventarse una respuesta. Pruebas en tareas 15 y 18.
5. **PDF con tabla larga, bloque de código o imagen faltante:** un archivo fallido no debe detener el lote ni afectar al RAG. Pruebas en tarea 19.

---

## File Map

```
geoassist/
├── pyproject.toml                    # deps, extras tools, ruff/mypy/pytest
├── uv.lock
├── Dockerfile                        # runtime api
├── docker/sync/Dockerfile            # git + pandoc + texlive
├── docker/Caddyfile
├── compose.yml
├── compose.dev.yml
├── Makefile
├── .env.example
├── .gitignore
├── .dockerignore
├── config/corpus_rules.yml
├── migrations/corpus/001_init.sql
├── migrations/state/001_init.sql
├── scripts/publish_models.py
├── eval/
│   ├── golden.jsonl
│   ├── evaluate.py
│   └── fixtures/
├── src/geoassist/
│   ├── __init__.py
│   ├── config.py                     # Settings, get_settings
│   ├── logging.py                    # JSON + redacción
│   ├── models.py                     # ChatMessage, InboundMessage, etc.
│   ├── healthcheck.py                # script para Docker
│   ├── main.py                       # create_app, lifespan
│   ├── api/
│   │   ├── app.py
│   │   ├── readiness.py
│   │   ├── ratelimit.py
│   │   └── routes/{health,teams,debug}.py
│   ├── services/chat.py              # ChatService
│   ├── teams/{base,outgoing_webhook}.py
│   ├── rag/
│   │   ├── tokenization.py
│   │   ├── embeddings.py
│   │   ├── vector_store.py
│   │   ├── lexical.py
│   │   ├── fusion.py
│   │   ├── rerank.py
│   │   ├── apps.py
│   │   ├── types.py
│   │   ├── retriever.py
│   │   └── prompt.py
│   ├── llm/{base,openai_compatible}.py
│   ├── ingest/{rules,metadata,loader,chunker,pipeline}.py
│   ├── storage/{migrations,corpus_write,corpus_read,versions,state}.py
│   ├── sync/{git,lock,orchestrator,__main__}.py
│   ├── pdf/{renderer,images}.py
│   └── pdf/templates/plantilla_manuales.tex
├── tests/
│   ├── conftest.py
│   ├── factories.py
│   ├── fixtures/
│   └── test_*.py
└── docs/
```

---

### Task 1: Esqueleto, configuración, logging y migraciones

**Files:**
- Create: `pyproject.toml`, `uv.lock`, `src/geoassist/__init__.py`, `src/geoassist/config.py`, `src/geoassist/logging.py`
- Create: `src/geoassist/storage/__init__.py`, `src/geoassist/storage/migrations.py`
- Create: `migrations/corpus/001_init.sql`, `migrations/state/001_init.sql`
- Test: `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_logging.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings) con todos los campos del spec y validación de producción.
- Produces: `get_settings() -> Settings`.
- Produces: `configure_logging(level: str) -> None` y `get_logger(name: str)`.
- Produces: `apply_migrations(db_path: Path, migrations_dir: Path) -> None`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_config.py
import pytest
from pydantic import ValidationError
from geoassist.config import Settings


def make_settings(**overrides):
    values = dict(
        app_env="development",
        teams_security_token="teams-token",
        llm_api_key="llm-key",
        llm_data_policy="external_approved",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_requires_teams_token():
    with pytest.raises(ValidationError):
        make_settings(app_env="production", teams_security_token="")


def test_production_requires_defined_llm_policy():
    with pytest.raises(ValidationError):
        make_settings(app_env="production", llm_data_policy=None)


def test_on_prem_only_rejects_public_host():
    with pytest.raises(ValidationError):
        make_settings(llm_data_policy="on_prem_only", llm_base_url="https://api.deepseek.com")


def test_on_prem_only_accepts_allowed_host():
    settings = make_settings(
        llm_data_policy="on_prem_only",
        llm_base_url="http://llm.internal:8080",
        llm_allowed_hosts=("llm.internal",),
    )
    assert settings.llm_data_policy == "on_prem_only"


def test_defaults_match_spec():
    settings = make_settings()
    assert settings.session_ttl_minutes == 120
    assert settings.rate_limit_per_minute == 6
    assert settings.manuals_root_rel == "data_source/manuales"
    assert settings.assets_root_rel == "data_source/assets"
```

```python
# tests/test_migrations.py
from pathlib import Path
from geoassist.storage.migrations import apply_migrations

ROOT = Path(__file__).resolve().parents[1]


def table_names(db_path):
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    return {row[0] for row in rows}


def test_corpus_migrations_create_schema(tmp_path):
    db = tmp_path / "corpus.db"
    apply_migrations(db, ROOT / "migrations" / "corpus")
    assert {"documents", "chunks", "chunks_fts", "corpus_meta", "schema_migrations"} <= table_names(
        db
    )


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "state.db"
    apply_migrations(db, ROOT / "migrations" / "state")
    apply_migrations(db, ROOT / "migrations" / "state")
    assert {"sessions", "messages", "activities", "feedback", "schema_migrations"} <= table_names(
        db
    )
```

```python
# tests/test_logging.py
from geoassist.logging import configure_logging, get_logger


def test_secret_fields_are_redacted(capsys):
    configure_logging("INFO")
    log = get_logger("test")
    log.info("request", authorization="Bearer super-secret", api_key="sk-secret", question="hola")
    output = capsys.readouterr().out
    assert "super-secret" not in output
    assert "sk-secret" not in output
    assert "hola" in output
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_config.py tests/test_migrations.py tests/test_logging.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`Settings` usa `pydantic-settings`, `SecretStr` para secretos y un `model_validator(mode="after")` que en `production` exige `teams_security_token`, `llm_api_key`, `llm_data_policy` y, si la política es `on_prem_only`, que el host de `llm_base_url` esté en `llm_allowed_hosts`. Incluir todos los campos de la tabla 14.2 del spec con los defaults indicados. `get_settings()` con `lru_cache`.

`logging.py` configura `structlog` con `JSONRenderer`, timestamp UTC y un procesador que reemplaza por `"***"` los valores de claves cuyo nombre contenga `token`, `secret`, `api_key`, `authorization` o `password`. Salida a stdout.

`storage/migrations.py` crea `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)`, lista `*.sql` ordenados, aplica cada uno dentro de una transacción y registra la versión. Ignora los ya aplicados.

Los dos `001_init.sql` contienen exactamente los esquemas de las secciones 7.2 y 7.3 del spec. `chunks_fts` es una tabla FTS5 externa sobre `chunks`; `documents` y `chunks` llevan índices por `app`, `status` y `doc_id`.

`tests/conftest.py` agrega `src/` y la raíz del proyecto al `sys.path` si el paquete no está instalado; `tests/__init__.py` vacío hace importable `tests.factories`.

`pyproject.toml` declara el paquete `src/geoassist`, Python 3.12, dependencias de runtime, grupo de test (`pytest`, `httpx`, `ruff`, `mypy`) y extras `tools`/`sync`; el lockfile se genera con `uv lock`.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_config.py tests/test_migrations.py tests/test_logging.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/geoassist tests migrations
git commit -m "feat: esqueleto, configuración, logging y migraciones"
```

---

### Task 2: Artefacto versionado de modelos

**Files:**
- Create: `scripts/publish_models.py`
- Create: `tests/test_publish_models.py`
- Modify: `pyproject.toml` (extra `tools` con `torch`, `transformers`, `huggingface-hub`, `onnx`, `onnxruntime`, `zstandard`; `zstandard` también en el grupo de test)

**Interfaces:**
- Produces: `sha256_file(path: Path) -> str`.
- Produces: `artifact_filename(version: str) -> str` → `geoassist-models-<version>.tar.zst`.
- Produces: `build_bundle(model_dirs: Mapping[str, Path], version: str, output_dir: Path) -> Path`; escribe el `.tar.zst` y su `.sha256`.
- CLI: `python scripts/publish_models.py --version V --output-dir dist [--export]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_publish_models.py
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
    bge.mkdir()
    rerank.mkdir()
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
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_publish_models.py -v`
Expected: FAIL por módulo inexistente.

- [ ] **Step 3: Implementar**

`build_bundle` empaqueta los directorios bajo un prefijo `models/`, ordena entradas para reproducibilidad, fija `mtime` a 0, comprime con `zstandard.ZstdCompressor` sobre un `tarfile` en modo stream (`w|`) y escribe el checksum en formato `sha256sum`. Actualizar el lockfile con `uv lock` e instalar el grupo de test antes de correr la suite. El CLI, con `--export`, reutiliza la lógica del script de referencia `/home/secragx/Documentos/Geocom_Software/GEO_RAG_CHATBOT/tools/export_models.py` para exportar BGE-M3 y el reranker desde revisiones fijadas de Hugging Face y cuantizarlos; sin `--export`, solo empaqueta directorios existentes. Al final imprime `MODELS_ARTIFACT_URL` y `MODELS_ARTIFACT_SHA256` sugeridos.

La publicación al release es un paso del operador o del Makefile con `gh release upload`; el script no inventa credenciales.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_publish_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/publish_models.py tests/test_publish_models.py pyproject.toml uv.lock
git commit -m "feat: empaquetado versionado de modelos ONNX"
```

---

### Task 3: Runtime Docker, API mínima y Compose

**Files:**
- Create: `Dockerfile`, `compose.yml`, `compose.dev.yml`, `docker/Caddyfile`
- Create: `Makefile`, `.env.example`, `.gitignore`, `.dockerignore`
- Create: `src/geoassist/main.py`, `src/geoassist/api/app.py`, `src/geoassist/api/readiness.py`
- Create: `src/geoassist/api/routes/health.py`, `src/geoassist/healthcheck.py`
- Test: `tests/api/test_health.py`

**Interfaces:**
- Produces: `create_app(settings: Settings | None = None, probe: ReadinessProbe | None = None) -> FastAPI`.
- Produces: `ReadinessProbe(checks: Mapping[str, Callable[[], bool]])` con `run() -> tuple[bool, dict[str, bool]]`.
- Produces: `python -m geoassist.healthcheck liveness|readiness`, exit 0/1.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/api/test_health.py
from fastapi.testclient import TestClient

from geoassist.api.app import create_app
from geoassist.api.readiness import ReadinessProbe


def test_liveness_is_independent_of_dependencies():
    app = create_app(probe=ReadinessProbe({"corpus": lambda: False}))
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_each_check():
    probe = ReadinessProbe({"corpus": lambda: False, "models": lambda: True})
    response = TestClient(create_app(probe=probe)).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "checks": {"corpus": False, "models": True}}


def test_readiness_returns_200_when_all_checks_pass():
    probe = ReadinessProbe({"corpus": lambda: True, "models": lambda: True})
    response = TestClient(create_app(probe=probe)).get("/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is True
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/api/test_health.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`create_app` registra los routers y guarda `settings` y `probe` en `app.state`. `/health` responde siempre `{"status":"ok"}`. `/ready` ejecuta el probe, devuelve 200 si todas las comprobaciones pasan y 503 si alguna falla, con el detalle por check.

`Dockerfile` multi-stage con `python:3.12-slim` fijado por digest (resolverlo con `docker buildx imagetools inspect python:3.12-slim` y escribir el digest), `uv` para instalar con `uv.lock`, `curl`, `ca-certificates`, `zstd` y `tar` en la etapa de descarga del artefacto, verificación `sha256sum -c`, extracción a `/app/models`, usuario `10001`, `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `LANG=C.UTF-8`, `TZ=UTC`. CMD: `uvicorn geoassist.main:app --host 0.0.0.0 --port 8000 --workers 1`.

`compose.yml` define `api` (sin puertos publicados, volumen `geoassist_data:/data`, healthcheck `python -m geoassist.healthcheck liveness`, `read_only`, `tmpfs: /tmp`, `cap_drop: [ALL]`, `no-new-privileges`, `mem_limit: 3g`, `cpus: 2`, `restart: unless-stopped`), `caddy` (perfil `prod`, publica 80/443, depende de `api` healthy) y `ngrok` (perfil `dev`, imagen fijada, token por entorno). `compose.dev.yml` activa ngrok y monta el código para desarrollo.

`.env.example` incluye todas las variables de la tabla 14.2 y las de modelos. `.gitignore` cubre `.env`, `dist/`, `models/`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `*.db`. `.dockerignore` excluye `.git`, `.env`, `docs`, `tests`, `dist`, `models`, `venv`.

`Makefile` con `build`, `up`, `down`, `logs`, `test`, `lint`, `shell`, `db`, `publish-models`.

- [ ] **Step 4: Verificar pruebas y configuración**

Run: `pytest tests/api/test_health.py -v`
Expected: PASS.

Run: `docker compose config --quiet && docker compose --profile dev config --quiet`
Expected: sin salida, exit 0.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile compose.yml compose.dev.yml docker Makefile .env.example .gitignore .dockerignore src/geoassist tests/api
git commit -m "feat: runtime Docker, API de salud y compose base"
```

---

### Task 4: Reglas de corpus y resolución de metadatos

**Files:**
- Create: `config/corpus_rules.yml`
- Create: `src/geoassist/ingest/__init__.py`, `src/geoassist/ingest/rules.py`, `src/geoassist/ingest/metadata.py`
- Test: `tests/test_rules.py`, `tests/test_metadata.py`, `tests/fixtures/corpus_rules.yml`

**Interfaces:**
- Produces: `CorpusRules.load(path: Path) -> CorpusRules` con `is_ignored(rel_path)`, `app_for(rel_path)`, `status_for(rel_path)`, `aliases() -> dict[str, str]`.
- Produces: `DocMetadata(rel_path, title, app, status, version, frontmatter)`.
- Produces: `MetadataResolver(rules).resolve(rel_path: str, frontmatter: Mapping[str, Any], first_h1: str | None) -> DocMetadata`.
- Constantes: `STATUSES = ("approved", "pending", "archived", "unknown")`, `CANONICAL_APPS = ("geopos", "geoinventarios", "geopricer", "geopromotion", "unknown")`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_rules.py
from pathlib import Path
from geoassist.ingest.rules import CorpusRules

RULES = Path(__file__).parent / "fixtures" / "corpus_rules.yml"


def test_suffix_and_accent_insensitive_matching():
    rules = CorpusRules.load(RULES)
    rel = "data_source/manuales/OXXO/GEOPOS - OK/VERSIONES APROBADAS - OK/a.md"
    assert rules.app_for(rel) == "geopos"
    assert rules.status_for(rel) == "approved"


def test_pending_is_evaluated_before_approved():
    rules = CorpusRules.load(RULES)
    rel = "data_source/manuales/OXXO/GEOPRICER - OK/VERSIONES POR APROBAR - OK/a.md"
    assert rules.status_for(rel) == "pending"


def test_template_is_ignored():
    rules = CorpusRules.load(RULES)
    assert rules.is_ignored("data_source/manuales/OXXO/model_manuales.md") is True
```

```python
# tests/test_metadata.py
from pathlib import Path
from geoassist.ingest.metadata import MetadataResolver
from geoassist.ingest.rules import CorpusRules

RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")
REL = "data_source/manuales/OXXO/GEOPOS - OK/VERSIONES APROBADAS - OK/SopPro_Error_Acceso.md"


def test_frontmatter_wins_over_path():
    meta = MetadataResolver(RULES).resolve(
        REL,
        {"app": "geopricer", "status": "pending", "title": "Manual real", "version": "2.0"},
        first_h1="Objeto",
    )
    assert (meta.app, meta.status, meta.title, meta.version) == (
        "geopricer",
        "pending",
        "Manual real",
        "2.0",
    )


def test_placeholder_title_falls_back_to_filename():
    meta = MetadataResolver(RULES).resolve(REL, {"title": "Nombre del Manual"}, first_h1="Objeto")
    assert meta.title == "SopPro Error Acceso"


def test_generic_heading_is_not_used_as_title():
    meta = MetadataResolver(RULES).resolve(REL, {"title": "Nombre del Manual"}, first_h1="Alcance")
    assert meta.title == "SopPro Error Acceso"


def test_unknown_app_and_status_are_reported_not_raised():
    meta = MetadataResolver(RULES).resolve("otra/ruta.md", {}, first_h1=None)
    assert meta.app == "unknown"
    assert meta.status == "unknown"


def test_alias_is_canonicalized():
    meta = MetadataResolver(RULES).resolve(REL, {"app": "geoprice"}, first_h1=None)
    assert meta.app == "geopricer"
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_rules.py tests/test_metadata.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`corpus_rules.yml` de producción replica el ejemplo del spec y agrega `**/model_manuales.md` a `ignore`. `tests/fixtures/corpus_rules.yml` es una copia reducida con las reglas de las cuatro apps y los tres estados.

La normalización de rutas y valores usa NFKD, quita acentos, baja a minúsculas y colapsa espacios. `match` es subcadena sobre `rel_path` normalizado; las reglas se evalúan en orden. `ignore` se evalúa con `fnmatch` sobre la misma ruta normalizada (los `*` cruzan separadores, así `**/model_manuales.md` cubre cualquier profundidad). `MetadataResolver` aplica la precedencia frontmatter → ruta → default, canonicaliza alias de app y valida el estado contra `STATUSES`. El título sigue la regla del spec, incluyendo placeholders y encabezados genéricos.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_rules.py tests/test_metadata.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/corpus_rules.yml src/geoassist/ingest tests/test_rules.py tests/test_metadata.py tests/fixtures/corpus_rules.yml
git commit -m "feat: reglas de corpus y metadatos"
```

### Task 5: Descubrimiento de manuales y tokenizer

**Files:**
- Create: `src/geoassist/ingest/loader.py`, `src/geoassist/rag/__init__.py`, `src/geoassist/rag/tokenization.py`
- Create: `tests/fixtures/repo/data_source/manuales/OXXO/...` (3 manuales, 1 plantilla, 1 `.txt`)
- Test: `tests/test_loader.py`, `tests/test_tokenization.py`

**Interfaces:**
- Produces: `RawDocument(rel_path, text, frontmatter, content_hash, warnings)`.
- Produces: `ManualLoader(repo_root: Path, manuals_root_rel: str, rules: CorpusRules, assets_root_rel: str = "data_source/assets").discover() -> list[RawDocument]`.
- Produces: `extract_first_h1(markdown: str) -> str | None`.
- Produces: `TokenCounter` (`count(text: str) -> int`) y `XlmRobertaTokenCounter(tokenizer_path: Path)`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_loader.py
from pathlib import Path
from geoassist.ingest.loader import ManualLoader, extract_first_h1
from geoassist.ingest.rules import CorpusRules

ROOT = Path(__file__).parent / "fixtures" / "repo"
RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


def test_discovers_only_markdown_under_manuals_root():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    assert len(docs) == 3
    assert all(doc.rel_path.endswith(".md") for doc in docs)
    assert all(not doc.rel_path.endswith("model_manuales.md") for doc in docs)


def test_paths_are_repo_relative_posix():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    assert docs[0].rel_path.startswith("data_source/manuales/OXXO/")


def test_crlf_and_bom_are_normalized():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "Acento" in d.rel_path)
    assert "\r" not in doc.text
    assert not doc.text.startswith("\ufeff")


def test_invalid_yaml_warns_and_keeps_text():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "YamlRoto" in d.rel_path)
    assert doc.frontmatter == {}
    assert any("yaml" in warning.lower() for warning in doc.warnings)


def test_missing_image_reference_warns():
    docs = ManualLoader(ROOT, "data_source/manuales", RULES).discover()
    doc = next(d for d in docs if "ImagenFaltante" in d.rel_path)
    assert any("imagen" in warning.lower() for warning in doc.warnings)


def test_extract_first_h1():
    assert extract_first_h1("texto\n\n# Título real\n## Sección\n") == "Título real"
    assert extract_first_h1("sin encabezados") is None
```

```python
# tests/test_tokenization.py
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from geoassist.rag.tokenization import XlmRobertaTokenCounter


def test_counts_with_real_tokenizer_file(tmp_path):
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "hola": 1, "mundo": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    counter = XlmRobertaTokenCounter(path)
    assert counter.count("hola mundo") == 2
    assert counter.count("") == 0
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_loader.py tests/test_tokenization.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`ManualLoader` resuelve `repo_root / manuals_root_rel`, recorre con `rglob("*.md")`, descarta los que `rules.is_ignored()` marque, ordena por `rel_path` y no sigue symlinks que salgan de la raíz. Lee bytes, detecta y quita BOM, normaliza CRLF/CR a LF, parsea frontmatter con `python-frontmatter`; si el YAML falla, deja `{}` y agrega un warning. Para cada `![alt](src)` busca el archivo por nombre base en `repo_root / assets_root_rel`; si no existe, agrega un warning `imagen ausente: <src>` y continúa. Calcula `content_hash` con SHA-256 del texto normalizado. `extract_first_h1` devuelve el texto del primer encabezado ATX de nivel 1, ignorando bloques de código.

`XlmRobertaTokenCounter` carga un `tokenizers.Tokenizer` desde `tokenizer.json` y cuenta `len(encoding.ids)`. Es el tokenizer real embebido en el directorio del modelo.

Los fixtures incluyen nombres con acentos, espacios y paréntesis, un `.md` con CRLF+BOM (`Acento_Manual.md`), uno con YAML inválido (`YamlRoto.md`), uno con una referencia a imagen inexistente (`ImagenFaltante.md`), al menos un manual con la palabra `recepción` y frontmatter de `app`/`status` que hagan deterministas las pruebas de filtros.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_loader.py tests/test_tokenization.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/ingest/loader.py src/geoassist/rag tests/test_loader.py tests/test_tokenization.py tests/fixtures/repo
git commit -m "feat: descubrimiento de manuales y tokenizer"
```

---

### Task 6: Chunker por estructura

**Files:**
- Create: `src/geoassist/ingest/chunker.py`
- Test: `tests/test_chunker.py`

**Interfaces:**
- Produces: `TextChunk(ordinal, heading, text, token_count, has_images)`.
- Produces: `Chunker(counter: TokenCounter, target_tokens=450, max_tokens=600, min_tokens=80, overlap_ratio=0.12, boilerplate_headings=BOILERPLATE_HEADINGS).chunk(markdown: str, title: str) -> list[TextChunk]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

Usar un contador de palabras de prueba (`cada palabra = 1 token`) para hacer deterministas los límites.

```python
# tests/test_chunker.py
import pytest
from geoassist.ingest.chunker import Chunker


class WordCounter:
    def count(self, text):
        return len(text.split())


def test_heading_breadcrumb_and_ordinals():
    md = "# Manual\n\ntexto uno\n\n## Sección A\n\ntexto dos\n"
    chunks = Chunker(WordCounter()).chunk(md, title="Manual")
    assert chunks[0].heading == "Manual"
    assert chunks[1].heading == "Manual > Sección A"
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_long_paragraph_is_split_and_overlaps():
    words = [f"w{i}" for i in range(1200)]
    md = "# T\n\n" + " ".join(words)
    chunks = Chunker(WordCounter(), max_tokens=600, target_tokens=450).chunk(md, title="T")
    assert len(chunks) >= 3
    assert all(c.token_count <= 600 for c in chunks)
    first = set(chunks[0].text.split()[-60:])
    second = set(chunks[1].text.split()[:60])
    assert len(first & second) >= 40


def test_short_sections_are_merged():
    md = "# T\n\n## A\n\ncorto\n\n## B\n\ncorto dos\n"
    chunks = Chunker(WordCounter(), min_tokens=80).chunk(md, title="T")
    assert len(chunks) == 1


def test_table_is_kept_whole_when_it_fits():
    rows = "\n".join(f"| fila {i} | valor {i} |" for i in range(10))
    md = f"# T\n\n| col | val |\n|---|---|\n{rows}\n"
    chunks = Chunker(WordCounter()).chunk(md, title="T")
    assert len(chunks) == 1
    assert "fila 9" in chunks[0].text


def test_long_table_repeats_header():
    rows = "\n".join(f"| fila {i} | valor {i} |" for i in range(200))
    md = f"# T\n\n| col | val |\n|---|---|\n{rows}\n"
    chunks = Chunker(WordCounter(), max_tokens=120).chunk(md, title="T")
    assert len(chunks) > 1
    assert all("| col | val |" in c.text for c in chunks)


def test_code_block_is_not_split_when_it_fits():
    code = "```sql\n" + "\n".join(f"SELECT {i};" for i in range(10)) + "\n```"
    chunks = Chunker(WordCounter()).chunk(f"# T\n\n{code}\n", title="T")
    assert len(chunks) == 1
    assert "SELECT 9;" in chunks[0].text


def test_boilerplate_section_is_skipped():
    md = "# T\n\n## Elaboración, Revisión y Aprobación\n\n| a | b |\n\n# Objeto\n\ncontenido útil\n"
    chunks = Chunker(WordCounter()).chunk(md, title="T")
    assert all("Elaboración" not in c.text for c in chunks)


def test_image_alt_is_kept_and_flagged():
    md = "# T\n\n![Pantallazo del error](SopPro_img1.png)\n"
    chunk = Chunker(WordCounter()).chunk(md, title="T")[0]
    assert "Pantallazo del error" in chunk.text
    assert chunk.has_images is True
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_chunker.py -v`
Expected: FAIL por módulo inexistente.

- [ ] **Step 3: Implementar**

El chunker recorre líneas y agrupa en bloques: párrafos, tablas (líneas consecutivas que empiezan con `|`), bloques de código cercados y encabezados. Mantiene una pila de encabezados H1–H4 para el breadcrumb. Descarta secciones cuyo encabezado esté en `BOILERPLATE_HEADINGS` (incluye “Elaboración, Revisión y Aprobación”). Fusiona bloques cortos de la misma sección hasta `min_tokens`. Si un bloque supera `max_tokens`, lo parte por palabras; las tablas repiten la fila de encabezado en cada parte y los bloques de código solo se parten si exceden el máximo. Agrega solape tomando las últimas `int(overlap_ratio * target_tokens)` palabras del fragmento anterior al inicio del siguiente. `has_images` es verdadero si el texto contiene `![`.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_chunker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/ingest/chunker.py tests/test_chunker.py
git commit -m "feat: chunking por estructura con solape"
```

---

### Task 7: Embedder ONNX BGE-M3

**Files:**
- Create: `src/geoassist/rag/embeddings.py`
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Produces: `Embedder` Protocol con `dimension: int`, `embed_documents(texts) -> list[list[float]]`, `embed_query(text) -> list[float]`.
- Produces: `pool_cls_normalize(hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray`.
- Produces: `OnnxBgeM3Embedder(model_dir: Path | None = None, *, session=None, tokenizer=None, max_length=512, batch_size=16, threads=4)` con `dimension = 1024`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_embeddings.py
import numpy as np
from geoassist.rag.embeddings import OnnxBgeM3Embedder, pool_cls_normalize


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
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_embeddings.py -v`
Expected: FAIL por módulo inexistente.

- [ ] **Step 3: Implementar**

`pool_cls_normalize` toma el token CLS (`hidden_state[:, 0, :]`), lo normaliza L2 con epsilon y devuelve `float32`. `OnnxBgeM3Embedder` carga `model.onnx` y `tokenizer.json` del directorio si no se inyectan; configura `intra_op_num_threads`; en `embed_documents` procesa en lotes, construye `input_ids` y `attention_mask`, llama `session.run`, aplica pooling y devuelve listas de floats; `embed_query` reutiliza `embed_documents([text])[0]`. Si la dimensión del vector no es 1024, lanza `EmbeddingDimensionError`.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_embeddings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/embeddings.py tests/test_embeddings.py
git commit -m "feat: embedder ONNX BGE-M3"
```

---

### Task 8: Índice FAISS

**Files:**
- Create: `src/geoassist/rag/vector_store.py`
- Test: `tests/test_vector_store.py`

**Interfaces:**
- Produces: `VectorHit(chunk_row_id: int, score: float)`.
- Produces: `VectorStore.build(index_path: Path, vectors: np.ndarray, ids: Sequence[int]) -> None`.
- Produces: `VectorStore(index_path: Path).search(query: Sequence[float], limit: int) -> list[VectorHit]`.
- Produce: `VectorStoreError`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_vector_store.py
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
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_vector_store.py -v`
Expected: FAIL por módulo inexistente.

- [ ] **Step 3: Implementar**

Usa `faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))`. `build` normaliza L2 las filas si no lo están, agrega con `np.asarray(ids, dtype=np.int64)`, escribe con `faiss.write_index` a un temporal en el mismo directorio y hace `os.replace` para publicación atómica. `search` valida dimensión y devuelve `VectorHit` ordenados por score descendente.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_vector_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/vector_store.py tests/test_vector_store.py
git commit -m "feat: índice FAISS con IDs estables"
```

---

### Task 9: Pipeline de ingesta y corpus SQLite

**Files:**
- Create: `src/geoassist/ingest/pipeline.py`, `src/geoassist/storage/corpus_write.py`
- Create: `tests/factories.py`
- Test: `tests/test_ingest_pipeline.py`

**Interfaces:**
- Produces: `IngestReport(version_id, doc_count, chunk_count, warnings, skipped)`.
- Produces: `IngestValidationError`.
- Produces: `DocumentRow(doc_id, rel_path, title, app, status, version, content_hash, frontmatter_json)` y `ChunkRow(chunk_id, doc_id, ordinal, heading, text, token_count, has_images)`.
- Produces: `Ingestor(loader, chunker, resolver, embedder, counter).build(version_id: str, output_dir: Path) -> IngestReport`.
- Produce: `write_corpus(db_path: Path, documents: Sequence[DocumentRow], chunks: Sequence[ChunkRow]) -> None`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/factories.py
import hashlib
import numpy as np


class HashEmbedder:
    dimension = 8

    def embed_documents(self, texts):
        result = []
        for text in texts:
            seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            vector = rng.normal(size=self.dimension)
            result.append((vector / np.linalg.norm(vector)).tolist())
        return result

    def embed_query(self, text):
        return self.embed_documents([text])[0]
```

```python
# tests/test_ingest_pipeline.py
import json
import sqlite3
from pathlib import Path

import pytest

from geoassist.ingest.chunker import Chunker
from geoassist.ingest.loader import ManualLoader
from geoassist.ingest.metadata import MetadataResolver
from geoassist.ingest.pipeline import Ingestor, IngestValidationError
from geoassist.ingest.rules import CorpusRules
from tests.factories import HashEmbedder

ROOT = Path(__file__).parent / "fixtures" / "repo"
RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


class WordCounter:
    def count(self, text):
        return len(text.split())


def make_ingestor(embedder=None):
    return Ingestor(
        loader=ManualLoader(ROOT, "data_source/manuales", RULES),
        chunker=Chunker(WordCounter()),
        resolver=MetadataResolver(RULES),
        embedder=embedder or HashEmbedder(),
        counter=WordCounter(),
    )


def test_build_writes_documents_chunks_fts_and_manifest(tmp_path):
    report = make_ingestor().build("v1", tmp_path)
    assert report.doc_count == 3
    assert report.chunk_count > 0
    db = sqlite3.connect(tmp_path / "corpus.db")
    assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == report.chunk_count
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["version_id"] == "v1"
    assert manifest["chunk_count"] == report.chunk_count
    assert manifest["db_sha256"] and manifest["index_sha256"]


def test_template_is_not_indexed(tmp_path):
    make_ingestor().build("v1", tmp_path)
    db = sqlite3.connect(tmp_path / "corpus.db")
    paths = [row[0] for row in db.execute("SELECT rel_path FROM documents")]
    assert all("model_manuales.md" not in path for path in paths)


def test_missing_image_is_a_warning_not_a_failure(tmp_path):
    report = make_ingestor().build("v1", tmp_path)
    assert any("imagen" in warning.lower() for warning in report.warnings)


def test_dimension_mismatch_aborts_and_removes_output(tmp_path):
    class BrokenEmbedder(HashEmbedder):
        def embed_documents(self, texts):
            return [[0.1, 0.2] for _ in texts]

    with pytest.raises(IngestValidationError):
        make_ingestor(BrokenEmbedder()).build("v1", tmp_path)
    assert not (tmp_path / "corpus.db").exists()
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_ingest_pipeline.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`Ingestor.build` crea un directorio temporal dentro de `output_dir.parent`, aplica migraciones, recorre los documentos, resuelve metadata con `first_h1`, genera chunks, embebe `heading + "\n" + text`, escribe documentos y chunks, puebla `chunks_fts` con `INSERT INTO chunks_fts(rowid, heading, text) SELECT id, heading, text FROM chunks`, escribe `corpus_meta`, construye `faiss.index` con `VectorStore.build`, valida conteos, dimensiones, duplicados y huérfanos, calcula SHA-256 de `corpus.db` e `faiss.index` y escribe `manifest.json`. Solo al final renombra el temporal a `output_dir`; ante cualquier excepción borra el temporal y propaga.

Las advertencias incluyen YAML inválido, app/estado desconocidos y referencias de imagen ausentes. `skipped` registra archivos ignorados con motivo.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_ingest_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/ingest/pipeline.py src/geoassist/storage/corpus_write.py tests/factories.py tests/test_ingest_pipeline.py
git commit -m "feat: pipeline de ingesta con validación y manifiesto"
```

### Task 10: Publicación atómica, lector de corpus y búsqueda léxica

**Files:**
- Create: `src/geoassist/storage/versions.py`, `src/geoassist/storage/corpus_read.py`, `src/geoassist/rag/lexical.py`
- Test: `tests/test_versions.py`, `tests/test_corpus_read.py`, `tests/test_lexical.py`

**Interfaces:**
- Produces: `CorpusVersion(version_id, git_commit, db_path, index_path, db_sha256, index_sha256, doc_count, chunk_count, activated_at)`.
- Produces: `publish_version(build_dir: Path, versions_dir: Path, version_id: str) -> CorpusVersion`.
- Produces: `write_current_atomic(corpus_dir: Path, version: CorpusVersion) -> None` y `read_current(corpus_dir: Path) -> CorpusVersion | None`.
- Produces: `VersionManager(corpus_dir: Path, *, refresh_seconds=30, clock=...)` con `refresh(force=False) -> CorpusVersion`, `current() -> CorpusVersion | None` y `activate(version_id) -> CorpusVersion`.
- Produces: `ChunkView(id, chunk_id, doc_id, heading, text, title, version, status, app, rel_path)`.
- Produces: `LexicalHit(chunk_row_id, score)`.
- Produces: `CorpusReader(db_path).lexical_search(query, limit, apps=None, statuses=("approved","pending")) -> list[LexicalHit]`, `get_chunks(ids)`, `counts()`.
- Produces: `build_fts_query(question: str) -> str`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_lexical.py
from geoassist.rag.lexical import build_fts_query


def test_quotes_terms_and_joins_with_or():
    assert build_fts_query("error al cargar Geopricer") == '"error" OR "cargar" OR "Geopricer"'


def test_drops_short_tokens_and_operators():
    query = build_fts_query('¿o? " OR 1=1 --')
    assert "OR 1=1" not in query
    assert '"o"' not in query


def test_empty_question_returns_empty_string():
    assert build_fts_query("   ") == ""
```

```python
# tests/test_versions.py
import hashlib
import json
from pathlib import Path

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
```

```python
# tests/test_corpus_read.py
from pathlib import Path
import pytest
from geoassist.ingest.chunker import Chunker
from geoassist.ingest.loader import ManualLoader
from geoassist.ingest.metadata import MetadataResolver
from geoassist.ingest.pipeline import Ingestor
from geoassist.ingest.rules import CorpusRules
from geoassist.storage.corpus_read import CorpusReader
from tests.factories import HashEmbedder

ROOT = Path(__file__).parent / "fixtures" / "repo"
RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


class WordCounter:
    def count(self, text):
        return len(text.split())


@pytest.fixture
def corpus(tmp_path):
    Ingestor(
        ManualLoader(ROOT, "data_source/manuales", RULES),
        Chunker(WordCounter()),
        MetadataResolver(RULES),
        HashEmbedder(),
        WordCounter(),
    ).build("v1", tmp_path)
    return CorpusReader(tmp_path / "corpus.db")


def test_lexical_search_is_accent_insensitive(corpus):
    hits = corpus.lexical_search("recepcion", limit=10)
    assert hits
    assert all(isinstance(hit.chunk_row_id, int) for hit in hits)


def test_lexical_search_filters_status_and_app(corpus):
    hits = corpus.lexical_search("manual", limit=10, apps=["geopos"], statuses=["approved"])
    assert hits
    chunks = corpus.get_chunks([hit.chunk_row_id for hit in hits])
    assert all(chunk.app == "geopos" and chunk.status == "approved" for chunk in chunks)


def test_get_chunks_preserves_requested_order(corpus):
    hits = corpus.lexical_search("manual", limit=5)
    assert hits
    ids = [hit.chunk_row_id for hit in hits]
    assert [chunk.id for chunk in corpus.get_chunks(ids)] == ids
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_versions.py tests/test_corpus_read.py tests/test_lexical.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`publish_version` verifica que el build tenga `corpus.db`, `faiss.index` y `manifest.json`, comprueba checksums contra el manifiesto, renombra a `versions/<version_id>`, devuelve `CorpusVersion` y elimina las versiones más antiguas conservando las últimas 3 (nunca la que apunta `current.json`). `write_current_atomic` escribe `current.json.tmp`, `fsync`, `os.replace` y `fsync` del directorio; guarda `db` e `index` relativos a `corpus_dir`. `read_current` devuelve `None` si no existe y resuelve esas rutas a absolutas. `VersionManager` cachea la versión, revalida cada 30 s, verifica checksums al cargar y conserva la anterior si la nueva falla; `activate` valida y publica una versión existente.

`CorpusReader` abre SQLite en solo lectura (`file:...?mode=ro`), ejecuta la consulta FTS con `bm25(chunks_fts, 3.0, 1.0)` y hace join con `documents` para filtrar `app` y `status`. `get_chunks` devuelve `ChunkView` en el orden pedido.

`build_fts_query` tokeniza por palabras alfanuméricas de 3+ caracteres, descarta operadores FTS (`OR`, `AND`, `NOT`, `NEAR`), entrecomilla cada término y los une con ` OR `.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_versions.py tests/test_corpus_read.py tests/test_lexical.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/storage/versions.py src/geoassist/storage/corpus_read.py src/geoassist/rag/lexical.py tests/test_versions.py tests/test_corpus_read.py tests/test_lexical.py
git commit -m "feat: publicación atómica y lectura del corpus"
```

---

### Task 11: Sincronización Git, lock, orquestador y contenedor sync

**Files:**
- Create: `src/geoassist/sync/__init__.py`, `src/geoassist/sync/git.py`, `src/geoassist/sync/lock.py`, `src/geoassist/sync/orchestrator.py`, `src/geoassist/sync/__main__.py`
- Create: `docker/sync/Dockerfile`
- Modify: `compose.yml` (servicio `sync`), `Makefile` (`sync`, `sync-status`, `activate`)
- Test: `tests/test_git_sync.py`, `tests/test_lock.py`, `tests/test_sync_orchestrator.py`

**Interfaces:**
- Produces: `update_repo(repo_dir: Path, repo_url: str, branch: str = "main") -> str`.
- Produces: `FileLock(path: Path)` context manager; `LockBusy`.
- Produces: `SyncReport(version_id, git_commit, doc_count, chunk_count, warnings, pdf_results, exit_code)`.
- Produces: `SyncOrchestrator(repo_dir, repo_url, corpus_dir, ingestor, renderer=None, clock=...).run(no_pdf=False) -> SyncReport`.
- CLI: `python -m geoassist.sync [--no-pdf]` y `python -m geoassist.sync activate <version_id>` (rollback validado).

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_git_sync.py
import subprocess
from pathlib import Path
from geoassist.sync.git import update_repo


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_clones_and_updates_to_remote_main(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(work)], check=True)
    (work / "a.md").write_text("# A\n")
    git("add", ".", cwd=work)
    git("-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-m", "a", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)

    checkout = tmp_path / "checkout"
    sha = update_repo(checkout, str(remote))
    assert sha == git("rev-parse", "HEAD", cwd=checkout)
    assert (checkout / "a.md").exists()

    (work / "a.md").write_text("# A actualizado\n")
    git("add", ".", cwd=work)
    git("-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-m", "b", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)
    assert update_repo(checkout, str(remote)) != sha
    assert "actualizado" in (checkout / "a.md").read_text()
```

```python
# tests/test_lock.py
import pytest
from geoassist.sync.lock import FileLock, LockBusy


def test_second_lock_fails_while_first_is_held(tmp_path):
    path = tmp_path / "sync.lock"
    with FileLock(path):
        with pytest.raises(LockBusy):
            with FileLock(path):
                pass
```

```python
# tests/test_sync_orchestrator.py
import hashlib
import json
from pathlib import Path
from geoassist.sync.orchestrator import SyncOrchestrator


class FakeIngestor:
    def build(self, version_id, output_dir):
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
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_git_sync.py tests/test_lock.py tests/test_sync_orchestrator.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`update_repo` clona con `--depth=1 --branch <branch>` si no existe `.git`; si existe, ejecuta `git fetch --depth=1 origin <branch>`, `git reset --hard origin/<branch>` y `git clean -fd`. Usa `GIT_TERMINAL_PROMPT=0`, timeout de 120 s y devuelve el SHA. Si `GIT_SSH_COMMAND` está definido en el entorno, lo respeta.

`FileLock` abre/crea el archivo y usa `fcntl.flock(LOCK_EX | LOCK_NB)`; si falla, lanza `LockBusy`.

`SyncOrchestrator` adquiere el lock, actualiza el repo, genera `version_id = UTC timestamp + "-" + sha[:7]`, construye en `corpus/build-<id>`, llama `publish_version`, publica con `write_current_atomic`, opcionalmente renderiza PDFs, escribe `sync_report.json` atómicamente y libera el lock. Códigos: 0 éxito, 1 fallo antes de publicar, 2 PDFs con fallos, 3 lock ocupado. El reporte incluye duraciones y warnings.

`docker/sync/Dockerfile` usa `python:3.12-slim` por digest e instala `git`, `openssh-client`, `pandoc`, `texlive-latex-base`, `texlive-latex-recommended`, `texlive-latex-extra`, `texlive-fonts-recommended`, `texlive-lang-spanish`, `lmodern`, `zstd`, `curl`. Copia el artefacto de modelos, instala con `uv`, usa usuario `10001` y `CMD ["python", "-m", "geoassist.sync"]`. El servicio `sync` de Compose usa perfil `tools`, monta `/manuals` y `/data`, y no publica puertos. `__main__.py` parsea `--no-pdf` y el subcomando `activate <version_id>`; este último usa `VersionManager.activate` y es la vía de rollback. `make activate VERSION=<id>` ejecuta ese subcomando dentro del contenedor `sync`. `make sync-status` imprime `/data/corpus/sync_report.json` desde el contenedor `sync`.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_git_sync.py tests/test_lock.py tests/test_sync_orchestrator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/sync docker/sync compose.yml Makefile tests/test_git_sync.py tests/test_lock.py tests/test_sync_orchestrator.py
git commit -m "feat: sincronización manual con lock y publicación atómica"
```

---

### Task 12: Sesiones, historial e idempotencia

**Files:**
- Create: `src/geoassist/models.py`, `src/geoassist/storage/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `ChatMessage(role, content, created_at)`.
- Produces: `SessionState(session_key, app_hint, history)`.
- Produces: `session_key(tenant_id: str | None, conversation_id: str, user_id: str) -> str`.
- Produces: `StateStore(db_path)` con `get_session`, `append_turn`, `get_cached_response`, `cache_response`, `purge_expired`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_state.py
from datetime import datetime, timedelta, timezone
from geoassist.storage.state import StateStore, session_key

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def test_session_is_isolated_by_user(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.append_turn(
        "k1",
        tenant_id="t",
        conversation_id="c",
        user_id="u1",
        question="q1",
        answer="a1",
        sources=[],
        app_hint=None,
        now=NOW,
    )
    store.append_turn(
        "k2",
        tenant_id="t",
        conversation_id="c",
        user_id="u2",
        question="q2",
        answer="a2",
        sources=[],
        app_hint=None,
        now=NOW,
    )
    assert [m.content for m in store.get_session("k1", NOW).history] == ["q1", "a1"]
    assert [m.content for m in store.get_session("k2", NOW).history] == ["q2", "a2"]


def test_history_keeps_last_six_turns(tmp_path):
    store = StateStore(tmp_path / "state.db")
    for i in range(8):
        store.append_turn(
            "k",
            tenant_id=None,
            conversation_id="c",
            user_id="u",
            question=f"q{i}",
            answer=f"a{i}",
            sources=[],
            app_hint=None,
            now=NOW + timedelta(minutes=i),
        )
    history = store.get_session("k", NOW + timedelta(minutes=8)).history
    assert len(history) == 12
    assert history[0].content == "q2"


def test_session_expires(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.append_turn(
        "k",
        tenant_id=None,
        conversation_id="c",
        user_id="u",
        question="q",
        answer="a",
        sources=[],
        app_hint=None,
        now=NOW,
    )
    assert store.get_session("k", NOW + timedelta(minutes=121)).history == ()


def test_activity_cache_expires(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.cache_response("act1", {"type": "message", "text": "hola"}, NOW)
    assert store.get_cached_response("act1", NOW + timedelta(hours=1)) == {
        "type": "message",
        "text": "hola",
    }
    assert store.get_cached_response("act1", NOW + timedelta(hours=25)) is None


def test_session_key_uses_default_tenant():
    assert session_key(None, "c", "u") == "default|c|u"
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_state.py -v`
Expected: FAIL por módulos inexistentes.

- [ ] **Step 3: Implementar**

`StateStore` abre SQLite con `PRAGMA journal_mode=WAL`, `foreign_keys=ON` y aplica migraciones de `migrations/state`. `append_turn` hace upsert de la sesión, inserta el mensaje del usuario y el de la asistente, actualiza `last_seen_at` y poda mensajes dejando los últimos 12. `get_session` devuelve historial vacío y `app_hint=None` si expiró o no existe. El caché de actividades devuelve `None` si superó las 24 h. Todas las fechas se serializan con `datetime.isoformat()` en UTC.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/models.py src/geoassist/storage/state.py tests/test_state.py
git commit -m "feat: sesiones, historial e idempotencia en SQLite"
```

---

### Task 13: Fusión RRF

**Files:**
- Create: `src/geoassist/rag/types.py`, `src/geoassist/rag/fusion.py`
- Test: `tests/test_fusion.py`

**Interfaces:**
- Produce: `VectorHit`, `LexicalHit` reexportados en `rag/types.py` para evitar imports circulares.
- Produce: `FusedHit(chunk_row_id, score, dense_rank, lexical_rank)`.
- Produce: `reciprocal_rank_fusion(dense: Sequence[VectorHit], lexical: Sequence[LexicalHit], k: int = 60) -> list[FusedHit]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_fusion.py
import pytest
from geoassist.rag.fusion import reciprocal_rank_fusion
from geoassist.rag.vector_store import VectorHit
from geoassist.storage.corpus_read import LexicalHit


def test_scores_sum_reciprocal_ranks_and_dedupe():
    dense = [VectorHit(1, 0.9), VectorHit(2, 0.8)]
    lexical = [LexicalHit(2, 5.0), LexicalHit(3, 4.0)]
    fused = reciprocal_rank_fusion(dense, lexical, k=60)
    by_id = {hit.chunk_row_id: hit for hit in fused}
    assert by_id[2].score == pytest.approx(1 / 62 + 1 / 61)
    assert by_id[1].score == pytest.approx(1 / 61)
    assert [hit.chunk_row_id for hit in fused] == [2, 1, 3]


def test_empty_lists_are_safe():
    assert reciprocal_rank_fusion([], [], k=60) == []
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_fusion.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

Recorre cada lista asignando `1 / (k + rank)` con rank 1-based, acumula por `chunk_row_id`, registra el primer rank denso y léxico, y ordena por score descendente con desempate por `chunk_row_id` ascendente para ser determinista.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_fusion.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/types.py src/geoassist/rag/fusion.py tests/test_fusion.py
git commit -m "feat: fusión RRF determinista"
```

---

### Task 14: Reranker y detección de aplicación

**Files:**
- Create: `src/geoassist/rag/rerank.py`, `src/geoassist/rag/apps.py`
- Test: `tests/test_rerank.py`, `tests/test_apps.py`

**Interfaces:**
- Produce: `sigmoid(x: np.ndarray) -> np.ndarray`.
- Produce: `Reranker` Protocol con `score(query: str, passages: Sequence[str]) -> list[float]`.
- Produce: `OnnxCrossEncoderReranker(model_dir=None, *, session=None, tokenizer=None, max_length=384, threads=4)`.
- Produce: `AppDetector(aliases: Mapping[str, str]).detect(question: str) -> str | None`.
- Produce: `build_app_detector(rules: CorpusRules) -> AppDetector`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_rerank.py
import numpy as np
from geoassist.rag.rerank import sigmoid


def test_sigmoid_is_stable_for_large_values():
    values = sigmoid(np.array([-1000.0, 0.0, 1000.0]))
    assert values.tolist() == [0.0, 0.5, 1.0]
```

```python
# tests/test_apps.py
from pathlib import Path
from geoassist.ingest.rules import CorpusRules
from geoassist.rag.apps import build_app_detector

RULES = CorpusRules.load(Path(__file__).parent / "fixtures" / "corpus_rules.yml")


def test_detects_canonical_names_and_aliases():
    detector = build_app_detector(RULES)
    assert detector.detect("Geopricer no carga") == "geopricer"
    assert detector.detect("el módulo geoprice falla") == "geopricer"
    assert detector.detect("Error en GEOPOS") == "geopos"


def test_generic_terms_do_not_filter():
    detector = build_app_detector(RULES)
    assert detector.detect("no abre la caja") is None
    assert detector.detect("error en backoffice") is None
    assert detector.detect("problema en POS") is None


def test_word_boundaries_are_respected():
    detector = build_app_detector(RULES)
    assert detector.detect("geopostal no es una app") is None
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_rerank.py tests/test_apps.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`sigmoid` usa una versión numéricamente estable (dos ramas según el signo). `OnnxCrossEncoderReranker` tokeniza pares `(query, passage)`, ejecuta el modelo, toma el logit y aplica sigmoid; devuelve un float por pasaje y procesa en lotes. `AppDetector` normaliza pregunta y alias (minúsculas, sin acentos), compila patrones con límites de palabra y devuelve la app canónica de la primera coincidencia; si no hay, `None`. `build_app_detector` arma el mapa desde `CorpusRules.aliases()`.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_rerank.py tests/test_apps.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/rerank.py src/geoassist/rag/apps.py tests/test_rerank.py tests/test_apps.py
git commit -m "feat: reranker y detección conservadora de aplicación"
```

### Task 15: Pipeline de recuperación

**Files:**
- Create: `src/geoassist/rag/retriever.py`
- Modify: `src/geoassist/rag/types.py`
- Test: `tests/test_retriever.py`

**Interfaces:**
- Produce: `RetrievedChunk(id, chunk_id, doc_id, heading, text, title, version, status, app, rel_path, score, dense_rank, lexical_rank, rerank_score)`.
- Produce: `RetrievalResult(chunks, sufficient, reason, trace)`.
- Produce: `Retriever(reader, store, embedder, reranker, *, dense_limit=20, lexical_limit=20, rerank_top_n=8, final_limit=5, max_per_doc=3, min_score=0.0)` con `retrieve(question, app_filter=None, statuses=("approved","pending"))`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_retriever.py
import pytest
from geoassist.rag.retriever import Retriever
from geoassist.rag.vector_store import VectorHit
from geoassist.storage.corpus_read import ChunkView, LexicalHit


class FakeEmbedder:
    dimension = 4

    def embed_query(self, text):
        return [1.0, 0.0, 0.0, 0.0]


class FakeStore:
    def search(self, vector, limit):
        return [VectorHit(1, 0.9), VectorHit(2, 0.8), VectorHit(3, 0.7), VectorHit(4, 0.6)]


class FakeReader:
    def lexical_search(self, query, limit, apps=None, statuses=None):
        return [LexicalHit(2, 5.0), LexicalHit(3, 4.0)]

    def get_chunks(self, ids):
        rows = {
            1: ChunkView(
                1, "d1:0", "d1", "H", "texto uno", "Doc uno", "1.0", "approved", "geopos", "a.md"
            ),
            2: ChunkView(
                2, "d2:0", "d2", "H", "texto dos", "Doc dos", "1.0", "pending", "geopos", "b.md"
            ),
            3: ChunkView(
                3, "d3:0", "d3", "H", "texto tres", "Doc tres", "1.0", "archived", "geopos", "c.md"
            ),
            4: ChunkView(
                4,
                "d4:0",
                "d4",
                "H",
                "texto cuatro",
                "Doc cuatro",
                "1.0",
                "approved",
                "geopricer",
                "d.md",
            ),
        }
        return [rows[i] for i in ids]


class FakeReranker:
    def score(self, query, passages):
        # Los pasajes llegan en orden de fusión [2, 1, 4]; el reranker los reordena a [1, 2, 4].
        return [0.2, 0.9, 0.1][: len(passages)]


def make_retriever(reranker=None, min_score=0.1):
    return Retriever(FakeReader(), FakeStore(), FakeEmbedder(), reranker, min_score=min_score)


def test_filters_archived_and_reranks_remaining():
    result = make_retriever(FakeReranker()).retrieve("consulta")
    assert [c.id for c in result.chunks] == [1, 2, 4]
    assert [c.status for c in result.chunks] == ["approved", "pending", "approved"]
    assert result.sufficient is True


def test_app_filter_is_forwarded_and_applied():
    result = make_retriever(FakeReranker()).retrieve("consulta", app_filter="geopricer")
    assert [c.app for c in result.chunks] == ["geopricer"]


def test_no_candidates_abstains_without_reranker():
    class EmptyStore(FakeStore):
        def search(self, vector, limit):
            return []

    class EmptyReader(FakeReader):
        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    result = Retriever(EmptyReader(), EmptyStore(), FakeEmbedder(), None).retrieve("nada")
    assert result.sufficient is False
    assert result.reason == "no_candidates"
    assert result.chunks == ()


def test_low_score_abstains():
    result = make_retriever(FakeReranker(), min_score=0.95).retrieve("consulta")
    assert result.sufficient is False
    assert result.reason == "low_score"


def test_max_three_chunks_per_document():
    class SameDocReader(FakeReader):
        def get_chunks(self, ids):
            return [
                ChunkView(
                    i, f"d1:{i}", "d1", "H", "texto", "Doc", "1.0", "approved", "geopos", "a.md"
                )
                for i in ids
            ]

    class FiveStore(FakeStore):
        def search(self, vector, limit):
            return [VectorHit(i, 1.0 - i / 10) for i in range(1, 6)]

    class NoLexical(FakeReader):
        def lexical_search(self, query, limit, apps=None, statuses=None):
            return []

    retriever = Retriever(
        NoLexical(), FiveStore(), FakeEmbedder(), None, max_per_doc=3, final_limit=5
    )
    result = retriever.retrieve("consulta")
    assert len(result.chunks) == 3
    assert all(chunk.doc_id == "d1" for chunk in result.chunks)
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_retriever.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`retrieve` valida y normaliza la pregunta; obtiene el vector; ejecuta denso y léxico en paralelo lógico; fusiona con RRF; carga los `ChunkView` en el orden fusionado; aplica filtro de `status` y `app`; agrupa por documento y conserva como máximo `max_per_doc`; rerankea los `rerank_top_n` mejores con `heading + "\n" + text`; ordena por score de rerank (o RRF si no hay reranker) y corta a `final_limit`. `sufficient` es falso si no hay chunks o si el mejor score queda por debajo de `min_score`; el motivo es `no_candidates` o `low_score`. La traza incluye límites, conteos por etapa, ranks y scores, sin texto completo de los documentos.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_retriever.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/retriever.py src/geoassist/rag/types.py tests/test_retriever.py
git commit -m "feat: recuperación híbrida con filtros y abstención"
```

---

### Task 16: Prompt, cliente LLM y fallback

**Files:**
- Create: `src/geoassist/rag/prompt.py`, `src/geoassist/llm/__init__.py`, `src/geoassist/llm/base.py`, `src/geoassist/llm/openai_compatible.py`
- Test: `tests/test_prompt.py`, `tests/test_llm_client.py`

**Interfaces:**
- Produce: `SYSTEM_PROMPT`, `build_messages(question, history, chunks) -> list[dict[str, str]]`, `format_sources(chunks) -> str`, `build_fallback(chunks, reason) -> str`.
- Produce: `LLMError(kind: str, message: str)`.
- Produce: `LLMClient` Protocol con `complete(messages, *, timeout_s, max_tokens=500, temperature=0.1) -> str`.
- Produce: `OpenAICompatibleClient(base_url, api_key, model, *, transport=None, sleeper=time.sleep)`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_prompt.py
from geoassist.models import ChatMessage
from geoassist.rag.prompt import build_fallback, build_messages, format_sources
from geoassist.rag.retriever import RetrievedChunk

CHUNK = RetrievedChunk(
    1,
    "d:0",
    "d",
    "Sección",
    "Texto útil",
    "Manual",
    "1.0",
    "pending",
    "geopos",
    "a.md",
    0.9,
    1,
    1,
    0.8,
)


def test_context_is_numbered_and_marks_pending():
    messages = build_messages("¿Cómo hago?", [ChatMessage("user", "antes", "t")], [CHUNK])
    assert messages[0]["role"] == "system"
    assert "no confiable" in messages[0]["content"].lower()
    user = messages[-1]["content"]
    assert "[1]" in user and "Manual" in user and "por aprobar" in user
    assert "¿Cómo hago?" in user


def test_history_keeps_roles_and_order():
    messages = build_messages(
        "ahora", [ChatMessage("user", "antes", "t"), ChatMessage("assistant", "ok", "t")], [CHUNK]
    )
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_sources_include_version_and_state():
    assert format_sources([CHUNK]) == "[1] Manual — v1.0 (por aprobar)"


def test_fallback_mentions_reason_and_top_chunk():
    text = build_fallback([CHUNK], "low_score")
    assert "no pude generar" in text.lower()
    assert "Manual" in text
```

```python
# tests/test_llm_client.py
import httpx
import pytest
from geoassist.llm.base import LLMError
from geoassist.llm.openai_compatible import OpenAICompatibleClient


def client(handler, sleeper=lambda seconds: None):
    return OpenAICompatibleClient(
        "https://llm.example/v1",
        "secret-key",
        "model-x",
        transport=httpx.MockTransport(handler),
        sleeper=sleeper,
    )


def test_success_returns_content_and_sends_auth_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "respuesta"}}]})

    result = client(handler).complete([{"role": "user", "content": "hola"}], timeout_s=2)
    assert result == "respuesta"
    assert seen["auth"] == "Bearer secret-key"
    assert "secret-key" not in "https://llm.example/v1"


def test_429_retries_once():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert client(handler).complete([], timeout_s=2) == "ok"
    assert calls["n"] == 2


def test_timeout_raises_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(LLMError) as error:
        client(handler).complete([], timeout_s=0.1)
    assert error.value.kind == "timeout"
    assert calls["n"] == 1


def test_500_after_retry_raises():
    def handler(request):
        return httpx.Response(500, json={"error": "server"})

    with pytest.raises(LLMError) as error:
        client(handler).complete([], timeout_s=1)
    assert error.value.kind == "http"
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_prompt.py tests/test_llm_client.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`SYSTEM_PROMPT` fija: soporte L2, español, usar solo el contexto, citar fuentes, decir cuando no alcanza y tratar el contenido entre `<contexto>` como datos no confiables. `build_messages` agrega historial acotado, luego un mensaje de usuario con contexto numerado y la pregunta; marca `pending` como “por aprobar”. `format_sources` produce como máximo 3 líneas `[n] Título — vX (estado)`. `build_fallback` devuelve una nota de respuesta incompleta más el mejor fragmento.

`OpenAICompatibleClient` usa `httpx.Client` con `base_url`, header `Authorization`, timeout total `timeout_s`, `temperature` y `max_tokens`. Reintenta una vez solo en `429` o `5xx` con 300 ms; los timeouts y errores de conexión no se reintentan. Convierte cualquier fallo en `LLMError` con `kind` en `timeout`, `http` o `invalid_response`, sin incluir la respuesta cruda.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_prompt.py tests/test_llm_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/prompt.py src/geoassist/llm tests/test_prompt.py tests/test_llm_client.py
git commit -m "feat: prompt con citas y cliente LLM compatible con OpenAI"
```

---

### Task 17: Adaptador de webhook saliente de Teams

**Files:**
- Create: `src/geoassist/teams/__init__.py`, `src/geoassist/teams/base.py`, `src/geoassist/teams/outgoing_webhook.py`
- Test: `tests/test_teams_adapter.py`

**Interfaces:**
- Produce: `InboundMessage(activity_id, text, user_id, conversation_id, tenant_id, service_url)`.
- Produce: `TeamsAuthError`, `InvalidPayload`.
- Produce: `TeamsAdapter` Protocol con `verify`, `parse`, `format_reply`.
- Produce: `OutgoingWebhookAdapter(secret: str, bot_name: str)`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_teams_adapter.py
import base64
import hashlib
import hmac
import json
import pytest
from geoassist.teams.outgoing_webhook import OutgoingWebhookAdapter, TeamsAuthError, InvalidPayload

SECRET = "teams-secret"
BODY = json.dumps(
    {
        "type": "message",
        "id": "act-1",
        "text": "<at>GeoAssist</at> ¿Cómo confirmo una remesa?",
        "from": {"id": "user-1"},
        "conversation": {"id": "conv-1"},
        "channelData": {"tenant": {"id": "tenant-1"}},
    }
).encode()


def signature(body=BODY, secret=SECRET):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return "HMAC " + base64.b64encode(digest).decode()


def test_valid_signature_is_accepted():
    OutgoingWebhookAdapter(SECRET, "GeoAssist").verify(BODY, {"Authorization": signature()})


def test_invalid_or_missing_signature_is_rejected():
    adapter = OutgoingWebhookAdapter(SECRET, "GeoAssist")
    with pytest.raises(TeamsAuthError):
        adapter.verify(BODY, {"Authorization": "HMAC bad"})
    with pytest.raises(TeamsAuthError):
        adapter.verify(BODY, {})


def test_parse_strips_mention_and_extracts_fields():
    message = OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(BODY)
    assert message.text == "¿Cómo confirmo una remesa?"
    assert (message.activity_id, message.user_id, message.conversation_id) == (
        "act-1",
        "user-1",
        "conv-1",
    )
    assert message.tenant_id == "tenant-1"


def test_non_message_activity_is_ignored():
    body = json.dumps({"type": "conversationUpdate", "id": "x"}).encode()
    assert OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body) is None


def test_missing_id_raises_invalid_payload():
    body = json.dumps({"type": "message", "text": "hola"}).encode()
    with pytest.raises(InvalidPayload):
        OutgoingWebhookAdapter(SECRET, "GeoAssist").parse(body)


def test_reply_format():
    reply = OutgoingWebhookAdapter(SECRET, "GeoAssist").format_reply("hola")
    assert reply == {"type": "message", "text": "hola"}
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_teams_adapter.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`verify` exige `Authorization` con prefijo `HMAC `, decodifica base64, calcula HMAC-SHA256 del cuerpo crudo y compara con `hmac.compare_digest`. Cualquier diferencia lanza `TeamsAuthError`. `parse` decodifica JSON, devuelve `None` si `type != "message"`, exige `id`, `from.id`, `conversation.id` y texto; elimina la mención al bot con un patrón que acepta `<at>...</at>` sin distinguir mayúsculas y recorta espacios. `tenant_id` y `service_url` son opcionales. `format_reply` devuelve el contrato de Teams.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_teams_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/teams tests/test_teams_adapter.py
git commit -m "feat: adaptador de webhook saliente de Teams"
```

---

### Task 18: Orquestación de chat, endpoints y presupuesto de latencia

**Files:**
- Create: `src/geoassist/services/__init__.py`, `src/geoassist/services/chat.py`, `src/geoassist/api/ratelimit.py`
- Create: `src/geoassist/api/routes/teams.py`, `src/geoassist/api/routes/debug.py`
- Modify: `src/geoassist/api/app.py`, `src/geoassist/main.py`, `src/geoassist/config.py` (validar debug token)
- Test: `tests/test_chat_service.py`, `tests/api/test_webhook.py`, `tests/api/test_debug.py`, `tests/api/test_ratelimit.py`

**Interfaces:**
- Produce: `ChatOutcome(text, degraded, sources, latency_ms)`.
- Produce: `ChatService(state, retriever, llm, detector, *, clock=time.monotonic, session_ttl_minutes=120)` con `handle(message: InboundMessage) -> ChatOutcome`.
- Produce: `Deadline(budget_ms, clock=time.monotonic)` con `remaining_ms()` y `expired()`.
- Produce: `RateLimiter(limit: int, window_seconds: int = 60, clock=...)` con `allow(key: str) -> bool`.
- Produce: `AppDependencies(settings, adapter, chat, retriever, versions, state)`.
- Produce: `create_app(settings=None, probe=None, dependencies=None) -> FastAPI`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_chat_service.py
from datetime import datetime, timezone
from geoassist.models import InboundMessage
from geoassist.rag.retriever import RetrievedChunk, RetrievalResult
from geoassist.services.chat import ChatService
from geoassist.storage.state import StateStore

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
CHUNK = RetrievedChunk(
    1, "d:0", "d", "H", "Texto útil", "Manual", "1.0", "approved", "geopos", "a.md", 0.9, 1, 1, 0.8
)
MESSAGE = InboundMessage("act-1", "¿Cómo hago?", "u1", "c1", "t1", None)


class FakeRetriever:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def retrieve(self, question, app_filter=None):
        self.calls += 1
        return self.result


class FakeLLM:
    def __init__(self, answer="respuesta", error=None):
        self.answer = answer
        self.error = error
        self.calls = 0
        self.messages = None

    def complete(self, messages, *, timeout_s, max_tokens=500, temperature=0.1):
        self.calls += 1
        self.messages = messages
        if self.error:
            raise self.error
        return self.answer


class FakeDetector:
    def detect(self, question):
        return None


def make_service(tmp_path, result, llm):
    return ChatService(
        StateStore(tmp_path / "state.db"), FakeRetriever(result), llm, FakeDetector()
    )


def test_abstention_does_not_call_llm(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((), False, "no_candidates", {})
    outcome = make_service(tmp_path, result, llm).handle(MESSAGE)
    assert llm.calls == 0
    assert "no encontr" in outcome.text.lower()
    assert outcome.degraded is False


def test_llm_timeout_returns_fallback(tmp_path):
    from geoassist.llm.base import LLMError

    llm = FakeLLM(error=LLMError("timeout", "boom"))
    result = RetrievalResult((CHUNK,), True, None, {})
    outcome = make_service(tmp_path, result, llm).handle(MESSAGE)
    assert outcome.degraded is True
    assert "Manual" in outcome.text


def test_second_turn_sends_history_to_llm(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    service = make_service(tmp_path, result, llm)
    service.handle(MESSAGE)
    service.handle(InboundMessage("act-2", "otra", "u1", "c1", "t1", None))
    roles = [m["role"] for m in llm.messages]
    assert roles[1:3] == ["user", "assistant"]
```

```python
# tests/api/test_ratelimit.py
from geoassist.api.ratelimit import RateLimiter


def test_seventh_request_in_window_is_rejected():
    now = {"t": 0.0}
    limiter = RateLimiter(limit=6, window_seconds=60, clock=lambda: now["t"])
    assert all(limiter.allow("u") for _ in range(6))
    assert limiter.allow("u") is False
    now["t"] = 61.0
    assert limiter.allow("u") is True


def test_limits_are_per_key():
    limiter = RateLimiter(limit=1, clock=lambda: 0.0)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False
```

```python
# tests/api/test_webhook.py
import base64, hashlib, hmac, json
from fastapi.testclient import TestClient
from geoassist.api.app import create_app
from geoassist.api.readiness import ReadinessProbe
from geoassist.storage.state import StateStore

SECRET = "secret"
BODY = json.dumps(
    {
        "type": "message",
        "id": "act-1",
        "text": "<at>Bot</at> hola",
        "from": {"id": "u"},
        "conversation": {"id": "c"},
    }
).encode()


def signed(body=BODY):
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    return {
        "Authorization": "HMAC " + base64.b64encode(digest).decode(),
        "Content-Type": "application/json",
    }


class FakeService:
    def __init__(self):
        self.calls = 0

    def handle(self, message):
        from geoassist.services.chat import ChatOutcome

        self.calls += 1
        return ChatOutcome("respuesta", False, (), 10)


def app_with(service, state=None):
    from geoassist.teams.outgoing_webhook import OutgoingWebhookAdapter
    from geoassist.api.app import AppDependencies
    from geoassist.config import Settings

    settings = Settings(
        _env_file=None,
        teams_security_token=SECRET,
        llm_api_key="k",
        llm_data_policy="external_approved",
    )
    deps = AppDependencies(
        settings, OutgoingWebhookAdapter(SECRET, "Bot"), service, None, None, state
    )
    return create_app(settings, probe=ReadinessProbe({}), dependencies=deps)


def test_valid_webhook_returns_teams_reply():
    service = FakeService()
    response = TestClient(app_with(service)).post("/teams/webhook", content=BODY, headers=signed())
    assert response.status_code == 200
    assert response.json() == {"type": "message", "text": "respuesta"}


def test_duplicate_activity_uses_cache(tmp_path):
    service = FakeService()
    client = TestClient(app_with(service, StateStore(tmp_path / "state.db")))
    assert client.post("/teams/webhook", content=BODY, headers=signed()).status_code == 200
    assert client.post("/teams/webhook", content=BODY, headers=signed()).status_code == 200
    assert service.calls == 1


def test_invalid_signature_returns_401():
    response = TestClient(app_with(FakeService())).post(
        "/teams/webhook", content=BODY, headers={"Authorization": "HMAC bad"}
    )
    assert response.status_code == 401


def test_oversized_body_returns_413():
    body = b"x" * 70000
    response = TestClient(app_with(FakeService())).post(
        "/teams/webhook", content=body, headers=signed(body)
    )
    assert response.status_code == 413
```

```python
# tests/api/test_debug.py
from fastapi.testclient import TestClient

from geoassist.api.app import AppDependencies, create_app
from geoassist.api.readiness import ReadinessProbe
from geoassist.config import Settings
from geoassist.rag.retriever import RetrievalResult
from geoassist.teams.outgoing_webhook import OutgoingWebhookAdapter


class FakeRetriever:
    def retrieve(self, question, app_filter=None):
        return RetrievalResult((), False, "no_candidates", {"dense": 0, "lexical": 0})


def make_app(enabled=True):
    settings = Settings(
        _env_file=None,
        teams_security_token="secret",
        llm_api_key="k",
        llm_data_policy="external_approved",
        debug_retrieval=enabled,
        debug_token="debug-token",
    )
    deps = AppDependencies(
        settings, OutgoingWebhookAdapter("secret", "Bot"), None, FakeRetriever(), None, None
    )
    return create_app(settings, probe=ReadinessProbe({}), dependencies=deps)


def test_debug_requires_token():
    response = TestClient(make_app()).post("/internal/retrieval", json={"question": "x"})
    assert response.status_code == 401


def test_debug_returns_trace_with_token():
    response = TestClient(make_app()).post(
        "/internal/retrieval",
        json={"question": "x"},
        headers={"X-Debug-Token": "debug-token"},
    )
    assert response.status_code == 200
    assert response.json()["trace"]["dense"] == 0
    assert response.json()["chunks"] == []


def test_debug_disabled_returns_404():
    response = TestClient(make_app(enabled=False)).post(
        "/internal/retrieval",
        json={"question": "x"},
        headers={"X-Debug-Token": "debug-token"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_chat_service.py tests/api/test_webhook.py tests/api/test_debug.py tests/api/test_ratelimit.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`ChatService.handle` resuelve la sesión, detecta app (pregunta o `app_hint`), llama al retriever, y si `sufficient` es falso responde abstención sin LLM. Si hay contexto, arma mensajes con historial, llama al LLM con el tiempo restante del `Deadline` (máximo 2500 ms), y ante `LLMError` usa `build_fallback`. Siempre guarda el turno con las fuentes y devuelve `ChatOutcome` con `degraded` y `latency_ms`. Los estados `pending` se citan como “por aprobar”.

`RateLimiter` mantiene ventanas deslizantes por clave en memoria; 7.ª llamada en 60 s devuelve `False` y el servicio responde con un mensaje de espera sin llamar al LLM.

La ruta `/teams/webhook` lee el cuerpo crudo, rechaza > 64 KB con 413, verifica HMAC (401), parsea (400 si inválido), consulta el caché de `activity.id` cuando `state` está configurado y devuelve la respuesta cacheada si existe; si no, aplica rate limit, llama al servicio, formatea con el adaptador, guarda en caché y responde 200. Una actividad duplicada no vuelve a ejecutar el pipeline.

`/internal/retrieval` existe solo si `DEBUG_RETRIEVAL=true`; exige `X-Debug-Token` igual a `DEBUG_TOKEN` (401 si falta o no coincide) y devuelve la traza y los chunks sin texto completo. `Settings` falla si `debug_retrieval` está activo y `debug_token` está vacío.

`/ready` comprueba `versions.current()`, `state` accesible, modelos calientes y configuración del LLM. `main.py` construye las dependencias reales, carga embedder y reranker, hace warmup y marca `models_warm`.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_chat_service.py tests/api/test_webhook.py tests/api/test_debug.py tests/api/test_ratelimit.py tests/api/test_health.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/services src/geoassist/api src/geoassist/main.py tests/test_chat_service.py tests/api
git commit -m "feat: chat, webhook de Teams, rate limit y presupuesto de latencia"
```

### Task 19: Generación de PDF y manifiesto

**Files:**
- Create: `src/geoassist/pdf/__init__.py`, `src/geoassist/pdf/renderer.py`, `src/geoassist/pdf/images.py`
- Create: `src/geoassist/pdf/templates/plantilla_manuales.tex`
- Modify: `src/geoassist/sync/orchestrator.py`, `docker/sync/Dockerfile`, `pyproject.toml` (extra `sync` con `pypandoc`)
- Test: `tests/test_pdf.py`

**Interfaces:**
- Produce: `PdfResult(rel_path, pdf_path, status, error)` con `status` en `generated`, `skipped`, `failed`.
- Produce: `output_path(output_dir: Path, rel_path: str) -> Path`.
- Produce: `render_key(rel_path, md_bytes, template_bytes, images: Mapping[str, bytes]) -> str`.
- Produce: `prepare_markdown(markdown: str, assets_dir: Path) -> str`.
- Produce: `PdfRenderer(repo_root, manuals_root_rel, assets_root_rel, template_path, output_dir, manifest_path)` con `render_all(only_changed=True) -> list[PdfResult]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_pdf.py
from pathlib import Path
import pytest
from geoassist.pdf.renderer import PdfRenderer, output_path, render_key
from geoassist.pdf.images import prepare_markdown


def test_output_path_mirrors_and_replaces_extension(tmp_path):
    rel = "data_source/manuales/OXXO/GEOPOS - OK/x y (1).md"
    assert (
        output_path(tmp_path, rel) == tmp_path / "data_source/manuales/OXXO/GEOPOS - OK/x y (1).pdf"
    )


def test_render_key_changes_with_content_and_template(tmp_path):
    base = render_key("a.md", b"md", b"tpl", {"img.png": b"img"})
    assert base != render_key("a.md", b"md2", b"tpl", {"img.png": b"img"})
    assert base != render_key("a.md", b"md", b"tpl2", {"img.png": b"img"})
    assert base != render_key("a.md", b"md", b"tpl", {"img.png": b"img2"})


def test_missing_image_is_replaced_with_alt_text(tmp_path):
    markdown = "![Pantallazo del error](no-existe.png)"
    prepared = prepare_markdown(markdown, tmp_path)
    assert "Pantallazo del error" in prepared
    assert "no-existe.png" not in prepared


def test_existing_image_is_resolved_to_assets(tmp_path):
    (tmp_path / "ok.png").write_bytes(b"png")
    prepared = prepare_markdown("![ok](ok.png)", tmp_path)
    assert str(tmp_path / "ok.png") in prepared


def test_manifest_skips_unchanged_and_regenerates_changed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    md = repo / "data_source" / "manuales" / "a.md"
    md.parent.mkdir(parents=True)
    md.write_text("# A\n\ncontenido\n")
    template = tmp_path / "template.tex"
    template.write_text("template")
    renderer = PdfRenderer(
        repo,
        "data_source/manuales",
        "data_source/assets",
        template,
        tmp_path / "pdfs",
        tmp_path / "pdf_manifest.json",
    )
    calls = []

    def fake_convert(rel_path, markdown, output, meta):
        calls.append(rel_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(renderer, "_convert", fake_convert)
    first = renderer.render_all()
    second = renderer.render_all()
    assert [r.status for r in first] == ["generated"]
    assert [r.status for r in second] == ["skipped"]
    md.write_text("# A\n\ncontenido cambiado\n")
    third = renderer.render_all()
    assert [r.status for r in third] == ["generated"]
    assert len(calls) == 2


def test_failure_does_not_stop_the_batch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    root = repo / "data_source" / "manuales"
    root.mkdir(parents=True)
    (root / "a.md").write_text("# A\n")
    (root / "b.md").write_text("# B\n")
    template = tmp_path / "template.tex"
    template.write_text("template")
    renderer = PdfRenderer(
        repo,
        "data_source/manuales",
        "data_source/assets",
        template,
        tmp_path / "pdfs",
        tmp_path / "manifest.json",
    )

    def fake_convert(rel_path, markdown, output, meta):
        if rel_path.endswith("a.md"):
            raise RuntimeError("pandoc falló")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(renderer, "_convert", fake_convert)
    results = renderer.render_all()
    assert {r.rel_path: r.status for r in results} == {
        "data_source/manuales/a.md": "failed",
        "data_source/manuales/b.md": "generated",
    }
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_pdf.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

`output_path` toma `rel_path`, cambia `.md` por `.pdf` y lo une a `output_dir`. `render_key` concatena hashes de ruta, Markdown, plantilla e imágenes referenciadas. `prepare_markdown` busca cada `![alt](src)` por nombre base en `assets_dir`; si existe, reescribe la ruta absoluta; si no, lo reemplaza por el texto alternativo.

`PdfRenderer.render_all` descubre todos los `.md` bajo `manuals_root_rel` (incluye archivados, excluye la plantilla), calcula la clave, compara con `pdf_manifest.json`, y para los cambios llama `_convert`. `_convert` parsea frontmatter, prepara Markdown, ejecuta `pypandoc.convert_text` con `--template`, `--resource-path`, `--pdf-engine=pdflatex`, `--pdf-engine-opt=-no-shell-escape`, `logo`, `version` y `date`, y escribe en un temporal antes de mover al destino. Un error por archivo se captura y se registra en el manifiesto; el lote continúa. El manifiesto se escribe atómicamente.

La plantilla se copia de `/home/secragx/Documentos/Geocom_Software/GEO_RAG_CHATBOT/plantilla_manuales.tex` y se le agregan `\usepackage[T1]{fontenc}` y `\usepackage{lmodern}`; la resolución del logo acepta `Geocom.png` o `Geocom.jpg`.

`SyncOrchestrator` publica el corpus antes de renderizar; si hay PDFs fallidos, el reporte final tiene código 2 pero la versión nueva queda activa.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_pdf.py -v`
Expected: PASS. La conversión real se prueba manualmente dentro de la imagen `sync`, que es la única con Pandoc y TeX Live.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/pdf tests/test_pdf.py src/geoassist/sync/orchestrator.py docker/sync/Dockerfile pyproject.toml uv.lock
git commit -m "feat: generación de PDF con manifiesto y aislamiento de fallos"
```

---

### Task 20: Evaluación, CI y runbook

**Files:**
- Create: `src/geoassist/eval/__init__.py`, `src/geoassist/eval/metrics.py`
- Create: `eval/evaluate.py`, `eval/golden.jsonl`, `eval/baseline.json`
- Create: `.github/workflows/ci.yml`, `docs/runbook.md`
- Modify: `README.md`, `Makefile` (`eval`, `lint`)
- Test: `tests/test_eval_metrics.py`, `tests/test_golden.py`

**Interfaces:**
- Produce: `GoldenCase(id, question, app, expected_docs, expected_facts, must_abstain, notes)`.
- Produce: `EvalMetrics(recall_at_5, mrr_at_10, ndcg_at_10, app_accuracy, abstention_accuracy)`.
- Produce: `recall_at_k(ranked_docs, expected_docs, k)`, `mean_reciprocal_rank(ranked_docs, expected_docs, k=10)`, `ndcg_at_k(ranked_docs, expected_docs, k=10)`, `evaluate(golden, retriever) -> EvalMetrics`.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
# tests/test_eval_metrics.py
import pytest
from geoassist.eval.metrics import mean_reciprocal_rank, ndcg_at_k, recall_at_k


def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], ["b"], 5) == 1.0
    assert recall_at_k(["a", "b", "c"], ["z"], 5) == 0.0


def test_mrr():
    assert mean_reciprocal_rank(["a", "b", "c"], ["b"], 10) == pytest.approx(0.5)
    assert mean_reciprocal_rank(["a"], ["z"], 10) == 0.0


def test_ndcg_rewards_early_hits():
    early = ndcg_at_k(["a", "b"], ["a"], 10)
    late = ndcg_at_k(["b", "a"], ["a"], 10)
    assert early == 1.0
    assert late < early
```

```python
# tests/test_golden.py
import json
from pathlib import Path
import pytest

GOLDEN = Path(__file__).parents[1] / "eval" / "golden.jsonl"


def test_golden_set_has_ten_to_fifteen_labeled_cases():
    cases = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    assert 10 <= len(cases) <= 15
    assert all(case["question"] and case["expected_docs"] for case in cases)
    assert all(case["must_abstain"] in (True, False) for case in cases)


def test_no_case_expects_an_archived_manual():
    cases = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    for case in cases:
        assert all("VERSIONES ANTERIORES" not in doc for doc in case["expected_docs"])
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `pytest tests/test_eval_metrics.py tests/test_golden.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementar**

Las métricas son binarias sobre documentos: `recall_at_k` es la proporción de documentos esperados presentes en el top-k; MRR usa el primer acierto; nDCG usa ganancia 1 por documento esperado. `evaluate` recorre el set dorado, llama `retriever.retrieve(question, app_filter=case.app)`, compara `chunk.rel_path` deduplicado y calcula abstención correcta cuando `must_abstain` coincide con `not result.sufficient`.

`eval/golden.jsonl` se escribe leyendo los manuales del repositorio `manuales_soporte_L2`: 10–15 casos con `id`, `question`, `app`, `expected_docs` (rutas `rel_path` reales), `expected_facts`, `must_abstain` y `notes`. Debe incluir al menos un caso de abstención y uno por aplicación. El operador los valida en M4.

`eval/evaluate.py` imprime las métricas en JSON y exit 1 si `recall_at_5` cae más de 2 puntos respecto de `eval/baseline.json` o si `abstention_accuracy < 1.0`. `eval/baseline.json` arranca en `{"recall_at_5": 0.0, "abstention_accuracy": 1.0}` y se actualiza solo con una corrida revisada.

`.github/workflows/ci.yml` ejecuta: `ruff check`, `ruff format --check`, `mypy src`, `pytest`, `gitleaks`, `docker compose config`, y build de `api`/`sync` cuando los secretos `MODELS_ARTIFACT_URL` y `MODELS_ARTIFACT_SHA256` están definidos; si faltan, el job de build se omite con un warning explícito.

`docs/runbook.md` documenta: requisitos del host, primer `make sync`, registro del webhook en Teams, rotación de secretos, política `LLM_DATA_POLICY`, rollback con `make activate`, lectura de `sync_report.json`, diagnóstico de `/health` y `/ready`, y qué hacer ante PDFs fallidos. `README.md` enlaza el diseño, el plan y el runbook.

- [ ] **Step 4: Ejecutar y verificar que pasan**

Run: `pytest tests/test_eval_metrics.py tests/test_golden.py -v`
Expected: PASS.

Run: `ruff check . && ruff format --check . && mypy src`
Expected: sin errores.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/eval eval .github/workflows/ci.yml docs/runbook.md README.md Makefile tests/test_eval_metrics.py tests/test_golden.py
git commit -m "feat: evaluación, gates de CI y runbook operativo"
```

---

## Self-Review Notes

- Cobertura del spec: ingesta (tareas 4–9), almacenamiento y versiones (1, 9, 10, 12), recuperación (13–15), generación (16), Teams (17–18), sync (11), PDF (19), operación y calidad (3, 20).
- Cada Review Focus tiene prueba en la tarea indicada; no quedan inputs críticos sin cobertura automatizada salvo la conversión real de PDF, que se valida manualmente en la imagen `sync`.
- Las interfaces se repiten solo donde una tarea necesita el contrato de otra; los nombres son consistentes entre tareas.
- `RERANK_MIN_SCORE` queda en 0.0 hasta la calibración de M4; el plan no inventa un umbral.