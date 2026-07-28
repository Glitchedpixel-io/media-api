FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update -y && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first so this layer is cached independently of source changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
# Version is injected, not read from git: .git is excluded via .dockerignore so
# hatch-vcs cannot derive a dirty/dev version from an incomplete in-image tree.
# The deploy workflow passes VERSION as the bare tag number (e.g. 0.3.3).
# Use the generic SETUPTOOLS_SCM_PRETEND_VERSION — hatch-vcs does not forward the
# dist name to setuptools-scm, so the per-dist *_FOR_<NAME> form is silently ignored.
# It is set as ENV (not just on the RUN) so it persists to runtime: any later
# hatchling build of the project (e.g. an implicit `uv sync`) can still resolve it.
ARG VERSION=0.0.0+local
ENV SETUPTOOLS_SCM_PRETEND_VERSION=$VERSION
RUN uv sync --frozen --no-dev

# --no-sync: the environment is already complete from the build above; do not
# re-sync at startup (which would reinstall dev deps and rebuild the project).
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:api", "--host", "0.0.0.0", "--port", "8000"]
