"""Unit tests for how TitleService translates a title_type code into its foreign key.

Separated from test_title_service.py because this is the seam introduced by
issue #41: the public models carry ``title_type`` (a code) while the internal
ones carry ``title_type_id``, and getting the translation wrong fails quietly
rather than loudly.
"""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException

from app.repositories.protocols import TitleRepository, TitleTypeRepository
from app.schemas import (
    TitleCreateInternal,
    TitleCreatePublic,
    TitlePatchPublic,
    TitleTypeRead,
    TitleUpdateInternal,
)
from app.services import TitleService
from tests.factories import TitleReadFactory


def _type_repo(known: dict[str, int] | None = None):
    """A title type repository that resolves codes to ids.

    Args:
        known: Codes this repository knows about, mapped to their ids. Omit to
            resolve every code to id 1, for tests that are not about resolution.

    Returns:
        An autospecced TitleTypeRepository.
    """
    repo = create_autospec(TitleTypeRepository, instance=True, spec_set=True)

    def get_by_code(code: str) -> TitleTypeRead | None:
        if known is not None and code not in known:
            return None
        return TitleTypeRead(id=known[code] if known else 1, code=code, label=code)

    repo.get_by_code.side_effect = get_by_code
    return repo


def _title_repo():
    return create_autospec(TitleRepository, instance=True, spec_set=True)


@pytest.mark.unit
class TestCreateResolution:
    def test_create_resolves_code_to_foreign_key(self) -> None:
        repo = _title_repo()
        repo.create.return_value = TitleReadFactory(id=1, title_type="season")
        svc = TitleService(repo, _type_repo({"movie": 1, "season": 7}))

        svc.create_title(TitleCreatePublic(name="Season 1", title_type="season"))

        call_arg = repo.create.call_args[0][0]
        assert isinstance(call_arg, TitleCreateInternal)
        assert call_arg.title_type_id == 7

    def test_create_with_unknown_code_is_422(self) -> None:
        """An unknown type stays a 422, as it was when title_type was an enum.

        This is the regression that matters most in issue #41: title_type is now
        an open string at the schema boundary, so this service check is the only
        thing standing between a bad code and a foreign key error.
        """
        repo = _title_repo()
        svc = TitleService(repo, _type_repo({"movie": 1}))

        with pytest.raises(HTTPException) as exc_info:
            svc.create_title(TitleCreatePublic(name="X", title_type="not_a_type"))

        assert exc_info.value.status_code == 422
        assert not repo.create.called
        # Matches FastAPI's own validation-error shape, not a bare string.
        assert isinstance(exc_info.value.detail, list)
        assert "not_a_type" in exc_info.value.detail[0]["msg"]


@pytest.mark.unit
class TestUpdateResolution:
    def test_update_with_unknown_code_is_422(self) -> None:
        repo = _title_repo()
        svc = TitleService(repo, _type_repo({"movie": 1}))

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title(5, TitlePatchPublic(title_type="not_a_type"), exclude_none=True)

        assert exc_info.value.status_code == 422
        assert not repo.update.called

    def test_patch_that_omits_title_type_leaves_it_alone(self) -> None:
        """A PATCH of another field must not touch the title's type.

        If the translation set the id unconditionally, every PATCH would rewrite
        the type; if it passed the code straight through, ``extra="forbid"`` on
        TitleCreateInternal would reject it. Neither should happen here.
        """
        repo = _title_repo()
        repo.update.return_value = TitleReadFactory(id=5)
        svc = TitleService(repo, _type_repo())

        svc.update_title(5, TitlePatchPublic(name="Renamed"), exclude_none=True)

        call_arg = repo.update.call_args[0][1]
        assert isinstance(call_arg, TitleUpdateInternal)
        written = call_arg.model_dump(exclude_unset=True)
        assert written == {"name": "Renamed"}

    def test_patch_that_supplies_title_type_translates_it(self) -> None:
        repo = _title_repo()
        repo.update.return_value = TitleReadFactory(id=5, title_type="season")
        svc = TitleService(repo, _type_repo({"movie": 1, "season": 7}))

        svc.update_title(5, TitlePatchPublic(title_type="season"), exclude_none=True)

        call_arg = repo.update.call_args[0][1]
        assert call_arg.model_dump(exclude_unset=True) == {"title_type_id": 7}

    def test_put_that_omits_title_type_still_clears_it(self) -> None:
        """PUT keeps replace semantics: an omitted type is written as NULL.

        The repository then hits the NOT NULL column and the caller gets the
        same 422 it always did. Silently preserving the old type here would turn
        a replace into a partial update.
        """
        repo = _title_repo()
        repo.update.return_value = TitleReadFactory(id=5)
        svc = TitleService(repo, _type_repo())

        svc.update_title(5, TitlePatchPublic(name="Replaced"), exclude_none=False)

        call_arg = repo.update.call_args[0][1]
        assert call_arg.model_dump(exclude_unset=True)["title_type_id"] is None
