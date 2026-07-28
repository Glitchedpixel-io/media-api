# media-api

[![Tests](https://github.com/Glitchedpixel-io/media-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Glitchedpixel-io/media-api/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Glitchedpixel-io/media-api/graph/badge.svg?token=U1UED7P3SX)](https://codecov.io/gh/Glitchedpixel-io/media-api)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted backend API for managing a media library — video and a selection of
audio types. Media metadata (titles, assets, tags, external IDs, transcripts) is
stored in PostgreSQL; the media files themselves, and any accessory files that go
with them, live on the filesystem.

Built with FastAPI + SQLAlchemy. Ships as a single Docker image with Alembic-managed
migrations, generic OIDC authentication, and an optional pluggable job-execution
backend for offloading work like transcoding or transcription.

## Features

- **Titles & assets** — group video/audio assets under a title, with metadata,
  tags, and external IDs (e.g. linking to TMDB/IMDB-style ID schemes).
- **Accessory files** — subtitles, cover art, soundtracks, and other files
  associated with an asset, stored alongside it and keyed by asset ID.
- **Transcript search** — optional full-text search over transcripts via
  Elasticsearch; the app boots and runs fine without it configured.
- **Transform requests** — a DB-backed pull queue for background work (e.g.
  transcoding, transcription), with an optional signal to an external runner.
- **OIDC authentication** — bearer-token auth against any standard OIDC
  provider, with a stub-principal bypass for local development.
- **Interactive API docs** — Swagger UI at `/docs` and OpenAPI schema at
  `/openapi.json` once the app is running.

## Quickstart (Docker Compose)

The fastest way to get a working instance running locally:

```bash
git clone https://github.com/Glitchedpixel-io/media-api.git
cd media-api
docker compose up -d db
uv run alembic upgrade head
docker compose up -d
```

This starts a local Postgres instance and the API with authentication disabled
(no OIDC provider required), listening on `http://localhost:8000`. Migrations
run against `postgresql+psycopg://media:media@localhost:5432/media`, matching
the `db` service above — see the comment at the bottom of
[`docker-compose.yml`](docker-compose.yml).

Open `http://localhost:8000/docs` for interactive API docs.

## Local development

Requires Python 3.11–3.13 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env.development   # edit as needed
uv run alembic upgrade head
uv run uvicorn app.main:api --reload
```

Run the test suite (requires a running Postgres instance, pointed to by
`TEST_DATABASE_URL`):

```bash
uv run pytest tests/
```

## Configuration

All configuration is via environment variables — see
[`.env.example`](.env.example) for the full list with descriptions. Notable
groups:

- **Database** — `DATABASE_URL` (runtime role) and optionally
  `ALEMBIC_DATABASE_URL` (migration role). See
  [`docs/database-permissions.md`](docs/database-permissions.md) for the
  `GRANT` statements needed to set up a properly scoped role.
- **Auth** — `OIDC_ISSUER` / `OIDC_AUDIENCE` / `OIDC_JWKS_URL` for your
  provider, or `AUTH_DISABLED=true` for local development (refused at
  startup if `APP_ENV=production`).
- **Filesystem roots** — `MEDIA_ROOT`, `ACCESSORY_ROOT`, `INBOX_ROOT`.
- **Job execution** — `RUNNER_BACKEND` (`none` by default — no
  orchestration framework required to boot), plus `RUNNER_WEBHOOK_URL` /
  `RUNNER_JOB_ROUTING_MAP` for the optional backends described below.
- **Elasticsearch** (optional) — `ELASTICSEARCH_URL` and friends, for
  transcript search.
- **Logfire** (optional) — `LOGFIRE_TOKEN`, for observability.

## Job execution backends

Runners live in a separate project; this API only enqueues work (the
DB-backed pull queue) and *optionally* signals a backend that work is ready.
The backend is selected with `RUNNER_BACKEND` (default `none`, i.e. a pure
pull model). Prefect is one optional adapter
(`pip install media-api[prefect]`, `RUNNER_BACKEND=prefect`); a generic
`webhook` adapter is also provided.

## Database migrations

Alembic migrations are the single source of truth for the schema — a fresh
deployment must run `uv run alembic upgrade head` as a deploy step (the app
does not create tables at startup). See the "Database Migrations" section of
[`CLAUDE.md`](CLAUDE.md) for the full model.

## Deployment

The API is designed to be bundled into a Docker container and deployed to a
host. The [`Dockerfile`](Dockerfile) and
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) GitHub
Actions workflow are set up to build and publish that image.

## Contributing & Security

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for dev setup, coding standards, and
the PR process; deeper development conventions (layout, testing, config,
dependency injection) are documented in [`CLAUDE.md`](CLAUDE.md). Please also
read the [Code of Conduct](CODE_OF_CONDUCT.md). To report a security
vulnerability, see [`SECURITY.md`](SECURITY.md) instead of filing a public
issue.

## License

[MIT](LICENSE)

<!-- ci smoke test -->
