# Contributing to media-api

Thanks for your interest in contributing! This document covers how to set up
a dev environment, the coding standards we follow, and how changes land on
`main`.

By participating in this project you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Getting Started

1. Fork the repo and clone your fork.
2. Install [`uv`](https://docs.astral.sh/uv/) if you don't already have it.
3. Install dependencies: `uv sync`
4. Copy `.env.example` to `.env.development` and fill in local values.
5. Run the app: `uv run uvicorn app.main:api --reload`
6. Run the tests: `uv run pytest tests/` (requires a running Postgres — see
   `TEST_DATABASE_URL` in `CLAUDE.md`)

## Coding Standards

- **Package manager:** `uv`. Use `uv add <package>`, not `pip install`.
- **Formatter:** [black](https://github.com/psf/black). Run `uv run black .`
  before opening a PR.
- **Tests:** [pytest](https://docs.pytest.org/). New behavior should come
  with tests; bug fixes should include a regression test where practical.
- **Type hints:** required on all functions and methods.
- Follow the layout and conventions in `CLAUDE.md` — in particular, routers
  contain no business logic, services contain no direct DB access, and all
  DB queries go through `app/repositories/`.

## Submitting a Pull Request

- Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md) — it's applied
  automatically when you open a PR.
- Keep PRs focused. Unrelated cleanup makes review harder — open a separate
  PR instead.
- CI (`test` job) must be green before merge.
- **PRs are squash-merged only**, with a required linear history. Your PR
  title becomes the squash commit subject, so write it as you'd want it in
  the changelog.
- **Version bump markers:** this project's version comes from git tags,
  bumped automatically based on your **PR title**:

  | Marker in the PR title | Bump |
  |---|---|
  | `[major]` | major version bump |
  | `[minor]` | minor version bump |
  | _(neither — the default)_ | patch bump |

  Add `[major]` or `[minor]` to the title if your change warrants it;
  otherwise leave it out for a patch release.

## Database Changes

If you change anything under `app/models/`, you must add a matching Alembic
migration (`alembic revision --autogenerate -m "..."`). CI runs
`alembic upgrade head` against an empty database followed by `alembic check`
— a model change without a matching migration will fail CI. See the
Database Migrations section of `CLAUDE.md` for details.

## Reporting Bugs & Requesting Features

Use the [issue templates](.github/ISSUE_TEMPLATE/) — bug report or feature
request, whichever fits.

**Do not** file security vulnerabilities as public issues — see
[SECURITY.md](SECURITY.md) for the private reporting process.

## Project Governance

media-api currently has a single maintainer ([@virorum](https://github.com/virorum))
who reviews and merges all pull requests. There's no formal governance
process beyond that at this stage. GitHub Issues is the primary channel for
bug reports, feature requests, and questions — there's no separate
Discussions board, chat, or mailing list yet. This may change as the project
and its contributor base grow.
