# GeoAssist — Diseño de arquitectura RAG para soporte L2

- **Fecha:** 2026-11-06
- **Estado:** Propuesto — pendiente de aprobación
- **Repositorio:** `Secragx/geoassist`
- **Fuente documental:** `Secragx/manuales_soporte_L2`
- **Origen:** reescritura Docker-first del MVP de soporte vía Microsoft Teams

---

## 1. Intención

GeoAssist es un asistente de soporte L2 que recibe preguntas en un canal de Microsoft Teams, busca en los manuales internos y responde citando las fuentes. La base documental llega desde un repositorio separado; el servidor la sincroniza bajo demanda, la indexa y genera los PDF corporativos cuando un operador lo pide.

La versión anterior funcionaba, pero migrarla al servidor fue doloroso por dependencias del sistema operativo. Además quedaron errores sin resolver: autenticación que fallaba abierta, indexación no atómica, evaluación vacía, timeouts incompatibles con Teams y secretos versionados. Esta reescritura conserva las ideas que sirven y descarta la capa de operación frágil.

**Usuarios:** agentes de soporte L2 que preguntan en un canal de Teams. Cada colaborador tiene su propio hilo de conversación, aunque el canal sea visible para el resto. Cuando la organización habilite el tenant, se migrará a conversaciones privadas con Bot Framework.

**Éxito de esta versión:** un host limpio con Docker ejecuta el sistema sin instalar nada más; `make sync` deja los manuales indexados y los PDF generados; una pregunta en el canal recibe una respuesta con fuentes en menos de 5 segundos; y ningún secreto, endpoint administrativo ni artefacto de índice queda expuesto o desincronizado.

---

## 2. Problema y por qué ahora

1. **Portabilidad.** La instalación anterior dependía de Python, poppler, modelos y servicios del host. El objetivo es que un host limpio con Docker ejecute el sistema sin pasos manuales ni `sudo`.
2. **Errores heredados.** Autenticación fail-open, endpoints de administración expuestos, índice reconstruido parcialmente, health que siempre respondía “ok”, evaluación que no evaluaba nada y un timeout de LLM de 20 s contra los ~5 s de Teams.
3. **Nuevo flujo documental.** Los manuales se actualizan mediante pull request, revisión y aprobación. El servidor no debe sincronizarse automáticamente: un operador ejecuta la sincronización cuando la versión fue aprobada.
4. **La estructura de carpetas actual es temporal.** Los metadatos no pueden depender de nombres como `VERSIONES APROBADAS`, porque esa organización va a cambiar. Las reglas deben ser configurables y el frontmatter debe tener prioridad.
5. **Se necesita conservar la generación de PDF.** Los manuales `.md` se convierten a PDF con plantilla corporativa y se procesan por lote. Esa función se mantiene, pero fuera de la imagen que atiende Teams.

---

## 3. Objetivos, no objetivos y anti-objetivos

### 3.1 Objetivos

- Responder en un canal de Teams vía webhook saliente, con hilos por colaborador.
- Recuperar fragmentos de manuales con búsqueda híbrida (densa + léxica + RRF + reranking) y citar fuente, versión y estado.
- Indexar manuales aprobados y pendientes de aprobación; excluir los archivados de la recuperación.
- Sincronizar manuales manualmente con `make sync`, desde el repositorio Git, dentro de contenedores.
- Generar PDF de todos los manuales, incluidos los archivados, con la plantilla existente.
- Publicar nuevas versiones del corpus de forma atómica, con validación y rollback.
- Arrancar desde un clon limpio con `docker compose up` y un `.env`.
- Operar sin `systemd`, `sudo`, `cron` del host ni socket de Docker.
- Medir la calidad de recuperación con un set dorado construido a partir de los manuales y validado por el operador.

### 3.2 No objetivos de esta versión

- Conversaciones privadas (DMs) y mensajes proactivos: quedan detrás de la interfaz de Teams para cuando exista el tenant.
- Autenticación y registro en Azure Bot Framework.
- Ingesta de PDF, DOCX, XLSX u OCR de imágenes.
- Sincronización automática por webhook de GitHub o por temporizador.
- Interfaz web, panel de administración y edición de manuales.
- Múltiples réplicas, alta disponibilidad y Postgres/pgvector.
- Reformulación de la pregunta con un LLM (rewriter): apagada al inicio por latencia.

### 3.3 Anti-objetivos

Cualquiera de estos hace fallar el proyecto aunque el bot responda:

- Un secreto en el repositorio, en un log o en un prompt.
- Un endpoint que ejecute trabajos o comandos sin autenticación.
- Un índice que pueda quedar vacío, parcial o desincronizado tras un fallo.
- Una respuesta que parezca respaldada por una fuente aunque el contexto no alcance.
- Un despliegue que necesite editar archivos o instalar paquetes en el host.
- Un “healthy” que no refleje que el modelo y el corpus están listos.

---

## 4. Restricciones

- **Docker y Compose** son el único mecanismo de despliegue. Nada se instala en el host salvo Docker y el cliente de Compose.
- **Sin acceso al tenant de Teams por ahora.** Solo webhook saliente; funciona en canales, requiere `@mención` y debe responder en menos de 5 segundos.
- **Los manuales son un repositorio Git separado**, privado, con flujo de pull request y aprobación.
- **La sincronización es manual.** No hay auto-pull ni webhook de GitHub en esta versión.
- **Hardware del servidor:** CPU únicamente, sin GPU asumida.
- **Volumen documental:** 31 archivos `.md` (30 manuales y una plantilla excluida) y 133 imágenes. Solo los `.md` entran al corpus; las imágenes se usan para los PDFs. El diseño debe escalar a algunos cientos de manuales sin cambiar de arquitectura.
- **Idioma:** español, con acentos y nombres de archivo con espacios y paréntesis.
- **Presupuesto de respuesta:** p95 menor a 4,5 s para dejar margen al límite de 5 s de Teams.

---

## 5. Alternativas consideradas

### 5.1 Entrada de Teams

| Alternativa | Ventaja | Costo | Decisión |
|---|---|---|---|
| Webhook saliente | Simple, ya probado, sin Azure | Solo canales, `@mención`, límite de 5 s, sin DMs ni proactividad | **Elegida para esta versión** |
| Azure Bot Framework | Camino soportado, DMs y proactividad, mejor autenticación | Requiere tenant, registro en Azure y SDK con pines delicados | Diferida; se implementa detrás de la misma interfaz |
| Bot Framework + servicios separados | Escala y aísla la ingesta | Demasiadas piezas para 30 manuales y un solo servidor | Descartada al inicio |

El adaptador de Teams se define como interfaz. Cambiar a Bot Framework no debe tocar recuperación, almacenamiento ni generación de respuesta.

### 5.2 Almacenamiento

| Alternativa | Ventaja | Costo | Decisión |
|---|---|---|---|
| SQLite + FTS5 + FAISS | Cero servicios extra, reproducible, suficiente para este volumen | Un escritor a la vez; el índice vectorial es un artefacto derivado | **Elegida** |
| Postgres + pgvector | Todo en una transacción, concurrencia y filtros cómodos | Un servicio más, administración y backup adicionales | Diferida; la capa de almacenamiento queda detrás de interfaces |
| Solo FAISS + metadatos en archivos | Menos dependencias | Sin consultas ni integridad; ya causó desincronización | Descartada |

La decisión sobre el modelo de embeddings también queda medida, no asumida: BGE-M3 ONNX int8 es el baseline porque ya funciona en CPU; se compara contra e5 y mpnet con el set dorado.

### 5.3 Distribución de procesos

| Alternativa | Ventaja | Costo | Decisión |
|---|---|---|---|
| Monolito modular + contenedor de herramientas | Un repo, una imagen de runtime, un entrypoint de ingesta y uno de PDF | La imagen de herramientas es pesada, pero corre por lotes | **Elegida** |
| Microservicios desde el inicio | Aislamiento y escalado independiente | Más redes, versiones y puntos de fallo | Descartada |

La imagen que atiende Teams no contiene Git, Pandoc ni TeX Live. La imagen de sincronización no atiende tráfico.

## 6. Arquitectura

### 6.1 Vista general

```
Teams (canal, @mención)
        │ HTTPS
        ▼
Caddy (prod) / ngrok (dev)
        │ red interna
        ▼
api ──────────────► /data/corpus (SQLite + FAISS, solo lectura)
 │                  /data/state  (sesiones, idempotencia)
 │
 ├── Embedder ONNX (BGE-M3 int8)
 ├── Reranker ONNX (mmarco-mMiniLMv2 int8)
 └── Cliente LLM compatible con OpenAI (DeepSeek por defecto)

sync (one-shot, perfil "tools")
 ├── git fetch/reset en /manuals
 ├── ingesta ──► /data/corpus/versions/<id>
 ├── publicación atómica de current.json
 └── PDF ──────► /data/pdfs
```

`api` nunca escribe en el corpus ni ejecuta Git, Pandoc o LaTeX. `sync` nunca atiende tráfico de Teams. La única comunicación entre ambos es el sistema de archivos del volumen y el puntero `current.json`.

### 6.2 Contenedores

| Servicio | Imagen | Responsabilidad | Perfil |
|---|---|---|---|
| `api` | runtime liviana (Python + ONNX + FAISS) | Webhook, recuperación, LLM, sesiones, health | `default` |
| `sync` | herramientas (git + Pandoc + TeX Live + embedder) | Sincronizar, indexar, publicar corpus, generar PDF | `tools` (one-shot) |
| `caddy` | `caddy` fijada por digest | TLS y reverse proxy hacia `api` | `prod` |
| `ngrok` | `ngrok/ngrok` fijada por digest | Túnel de desarrollo | `dev` |

`api` y `caddy` comparten una red interna. Solo `caddy` (o `ngrok` en desarrollo) publica puertos. `sync` se ejecuta con `docker compose run --rm sync` y no tiene puertos.

### 6.3 Estructura del repositorio

```
geoassist/
├── compose.yml
├── compose.dev.yml
├── Makefile
├── .env.example
├── .dockerignore
├── pyproject.toml
├── uv.lock
├── Dockerfile                 # runtime api
├── docker/
│   └── sync/Dockerfile        # git + pandoc + texlive + embedder
├── config/
│   ├── corpus_rules.yml       # reglas ruta → app/status
│   └── logging.yml
├── migrations/
│   ├── corpus/
│   └── state/
├── src/geoassist/
│   ├── main.py                # entrypoint api
│   ├── config.py              # pydantic-settings, fallo rápido
│   ├── logging.py             # JSON, request_id, redacción
│   ├── api/
│   │   ├── app.py
│   │   └── routes/            # teams, health, debug
│   ├── teams/
│   │   ├── base.py            # protocolo del adaptador
│   │   └── outgoing_webhook.py
│   ├── rag/
│   │   ├── embeddings.py      # interfaz + ONNX
│   │   ├── vector_store.py    # FAISS
│   │   ├── lexical.py         # FTS5
│   │   ├── fusion.py          # RRF
│   │   ├── rerank.py          # interfaz + ONNX
│   │   ├── pipeline.py        # orquestación y presupuesto
│   │   └── prompt.py
│   ├── llm/
│   │   ├── base.py
│   │   └── openai_compatible.py
│   ├── ingest/
│   │   ├── loader.py          # descubrimiento y frontmatter
│   │   ├── metadata.py        # frontmatter + reglas de ruta
│   │   ├── chunker.py
│   │   └── pipeline.py
│   ├── pdf/
│   │   ├── renderer.py
│   │   └── templates/plantilla_manuales.tex
│   ├── storage/
│   │   ├── corpus.py
│   │   ├── state.py
│   │   └── versions.py        # publicación y rollback
│   └── sync/
│       ├── git.py
│       └── orchestrator.py
├── scripts/                   # healthcheck, activate, export
├── tests/
├── eval/
│   ├── golden.jsonl
│   ├── evaluate.py
│   └── fixtures/
└── docs/
```

Los modelos ONNX viven en la imagen, no en el repositorio. Se obtienen de un **artefacto versionado** publicado en un release o registry, con URL y SHA-256 fijados; el build lo descarga, verifica y extrae. No se exportan en cada build ni se descargan en runtime.

### 6.4 Flujo de una pregunta

1. Teams envía el webhook con el cuerpo crudo y la firma HMAC.
2. El adaptador verifica la firma, parsea la actividad y descarta duplicados por `activity.id`.
3. Se resuelve la sesión (`tenant + conversation + user`) y se carga el historial acotado.
4. Se normaliza la pregunta, se detecta la aplicación si se nombra y se aplican filtros.
5. Recuperación híbrida: FAISS top-20 + FTS5 top-20, fusión RRF, rerank de los 8 mejores.
6. Si no hay contexto suficiente, se responde con abstención y sin llamar al LLM.
7. Se arma el prompt con historial, contexto citado y reglas anti-inyección.
8. El LLM responde dentro del presupuesto; si se agota, se devuelve un extracto con fuentes.
9. Se guarda el turno y se responde en el formato que espera Teams, antes de los 5 s.

---

## 7. Datos y almacenamiento

### 7.1 Dos bases, dos dominios de escritura

Se usan dos archivos SQLite separados dentro del volumen `geoassist_data`:

| Archivo | Contenido | Escritor | Lector |
|---|---|---|---|
| `corpus/versions/<id>/corpus.db` | Documentos, chunks, FTS5, metadatos del corpus | `sync` | `api` (solo lectura) |
| `state/app.db` | Sesiones, mensajes, idempotencia, feedback | `api` | `api` |

La separación evita que una sincronización bloquee las escrituras de sesiones y permite abrir el corpus en modo `read-only`. Cada versión publicada es inmutable: nunca se modifica un archivo ya publicado.

### 7.2 Esquema del corpus

```sql
CREATE TABLE documents (
    doc_id         TEXT PRIMARY KEY,       -- hash estable de la ruta relativa
    rel_path       TEXT NOT NULL UNIQUE,  -- ruta POSIX relativa a /manuals
    title          TEXT NOT NULL,
    app            TEXT NOT NULL,          -- geopos | geoinventarios | geopricer | geopromotion | unknown
    status         TEXT NOT NULL,          -- approved | pending | archived
    version        TEXT,
    content_hash   TEXT NOT NULL,
    frontmatter    TEXT,                   -- JSON serializado
    chunk_count    INTEGER NOT NULL,
    indexed_at     TEXT NOT NULL
);

CREATE TABLE chunks (
    id             INTEGER PRIMARY KEY,
    chunk_id       TEXT NOT NULL UNIQUE,   -- doc_id:ordinal
    doc_id         TEXT NOT NULL REFERENCES documents(doc_id),
    ordinal        INTEGER NOT NULL,
    heading        TEXT,                   -- breadcrumb de secciones
    text           TEXT NOT NULL,
    token_count    INTEGER NOT NULL,
    has_images     INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    heading, text,
    content='chunks', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE corpus_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

`corpus_meta` guarda `version_id`, `git_commit`, `built_at`, `doc_count`, `chunk_count`, `embedding_model`, `dimension` y el checksum del índice. La API valida estos valores al cargar.

### 7.3 Esquema de estado

```sql
CREATE TABLE sessions (
    session_key     TEXT PRIMARY KEY,   -- tenant|conversation|user
    tenant_id       TEXT,
    conversation_id TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    app_hint        TEXT,
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

CREATE TABLE messages (
    id           INTEGER PRIMARY KEY,
    session_key  TEXT NOT NULL REFERENCES sessions(session_key),
    role         TEXT NOT NULL,          -- user | assistant
    content      TEXT NOT NULL,
    sources      TEXT,                   -- JSON
    created_at   TEXT NOT NULL
);

CREATE TABLE activities (
    activity_id  TEXT PRIMARY KEY,
    response     TEXT NOT NULL,          -- JSON ya formateado
    created_at   TEXT NOT NULL
);

CREATE TABLE feedback (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    value      TEXT NOT NULL,
    comment    TEXT,
    created_at TEXT NOT NULL
);
```

Los mensajes se purgan por TTL (2 h por defecto) y se limitan a los últimos 6 turnos. `activities` expira a las 24 h. Las fechas son ISO-8601 en UTC.

### 7.4 Versiones del corpus y publicación atómica

```
/data/corpus/
├── versions/
│   ├── 20261106T143012Z-a1b2c3d/
│   │   ├── corpus.db
│   │   ├── faiss.index
│   │   └── manifest.json
│   └── 20261105T090000Z-9f8e7d6/
├── build-20261106T143012Z/     # temporal, nunca visible
└── current.json
```

`current.json`:

```json
{
  "version_id": "20261106T143012Z-a1b2c3d",
  "git_commit": "a1b2c3d",
  "db": "versions/20261106T143012Z-a1b2c3d/corpus.db",
  "index": "versions/20261106T143012Z-a1b2c3d/faiss.index",
  "db_sha256": "...",
  "index_sha256": "...",
  "doc_count": 30,
  "chunk_count": 412,
  "activated_at": "2026-11-06T14:31:20Z"
}
```

Publicar significa escribir `current.json.tmp`, hacer `fsync`, `os.replace` sobre `current.json` y `fsync` del directorio. La versión anterior queda intacta para rollback. El sistema conserva las últimas 3 versiones.

**Carga en la API:** un componente `VersionManager` memoriza el contenido de `current.json` y lo revalida cada 30 s o ante `/ready`. Si cambia, abre la nueva base en solo lectura y el nuevo índice, verifica dimensiones y conteos, y recién entonces intercambia las referencias bajo un lock de lectura/escritura. Las peticiones en curso terminan con la versión anterior, que se cierra al quedar sin uso. Si la carga falla, se mantiene la versión anterior y se registra el error.

**Rollback:** `make activate VERSION=<id>` reescribe `current.json` apuntando a una versión existente y validada. No requiere reiniciar la API.

## 8. Ingesta de manuales

### 8.1 Descubrimiento

**Contrato de montaje:** `/manuals` es el clon de la raíz del repositorio de manuales. El corpus y los assets se ubican por configuración, no por suposición:

| Clave | Default | Significado |
|---|---|---|
| `MANUALS_ROOT_REL` | `data_source/manuales` | Raíz de los `.md` a indexar |
| `ASSETS_ROOT_REL` | `data_source/assets` | Raíz central de imágenes para el PDF |

- Se recorren todos los `.md` bajo `MANUALS_ROOT_REL`, excluyendo `.git` y los globs de `ignore` de `config/corpus_rules.yml`. Los archivos fuera de esa raíz no se indexan.
- La ruta se normaliza a POSIX **relativa a la raíz del repositorio** (`data_source/manuales/OXXO/03 - APLICACIONES/.../manual.md`). Nunca se guardan rutas absolutas. `rel_path` es la identidad del documento y la clave del manifiesto de PDF.
- El contenido se lee como UTF-8 con detección de BOM; los saltos CRLF se normalizan a LF antes de calcular hashes.
- Un archivo ilegible o con YAML inválido genera una advertencia y se procesa con frontmatter vacío. No aborta el lote.
- `model_manuales.md` es una plantilla, no un manual. Se excluye por defecto con una regla de `ignore`, igual que hacía el MVP anterior.

### 8.2 Resolución de metadatos

Orden de prioridad, de mayor a menor:

1. Campos explícitos en el frontmatter: `app`, `status`, `version`, `title`.
2. Reglas de ruta de `config/corpus_rules.yml`.
3. Valores por defecto y advertencia.

Esto desacopla el dominio de la estructura de carpetas, que va a cambiar cuando el flujo de PR reemplace a `VERSIONES APROBADAS` / `VERSIONES POR APROBAR` / `VERSIONES ANTERIORES`.

Formato de reglas:

```yaml
version: 1
ignore:
  - "**/.git/**"
  - "**/_drafts/**"
  - "**/model_manuales.md"

apps:
  - match: "GEOINVENTARIOS"
    app: geoinventarios
  - match: "GEOPRICER"
    app: geopricer
    aliases: ["geoprice", "geopricer"]
  - match: "GEOPOS"
    app: geopos
  - match: "GEOPROMOTION"
    app: geopromotion

status:
  - match: "VERSIONES POR APROBAR"
    status: pending
  - match: "VERSIONES APROBADAS"
    status: approved
  - match: "VERSIONES ANTERIORES"
    status: archived

defaults:
  app: unknown
  status: unknown
  on_unmatched: warn
```

`match` es una prueba de subcadena sobre `rel_path`, sin distinguir mayúsculas ni acentos. Las reglas se evalúan en orden y la primera coincidencia gana, de modo que `VERSIONES APROBADAS - OK` coincide con la regla `VERSIONES APROBADAS` y `GEOPOS - OK` con `GEOPOS`. Un `status` desconocido se indexa con `status=unknown`, se reporta y queda fuera de la recuperación por defecto. Un `app` desconocido se indexa, se reporta y solo se recupera cuando no hay filtro de aplicación.

**Título:** se usa el `title` del frontmatter si no es un placeholder conocido (`Nombre del Manual`, vacío o `TODO`). Si lo es, se usa el primer `H1` que no sea de la sección de aprobación ni un encabezado genérico (`Objeto`, `Alcance`, `Definiciones`, `Propiedades`, `Componentes`). Si no hay, se deriva del nombre de archivo, sin extensión y con `_` reemplazados por espacios.

**Estado:** `approved`, `pending`, `archived` o `unknown`. La recuperación por defecto incluye `approved` y `pending`; `archived` y `unknown` quedan excluidos. Los PDF se generan para todos, incluidos `archived`.

### 8.3 Chunking

- Se parte por estructura: `H1`–`H4`, párrafos, tablas y bloques de código. Nunca se corta una tabla o un bloque de código si entra en el límite.
- Objetivo de 450 tokens, máximo 600, mínimo 80. Los fragmentos cortos se fusionan con el vecino de la misma sección.
- Solape del 12 % entre fragmentos consecutivos de la misma sección.
- El conteo de tokens usa el tokenizer de BGE-M3 (XLM-R), disponible offline en la imagen. No se estima por palabras.
- Cada chunk guarda un `heading` con el breadcrumb (`Documento > Sección > Subsección`). El texto que se embebe y se rerankea es `heading + "\n" + text`; el texto almacenado y citado es solo `text`.
- Tablas largas: se repite la fila de encabezado en cada continuación. Filas individuales mayores al límite se parten, con advertencia.
- Las imágenes se representan por su texto alternativo en el lugar que ocupan y se marca `has_images`. Una referencia a una imagen inexistente genera advertencia, no error: ya existe al menos una (`OK_SopPro_confirmaci_n_remesas_cajero_sin_total_img1.png`).
- Se omite el boilerplate configurable (por ejemplo, la tabla de “Elaboración, Revisión y Aprobación”), porque agrega ruido y no responde preguntas.
- `doc_id` = SHA-256 de la ruta relativa normalizada, truncado a 16 hex. `chunk_id` = `doc_id:ordinal`, con ordinal en orden de aparición. Son estables entre sincronizaciones mientras la ruta no cambie.

### 8.4 Embeddings e índice

- **Modelo baseline:** BGE-M3 ONNX int8, 1024 dimensiones, pooling CLS y normalización L2, `max_length=512`.
- **Interfaz:** `Embedder.embed_documents(texts) -> list[list[float]]` y `embed_query(text) -> list[float]`. El modelo concreto se elige por configuración.
- **FAISS:** `IndexIDMap2` sobre `IndexFlatIP`, con los `id` de la tabla `chunks` como identificadores. Búsqueda exacta, sin entrenamiento.
- **Reconstrucción completa:** cada sincronización reconstruye el índice con todos los chunks del corpus permitido. Con este volumen tarda segundos y elimina la clase de error del índice incremental.
- **Determinismo:** mismo modelo, mismos chunks y mismo orden producen el mismo índice. El manifiesto guarda checksums de `corpus.db` y `faiss.index`.
- **Procedencia de los modelos:** un comando `make publish-models` (imagen de herramientas con Torch, solo para mantenimiento) exporta y cuantiza BGE-M3 y el reranker desde revisiones fijadas de Hugging Face, arma un bundle `geoassist-models-<versión>.tar.zst`, calcula su SHA-256 y lo publica como asset de release o artefacto de registry. El build de `api` y `sync` descarga ese bundle usando `MODELS_ARTIFACT_URL` y `MODELS_ARTIFACT_SHA256`, verifica y lo hornea. Si el artefacto no está disponible, el build falla con un mensaje claro; nunca se recurre a una descarga en runtime.
- **Warmup:** la API hace `warmup` con una frase fija y solo entonces declara readiness. El reranker se carga una vez y se reutiliza.

### 8.5 Validación antes de publicar

La sincronización falla y no publica si:

- No se encontró ningún manual o ningún chunk.
- Algún documento tiene cero chunks.
- La dimensión de los vectores no coincide con `corpus_meta.dimension`.
- Hay `chunk_id` duplicados o `doc_id` huérfanos.
- El número de vectores del índice difiere de `chunk_count`.
- Una consulta de humo no recupera nada.

El reporte de la sincronización incluye documentos indexados, omitidos y con advertencias, con el motivo de cada uno.

---

## 9. Recuperación

### 9.1 Pipeline

```
pregunta
  → normalización y detección de app
  → denso (FAISS top-20)  ┐
  → léxico (FTS5 top-20)  ┴→ RRF (k=60) → rerank top-8 → política de contexto → abstención o prompt
```

- **Normalización:** se quitan menciones y formato de Teams, se aplica NFKC, se colapsan espacios y se recorta a 2000 caracteres.
- **Detección de app:** diccionario de alias conservador. Solo se reconocen nombres explícitos de aplicación y sus variantes (`geopos`, `geoinventarios`, `geopricer`/`geoprice`, `geopromotion`). Términos genéricos como “caja”, “POS” o “backoffice” **no** aplican filtro, porque un falso positivo suprimiría los documentos correctos. Si la pregunta nombra una aplicación, se aplica filtro duro por `app`; el filtro se mantiene durante la sesión salvo que la pregunta nombre otra. Los alias nuevos se agregan por configuración después de revisar casos reales.
- **Denso:** embedding de la consulta y búsqueda en FAISS, top-20.
- **Léxico:** consulta FTS5 construida con términos entrecomillados y unidos por `OR`, sobre `chunks_fts`, con `bm25(chunks_fts, 3.0, 1.0)` (encabezado, texto), top-20. Nivel chunk, igual que el denso.
- **Fusión RRF:** `score = Σ 1/(60 + rank)` sobre ambas listas, deduplicado por `chunk_id`.
- **Filtro de estado:** por defecto `approved` y `pending`. Los fragmentos `pending` se recuperan, pero se marcan en la respuesta como “versión por aprobar”. Nunca se cita un `archived`.
- **Diversidad:** máximo 3 chunks por documento en el resultado final, configurable.
- **Reranking:** cross-encoder mmarco-mMiniLMv2 ONNX int8 sobre los 8 mejores candidatos, con `heading + text`. El umbral se calibra con el set dorado; no se usa un valor inventado como el `0.005` anterior.
- **Presupuesto:** recuperación ≤ 800 ms, rerank ≤ 500 ms, medidos y registrados por etapa.

### 9.2 Política de abstención

No se llama al LLM si:

- No hay candidatos.
- El mejor score de rerank está por debajo del umbral calibrado.
- Los fragmentos recuperados no cubren la aplicación detectada.

En esos casos se responde: no se encontró información suficiente, qué se buscó y qué datos ayudarían a precisar (aplicación, mensaje de error, versión). Es preferible una abstención explícita a una respuesta plausible sin respaldo.

### 9.3 Trazabilidad

Con `DEBUG_RETRIEVAL=true`, cada respuesta registra la traza por etapa: términos léxicos, ids y scores densos, ranks RRF, scores de rerank, filtros aplicados y versión del corpus. La traza se expone solo en el endpoint interno de debug, protegido por token y accesible únicamente desde la red interna.

---

## 10. Generación de respuesta

- **Interfaz:** `LLMClient.complete(messages, timeout_s, max_tokens) -> str`. Implementación inicial `OpenAICompatibleClient` con `httpx`; DeepSeek por defecto, endpoint y modelo configurables.
- **Política de datos pendiente:** todavía no está definido si el contenido interno puede enviarse a un proveedor externo. El diseño deja un interruptor explícito, `LLM_DATA_POLICY`:
  - `external_approved`: permite el endpoint configurado y registra la decisión.
  - `on_prem_only`: exige que `LLM_BASE_URL` apunte a un host de la red interna o a una allowlist privada; si no, el servicio no arranca.

  En `production`, el valor debe estar definido; no hay default implícito. Mientras la política no se resuelva, desarrollo puede usar `external_approved`, pero producción no arranca sin una decisión. En ambos modos se envían solo la pregunta, el historial acotado y hasta 5 fragmentos recuperados; nunca el corpus completo ni secretos.
- **Sin secretos en URL.** La API key va en el header `Authorization`. No se registra en logs.
- **Presupuesto:** el LLM recibe como máximo 2500 ms. Un reintento único solo ante `429` o `5xx`, con 300 ms de espera; nunca se reintenta un timeout.
- **Prompt:** rol de soporte L2, responder en español, usar únicamente el contexto entregado, citar fuentes, decir explícitamente cuando no alcanza, y tratar el texto de los manuales como datos no confiables (ignorar instrucciones embebidas). El contexto se delimita con marcadores y se numera.
- **Contexto:** hasta 5 fragmentos y ~6000 tokens. El historial se limita a los últimos 6 turnos y ~800 tokens.
- **Salida:** `temperature=0.1`, `max_tokens=500`, texto plano compatible con Teams. Citas al final, máximo 3, con título, versión y estado: `[1] Error de recepción de bulto — v1.0 (aprobada)`.
- **Fallback:** si el LLM falla o se agota el tiempo, se responde con los fragmentos más relevantes y una nota de que la respuesta completa no pudo generarse. Se registra como `degraded=true`; no se devuelve un error a Teams.
- **Sin rewriter** en esta versión. La pregunta se envía tal cual, normalizada.

---

## 11. Integración con Teams

### 11.1 Contrato del adaptador

```python
class TeamsAdapter(Protocol):
    def verify(self, raw_body: bytes, headers: Mapping[str, str]) -> None: ...
    def parse(self, raw_body: bytes) -> InboundMessage | None: ...
    def format_reply(self, text: str) -> dict[str, Any]: ...
```

`OutgoingWebhookAdapter` implementa esta versión. `BotFrameworkAdapter` se agregará después sin cambiar el núcleo.

### 11.2 Webhook saliente

- **Verificación:** HMAC-SHA256 del cuerpo crudo, comparado con `hmac.compare_digest` contra el header `Authorization: HMAC <base64>`. Si `TEAMS_SECURITY_TOKEN` no está configurado, el servicio no arranca en modo producción. No existe camino fail-open.
- **Tamaño:** se rechazan cuerpos mayores a 64 KB antes de parsear.
- **Parseo:** se aceptan actividades de tipo `message`, se elimina la mención al bot y se ignoran mensajes vacíos. Se extraen `activity.id`, `from.id`, `conversation.id` y `channelData.tenant.id` si existe.
- **Idempotencia:** se guarda la respuesta por `activity.id` durante 24 h. Un reintento de Teams devuelve la misma respuesta sin volver a ejecutar el pipeline.
- **Respuesta:** `{"type": "message", "text": "..."}`. Se responde antes de 4,5 s; si el pipeline se acerca al límite, se devuelve el fallback con fuentes.
- **Límite de uso:** 6 preguntas por minuto por usuario. El exceso recibe un mensaje de espera, no un 429 silencioso.
- **Sesión:** clave `tenant|conversation|user`, con `tenant="default"` mientras no exista. Cada colaborador mantiene su historial aunque comparta el canal; Teams muestra cada respuesta en el hilo del mensaje que la originó.

### 11.3 Migración futura a Bot Framework

El adaptador de Bot Framework reutilizará el mismo `InboundMessage` y el mismo pipeline. Los cambios previstos son: validación JWT del conector, allowlist de `service_url`, `app_id`/`app_password`, y respuesta proactiva con un ack inmediato para poder exceder los 5 s. Nada de eso altera almacenamiento, recuperación ni prompt.

## 12. Sincronización manual

### 12.1 Comando

```bash
make sync            # sincroniza, indexa, publica y genera PDF
make sync NO_PDF=1   # solo corpus
make sync-status     # última sincronización y versión activa
```

Internamente ejecuta `docker compose --profile tools run --rm sync`. No hay temporizador, cron del host ni webhook de GitHub. El operador decide cuándo ejecutarlo, después de que el pull request fue revisado y aprobado.

### 12.2 Pasos

1. **Lock:** `flock` sobre `/data/locks/sync.lock`. Si otra sincronización está en curso, termina con código 3 sin tocar nada.
2. **Actualizar manuales:** `git fetch --depth=1 origin main` y `git reset --hard origin/main` en `/manuals`, seguido de `git clean -fd`. Se registra el commit resultante. Si falla la red o la autenticación, no se publica nada.
3. **Descubrir y parsear:** se listan los `.md`, se resuelven metadatos y se generan los chunks.
4. **Construir:** se escribe `corpus.db` y `faiss.index` en `/data/corpus/build-<id>/`. Se embeben todos los chunks del corpus recuperable (`approved` + `pending`, más `unknown` marcado).
5. **Validar:** conteos, dimensiones, integridad referencial, checksums y consulta de humo.
6. **Publicar:** renombrar el directorio de build a `versions/<id>/` y reemplazar `current.json` atómicamente. Desde este momento la API puede servir la versión nueva.
7. **PDF:** comparar contra `pdf_manifest.json` y renderizar solo los manuales cuyo hash de entrada cambió. Un fallo por archivo no detiene el resto.
8. **Reportar:** escribir `sync_report.json` con commit, versión, duraciones, documentos indexados/omitidos, advertencias y resultado de PDF.
9. **Liberar el lock.**

### 12.3 Códigos de salida

| Código | Significado |
|---|---|
| 0 | Todo correcto |
| 1 | Falló antes de publicar; la versión activa no cambió |
| 2 | Corpus publicado, pero hubo PDFs fallidos o advertencias graves |
| 3 | Otra sincronización en curso |

### 12.4 Credenciales de Git

El repositorio de manuales es privado. La autenticación se resuelve con una deploy key SSH montada como secreto de solo lectura en `/run/secrets/git_deploy_key`, o con `SYNC_GIT_TOKEN` por entorno. La clave no se copia a la imagen, no se registra y no se usa en la API. El contenedor `sync` es el único con salida a Git.

### 12.5 Concurrencia y estado

`sync` es el único escritor del corpus. La API nunca escribe en `/data/corpus`. El lock y el hecho de que `sync` sea un contenedor one-shot eliminan las sincronizaciones simultáneas sin necesidad de un coordinador externo.

---

## 13. Generación de PDF

Se conserva la función y la plantilla, no la implementación frágil.

### 13.1 Qué se conserva

- `plantilla_manuales.tex` con portada, control de versiones, tabla de contenido y estilo corporativo.
- La resolución de imágenes contra un directorio central de assets, sin importar dónde esté el `.md`.
- La generación en lote con salida espejo de la estructura de carpetas.

### 13.2 Correcciones

- **Frontmatter robusto:** parseo YAML real, tolerante a CRLF y BOM; no regex sobre el encabezado.
- **Imágenes:** `--resource-path` apunta a `data_source/assets`. Si una referencia no existe, se reemplaza por su texto alternativo y se registra una advertencia, en lugar de romper el PDF.
- **Logo:** el frontmatter declara `Geocom.png`, pero el asset real es `Geocom.jpg`. Se resuelve con búsqueda por nombre base y extensión alternativa.
- **Acentos:** `fontenc` T1 y `lmodern` en la plantilla, y `LANG=C.UTF-8` en el contenedor.
- **Seguridad:** `-no-shell-escape`, sin `\write18`, sin rutas del host. El trabajo ocurre en `/tmp`, porque el volumen de manuales es de solo lectura.
- **Aislamiento:** Pandoc y TeX Live viven solo en la imagen `sync`. La imagen de la API no los contiene.

### 13.3 Alcance y manifiesto

- Se genera PDF de **todos** los `.md`, incluidos los archivados; funcionan como archivo histórico.
- `pdf_manifest.json` guarda, por ruta relativa: hash del `.md`, hash de las imágenes referenciadas, hash de la plantilla, motor y versión, ruta del PDF, SHA-256 del PDF, fecha, estado y error.
- Se regenera solo lo que cambió. Un cambio en una imagen referenciada o en la plantilla invalida los PDFs afectados.
- La salida es `/data/pdfs/<rel_path sin .md>.pdf`, es decir, se conserva la ruta relativa completa y se reemplaza la extensión. Así dos manuales del mismo directorio nunca colisionan. La clave del manifiesto es `rel_path`.
- En esta versión los PDFs **no se publican por HTTP**. Quedan en el volumen; exponerlos es una decisión posterior con su propio control de acceso.

### 13.4 Fallos

Un manual que falla queda en el manifiesto con su error y no interrumpe el lote. Al final, si hubo fallos, el comando termina con código 2 y el reporte indica cuáles. Como el corpus se publica antes de generar PDFs, un fallo de PDF no deja el RAG sin servicio.

---

## 14. Configuración y seguridad

### 14.1 Configuración

- `pydantic-settings` valida todo al arranque. Un valor faltante o inválido detiene el proceso con un mensaje claro; no hay defaults inseguros.
- Secretos por entorno o archivos en `/run/secrets`. Nunca en la imagen, el repositorio ni argumentos de build.
- `.env.example` documenta cada variable; `.env` está en `.gitignore` y en `.dockerignore`.
- En `APP_ENV=production`, `TEAMS_SECURITY_TOKEN`, `LLM_API_KEY` y las rutas de modelos son obligatorios. Sin ellos, el servicio no arranca.

### 14.2 Variables principales

| Variable | Uso |
|---|---|
| `APP_ENV` | `development` o `production` |
| `TEAMS_SECURITY_TOKEN` | Secreto HMAC del webhook saliente |
| `TEAMS_BOT_NAME` | Nombre para quitar la mención |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Cliente compatible con OpenAI |
| `LLM_TIMEOUT_MS` | Presupuesto del LLM (default 2500) |
| `EMBEDDING_MODEL_PATH` | Directorio ONNX de BGE-M3 |
| `RERANK_MODEL_PATH` | Directorio ONNX del reranker |
| `RERANK_MIN_SCORE` | Umbral calibrado con el set dorado |
| `DATA_DIR` / `MANUALS_DIR` | Rutas internas de volúmenes |
| `SESSION_TTL_MINUTES` | Expiración de sesiones (default 120) |
| `RATE_LIMIT_PER_MINUTE` | Límite por usuario (default 6) |
| `DEBUG_RETRIEVAL` / `DEBUG_TOKEN` | Traza interna; apagada por defecto |
| `LOG_QUESTION_TEXT` | Registrar texto de preguntas (default `false`) |
| `SYNC_REPO_URL` | Repositorio de manuales |
| `SYNC_GIT_TOKEN` | Alternativa a la deploy key |

### 14.3 Seguridad

- **Fail-closed:** sin secreto no hay servicio. La validación HMAC es obligatoria y usa el cuerpo crudo.
- **Sin ejecución desde el chat.** No existen comandos `exec:`, herramientas de shell ni acciones administrativas en el webhook.
- **Administración separada.** `/internal/*` solo escucha en la red interna, exige `DEBUG_TOKEN` y se deshabilita por completo si `DEBUG_RETRIEVAL=false`.
- **Superficie mínima.** Solo Caddy publica puertos. FastAPI no expone `/docs` ni `/openapi.json` en producción.
- **Límites:** cuerpo máximo 64 KB, pregunta máxima 2000 caracteres, rate limit por usuario y timeouts en todas las llamadas externas.
- **Errores opacos:** al cliente se le devuelve un código estable y un mensaje genérico; el detalle va al log con `request_id`. Nunca `str(e)`.
- **Secretos fuera de logs y prompts.** El logger redacta headers, tokens y claves. `LOG_QUESTION_TEXT=false` evita guardar contenido de soporte por defecto.
- **Inyección de prompt:** el contexto se delimita, se numera y se declara como datos; el sistema indica ignorar instrucciones contenidas en los manuales.
- **Cadena de suministro:** imágenes por digest, dependencias con lockfile y hashes, `gitleaks` en pre-commit y CI, y modelos verificados por SHA-256.

**Requisito previo, fuera de este repositorio:** rotar los secretos expuestos en `RAG2/GeoAssist` y purgar el historial de Git de ese repositorio. Ninguna credencial de esa versión se reutiliza acá.

---

## 15. Operación con Docker

### 15.1 Imágenes

- Base `python:3.12-slim` fijada por digest; `uv` para instalar con lockfile y hashes.
- Build multi-stage: dependencias en una etapa, runtime mínimo en otra.
- `api`: sin compiladores, sin Git, sin Pandoc, sin TeX. Solo runtime, código y modelos.
- `sync`: Git, Pandoc, TeX Live, `pypandoc` y el embedder. Es grande y se ejecuta por lotes.
- Usuario no root `10001`, `read_only: true`, `tmpfs` en `/tmp`, `cap_drop: [ALL]`, `no-new-privileges`, `restart: unless-stopped`.
- Límites de recursos: `api` 3 GB de memoria y 2 CPUs; `sync` 4 GB y 2 CPUs. El reranker y el embedder se cargan una vez, no por request.

### 15.2 Volúmenes y redes

| Volumen | Montaje | Acceso |
|---|---|---|
| `geoassist_data` | `/data` | `api` lectura/escritura solo en `state/` y `logs/`; `sync` lectura/escritura en todo |
| `geoassist_manuals` | `/manuals` | `api` sin acceso; `sync` lectura/escritura del clon |

- Red `internal` para `api` y `caddy`; `sync` solo se conecta cuando corre.
- `api` no publica puertos. `caddy` publica 80/443. `ngrok` es un perfil de desarrollo.
- Zona horaria UTC en todos los contenedores.

### 15.3 Healthchecks

- `GET /health` (liveness): proceso vivo, responde siempre rápido, sin dependencias.
- `GET /ready` (readiness): base de estado accesible, versión de corpus cargada, embedder y reranker calientes, `LLM_BASE_URL` configurado. No llama al LLM.
- `docker compose` usa `depends_on: condition: service_healthy` para no arrancar Caddy antes que la API.
- El healthcheck se implementa con un script Python incluido en la imagen; no se depende de `curl`.

### 15.4 Copias y recuperación

- Corpus y PDFs son **regenerables** desde el repositorio de manuales; no requieren backup.
- `state/app.db` contiene sesiones e idempotencia: se puede perder sin daño permanente.
- Se conservan las últimas 3 versiones del corpus para rollback inmediato sin reindexar.
- Un host nuevo se recupera con: clonar, `.env`, `docker compose up`, `make sync`.

## 16. Pruebas y evaluación

### 16.1 Pruebas automatizadas

| Nivel | Qué cubre |
|---|---|
| Unitarias | Chunker (tablas, código, solape, límites), precedencia de metadatos, placeholders, detección de app, RRF, filtro de estado, HMAC, claves de sesión, delimitación del prompt |
| Integración | Corpus de fixture (5 `.md` pequeños) con embedder determinista, construcción de `corpus.db` + FAISS, consulta, publicación atómica fallida, rollback y recarga de versión |
| Contrato | Payloads de Teams saneados: firma válida, inválida y ausente; actividad duplicada devuelve la respuesta cacheada; formato de respuesta |
| End-to-end | Compose con un LLM falso: webhook firmado → respuesta con citas → latencia registrada |
| PDF | Fixture con tabla, acentos, imagen existente e imagen faltante: PDF generado, manifiesto correcto, lote continúa |
| Seguridad | Sin secreto el servicio no arranca; `/internal` cerrado sin token; cuerpo sobredimensionado rechazado; `gitleaks` limpio |

Las pruebas no requieren red ni modelos reales, salvo la suite de evaluación. El embedder de prueba es determinista y vive solo en `tests/`.

### 16.2 Set dorado

Archivo `eval/golden.jsonl`, versionado en el repositorio. **No se usa la bitácora en Excel como fuente**: el corpus esperado son únicamente los `.md` de manuales, y cualquier archivo que no sea `.md` se ignora en la ingesta. El set dorado se construye leyendo los manuales: el builder propone 10–15 casos con pregunta, aplicación, documento esperado, hechos esperados y si debe abstenerse; el operador los valida en M4 y desde ahí quedan versionados.

```json
{
  "id": "INC9954089",
  "question": "¿Cómo confirmo una remesa en caja sin total?",
  "app": "geopromotion",
  "expected_docs": ["OXXO/.../SopPro_....md"],
  "expected_facts": ["remesas", "confirmación", "cajero"],
  "must_abstain": false,
  "notes": "Caso que falló por recuperar fondo sencillo"
}
```

### 16.3 Métricas

- **Recuperación:** Recall@5, MRR@10, nDCG@10, exactitud del filtro de aplicación.
- **Abstención:** proporción correcta en casos `must_abstain` y falsas abstenciones.
- **Respuesta:** cobertura de `expected_facts` sobre el texto normalizado y revisión humana en release.
- **Operación:** latencia p50/p95 por etapa, tasa de fallback, tasa de “no encontrado”, duplicados detectados.

### 16.4 Gates

- Recall@5 no puede caer más de 2 puntos respecto de la última línea base registrada.
- Los casos `must_abstain` deben abstenerse en el 100 % de las corridas automatizadas.
- Ninguna respuesta puede citar un documento `archived`.
- La suite de integración y contrato debe pasar en cada PR.
- La evaluación con modelos reales corre en merge a `main` o manualmente (`make eval`), no en cada PR.

### 16.5 CI

Lint y formato (`ruff`), tipos (`mypy` pragmático), tests, `gitleaks`, validación de `docker compose config` y build de las dos imágenes. El pipeline falla si aparece un secreto o si baja un gate de recuperación.

---

## 17. Hitos y criterios de aceptación

| Hito | Entrega | Aceptación |
|---|---|---|
| **M0 Fundaciones** | Repo, Compose, Dockerfiles, artefacto de modelos, configuración, logging, `/health` y `/ready`, CI | Clon limpio + `.env` + `docker compose up` deja `api` healthy sin instalar nada en el host, con el artefacto de modelos verificado |
| **M1 Ingesta** | Metadatos configurables, chunker, embedder ONNX, FAISS, versionado, validación, `sync` sin PDF | `make sync NO_PDF=1` indexa los 30 manuales (excluye `model_manuales.md`), publica una versión y una falla de validación no cambia `current.json` |
| **M2 Teams** | Adaptador, HMAC, idempotencia, sesiones, retrieval híbrido, rerank, prompt, LLM, fallback | Un webhook firmado recibe respuesta con fuentes; p95 < 4,5 s en el servidor; un duplicado no ejecuta el pipeline dos veces |
| **M3 PDF** | Renderer, manifiesto, resolución de imágenes, correcciones de plantilla | Se generan los PDFs de todos los `.md`; los fallos quedan reportados y no afectan al RAG |
| **M4 Calidad** | Set dorado, `make eval`, gates, runbook, rollback documentado | Set de 10–15 casos validado por el operador, métricas base registradas, gates activos y rollback probado |

M3 puede ejecutarse en paralelo con M2; M1 es prerrequisito de ambos.

---

## 18. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| El LLM tarda más que el límite de Teams | Respuestas cortadas o duplicadas | Presupuesto por etapa, timeout duro, fallback con fuentes, medición temprana en el servidor real |
| El webhook saliente es un mecanismo legacy | Quedar sin integración a futuro | Adaptador aislado y migración planificada a Bot Framework |
| Baja fidelidad de los PDF (tablas, imágenes) | Pérdida de calidad documental | Comparación visual contra los PDFs anteriores en M3, manifiesto por archivo |
| El conocimiento visual de los manuales no es recuperable | Respuestas incompletas | Limitación documentada; OCR/captions como extensión posterior |
| Las reglas de ruta quedan obsoletas | Documentos sin app o estado | Reporte de no coincidentes en cada sync y test de fixture |
| La elección de embeddings no es la óptima | Peor recuperación | Benchmark BGE-M3 vs e5 vs mpnet en M4; reindexar es parte del flujo |
| Credenciales del repositorio privado | Sync imposible o secreto expuesto | Deploy key de solo lectura en `/run/secrets`, rotación y `gitleaks` |
| Corrupción de SQLite por corte de energía | Corpus ilegible | Versiones inmutables, WAL en `state`, reconstrucción desde Git |
| Sin tenant de Teams | Sin DMs ni proactividad | `tenant="default"` y migración posterior sin cambiar el núcleo |
| Picos de memoria al cargar modelos | Contenedor reiniciado | Límites explícitos, carga única con warmup, sin recarga por request |
| Política de datos del LLM sin definir | Enviar documentación interna a un tercero sin aprobación | `LLM_DATA_POLICY` obligatorio en producción y bloqueo de endpoints externos en modo `on_prem_only` |
| Artefacto de modelos no disponible o corrupto | Build no reproducible o modelo equivocado | URL y SHA-256 fijados, verificación en build y fallo explícito |

### Dependencias de despliegue (las provee el operador)

- URL pública y secreto del webhook saliente registrado en Teams.
- Clave y endpoint del LLM (DeepSeek por defecto) y definición de `LLM_DATA_POLICY`.
- Artefacto de modelos publicado con su SHA-256, en release o registry.
- Deploy key o token para el repositorio privado de manuales.
- Dominio para Caddy en producción; `ngrok` para desarrollo.

Ninguna de estas bloquea el diseño; se configuran por entorno.

---

## 19. Decisiones de implementación

### 19.1 Decisiones tomadas durante la revisión del diseño

- `/manuals` es la raíz del repositorio; el corpus y los assets se ubican por `MANUALS_ROOT_REL` y `ASSETS_ROOT_REL`.
- `rel_path` es relativo a la raíz del repositorio, no a la carpeta de manuales.
- Los PDFs se guardan en `/data/pdfs/<rel_path sin .md>.pdf` y no se publican por HTTP en esta versión.
- La detección de aplicación usa solo nombres explícitos y alias conservadores; los términos genéricos no filtran.
- `model_manuales.md` se excluye por ser plantilla, no manual.
- Los modelos se distribuyen como artefacto versionado con SHA-256, no se exportan en cada build.
- `LLM_DATA_POLICY` es obligatorio en producción mientras la política de datos no esté definida.

### 19.2 Decisiones que quedan en manos del builder

- Librerías concretas: `python-frontmatter` o parser propio, `structlog` o logging estándar, `httpx`, `pytest`, `ruff`, `mypy`.
- Runner de migraciones: archivos SQL numerados con tabla `schema_migrations`, sin framework.
- CI en GitHub Actions (default), con los pasos descritos.
- Contenido exacto del corpus de fixture y de los payloads saneados de Teams.
- Paquete y formato del tokenizer XLM-R para contar tokens offline.
- Detalles de la plantilla LaTeX necesarios para paridad visual, sin cambiar el estilo.
- Implementación de redacción de logs y del script de healthcheck.

---

## 20. Anexo: comandos y configuración

### 20.1 Make targets

| Comando | Efecto |
|---|---|
| `make up` | Levanta `api` y, en producción, `caddy` |
| `make down` | Detiene los servicios |
| `make logs` | Sigue logs JSON de `api` |
| `make sync` | Sincroniza manuales, indexa, publica y genera PDF |
| `make sync NO_PDF=1` | Sincroniza e indexa sin PDF |
| `make sync-status` | Muestra versión activa y último reporte |
| `make activate VERSION=<id>` | Rollback a una versión anterior validada |
| `make publish-models` | Exporta, empaqueta y publica el artefacto de modelos (mantenimiento) |
| `make eval` | Corre el set dorado y reporta métricas |
| `make test` | Unitarias, integración, contrato |
| `make lint` | Ruff, mypy y `gitleaks` |
| `make shell` | Shell dentro del contenedor `api` |
| `make db` | Consulta de solo lectura sobre `corpus.db` activo |

### 20.2 Ejemplo de `.env.example`

```dotenv
APP_ENV=development
TEAMS_SECURITY_TOKEN=
TEAMS_BOT_NAME=GeoAssist

LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL=deepseek-chat
LLM_TIMEOUT_MS=2500
# production exige external_approved | on_prem_only
LLM_DATA_POLICY=

MODELS_ARTIFACT_URL=
MODELS_ARTIFACT_SHA256=
EMBEDDING_MODEL_PATH=/app/models/bge-m3-int8
RERANK_MODEL_PATH=/app/models/mmarco-mMiniLMv2-L12-H384-v1-int8
RERANK_MIN_SCORE=0.0

DATA_DIR=/data
MANUALS_DIR=/manuals
MANUALS_ROOT_REL=data_source/manuales
ASSETS_ROOT_REL=data_source/assets
SESSION_TTL_MINUTES=120
RATE_LIMIT_PER_MINUTE=6

DEBUG_RETRIEVAL=false
DEBUG_TOKEN=
LOG_QUESTION_TEXT=false
LOG_LEVEL=INFO

SYNC_REPO_URL=git@github.com:Secragx/manuales_soporte_L2.git
SYNC_GIT_TOKEN=
```

`RERANK_MIN_SCORE=0.0` es un valor provisional que se reemplaza por el umbral calibrado en M4; no se asume como definitivo.

### 20.3 Fuera del alcance, explícitamente

Conversaciones privadas, mensajes proactivos, autenticación Azure, ingesta de PDF/DOCX, OCR, interfaz web, edición de manuales, auto-sync, multi-réplica y Postgres/pgvector. Todos tienen un punto de extensión definido, pero no se implementan en esta versión.

---

## 21. Aprobación

Este documento requiere aprobación explícita antes de escribir código de producto. La aprobación cubre la arquitectura, las decisiones de almacenamiento y recuperación, el alcance del webhook y la generación de PDF. Cualquier cambio posterior de alcance se refleja acá antes de implementarse.
