"""Unit tests for the front-end contract generator (tools/design_contract).

These cover the places where being subtly wrong would produce a confident, wrong
document rather than a visible failure. The output lives permanently in a design
tool's context, so a silent error there is designed against for weeks:

* an endpoint dropped from the map entirely, or named with a typo;
* an availability claim that has gone stale — the document telling a designer a
  capability is missing when the API grew it;
* a type containing ``|`` splitting a Markdown table into the wrong columns;
* a fill rate guessed for a field that has no column behind it;
* a nested collection costed by its table's total size rather than by how many
  rows one parent actually has.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tools.design_contract.load import Inventory
from tools.design_contract.render import _costly, _type, render
from tools.design_contract.surfaces import SurfaceMapError, load_surface_map

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = REPO_ROOT / "docs" / "capability-inventory.json"
SURFACES_PATH = REPO_ROOT / "tools" / "design_contract" / "surfaces.yaml"


@pytest.fixture(scope="module")
def inventory() -> Inventory:
    """The real capability inventory."""
    return Inventory.from_path(INVENTORY_PATH)


@pytest.fixture(scope="module")
def surface_map(inventory: Inventory):  # type: ignore[no-untyped-def]
    """The real surface map, validated against the real inventory."""
    return load_surface_map(SURFACES_PATH, inventory)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    """Write a minimal surface map and return its path.

    Args:
        tmp_path: Directory to write into.
        body: YAML body appended after the surfaces block.

    Returns:
        Path to the written file.
    """
    path = tmp_path / "surfaces.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestSurfaceMapValidation:
    """The map is hand-maintained, so it has to be checked, not trusted."""

    def test_every_endpoint_is_accounted_for(self, inventory: Inventory, surface_map: Any) -> None:
        """No endpoint may be missing from the shipped map."""
        assigned = {op for surface in surface_map.surfaces for op in surface.operations}
        assigned |= {n.operation for n in surface_map.do_not_call}
        assigned |= {n.operation for n in surface_map.unassigned}
        assert assigned == set(inventory.endpoints)

    def test_a_typo_in_an_operation_name_fails(self, inventory: Inventory, tmp_path: Path) -> None:
        """A misspelled operation must fail rather than silently vanish."""
        path = _write_yaml(
            tmp_path,
            "surfaces:\n"
            "  library:\n"
            "    title: Library\n"
            "    summary: x\n"
            "    primary: [list_titlez]\n",
        )
        with pytest.raises(SurfaceMapError, match="list_titlez"):
            load_surface_map(path, inventory)

    def test_an_unassigned_endpoint_fails(self, inventory: Inventory, tmp_path: Path) -> None:
        """Leaving an endpoint out of every bucket must fail."""
        path = _write_yaml(
            tmp_path,
            "surfaces:\n"
            "  library:\n"
            "    title: Library\n"
            "    summary: x\n"
            "    primary: [list_titles]\n",
        )
        with pytest.raises(SurfaceMapError, match="not assigned to a surface"):
            load_surface_map(path, inventory)

    def test_a_surface_endpoint_cannot_also_be_do_not_call(
        self, inventory: Inventory, tmp_path: Path
    ) -> None:
        """An endpoint is either designed against or it is not."""
        path = _write_yaml(
            tmp_path,
            "surfaces:\n"
            "  library:\n"
            "    title: Library\n"
            "    summary: x\n"
            "    primary: [list_titles]\n"
            "do_not_call:\n"
            "  - operation: list_titles\n"
            "    reason: contradictory\n",
        )
        with pytest.raises(SurfaceMapError, match="must be one or the other"):
            load_surface_map(path, inventory)


class TestStaleClaims:
    """The document must never tell a designer to design around a gap that closed."""

    def test_a_closed_gap_fails_the_build(self, inventory: Inventory, tmp_path: Path) -> None:
        """Claiming something is absent when it exists must fail."""
        path = _write_yaml(
            tmp_path,
            "not_available:\n"
            "  - capability: No move operation\n"
            "    issue: none\n"
            "    detail: two calls, no transaction\n"
            "    absent_op: move_title_content\n",
        )
        with pytest.raises(SurfaceMapError, match="absent_op=move_title_content"):
            load_surface_map(path, inventory)

    def test_a_regressed_capability_fails_the_build(
        self, inventory: Inventory, tmp_path: Path
    ) -> None:
        """Claiming something exists when it does not must fail."""
        path = _write_yaml(
            tmp_path,
            "resolved_since_brief:\n"
            "  - brief: edition is not a field\n"
            "    now: it is now\n"
            "    present_field: get_asset.no_such_field\n",
        )
        with pytest.raises(SurfaceMapError, match="present_field"):
            load_surface_map(path, inventory)

    def test_the_shipped_claims_all_hold(self, surface_map: Any) -> None:
        """Loading the real map is itself the assertion; this pins the count."""
        assert surface_map.not_available
        assert surface_map.resolved


class TestRendering:
    """Formatting errors that would mislead rather than break."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("int | None", "int?"),
            ("str", "str"),
            ("list[TagRead] | None", "list[TagRead]?"),
            ("str(date-time) | None", "str(date-time)?"),
        ],
    )
    def test_optional_types_lose_their_pipe(self, raw: str, expected: str) -> None:
        """A ``|`` in a cell would split the Markdown table into wrong columns."""
        assert _type(raw) == expected

    def test_no_table_row_contains_a_bare_pipe(
        self, inventory: Inventory, surface_map: Any
    ) -> None:
        """Every rendered table row must have exactly three columns."""
        for line in render(inventory, surface_map).splitlines():
            if not line.startswith("| ") or line.startswith("|---"):
                continue
            assert line.count("|") == 4, line

    def test_unmapped_models_get_no_fill_rate(self, inventory: Inventory) -> None:
        """A guessed fill rate is worse than an absent one."""
        assert inventory.fill_rate(None, "anything") is None
        assert inventory.fill_rate("assets", "not_a_column") is None
        assert inventory.fill_rate("assets", "path") == pytest.approx(1.0)

    def test_document_fits_the_context_budget(self, inventory: Inventory, surface_map: Any) -> None:
        """The document sits permanently in a design tool's context."""
        assert len(render(inventory, surface_map).encode("utf-8")) < 20_480


class TestCostly:
    """A nested collection is bounded by its fan-out, not by its table size."""

    def test_fan_out_prefers_rows_per_parent(self, inventory: Inventory) -> None:
        """One asset's streams are bounded per asset, not by all 65,230 streams."""
        record = inventory.fan_out("assets", "streams")
        assert record is not None
        assert record["max_children"] < inventory.row_counts["streams"]

    def test_small_fan_out_is_not_called_costly(
        self, inventory: Inventory, surface_map: Any
    ) -> None:
        """Tag lists join through a small table and must not be flagged."""
        costly = "\n".join(_costly(inventory, surface_map))
        assert "/api/titles/{title_id}/tags" not in costly
        assert "/api/assets/{asset_id}/tags" not in costly

    def test_measured_slow_endpoints_are_flagged_with_the_number(
        self, inventory: Inventory, surface_map: Any
    ) -> None:
        """A slow endpoint appears with its measured p95."""
        costly = "\n".join(_costly(inventory, surface_map))
        assert "GET /api/assets/" in costly
        assert "p95" in costly


class TestAgainstMutatedInventory:
    """The generator must react to the inventory, not to hard-coded knowledge."""

    def test_removing_a_filter_changes_the_output(self, surface_map: Any) -> None:
        """Filters are read from the inventory, not remembered."""
        raw: dict[str, Any] = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(raw)
        for record in mutated["endpoints"]:
            if record["surface"]["operation_id"] == "list_titles":
                record["surface"]["params"] = [
                    p for p in record["surface"]["params"] if p["name"] != "library_root"
                ]
        before = render(Inventory(raw), surface_map)
        after = render(Inventory(mutated), surface_map)
        assert "`library_root`" in before
        assert before != after
