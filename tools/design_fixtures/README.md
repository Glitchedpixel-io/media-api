# Design fixture generator

Captures real API response bodies and writes them to disk **verbatim**, so front-end
design work has true response shapes to build against instead of invented data.

```bash
uv run design-fixtures --database-url "postgresql+psycopg://user:pass@host:5432/db"
```

Fixtures go to `--out`, which falls back to `$DESIGN_FIXTURES_OUT` and then to
`./design-fixtures`. Point it at the fixture directory of whichever project consumes
them — setting `$DESIGN_FIXTURES_OUT` once is usually easier than passing the path
every run.

A relative `--out` resolves against the current directory, so a path meant as a sibling
of the repository lands somewhere else entirely when the command is run from inside
`.claude/worktrees/<name>`. The tool prints the absolute directory it resolved and warns
if it lands inside a worktree, so a wrong run is visible rather than silent.

The DSN can also come from `$DESIGN_FIXTURES_DATABASE_URL`. Use a read-only role.

## What it guarantees

- **Read-only.** The only way to reach the API is `FixtureCapture.get`, and
  `Selectors._rows` refuses any statement that is not a `SELECT` or a `WITH`.
- **Verbatim.** Bodies are written with `write_bytes` exactly as returned — nothing is
  parsed, re-serialised, prettified, sorted or newline-terminated on the way to disk.
  Where a case needs a cursor or an id, it parses an in-memory copy that never reaches
  a file.
- **No server, no port.** The app runs in process through Starlette's `TestClient`, so
  the tool needs nothing running and is safe alongside another session in the same
  repository.

## How records are chosen

Where the API can express the query, the fixture is a single API response. Where it
cannot — "roots with no release year", "assets belonging to no title", "the asset with
the most streams" — the ids are chosen by a `SELECT` and each record is then fetched
through the API, so the fixture is still a real API response shape. Those cases produce
a **directory of individual detail bodies, not a list page**, which `manifest.md` states
per fixture so nothing wires the wrong shape by accident.

Per-record sets are capped by `--max-records` (default 50) and taken in id order, so a
re-run reproduces the same set. The manifest records the true total beside the captured
count, so a capped fixture is never mistaken for a complete one.

## Output

Fixtures, plus `manifest.md`: one line per fixture giving the filename, what it is, how
it was selected and its record count; the measured totals behind the selections; any
fixture over 1 MB (reported, never truncated); and a section for cases that matched no
data — an empty result is a finding about the library, not a gap to leave out.

## Configuration it sets

`APP_ENV=development` and `AUTH_DISABLED=true` (the API is bearer-authenticated; the
dev bypass is refused under `APP_ENV=production`), and **both** `DATABASE_URL` and
`TEST_DATABASE_URL` to the given DSN — the settings loader resolves the database with
`AliasChoices("TEST_DATABASE_URL", "DATABASE_URL")`, so a `TEST_DATABASE_URL` left in
the shell profile would otherwise win silently and the capture would run against the
wrong database while still looking healthy. It smoke-tests `GET /api/title_types`
before capturing anything, because `/api/ping` answers without touching the database
and would pass against a misconfigured instance.
