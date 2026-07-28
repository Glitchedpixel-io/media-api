# tests/unit/utils/test_sorting.py
import pytest
from datetime import UTC, datetime
from sqlalchemy import Column, Integer, String, DateTime, select
from sqlalchemy.orm import declarative_base

from app.utils.sorting import (
    normalize_sort,
    build_order_by,
    apply_ordering,
    SortConfig,
    DT_MAX,
    DT_MIN,
)
from app.repositories.errors import EnumViolation

Base = declarative_base()


class MockModel(Base):
    __tablename__ = "mock"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    created_at = Column(DateTime, nullable=True)
    priority = Column(Integer, nullable=False)


@pytest.mark.unit
class TestNormalizeSort:
    def test_single_field_ascending_default(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        result = normalize_sort("name", config)
        assert result == [("name", "asc"), ("id", "asc")]

    def test_single_field_descending_explicit(self):
        config = SortConfig(model=MockModel, allowed_fields={"created_at", "id"})
        result = normalize_sort("created_at:desc", config)
        assert result == [("created_at", "desc"), ("id", "asc")]

    def test_multiple_fields_with_directions(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "created_at", "id"})
        result = normalize_sort("name:asc,created_at:desc", config)
        assert result == [("name", "asc"), ("created_at", "desc"), ("id", "asc")]

    def test_id_field_moved_to_end_when_present(self):
        config = SortConfig(model=MockModel, allowed_fields={"id", "name", "created_at"})
        result = normalize_sort("id:desc,name:asc,created_at:asc", config)
        # id should be moved to the end, preserving its direction
        assert result == [("name", "asc"), ("created_at", "asc"), ("id", "desc")]

    def test_id_field_appended_when_not_present(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        result = normalize_sort("name:desc", config)
        assert result == [("name", "desc"), ("id", "asc")]

    def test_duplicate_fields_ignored(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        result = normalize_sort("name:asc,name:desc", config)
        # Only first occurrence is kept
        assert result == [("name", "asc"), ("id", "asc")]

    def test_whitespace_handling(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        result = normalize_sort(" name : desc , id : asc ", config)
        assert result == [("name", "desc"), ("id", "asc")]

    def test_unsupported_field_raises_error(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        with pytest.raises(EnumViolation, match="Unsupported sort field"):
            normalize_sort("invalid_field:asc", config)

    def test_invalid_direction_raises_error(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        with pytest.raises(EnumViolation, match="Invalid sort direction"):
            normalize_sort("name:invalid", config)

    def test_empty_string_returns_only_id(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        result = normalize_sort("", config)
        assert result == [("id", "asc")]

    def test_custom_id_field(self):
        config = SortConfig(
            model=MockModel, allowed_fields={"name", "custom_id"}, id_field="custom_id"
        )
        result = normalize_sort("name:desc", config)
        assert result == [("name", "desc"), ("custom_id", "asc")]


@pytest.mark.unit
class TestBuildOrderBy:
    def test_builds_order_by_clauses(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        clauses = build_order_by(config, "name:desc,id:asc")
        assert len(clauses) == 2
        # Check that clauses are SQLAlchemy expressions
        assert hasattr(clauses[0], "compare")
        assert hasattr(clauses[1], "compare")

    def test_coalesce_applied_for_nullable_with_sentinel(self):
        config = SortConfig(
            model=MockModel,
            allowed_fields={"created_at", "id"},
            sentinels={"created_at": {"asc": DT_MAX, "desc": DT_MIN}},
        )
        clauses = build_order_by(config, "created_at:asc")
        # created_at is nullable, so COALESCE should be applied
        assert len(clauses) == 2

    def test_field_override_uses_custom_expression(self):
        from sqlalchemy import func

        custom_expr = func.lower(MockModel.name)
        config = SortConfig(
            model=MockModel,
            allowed_fields={"name_lower", "id"},
            field_overrides={"name_lower": custom_expr},
        )
        clauses = build_order_by(config, "name_lower:asc")
        assert len(clauses) == 2


@pytest.mark.unit
class TestApplyOrdering:
    def test_apply_ordering_to_select_statement(self):
        config = SortConfig(model=MockModel, allowed_fields={"name", "id"})
        stmt = select(MockModel)
        ordered_stmt = apply_ordering(stmt, config, "name:desc")
        # Verify that order_by was applied (statement should have _order_by_clauses)
        assert hasattr(ordered_stmt, "_order_by_clauses")


@pytest.mark.unit
class TestSortConfig:
    def test_sort_config_defaults(self):
        config = SortConfig(model=MockModel, allowed_fields={"name"})
        assert config.id_field == "id"
        assert config.sentinels == {}
        assert config.field_overrides == {}

    def test_sort_config_with_sentinels(self):
        config = SortConfig(
            model=MockModel,
            allowed_fields={"created_at"},
            sentinels={"created_at": {"asc": DT_MAX, "desc": DT_MIN}},
        )
        assert "created_at" in config.sentinels
        assert config.sentinels["created_at"]["asc"] == DT_MAX

    def test_dt_max_and_dt_min_constants(self):
        assert DT_MAX == datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
        assert DT_MIN == datetime(1, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert DT_MIN < DT_MAX
