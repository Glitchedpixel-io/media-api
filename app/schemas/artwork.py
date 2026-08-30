# app/schemas/artwork.py
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from ._dynamic import make_partial_model
from .enums import EntityTypeEnum
from .mixins import IDMixin


class ArtworkKindAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    code: str = Field(
        ...,
        title="Code",
        description="Short unique code for the artwork kind, e.g. poster",
        max_length=32,
    )
    label: str = Field(..., title="Label", description="Human readable label for the kind")
    description: str | None = Field(
        None, title="Description", description="Helpful information about when to use this kind"
    )

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """Normalise the code to lowercase and reject blank values.

        Args:
            v: The submitted code.

        Returns:
            str: The lowercased, stripped code.

        Raises:
            ValueError: If the code is empty or only whitespace.
        """
        if not v or not v.strip():
            raise ValueError("Artwork kind code cannot be empty")
        return v.strip().lower()


class ArtworkKindCreatePublic(ArtworkKindAttrs):
    pass


class ArtworkKindCreateInternal(ArtworkKindCreatePublic):
    pass


class ArtworkKindRead(ArtworkKindCreateInternal, IDMixin):
    pass


ArtworkKindPatchPublic = make_partial_model(ArtworkKindCreatePublic, name="ArtworkKindPatchPublic")
ArtworkKindUpdateInternal = make_partial_model(
    ArtworkKindCreateInternal, name="ArtworkKindUpdateInternal"
)


class ArtworkAttrs(BaseModel):
    """The public shape of an artwork, where the kind is identified by its code."""

    model_config = {"from_attributes": True, "extra": "forbid"}

    artwork_kind: str = Field(
        ...,
        title="Artwork Kind",
        description=(
            "Code of the artwork's kind, e.g. poster, backdrop or thumbnail. Must match "
            "the code of an existing artwork kind."
        ),
        max_length=32,
    )
    storage_path: str = Field(
        ...,
        title="Storage Path",
        description="Path to the file relative to ARTWORK_ROOT, in the content-addressed layout",
    )
    mime: str = Field(..., title="MIME type", description="Media type of the file, e.g. image/jpeg")
    width: int = Field(
        ...,
        title="Width",
        description="Pixel width, measured from the file by the server",
        gt=0,
    )
    height: int = Field(
        ...,
        title="Height",
        description="Pixel height, measured from the file by the server",
        gt=0,
    )
    is_primary: bool = Field(
        False,
        title="Is Primary",
        description="Whether this is the artwork to use for its entity and kind",
    )
    source_scheme_id: int | None = Field(
        None,
        title="Source Scheme ID",
        description="ID scheme this artwork was sourced from; paired with source_external_id",
    )
    source_external_id: str | None = Field(
        None,
        title="Source External ID",
        description="Identifier within source_scheme_id; paired with source_scheme_id",
    )
    source_url: str | None = Field(
        None, title="Source URL", description="Where this artwork was fetched from, if anywhere"
    )

    @model_validator(mode="after")
    def source_scheme_and_id_travel_together(self) -> ArtworkAttrs:
        """Reject half a provenance pair.

        A scheme without an identifier cannot be resolved and an identifier without a
        scheme cannot be interpreted, so the database refuses the pair via
        ``ck_artwork_source_pair``. Catching it here turns what would surface as a
        ``CheckViolation`` into an ordinary validation error naming the fields --
        which matters because a 422 raised from the service reaches the client through
        ``domain_error_detail`` with an empty ``loc``.

        Returns:
            ArtworkAttrs: This model, unchanged.

        Raises:
            ValueError: If exactly one of the pair is set.
        """
        if (self.source_scheme_id is None) != (self.source_external_id is None):
            raise ValueError(
                "source_scheme_id and source_external_id must be provided together, or not at all"
            )
        return self


class ArtworkCreatePublic(ArtworkAttrs):
    pass


class ArtworkCreateInternal(BaseModel):
    """The persistence shape of an artwork, where the kind is a foreign key.

    ``extra="forbid"`` is load-bearing rather than decorative, for the same reason it
    is on ``TitleCreateInternal``: the public model carries ``artwork_kind`` (a code)
    and this one carries ``artwork_kind_id``, so a caller that forgets to translate
    between them gets a loud validation error instead of Pydantic silently dropping
    the field.
    """

    model_config = {"from_attributes": True, "extra": "forbid"}

    entity_type: EntityTypeEnum = Field(
        ..., title="Entity Type", description="Whether this artwork belongs to a title or an asset"
    )
    entity_id: int = Field(
        ..., title="Entity ID", description="ID of the title or asset this artwork belongs to"
    )
    artwork_kind_id: int = Field(
        ..., title="Artwork Kind ID", description="ID of the artwork's kind in artwork_kinds"
    )
    storage_path: str = Field(
        ..., title="Storage Path", description="Path relative to ARTWORK_ROOT"
    )
    mime: str = Field(..., title="MIME type", description="Media type of the file")
    width: int = Field(..., title="Width", description="Pixel width, measured from the file")
    height: int = Field(..., title="Height", description="Pixel height, measured from the file")
    is_primary: bool = Field(False, title="Is Primary", description="Whether this is the primary")
    source_scheme_id: int | None = Field(
        None, title="Source Scheme ID", description="Source scheme"
    )
    source_external_id: str | None = Field(
        None, title="Source External ID", description="Identifier within the source scheme"
    )
    source_url: str | None = Field(None, title="Source URL", description="Where it came from")


class ArtworkRead(ArtworkAttrs, IDMixin):
    entity_type: EntityTypeEnum = Field(
        ..., title="Entity Type", description="Whether this artwork belongs to a title or an asset"
    )
    entity_id: int = Field(
        ..., title="Entity ID", description="ID of the title or asset this artwork belongs to"
    )
    created_at: datetime = Field(
        ..., title="Created At", description="When this artwork was registered"
    )


class ArtworkPatchPublic(BaseModel):
    """What a client may change about an artwork after it was uploaded.

    Deliberately **not** a partial of ``ArtworkCreatePublic``, and the distinction is
    the point of the model: ``storage_path``, ``mime``, ``width`` and ``height`` are
    established by the server from the bytes it received, so a client able to rewrite
    them could undo every check ``ArtworkStore`` performs. That store sniffs magic
    numbers precisely because the filename and ``Content-Type`` are caller-controlled;
    a patchable ``mime`` hands that decision straight back, and a patchable
    ``storage_path`` lets a row be repointed at any other file under ``ARTWORK_ROOT``
    while keeping the dimensions of the file it used to be.

    The line is: **discovered from the bytes is immutable, asserted by the caller is
    mutable.** Provenance and the kind are the caller's claims and stay editable; the
    kind in particular is the lever any reclassification needs. See #139.

    ``extra="forbid"`` makes a submitted ``width`` a 422 naming the field rather than a
    silent no-op, which would otherwise leave the caller believing the write landed.
    """

    model_config = {"from_attributes": True, "extra": "forbid"}

    artwork_kind: str | None = Field(
        None,
        title="Artwork Kind",
        description=(
            "Code of the artwork's kind, e.g. poster, backdrop or thumbnail. Must match "
            "the code of an existing artwork kind."
        ),
        max_length=32,
    )
    is_primary: bool | None = Field(
        None,
        title="Is Primary",
        description=(
            "Make this the artwork used for its entity and kind, demoting whichever "
            "artwork currently holds that position."
        ),
    )
    source_scheme_id: int | None = Field(
        None,
        title="Source Scheme ID",
        description="ID scheme this artwork was sourced from; paired with source_external_id",
    )
    source_external_id: str | None = Field(
        None,
        title="Source External ID",
        description="Identifier within source_scheme_id; paired with source_scheme_id",
    )
    source_url: str | None = Field(
        None, title="Source URL", description="Where this artwork was fetched from, if anywhere"
    )

    @model_validator(mode="after")
    def source_scheme_and_id_travel_together(self) -> ArtworkPatchPublic:
        """Reject half a provenance pair, as ``ArtworkAttrs`` does.

        Returns:
            ArtworkPatchPublic: This model, unchanged.

        Raises:
            ValueError: If exactly one of the pair is set.
        """
        if (self.source_scheme_id is None) != (self.source_external_id is None):
            raise ValueError(
                "source_scheme_id and source_external_id must be provided together, or not at all"
            )
        return self


ArtworkUpdateInternal = make_partial_model(ArtworkCreateInternal, name="ArtworkUpdateInternal")


class ArtworkUploadForm(BaseModel):
    """The metadata accompanying an uploaded artwork file.

    Carries no ``storage_path`` or ``mime``: both are derived from the bytes rather
    than taken from the caller. A client-supplied MIME type is a claim about a file
    the client also controls, so trusting it would let an HTML document be recorded --
    and later served -- as an image. ``ArtworkStore`` sniffs the real format.

    It carries no ``width`` or ``height`` either, for the same reason and since #141.
    Those are measured from the bytes by ``ArtworkStore`` and taken from what it
    returns, never from the request: a submitted size is a claim about a file the
    caller also controls, and one the caller could use to describe an image as
    something it is not. An earlier version of this docstring argued the opposite --
    that reading them needed an image library this project did not depend on -- which
    #140 settled by taking the dependency.
    """

    model_config = {"from_attributes": True, "extra": "forbid"}

    artwork_kind: str = Field(
        ...,
        title="Artwork Kind",
        description=(
            "Code of the artwork's kind, e.g. poster, backdrop or thumbnail. Must match "
            "the code of an existing artwork kind."
        ),
        max_length=32,
    )
    is_primary: bool = Field(
        False,
        title="Is Primary",
        description=(
            "Make this the artwork used for its entity and kind, demoting whichever "
            "artwork currently holds that position."
        ),
    )
    source_scheme_id: int | None = Field(
        None,
        title="Source Scheme ID",
        description="ID scheme this artwork was sourced from; paired with source_external_id",
    )
    source_external_id: str | None = Field(
        None,
        title="Source External ID",
        description="Identifier within source_scheme_id; paired with source_scheme_id",
    )
    source_url: str | None = Field(
        None, title="Source URL", description="Where this artwork was fetched from, if anywhere"
    )

    @model_validator(mode="after")
    def source_scheme_and_id_travel_together(self) -> ArtworkUploadForm:
        """Reject half a provenance pair, as ``ArtworkAttrs`` does.

        Returns:
            ArtworkUploadForm: This model, unchanged.

        Raises:
            ValueError: If exactly one of the pair is set.
        """
        if (self.source_scheme_id is None) != (self.source_external_id is None):
            raise ValueError(
                "source_scheme_id and source_external_id must be provided together, or not at all"
            )
        return self
