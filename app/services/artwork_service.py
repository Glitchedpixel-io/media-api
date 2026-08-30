# app/services/artwork_service.py
from __future__ import annotations

from typing import BinaryIO

from fastapi import HTTPException

from app.repositories import (
    ArtworkKindRepository,
    ArtworkRepository,
    MediaRepository,
    TitleRepository,
)
from app.schemas import (
    ArtworkCreateInternal,
    ArtworkKindCreateInternal,
    ArtworkKindCreatePublic,
    ArtworkKindPatchPublic,
    ArtworkKindRead,
    ArtworkKindUpdateInternal,
    ArtworkListParams,
    ArtworkPatchPublic,
    ArtworkRead,
    ArtworkUpdateInternal,
    ArtworkUploadForm,
    PaginatedResponse,
)
from app.schemas.enums import EntityTypeEnum
from app.services.artwork_storage import ArtworkStore, StoredArtwork
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
        store: ArtworkStore,
    ) -> None:
        self.repo = repository
        self.kinds = kinds
        self.titles = titles
        self.assets = assets
        self.store = store

    def _resolve_kind(self, code: str) -> ArtworkKindRead:
        """Resolve an artwork kind code to the kind itself.

        Returns the whole record rather than the id because the shape constraints live
        on it, and an upload needs both.

        Args:
            code: The kind code the caller declared.

        Returns:
            ArtworkKindRead: The matching kind.

        Raises:
            HTTPException: 422 if no kind carries that code.
        """
        kind = self.kinds.get_by_code(code)
        if kind is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown artwork kind '{code}'. See the artwork kinds endpoint.",
            )
        return kind

    def _resolve_kind_id(self, code: str) -> int:
        """Resolve an artwork kind code to its ID.

        Raises:
            HTTPException: 422 if no kind carries that code.
        """
        return self._resolve_kind(code).id

    @staticmethod
    def _check_shape(kind: ArtworkKindRead, stored: StoredArtwork) -> None:
        """Refuse an image whose shape contradicts the kind the caller declared.

        Shape is **necessary but not sufficient** (#127): the client says what the
        artwork is, and this says whether the pixels contradict that claim. Nothing
        here infers a kind, which is why three kinds sharing a shape is not a problem
        to solve.

        A kind with no ``target_ratio`` has no shape expectation and passes any ratio --
        the honest state for a transparent logo, for a thumbnail whose source decides
        its shape, and for artwork nobody has classified. A ``target_ratio`` with a null
        tolerance demands an exact match, which the seeded kinds never do but the API
        permits.

        **Called before the file is committed**, from ``ArtworkStore``'s acceptance
        hook, so a refusal leaves nothing on disk. Deleting afterwards would not be
        equivalent -- content addressing means the file may already be shared with
        another row.

        Args:
            kind: The declared kind, carrying its shape constraints.
            stored: What the store measured, not what the caller claimed.

        Raises:
            HTTPException: 422 if the image's shape is not permissible for the kind.
        """
        if kind.min_width is not None and stored.width < kind.min_width:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Artwork of kind '{kind.code}' must be at least {kind.min_width}px "
                    f"wide; this image is {stored.width}x{stored.height}"
                ),
            )
        if kind.max_width is not None and stored.width > kind.max_width:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Artwork of kind '{kind.code}' must be at most {kind.max_width}px "
                    f"wide; this image is {stored.width}x{stored.height}"
                ),
            )

        if kind.target_ratio is None:
            return

        ratio = stored.width / stored.height
        tolerance = kind.ratio_tolerance or 0.0
        # Relative to the target rather than absolute, so one tolerance means the same
        # thing at 0.667 as it does at 1.778.
        if abs(ratio - kind.target_ratio) / kind.target_ratio > tolerance:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Artwork of kind '{kind.code}' must have an aspect ratio near "
                    f"{kind.target_ratio:.3f} (within {tolerance:.0%}); this image is "
                    f"{stored.width}x{stored.height}, a ratio of {ratio:.3f}"
                ),
            )

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

    @translate_repository_errors
    def list_all_artwork(self, params: ArtworkListParams) -> PaginatedResponse[ArtworkRead]:
        """A page of artwork across every entity.

        The one route that can answer "what artwork exists?". Everything else needs an
        entity id in hand, which makes auditing or walking the collection impossible --
        see #113.

        Decorated so an unsupported `sort` field becomes a 422 rather than a 500, as on
        the other listings.

        Args:
            params: Filters, sort and cursor.

        Returns:
            PaginatedResponse[ArtworkRead]: The page and its cursors.

        Raises:
            HTTPException: 422 if `kind` names no existing artwork kind.
        """
        kind_id = self._resolve_kind_id(params.kind) if params.kind is not None else None
        return self.repo.list_paged(params, kind_id)

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
    def register_stored(
        self,
        entity_type: EntityTypeEnum,
        entity_id: int,
        artwork_kind_id: int,
        stored: StoredArtwork,
        *,
        is_primary: bool = False,
        source_scheme_id: int | None = None,
        source_external_id: str | None = None,
        source_url: str | None = None,
    ) -> ArtworkRead:
        """Register a file the store has already accepted.

        **The one place an artwork row is born.** Both the upload endpoint and
        ``tools/artwork_backfill`` come through here, which is the point: #138 was
        caused by two write paths where only one was validated, and the backfill --
        the unvalidated one -- asserted a kind nothing checked and wrote null
        dimensions for a year's worth of rows.

        Taking a ``StoredArtwork`` rather than four loose values is what makes that
        stick. ``storage_path``, ``mime``, ``width`` and ``height`` cannot be supplied
        by a caller at all; they can only come from bytes the store has sniffed and
        measured. That is a guarantee in the signature rather than a rule someone has
        to remember, and it is the same line #139 and #141 drew at the API boundary.

        **Shape is not checked here, and that is not an omission.** A shape refusal has
        to happen before the bytes reach a content-addressed path, or there is a
        committed file to delete -- and deleting it is unsafe, because content
        addressing means it may already be shared with another row. So the check lives
        in ``ArtworkStore``'s acceptance hook, which ``register_upload`` passes and
        which runs while the file is still staged (#153). By the time a ``StoredArtwork``
        exists, the decision has been made.

        ``tools/artwork_backfill`` deliberately passes no such hook: it declares no kind
        of its own to check against, which is what #154 makes explicit.

        ``is_primary`` is applied by promotion rather than written on the insert, for
        both callers. A second primary would collide with
        ``uq_artwork_one_primary_per_kind``, and promotion runs the demote-then-promote
        path instead. The backfill previously wrote ``is_primary=True`` directly and
        was correct only because its pre-load guaranteed no incumbent; going through
        ``set_primary`` makes it correct whether or not that guarantee holds.

        Args:
            entity_type: Whether this artwork belongs to a title or an asset.
            entity_id: ID of that title or asset.
            artwork_kind_id: ID of the artwork's kind, already resolved.
            stored: What the store produced. The only source of the file's identity,
                type and dimensions.
            is_primary: Make this the artwork for its entity and kind.
            source_scheme_id: ID scheme this came from, paired with the external ID.
            source_external_id: Identifier within that scheme.
            source_url: Where it was fetched from, if anywhere.

        Returns:
            ArtworkRead: The registered artwork.

        Raises:
            HTTPException: 404 if the entity does not exist, 409 if this entity
                already has this exact file.
        """
        self._require_entity(entity_type, entity_id)

        created = self.repo.create(
            ArtworkCreateInternal(
                entity_type=entity_type,
                entity_id=entity_id,
                artwork_kind_id=artwork_kind_id,
                storage_path=stored.storage_path,
                mime=stored.mime,
                width=stored.width,
                height=stored.height,
                is_primary=False,
                source_scheme_id=source_scheme_id,
                source_external_id=source_external_id,
                source_url=source_url,
            )
        )

        if is_primary:
            return self.repo.set_primary(created.id)
        return created

    @translate_repository_errors
    def register_upload(
        self,
        entity_type: EntityTypeEnum,
        entity_id: int,
        upload: ArtworkUploadForm,
        stream: BinaryIO,
    ) -> ArtworkRead:
        """Store an uploaded file and register it against an entity.

        **Everything that can be checked without the bytes is checked first.** An
        unknown entity or an unknown kind code is settled before a single byte is
        written, so an ordinary client mistake never leaves a file behind. Only after
        both pass does the upload reach disk.

        The remaining window -- file written, row rejected -- is deliberate and is why
        this order is safe rather than merely convenient. The file is content
        addressed, so a leftover is a correct, immutable, unreferenced blob that a
        later sweep can reclaim; the reverse order would leave a row pointing at
        nothing, which renders as a broken image forever. This is the ordering
        question ``MediaService.update`` raises, answered the other way round because
        writing here is idempotent where a rename is destructive.

        Args:
            entity_type: Whether this artwork belongs to a title or an asset.
            entity_id: ID of that entity.
            upload: The submitted metadata.
            stream: The uploaded bytes.

        Returns:
            ArtworkRead: The registered artwork.

        Raises:
            HTTPException: 404 for an unknown entity, 422 for an unknown kind, 400 for
                an empty file or one whose dimensions cannot be read, 413 for one over
                the size or pixel cap, 415 for one that is not an image, and 409 if
                this entity already has this exact file.
        """
        # Checked before a byte is written, so an unknown entity or kind is settled
        # without touching the store. register_stored checks the entity again at the
        # point of insert; that is the invariant, this is the fail-fast.
        self._require_entity(entity_type, entity_id)
        kind = self._resolve_kind(upload.artwork_kind)

        # The shape check runs inside the store, after the bytes are measured and
        # before they are moved to a content-addressed path, so a refusal leaves
        # nothing behind. Passing it here rather than checking the result means there
        # is never a committed file to clean up (#153).
        stored = self.store.store(stream, accept=lambda s: self._check_shape(kind, s))

        return self.register_stored(
            entity_type,
            entity_id,
            kind.id,
            stored,
            is_primary=upload.is_primary,
            source_scheme_id=upload.source_scheme_id,
            source_external_id=upload.source_external_id,
            source_url=upload.source_url,
        )

    @translate_repository_errors(not_found_message="Artwork not found")
    def update_artwork(
        self,
        artwork_id: int,
        update: ArtworkPatchPublic,
        exclude_none: bool,
    ) -> ArtworkRead:
        payload = update.model_dump(exclude_none=exclude_none, exclude_unset=True)
        kind_code = payload.pop("artwork_kind", None)
        if kind_code is not None:
            payload["artwork_kind_id"] = self._resolve_kind_id(kind_code)

        # is_primary is pulled out and applied last. Writing it straight through would
        # hit uq_artwork_one_primary_per_kind whenever the entity already had a primary
        # of this kind -- turning "make this the poster" into a 409 the caller can do
        # nothing useful about. Promotion has to demote the incumbent, which is
        # set_primary's job.
        wants_primary = payload.pop("is_primary", None)

        # Called even with nothing left to write, so a missing ID is still a 404 rather
        # than silently succeeding.
        updated = self.repo.update(artwork_id, ArtworkUpdateInternal(**payload))

        if wants_primary is True:
            return self.repo.set_primary(artwork_id)
        if wants_primary is False:
            # Demotion needs no counterpart: an entity is allowed no primary at all,
            # which is what a title whose poster was withdrawn should look like.
            return self.repo.update(
                artwork_id,
                ArtworkUpdateInternal(is_primary=False),  # type: ignore[call-arg]
            )
        return updated

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
