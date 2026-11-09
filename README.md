# GeoAssist

Asistente RAG de soporte L2 para Microsoft Teams. Indexa manuales en Markdown,
los recupera con búsqueda híbrida (densa + léxica) y genera respuestas con
citas a las fuentes. Todo corre en contenedores Docker; los modelos ONNX se
empaquetan como artefacto versionado y nunca se descargan en runtime.

- Diseño: [docs/superpowers/specs/2026-11-06-geoassist-rag-design.md](docs/superpowers/specs/2026-11-06-geoassist-rag-design.md)
- Plan: [docs/superpowers/plans/2026-11-06-geoassist-rag.md](docs/superpowers/plans/2026-11-06-geoassist-rag.md)
- Runbook: [docs/runbook.md](docs/runbook.md)

## Arquitectura

```
Manuales (Markdown) ──► sync container ──► corpus SQLite + FTS5 + índice FAISS
                                                │
Teams webhook ──► API FastAPI ──► ChatService ──► RetrievalPipeline (RRF + rerank)
                                                │
                                                └──► LLM (OpenAI-compatible) ──► respuesta con citas
```

Componentes:
- `api`: servicio FastAPI (puerto 8000, sin puertos públicos; expuesto por Caddy o ngrok).
- `sync`: trabajo batch que sincroniza manuales, genera PDFs y publica una versión del corpus.
- `caddy`: perfil `prod`, publica 80/443 con TLS automático.
- `ngrok`: perfil `dev`, túnel de desarrollo.
- Volúmenes: `geoassist_data` (estado y corpus), `geoassist_manuals` (manuales).

## Requisitos previos

- Docker y Docker Compose ≥ 2.24.
- Python 3.12 + `uv` para desarrollo local.
- `git`.
- `pandoc` + TeX Live **solo** dentro del contenedor `sync` (no en el host).
- Artefacto de modelos `geoassist-models-<versión>.tar.zst` y su SHA-256.

## Inicio rápido

```bash
# 1. Configurar variables
cp .env.example .env
# Completar TEAMS_SECURITY_TOKEN, LLM_API_KEY, LLM_DATA_POLICY
# y MODELS_ARTIFACT_URL / MODELS_ARTIFACT_SHA256

# 2. Construir la imagen de la API con los modelos verificados
make build

# 3. Sincronizar manuales y construir la primera versión del corpus
make sync

# 4. Levantar la API
make up

# 5. Verificar
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

## Configuración (`.env`)

Copiar `.env.example` a `.env`. En `APP_ENV=production` la aplicación es
**fail-closed**: no arranca sin `TEAMS_SECURITY_TOKEN`, `LLM_API_KEY`,
`LLM_DATA_POLICY` y las rutas de modelos.

| Variable | Default | Obligatoria | Descripción |
|---|---|---|---|
| `APP_ENV` | `development` | Sí | `development` o `production` |
| `TEAMS_SECURITY_TOKEN` | — | Sí en prod | Secreto HMAC del webhook saliente |
| `TEAMS_BOT_NAME` | `GeoAssist` | No | Nombre del bot en las respuestas |
| `LLM_BASE_URL` | `https://api.deepseek.com` | Sí | Endpoint compatible con OpenAI |
| `LLM_API_KEY` | — | Sí en prod | Clave del LLM |
| `LLM_MODEL` | `deepseek-chat` | Sí | Modelo de chat |
| `LLM_TIMEOUT_MS` | `5000` | No | Timeout de generación (ms) |
| `LLM_DATA_POLICY` | — | Sí en prod | `external_approved` o `on_prem_only` |
| `LLM_ALLOWED_HOSTS` | — | Si `on_prem_only` | Hosts permitidos (coma o JSON) |
| `EMBEDDING_MODEL_PATH` | `/app/models/bge-m3-int8` | Sí | Ruta del modelo de embeddings |
| `RERANK_MODEL_PATH` | `/app/models/mmarco-mMiniLMv2-L12-H384-v1-int8` | Sí | Ruta del reranker |
| `RERANK_MIN_SCORE` | `0.0` | No | Umbral mínimo de rerank |
| `MODELS_ARTIFACT_URL` | — | Build | URL del artefacto de modelos |
| `MODELS_ARTIFACT_SHA256` | — | Build | SHA-256 del artefacto (64 hex) |
| `DATA_DIR` | `/data` | No | Volumen de estado y corpus |
| `MANUALS_DIR` | `/manuals` | No | Volumen de manuales |
| `MANUALS_ROOT_REL` | `data_source/manuales` | No | Subruta de manuales dentro del repo |
| `ASSETS_ROOT_REL` | `data_source/assets` | No | Subruta de imágenes |
| `SESSION_TTL_MINUTES` | `120` | No | TTL de sesión en minutos |
| `RATE_LIMIT_PER_MINUTE` | `6` | No | Límite de mensajes por usuario |
| `QUERY_REWRITE_ENABLED` | `false` | No | Reformula la consulta con el LLM antes de buscar |
| `QUERY_REWRITE_CACHE` | `128` | No | Entradas de cache del reescritor |
| `QUERY_REWRITE_TIMEOUT_MS` | `1200` | No | Timeout del reescritor (fallback automático) |
| `TOTAL_BUDGET_MS` | `8000` | No | Presupuesto total del pipeline (búsqueda 20 %, generación 65 %, estado 10 %) |
| `DEBUG_RETRIEVAL` | `false` | No | Activa `/internal/retrieval` |
| `DEBUG_TOKEN` | — | Si debug | Token del endpoint interno |
| `LOG_QUESTION_TEXT` | `false` | No | Loguear texto de preguntas |
| `LOG_LEVEL` | `INFO` | No | Nivel de log |
| `SYNC_REPO_URL` | repo de manuales | Sync | Repositorio Git de manuales |
| `SYNC_GIT_TOKEN` | — | Sync privado | Token de acceso Git |
| `GEOASSIST_DOMAIN` | `localhost` | Prod | Dominio servido por Caddy |
| `NGROK_AUTHTOKEN` | — | Dev | Token de ngrok |
| `NGROK_DOMAIN` | — | Dev estable | Dominio estático de ngrok |
| `COMPOSE_PROFILES` | vacío | No | `prod`, `dev` o vacío |

`Settings` ignora claves desconocidas del `.env`, así que copiar la plantilla
completa no rompe el arranque local.

## Modelos ONNX

Los modelos nunca se descargan en runtime. Se empaquetan como
`geoassist-models-<versión>.tar.zst` con SHA-256 y se verifican en el build:

```bash
# Empaquetar (y opcionalmente exportar desde HuggingFace)
make publish-models VERSION=1.0.0
make publish-models VERSION=1.0.0 EXPORT=1

# Construir la imagen con el artefacto
docker build \
  --build-arg MODELS_ARTIFACT_URL=https://.../geoassist-models-1.0.0.tar.zst \
  --build-arg MODELS_ARTIFACT_SHA256=<64 hex> \
  -t geoassist-api .
```

## Manuales y sincronización

Estructura esperada del repositorio de manuales:

```
data_source/
├── manuales/          # solo .md se indexan
│   ├── OXXO/
│   │   └── VERSIONES APROBADAS/
│   └── ...
└── assets/            # imágenes referenciadas
```

Reglas en `config/corpus_rules.yml`: se indexan solo `.md`, se excluye
`model_manuales.md`, y los estados válidos son `approved`, `pending`,
`archived`.

```bash
# Sincronizar y publicar la primera versión del corpus
make sync

# Ver el último reporte
make sync-status

# Activar (rollback a) una versión publicada
make activate VERSION=<version_id>
```

La publicación es atómica: cada sync crea una versión inmutable en
`/data/corpus/versions`, mantiene las últimas 3 y actualiza `current.json`
con `fsync`. Los PDFs se generan **solo** en el contenedor `sync` y nunca se
exponen por HTTP.

## Exposición pública (producción vs desarrollo)

### Producción (recomendado): Caddy

```bash
# .env
GEOASSIST_DOMAIN=bot.tudominio.com
COMPOSE_PROFILES=prod

docker compose up -d
```

Requisitos: DNS del dominio apuntando al servidor y puertos 80/443 abiertos.
Caddy obtiene el certificado TLS automáticamente.

### Desarrollo con URL estable: ngrok con dominio estático

ngrok ofrece un dominio estático gratuito con cuenta verificada. Configúralo
en el dashboard de ngrok y en `.env`:

```env
NGROK_AUTHTOKEN=<tu-token>
NGROK_DOMAIN=mi-bot.ngrok-free.app
COMPOSE_PROFILES=dev
```

Y asegúrate de que el comando del servicio `ngrok` pase el dominio:

```yaml
command: ["http", "api:8000", "--domain=${NGROK_DOMAIN}", "--log=stdout"]
```

### Desarrollo rápido: ngrok sin dominio

Sin `NGROK_DOMAIN` la URL cambia en cada arranque, por lo que **no sirve**
para un webhook de Teams fijo; úsalo solo para pruebas puntuales.

### Registro del webhook en Teams

1. Configurar `TEAMS_SECURITY_TOKEN` y `TEAMS_BOT_NAME`.
2. Exponer la API (Caddy en prod o ngrok estático en dev).
3. Registrar la URL pública como outgoing webhook en Teams:
   `https://<dominio>/teams/webhook`
4. Probar con una pregunta y verificar `/health` y `/ready`.

## Ejecución local sin Docker

```bash
uv sync
uv run uvicorn geoassist.main:app --reload
```

Limitaciones: requiere corpus publicado en `/data/corpus` y modelos ONNX en las
rutas configuradas.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/teams/webhook` | Webhook saliente de Teams (HMAC, idempotencia, rate limit, 4.5 s de presupuesto) |
| `GET` | `/health` | Liveness del proceso |
| `GET` | `/ready` | Corpus activo, estado, modelos y LLM |
| `POST` | `/internal/retrieval` | Solo con `DEBUG_RETRIEVAL=true` y `X-Debug-Token` |

## Operación y diagnóstico

```bash
make logs          # logs de la API
make shell         # shell en el contenedor
make db            # consola SQLite sobre state.db
make eval          # ejecutar la suite de evaluación
```

- `make sync-status`: muestra `sync_report.json` (`doc_count`, `chunk_count`,
  `failed_pdfs`, `status`).
- Código de salida 2 en sync: PDFs fallidos, pero la versión queda activa.
- Rollback: `make activate VERSION=<id>` y verificar `/health` y `/ready`.
- Rotación de secretos: actualizar `.env`, reiniciar y revocar credenciales
  antiguas tras verificar `/ready`.

## Pruebas y calidad

```bash
make test    # pytest
make lint    # ruff format --check, ruff check, mypy
make check   # lint + tests
```

- 171 tests.
- CI en `.github/workflows/ci.yml`: ruff, mypy, pytest, gitleaks, validación
  de Compose y build condicional a secretos.

## Solución de problemas

- **La API no arranca en producción:** faltan `TEAMS_SECURITY_TOKEN`,
  `LLM_API_KEY`, `LLM_DATA_POLICY` o rutas de modelos (fail-closed).
- **`/ready` falla por corpus:** ejecutar `make sync` y verificar
  `make sync-status`.
- **`/ready` falla por modelos:** revisar `MODELS_ARTIFACT_URL` y
  `MODELS_ARTIFACT_SHA256` en el build.
- **401 en el webhook:** el header `Authorization: HMAC <base64>` no coincide
  con `TEAMS_SECURITY_TOKEN`.
- **429 en el webhook:** se superó `RATE_LIMIT_PER_MINUTE` por usuario.
- **PDFs fallidos en sync:** revisar `sync_report.json`, corregir el Markdown
  o la plantilla y re-ejecutar `make sync`; si persiste, activar la última
  versión estable.
- **ngrok con URL aleatoria:** configurar `NGROK_DOMAIN` o usar Caddy en prod.

## Documentación adicional

- Diseño: [docs/superpowers/specs/2026-11-06-geoassist-rag-design.md](docs/superpowers/specs/2026-11-06-geoassist-rag-design.md)
- Plan: [docs/superpowers/plans/2026-11-06-geoassist-rag.md](docs/superpowers/plans/2026-11-06-geoassist-rag.md)
- Runbook: [docs/runbook.md](docs/runbook.md)
