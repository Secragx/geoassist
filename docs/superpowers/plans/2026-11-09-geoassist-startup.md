# GeoAssist — Plan de arranque local (2026-11-09)

> Plan operativo para levantar GeoAssist en esta PC e integrarlo con el
> outgoing webhook de Teams ya registrado (dominio ngrok estático reutilizado
> del proyecto GEO_RAG_CHATBOT).

**Goal:** API de GeoAssist corriendo en contenedores, corpus indexado desde los
manuales locales, expuesta por el ngrok existente (puerto 9000) y respondiendo
en Teams — con validación HMAC deshabilitada solo para esta prueba
(`APP_ENV=development`, token vacío).

**Decisiones del usuario:**
- Artefacto de modelos servido por HTTP local temporal (no GitHub release).
- Manuales desde el directorio local `../manuales_soporte_L2` (sin GitHub).
- Alcance: ya con Teams, reutilizando el ngrok del host
  (`ngrok http --domain=unaudaciously-founderous-marielle.ngrok-free.dev --host-header=rewrite 9000`).
- Prueba sin `TEAMS_SECURITY_TOKEN` (bypass HMAC solo en desarrollo).

## Global Constraints

- Modelos ONNX ya exportados en `/home/secragx/Documentos/Geocom_Software/GEO_RAG_CHATBOT/models`.
- El bypass HMAC solo aplica con `APP_ENV=development` y token vacío; producción sigue fail-closed.
- Alias `/webhook` para no recrear el webhook de Teams registrado.
- API publicada solo en `127.0.0.1:9000` (ngrok es la entrada pública).
- Nada se pushea a remotos; secretos solo en `.env` (git-ignored).

---

### Task 1: Empaquetar el artefacto de modelos

**Files:** `dist/geoassist-models-1.0.0.tar.zst` + `.sha256` (nuevos, git-ignored)

- [ ] Step 1: `scripts/publish_models.py --version 1.0.0 --output-dir dist --models-dir /home/secragx/Documentos/Geocom_Software/GEO_RAG_CHATBOT/models`
- [ ] Step 2: Verificar que el tar contiene `models/bge-m3-int8/model.onnx`, `models/bge-m3-int8/tokenizer.json`, `models/mmarco-mMiniLMv2-L12-H384-v1-int8/model.onnx` y que el SHA-256 del `.sha256` coincide.

### Task 2: Servidor HTTP local del artefacto

**Files:** proceso `python3 -m http.server 8080 -d dist` (background)

- [ ] Step 1: Levantar el servidor en background.
- [ ] Step 2: `curl -sI http://127.0.0.1:8080/geoassist-models-1.0.0.tar.zst` → HTTP 200 y Content-Length ≈ tamaño del archivo.

### Task 3: Crear `.env`

**Files:** `.env` (nuevo, git-ignored)

- [ ] Step 1: Crear desde `.env.example` con: `APP_ENV=development`, `LLM_API_KEY` (DeepSeek del usuario), `LLM_DATA_POLICY=external_approved`, `MODELS_ARTIFACT_URL=http://172.17.0.1:8080/geoassist-models-1.0.0.tar.zst`, `MODELS_ARTIFACT_SHA256`, `SYNC_REPO_URL=/manuals_src`, `NGROK_AUTHTOKEN`/`NGROK_DOMAIN` (reutilizados de GEO_RAG_CHATBOT/.env), `DEBUG_RETRIEVAL=true`, `DEBUG_TOKEN` aleatorio, `TEAMS_SECURITY_TOKEN=` vacío.
- [ ] Step 2: Verificar que `Settings` carga el `.env` sin errores (`python -c "from geoassist.config import get_settings; ..."`).

### Task 4: Ajuste de `compose.yml`

**Files:** `compose.yml` (modificar)

- [ ] Step 1: Añadir `../manuales_soporte_L2:/manuals_src:ro` al servicio `sync` y `ports: ["127.0.0.1:9000:8000"]` al servicio `api`.
- [ ] Step 2: `docker compose config --quiet` → exit 0.

### Task 5: Bypass HMAC en desarrollo + alias `/webhook` (TDD)

**Files:** `src/geoassist/teams/outgoing_webhook.py`, `src/geoassist/api/app.py`, `src/geoassist/main.py`, `src/geoassist/api/routes/teams.py`, `tests/...`

- [ ] Step 1 (RED): pruebas: `verify` se salta con secreto vacío + `allow_unsigned` (y loguea warning); `verify` sigue exigiendo firma con secreto configurado; `verify` exige firma sin `allow_unsigned`; `POST /webhook` responde igual que `/teams/webhook`; con token configurado ambos exigen HMAC.
- [ ] Step 2: Verificar FAIL por comportamiento inexistente.
- [ ] Step 3 (GREEN): `OutgoingWebhookAdapter(secret, bot_name, allow_unsigned=False)`; skip solo si `allow_unsigned` y secreto vacío, con warning; wiring `allow_unsigned=(app_env == "development")`; segundo decorator `@router.post("/webhook")`.
- [ ] Step 4: Suite completa verde (171 + nuevas).

### Task 6: `uv` y verificación de calidad

- [ ] Step 1: Reinstalar `uv` (script oficial de astral).
- [ ] Step 2: `make check` → lint + tests en verde.

### Task 7: Build de imágenes

- [ ] Step 1: `docker compose build api sync` (con el servidor HTTP del Task 2 corriendo).
- [ ] Step 2: `docker images` muestra `geoassist-api:latest` y `geoassist-sync:latest` con modelos incluidos.

### Task 8: Primer corpus (`make sync`)

- [ ] Step 1: `make sync` → exit 0 o 2 (PDFs fallidos tolerados).
- [ ] Step 2: `make sync-status` → `doc_count > 0`, `chunk_count > 0`, `status` ok.

### Task 9: API arriba y verificación end-to-end

- [ ] Step 1: `make up` → contenedor healthy.
- [ ] Step 2: `curl http://127.0.0.1:9000/health` → 200; `/ready` → 200 con corpus+modelos+LLM.
- [ ] Step 3: `POST /internal/retrieval` con `X-Debug-Token` devuelve hits del corpus.
- [ ] Step 4: Instrucciones finales: usuario levanta ngrok y prueba en Teams.
