SHELL := /bin/bash

COMPOSE ?= docker compose
UV ?= uv
VERSION ?= 1.0.0

.PHONY: build up up-dev down logs test lint check eval shell db publish-models sync sync-status activate

## Construye la imagen de la API con el artefacto de modelos verificado.
build:
	$(COMPOSE) build api

## Levanta la API; añade COMPOSE_PROFILES=prod o dev para caddy/ngrok.
up:
	$(COMPOSE) up -d api

## Levanta API + ngrok persistente (perfil dev, dominio estático en .env).
up-dev:
	$(COMPOSE) --profile dev up -d api ngrok

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

check: lint
	$(UV) run pytest

eval:
	$(UV) run python eval/evaluate.py

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
	@test "$(origin VERSION)" = "command line" || (echo "ERROR: VERSION is required (use make activate VERSION=<id>)" >&2 && exit 1)
	$(COMPOSE) --profile tools run --rm sync python -m geoassist.sync activate $(VERSION)
