"""Unit tests for the capability-inventory harness (tools/capability_inventory).

These cover the parts where being subtly wrong would produce a confident, wrong
report rather than a visible failure:

* index coverage judged against the *operator*, not just the column;
* LIKE patterns held in a local variable before use;
* the read-only and GET-only guards;
* path-converter normalisation, without which every route declaring
  ``{asset_id:int}`` silently reports as unanalysable.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from tools.capability_inventory import (
    cli,
    data_shape,
    dead_surface,
    load,
    probes,
    render,
    static_surface,
    verdict,
)
from tools.capability_inventory.annotate import _describe_predicate, _pattern_shape
from tools.capability_inventory.indexes import IndexLookup
from tools.capability_inventory.models import (
    CollectionStats,
    ColumnStats,
    DataShape,
    EndpointRecord,
    FieldInfo,
    FilterCoverage,
    IndexInfo,
    Inventory,
    PaginationInfo,
    ParamInfo,
    ProbeResult,
    QueryInfo,
    ResponseInfo,
    RouteAnnotation,
    RouteSurface,
    Timing,
    UsageEvidence,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# Index coverage
# --------------------------------------------------------------------------


@pytest.fixture
def lookup() -> IndexLookup:
    """An index set mirroring the shapes this repository actually has."""
    return IndexLookup(
        (
            IndexInfo("assets_pkey", "assets", ("id",), True, source="primary key"),
            IndexInfo("assets_path_key", "assets", ("path",), True, source="column unique=True"),
            IndexInfo(
                "ix_tags_name_lower",
                "tags",
                (),
                False,
                expression="lower(tags.name)",
                source="models",
            ),
            IndexInfo(
                "uq_external_identifier_scheme_id",
                "external_identifiers",
                ("scheme_id", "external_id"),
                True,
                source="unique constraint",
            ),
        )
    )


def test_equality_on_an_indexed_column_is_covered(lookup: IndexLookup) -> None:
    covered, index, _ = lookup.judge("assets", "id", "==")
    assert covered is True
    assert index == "assets_pkey"


def test_case_insensitive_prefix_cannot_use_a_case_sensitive_index(lookup: IndexLookup) -> None:
    """The single most misleading line the report could print.

    `assets.path` carries a unique btree, so a naive check says "indexed: yes".
    The filter is `ILIKE 'x%'`, which the default case-sensitive opclass cannot
    serve, so the truthful answer is no.
    """
    covered, index, note = lookup.judge("assets", "path", "ilike_prefix")
    assert covered is False
    assert index is None
    assert "case-insensitive" in note
    assert "assets_path_key" in note, "the note should name the index that does not help"


def test_case_insensitive_prefix_is_covered_by_a_matching_expression_index(
    lookup: IndexLookup,
) -> None:
    covered, index, _ = lookup.judge("tags", "name", "ilike_prefix")
    assert covered is True
    assert index == "ix_tags_name_lower"


@pytest.mark.parametrize("operator", ["ilike_contains", "ilike_suffix", "like_contains"])
def test_leading_wildcard_matches_are_never_covered(lookup: IndexLookup, operator: str) -> None:
    covered, index, note = lookup.judge("assets", "path", operator)
    assert covered is False
    assert index is None
    assert "wildcard" in note


def test_non_leading_column_of_a_composite_index_is_not_covered(lookup: IndexLookup) -> None:
    """`(scheme_id, external_id)` cannot serve a lookup on `external_id` alone."""
    covered, _, note = lookup.judge("external_identifiers", "external_id", "==")
    assert covered is False
    assert "sequential scan" in note

    covered_leading, index, _ = lookup.judge("external_identifiers", "scheme_id", "==")
    assert covered_leading is True
    assert index == "uq_external_identifier_scheme_id"


def test_composite_index_applies_when_a_join_pins_its_leading_column(
    lookup: IndexLookup,
) -> None:
    """The false "sequential scan" that sent #53 after an index it did not need.

    `resolve_by_code` joins id_schemes on scheme_id and filters external_id. The join
    pins the leading column, so `uq(scheme_id, external_id)` serves the lookup --
    measured at 300k rows, the planner index-scans it. Judging external_id in
    isolation reported a sequential scan.
    """
    covered, index, note = lookup.judge(
        "external_identifiers", "external_id", "==", frozenset({"scheme_id"})
    )

    assert covered is True
    assert index == "uq_external_identifier_scheme_id"
    assert "scheme_id" in note, "the note should say which constraint makes it apply"


def test_composite_index_does_not_apply_when_the_leading_column_is_free(
    lookup: IndexLookup,
) -> None:
    """Constraining an unrelated column must not conjure coverage."""
    covered, _, note = lookup.judge(
        "external_identifiers", "external_id", "==", frozenset({"entity_id"})
    )

    assert covered is False
    assert "sequential scan" in note


def test_lookup_refuses_indexes_from_the_migration_scan() -> None:
    """Coverage must be judged against the live schema, not the migration history.

    The scan does not resolve revision order, so `videos` and
    `uniq_pending_transform_per_video_and_type` still appear in it long after
    5eab333f4197 renamed them. Merging both collections into the lookup let a dead
    object count as live coverage; this guard makes that unrepresentable.
    """
    with pytest.raises(ValueError, match="live schema"):
        IndexLookup(
            (
                IndexInfo("assets_pkey", "assets", ("id",), True, source="primary key"),
                IndexInfo(
                    "ix_videos_master_asset_id",
                    "videos",
                    ("master_asset_id",),
                    False,
                    source="migration 31d43b7e01c0",
                ),
            )
        )


def test_unresolved_input_reports_unknown_rather_than_false(lookup: IndexLookup) -> None:
    """A gap must stay a gap. Reporting it as False would be a silent claim."""
    assert lookup.judge(None, "path", "==")[0] is None
    assert lookup.judge("assets", None, "==")[0] is None
    assert lookup.judge("assets", "path", None)[0] is None


# --------------------------------------------------------------------------
# Predicate extraction
# --------------------------------------------------------------------------


def _expression(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def test_inline_like_pattern_is_classified_by_shape() -> None:
    assert _pattern_shape(_expression('f"{x}%"')) == "prefix"
    assert _pattern_shape(_expression('f"%{x}%"')) == "contains"
    assert _pattern_shape(_expression('f"%.{ext}"')) == "suffix"


def test_like_pattern_held_in_a_local_is_resolved_before_classification() -> None:
    """`SQLAlchemyMediaRepository.list_paged` builds `like_val` before using it.

    Classifying the bare name would report a guaranteed sequential scan as an
    exact match.
    """
    body = ast.parse('like_val = f"%{params.path_part}%"').body[0]
    assert isinstance(body, ast.Assign)
    locals_ = {"like_val": [body.value]}
    assert _pattern_shape(_expression("like_val"), locals_) == "contains"
    assert _pattern_shape(_expression("like_val")) == "exact", "unresolved, as expected"


def test_predicate_reduces_to_table_column_and_operator() -> None:
    assert _describe_predicate(_expression("AssetORM.size >= params.size_min")) == (
        "AssetORM",
        "size",
        ">=",
    )
    assert _describe_predicate(_expression('AssetORM.path.ilike(f"{p}%")')) == (
        "AssetORM",
        "path",
        "ilike_prefix",
    )
    assert _describe_predicate(_expression("AssetTagORM.tag_id.in_(tags)")) == (
        "AssetTagORM",
        "tag_id",
        "in_",
    )


# --------------------------------------------------------------------------
# Read-only guard
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE assets SET path = 'x'",
        "DELETE FROM assets",
        "  insert into assets values (1)",
        "TRUNCATE assets",
        "CREATE INDEX ix ON assets (path)",
    ],
)
def test_non_select_statements_are_refused(statement: str) -> None:
    with pytest.raises(data_shape.ReadOnlyViolation):
        data_shape._guard(statement)


@pytest.mark.parametrize(
    "statement",
    ["SELECT 1", "  select count(*) from assets", "WITH x AS (SELECT 1) SELECT * FROM x"],
)
def test_reads_are_allowed(statement: str) -> None:
    assert data_shape._guard(statement) == statement


@pytest.mark.parametrize("identifier", ['assets"; DROP TABLE x --', "a b", "", "1abc", "a-b"])
def test_identifiers_that_are_not_identifiers_are_refused(identifier: str) -> None:
    with pytest.raises(ValueError):
        data_shape._quote(identifier)


def test_credentials_never_reach_the_output() -> None:
    redacted = data_shape.redact("postgresql://user:hunter2@db.internal:5432/media")
    assert "hunter2" not in redacted
    assert "user" not in redacted
    assert "db.internal:5432/media" in redacted


def test_missing_dsn_names_the_variable_and_the_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(data_shape.ENV_VAR, raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        data_shape.resolve_dsn()
    assert data_shape.ENV_VAR in str(excinfo.value)
    assert "--skip-db" in str(excinfo.value)


def test_sqlalchemy_driver_suffix_is_stripped_for_psycopg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(data_shape.ENV_VAR, "postgresql+psycopg://u:p@h:5432/media")
    assert data_shape.resolve_dsn().startswith("postgresql://")


# --------------------------------------------------------------------------
# Probe configuration
# --------------------------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "probes.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _spec(**overrides: Any) -> probes.ProbeSpec:
    """A minimal GET probe against /api/streams, for exercising `run` directly."""
    fields: dict[str, Any] = {
        "name": "by-asset",
        "method": "GET",
        "path": "/api/streams",
        "query": {},
        "headers": {},
        "expect_status": (200,),
        "stream": False,
        "paginate": None,
        "note": None,
        "runs": 1,
        "warmup": 0,
        "timeout": 1.0,
    }
    return probes.ProbeSpec(**{**fields, **overrides})


def test_shipped_probe_definitions_are_valid() -> None:
    config = probes.load_config(Path(probes.__file__).with_name("probes.yaml"))
    assert config.probes
    assert config.allowlist == frozenset(), "the shipped allowlist must stay empty"
    assert all(spec.method == "GET" for spec in config.probes)


def test_a_write_probe_is_refused_unless_explicitly_allowlisted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "probes:\n  - name: danger\n    path: /api/assets/\n    method: DELETE\n",
    )
    with pytest.raises(probes.ProbeConfigError, match="allowlist"):
        probes.load_config(path)


def test_an_allowlisted_write_probe_is_accepted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "allowlist:\n  - DELETE /api/assets/\n"
        "probes:\n  - name: danger\n    path: /api/assets/\n    method: DELETE\n",
    )
    assert probes.load_config(path).probes[0].method == "DELETE"


def test_duplicate_probe_names_are_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "probes:\n  - name: a\n    path: /api/ping\n  - name: a\n    path: /api/version\n",
    )
    with pytest.raises(probes.ProbeConfigError, match="duplicate"):
        probes.load_config(path)


def test_an_empty_probe_file_is_an_error_not_an_empty_run(tmp_path: Path) -> None:
    with pytest.raises(probes.ProbeConfigError, match="no probes"):
        probes.load_config(_write(tmp_path, "probes: []\n"))


def test_a_query_string_in_the_path_is_refused(tmp_path: Path) -> None:
    """A probe whose path carries ``?`` matches no route.

    It still runs and times successfully, so the failure is invisible: the
    endpoint reports UNKNOWN while the file looks correct. Three /api/streams
    probes shipped that way and went unmeasured, so it is refused at load.
    """
    path = _write(
        tmp_path,
        "probes:\n  - name: streams\n    path: /api/streams?limit=50\n",
    )
    with pytest.raises(probes.ProbeConfigError, match="query string in `path`"):
        probes.load_config(path)


def test_no_shipped_probe_hides_a_query_string_in_its_path() -> None:
    config = probes.load_config(Path(probes.__file__).with_name("probes.yaml"))
    assert all("?" not in spec.path for spec in config.probes)


def test_variables_are_substituted_into_query_values() -> None:
    """``{name}`` resolves in a query value, not only in the path.

    Without this a filter probe has to smuggle its variable into the path as a
    query string, which is precisely what the loader now refuses.
    """
    spec = _spec(query={"asset_id": "{asset_id}", "limit": 50})
    captured: dict[str, Any] = {}

    def _fake_simple(_spec: probes.ProbeSpec, path: str, query: dict[str, Any], _n: list[str]):
        captured.update({"path": path, "query": query})
        return None

    runner = probes.ProbeRunner.__new__(probes.ProbeRunner)
    runner._run_simple = _fake_simple  # type: ignore[method-assign]
    probes.ProbeRunner.run(runner, spec, {"asset_id": 4213})

    assert captured["path"] == "/api/streams"
    assert captured["query"] == {"asset_id": "4213", "limit": 50}


def test_an_unresolved_query_variable_reports_unavailable_not_a_crash() -> None:
    """An unknown variable in a query value degrades the way a path one does."""
    runner = probes.ProbeRunner.__new__(probes.ProbeRunner)
    result = probes.ProbeRunner.run(runner, _spec(query={"asset_id": "{nope}"}), {})

    assert result.status == "unavailable"
    assert "nope" in (result.reason or "")
    assert result.endpoint_key == "GET /api/streams"


# --------------------------------------------------------------------------
# Usage evidence
# --------------------------------------------------------------------------


def test_skip_list_is_judged_below_the_search_root(tmp_path: Path) -> None:
    """A checkout living inside a skipped directory must still be scanned.

    `_SKIP_DIRECTORIES` exists to skip vendored trees *within* the scan. Matching
    it against the absolute path instead made the result depend on where the
    repository sits on disk: run the harness from `.claude/worktrees/<branch>/`
    and every file is excluded, the scan finds nothing, and all 96 endpoints are
    reported as candidates for removal. That is a whole API surface presented as
    dead code, with no failure anywhere to signal it.
    """
    checkout = tmp_path / ".claude" / "worktrees" / "some-branch"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "tests" / "test_assets.py").write_text('client.get("/api/assets/")\n')
    (checkout / "tests" / "node_modules").mkdir()
    (checkout / "tests" / "node_modules" / "vendored.py").write_text("noise\n")

    found = dead_surface._search_root(checkout / "tests")

    assert [p.name for p in found] == ["test_assets.py"], (
        "the real file must be found despite `.claude` above the root, and the "
        "vendored directory below it must still be skipped"
    )


def test_a_referenced_endpoint_is_not_a_removal_candidate(tmp_path: Path) -> None:
    """End-to-end guard on the regression, through the public entry point."""
    checkout = tmp_path / ".claude" / "worktrees" / "some-branch"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "tests" / "test_assets.py").write_text('client.get("/api/assets/")\n')

    route = RouteSurface(
        method="GET",
        path="/api/assets/",
        operation_id="list_assets",
        summary=None,
        tags=(),
        auth="bearer",
        handler_module="app.routers.assets",
        handler_name="list_assets",
        params=(),
        request_body=None,
        responses=(),
        success_status="200",
        is_streaming=False,
        trailing_slash_required=True,
    )
    evidence = dead_surface.from_repository((route,), checkout)

    assert evidence["GET /api/assets/"].referenced is True
    assert evidence["GET /api/assets/"].test_references == ("tests/test_assets.py",)


def test_report_identity_comes_from_the_project_not_the_directory(tmp_path: Path) -> None:
    """Otherwise a worktree run rewrites the identity line for no real reason."""
    root = tmp_path / "capinv-some-branch"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "media-api"\n')

    assert cli._project_name(root) == "media-api"


def test_report_identity_falls_back_to_the_directory_name(tmp_path: Path) -> None:
    root = tmp_path / "fallback-name"
    root.mkdir()
    (root / "pyproject.toml").write_text("this is not valid toml =\n")

    assert cli._project_name(root) == "fallback-name"


def test_percentiles_are_nearest_rank_over_the_sample() -> None:
    timing = probes._summarise([10.0, 20.0, 30.0, 40.0, 100.0], [])
    assert timing is not None
    assert timing.runs == 5
    assert timing.p50_ms == 30.0
    assert timing.p95_ms == 100.0
    assert timing.min_ms == 10.0
    assert timing.max_ms == 100.0


def test_no_samples_yields_no_timing_rather_than_zero() -> None:
    assert probes._summarise([], []) is None


def test_pick_walks_mappings_and_list_indices() -> None:
    payload = {"items": [{"id": 41}, {"id": 42}]}
    assert probes._pick(payload, "items.0.id") == 41
    assert probes._pick(payload, "items.9.id") is None
    assert probes._pick(payload, "missing.0") is None


# --------------------------------------------------------------------------
# Path normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("declared", "published"),
    [
        ("/api/assets/{asset_id:int}", "/api/assets/{asset_id}"),
        ("/api/runner_state/{runner_key:str}", "/api/runner_state/{runner_key}"),
        ("/api/assets/{asset_id:int}/titles", "/api/assets/{asset_id}/titles"),
        ("/api/assets/{asset_id}", "/api/assets/{asset_id}"),
        ("/api/ping", "/api/ping"),
    ],
)
def test_route_paths_are_normalised_to_their_openapi_form(declared: str, published: str) -> None:
    """Without this, every converter-typed route loses its handler and its analysis."""
    assert static_surface._normalise_path(declared) == published


# --------------------------------------------------------------------------
# A small inventory covering every kind of section the renderer emits
# --------------------------------------------------------------------------


def _surface(
    method: str,
    path: str,
    *,
    model: str = "AssetRead",
    row_model: str | None = None,
    fields: tuple[FieldInfo, ...] = (),
    params: tuple[ParamInfo, ...] = (),
    body: str | None = None,
) -> RouteSurface:
    """Build a route surface with sensible defaults for a test."""
    return RouteSurface(
        method=method,
        path=path,
        operation_id=f"{method.lower()}_{path.strip('/').replace('/', '_')}",
        summary=f"{method} {path}",
        tags=("t",),
        auth="bearer, no scope or role enforced at the route",
        handler_module="app.routers.assets.core",
        handler_name="handler",
        params=params,
        request_body=body,
        responses=(
            ResponseInfo(
                status="200",
                description="ok",
                model=model,
                fields=fields,
                media_type="application/json",
                row_model=row_model,
            ),
            ResponseInfo(status="422", description="Validation Error", model=None),
        ),
        success_status="200",
        is_streaming=False,
        trailing_slash_required=path.endswith("/") and path != "/",
    )


def _annotation(
    *,
    queries: tuple[QueryInfo, ...] = (),
    loops: tuple[QueryInfo, ...] = (),
    style: str = "none",
    coverage: tuple[FilterCoverage, ...] = (),
) -> RouteAnnotation:
    return RouteAnnotation(
        service="AssetService",
        repositories=("SQLAlchemyMediaRepository",),
        queries=queries,
        n_plus_one=loops,
        coverage=coverage,
        pagination=PaginationInfo(
            style=style,
            default_limit=50 if style == "keyset" else None,
            max_limit=500 if style == "keyset" else None,
            stable_under_writes=True if style == "keyset" else None,
            stability_note="tie-broken on id" if style == "keyset" else "",
        ),
        external_calls=(),
        background_work=(),
        hard_limits=("`limit` <= 500",),
        filesystem_access=(),
        unknowns=(),
    )


def _query(*, in_loop: bool = False) -> QueryInfo:
    return QueryInfo(
        owner="SQLAlchemyMediaRepository.list_paged",
        kind="select",
        tables=("assets",),
        in_loop=in_loop,
        loop_note="for tag_id in tag_ids" if in_loop else None,
        writes=False,
        line=118,
        source_file="app/repositories/media_repository.py",
    )


def _usage(key: str) -> UsageEvidence:
    return UsageEvidence(
        endpoint_key=key,
        referenced=True,
        strength="weak",
        callers=(),
        test_references=("tests/x.py",),
        note="in-repository evidence only",
    )


def _sample_inventory() -> Inventory:
    """An inventory exercising a read, a uniform write and a looping write."""
    listing = EndpointRecord(
        surface=_surface(
            "GET",
            "/api/assets/",
            model="PaginatedResponse_AssetReadExtended_",
            row_model="AssetReadExtended",
            fields=(
                FieldInfo("id", "int", False),
                FieldInfo("tags", "list[TagRead]", True, conditional_on="include=tags"),
            ),
            params=(ParamInfo("limit", "query", "int", False, 50, "page size", {"maximum": 500}),),
        ),
        annotation=_annotation(queries=(_query(),), style="keyset"),
        probes=(
            ProbeResult(
                name="assets-page-1",
                endpoint_key="GET /api/assets/",
                method="GET",
                url="/api/assets/?limit=50",
                status="ok",
                http_status=200,
                timing=Timing(runs=7, p50_ms=1830.0, p95_ms=2520.0, min_ms=1700.0, max_ms=2600.0),
                bytes_=19471,
                item_count=50,
                notes=("first page",),
            ),
        ),
        usage=_usage("GET /api/assets/"),
        risks=("one extra SELECT per row",),
        verdict="worst-case p95 is 2.5s, and the cause is the per-row lazy load.",
        verdict_class="unsafe",
    )
    uniform_write = EndpointRecord(
        surface=_surface("POST", "/api/tags", body="TagCreatePublic", model="TagRead"),
        annotation=_annotation(queries=(_query(),)),
        usage=_usage("POST /api/tags"),
        verdict="single-row write with no loops.",
        verdict_class="write",
    )
    looping_write = EndpointRecord(
        surface=_surface(
            "PUT", "/api/assets/{asset_id}/tags", body="TagSet", model="list[TagRead]"
        ),
        annotation=_annotation(queries=(_query(in_loop=True),), loops=(_query(in_loop=True),)),
        usage=_usage("PUT /api/assets/{asset_id}/tags"),
        risks=("queries issued inside a loop",),
        verdict="work is proportional to the size of the payload.",
        verdict_class="caution",
    )
    unreferenced = EndpointRecord(
        surface=_surface("GET", "/api/health", model=None),
        annotation=_annotation(),
        usage=UsageEvidence(
            endpoint_key="GET /api/health",
            referenced=False,
            strength="weak",
            callers=(),
            test_references=(),
            note="none",
        ),
        verdict="the read is index-covered.",
        verdict_class="safe",
    )
    shape = DataShape(
        row_counts={"assets": 13321, "tags": 43},
        columns=(
            ColumnStats("assets", "path", 1.0, 13321, 13321, distinct=5000, distinct_capped=True),
            ColumnStats(
                "assets",
                "container_format",
                0.99,
                13173,
                13321,
                distinct=4,
                facet_candidate=True,
            ),
        ),
        collections=(),
        server_version="PostgreSQL 17.9",
        captured_from="read-only Postgres, connection fingerprint abc123",
        baseline_rtt_ms=28.0,
    )
    return Inventory(
        generated_from="media-api @ app.openapi()",
        app_version="1.5.4",
        phases_run=("1", "2", "3", "4", "5"),
        phases_skipped=(),
        endpoints=(listing, uniform_write, looping_write, unreferenced),
        indexes=(IndexInfo("assets_pkey", "assets", ("id",), True, source="primary key"),),
        data_shape=shape,
        unknowns=(),
        notes=("a note",),
    )


# --------------------------------------------------------------------------
# Rendered-document formatting contract
# --------------------------------------------------------------------------
#
# The report is Markdown, and Markdown fails quietly. A run of `**Label:** value`
# lines is a single CommonMark paragraph, not four lines, so a renderer collapses
# it into an unreadable block while the source still looks fine in a diff. A
# heading without a blank line before it is not a heading at all. None of that
# raises, so it has to be asserted.


ENDPOINT_HEADING = re.compile(r"^### (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /")
VERDICT_LINE = re.compile(r"^> \*\*(SAFE|CAUTION|NOT SAFE|UNKNOWN)\*\* — \S")


def _render_sample() -> str:
    """Render a small inventory exercising every kind of section."""
    return render.to_markdown(_sample_inventory())


@pytest.fixture(scope="module")
def rendered() -> str:
    """The rendered sample document, split once for the whole module."""
    return _render_sample()


def test_no_two_consecutive_bold_label_lines(rendered: str) -> None:
    """Consecutive `**Label:** value` lines collapse into one paragraph.

    This is the defect the header table exists to prevent, so it is asserted
    over the whole document rather than over the header alone.
    """
    lines = rendered.split("\n")
    offenders = [
        (index + 1, previous, line)
        for index, (previous, line) in enumerate(zip(lines, lines[1:], strict=False))
        if previous.startswith("**") and line.startswith("**")
    ]
    assert not offenders, f"consecutive bold-label lines would render as one block: {offenders}"


def test_every_endpoint_section_opens_with_a_verdict(rendered: str) -> None:
    """The conclusion leads, and its token is one of the four."""
    lines = rendered.split("\n")
    headings = [i for i, line in enumerate(lines) if ENDPOINT_HEADING.match(line)]
    assert headings, "the sample rendered no endpoint sections"
    for index in headings:
        window = lines[index + 1 : index + 4]
        matches = [line for line in window if VERDICT_LINE.match(line)]
        assert matches, (
            f"{lines[index]!r} is not followed within two lines by a verdict "
            f"blockquote; got {window!r}"
        )


def test_exactly_one_verdict_line_per_endpoint_section(rendered: str) -> None:
    """The token stays greppable: one `> **TOKEN**` line per section."""
    lines = rendered.split("\n")
    headings = sum(1 for line in lines if ENDPOINT_HEADING.match(line))
    verdicts = sum(1 for line in lines if VERDICT_LINE.match(line))
    assert headings == verdicts, (
        f"{headings} endpoint sections but {verdicts} verdict lines; "
        "`grep -c '> \\*\\*NOT SAFE\\*\\*'` has to count sections"
    )


def test_subheadings_are_surrounded_by_blank_lines(rendered: str) -> None:
    """A `####` heading needs air on both sides or it is not a heading."""
    lines = rendered.split("\n")
    for index, line in enumerate(lines):
        if not line.startswith("#### "):
            continue
        assert index > 0 and lines[index - 1] == "", f"no blank line before {line!r}"
        assert index + 1 < len(lines) and lines[index + 1] == "", f"no blank line after {line!r}"


def test_tables_and_lists_are_surrounded_by_blank_lines(rendered: str) -> None:
    """A table or list that abuts a paragraph is absorbed into it."""
    lines = rendered.split("\n")
    for index, line in enumerate(lines):
        is_table = line.startswith("|")
        is_list = line.startswith("- ")
        if not (is_table or is_list):
            continue
        previous = lines[index - 1] if index else ""
        starts_block = not (
            previous.startswith("|") if is_table else previous.startswith(("- ", "  "))
        )
        if starts_block:
            assert previous == "", f"line {index + 1} starts a block after {previous!r}"


def _write_table(rendered: str) -> str:
    """Just the Write endpoints section.

    Scoping matters: every endpoint appears in the summary table too, so a bare
    substring search cannot tell a collapsed write from a sectioned one.
    """
    _, _, tail = rendered.partition("## Write endpoints")
    body, _, _ = tail.partition("\n## ")
    return body


def test_uniform_writes_are_collapsed_not_sectioned(rendered: str) -> None:
    """A single-row write gets a table row, not a section of its own."""
    assert "## Write endpoints" in rendered
    assert "### POST /api/tags" not in rendered, "a uniform write should not get a section"
    assert "| `POST /api/tags` |" in _write_table(rendered)


def test_a_looping_write_keeps_its_own_section(rendered: str) -> None:
    """A write that issues per-item queries has a failure mode worth a section."""
    assert "### PUT /api/assets/{asset_id}/tags" in rendered
    assert "| `PUT /api/assets/{asset_id}/tags` |" not in _write_table(rendered)


def test_table_facts_are_written_once(rendered: str) -> None:
    """Row counts live in the appendix, not in every endpoint that reads a table."""
    assert rendered.count("**13,321 rows.**") == 1
    assert "### Table: assets" in rendered
    # Endpoints link to it rather than restating it.
    assert "[`assets`](#table-assets)" in rendered


def test_endpoint_sections_are_separated_by_a_rule(rendered: str) -> None:
    """A horizontal rule closes each section."""
    lines = rendered.split("\n")
    headings = sum(1 for line in lines if ENDPOINT_HEADING.match(line))
    assert sum(1 for line in lines if line == "---") == headings


def test_document_ends_with_exactly_one_newline(rendered: str) -> None:
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_no_run_of_blank_lines(rendered: str) -> None:
    assert "\n\n\n" not in rendered


# --------------------------------------------------------------------------
# The committed artefact, and the loader that lets it be re-rendered
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_MARKDOWN = REPO_ROOT / "docs" / "capability-inventory.md"
COMMITTED_JSON = REPO_ROOT / "docs" / "capability-inventory.json"


@pytest.mark.skipif(not COMMITTED_MARKDOWN.is_file(), reason="no committed report to check")
def test_committed_report_satisfies_the_formatting_contract() -> None:
    """The real artefact obeys the same rules as the synthetic sample.

    The sample covers every branch; this covers the ninety-six-endpoint document
    people actually read, and catches a section shape the sample happens not to
    exercise.
    """
    lines = COMMITTED_MARKDOWN.read_text(encoding="utf-8").split("\n")

    consecutive = [
        index + 1
        for index, (previous, line) in enumerate(zip(lines, lines[1:], strict=False))
        if previous.startswith("**") and line.startswith("**")
    ]
    assert not consecutive, f"consecutive bold-label lines at {consecutive}"

    headings = [i for i, line in enumerate(lines) if ENDPOINT_HEADING.match(line)]
    assert headings, "the committed report has no endpoint sections"
    for index in headings:
        window = lines[index + 1 : index + 4]
        assert any(
            VERDICT_LINE.match(line) for line in window
        ), f"{lines[index]!r} has no verdict blockquote within two lines"

    for index, line in enumerate(lines):
        if line.startswith("#### "):
            assert lines[index - 1] == "", f"no blank line before {line!r}"
            assert lines[index + 1] == "", f"no blank line after {line!r}"


@pytest.mark.skipif(not COMMITTED_JSON.is_file(), reason="no committed JSON to load")
def test_committed_json_round_trips_through_the_loader() -> None:
    """`--from-json` can rebuild the inventory the committed JSON describes.

    Re-rendering without re-probing is what keeps a presentation change to a
    presentation-only diff, so the loader staying in step with the models is
    load-bearing rather than a convenience.
    """
    inventory = load.from_json(COMMITTED_JSON)
    assert inventory.endpoints, "no endpoints were reconstructed"
    assert all(e.surface.method for e in inventory.endpoints)

    rendered = render.to_markdown(inventory)
    assert rendered.startswith("# Capability inventory")
    assert "## Tables" in rendered
    assert "\n\n\n" not in rendered


def test_loader_names_the_missing_field_rather_than_failing_obscurely(
    tmp_path: Path,
) -> None:
    """A schema drift must say which field moved."""
    path = tmp_path / "broken.json"
    path.write_text('{"endpoints": []}', encoding="utf-8")
    with pytest.raises(load.InventoryFormatError, match="generated_from"):
        load.from_json(path)


def test_loader_rejects_a_non_json_file(tmp_path: Path) -> None:
    path = tmp_path / "notjson.json"
    path.write_text("this is not json", encoding="utf-8")
    with pytest.raises(load.InventoryFormatError, match="not valid JSON"):
        load.from_json(path)


def test_severity_tokens_are_the_documented_four() -> None:
    """The set a reader learns, and the set the contract asserts, are one set."""
    assert set(render.SEVERITY_TOKENS) == {"SAFE", "CAUTION", "NOT SAFE", "UNKNOWN"}
    assert set(render._TOKEN_FOR_CLASS.values()) <= set(render.SEVERITY_TOKENS)


# --------------------------------------------------------------------------
# Collection ceiling
# --------------------------------------------------------------------------


def _collection(child: str, fk: str, parent: str, max_children: int) -> CollectionStats:
    return CollectionStats(
        parent_table=parent,
        child_table=child,
        fk_column=fk,
        parents_with_children=1,
        parents_total=1,
        min_children=0,
        median_children=0.0,
        p95_children=float(max_children),
        max_children=max_children,
    )


def _narrowed_on(table: str, column: str) -> FilterCoverage:
    """The coverage entry a scoped read produces for its own lookup."""
    return FilterCoverage(
        param=f"{table}.{column}",
        role="lookup",
        table=table,
        column=column,
        operator="==",
        covered=True,
        index=None,
        note="",
    )


def _shape(*collections: CollectionStats, row_counts: dict[str, int] | None = None) -> DataShape:
    return DataShape(
        row_counts=row_counts or {},
        columns=(),
        collections=collections,
        server_version="PostgreSQL 17.9",
        captured_from="test",
    )


def _query_on(*tables: str) -> QueryInfo:
    return QueryInfo(
        owner="SQLAlchemyThingRepository.list_for",
        kind="select",
        tables=tables,
        in_loop=False,
        loop_note=None,
        writes=False,
        line=1,
        source_file="app/repositories/thing_repository.py",
    )


def _scoped(path: str) -> RouteSurface:
    """A route scoped by a path parameter, which is what selects source 3."""
    return _surface(
        "GET",
        path,
        params=(ParamInfo(name="id", location="path", type_="int", required=True),),
    )


def test_ceiling_uses_the_relationship_the_route_actually_narrows_on() -> None:
    """The false NOT SAFE on /titles/{title_id}/references.

    That route filters `title_references.title_id`, and the table holds no rows. It
    was reported as "727 rows" because `titles` -- joined only for a 404 existence
    check -- is the child of a much larger relationship, and the ceiling matched on
    child table alone.
    """
    annotation = _annotation(
        queries=(_query_on("title_references", "titles"),),
        coverage=(_narrowed_on("title_references", "title_id"),),
    )
    shape = _shape(
        _collection("titles", "title_type_id", "title_types", 727),
        _collection("title_references", "title_id", "titles", 0),
    )

    ceiling, source = verdict._collection_ceiling(
        _scoped("/api/titles/{title_id}/references"), annotation, shape
    )

    assert ceiling == 0
    assert source is not None and "title_references.title_id" in source


def test_ceiling_refuses_an_unrelated_relationship_rather_than_reassuring() -> None:
    """The false SAFE on /transform_requests/{request_id}/logs -- the worse direction.

    No measured relationship describes logs-per-request, so the honest answer is that
    the ceiling is unknown. Reporting requests-per-asset instead cleared the endpoint
    on a number about a different relationship entirely.
    """
    annotation = _annotation(
        queries=(_query_on("run_logs", "media_transform_requests"),),
        coverage=(_narrowed_on("run_logs", "request_id"),),
    )
    shape = _shape(_collection("media_transform_requests", "asset_id", "assets", 17))

    ceiling, source = verdict._collection_ceiling(
        _scoped("/api/transform_requests/{request_id}/logs"), annotation, shape
    )

    assert ceiling is None, "an unrelated relationship must not become the ceiling"
    assert source is None


def test_ceiling_falls_back_to_the_row_count_of_the_narrowed_table() -> None:
    """With no matching relationship, the table's own size is still a true bound."""
    annotation = _annotation(
        queries=(_query_on("title_references"),),
        coverage=(_narrowed_on("title_references", "title_id"),),
    )
    shape = _shape(row_counts={"title_references": 40})

    ceiling, source = verdict._collection_ceiling(
        _scoped("/api/titles/{title_id}/references"), annotation, shape
    )

    assert ceiling == 40
    assert source is not None and "at most" in source


def test_a_probe_still_outranks_every_inference() -> None:
    """Source order is unchanged: what a probe actually received is never overridden."""
    annotation = _annotation(
        queries=(_query_on("streams"),),
        coverage=(_narrowed_on("streams", "asset_id"),),
    )
    shape = _shape(_collection("streams", "asset_id", "assets", 79))
    probe = ProbeResult(
        name="streams-all",
        endpoint_key="GET /api/streams",
        method="GET",
        url="/api/streams",
        status="ok",
        http_status=200,
        item_count=65_739,
    )

    ceiling, source = verdict._collection_ceiling(
        _scoped("/api/streams/{stream_id}"), annotation, shape, (probe,)
    )

    assert ceiling == 65_739
    assert source == "measured directly from a probe response"
