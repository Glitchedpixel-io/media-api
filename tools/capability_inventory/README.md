# capability-inventory

Produces an annotated **capability inventory** of the media-api HTTP surface:
what each endpoint is, what it costs, what data is reliably present behind it,
and what a front end can responsibly build on it.

Two artefacts, both committed:

| File | For |
|---|---|
| `docs/capability-inventory.md` | humans and LLMs — one section per endpoint, ending in a **UI verdict** |
| `docs/capability-inventory.json` | machines — the same data, sorted and rounded so successive runs diff cleanly |

The raw OpenAPI document is the skeleton, not the answer. Everything that
decides whether an endpoint is usable in a UI — query cost, index coverage, fill
rate, pagination stability, streaming latency — is measured or read out of the
code.

## Running it

```bash
# Everything the repository alone can answer. No database, no server.
uv run capability-inventory --skip-db --skip-probes --skip-writes

# Add the data shape.
export CAPINV_DATABASE_URL='postgresql://readonly_user:...@host:5432/media'
uv run capability-inventory --skip-probes --skip-writes

# Everything except the writes, against a locally-running instance.
export CAPINV_BASE_URL='http://127.0.0.1:8000'
uv run capability-inventory --skip-writes

# Everything, including the write contracts. Needs a DISPOSABLE instance and
# its own DISPOSABLE database -- see "What has to be true" below.
export CAPINV_WRITE_BASE_URL='http://127.0.0.1:8077'
export CAPINV_WRITE_DATABASE_URL='postgresql+psycopg://user:pass@scratch:5432/capinv_write_scratch'
uv run capability-inventory --allow-writes
```

`python -m tools.capability_inventory` is equivalent to the console script.

## Flags

| Flag | Effect |
|---|---|
| `--skip-probes` | Skip Phase 4. No instance is contacted. Every **Measured** line reads `UNKNOWN` and says why. |
| `--skip-db` | Skip Phase 3. No database is contacted. Row counts, fill rates and collection sizes read `UNKNOWN`. |
| `--skip-writes` | Skip Phase 6. Nothing is written. Every **Write contract** reports what the code implies, and UNKNOWN for what only a request could settle. |
| `--allow-writes` | Permit Phase 6 to mutate the disposable target. Required *in addition to* the two write variables — configuration alone never authorises a write. |
| `--only PATTERN` | Restrict the report to routes whose path matches a glob, e.g. `--only '/api/assets/*'`. Every phase still runs, against the matching subset only. |
| `--frontend-path DIR` | Phase 5: grep a consumer checkout for call sites. Searches both URL literals and generated-client `operationId`s, and reports them separately. |
| `--access-log FILE` | Phase 5: parse an access log. Request paths are normalised back to route templates, so `/api/assets/4213` counts against `/api/assets/{asset_id}`. |
| `--probes-file FILE` | Alternative probe definitions (default: the `probes.yaml` beside the package). |
| `--filter-map FILE` | Alternative filter declarations (default: the `filters.yaml` beside the package). |
| `--markdown-out FILE` / `--json-out FILE` | Alternative output paths. |
| `--cardinality-scan-limit N` | Distinct-value scan cap per column in Phase 3 (default 5000). Above the cap the count is reported as a floor and flagged. |
| `--from-json FILE` | Re-render from a previous run's JSON. No phase runs, no database or instance is contacted. Risks and verdicts are re-derived from the stored measurements, so a changed threshold in `verdict.py` takes effect without re-probing. |
| `--repo-root DIR` | Repository root, if not the working directory. |

## Environment

Credentials come from the environment only. Nothing secret is read from a
committed file, and the DSN is redacted before it reaches the output.

| Variable | Phase | Required |
|---|---|---|
| `CAPINV_DATABASE_URL` | 3 | unless `--skip-db` |
| `CAPINV_BASE_URL` | 4 | unless `--skip-probes` |
| `CAPINV_TOKEN` | 4 | only if the target instance enforces auth |
| `CAPINV_WRITE_BASE_URL` | 6 | unless `--skip-writes` |
| `CAPINV_WRITE_DATABASE_URL` | 6 | unless `--skip-writes` |
| `CAPINV_WRITE_MEDIA_ROOT` | 6 | optional; without it, filesystem-touching write probes are skipped rather than run against a real media root |

`CAPINV_DATABASE_URL` is deliberately **not** `DATABASE_URL`. The application
resolves its own database with
`AliasChoices("TEST_DATABASE_URL", "DATABASE_URL")`, so a `TEST_DATABASE_URL`
left over in your shell silently outranks `DATABASE_URL` — the harness would
then profile a different database than you intended and say nothing about it. A
separate namespace makes that impossible.

## What has to be true about the environment

**Phase 1 and 2** need only the repository. The app object is imported and
`app.openapi()` is called in process; `APP_ENV` defaults to `test` if unset, and
every setting has a default, so no `.env`, database or server is involved.

**Phase 3** needs a reachable Postgres holding a realistic library. A read-only
role is the right way to run it, and the harness does not depend on being given
one: every statement runs inside `BEGIN READ ONLY` and is asserted to start with
`SELECT` before it is sent. A table listed in the models but absent from the
target database is reported as a gap, not skipped silently.

**Phase 4** needs a running instance. The recommended setup is a local
`uvicorn` with `AUTH_DISABLED=true` pointed at the read-only database — that
gives realistic query costs and payload sizes without putting probe load on
production:

```bash
APP_ENV=development AUTH_DISABLED=true \
  DATABASE_URL="$CAPINV_DATABASE_URL" \
  uv run uvicorn app.main:api --port 8000
```

Two consequences of running it that way, both of which the harness reports
rather than hides:

- `GET /api/fetch/{asset_id}` and the inbox and accessory routes read the
  filesystem. Without `MEDIA_ROOT`, `INBOX_ROOT` and `ACCESSORY_ROOT` mounted,
  those probes come back `unavailable` with the reason, not as fast successes.
- `GET /api/search/transcripts` needs Elasticsearch. Without it the endpoint
  returns 503 and the probe is recorded as a failure.

**Phase 5** works with no configuration, but its evidence is weak on its own —
see below.

**Phase 6** needs a **disposable** instance and its own **disposable** database,
and refuses to run against anything it cannot prove is both. Build one the way
`scripts/rehearse_migration.sh` does — drop and recreate a scratch database on a
throwaway server, `alembic upgrade head`, then point a uvicorn at it:

```bash
export CAPINV_WRITE_DATABASE_URL='postgresql+psycopg://user:pass@scratch:5432/capinv_write_scratch'
APP_ENV=development AUTH_DISABLED=true \
  DATABASE_URL="$CAPINV_WRITE_DATABASE_URL" \
  uv run uvicorn app.main:api --port 8077
export CAPINV_WRITE_BASE_URL='http://127.0.0.1:8077'
```

Restoring a production snapshot into that scratch database makes the constraint
probes bite against realistic conflict pressure, but is not required: what
Phase 6 measures is *semantics* — whether a duplicate returns 409 or 500,
whether an omitted field survives, whether a delete cascades — and those are
properties of the schema and the code rather than of the row count. The
scenarios seed everything they need through the API.

`unset TEST_DATABASE_URL` before starting that server. The application resolves
its database with `AliasChoices("TEST_DATABASE_URL", "DATABASE_URL")`, so a
stray one silently outranks the `DATABASE_URL` above and the instance comes up
looking perfectly healthy against the wrong database — which the sentinel bind
check would then catch, but only after wasting the run.

## Safety

- **Read-only except Phase 6.** Phase 3 opens a read-only transaction and
  refuses to emit a non-`SELECT`. Phase 4 refuses to send any method other than
  `GET` unless that exact `METHOD /path` appears in the `allowlist` in
  `probes.yaml`, which ships empty and stays empty — Phase 6's scenarios live
  under a separate `write_probes` key that Phase 4's loader never reads, so
  adding a write probe cannot make Phase 4 capable of mutating the
  production-backed instance it runs against.
- **Phase 6 writes, and has to earn it.** Four gates, in order:
  1. `--allow-writes` must be passed explicitly.
  2. `CAPINV_WRITE_BASE_URL` and `CAPINV_WRITE_DATABASE_URL` must both be set.
  3. Neither may resolve to the read side. The comparison is on a normalised
     `(host, port, dbname)` tuple *and* on the cluster's own
     `system_identifier`, because string inequality is not identity —
     `localhost` and `127.0.0.1`, a different user and a different `sslmode`
     all spell one database three ways.
  4. **The sentinel bind check.** A uniquely-named row is written through
     `CAPINV_WRITE_DATABASE_URL` and read back through `CAPINV_WRITE_BASE_URL`;
     if the API cannot see it, the run aborts. This is the only gate that
     establishes the binding *positively*. The first three compare
     configuration against configuration, and all three pass cleanly for a
     write base URL aimed at production with a scratch DSN beside it.
- **Every write probe cleans up after itself,** and anything it could not remove
  is reported in Gaps rather than forgotten. Teardown needs the database as well
  as the API because the API is not a complete inverse of itself: there is no
  `DELETE` for titles, assets, tags or id_schemes.
- **Nothing is deleted.** Phase 5 produces a list of candidates and the evidence
  behind each.
- **Fails loudly.** A missing variable, an unreachable database, an unreachable
  instance, a malformed `probes.yaml` and a `--only` pattern that matches
  nothing are all errors. A phase you did not ask to skip never silently
  produces nothing.

## Declared filters

Phase 2 resolves a filter by finding an `if params.<name>` branch in a repository's
list method and reducing the `where()` under it to a column. That derives the answer
from the code rather than from a note about the code, so it cannot drift, and it is
the default for every filter.

Two shapes defeat it, and no amount of cleverness changes that:

- **The repository never sees the parameter.** `kind` arrives as a public code and
  the service resolves it to an id before the query is built, so `params.kind`
  appears nowhere in the query layer. Following it needs dataflow across the service
  boundary through a rename.
- **The expression cannot be matched textually.** `filename_ext` is written against
  `filename_extension()` so that it matches `ix_assets_filename_ext`;
  `app/models/asset.py` documents at length why the model and PostgreSQL spellings of
  that expression never match as text, which is the same reason the tracer cannot
  match them either.

Those are declared in `filters.yaml`, each with the reason it cannot be derived. A
declaration supplies only what the tracer could not — the column and the operator —
and coverage is still judged by the index oracle from them, so declaring a filter
cannot assert that it is cheap. The single exception is `index:`, for the expression
case, and it names the test that holds the pairing true.

`constrained:` is part of that judgement rather than decoration. A composite index
serves a column that is not its first only when every column before it is pinned too,
so the nested artwork reads — which always pin `entity_type` and `entity_id` before
narrowing by kind — are covered, while the collection route, where both are optional,
is not. Omit it and the first pair reads as a sequential scan.

A third case is not a resolution at all: `GET /api/inbox`'s `depth` walks the
filesystem and has no column behind it, so it is marked `kind: not-a-database-filter`
and reported as such rather than asked about.

**Every declaration is verified before the report is written.** An endpoint that no
longer exists, a parameter it no longer accepts, or an index no longer in the schema
fails the run. That check is the only thing keeping a declaration honest as the code
moves underneath it, so it is an error rather than a warning.

## Editing probes

`probes.yaml` is the whole of Phase 4's configuration; adding a measurement
needs no code change. Path variables like `{asset_id}` are resolved against the
running instance rather than hard-coded, so the file stays valid as the library
changes. A variable that cannot be resolved marks every probe depending on it
`unavailable` with the reason — it is never quietly dropped.

Deep pagination is expressed per probe:

```yaml
- name: assets-deep-page
  path: /api/assets/
  query: {limit: 50}
  paginate: {style: keyset, pages: 40}   # follows 40 `page.next` cursors, times page 41
```

`style: offset` sets the offset directly instead, which is what the
Elasticsearch-backed search endpoint needs.

## Editing write probes

Phase 6's scenarios live under `write_probes` in the same file, in their own
section, and are read by their own loader. Three kinds:

| Kind | Asks |
|---|---|
| `repeat` | Send the identical request twice. Classifies the route `idempotent`, `guarded` or `duplicating` from what happened — never from the verb. |
| `violation` | Provoke a named constraint and record verbatim what comes back. A 5xx is the finding; so is a 4xx whose body says nothing a user could act on. |
| `omission` | Create a row, re-send it without one field and then with that field explicitly `null`, re-reading after each. Checks the static `exclude_none` trace against the running system. |

A scenario is responsible for its own fixtures. `cleanup` (HTTP) is preferred
wherever a route exists, so teardown exercises the same cascades a real client
would; `sql_cleanup` is the fallback for what has no route, and its table and
column names are checked against a closed list in the runner because they reach
an identifier position where no parameter binding is possible.

`needs_media_root: true` marks a scenario that writes bytes. It is skipped and
reported UNKNOWN unless `CAPINV_WRITE_MEDIA_ROOT` names a scratch root.

## Reading the output

Each endpoint section **opens** with its verdict, as a blockquote tagged `SAFE`,
`CAUTION`, `NOT SAFE` or `UNKNOWN`. Everything below it in the section is the
evidence behind it. The verdict leads rather than closes because at ninety-odd
endpoints nobody reads bottom-up, and because it is the reason the document
exists.

The four tokens are fixed and there is exactly one per section, so they can be
counted without parsing anything:

```bash
grep -c '^> \*\*NOT SAFE\*\*' docs/capability-inventory.md
```

Three structural rules keep the document readable, and
`tests/unit/tools/test_capability_inventory.py` asserts all three against the
rendered output rather than trusting a re-read:

- **Header facts are a table.** A run of `**Label:** value` lines is a single
  CommonMark paragraph — lazy continuation lines — so a renderer collapses it
  into one unreadable block while the source still looks fine in a diff. A table
  cannot collapse.
- **Every write carries a contract.** Writes used to be collapsed into one
  **Write endpoints** table, on the grounds that a single-row write with no
  loops has nothing endpoint-specific to get wrong. That was true of their
  *query shape* and false of their *contract*: what a form gets wrong is
  whether a partial submit erases the fields it omitted, whether a retry
  duplicates, and whether a failure is legible — and those differ per route.
  One sentence repeated across fifty-eight endpoints said nothing about any of
  them, so each now has a section and a **Write contract** block.
- **Table facts are written once.** Row counts, fill rates, cardinality and
  collection sizes belong to a database table, not to each of the twenty-seven
  endpoints that read it. They live in the **Tables** appendix, and each
  endpoint's **Data shape** links to it and adds only what is true of that
  endpoint.

Four appendices invert the per-endpoint sections into the questions a front end
asks once rather than sixty-one times:

- **Coverage** — the three figures the library grid's design turns on, measured
  over `library_root=true` Titles rather than over whole tables. The
  display-image figure is computed by compiling the application's *own*
  resolution query, so it agrees with `resolves_display_image=true` by
  construction rather than by being written to match.
- **Error taxonomy** — one row per distinct status and condition, with the
  endpoints that emit it, so one error handler covers the write surface. The
  column that earns it its place is **Usable message**: a status a client can
  branch on is not the same as a message it can show a person.
- **Constraint map** — every constraint a user could reach through the
  interface, mapped to the response it produces. Where a violation produces an
  indistinguishable generic error, the map says so — that is a back-end issue,
  and the front end cannot work around it.
- **Tables**, as before.

Anything the harness could not establish is written `UNKNOWN` with a one-line
note, and repeated in the **Gaps** section with the specific thing that would
settle it. There is no smoothing over: a value is measured, or it is a gap.

Two things worth knowing when reading it:

- **`Candidates for removal` is weak evidence by default.** With no
  `--frontend-path` or `--access-log`, the only references available are inside
  this repository. Several endpoints here exist for machine consumers that live
  in other repositories — the transform-request claim and heartbeat routes are a
  worker pull queue that no front end would ever call. The section says so, and
  the flag on each row records which kind of evidence produced it.
- **Fill rate is not reported for `include=`-gated fields.** `AssetORM.tags`,
  `AssetORM.master_asset`, `TitleORM.tags` and `TitleORM.references` are mapped
  `lazy="noload"`: without `?include=`, they serialise as `[]`, which is
  indistinguishable from genuinely having none. A database-derived percentage
  would describe something the front end never sees, so those fields carry the
  caveat instead of a number.

## Determinism

Re-running with the same inputs produces the same report apart from timings.
To change how the report *looks* without disturbing what it *says*, re-render
from the committed JSON:

```bash
uv run capability-inventory --from-json docs/capability-inventory.json
```

That contacts nothing and re-runs no phase, so the diff is presentation alone.

Collections are sorted by a stable key, floats are rounded at fixed precision,
and the JSON is written with sorted keys — so a diff shows what actually
changed about the API, not noise from the harness.

## Layout

| Module | Phase |
|---|---|
| `static_surface.py` | 1 — OpenAPI document plus route-table and mapper introspection |
| `annotate.py` | 2 — router → service → repository call-graph walk |
| `indexes.py` | 2 — index inventory from the models and the migrations, and the coverage oracle |
| `filter_map.py` | 2 — declared filter resolutions, and their verification |
| `data_shape.py` | 3 — read-only SQL |
| `probes.py` | 4 — the `probes.yaml` runner |
| `dead_surface.py` | 5 — usage evidence |
| `write_semantics.py` | 6 — write-target validation and the sentinel bind check |
| `write_contracts.py` | 6 — the contract derived from the code and the OpenAPI document |
| `write_probes.py` | 6 — the `write_probes` scenario runner |
| `write_assemble.py` | 6 — folding probe results into contracts; the two appendices |
| `load.py` | rebuilds an inventory from a previous run's JSON, for `--from-json` |
| `verdict.py` | risk and verdict derivation, in one auditable place |
| `render.py` | Markdown and JSON emitters |
| `cli.py` | flags, configuration validation, phase orchestration |
