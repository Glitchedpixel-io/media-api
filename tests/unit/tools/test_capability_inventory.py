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
from pathlib import Path

import pytest

from tools.capability_inventory import data_shape, probes, static_surface
from tools.capability_inventory.annotate import _describe_predicate, _pattern_shape
from tools.capability_inventory.indexes import IndexLookup
from tools.capability_inventory.models import IndexInfo

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
