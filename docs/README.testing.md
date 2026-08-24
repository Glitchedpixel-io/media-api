Testing guide for media-api

Prerequisites
- Python 3.13.x
- Poetry >= 2.1.x with in-project virtualenvs

Install dev dependencies
- poetry install

Run tests
- All tests (auto-parallel): pytest -n auto
- Unit only: pytest -q tests/unit
- Integration only: pytest -q tests/integration
- E2E smoke: pytest -q tests/e2e
- Exclude slow tests: pytest -m "not slow"

Coverage
- Configured via pyproject to fail under 64%: --cov=app --cov-report=term-missing:skip-covered --cov-fail-under=85

Databases & services
- Default tests run on an in-memory SQLite database with per-test savepoints; repositories commit as normal but outer transaction rolls back after each test.
- To run against real Postgres/Elasticsearch for heavier integration:
  - docker compose -f tests/docker-compose.tests.yml up -d
  - export DATABASE_URL=postgresql+psycopg://test:test@localhost:5433/media_test
  - pytest -q tests/integration -m "not slow"

Auth & Elasticsearch
- Current API does not enforce JWT auth at the router layer; auth tests are stubbed as TODO when auth dependency is added.
- Elasticsearch is not directly referenced in app code; ES tests are omitted for now and can be added when integration endpoints exist.

Mutation testing (optional)
- mutmut installed optionally. Quick run (may be slow): mutmut run --paths-to-mutate app --tests-dir tests --runner "pytest -q"

Expected timings
- Unit+integration on SQLite: typically < 10s on a modern laptop.
- With Postgres+ES services: may take longer due to service startup; app tests themselves remain fast.
