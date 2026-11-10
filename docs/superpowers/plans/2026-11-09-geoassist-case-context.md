# Contexto a nivel de caso — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el detalle de la respuesta no dependa de la frase con la que se pregunta: cualquier forma de preguntar por un caso entrega su procedimiento completo.

**Architecture:** El ensamblado del contexto pasa de nivel de fragmento a nivel de caso: el documento ganador (top-1 tras rerank) aporta todos sus chunks candidatos en orden de lectura (hasta `max_case_chunks=6`), los documentos secundarios conservan el tope de 3/doc y el total se acota a `final_limit=8`. La suficiencia/abstención se calcula igual que antes sobre el top rerankeado.

**Tech Stack:** Python 3.12, pytest, FAISS/FTS5/reranker existentes.

**Spec:** Decisión del humano (2026-11-09): "Opción A — contexto a nivel de caso", tras observar que el detalle dependía de la frase ("paso a paso" vs. preguntas casuales) por `max_per_doc=3` + `final_limit=5`.

## Global Constraints

- Suficiencia/abstención y `min_score` sin cambios (se calculan sobre el top rerankeado previo al ensamblado).
- Orden de lectura para los chunks del caso ganador (rowid ascendente = orden de ingesta).
- Tope de contexto acotado para no desbordar el presupuesto de latencia: caso ganador ≤ 6 chunks, total ≤ 8.

## Review Focus

1. Un manual con 10+ chunks candidatos no debe desbordar el prompt (tope `max_case_chunks`). Test: `test_winner_case_context_is_capped`.
2. Documentos secundarios conservan diversidad (≤ 3/doc). Test: `test_secondary_docs_stay_capped_at_three`.
3. Los chunks del ganador llegan en orden de lectura (coherencia del procedimiento). Test: `test_winner_chunks_are_in_reading_order`.

---

### Task 1: `_assemble_case_context` en `RetrievalPipeline`

**Files:**
- Modify: `src/geoassist/rag/retriever.py`
- Test: `tests/test_retriever.py`

**Interfaces:**
- Produces: `Retriever(..., final_limit=8, max_case_chunks=6)`.
- Produces: `Retriever._assemble_case_context(final, candidates) -> list[tuple[FusedHit, ChunkView, float | None]]`.

- [x] Step 1: Tests RED (`test_winner_case_gets_all_its_candidate_chunks`, `test_winner_case_context_is_capped`, `test_winner_chunks_are_in_reading_order`, `test_secondary_docs_stay_capped_at_three`) — FAIL; el test `test_max_three_chunks_per_document` queda superado y se reemplaza.
- [x] Step 2: Implementar `_assemble_case_context` (ganador por orden de lectura + secundarios acotados + tope total).
- [x] Step 3: Suite verde (211/211) + lint.

### Task 2: Verificación end-to-end

- [x] Step 1: rebuild + 3 formas de preguntar el mismo caso (formal/casual/vaga+typo) → detalle comparable (1495/1193/1065 chars, pasos completos en las tres).
- [x] Step 2: Commit.
