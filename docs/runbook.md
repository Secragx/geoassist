# Runbook operativo de GeoAssist

## Requisitos del host

- Docker y Docker Compose instalados.
- Acceso a la red y secretos configurados en `.env`.
- Artefacto de modelos disponible (URL + SHA-256) para producción.

## Primer `make sync`

1. Clonar el repositorio de manuales y configurar `MANUALS_DIR`.
2. Ejecutar `make sync` para construir la primera versión del corpus.
3. Verificar `make sync-status` y revisar `sync_report.json`.

## Registro del webhook en Teams

- Configurar `TEAMS_SECURITY_TOKEN` y `TEAMS_BOT_NAME`.
- Registrar la URL pública del servicio como outgoing webhook en Teams.
- Validar con una pregunta de prueba.

## Rotación de secretos

- Actualizar `.env` y reiniciar el servicio.
- Rotar tokens de Teams, LLM y git de sincronización de forma coordinada.
- Revocar credenciales antiguas tras verificar `/ready`.

## Política `LLM_DATA_POLICY`

- `external_approved`: permite LLM externo aprobado.
- `on_prem_only`: exige host en `LLM_ALLOWED_HOSTS`.
- En producción la política es obligatoria.

## Rollback con `make activate`

- Listar versiones disponibles en `/data/corpus/versions`.
- Ejecutar `make activate VERSION=<id>`.
- Verificar `/health` y `/ready`.

## Lectura de `sync_report.json`

- Revisar `doc_count`, `chunk_count`, `failed_pdfs` y `status`.
- Un código de salida 2 indica PDFs fallidos, pero la versión queda activa.

## Diagnóstico de `/health` y `/ready`

- `/health`: liveness del proceso.
- `/ready`: verifica corpus activo y modelos cargados.
- Ante fallos, revisar logs estructurados y estado de la base.

## Qué hacer ante PDFs fallidos

- Revisar `sync_report.json` para la lista de archivos fallidos.
- Corregir el Markdown o la plantilla y re-ejecutar `make sync`.
- Si persiste, activar la última versión estable con `make activate`.
