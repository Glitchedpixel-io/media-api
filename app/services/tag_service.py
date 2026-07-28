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

    def get_tags(
        self, params: TagListParams, parent_id: int | None = None
    ) -> PaginatedResponse[TagRead]:
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
        # Filter out duplicate names
        unique_names = list(dict.fromkeys(tag_names))

        tags: list[TagRead] = []
        failures: list[str] = []

        # Fetch existing tags
        existing_tags = {
            tag.name.lower(): tag
            for tag in [self.repo.get_by_name(name.lower()) for name in unique_names]
            if tag is not None
        }

        for tag_name in unique_names:
            if tag_name in existing_tags:
                tags.append(existing_tags[tag_name])
            elif create_missing_tags:
                # Create the tag
                creation_outcome = self._create_tag_from_name(tag_name)
                if isinstance(creation_outcome, TagRead):
                    tags.append(creation_outcome)
                else:
                    failures.append(creation_outcome)
            else:
                failures.append(f"Tag '{tag_name}' does not exist")

        return tags, failures

    def _create_tag_from_name(self, name: str) -> TagRead | str:
        # create the tag
        try:
            tag = self.create_tag(
                TagCreatePublic(name=name, description="<<auto created>>", color="#000000"),
                parent_id=None,
            )
            if tag:
                return tag
            else:
                return f"Tag {name} could not be created"
        except Exception as e:
            return f"Failed to create tag {name} with {e}"

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
        exclude_none: bool,
    ) -> TagRead:
        return self.repo.update(
            tag_id,
            TagUpdateInternal(**update.model_dump(exclude_none=exclude_none)),  # type: ignore
        )
