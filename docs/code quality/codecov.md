# Codecov Integration

This document describes how `media-api` reports **coverage** and **test analytics**
to [Codecov](https://about.codecov.io/), and how to apply the same pattern to another
repo in our org. It is written for engineers and AI coding agents — follow the steps
exactly and adapt the repo-specific values noted in **Step 0**.

## What you get

- **Coverage** — line/branch coverage uploaded on every push and PR; Codecov posts a
  PR comment and project/patch status checks.
- **Test Analytics** — per-test pass/fail/timing data uploaded from a JUnit XML report;
  Codecov surfaces flaky tests and failure trends in its UI (no separate badge).
- **Badges** — a coverage badge (and a companion CI badge) in `README.md`.

## Prerequisites

- The project already runs `pytest` with [`pytest-cov`](https://pytest-cov.readthedocs.io/)
  (a dev dependency). Coverage source is configured via `--cov=<pkg>` in
  `pyproject.toml`'s `[tool.pytest.ini_options].addopts`.
- The repo is enabled in Codecov and has an **upload token** stored as the
  `CODECOV_TOKEN` GitHub Actions secret (org-level or repo-level).
- You know the repo's **graph badge token** (distinct from the upload token; it is safe
  to commit and only grants read access to the badge SVG). Find it in
  Codecov → repo → Settings → Badges & Graphs.

## Step 0 — Gather repo-specific values

| Value | `media-api` example | Where to find it |
|---|---|---|
| `owner/repo` | `Glitchedpixel-io/media-api` | `git remote -v` |
| Coverage source package | `app` | the `--cov=` flag in `pyproject.toml` |
| Upload secret name | `CODECOV_TOKEN` | repo/org Actions secrets |
| Badge token | `U1UED7P3SX` | Codecov → Settings → Badges & Graphs |
| CI workflow filename | `ci.yml` | `.github/workflows/` |

Substitute these everywhere the snippets below use the `media-api` values.

## Step 1 — Emit the report artifacts in CI

Codecov needs two files from the test run:

- `coverage.xml` — Cobertura format, for coverage (`--cov-report=xml`).
- `junit.xml` — JUnit format, for test analytics (`--junitxml` + `junit_family=legacy`,
  which is the dialect Codecov parses).

In the workflow's test step, append these flags to the existing `pytest` invocation.
**Do not** put `--cov-report=xml`/`--junitxml` permanently in `pyproject.toml`'s
`addopts` — keep CI-only artifacts in the workflow so local runs stay clean.

```yaml
- name: Run tests
  run: >-
    uv run pytest tests/
    --maxfail=0
    --cov-report=xml
    --junitxml=junit.xml
    -o junit_family=legacy
```

**`--maxfail=0` is important.** If `addopts` sets `--maxfail=1` (as `media-api` does),
the suite stops at the first failure and test analytics only ever sees a partial run.
The command-line flag overrides `addopts`, so the full suite runs and Codecov gets every
result. `--cov=<pkg>` is inherited from `addopts`, so it is not repeated here.

## Step 2 — Upload to Codecov

Add two steps **after** the test step. Both use `codecov/codecov-action@v7`; the test
results upload is selected with `report_type: test_results`. (The old
`codecov/test-results-action` is deprecated — its functionality folded into
`codecov-action`, so do not use it.)

```yaml
- name: Upload coverage to Codecov
  uses: codecov/codecov-action@v7
  if: ${{ !cancelled() }}
  with:
    token: ${{ secrets.CODECOV_TOKEN }}
    files: ./coverage.xml

- name: Upload test results to Codecov
  uses: codecov/codecov-action@v7
  if: ${{ !cancelled() }}
  with:
    token: ${{ secrets.CODECOV_TOKEN }}
    report_type: test_results
    files: ./junit.xml
```

`report_type` defaults to `coverage`, so the first step omits it; the second sets
`test_results` to upload the JUnit report. Pin to `@v7` (or later) — older majors run on
a deprecated Node runtime and emit a warning.

**`if: ${{ !cancelled() }}` is deliberate.** Both uploads must run even when tests fail
or a coverage gate (e.g. `--cov-fail-under=85`) makes pytest exit non-zero — that is
precisely when the data is most useful. `!cancelled()` runs the step on success *and*
failure, but skips it if the job was manually cancelled.

### Container gotcha

If the job runs inside a container (e.g. `ghcr.io/astral-sh/uv:...-slim`), the Codecov
actions shell out to download their uploader CLI and need `curl`, `gpg`, and `git`. Slim
images often lack these. Ensure they are installed earlier in the job — `media-api`
does `apt-get install -y git curl gnupg` for exactly this reason. Omitting `curl`/`gnupg`
makes the upload steps fail to bootstrap the uploader CLI even though tests pass.

## Step 3 — Add `codecov.yml`

Commit a `codecov.yml` at the repo root to pin status-check behavior (otherwise Codecov
uses opaque defaults that can change):

```yaml
coverage:
  status:
    project:
      default:
        target: auto        # compare against the base commit
        threshold: 1%        # allow a 1% drop without failing the check
    patch:
      default:
        target: 85%          # new/changed lines must hit 85% coverage

comment:
  layout: "reach, diff, flags, files"
```

Tune `patch.target` to match the project's `--cov-fail-under` so PR feedback and the
local gate agree.

## Step 4 — Add badges to `README.md`

Place near the top, under the title. The coverage badge embeds the **badge token**, not
the upload secret:

```markdown
[![codecov](https://codecov.io/gh/Glitchedpixel-io/media-api/graph/badge.svg?token=U1UED7P3SX)](https://codecov.io/gh/Glitchedpixel-io/media-api)
[![CI](https://github.com/Glitchedpixel-io/media-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Glitchedpixel-io/media-api/actions/workflows/ci.yml)
```

Test analytics has no standalone badge; it lives in the Codecov UI. The coverage badge
is the relevant one, with the CI status badge as a natural companion.

## Verifying

1. Open a PR. The CI job should run, produce `coverage.xml` and `junit.xml`, and both
   upload steps should report success in the logs.
2. Within a minute or two, Codecov posts a coverage comment and project/patch status
   checks on the PR.
3. After merge to the default branch, the README coverage badge resolves to a percentage
   and the Codecov **Tests** tab populates with per-test analytics.

## Troubleshooting

- **"Token required" / rate-limited upload** — `CODECOV_TOKEN` is missing or not exposed
  to the job. Confirm the secret exists and (for forked PRs) note that secrets are not
  available to fork-originated workflows.
- **No test analytics** — the JUnit file is empty or in the wrong dialect. Confirm
  `junit_family=legacy` and that `--maxfail` did not abort the run early.
- **Badge shows "unknown"** — no report has landed on the default branch yet, or the
  badge token is wrong. Re-copy it from Codecov → Settings → Badges & Graphs.
- **Upload step fails to download the CLI** — missing `curl`/`gpg` in a slim container
  image; see the container gotcha in Step 2.
