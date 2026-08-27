# media-api

[![Tests](https://github.com/Glitchedpixel-io/media-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Glitchedpixel-io/media-api/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Glitchedpixel-io/media-api/graph/badge.svg?token=lJf4cmHif7)](https://codecov.io/gh/Glitchedpixel-io/media-api)
[![Python](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/)
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

Requires Python 3.13 and [`uv`](https://docs.astral.sh/uv/).

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
- **Filesystem roots** — `MEDIA_ROOT`, `ACCESSORY_ROOT`, `INBOX_ROOT`,
  `ARTWORK_ROOT`. Artwork is laid out content-addressed
  (`<ab>/<cd>/<sha256>.<ext>`) rather than keyed by the title or asset it
  belongs to, so a poster shared across a season and its episodes is stored
  once. Nothing serves these files over HTTP yet.
- **Job execution** — `ENABLED_ORCHESTRATION_PROVIDERS` (empty by default —
  no orchestration framework required to boot), plus
  `ORCHESTRATION_PROVIDER_OPTIONS` for provider-scoped config such as the
  webhook adapter's URL. See below.
- **Elasticsearch** (optional) — `ELASTICSEARCH_URL` and friends, for
  transcript search.
- **Logfire** (optional) — `LOGFIRE_TOKEN`, for observability.

## Job execution backends

Runners live in a separate project; this API only enqueues work (the
DB-backed pull queue) and *optionally* signals a backend that work is ready.
Backends are pluggable orchestration providers, discovered from the
`media_api.orchestration_providers` Python entry-point group and only
instantiated if named in `ENABLED_ORCHESTRATION_PROVIDERS` (empty by default,
i.e. a pure pull model; comma-separated, e.g. `prefect,webhook`). A
provider that's enabled but unavailable (missing entry point, missing
optional dependency, incompatible API version) fails startup with a clear
error rather than degrading silently — so enabling a provider whose optional
dependency isn't installed aborts startup rather than warning. Two adapters
ship built in: Prefect (needs the `media-api[prefect]` extra) and a generic
`webhook` dispatcher (no extra install, configured via
`ORCHESTRATION_PROVIDER_OPTIONS`, e.g.
`{"webhook": {"url": "https://example.com/hook"}}`).

### Image flavours

The core product depends on no orchestration framework — that's what the
pluggable registry is for — so the default image installs no orchestration
extra. Enabling a provider whose package isn't present aborts startup, so an
extra that a deployment needs is shipped as a *separate artifact* rather than
added to the default build:

| tag | contents | use |
| --- | --- | --- |
| `:<version>`, `:latest` | no orchestration framework | pure pull model, or the `webhook` provider |
| `:<version>-prefect`, `:latest-prefect` | adds `media-api[prefect]` | deployments enabling the `prefect` provider |

Both are built from this Dockerfile; the flavour is selected with the `EXTRAS`
build arg (`--build-arg EXTRAS=prefect`). A deployment picks the tag matching
the providers it enables. Publish-time checks assert that the core image
contains no orchestration framework and that the flavoured image can construct
the providers it ships.

Routing is decided by the request, not server config: `transform_type` is a
provider-qualified routing key, `<provider>.<provider-local-type>` (e.g.
`prefect.transcode`). The API validates only the shape — no allow-list of
providers or job names — so adding a new Prefect deployment needs no API
release. The key is split on the first `.`; everything after is forwarded
verbatim as that provider's own vocabulary and used to resolve both dispatch
and log retrieval to the matching adapter. A request whose provider isn't
enabled remains persistable — dispatch is a logged no-op and log retrieval
returns an empty result, rather than failing the request.

The Prefect adapter takes an optional `deployments` map, because Prefect
identifies a deployment as `<flow name>/<deployment name>` and routing keys
forbid whitespace — so a deployment called `Extract Audio` is unreachable
without translation:

```
ORCHESTRATION_PROVIDER_OPTIONS='{"prefect": {"deployments": {
  "transcode": "transcode-flow/Transcoder",
  "extract_audio": "extract-audio-flow/Extract Audio"
}}}'
```

The map is provider-scoped, not core config: the core still never interprets a
provider-local command — the adapter that owns Prefect's vocabulary is the one
that resolves it. An unmapped command is passed through verbatim, so the map is
optional, and a command that can't resolve to a `<flow>/<deployment>` identifier
is logged as a warning rather than failing quietly.

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
