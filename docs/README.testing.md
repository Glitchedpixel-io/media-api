# Testing guide for media-api

## Prerequisites

- Python 3.13.x
- [`uv`](https://docs.astral.sh/uv/)
- Postgres, for the contract and integration suites (see [Database](#database))

Install dependencies, including the dev group:

```bash
uv sync
```

## Running the tests

`APP_ENV=test` is **mandatory**. `tests/conftest.py` calls `pytest.exit()` without it,
so the run stops before collecting anything.

```bash
export APP_ENV=test
export TEST_DATABASE_URL=postgresql+psycopg://testuser:testpass@localhost:5432/testdb

uv run pytest                      # everything — 1256 tests
uv run pytest tests/unit           # 971 tests, no database needed
uv run pytest tests/contracts      # 134 tests, needs Postgres
uv run pytest tests/integration    # 151 tests, needs Postgres
```

Coverage runs automatically — `--cov=app` and `--cov-fail-under=85` are set in
`pyproject.toml` under `[tool.pytest.ini_options].addopts`, so a run that dips below
85% fails even when every test passes. Pass `--no-cov` to skip it while iterating.

`LOGFIRE_IGNORE_NO_CONFIG=1` silences the warning Logfire emits when no token is
configured. CI sets it; it is optional locally.

### Do not use `pytest -n auto`

`pytest-xdist` is installed but the suite is **not** parallel-safe. The schema is built
with `Base.metadata.create_all()` against a single shared database, so workers race —
one drops tables while another is mid-test:

```
psycopg.errors.UndefinedTable: table "external_identifiers" does not exist
```

Serial: 1256 passed. `-n auto`: 2 failed, 978 passed, 125 errors. Run the suite serially
until per-worker database isolation exists.

## Database

The URL is read from `TEST_DATABASE_URL`, falling back to `DATABASE_URL` — see
`AliasChoices` in `app/config/settings.py`. There is no SQLite mode and no default that
works out of the box: with neither variable set the config falls back to the placeholder
`postgresql+psycopg://user:secret@localhost:5432/media_dev`, and every database-backed
test errors with `psycopg.OperationalError: connection refused`. (`tests/conftest.py`
also raises `ValueError: No database url provided`, but only if the URL resolves to an
empty string.)

`tests/unit` is the exception — it does not touch the database and passes with neither
variable set.

Any reachable Postgres will do. For a throwaway instance:

```bash
docker run -d --name media-api-testdb \
  -e POSTGRES_USER=testuser -e POSTGRES_PASSWORD=testpass -e POSTGRES_DB=testdb \
  -p 5432:5432 postgres:17
```

Tables are created from the ORM models via `create_all` in `tests/conftest.py`, not by
running migrations. Alembic is the source of truth for the real schema; CI has a separate
gate (`alembic upgrade head` then `alembic check`) that fails if the two drift. See the
"Database Migrations" section of `CLAUDE.md`.

## Layout and markers

| Directory | Tests | Database |
|---|---|---|
| `tests/unit` | 971 | not required |
| `tests/contracts` | 134 | Postgres |
| `tests/integration` | 151 | Postgres |

Markers in active use — select with `-m`:

| Marker | Selects | Meaning |
|---|---|---|
| `unit` | 953 | fast, no I/O; repositories and services mocked at protocol boundaries |
| `api` | 358 | FastAPI layer, dependency overrides, httpx ASGI transport |
| `contract` | 134 | repository protocol conformance across implementations |
| `integration` | 151 | database, migrations, SQL behaviour |

The marker counts overlap and do not sum to 1256 — 214 tests carry both `unit` and `api`,
for instance — so use them to select, not to account for coverage of the suite.

```bash
uv run pytest -m unit
uv run pytest -m "not integration"
```

`pyproject.toml` also registers `e2e`, `worker` and `slow`, but no test currently uses
them. They are kept so the markers stay available and `--strict-markers` does not reject
them.

## Mutation testing (optional)

`mutmut` is in the dev group but the project has **no `[tool.mutmut]` configuration**, so
there is nothing to run out of the box. Note that mutmut 3.x removed the
`--paths-to-mutate`, `--tests-dir` and `--runner` flags that older guides use; `mutmut run`
now takes only `--max-children` and reads its configuration from `pyproject.toml`.
Configuring it is unfinished work, not a supported workflow.

## Expected timings

On a reasonable machine, against a local Postgres:

- `tests/unit` — about 30s
- Full suite with coverage — about 2 minutes
