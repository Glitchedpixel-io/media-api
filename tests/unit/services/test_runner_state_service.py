"""Unit tests for RunnerStateService."""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException

from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.repositories.protocols import RunnerStateRepository
from app.schemas import (
    RunnerStateCreateInternal,
    RunnerStateCreatePublic,
    RunnerStatePatchPublic,
    RunnerStateUpdateInternal,
)
from app.services import RunnerStateService
from tests.factories import RunnerStateReadFactory


class TestGetRunnerState:
    """Tests for RunnerStateService.get_runner_state."""

    @pytest.mark.unit
    def test_get_runner_state_success(self) -> None:
        """get_runner_state returns state when found in repository."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        expected_state = RunnerStateReadFactory(runner_key="scanner", state={"page": 1})
        repo.get_runner_state.return_value = expected_state
        svc = RunnerStateService(repo)

        result = svc.get_runner_state("scanner")

        assert result is expected_state
        assert result.runner_key == "scanner"
        assert result.state == {"page": 1}
        repo.get_runner_state.assert_called_once_with("scanner")

    @pytest.mark.unit
    def test_get_runner_state_not_found(self) -> None:
        """get_runner_state raises 404 when repository returns None."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.get_runner_state.return_value = None
        svc = RunnerStateService(repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_runner_state("unknown")

        assert exc_info.value.status_code == 404
        assert "Runner State not found" in exc_info.value.detail
        repo.get_runner_state.assert_called_once_with("unknown")

    @pytest.mark.unit
    def test_get_runner_state_with_various_keys(self) -> None:
        """get_runner_state correctly handles different runner keys."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        svc = RunnerStateService(repo)

        test_keys = ["scanner", "processor", "importer"]
        for runner_key in test_keys:
            repo.reset_mock()
            expected = RunnerStateReadFactory(runner_key=runner_key)
            repo.get_runner_state.return_value = expected

            result = svc.get_runner_state(runner_key)

            assert result.runner_key == runner_key
            repo.get_runner_state.assert_called_once_with(runner_key)

    @pytest.mark.unit
    def test_get_runner_state_with_complex_state(self) -> None:
        """get_runner_state returns state with complex nested data."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        complex_state = {
            "offset": 100,
            "last_processed": "2024-01-01",
            "metadata": {"version": "1.0", "nested": {"value": 42}},
        }
        expected_state = RunnerStateReadFactory(runner_key="test", state=complex_state)
        repo.get_runner_state.return_value = expected_state
        svc = RunnerStateService(repo)

        result = svc.get_runner_state("test")

        assert result.state == complex_state
        assert result.state["metadata"]["nested"]["value"] == 42


class TestCreateRunnerState:
    """Tests for RunnerStateService.create_runner_state."""

    @pytest.mark.unit
    def test_create_runner_state_success(self) -> None:
        """create_runner_state creates new state and returns it."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        created_state = RunnerStateReadFactory(state={"offset": 10})
        repo.create.return_value = created_state
        svc = RunnerStateService(repo)

        payload = RunnerStateCreatePublic(state={"offset": 10})

        result = svc.create_runner_state(payload)

        assert result is created_state
        assert result.state == {"offset": 10}

        # Verify internal DTO conversion and runner_key generation
        repo.create.assert_called_once()
        call_arg = repo.create.call_args[0][0]
        assert isinstance(call_arg, RunnerStateCreateInternal)
        assert hasattr(call_arg, "runner_key")
        assert call_arg.runner_key  # Generated UUID
        assert call_arg.state == {"offset": 10}

    @pytest.mark.unit
    def test_create_runner_state_with_none_state_adds_default(self) -> None:
        """create_runner_state adds default state when state is None."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        created_state = RunnerStateReadFactory(state={"q": "created by API"})
        repo.create.return_value = created_state
        svc = RunnerStateService(repo)

        payload = RunnerStateCreatePublic(state=None)

        result = svc.create_runner_state(payload)

        assert result is created_state
        # Verify default state was added
        call_arg = repo.create.call_args[0][0]
        assert call_arg.state == {"q": "created by API"}

    @pytest.mark.unit
    def test_create_runner_state_with_empty_dict(self) -> None:
        """create_runner_state handles empty state dict."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        created_state = RunnerStateReadFactory(state={})
        repo.create.return_value = created_state
        svc = RunnerStateService(repo)

        payload = RunnerStateCreatePublic(state={})

        result = svc.create_runner_state(payload)

        assert result is created_state

    @pytest.mark.unit
    def test_create_runner_state_unique_violation(self) -> None:
        """create_runner_state raises 409 on unique constraint violation."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.create.side_effect = UniqueViolation("u")
        svc = RunnerStateService(repo)

        payload = RunnerStateCreatePublic(state={"page": 1})

        with pytest.raises(HTTPException) as exc_info:
            svc.create_runner_state(payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_runner_state_database_locked(self) -> None:
        """create_runner_state raises 423 when database is read-only."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.create.side_effect = DatabaseLocked("locked")
        svc = RunnerStateService(repo)

        payload = RunnerStateCreatePublic(state={"page": 1})

        with pytest.raises(HTTPException) as exc_info:
            svc.create_runner_state(payload)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ],
    )
    def test_create_runner_state_constraint_violations(self, exc_class) -> None:
        """create_runner_state raises 422 for various constraint violations."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.create.side_effect = exc_class("c")
        svc = RunnerStateService(repo)

        payload = RunnerStateCreatePublic(state={"page": 1})

        with pytest.raises(HTTPException) as exc_info:
            svc.create_runner_state(payload)

        assert exc_info.value.status_code == 422


class TestUpdateRunnerState:
    """Tests for RunnerStateService.update_runner_state."""

    @pytest.mark.unit
    def test_update_runner_state_success_with_exclude_none(self) -> None:
        """update_runner_state updates state with exclude_none=True."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        updated_state = RunnerStateReadFactory(runner_key="scanner", state={"offset": 25})
        repo.set_runner_state.return_value = updated_state
        svc = RunnerStateService(repo)

        patch = RunnerStatePatchPublic(state={"offset": 25})

        result = svc.update_runner_state("scanner", patch, exclude_none=True)

        assert result is updated_state
        assert result.runner_key == "scanner"
        assert result.state == {"offset": 25}

        # Verify internal DTO
        repo.set_runner_state.assert_called_once()
        call_args = repo.set_runner_state.call_args[0]
        assert call_args[0] == "scanner"
        assert isinstance(call_args[1], RunnerStateUpdateInternal)
        assert call_args[1].state == {"offset": 25}

    @pytest.mark.unit
    def test_update_runner_state_success_without_exclude_none(self) -> None:
        """update_runner_state updates state with exclude_none=False."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        updated_state = RunnerStateReadFactory(runner_key="scanner")
        repo.set_runner_state.return_value = updated_state
        svc = RunnerStateService(repo)

        patch = RunnerStatePatchPublic(state=None)

        result = svc.update_runner_state("scanner", patch, exclude_none=False)

        assert result is updated_state
        repo.set_runner_state.assert_called_once()

    @pytest.mark.unit
    def test_update_runner_state_with_complex_state(self) -> None:
        """update_runner_state handles complex nested state updates."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        complex_state = {
            "offset": 500,
            "metadata": {"last_run": "2024-01-01", "count": 100},
        }
        updated_state = RunnerStateReadFactory(state=complex_state)
        repo.set_runner_state.return_value = updated_state
        svc = RunnerStateService(repo)

        patch = RunnerStatePatchPublic(state=complex_state)

        result = svc.update_runner_state("processor", patch, exclude_none=True)

        assert result.state == complex_state
        call_arg = repo.set_runner_state.call_args[0][1]
        assert call_arg.state == complex_state

    @pytest.mark.unit
    def test_update_runner_state_not_found(self) -> None:
        """update_runner_state raises 404 when state doesn't exist."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.set_runner_state.side_effect = NotFoundError("missing")
        svc = RunnerStateService(repo)

        patch = RunnerStatePatchPublic(state=None)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_runner_state("scanner", patch, exclude_none=True)

        assert exc_info.value.status_code == 404
        assert "Runner state not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_runner_state_unique_violation(self) -> None:
        """update_runner_state raises 409 on unique constraint violation."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.set_runner_state.side_effect = UniqueViolation("u")
        svc = RunnerStateService(repo)

        patch = RunnerStatePatchPublic(state=None)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_runner_state("scanner", patch, exclude_none=False)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_runner_state_database_locked(self) -> None:
        """update_runner_state raises 423 when database is read-only."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.set_runner_state.side_effect = DatabaseLocked("locked")
        svc = RunnerStateService(repo)

        patch = RunnerStatePatchPublic(state=None)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_runner_state("scanner", patch, exclude_none=True)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ],
    )
    def test_update_runner_state_constraint_violations(self, exc_class) -> None:
        """update_runner_state raises 422 for various constraint violations."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.set_runner_state.side_effect = exc_class("c")
        svc = RunnerStateService(repo)

        patch = RunnerStatePatchPublic(state=None)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_runner_state("scanner", patch, exclude_none=True)

        assert exc_info.value.status_code == 422

    @pytest.mark.unit
    def test_update_runner_state_with_different_keys(self) -> None:
        """update_runner_state correctly passes runner_key to repository."""
        repo = create_autospec(RunnerStateRepository, instance=True, spec_set=True)
        repo.set_runner_state.return_value = RunnerStateReadFactory()
        svc = RunnerStateService(repo)

        test_keys = ["scanner", "processor", "importer"]
        for runner_key in test_keys:
            repo.reset_mock()
            patch = RunnerStatePatchPublic(state={"test": runner_key})

            svc.update_runner_state(runner_key, patch, exclude_none=True)

            call_args = repo.set_runner_state.call_args[0]
            assert call_args[0] == runner_key
