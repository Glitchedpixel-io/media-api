# app/services/artwork_service.py
from __future__ import annotations

from fastapi import HTTPException

from app.repositories import (
    ArtworkKindRepository,
    ArtworkRepository,
    MediaRepository,
    TitleRepository,
)
from app.schemas import (
    ArtworkCreateInternal,
    ArtworkCreatePublic,
    ArtworkKindCreateInternal,
    ArtworkKindCreatePublic,
    ArtworkKindPatchPublic,
    ArtworkKindRead,
    ArtworkKindUpdateInternal,
    ArtworkPatchPublic,
    ArtworkRead,
    ArtworkUpdateInternal,
)
from app.schemas.enums import EntityTypeEnum
from app.services.errors import translate_repository_errors


class ArtworkKindService:
    """The artwork kinds an artwork can be categorised as.

    A lookup table rather than an enum, so adding a kind is a row edit rather than a
    migration -- the lesson #41 recorded and #93 collected on.
    """

    def __init__(self, repository: ArtworkKindRepository) -> None:
        self.repo = repository

    def get_artwork_kinds(self) -> list[ArtworkKindRead]:
        return self.repo.list_all()

    def get_artwork_kind(self, kind_id: int) -> ArtworkKindRead:
        kind = self.repo.get(kind_id)
        if kind is None:
            raise HTTPException(status_code=404, detail="Artwork kind not found")
        return kind

    @translate_repository_errors
    def create_artwork_kind(self, kind: ArtworkKindCreatePublic) -> ArtworkKindRead:
        internal = ArtworkKindCreateInternal(**kind.model_dump())
        return self.repo.create(internal)

    @translate_repository_errors(not_found_message="Artwork kind not found")
    def update_artwork_kind(
        self,
        kind_id: int,
        update: ArtworkKindPatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> ArtworkKindRead:
        internal = ArtworkKindUpdateInternal(**update.model_dump(exclude_none=exclude_none))  # type: ignore
        return self.repo.update(kind_id, internal)

    @translate_repository_errors(not_found_message="Artwork kind not found")
    def delete_artwork_kind(self, kind_id: int) -> None:
        """Delete an artwork kind that no artwork is using.

        The usage check is what produces a meaningful 409, for the same reason
        ``TitleTypeService.delete_title_type`` carries one: ``ondelete="RESTRICT"``
        alone surfaces as a ``ForeignKeyViolation``, which
        ``translate_repository_errors`` maps to 422 -- the wrong code for a resource
        that exists but is still referenced.

        Args:
            kind_id: ID of the artwork kind to delete.

        Raises:
            HTTPException: 404 if the kind does not exist, or 409 if any artwork
                still references it.
        """
        if not self.repo.exists(kind_id):
            raise HTTPException(status_code=404, detail="Artwork kind not found")

        in_use = self.repo.usage_count(kind_id)
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Artwork kind is still used by {in_use} artwork(s) and cannot be "
                    "deleted. Reassign those artworks to another kind first."
                ),
            )
        self.repo.delete(kind_id)


class ArtworkService:
    """Artwork belonging to a title or an asset.

    Takes four repositories rather than one because it owns two integrity checks the
    database cannot: that the referenced kind code exists, and that the referenced
    entity exists. The second is the application-layer half of the typed association
    pattern -- ``artwork.entity_id`` has no foreign key, because its target table
    depends on ``entity_type`` and Postgres cannot express that.

    Per CLAUDE.md, a service depending on several repositories must be constructed from
    a single session rather than by chaining ``Depends(get_*_repository)``, or FastAPI
    opens one session per repository and the transaction is silently split.
    """

    def __init__(
        self,
        repository: ArtworkRepository,
        kinds: ArtworkKindRepository,
        titles: TitleRepository,
        assets: MediaRepository,
    ) -> None:
        self.repo = repository
        self.kinds = kinds
        self.titles = titles
        self.assets = assets

    def _resolve_kind_id(self, code: str) -> int:
        """Resolve an artwork kind code to its ID.

        Raises:
            HTTPException: 422 if no kind carries that code.
        """
        kind = self.kinds.get_by_code(code)
        if kind is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown artwork kind '{code}'. See the artwork kinds endpoint.",
            )
        return kind.id

    def _require_entity(self, entity_type: EntityTypeEnum, entity_id: int) -> None:
        """Confirm the entity this artwork claims to belong to exists.

        Nothing in the schema enforces this -- see the class docstring -- so an
        unchecked write would leave a row pointing at a title that never existed, and
        the artwork would simply never be found again.

        Raises:
            HTTPException: 404 if the entity does not exist.
        """
        if entity_type == EntityTypeEnum.title:
            if not self.titles.exists(entity_id):
                raise HTTPException(status_code=404, detail="Title not found")
        elif not self.assets.exists(entity_id):
            raise HTTPException(status_code=404, detail="Asset not found")

    def list_artwork(
        self, entity_type: EntityTypeEnum, entity_id: int, kind: str | None = None
    ) -> list[ArtworkRead]:
        self._require_entity(entity_type, entity_id)
        kind_id = self._resolve_kind_id(kind) if kind is not None else None
        return self.repo.list_for_entity(entity_type, entity_id, kind_id)

    def get_artwork(self, artwork_id: int) -> ArtworkRead:
        artwork = self.repo.get(artwork_id)
        if artwork is None:
            raise HTTPException(status_code=404, detail="Artwork not found")
        return artwork

    def get_primary_artwork(
        self, entity_type: EntityTypeEnum, entity_id: int, kind: str
    ) -> ArtworkRead | None:
        """The artwork to use for an entity and kind, or None if it has none.

        Returns ``None`` rather than raising, because "this title has no poster" is an
        ordinary answer the browse grid must render a placeholder for -- not a client
        error. Resolving a title's poster from a *child* when it has none of its own
        is #105's job, deliberately not this one.
        """
        self._require_entity(entity_type, entity_id)
        return self.repo.get_primary(entity_type, entity_id, self._resolve_kind_id(kind))

    @translate_repository_errors
    def create_artwork(
        self, entity_type: EntityTypeEnum, entity_id: int, artwork: ArtworkCreatePublic
    ) -> ArtworkRead:
        self._require_entity(entity_type, entity_id)
        payload = artwork.model_dump()
        kind_code = payload.pop("artwork_kind")
        internal = ArtworkCreateInternal(
            entity_type=entity_type,
            entity_id=entity_id,
            artwork_kind_id=self._resolve_kind_id(kind_code),
            **payload,
        )
        return self.repo.create(internal)

    @translate_repository_errors(not_found_message="Artwork not found")
    def update_artwork(
        self,
        artwork_id: int,
        update: ArtworkPatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> ArtworkRead:
        payload = update.model_dump(exclude_none=exclude_none, exclude_unset=True)  # type: ignore
        kind_code = payload.pop("artwork_kind", None)
        if kind_code is not None:
            payload["artwork_kind_id"] = self._resolve_kind_id(kind_code)
        internal = ArtworkUpdateInternal(**payload)
        return self.repo.update(artwork_id, internal)

    @translate_repository_errors(not_found_message="Artwork not found")
    def set_primary_artwork(self, artwork_id: int) -> ArtworkRead:
        """Promote one artwork to primary, demoting whatever held the flag."""
        return self.repo.set_primary(artwork_id)

    @translate_repository_errors(not_found_message="Artwork not found")
    def delete_artwork(self, artwork_id: int) -> None:
        """Delete an artwork row.

        Deliberately leaves the file on disk. Content addressing means one file can be
        referenced by several rows -- the poster a season shares with its episodes is
        one file -- so deleting bytes here would break the other references. Reclaiming
        unreferenced files is a sweep, not a side effect of one delete.
        """
        self.repo.delete(artwork_id)
