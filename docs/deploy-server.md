# Montaje de GeoAssist en el servidor de pruebas

Guía para levantar GeoAssist en un servidor limpio a partir de los repositorios
de GitHub, con el mismo comportamiento de la versión de desarrollo y con el
túnel ngrok persistente bajo Docker Compose.

## Requisitos del servidor

- Docker Engine ≥ 24 y Docker Compose ≥ 2.24 (`docker compose version`).
- `git`.
- Salida a Internet: DeepSeek (`api.deepseek.com`), GitHub (clon y descarga del
  artefacto de modelos) y ngrok (túnel saliente).
- ~6 GB de RAM libres (API 3 GB + sync con TeX Live en picos) y ~8 GB de disco
  (imágenes + corpus + PDFs).
- No se necesitan puertos abiertos entrantes: ngrok hace el túnel saliente.

## 1. Clonar los repositorios

Los dos repositorios deben quedar **como hermanos**: el Compose monta
`../manuales_soporte_L2` dentro del contenedor `sync`.

```bash
sudo mkdir -p /opt/geocom
sudo chown "$USER":"$USER" /opt/geocom
cd /opt/geocom

git clone https://github.com/Secragx/geoassist.git
git clone https://github.com/Secragx/manuales_soporte_L2.git

# Estructura resultante:
# /opt/geocom/
# ├── geoassist/
# └── manuales_soporte_L2/
```

## 2. Crear `.env`

```bash
cd /opt/geocom/geoassist
cp .env.example .env
chmod 600 .env
```

Completar como mínimo:

| Variable | Valor |
|---|---|
| `APP_ENV` | `development` (para prueba con Teams sin HMAC) o `production` |
| `LLM_API_KEY` | API key de DeepSeek |
| `LLM_DATA_POLICY` | `external_approved` |
| `LLM_TIMEOUT_MS` | `5000` |
| `TOTAL_BUDGET_MS` | `8000` |
| `QUERY_REWRITE_ENABLED` | `true` |
| `MODELS_ARTIFACT_URL` | URL del release: `https://github.com/Secragx/geoassist/releases/download/models-v1.0.0/geoassist-models-1.0.0.tar.zst` |
| `MODELS_ARTIFACT_SHA256` | SHA-256 publicado junto al release |
| `SYNC_REPO_URL` | `/manuals_src` (manuales locales montados) |
| `NGROK_AUTHTOKEN` | token de la cuenta ngrok |
| `NGROK_DOMAIN` | dominio estático (ej. `unaudaciously-founderous-marielle.ngrok-free.dev`) |
| `TEAMS_SECURITY_TOKEN` | vacío en pruebas; token del webhook de Teams en producción |

Nunca se versiona `.env`; contiene secretos y está en `.gitignore`.

## 3. Construir las imágenes

El artefacto de modelos se descarga y verifica (SHA-256) durante el build:

```bash
cd /opt/geocom/geoassist
docker compose --profile tools build api sync
```

Equivalente con Make (solo la API):

```bash
make build
```

## 4. Primera sincronización del corpus

```bash
make sync        # clona los manuales, indexa y genera los PDFs
make sync-status # revisa doc_count / chunk_count / failed_pdfs
```

Los manuales se clonan desde `../manuales_soporte_L2` (montado en
`/manuals_src`), sin salir a GitHub. La publicación es atómica: se conservan
las últimas 3 versiones en `/data/corpus/versions`.

## 5. Levantar API + ngrok persistente

```bash
make up-dev
```

Equivale a `docker compose --profile dev up -d api ngrok`:

- `api` queda publicada **solo en** `127.0.0.1:9000` del servidor.
- `ngrok` abre el túnel hacia `api:8000` dentro de la red de Compose con el
  dominio estático configurado.
- Ambos servicios tienen `restart: unless-stopped`: Docker los relanza si se
  reinician o si el servidor arranca con Docker habilitado:

```bash
sudo systemctl enable --now docker
```

Tras un reinicio del servidor basta con que Docker arranque; si por algún
motivo quedan detenidos:

```bash
cd /opt/geocom/geoassist && make up-dev
```

## 6. Verificación

```bash
curl http://127.0.0.1:9000/health   # {"status":"ok"}
curl http://127.0.0.1:9000/ready    # corpus/state/models/llm en true
```

URL pública del webhook (dominio ngrok):

```text
https://<NGROK_DOMAIN>/webhook        # alias usado por el webhook registrado
https://<NGROK_DOMAIN>/teams/webhook  # ruta canónica
```

Probar una pregunta en el canal de Teams y revisar `make logs`.

## Operación

```bash
make logs          # logs de la API
make db            # consola SQLite sobre /data/state.db
make eval          # evaluación golden set
make activate VERSION=<id>   # rollback del corpus
docker compose --profile dev logs -f ngrok   # estado del túnel
```

**Actualizar la versión:** `git pull`, `docker compose --profile tools build
api sync`, `make up-dev`, `make sync` (si cambiaron los manuales, `git pull`
también en `../manuales_soporte_L2`).

**Rollback del corpus:** `make sync-status` lista la versión activa;
`make activate VERSION=<id>` vuelve a una anterior sin reconstruir imágenes.

## Notas de producción

- `APP_ENV=production` es **fail-closed**: exige `TEAMS_SECURITY_TOKEN`,
  `LLM_API_KEY` y `LLM_DATA_POLICY`, y valida HMAC en cada mensaje. Para pasar
  a producción, recrear el outgoing webhook de Teams con la URL pública, copiar
  el token que Teams muestra y reiniciar.
- El perfil `dev` con token vacío **no valida firmas**: úsese solo en pruebas.
- Rotar `LLM_API_KEY` y `NGROK_AUTHTOKEN` si alguna vez se expusieron.
