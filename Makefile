SHELL := /bin/bash

COMPOSE ?= docker compose
UV ?= uv
VERSION ?= 1.0.0

.PHONY: build up down logs test lint shell db publish-models sync sync-status activate

## Construye la imagen de la API con el artefacto de modelos verificado.
build:
	$(COMPOSE) build api

## Levanta la API; añade COMPOSE_PROFILES=prod o dev para caddy/ngrok.
up:
	$(COMPOSE) up -d api

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api

test:
	$(UV) run pytest

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run mypy src

## Shell dentro del contenedor, sin arrancar la API.
shell:
	$(COMPOSE) run --rm --no-deps --entrypoint /bin/sh api

## Consola SQLite sobre la base de estado.
db:
	$(COMPOSE) exec api python -m sqlite3 /data/state/app.db

## Exporta/empaqueta el artefacto de modelos. Usa EXPORT=1 para exportar ONNX.
publish-models:
	$(UV) run python scripts/publish_models.py \
		--version $(VERSION) \
		--output-dir dist \
		$(if $(filter 1,$(EXPORT)),--export,)

## Sincroniza el corpus desde el repositorio de manuales.
sync:
	$(COMPOSE) --profile tools run --rm sync

## Muestra el último reporte de sincronización.
sync-status:
	$(COMPOSE) --profile tools run --rm sync cat /data/corpus/sync_report.json

## Activa una versión publicada (rollback). Uso: make activate VERSION=<id>.
activate:
	$(COMPOSE) --profile tools run --rm sync python -m geoassist.sync activate $(VERSION)
