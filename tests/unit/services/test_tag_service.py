"""Unit tests for TagService."""

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
from app.repositories.protocols import MediaRepository, TagRepository, TitleRepository
from app.schemas import (
    PageInfo,
    PaginatedResponse,
    TagCreateInternal,
    TagCreatePublic,
    TagListParams,
    TagNameSet,
    TagPatchPublic,
    TagSet,
    TagUpdateInternal,
)
from app.services import TagService
from tests.factories import TagReadFactory


class TestGetTag:
    """Tests for TagService.get_tag."""

    @pytest.mark.unit
    def test_get_tag_success(self) -> None:
        """get_tag returns tag when found in repository."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        expected_tag = TagReadFactory(id=7, name="action")
        tag_repo.get.return_value = expected_tag
        svc = TagService(tag_repo, media_repo, title_repo)

        result = svc.get_tag(7)

        assert result is expected_tag
        assert result.id == 7
        assert result.name == "action"
        tag_repo.get.assert_called_once_with(7)

    @pytest.mark.unit
    def test_get_tag_not_found(self) -> None:
        """get_tag raises 404 when repository returns None."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.get.return_value = None
        svc = TagService(tag_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_tag(999)

        assert exc_info.value.status_code == 404
        assert "Tag not found" in exc_info.value.detail
        tag_repo.get.assert_called_once_with(999)


class TestGetTags:
    """Tests for TagService.get_tags."""

    @pytest.mark.unit
    def test_get_tags_with_default_params(self) -> None:
        """get_tags delegates to repository with provided params."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tags = [TagReadFactory() for _ in range(3)]
        tag_repo.list_paged.return_value = PaginatedResponse(
            items=tags, page=PageInfo(next=None, prev=None)
        )
        svc = TagService(tag_repo, media_repo, title_repo)

        params = TagListParams()
        result = svc.get_tags(params)

        assert len(result.items) == 3
        tag_repo.list_paged.assert_called_once_with(params, None)

    @pytest.mark.unit
    def test_get_tags_with_parent_id(self) -> None:
        """get_tags retrieves child tags when parent_id is provided."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.exists.return_value = True
        tag_repo.list_paged.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )
        svc = TagService(tag_repo, media_repo, title_repo)

        params = TagListParams()
        svc.get_tags(params, parent_id=5)

        tag_repo.exists.assert_called_once_with(5)
        tag_repo.list_paged.assert_called_once_with(params, 5)

    @pytest.mark.unit
    def test_get_tags_parent_not_found(self) -> None:
        """get_tags raises 404 when parent_id doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        params = TagListParams()

        with pytest.raises(HTTPException) as exc_info:
            svc.get_tags(params, parent_id=999)

        assert exc_info.value.status_code == 404
        assert "Parent tag not found" in exc_info.value.detail


class TestCreateTag:
    """Tests for TagService.create_tag."""

    @pytest.mark.unit
    def test_create_tag_success(self) -> None:
        """create_tag creates new tag and returns it."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        created_tag = TagReadFactory(id=1, name="action", description="Action movies")
        tag_repo.create.return_value = created_tag
        svc = TagService(tag_repo, media_repo, title_repo)

        payload = TagCreatePublic(name="action", description="Action movies", color="#ff0000")

        result = svc.create_tag(payload, parent_id=None)

        assert result is created_tag
        assert result.name == "action"

        # Verify internal DTO
        tag_repo.create.assert_called_once()
        call_arg = tag_repo.create.call_args[0][0]
        assert isinstance(call_arg, TagCreateInternal)
        assert call_arg.name == "action"
        assert call_arg.parent_id is None

    @pytest.mark.unit
    def test_create_tag_with_parent(self) -> None:
        """create_tag creates child tag with parent_id successfully."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.exists.return_value = True
        created_tag = TagReadFactory(id=2, name="scifi-action", parent_id=1)
        tag_repo.create.return_value = created_tag
        svc = TagService(tag_repo, media_repo, title_repo)

        payload = TagCreatePublic(name="scifi-action", description="", color="#000000")

        result = svc.create_tag(payload, parent_id=1)

        assert result.parent_id == 1
        tag_repo.exists.assert_called_once_with(1)
        call_arg = tag_repo.create.call_args[0][0]
        assert call_arg.parent_id == 1

    @pytest.mark.unit
    def test_create_tag_parent_not_found(self) -> None:
        """create_tag raises 404 when parent_id doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        payload = TagCreatePublic(name="test", description="", color="#000000")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_tag(payload, parent_id=999)

        assert exc_info.value.status_code == 404
        assert "Parent tag not found" in exc_info.value.detail
        tag_repo.create.assert_not_called()

    @pytest.mark.unit
    def test_create_tag_unique_violation(self) -> None:
        """create_tag raises 409 on unique constraint violation."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.create.side_effect = UniqueViolation("u")
        svc = TagService(tag_repo, media_repo, title_repo)

        payload = TagCreatePublic(name="duplicate", description="", color="#000000")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_tag(payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_tag_database_locked(self) -> None:
        """create_tag raises 423 when database is read-only."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.create.side_effect = DatabaseLocked("locked")
        svc = TagService(tag_repo, media_repo, title_repo)

        payload = TagCreatePublic(name="test", description="", color="#000000")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_tag(payload)

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
    def test_create_tag_constraint_violations(self, exc_class) -> None:
        """create_tag raises 422 for various constraint violations."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.create.side_effect = exc_class("c")
        svc = TagService(tag_repo, media_repo, title_repo)

        payload = TagCreatePublic(name="test", description="", color="#000000")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_tag(payload)

        assert exc_info.value.status_code == 422


class TestUpdateTag:
    """Tests for TagService.update_tag."""

    @pytest.mark.unit
    def test_update_tag_success_with_exclude_none(self) -> None:
        """update_tag leaves omitted fields alone -- the only behaviour since #181."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        updated_tag = TagReadFactory(id=3, name="Updated Name")
        tag_repo.update.return_value = updated_tag
        svc = TagService(tag_repo, media_repo, title_repo)

        patch = TagPatchPublic(name="Updated Name")

        result = svc.update_tag(3, patch)

        assert result is updated_tag
        assert result.name == "updated name"

        # Verify internal DTO
        tag_repo.update.assert_called_once()
        call_args = tag_repo.update.call_args[0]
        assert call_args[0] == 3
        assert isinstance(call_args[1], TagUpdateInternal)

    @pytest.mark.unit
    def test_update_tag_not_found(self) -> None:
        """update_tag raises 404 when tag doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.update.side_effect = NotFoundError("missing")
        svc = TagService(tag_repo, media_repo, title_repo)

        patch = TagPatchPublic(name="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_tag(999, patch)

        assert exc_info.value.status_code == 404
        assert "Tag not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_tag_unique_violation(self) -> None:
        """update_tag raises 409 on unique constraint violation."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.update.side_effect = UniqueViolation("u")
        svc = TagService(tag_repo, media_repo, title_repo)

        patch = TagPatchPublic(name="duplicate")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_tag(3, patch)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_tag_database_locked(self) -> None:
        """update_tag raises 423 when database is read-only."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.update.side_effect = DatabaseLocked("locked")
        svc = TagService(tag_repo, media_repo, title_repo)

        patch = TagPatchPublic(name="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_tag(3, patch)

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
    def test_update_tag_constraint_violations(self, exc_class) -> None:
        """update_tag raises 422 for various constraint violations."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        tag_repo.update.side_effect = exc_class("c")
        svc = TagService(tag_repo, media_repo, title_repo)

        patch = TagPatchPublic(name="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_tag(3, patch)

        assert exc_info.value.status_code == 422


class TestGetTitleTags:
    """Tests for TagService.get_title_tags."""

    @pytest.mark.unit
    def test_get_title_tags_success(self) -> None:
        """get_title_tags returns list of tags for title."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = True
        tags = [TagReadFactory() for _ in range(3)]
        tag_repo.get_title_tags.return_value = tags
        svc = TagService(tag_repo, media_repo, title_repo)

        result = svc.get_title_tags(10)

        assert len(result) == 3
        title_repo.exists.assert_called_once_with(10)
        tag_repo.get_title_tags.assert_called_once_with(10)

    @pytest.mark.unit
    def test_get_title_tags_empty_list(self) -> None:
        """get_title_tags returns empty list when title has no tags."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = True
        tag_repo.get_title_tags.return_value = []
        svc = TagService(tag_repo, media_repo, title_repo)

        result = svc.get_title_tags(10)

        assert len(result) == 0

    @pytest.mark.unit
    def test_get_title_tags_title_not_found(self) -> None:
        """get_title_tags raises 404 when title doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_title_tags(999)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        tag_repo.get_title_tags.assert_not_called()


class TestTagTitleWithTagIds:
    """Tests for TagService.tag_title_with_tag_ids."""

    @pytest.mark.unit
    def test_tag_title_with_tag_ids_success(self) -> None:
        """tag_title_with_tag_ids adds tags to title successfully."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = True
        added_tags = [TagReadFactory(id=1), TagReadFactory(id=2)]
        tag_repo.add_title_tags.return_value = added_tags
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_set = TagSet(tag_ids=[1, 2])
        result = svc.tag_title_with_tag_ids(10, tag_set)

        assert len(result) == 2
        title_repo.exists.assert_called_once_with(10)
        tag_repo.add_title_tags.assert_called_once_with(10, [1, 2])

    @pytest.mark.unit
    def test_tag_title_with_tag_ids_title_not_found(self) -> None:
        """tag_title_with_tag_ids raises 404 when title doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_set = TagSet(tag_ids=[1, 2])

        with pytest.raises(HTTPException) as exc_info:
            svc.tag_title_with_tag_ids(999, tag_set)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        tag_repo.add_title_tags.assert_not_called()


class TestUntagTitle:
    """Tests for TagService.untag_title."""

    @pytest.mark.unit
    def test_untag_title_success(self) -> None:
        """untag_title removes tag from title successfully."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = True
        tag_repo.remove_title_tag.return_value = True
        svc = TagService(tag_repo, media_repo, title_repo)

        result = svc.untag_title(10, 3)

        assert result is True
        title_repo.exists.assert_called_once_with(10)
        tag_repo.remove_title_tag.assert_called_once_with(10, 3)

    @pytest.mark.unit
    def test_untag_title_title_not_found(self) -> None:
        """untag_title raises 404 when title doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.untag_title(999, 3)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        tag_repo.remove_title_tag.assert_not_called()


class TestGetAssetTags:
    """Tests for TagService.get_asset_tags."""

    @pytest.mark.unit
    def test_get_asset_tags_success(self) -> None:
        """get_asset_tags returns list of tags for asset."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = True
        tags = [TagReadFactory() for _ in range(2)]
        tag_repo.get_asset_tags.return_value = tags
        svc = TagService(tag_repo, media_repo, title_repo)

        result = svc.get_asset_tags(20)

        assert len(result) == 2
        media_repo.exists.assert_called_once_with(20)
        tag_repo.get_asset_tags.assert_called_once_with(20)

    @pytest.mark.unit
    def test_get_asset_tags_asset_not_found(self) -> None:
        """get_asset_tags raises 404 when asset doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_tags(999)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        tag_repo.get_asset_tags.assert_not_called()


class TestTagAssetWithTagIds:
    """Tests for TagService.tag_asset_with_tag_ids."""

    @pytest.mark.unit
    def test_tag_asset_with_tag_ids_success(self) -> None:
        """tag_asset_with_tag_ids adds tags to asset successfully."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = True
        added_tags = [TagReadFactory(id=5), TagReadFactory(id=6)]
        tag_repo.add_asset_tags.return_value = added_tags
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_set = TagSet(tag_ids=[5, 6])
        result = svc.tag_asset_with_tag_ids(20, tag_set)

        assert len(result) == 2
        media_repo.exists.assert_called_once_with(20)
        tag_repo.add_asset_tags.assert_called_once_with(20, [5, 6])

    @pytest.mark.unit
    def test_tag_asset_with_tag_ids_asset_not_found(self) -> None:
        """tag_asset_with_tag_ids raises 404 when asset doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_set = TagSet(tag_ids=[5, 6])

        with pytest.raises(HTTPException) as exc_info:
            svc.tag_asset_with_tag_ids(999, tag_set)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        tag_repo.add_asset_tags.assert_not_called()


class TestUntagAsset:
    """Tests for TagService.untag_asset."""

    @pytest.mark.unit
    def test_untag_asset_success(self) -> None:
        """untag_asset removes tag from asset successfully."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = True
        tag_repo.remove_asset_tag.return_value = True
        svc = TagService(tag_repo, media_repo, title_repo)

        result = svc.untag_asset(20, 7)

        assert result is True
        media_repo.exists.assert_called_once_with(20)
        tag_repo.remove_asset_tag.assert_called_once_with(20, 7)

    @pytest.mark.unit
    def test_untag_asset_asset_not_found(self) -> None:
        """untag_asset raises 404 when asset doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.untag_asset(999, 7)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        tag_repo.remove_asset_tag.assert_not_called()


class TestTagTitleWithTagNames:
    """Tests for TagService.tag_title_with_tag_names."""

    @pytest.mark.unit
    def test_tag_title_with_tag_names_existing_tags(self) -> None:
        """tag_title_with_tag_names uses existing tags successfully."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = True

        # Existing tags
        tag1 = TagReadFactory(id=1, name="action")
        tag2 = TagReadFactory(id=2, name="scifi")
        tag_repo.get_by_names.return_value = [tag1, tag2]

        # Added tags
        tag_repo.add_title_tags.return_value = [tag1, tag2]
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_names = TagNameSet(tag_names=["action", "scifi"], auto_tag_create=False)
        result = svc.tag_title_with_tag_names(10, tag_names)

        assert len(result.added_tags) == 2
        assert len(result.tagging_errors) == 0
        tag_repo.add_title_tags.assert_called_once_with(10, [1, 2])

    @pytest.mark.unit
    def test_tag_title_with_tag_names_creates_missing_tags(self) -> None:
        """tag_title_with_tag_names creates missing tags when auto_tag_create=True."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = True

        # First tag exists, second doesn't
        tag1 = TagReadFactory(id=1, name="action")
        new_tag = TagReadFactory(id=3, name="drama")
        tag_repo.get_or_create_by_names.return_value = [tag1, new_tag]

        # Both tags added
        tag_repo.add_title_tags.return_value = [tag1, new_tag]
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_names = TagNameSet(tag_names=["action", "drama"], auto_tag_create=True)
        result = svc.tag_title_with_tag_names(10, tag_names)

        assert len(result.added_tags) == 2
        assert len(result.tagging_errors) == 0
        tag_repo.get_or_create_by_names.assert_called_once_with(["action", "drama"])

    @pytest.mark.unit
    def test_tag_title_with_tag_names_reports_missing_tags(self) -> None:
        """tag_title_with_tag_names reports errors when tags don't exist and auto_create=False."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = True
        tag_repo.get_by_names.return_value = []
        tag_repo.add_title_tags.return_value = []
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_names = TagNameSet(tag_names=["nonexistent"], auto_tag_create=False)
        result = svc.tag_title_with_tag_names(10, tag_names)

        assert len(result.added_tags) == 0
        assert len(result.tagging_errors) == 1
        assert "does not exist" in result.tagging_errors[0]

    @pytest.mark.unit
    def test_tag_title_with_tag_names_title_not_found(self) -> None:
        """tag_title_with_tag_names raises 404 when title doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        title_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_names = TagNameSet(tag_names=["action"], auto_tag_create=False)

        with pytest.raises(HTTPException) as exc_info:
            svc.tag_title_with_tag_names(999, tag_names)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail


class TestTagAssetWithTagNames:
    """Tests for TagService.tag_asset_with_tag_names."""

    @pytest.mark.unit
    def test_tag_asset_with_tag_names_existing_tags(self) -> None:
        """tag_asset_with_tag_names uses existing tags successfully."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = True

        tag1 = TagReadFactory(id=1, name="hd")
        tag_repo.get_by_names.return_value = [tag1]
        tag_repo.add_asset_tags.return_value = [tag1]
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_names = TagNameSet(tag_names=["hd"], auto_tag_create=False)
        result = svc.tag_asset_with_tag_names(20, tag_names)

        assert len(result.added_tags) == 1
        assert len(result.tagging_errors) == 0

    @pytest.mark.unit
    def test_tag_asset_with_tag_names_asset_not_found(self) -> None:
        """tag_asset_with_tag_names raises 404 when asset doesn't exist."""
        tag_repo = create_autospec(TagRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        media_repo.exists.return_value = False
        svc = TagService(tag_repo, media_repo, title_repo)

        tag_names = TagNameSet(tag_names=["hd"], auto_tag_create=False)

        with pytest.raises(HTTPException) as exc_info:
            svc.tag_asset_with_tag_names(999, tag_names)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
