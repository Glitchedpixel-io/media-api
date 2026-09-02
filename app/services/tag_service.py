# app/services/tag_service.py
from __future__ import annotations

from fastapi import HTTPException

from app.repositories import MediaRepository, TagRepository, TitleRepository
from app.schemas import (
    PaginatedResponse,
    TagCreateInternal,
    TagCreatePublic,
    TaggingReport,
    TagListParams,
    TagNameSet,
    TagPatchPublic,
    TagRead,
    TagSet,
    TagUpdateInternal,
)
from app.services.errors import translate_repository_errors

# todo: decided against putting the replace_tags methods in the repo, these feel like they belong here in the service layer


class TagService:
    def __init__(
        self,
        tag_repository: TagRepository,
        media_repository: MediaRepository,
        title_repository: TitleRepository,
    ) -> None:
        self.repo = tag_repository
        self.media_repo = media_repository
        self.title_repo = title_repository

    @translate_repository_errors
    def get_tags(
        self, params: TagListParams, parent_id: int | None = None
    ) -> PaginatedResponse[TagRead]:
        """List tags, optionally scoped to a parent.

        Decorated so an unsupported `sort` field becomes a 422 rather than a 500.
        The 404 raised below is an HTTPException already and passes through
        untouched.
        """
        if parent_id is not None and not self.repo.exists(parent_id):
            raise HTTPException(status_code=404, detail="Parent tag not found")

        return self.repo.list_paged(params, parent_id)

    def get_tag(self, tag_id: int) -> TagRead:
        tag = self.repo.get(tag_id)
        if tag is None:
            raise HTTPException(status_code=404, detail="Tag not found")
        return tag

    # ----------------------- Title tags

    def get_title_tags(self, title_id: int) -> list[TagRead]:
        if not self.title_repo.exists(title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.repo.get_title_tags(title_id)

    def tag_title_with_tag_ids(self, title_id: int, tag_ids: TagSet) -> list[TagRead]:
        if not self.title_repo.exists(title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.repo.add_title_tags(title_id, tag_ids.tag_ids)

    def tag_title_with_tag_names(self, title_id: int, tag_names: TagNameSet) -> TaggingReport:
        if not self.title_repo.exists(title_id):
            raise HTTPException(status_code=404, detail="Title not found")

        tags, missing_tags = self._get_or_create_tags(
            tag_names.tag_names, tag_names.auto_tag_create
        )
        # tag the title with the tags we created or found
        added_tags = self.tag_title_with_tag_ids(title_id, TagSet(tag_ids=[tag.id for tag in tags]))
        return TaggingReport(added_tags=added_tags, tagging_errors=missing_tags)

    def untag_title(self, title_id: int, tag_id: int) -> bool:
        if not self.title_repo.exists(title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.repo.remove_title_tag(title_id, tag_id)

    # ----------------------- Asset tags

    def get_asset_tags(self, asset_id: int) -> list[TagRead]:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.get_asset_tags(asset_id)

    def tag_asset_with_tag_ids(self, asset_id: int, tag_ids: TagSet) -> list[TagRead]:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.add_asset_tags(asset_id, tag_ids.tag_ids)

    def tag_asset_with_tag_names(self, asset_id: int, tag_names: TagNameSet) -> TaggingReport:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")

        tags, missing_tags = self._get_or_create_tags(
            tag_names.tag_names, tag_names.auto_tag_create
        )
        # tag the asset with the tags we created or found
        added_tags = self.tag_asset_with_tag_ids(asset_id, TagSet(tag_ids=[tag.id for tag in tags]))
        return TaggingReport(added_tags=added_tags, tagging_errors=missing_tags)

    def untag_asset(self, asset_id: int, tag_id: int) -> bool:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.remove_asset_tag(asset_id, tag_id)

    def _get_or_create_tags(
        self, tag_names: list[str], create_missing_tags: bool = True
    ) -> tuple[list[TagRead], list[str]]:
        """Resolve names to tags, optionally creating the ones that do not exist.

        Resolution is a single repository call rather than a loop. The previous
        shape -- check each name, then create the missing ones one at a time --
        committed once per created tag, so a failure part-way left some tags
        created and committed with no way to tell which from the response, and it
        raced with a concurrent request creating the same name.

        Args:
            tag_names: Names to resolve. Case and duplicates are handled below.
            create_missing_tags: Whether a name with no tag should create one.

        Returns:
            A tuple of (resolved tags, one message per name that could not be
            resolved). The second element is only ever non-empty when
            ``create_missing_tags`` is False.
        """
        unique_names = list(dict.fromkeys(name.lower() for name in tag_names))
        if not unique_names:
            return [], []

        if create_missing_tags:
            tags = self.repo.get_or_create_by_names(unique_names)
        else:
            tags = self.repo.get_by_names(unique_names)

        found = {tag.name for tag in tags}
        failures = [f"Tag '{name}' does not exist" for name in unique_names if name not in found]
        return tags, failures

    @translate_repository_errors
    def create_tag(self, tag: TagCreatePublic, parent_id: int | None = None) -> TagRead:
        if parent_id is not None and not self.repo.exists(parent_id):
            raise HTTPException(status_code=404, detail="Parent tag not found")
        return self.repo.create(TagCreateInternal(parent_id=parent_id, **tag.model_dump()))

    @translate_repository_errors(not_found_message="Tag not found")
    def update_tag(
        self,
        tag_id: int,
        update: TagPatchPublic,  # type: ignore
    ) -> TagRead:
        """Apply a partial update to a tag.

        Omitted fields are left unchanged, and an explicit null is discarded by the
        same rule rather than clearing the field.

        ``exclude_none`` used to be a parameter, passed ``False`` by a PUT route so
        that omitted fields were written as nulls (#181). Both are gone. Besides the
        contract, that path could not even be relied on to fail cleanly: an empty body
        dumped ``name=None`` into ``TagUpdateInternal``, whose inherited
        ``validate_name_not_empty`` validator assumed a string, and the resulting
        ValidationError escaped as a 500.

        Args:
            tag_id: ID of the tag to update.
            update: The submitted partial update.

        Returns:
            TagRead: The updated tag.
        """
        return self.repo.update(
            tag_id,
            TagUpdateInternal(**update.model_dump(exclude_none=True)),  # type: ignore
        )
