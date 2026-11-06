# syntax=docker/dockerfile:1.7
#
# Contenedor de sincronización del corpus.
#
# Descarga y verifica el artefacto de modelos durante el build, instala Git,
# pandoc y TeX Live para la generación opcional de PDFs, y ejecuta
# ``python -m geoassist.sync`` por defecto.

ARG PYTHON_IMAGE=python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

# ---------------------------------------------------------------------------
# 1. Descarga y verificación del artefacto de modelos.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS model-artifact

ARG MODELS_ARTIFACT_URL
ARG MODELS_ARTIFACT_SHA256

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl zstd \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    test -n "${MODELS_ARTIFACT_URL}"; \
    printf '%s' "${MODELS_ARTIFACT_SHA256}" | grep -Eq '^[0-9a-fA-F]{64}$'; \
    curl \
        --fail \
        --location \
        --silent \
        --show-error \
        --retry 3 \
        --retry-delay 2 \
        --output /tmp/models.tar.zst \
        "${MODELS_ARTIFACT_URL}"; \
    printf '%s  %s\n' "${MODELS_ARTIFACT_SHA256}" /tmp/models.tar.zst | sha256sum -c -; \
    mkdir -p /app/models; \
    tar \
        --zstd \
        --extract \
        --file /tmp/models.tar.zst \
        --directory /app/models \
        --strip-components=1; \
    rm -f /tmp/models.tar.zst

# ---------------------------------------------------------------------------
# 2. Instalación de dependencias con el lockfile.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --no-install-project --extra sync

COPY src ./src
RUN uv sync --frozen --no-default-groups --extra sync

# ---------------------------------------------------------------------------
# 3. Runtime con Git, SSH, pandoc y TeX Live.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        lmodern \
        openssh-client \
        pandoc \
        texlive-fonts-recommended \
        texlive-lang-spanish \
        texlive-latex-base \
        texlive-latex-extra \
        texlive-latex-recommended \
        zstd \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 geoassist \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --home-dir /app \
        --no-create-home \
        --shell /usr/sbin/nologin \
        geoassist \
    && mkdir -p /app /data /manuals \
    && chown 10001:10001 /app /data /manuals

WORKDIR /app

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/src /app/src
COPY --from=model-artifact --chown=10001:10001 /app/models /app/models
COPY --chown=10001:10001 config /app/config
COPY --chown=10001:10001 migrations /app/migrations

USER 10001:10001

CMD ["python", "-m", "geoassist.sync"]
