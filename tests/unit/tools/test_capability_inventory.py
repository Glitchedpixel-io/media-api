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
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tools.capability_inventory import (
    annotate,
    cli,
    data_shape,
    dead_surface,
    filter_map,
    load,
    probes,
    render,
    indexes,
    static_surface,
    verdict,
    write_assemble,
    write_contracts,
    write_probes,
    write_semantics,
)
from tools.capability_inventory.annotate import (
    _describe_expression_predicate,
    _describe_predicate,
    _pattern_shape,
)
from tools.capability_inventory.indexes import IndexLookup
from tools.capability_inventory.models import (
    CollectionStats,
    ColumnStats,
    ConstraintMapping,
    CoverageMetric,
    DataShape,
    DeleteSemantics,
    EndpointRecord,
    ErrorCase,
    FieldContract,
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
    WriteContract,
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
        write_contract=WriteContract(
            fields=(
                FieldContract(
                    name="name",
                    type_="string",
                    required=True,
                    nullable=False,
                    omitted_means="rejected",
                    constraints={"maxLength": 50},
                ),
            ),
            unknown_fields='rejected with 422 naming the field (`extra="forbid"`)',
            omission_semantics="Create: an omitted optional field takes its declared default.",
            idempotency="guarded",
            idempotency_evidence="probed -- the second identical request is refused with 409",
            atomic=True,
            atomicity_note="Single transaction.",
            concurrency="last-write-wins",
            auth="bearer",
            audience="front end",
            errors=(
                ErrorCase(
                    status="409",
                    condition="the same request is sent a second time",
                    body='`{"detail": "Unique constraint violated."}`',
                    usable_message=False,
                    source="probed",
                ),
            ),
            probed=True,
        ),
        verdict="Usable, with handling — a repeat is refused with a conflict.",
        verdict_class="caution",
    )
    looping_write = EndpointRecord(
        surface=_surface(
            "PUT", "/api/assets/{asset_id}/tags", body="TagSet", model="list[TagRead]"
        ),
        annotation=_annotation(queries=(_query(in_loop=True),), loops=(_query(in_loop=True),)),
        usage=_usage("PUT /api/assets/{asset_id}/tags"),
        risks=("queries issued inside a loop",),
        write_contract=WriteContract(
            fields=(
                FieldContract(
                    name="tag_ids",
                    type_="array[integer]",
                    required=True,
                    nullable=False,
                    omitted_means="rejected -- the field is required",
                ),
            ),
            unknown_fields='rejected with 422 naming the field (`extra="forbid"`)',
            omission_semantics=(
                "Whole-collection replacement: `tag_ids` replaces the existing set."
            ),
            idempotency="idempotent",
            idempotency_evidence="probed",
            atomic=True,
            atomicity_note="Single transaction.",
            concurrency="last-write-wins",
            auth="bearer",
            audience="front end",
            delete=None,
            probed=True,
        ),
        verdict="work is proportional to the size of the payload.",
        verdict_class="caution",
    )
    deleting_write = EndpointRecord(
        surface=_surface("DELETE", "/api/titles/{title_id}/tags/{tag_id}", model=None),
        annotation=_annotation(queries=(_query(),)),
        usage=_usage("DELETE /api/titles/{title_id}/tags/{tag_id}"),
        write_contract=WriteContract(
            unknown_fields="n/a -- no request body",
            omission_semantics="No request body.",
            idempotency="idempotent",
            idempotency_evidence="probed",
            atomic=True,
            atomicity_note="Single transaction.",
            concurrency="last-write-wins",
            auth="bearer",
            audience="front end",
            delete=DeleteSemantics(
                destroys="nothing -- the Tag itself is untouched",
                detaches="the edge between this Title and the Tag",
                children="none",
                reachable_with_references=True,
                ui_vocabulary="Remove tag from this Title (never 'Delete tag')",
            ),
            probed=True,
        ),
        verdict="Safe to build on — it detaches an edge and destroys nothing.",
        verdict_class="safe",
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
        coverage=(
            CoverageMetric(
                population="Titles with library_root=true",
                attribute="resolve a display image",
                covered=955,
                total=1136,
                note="the browse grid's central design constraint",
            ),
        ),
    )
    return Inventory(
        generated_from="media-api @ app.openapi()",
        app_version="1.5.4",
        phases_run=("1", "2", "3", "4", "5", "6"),
        phases_skipped=(),
        endpoints=(listing, uniform_write, looping_write, deleting_write, unreferenced),
        indexes=(IndexInfo("assets_pkey", "assets", ("id",), True, source="primary key"),),
        data_shape=shape,
        unknowns=(),
        constraint_map=(
            ConstraintMapping(
                name="ix_tags_name",
                table="tags",
                kind="unique (index)",
                definition="UNIQUE (name)",
                endpoints=("POST /api/tags",),
                status=409,
                body='{"detail": "Unique constraint violated."}',
                distinguishable=False,
                ui_message="nothing usable -- the body names neither the field nor the cause",
            ),
        ),
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


def test_writes_are_sectioned_rather_than_collapsed(rendered: str) -> None:
    """Every write gets a section, and the collapsed table is gone.

    This reverses the rule that held before Phase 6. Writes used to be collapsed
    on the grounds that a single-row write with no loops has nothing
    endpoint-specific to get wrong -- true of its *query shape*, and false of its
    contract. What a form gets wrong is whether a partial submit erases fields,
    whether a retry duplicates, and whether a failure is legible, and those
    differ per route. The collapsed table said one sentence about fifty-eight
    endpoints and so said nothing about any of them.
    """
    assert "## Write endpoints" not in rendered
    assert "### POST /api/tags" in rendered
    assert "### PUT /api/assets/{asset_id}/tags" in rendered


def test_a_looping_write_keeps_its_own_section(rendered: str) -> None:
    """A write that issues per-item queries has a failure mode worth a section."""
    assert "### PUT /api/assets/{asset_id}/tags" in rendered


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


def test_variables_resolve_in_dependency_order_not_alphabetical() -> None:
    """A variable read from an endpoint another variable addresses.

    `metadata_id` comes from `/api/assets/{asset_id}/metadata`. Alphabetical order
    happens to work for that pair and fails for others -- `external_id` sorts
    before `title_id` -- so the order has to follow the dependencies.
    """
    order = [
        name
        for name, _ in probes._resolution_order(
            {
                "metadata_id": {"from_endpoint": "/api/assets/{asset_id}/metadata"},
                "asset_id": {"from_endpoint": "/api/assets/"},
                "aaa_first_alphabetically": {"from_endpoint": "/api/titles/{title_id}/ids"},
                "title_id": {"from_endpoint": "/api/titles/"},
            },
            [],
        )
    ]

    assert order.index("asset_id") < order.index("metadata_id")
    assert order.index("title_id") < order.index("aaa_first_alphabetically")


def test_a_variable_needing_an_undeclared_one_is_reported_not_silently_dropped() -> None:
    """A gap must name what is missing rather than becoming an unexplained UNKNOWN."""
    unknowns: list = []

    order = probes._resolution_order(
        {"metadata_id": {"from_endpoint": "/api/assets/{asset_id}/metadata"}}, unknowns
    )

    assert order == []
    assert len(unknowns) == 1
    assert "asset_id" in unknowns[0].resolution


def test_a_cycle_between_variables_is_reported_rather_than_looping() -> None:
    unknowns: list = []

    order = probes._resolution_order(
        {
            "a": {"from_endpoint": "/x/{b}"},
            "b": {"from_endpoint": "/y/{a}"},
        },
        unknowns,
    )

    assert order == []
    assert len(unknowns) == 2


def test_literals_and_independent_variables_still_resolve() -> None:
    """The common case has no dependencies and must be unaffected."""
    order = [
        name
        for name, _ in probes._resolution_order(
            {
                "external_id": {"literal": "nope"},
                "asset_id": {"from_endpoint": "/api/assets/"},
            },
            [],
        )
    ]

    assert sorted(order) == ["asset_id", "external_id"]


def test_every_probe_placeholder_has_a_declared_variable() -> None:
    """A probe naming a variable that does not exist can never run.

    Cheap to get wrong by hand, and the failure surfaces only as an `unavailable`
    probe buried in a report, so it is asserted against the shipped file.
    """
    config = probes.load_config(Path(probes.__file__).with_name("probes.yaml"))
    declared = set(config.variables)

    for spec in config.probes:
        used = probes._placeholders(spec.path)
        for value in spec.query.values():
            if isinstance(value, str):
                used |= probes._placeholders(value)
        missing = used - declared
        assert not missing, f"probe {spec.name!r} uses undeclared variable(s) {missing}"


# --------------------------------------------------------------------------
# What counts as a measurement
# --------------------------------------------------------------------------


def _result(**overrides) -> ProbeResult:
    base = dict(
        name="probe",
        endpoint_key="GET /api/x",
        method="GET",
        url="/api/x",
        status="ok",
        http_status=200,
        timing=Timing(runs=7, p50_ms=1.0, p95_ms=3.0, min_ms=1.0, max_ms=3.0),
    )
    return ProbeResult(**{**base, **overrides})


def test_a_probe_recording_a_failure_mode_is_not_a_measurement() -> None:
    """The defect behind #57.

    `search-transcripts-past-window` accepts 503 so the report can say how the
    max_result_window ceiling surfaces. Counting that as a measurement put
    "worst-case p95 3ms" in the summary for an endpoint that never returned a result.
    """
    probe = _result(http_status=503, records_failure_mode=True)

    assert probe.measured is False


def test_a_server_error_is_never_a_measurement_even_unflagged() -> None:
    """A 5xx is never the endpoint doing its work, flag or not."""
    assert _result(http_status=503).measured is False
    assert _result(http_status=500).measured is False


def test_a_deliberate_client_error_is_still_a_measurement() -> None:
    """The distinction is not the status code.

    A by-scheme lookup that misses runs the same query as one that hits, and an
    unsatisfiable range is the endpoint behaving correctly. Discarding these would
    send several endpoints back to UNKNOWN for no reason.
    """
    assert _result(http_status=404).measured is True
    assert _result(http_status=416).measured is True


def test_a_probe_that_did_not_run_is_not_a_measurement() -> None:
    assert _result(status="unavailable", http_status=None).measured is False
    assert _result(status="error", http_status=404).measured is False


def test_a_verdict_ignores_a_failure_mode_probe_when_choosing_the_worst() -> None:
    """End of the chain: the summary p95 must not come from the failure probe."""
    real = _result(
        name="page-1",
        http_status=200,
        timing=Timing(runs=7, p50_ms=80.0, p95_ms=120.0, min_ms=70.0, max_ms=130.0),
    )
    failure = _result(
        name="past-window",
        http_status=503,
        records_failure_mode=True,
        timing=Timing(runs=7, p50_ms=2.0, p95_ms=3.0, min_ms=2.0, max_ms=3.0),
    )

    worst = verdict._worst_probe((failure, real))

    assert worst is not None and worst.name == "page-1"


def test_a_verdict_is_unmeasured_when_only_failure_probes_ran() -> None:
    """Elasticsearch unreachable: every probe 503s, so nothing was measured."""
    failure = _result(http_status=503, records_failure_mode=True)

    assert verdict._worst_probe((failure,)) is None


def test_the_shipped_failure_probe_is_flagged() -> None:
    """The one probe in the file that accepts a status meaning the endpoint failed."""
    config = probes.load_config(Path(probes.__file__).with_name("probes.yaml"))
    by_name = {spec.name: spec for spec in config.probes}

    assert by_name["search-transcripts-past-window"].records_failure_mode is True

    # The probes that accept a client error are deliberately *not* flagged, because
    # they do measure. A by-scheme lookup that misses runs the same query as one
    # that hits, and an unsatisfiable range is the endpoint behaving correctly.
    # Flagging these would send those endpoints back to UNKNOWN.
    for name in (
        "asset-by-scheme",
        "title-by-scheme",
        "external-id-resolve",
        "fetch-asset-range-unsatisfiable",
    ):
        assert by_name[name].records_failure_mode is False, name


# --------------------------------------------------------------------------
# One declaration, one index
# --------------------------------------------------------------------------


def test_a_column_declared_unique_and_indexed_yields_one_index() -> None:
    """The false duplicate behind #59.

    `mapped_column(String(50), unique=True, index=True)` creates a single unique
    index, already carrying the name Postgres will use. Synthesising a second
    `<table>_<col>_key` alongside it invented an index that does not exist, and the
    pair read as duplicated storage worth a migration to remove.
    """
    entries = indexes._deduplicate(
        [
            IndexInfo("ix_tags_name", "tags", ("name",), True, source="models"),
            IndexInfo("tags_name_key", "tags", ("name",), True, source="column unique=True"),
        ]
    )

    assert len(entries) == 1
    assert (
        entries[0].name == "ix_tags_name"
    ), "the real Index names it; the synthesised one does not"


def test_an_unnamed_unique_constraint_does_not_shadow_the_real_name() -> None:
    """`assets.path` is `unique=True` with no index=True.

    That renders as an unnamed UniqueConstraint, which the constraint pass called
    `uq_assets` -- a name Postgres never uses. Postgres names it `assets_path_key`,
    which is what the column pass produces.
    """
    entries = indexes._deduplicate(
        [
            IndexInfo("uq_assets", "assets", ("path",), True, source="unique constraint"),
            IndexInfo("assets_path_key", "assets", ("path",), True, source="column unique=True"),
        ]
    )

    assert len(entries) == 1
    assert entries[0].name == "assets_path_key"


def test_genuinely_distinct_indexes_on_one_column_are_kept() -> None:
    """Deduplication must not collapse indexes that really are different.

    `tags` carries both a unique btree on `name` and a non-unique expression index
    on `lower(name)`. They serve different queries and both exist.
    """
    entries = indexes._deduplicate(
        [
            IndexInfo("ix_tags_name", "tags", ("name",), True, source="models"),
            IndexInfo(
                "ix_tags_name_lower",
                "tags",
                (),
                False,
                expression="lower(tags.name)",
                source="models",
            ),
        ]
    )

    assert len(entries) == 2


def test_a_named_composite_constraint_survives() -> None:
    """A named UniqueConstraint is real and nothing else describes it."""
    entries = indexes._deduplicate(
        [
            IndexInfo(
                "uq_external_identifier_scheme_id",
                "external_identifiers",
                ("scheme_id", "external_id"),
                True,
                source="unique constraint",
            ),
        ]
    )

    assert [e.name for e in entries] == ["uq_external_identifier_scheme_id"]


def test_partial_indexes_differing_only_by_predicate_are_distinct() -> None:
    """`title_contents` carries two partial uniques over overlapping columns."""
    entries = indexes._deduplicate(
        [
            IndexInfo(
                "uq_parent_asset_once",
                "title_contents",
                ("parent_title_id", "asset_id"),
                True,
                where="asset_id IS NOT NULL",
                source="models",
            ),
            IndexInfo(
                "uq_parent_child_title_once",
                "title_contents",
                ("parent_title_id", "child_title_id"),
                True,
                where="child_title_id IS NOT NULL",
                source="models",
            ),
        ]
    )

    assert len(entries) == 2


def test_the_models_report_one_index_per_constrained_column() -> None:
    """Asserted against the real models, not a fixture.

    Verified against `pg_index` on a schema built from the migrations: each of these
    columns carries exactly one unique index.
    """
    static_surface.load_app()
    entries = indexes.from_metadata()

    for table, column in (
        ("assets", "path"),
        ("tags", "name"),
        ("id_schemes", "code"),
        ("title_types", "code"),
    ):
        matching = [e for e in entries if e.table == table and e.columns == (column,) and e.unique]
        assert len(matching) == 1, f"{table}.{column} reported {[e.name for e in matching]}"


# --------------------------------------------------------------------------
# Declared filter resolutions, and the index properties that decide coverage
# --------------------------------------------------------------------------


def test_shipped_filter_declarations_are_valid() -> None:
    """The sibling of the probe check: every shipped declaration must parse."""
    declarations = filter_map.load()
    assert declarations, "filters.yaml ships declarations and must not be empty"
    for (endpoint, param), declared in declarations.items():
        assert declared.endpoint == endpoint
        assert declared.param == param
        assert declared.established_by, f"{endpoint} `{param}` declares no reason"
        if declared.is_database_filter:
            assert declared.table and declared.column


def test_a_declaration_without_a_reason_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "filters.yaml"
    path.write_text(
        "declarations:\n"
        "  - endpoint: 'GET /api/artwork'\n"
        "    param: kind\n"
        "    table: artwork\n"
        "    column: artwork_kind_id\n"
    )
    with pytest.raises(filter_map.FilterMapError, match="established_by"):
        filter_map.load(path)


def test_a_declaration_for_an_endpoint_that_no_longer_exists_fails_the_run() -> None:
    """A declaration is a standing claim; it must not outlive what it describes."""
    declared = filter_map.FilterDeclaration(
        endpoint="GET /api/gone",
        param="kind",
        established_by="why",
        table="artwork",
        column="artwork_kind_id",
    )
    with pytest.raises(filter_map.FilterMapError, match="no such endpoint"):
        filter_map.verify(
            {("GET /api/gone", "kind"): declared},
            endpoint_params={"GET /api/artwork": {"kind"}},
            index_names=set(),
        )


def test_a_declaration_naming_a_dropped_index_fails_the_run() -> None:
    declared = filter_map.FilterDeclaration(
        endpoint="GET /api/assets/",
        param="filename_ext",
        established_by="why",
        table="assets",
        column="filename",
        index="ix_assets_filename_ext",
    )
    with pytest.raises(filter_map.FilterMapError, match="not in the live inventory"):
        filter_map.verify(
            {("GET /api/assets/", "filename_ext"): declared},
            endpoint_params={"GET /api/assets/": {"filename_ext"}},
            index_names={"some_other_index"},
        )


def test_the_shipped_declarations_match_the_live_surface() -> None:
    """Runs the same verification the CLI does, against the real app and models."""
    routes, _ = static_surface.collect(static_surface.load_app())
    filter_map.verify(
        filter_map.load(),
        endpoint_params={
            route.key: {p.name for p in route.params if p.location == "query"} for route in routes
        },
        index_names={i.name for i in indexes.from_metadata()},
    )


def test_a_predicate_on_a_function_of_a_column_resolves_to_that_function() -> None:
    """``func.lower(X.col).like(...)`` is a filter on lower(col), not on col."""
    node = ast.parse("func.lower(AssetORM.path).like(f'{prefix}%')").body[0].value
    assert _describe_expression_predicate(node) == ("AssetORM", "path", "like_prefix", "lower")


def test_a_bare_column_predicate_is_not_mistaken_for_an_expression() -> None:
    node = ast.parse("AssetORM.path.ilike(pattern)").body[0].value
    assert _describe_expression_predicate(node) is None


def test_a_function_predicate_is_judged_against_an_expression_index() -> None:
    lookup = IndexLookup(
        (
            IndexInfo(
                name="ix_assets_path_lower",
                table="assets",
                columns=(),
                unique=False,
                expression="lower(assets.path)",
            ),
        )
    )
    covered, index, _ = lookup.judge("assets", "path", "like_prefix", function="lower")
    assert covered is True
    assert index == "ix_assets_path_lower"


def test_a_function_predicate_never_falls_back_to_the_bare_column_index() -> None:
    """The trap this exists to prevent: an index on `path` cannot serve lower(path)."""
    lookup = IndexLookup(
        (IndexInfo(name="ix_assets_path", table="assets", columns=("path",), unique=False),)
    )
    covered, index, note = lookup.judge("assets", "path", "==", function="lower")
    assert covered is False
    assert index is None
    assert "lower(assets.path)" in note


def test_a_partial_index_is_not_offered_for_an_unconstrained_query() -> None:
    """A partial index serves only a query implying its WHERE, which is unknowable here."""
    lookup = IndexLookup(
        (
            IndexInfo(
                name="uniq_pending_per_asset",
                table="media_transform_requests",
                columns=("asset_id", "transform_type"),
                unique=True,
                where="actioned = false",
            ),
        )
    )
    assert lookup.covering("media_transform_requests", "asset_id") is None


def test_a_gin_index_is_not_offered_for_an_ordering() -> None:
    """GIN has no order, so it cannot back `sort=name` however it is spelled."""
    lookup = IndexLookup(
        (
            IndexInfo(
                name="ix_titles_name_trgm",
                table="titles",
                columns=("name",),
                unique=False,
                method="gin",
            ),
            IndexInfo(name="ix_titles_name", table="titles", columns=("name",), unique=False),
        )
    )
    index = lookup.covering("titles", "name")
    assert index is not None and index.name == "ix_titles_name"


def test_two_indexes_on_one_column_survive_when_their_methods_differ() -> None:
    """Deduping them by columns alone let allocation order decide which was reported."""
    names = {
        i.name for i in indexes.from_metadata() if i.table == "titles" and i.columns == ("name",)
    }
    assert names == {"ix_titles_name", "ix_titles_name_trgm"}


def test_an_index_method_survives_a_json_round_trip() -> None:
    """``--from-json`` promises presentation-only changes, so it must lose nothing.

    Dropping the method here would be invisible rather than loud: every GIN index
    would reload as a btree, and ``covering()`` would start offering one for an
    ordering it cannot serve -- reintroducing the bug through the one path the
    README says re-runs no phase.
    """
    payload = {
        "name": "ix_titles_name_trgm",
        "table": "titles",
        "columns": ["name"],
        "unique": False,
        "method": "gin",
        "source": "models",
    }
    assert load._index(payload).method == "gin"


def test_a_json_written_before_the_method_existed_still_loads() -> None:
    payload = {"name": "ix_titles_name", "table": "titles", "columns": ["name"], "unique": False}
    assert load._index(payload).method == "btree"


# --------------------------------------------------------------------------
# Trigram coverage (#171)
# --------------------------------------------------------------------------


def _trigram_lookup() -> IndexLookup:
    return IndexLookup(
        (
            IndexInfo(
                name="ix_titles_name_trgm",
                table="titles",
                columns=("name",),
                unique=False,
                method="gin",
                ops=("gin_trgm_ops",),
            ),
        )
    )


def test_a_substring_match_is_served_by_a_trigram_index() -> None:
    """The report used to recommend adding an index that already existed."""
    covered, index, note = _trigram_lookup().judge("titles", "name", "ilike_contains")
    assert covered is True
    assert index == "ix_titles_name_trgm"
    assert "three characters or more" in note


def test_a_suffix_match_is_served_by_a_trigram_index() -> None:
    covered, index, _ = _trigram_lookup().judge("titles", "name", "ilike_suffix")
    assert covered is True
    assert index == "ix_titles_name_trgm"


def test_a_column_without_a_trigram_index_keeps_the_old_advice() -> None:
    """`tags.name` genuinely has none, and its wording must not change."""
    lookup = IndexLookup(
        (IndexInfo(name="ix_tags_name", table="tags", columns=("name",), unique=False),)
    )
    covered, index, note = lookup.judge("tags", "name", "ilike_contains")
    assert covered is False
    assert index is None
    assert "a trigram index would be needed" in note


def test_a_gin_index_that_is_not_a_trigram_index_does_not_serve_a_substring_match() -> None:
    """A GIN index over jsonb serves containment and nothing resembling a LIKE.

    Deciding this from the index's name would be guessing, which is the failure this
    area keeps repeating; it is decided from the recorded operator class.
    """
    lookup = IndexLookup(
        (
            IndexInfo(
                name="ix_assets_metadata_gin",
                table="assets",
                columns=("metadata",),
                unique=False,
                method="gin",
                ops=("jsonb_path_ops",),
            ),
        )
    )
    covered, index, _ = lookup.judge("assets", "metadata", "ilike_contains")
    assert covered is False
    assert index is None


def test_the_operator_classes_are_read_from_the_models() -> None:
    trigram = {
        i.name: i.ops for i in indexes.from_metadata() if i.method == "gin" and i.source == "models"
    }
    assert trigram, "the schema declares GIN indexes and they must carry their opclass"
    assert all(any(o.endswith("trgm_ops") for o in ops) for ops in trigram.values())


def test_operator_classes_survive_a_json_round_trip() -> None:
    payload = {
        "name": "ix_titles_name_trgm",
        "table": "titles",
        "columns": ["name"],
        "unique": False,
        "method": "gin",
        "ops": ["gin_trgm_ops"],
    }
    assert load._index(payload).ops == ("gin_trgm_ops",)


# --------------------------------------------------------------------------
# Phase 6 -- write semantics
# --------------------------------------------------------------------------
#
# The safety design is the part of this phase worth testing hardest. Everything
# else produces a wrong report when it breaks; this produces a wrong database.


def test_writes_are_refused_without_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configuration alone never authorises a write."""
    monkeypatch.setenv(write_semantics.BASE_URL_ENV, "http://127.0.0.1:9")
    monkeypatch.setenv(write_semantics.DATABASE_URL_ENV, "postgresql://u@h:5432/scratch")
    with pytest.raises(write_semantics.WriteTargetError, match="--allow-writes"):
        write_semantics.resolve_target(allow_writes=False)


def test_writes_are_refused_without_the_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(write_semantics.BASE_URL_ENV, raising=False)
    monkeypatch.delenv(write_semantics.DATABASE_URL_ENV, raising=False)
    with pytest.raises(write_semantics.WriteTargetError, match=write_semantics.BASE_URL_ENV):
        write_semantics.resolve_target(allow_writes=True)


@pytest.mark.parametrize(
    "write_dsn, read_dsn",
    [
        # The same database, spelled differently every way that matters. A string
        # comparison passes all of these and then writes to the read-side database.
        (
            "postgresql+psycopg://u:p@localhost:5432/media",
            "postgresql://other:q@127.0.0.1:5432/media",
        ),
        ("postgresql://u@localhost/media", "postgresql://u@localhost:5432/media"),
        (
            "postgresql://u@host:5432/media?sslmode=require",
            "postgresql://u@host:5432/media",
        ),
    ],
)
def test_a_respelled_read_dsn_is_still_the_read_database(write_dsn: str, read_dsn: str) -> None:
    """Identity is host, port and database -- not the characters of the URL."""
    assert write_semantics.normalise_dsn(write_dsn) == write_semantics.normalise_dsn(read_dsn)


def test_distinct_databases_are_distinguished() -> None:
    assert write_semantics.normalise_dsn(
        "postgresql://u@host:5432/scratch"
    ) != write_semantics.normalise_dsn("postgresql://u@host:5432/media")


def test_the_forbidden_digest_matches_the_published_fingerprint() -> None:
    """The refusal list must hold the same kind of value the report publishes.

    ``data_shape.fingerprint`` publishes ``sha256(dsn)[:12]``. If the constant
    guarding against a known production DSN held a Postgres system identifier
    instead it could never match anything -- a gate that reads as protection and
    provides none, which is worse than no gate at all.
    """
    dsn = "postgresql://someone@example:5432/db"
    assert write_semantics.dsn_digest(dsn) in data_shape.fingerprint(dsn)


def test_write_probes_are_not_reachable_through_the_phase_4_allowlist() -> None:
    """``allowlist`` stays empty, and the write scenarios live outside it.

    That empty list is what makes Phase 4 structurally unable to mutate the
    production-backed instance it runs against. Routing write probes through it
    would trade the guarantee for a code path.
    """
    path = Path(probes.__file__).with_name("probes.yaml")
    config = probes.load_config(path)
    assert config.allowlist == frozenset()
    assert probes.load_write_scenarios(path), "the write scenarios must still be declared"


def test_every_shipped_write_scenario_is_valid() -> None:
    scenarios = probes.load_write_scenarios(Path(probes.__file__).with_name("probes.yaml"))
    kinds = {s["kind"] for s in scenarios}
    assert kinds <= {"repeat", "violation", "omission"}
    for scenario in scenarios:
        assert scenario["endpoint"].split(" ", 1)[0] in {"POST", "PUT", "PATCH", "DELETE"}


def test_a_write_scenario_may_only_clean_up_listed_tables() -> None:
    """Table and column names reach an identifier position, where no binding is
    possible, so they are checked against a closed list rather than escaped."""
    for scenario in probes.load_write_scenarios(Path(probes.__file__).with_name("probes.yaml")):
        for step in scenario.get("setup") or ():
            cleanup = step.get("sql_cleanup")
            if cleanup:
                assert cleanup["table"] in write_probes._CLEANABLE_TABLES
        for entry in scenario.get("sql_cleanup") or ():
            assert entry["table"] in write_probes._CLEANABLE_TABLES
            assert entry.get("column", "id") in write_probes._CLEANABLE_COLUMNS
        if scenario.get("act_sql_table"):
            assert scenario["act_sql_table"] in write_probes._CLEANABLE_TABLES


# -- the contract derivation ------------------------------------------------


def test_patch_and_put_sharing_one_body_model_have_opposite_null_semantics() -> None:
    """The finding the whole phase exists to surface.

    ``PATCH`` and ``PUT /api/titles/{title_id}`` both take ``TitlePatchPublic``.
    The PATCH leaves an omitted field alone; the PUT writes it as null. Nothing
    in the OpenAPI document distinguishes them -- only the positional argument
    the router hands the service does, which is why the tracer has to read
    positional arguments and not only keywords.
    """
    api = static_surface.load_app()
    routes, spec = static_surface.collect(api)
    root = Path.cwd()
    graph = annotate.CodeGraph(root / "app", root)

    by_key = {r.key: r for r in routes}
    patch = write_contracts.derive(by_key["PATCH /api/titles/{title_id}"], None, spec, graph)
    put = write_contracts.derive(by_key["PUT /api/titles/{title_id}"], None, spec, graph)

    assert by_key["PATCH /api/titles/{title_id}"].request_body == "TitlePatchPublic"
    assert by_key["PUT /api/titles/{title_id}"].request_body == "TitlePatchPublic"

    synopsis_patch = next(f for f in patch.fields if f.name == "synopsis")
    synopsis_put = next(f for f in put.fields if f.name == "synopsis")
    assert synopsis_patch.omitted_means == "unchanged"
    assert synopsis_put.omitted_means == "set to null"
    assert "cannot be cleared" in (synopsis_patch.null_means or "")


def test_every_write_route_resolves_its_omission_semantics() -> None:
    """No write endpoint may report UNKNOWN for what omitting a field does.

    This is the single most destructive thing a management UI can get wrong, and
    the repository answers it for every route -- via a keyword, a positional
    argument, a callee default, a hard-coded ``model_dump`` inside the service,
    or the absence of a body. An UNKNOWN here means the tracer stopped working,
    not that the answer is unknowable.
    """
    api = static_surface.load_app()
    routes, spec = static_surface.collect(api)
    root = Path.cwd()
    graph = annotate.CodeGraph(root / "app", root)

    unresolved = [
        route.key
        for route in routes
        if route.method not in ("GET", "HEAD")
        and write_contracts.derive(route, None, spec, graph).omission_semantics == "UNKNOWN"
    ]
    assert not unresolved, f"omission semantics unresolved for: {unresolved}"


def test_the_constraint_inventory_finds_the_partial_unique_indexes() -> None:
    """A partial unique index guards a duplicate exactly as a constraint does.

    ``uq_parent_asset_once`` is spelled as an ``Index`` because Postgres has no
    partial unique constraint. Omitting it because of how it is spelled would
    drop one of the constraints a UI is most likely to hit.
    """
    static_surface.load_app()
    names = {c.name for c in write_contracts.constraints_from_metadata()}
    assert {"uq_parent_asset_once", "uq_artwork_entity_storage_path"} <= names


def test_an_unusable_error_body_is_recognised() -> None:
    """A status a client can branch on is not the same as a message it can show."""
    assert not write_assemble._message_is_usable(
        '{"detail": [{"loc": [], "msg": "CHECK constraint violated.", "type": "domain_error"}]}'
    )
    assert write_assemble._message_is_usable('{"detail": "A title cannot contain itself."}')


# -- rendering --------------------------------------------------------------


def test_every_write_endpoint_renders_a_write_contract(rendered: str) -> None:
    """The block replaces the collapsed table, so no write may be without one."""
    inventory = _sample_inventory()
    writes = sum(1 for e in inventory.endpoints if e.write_contract is not None)
    assert rendered.count("#### Write contract") == writes


def test_writes_are_no_longer_collapsed(rendered: str) -> None:
    """The uniform-write table is retired; each write has its own section."""
    assert "## Write endpoints" not in rendered
    assert "### POST /api/tags" in rendered


def test_both_new_appendices_render(rendered: str) -> None:
    assert "## Error taxonomy" in rendered
    assert "## Constraint map" in rendered
    assert "## Coverage" in rendered


def test_an_unusable_error_body_is_flagged_in_the_taxonomy(rendered: str) -> None:
    """The column the taxonomy exists for must survive rendering."""
    taxonomy = rendered.split("## Error taxonomy", 1)[1].split("## Constraint map", 1)[0]
    assert "**no**" in taxonomy, "an error a UI cannot show must be flagged, not softened"


def test_delete_semantics_state_what_the_button_says(rendered: str) -> None:
    """`Remove from collection` and `Delete permanently` are different buttons."""
    assert "The button must say" in rendered
    assert "never 'Delete tag'" in rendered


def test_a_skipped_write_phase_renders_correctly_without_it() -> None:
    """The document must render without Phase 6, marking contracts absent."""
    inventory = _sample_inventory()
    stripped = replace(
        inventory,
        endpoints=tuple(replace(e, write_contract=None) for e in inventory.endpoints),
        constraint_map=(),
        phases_run=("1", "2", "3", "4", "5"),
        phases_skipped=("6 (write semantics)",),
    )
    out = render.to_markdown(stripped)
    assert "#### Write contract" not in out
    assert "## Constraint map" not in out
    assert "### POST /api/tags" in out, "sections must still render without the phase"


def test_a_write_contract_survives_a_json_round_trip() -> None:
    """``--from-json`` must not silently drop Phase 6.

    The loader reads field by field, so a record it does not know about vanishes
    on the next presentation-only re-render of the committed document.
    """
    inventory = _sample_inventory()
    payload = json.loads(render.to_json(inventory))
    endpoint = next(e for e in payload["endpoints"] if e["surface"]["path"] == "/api/tags")
    restored = load._endpoint(endpoint)

    assert restored.write_contract is not None
    assert restored.write_contract.idempotency == "guarded"
    assert restored.write_contract.errors[0].usable_message is False
    assert restored.write_contract.fields[0].constraints == {"maxLength": 50}

    constraints = tuple(load._constraint_mapping(c) for c in payload["constraint_map"])
    assert constraints[0].distinguishable is False


def test_a_scaling_probe_downgrades_a_static_n_plus_one_flag() -> None:
    """C1: measurement beats comprehension detection where the two disagree.

    A query issued per row costs about one round trip per row. When two probes
    of one endpoint differ only in page size and the cost is flat, the queries
    are not per-row, whatever the comprehension around them looks like.
    """
    fast = ProbeResult(
        name="titles-page-1",
        endpoint_key="GET /api/titles/",
        method="GET",
        url="/api/titles/?limit=50",
        status="ok",
        http_status=200,
        timing=Timing(runs=7, p50_ms=110.0, p95_ms=120.0, min_ms=100.0, max_ms=130.0),
        item_count=50,
    )
    big = replace(
        fast,
        name="titles-scaling-200-rows",
        timing=Timing(runs=7, p50_ms=127.0, p95_ms=140.0, min_ms=120.0, max_ms=150.0),
        item_count=200,
    )
    assert verdict._scaling_contradiction((fast, big), 25.0) is not None

    # A genuine per-row cost is not downgraded.
    slow = replace(
        big,
        timing=Timing(runs=7, p50_ms=3900.0, p95_ms=4200.0, min_ms=3800.0, max_ms=4300.0),
    )
    assert verdict._scaling_contradiction((fast, slow), 25.0) is None


def test_a_contradiction_needs_a_measured_round_trip() -> None:
    """Without Phase 3 there is no baseline, so nothing is downgraded."""
    probe = ProbeResult(
        name="p",
        endpoint_key="GET /api/titles/",
        method="GET",
        url="/",
        status="ok",
        http_status=200,
        timing=Timing(runs=7, p50_ms=110.0, p95_ms=1.0, min_ms=1.0, max_ms=1.0),
        item_count=50,
    )
    assert verdict._scaling_contradiction((probe,), None) is None


def test_every_worker_fleet_declaration_names_a_live_route() -> None:
    """A declaration nothing else can check must at least be checked for existence.

    ``_WORKER_FLEET`` is hand-written: nothing in the code says a route is for the
    runner fleet rather than for the front end. That is the same situation
    ``filters.yaml`` is in, and the rule there is that a declaration naming an
    endpoint which no longer exists fails the run. A renamed worker route would
    otherwise silently report ``audience: front end``, which is precisely what 6h
    exists to prevent.
    """
    api = static_surface.load_app()
    routes, _spec = static_surface.collect(api)
    live = {route.key for route in routes}
    missing = sorted(write_contracts._WORKER_FLEET - live)
    assert not missing, f"_WORKER_FLEET names routes that no longer exist: {missing}"


def test_a_probed_body_excerpt_is_stable_across_runs() -> None:
    """Row ids must not reach the committed artefact.

    The better error messages on this API quote the id they are about --
    "Title 3 already has an intrinsic parent, recorded by containment row 3" --
    and those ids come from a scratch database whose sequences are not reset
    between runs. Committed verbatim, each Phase 6 run would produce a diff that
    says nothing about the API.
    """
    first = write_probes._excerpt(
        {"detail": "Title 3 already has an intrinsic parent, recorded by containment row 3."}
    )
    second = write_probes._excerpt(
        {"detail": "Title 41 already has an intrinsic parent, recorded by containment row 41."}
    )
    assert first == second
    assert "intrinsic parent" in first, "the message shape must survive the scrub"
