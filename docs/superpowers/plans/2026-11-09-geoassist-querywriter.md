# QueryWriter y respuestas enriquecidas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interpretar las preguntas de los asesores (jerga, typos, formas variables de escribir) antes de buscar, y devolver respuestas largas y completas con los procedimientos enriquecidos del contexto.

**Architecture:** Dos capas de interpretación antes del retrieval: una local instantánea (`LocalQueryInterpreter`: alias de jerga + corrección de typos contra el vocabulario del corpus) y una LLM (`QueryRewriter` estilo bot anterior: DeepSeek con cache y fallback a la consulta original). El `ChatService` las encadena antes de buscar, con presupuesto de latencia configurable por env y generación acotada a 500 tokens con prompt de procedimiento completo.

**Tech Stack:** Python 3.12, difflib (stdlib) para fuzzy matching, cliente LLM OpenAI-compatible existente, structlog, pytest.

**Spec:** Diseño corto aprobado por el humano el 2026-11-09 en conversación: QueryWriter con capa local + capa LLM (fallback obligatorio), respuestas largas enriquecidas (max_tokens 500), presupuesto `TOTAL_BUDGET_MS=8000` configurable, jerga configurable en `config/corpus_rules.yml`, `config/` montado como volumen.

## Global Constraints

- La capa LLM del rewrite **nunca bloquea**: timeout propio (`QUERY_REWRITE_TIMEOUT_MS`, 1200 ms) y fallback a la consulta original ante error/timeout/vacío/sobredimensionada (>300 chars).
- La consulta reescrita alimenta **solo retrieval y detección de app**, nunca el texto de la respuesta; la generación recibe la pregunta original del usuario.
- Presupuestos por fase derivados de `TOTAL_BUDGET_MS` (default 8000): búsqueda 20 %, generación 65 %, estado 10 %, buffer 5 %.
- `max_tokens` de generación: 500. Prompt sin límite de "4-5 oraciones": procedimiento completo, todos los pasos, sin omitir ninguno.
- Corrección de typos **conservadora**: solo tokens ≥ 4 caracteres y solo si hay candidato con similitud alta; jamás modificar palabras del historial ni la pregunta original almacenada en sesiones.
- Alias de jerga con límite de palabra completo (nada de subcadenas).
- TDD obligatorio: cada test falla antes de implementar. Suite completa (`make check`) en verde al cierre de cada tarea.

## Review Focus

1. **Teams podría cortar respuestas > 5 s** (la doc oficial dice 5 s; hay mediciones de ~10 s): la respuesta larga puede no llegar. Prueba: tarea 7 valida empíricamente en Teams y `TOTAL_BUDGET_MS` permite bajar sin tocar código.
2. **Sobre-corrección de typos**: una palabra válida pero poco común podría "corregirse" a un término del vocabulario y arruinar la búsqueda. Prueba: tarea 3 fija falsos positivos ("error en la impresora" intacto).
3. **Manipulación vía historial en el rewrite**: el historial va al rewrite; el texto reescrito jamás debe llegar al prompt de generación. Prueba: tarea 5 fija que la generación recibe la pregunta original.
4. **El rewrite corre fuera de las fases de presupuesto**: suma máxima ≈ rewrite (1.2 s) + fases (8 s) ≈ 9.2 s. Prueba: tarea 4 fija que el fallback ante timeout es inmediato.
5. **Alias demasiado genéricos** filtrarían la búsqueda a la app equivocada. Prueba: tarea 3 fija que términos genéricos ("local", "caja") no se mapean.

---

## File Map

```
geoassist/
├── config/corpus_rules.yml            # aliases de jerga ampliados
├── compose.yml                        # monta ./config:/app/config:ro
├── .env / .env.example                # QUERY_REWRITE_*, TOTAL_BUDGET_MS, LLM_TIMEOUT_MS
└── src/geoassist/
    ├── config.py                      # nuevos Settings
    ├── main.py                        # wiring interpreter + rewriter
    ├── rag/rewrite.py                 # NUEVO: LocalQueryInterpreter, QueryRewriter
    ├── rag/prompt.py                  # SYSTEM_PROMPT enriquecido
    ├── services/chat.py               # pipeline + presupuesto configurable + max_tokens
    └── storage/corpus_read.py         # CorpusReader.vocabulary()
```

---

### Task 1: Campos de configuración

**Files:**
- Modify: `src/geoassist/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.query_rewrite_enabled: bool = False`, `Settings.query_rewrite_cache: int = Field(default=128, gt=0)`, `Settings.query_rewrite_timeout_ms: int = Field(default=1200, gt=0)`, `Settings.total_budget_ms: int = Field(default=8000, gt=0)`.

- [ ] **Step 1: Write the failing test**

```python
def test_query_rewrite_and_budget_defaults():
    settings = make_settings()
    assert settings.query_rewrite_enabled is False
    assert settings.query_rewrite_cache == 128
    assert settings.query_rewrite_timeout_ms == 1200
    assert settings.total_budget_ms == 8000


def test_budget_and_timeouts_must_be_positive():
    with pytest.raises(ValidationError):
        make_settings(total_budget_ms=0)
    with pytest.raises(ValidationError):
        make_settings(query_rewrite_timeout_ms=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v -k "budget or rewrite"`
Expected: FAIL (los campos no existen).

- [ ] **Step 3: Implementar los cuatro campos en `Settings`** con los defaults exactos de arriba (`Field(gt=0)` en los tres numéricos).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/config.py tests/test_config.py
git commit -m "feat: configuración de query rewrite y presupuesto total"
```

---

### Task 2: Vocabulario del corpus

**Files:**
- Modify: `src/geoassist/storage/corpus_read.py`
- Test: `tests/test_corpus_read.py`

**Interfaces:**
- Produces: `CorpusReader.vocabulary() -> list[str]` — términos normalizados (minúsculas, sin acentos, ≥ 4 caracteres) de títulos de documentos y headings de chunks, deduplicados, ordenados por frecuencia descendente, máximo 2000, con stopwords del español descartadas (lista mínima: de, la, el, los, las, para, por, con, una, un, que, del, al, como, mas, esta, este, desde, sobre, entre, cuando, puede, debe, no, si, o, y, e, u, a).

- [ ] **Step 1: Write the failing test** (usa el fixture `corpus` existente de este archivo)

```python
def test_vocabulary_contains_titles_and_headings(corpus):
    vocab = corpus.vocabulary()
    assert "recepcion" in vocab or "devolucion" in vocab
    assert all(len(term) >= 4 for term in vocab)
    assert all(term == term.lower() for term in vocab)


def test_vocabulary_excludes_stopwords_and_caps_size(corpus):
    vocab = corpus.vocabulary()
    assert "para" not in vocab
    assert "como" not in vocab
    assert len(vocab) <= 2000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_corpus_read.py -k vocabulary -v`
Expected: FAIL (`AttributeError: 'CorpusReader' object has no attribute 'vocabulary'`).

- [ ] **Step 3: Implementar `vocabulary()` en `CorpusReader`** — una consulta SQL sobre `documents.title` y `chunks.heading` (mismo estilo de conexión solo-lectura que el resto de la clase), tokenización por `\w+`, normalización NFKD sin acentos (patrón de `geoassist.ingest.rules.normalize_text`), conteo por término, orden por frecuencia.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_corpus_read.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/storage/corpus_read.py tests/test_corpus_read.py
git commit -m "feat: vocabulario del corpus para interpretación local"
```

---

### Task 3: LocalQueryInterpreter (jerga + typos)

**Files:**
- Create: `src/geoassist/rag/rewrite.py`
- Test: `tests/test_rewrite.py`

**Interfaces:**
- Produces: `LocalQueryInterpreter(aliases: Mapping[str, str], vocabulary: Sequence[str] = ())`
- Produces: `.interpret(text: str) -> str` — devuelve el texto con los términos reemplazados por su forma canónica; el texto original nunca se muta.
- Constantes: `MIN_TOKEN_LEN = 4`, `ALIAS_CUTOFF = 0.85` (similitud `difflib.SequenceMatcher`), `VOCAB_CUTOFF = 0.9`.

- [ ] **Step 1: Write the failing test**

```python
from geoassist.rag.rewrite import LocalQueryInterpreter

ALIASES = {"geoconsola": "geopos", "consola": "geopos", "inventario": "geoinventarios"}
VOCAB = ["geopos", "geoinventarios", "devolucion", "cedis", "impresora", "remesa"]


def test_jerga_maps_to_canonical_app():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("la geoconsola no carga")
    assert "geopos" in out
    assert "geoconsola" not in out


def test_typo_in_product_name_is_corrected():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("geopcos no abre")
    assert "geopos" in out


def test_typo_in_case_term_is_corrected():
    out = LocalQueryInterpreter(ALIASES, VOCAB).interpret("no puedo hacer la devoLucion")
    assert "devolucion" in out


def test_valid_uncommon_words_are_not_mangled():
    original = "error en la impresora del local grande"
    assert LocalQueryInterpreter(ALIASES, VOCAB).interpret(original) == original


def test_short_tokens_are_untouched():
    original = "el pos no va"
    assert LocalQueryInterpreter(ALIASES, VOCAB).interpret(original) == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rewrite.py -v`
Expected: FAIL (módulo inexistente).

- [ ] **Step 3: Implementar `LocalQueryInterpreter`** — tokeniza por palabras (`\w+`), normaliza con `normalize_text` para comparar pero reemplaza sobre el texto original preservando el resto tal cual. Para cada token de longitud ≥ `MIN_TOKEN_LEN`: 1) match exacto normalizado contra alias → reemplaza por el valor canónico; 2) si no, `difflib.get_close_matches` contra las claves de alias con `cutoff=ALIAS_CUTOFF` → reemplaza; 3) si no, contra el vocabulario con `cutoff=VOCAB_CUTOFF` → reemplaza por el término del vocabulario. Sin candidatos → token intacto.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rewrite.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/rewrite.py tests/test_rewrite.py
git commit -m "feat: interpretación local de consultas (jerga y typos)"
```

---

### Task 4: QueryRewriter LLM (cache + fallback)

**Files:**
- Modify: `src/geoassist/rag/rewrite.py`
- Test: `tests/test_rewrite.py`

**Interfaces:**
- Produces: `REWRITE_PROMPT: str` — "Reformula la consulta del usuario para buscar en manuales técnicos de soporte (GEOPOS, GEOINVENTARIOS, GEOPRICER, GEOPROMOTION). Corrige errores de ortografía y traduce nombres coloquiales de productos al nombre real. Devuelve SOLO la consulta reformulada en español, conservando nombres de sistemas, módulos y términos clave. No expliques nada."
- Produces: `QueryRewriter(llm: LLMClient, *, enabled: bool = True, cache_size: int = 128, timeout_s: float = 1.2)`
- Produces: `.rewrite(query: str, history: Sequence[ChatMessage]) -> str` — devuelve la consulta reescrita o la original si falla.

- [ ] **Step 1: Write the failing test**

```python
from geoassist.models import ChatMessage
from geoassist.rag.rewrite import QueryRewriter


class RecordingLLM:
    def __init__(self, answer="geopos no carga", error=None):
        self.answer, self.error, self.calls = answer, error, 0
        self.last = None

    def complete(self, messages, *, timeout_s, max_tokens=500, temperature=0.1):
        self.calls += 1
        self.last = {"messages": messages, "timeout_s": timeout_s, "max_tokens": max_tokens}
        if self.error:
            raise self.error
        return self.answer


def test_rewrite_uses_llm_with_short_output_and_timeout():
    llm = RecordingLLM()
    out = QueryRewriter(llm).rewrite("geoconsola no crga", [])
    assert out == "geopos no carga"
    assert llm.last["max_tokens"] <= 96
    assert llm.last["timeout_s"] <= 1.2


def test_rewrite_falls_back_to_original_on_error():
    from geoassist.llm.base import LLMError

    llm = RecordingLLM(error=LLMError("timeout", "boom"))
    assert QueryRewriter(llm).rewrite("hola", []) == "hola"


def test_rewrite_falls_back_on_empty_or_oversized():
    assert QueryRewriter(RecordingLLM(answer="")).rewrite("hola", []) == "hola"
    assert QueryRewriter(RecordingLLM(answer="x" * 400)).rewrite("hola", []) == "hola"


def test_cache_avoids_second_call():
    llm = RecordingLLM()
    rewriter = QueryRewriter(llm)
    rewriter.rewrite("hola", [])
    rewriter.rewrite("hola", [])
    assert llm.calls == 1


def test_disabled_rewriter_returns_original_without_llm():
    llm = RecordingLLM()
    assert QueryRewriter(llm, enabled=False).rewrite("hola", []) == "hola"
    assert llm.calls == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rewrite.py -v`
Expected: FAIL (`QueryRewriter` no existe).

- [ ] **Step 3: Implementar `QueryRewriter`** — estilo del `QueryRewriter` del bot anterior: `OrderedDict` con `move_to_end`/`popitem` (LRU), historial limitado a los últimos 2 turnos, `complete(messages, timeout_s=timeout_s, max_tokens=96)`, resultado saneado (strip, sin comillas, primera línea) y descartado si está vacío o supera 300 caracteres. `enabled=False` → devuelve la consulta sin llamar al LLM.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rewrite.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/rewrite.py tests/test_rewrite.py
git commit -m "feat: QueryRewriter LLM con cache y fallback"
```

---

### Task 5: ChatService — pipeline interpretado, presupuesto configurable, respuestas largas

**Files:**
- Modify: `src/geoassist/services/chat.py`
- Test: `tests/test_chat_service.py`

**Interfaces:**
- Consumes: `LocalQueryInterpreter.interpret`, `QueryRewriter.rewrite` (Task 3/4).
- Produces: `ChatService(state, retriever, llm, detector, *, clock, session_ttl_minutes=120, max_llm_ms=2500, interpreter=None, rewriter=None, total_budget_ms=8000)`.
- Reparto de presupuesto desde `total_budget_ms`: búsqueda 20 %, generación 65 %, estado 10 % (el resto es buffer). `_MAX_COMPLETION_TOKENS = 500`.
- Flujo en `handle`: `interpretado = interpreter.interpret(texto)` (si hay interpreter) → `consulta = rewriter.rewrite(interpretado, historial)` (si hay rewriter; fallback interno) → detector de app sobre `interpretado` → retrieval con `consulta` → generación con la **pregunta original** (`message.text`) en el prompt.

- [ ] **Step 1: Write the failing test**

```python
class RecordingInterpreter:
    def __init__(self, out):
        self.out, self.seen = out, None

    def interpret(self, text):
        self.seen = text
        return self.out


class RecordingRewriter:
    def __init__(self, out):
        self.out, self.seen = out, None

    def rewrite(self, query, history):
        self.seen = query
        return self.out


def test_interpreter_and_rewriter_feed_retrieval(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    retriever = FakeRetriever(result)
    interpreter = RecordingInterpreter("geopos no carga")
    rewriter = RecordingRewriter("geopos no abre ni carga")
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        retriever,
        llm,
        FakeDetector(),
        interpreter=interpreter,
        rewriter=rewriter,
    )
    service.handle(MESSAGE)
    assert interpreter.seen == MESSAGE.text
    assert rewriter.seen == "geopos no carga"
    assert retriever.seen_question == "geopos no abre ni carga"


def test_generation_receives_original_question_not_rewrite(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    rewriter = RecordingRewriter("consulta inyectada")
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        FakeRetriever(result),
        llm,
        FakeDetector(),
        rewriter=rewriter,
    )
    service.handle(MESSAGE)
    user_msgs = [m["content"] for m in llm.messages if m["role"] == "user"]
    assert any(MESSAGE.text in m for m in user_msgs)
    assert not any("consulta inyectada" in m for m in user_msgs)


def test_budget_split_follows_total_budget_ms(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    service = ChatService(
        StateStore(tmp_path / "state.db"),
        FakeRetriever(result),
        llm,
        FakeDetector(),
        max_llm_ms=100_000,
        total_budget_ms=2000,
    )
    service.handle(MESSAGE)
    # generación = 65 % de 2000 = 1300 ms
    assert llm.last_timeout_s <= 1.3


def test_generation_asks_for_long_enriched_answers(tmp_path):
    llm = FakeLLM()
    result = RetrievalResult((CHUNK,), True, None, {})
    make_service(tmp_path, result, llm).handle(MESSAGE)
    assert llm.last_max_tokens == 500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_chat_service.py -v`
Expected: FAIL (parámetros y flujo inexistentes). **Nota:** eliminar `test_generation_caps_completion_tokens_to_fit_budget` (queda superado por `test_generation_asks_for_long_enriched_answers`: el requisito aprobado pasó de respuestas breves a largas).

- [ ] **Step 3: Implementar** — `FakeRetriever` en tests registra `seen_question`. En `chat.py`: constructor con `interpreter`, `rewriter`, `total_budget_ms`; las tres constantes de fase pasan a calcularse desde `total_budget_ms` (20/65/10 %); `_MAX_COMPLETION_TOKENS = 500`; el prompt de generación recibe `message.text` como `Pregunta:` y la consulta reescrita solo entra al `retrieve(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_chat_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/services/chat.py tests/test_chat_service.py
git commit -m "feat: pipeline interpretado y presupuesto configurable en ChatService"
```

---

### Task 6: Prompt enriquecido

**Files:**
- Modify: `src/geoassist/rag/prompt.py`
- Test: `tests/test_prompt.py`

**Interfaces:**
- Produces: `SYSTEM_PROMPT` con: procedimiento **completo** (todos los pasos del contexto, sin omitir ninguno), cada paso enriquecido con la explicación del contexto, formato paso a paso numerado, citas `[n]`, sin preámbulos ni frases de relleno, abstención solo si el contexto no cubre el caso.

- [ ] **Step 1: Write the failing test** (reemplaza `test_system_prompt_requests_brief_answers`, que contradice el nuevo requisito)

```python
def test_system_prompt_requests_complete_enriched_procedures():
    from geoassist.rag.prompt import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "completo" in lowered
    assert "todos los pasos" in lowered
    assert "4-5 oraciones" not in lowered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prompt.py -k complete -v`
Expected: FAIL.

- [ ] **Step 3: Reescribir `SYSTEM_PROMPT`** con los requisitos de la interfaz, conservando las reglas existentes de citas, contexto `<contexto>`, contenido no confiable, abstención y no-alucinación.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prompt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/geoassist/rag/prompt.py tests/test_prompt.py
git commit -m "feat: prompt de procedimiento completo y enriquecido"
```

---

### Task 7: Wiring, jerga, compose, .env y verificación end-to-end

**Files:**
- Modify: `src/geoassist/main.py`, `config/corpus_rules.yml`, `compose.yml`, `.env`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: todo lo anterior. `main.py` construye `LocalQueryInterpreter` (alias de `rules.aliases()` + vocabulario de `CorpusReader.vocabulary()`) y `QueryRewriter` (llm existente, `enabled=settings.query_rewrite_enabled`, `cache_size=settings.query_rewrite_cache`, `timeout_s=settings.query_rewrite_timeout_ms / 1000`) y los pasa a `ChatService` junto a `total_budget_ms=settings.total_budget_ms`.

- [ ] **Step 1: Jerga en `config/corpus_rules.yml`** — ampliar aliases: `geopos: ["geoconsola", "consola", "pos"]`, `geoinventarios: ["inventario", "inventarios", "inventary"]`, `geopricer: ["geoprice", "geopricer", "price", "pricer"]`, `geopromotion: ["promocion", "promociones", "promo"]` (valores normalizados; verificar contra el archivo existente y unificar su formato).

- [ ] **Step 2: Wiring en `main.py`** según la interfaz de arriba.

- [ ] **Step 3: `compose.yml`** — montar `./config:/app/config:ro` en los servicios `api` y `sync` (la jerga se ajusta sin reconstruir imágenes).

- [ ] **Step 4: `.env` / `.env.example`** — `QUERY_REWRITE_ENABLED=true`, `QUERY_REWRITE_CACHE=128`, `QUERY_REWRITE_TIMEOUT_MS=1200`, `TOTAL_BUDGET_MS=8000`, `LLM_TIMEOUT_MS=4500`. Tabla del README actualizada.

- [ ] **Step 5: Verificación**

Run: `make check`
Expected: PASS (suite completa).
Run: rebuild de `geoassist-api` + `make up` + `/ready` → `ready: true`.
Run: prueba e2e por `/webhook` con preguntas con jerga y typos ("la geoconsola no carga", "necsito pasos para la devoLucion de cedis"): respuestas largas con pasos completos y citas, latencia < 9 s.
Expected: el retrieval acepta la jerga; la respuesta contiene el procedimiento completo.

- [ ] **Step 6: Commit**

```bash
git add src/geoassist/main.py config/corpus_rules.yml compose.yml .env.example README.md
git commit -m "feat: wiring de QueryWriter, jerga configurable y presupuesto ampliado"
```

**Nota de operación:** el usuario valida en Teams con `TOTAL_BUDGET_MS=8000`; si Teams corta la respuesta (> 5 s reales), bajar el env a `5000` y ajustar `LLM_TIMEOUT_MS=3000` sin tocar código.
