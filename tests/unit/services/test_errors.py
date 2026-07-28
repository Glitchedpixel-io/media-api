"""Unit tests for app.services.errors."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.repositories.errors import (
    CheckViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.services.errors import domain_error_detail, translate_repository_errors


@pytest.mark.unit
class TestDomainErrorDetail:
    def test_matches_fastapi_validation_error_shape(self) -> None:
        detail = domain_error_detail("bad value")

        assert isinstance(detail, list)
        assert len(detail) == 1
        assert set(detail[0]) == {"loc", "msg", "type"}

    def test_carries_message_and_default_type(self) -> None:
        detail = domain_error_detail("bad value")

        assert detail[0]["msg"] == "bad value"
        assert detail[0]["type"] == "domain_error"

    def test_error_type_is_overridable(self) -> None:
        detail = domain_error_detail("bad value", error_type="constraint_violation")

        assert detail[0]["type"] == "constraint_violation"


@pytest.mark.unit
class TestTranslateRepositoryErrors:
    def test_bare_usage_passes_through_return_value(self) -> None:
        @translate_repository_errors
        def ok(x: int) -> int:
            return x * 2

        assert ok(21) == 42

    def test_bare_usage_translates_unique_violation(self) -> None:
        @translate_repository_errors
        def raises() -> None:
            raise UniqueViolation("dup")

        with pytest.raises(HTTPException) as exc_info:
            raises()
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Unique constraint violated."

    def test_translates_database_locked(self) -> None:
        @translate_repository_errors
        def raises() -> None:
            raise DatabaseLocked("locked")

        with pytest.raises(HTTPException) as exc_info:
            raises()
        assert exc_info.value.status_code == 423

    @pytest.mark.parametrize(
        "exc",
        [
            ForeignKeyViolation("x"),
            NotNullViolation("x"),
            CheckViolation("x"),
            EnumViolation("x"),
        ],
    )
    def test_translates_constraint_violations_to_422(self, exc: Exception) -> None:
        @translate_repository_errors
        def raises() -> None:
            raise exc

        with pytest.raises(HTTPException) as exc_info:
            raises()
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail[0]["msg"] == str(exc)

    def test_not_found_propagates_unchanged_by_default(self) -> None:
        @translate_repository_errors
        def raises() -> None:
            raise NotFoundError("missing")

        with pytest.raises(NotFoundError):
            raises()

    def test_not_found_message_translates_to_404(self) -> None:
        @translate_repository_errors(not_found_message="Widget not found")
        def raises() -> None:
            raise NotFoundError("missing")

        with pytest.raises(HTTPException) as exc_info:
            raises()
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Widget not found"

    def test_preserves_function_metadata(self) -> None:
        @translate_repository_errors
        def some_method(self: object) -> None:
            """docstring."""

        assert some_method.__name__ == "some_method"
        assert some_method.__doc__ == "docstring."
