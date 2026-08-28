# app/models/sort_configs.py
from app.models import (
    ArtworkORM,
    AssetORM,
    StreamORM,
    TagORM,
    TitleORM,
    TitleTypeORM,
    TransformRequestORM,
)
from app.utils.sorting import DT_MAX, DT_MIN, SortConfig

TITLE_SORT = SortConfig(
    model=TitleORM,
    allowed_fields={
        "id",
        "name",
        "title_type",
    },
    id_field="id",
    # `title_type` is no longer a column on TitleORM -- it lives on the joined
    # `title_types` table. Sorting on the code keeps the ordering meaningful and
    # stable as new types are added (a new type sorts into its alphabetical
    # position rather than always sorting last, as ordering by the FK id would).
    # Callers must join TitleORM.type for this to resolve; see
    # SQLAlchemyTitleRepository.list_paged.
    field_overrides={"title_type": TitleTypeORM.code.expression},
)

TAG_SORT = SortConfig(
    model=TagORM,
    allowed_fields={
        "id",
        "name",
        "color",
    },
    id_field="id",
)

ASSET_SORT = SortConfig(
    model=AssetORM,
    # `mtime` was offered here and is not any more: nothing sorted by it. The front
    # end lists the other six and defaults to created_at:desc; the runner client
    # sorts by id. It was also the only key needing NULL sentinels, for the 1% of
    # rows that have no mtime. `sort=mtime:desc` is now a 422 rather than a scan
    # nobody asked for.
    #
    # Of the rest, `id` and `path` are index-covered, and `created_at` -- the front
    # end's default -- is served by ix_assets_created_at. `size`, `duration` and
    # `filename` are deliberately unindexed: measured at 13k rows they cost 1-2ms a
    # page, at 130k 11-14ms, and at 1.3M 63-89ms. Cheap at today's size, and each
    # wants an index before `assets` reaches roughly a million rows.
    #
    # A single-column index is enough for any of them. The keyset cursor compares
    # (sort column, id) as a tuple, but measured at 1.3M rows a single-column index
    # serves that in 0.3ms against 0.6ms for a composite (created_at, id): the
    # composite is wider and buys nothing.
    allowed_fields={
        "id",
        "created_at",
        "size",
        "filename",
        "path",
        "duration",
    },
    id_field="id",
)

STREAM_SORT = SortConfig(
    model=StreamORM,
    allowed_fields={
        "id",
        "asset_id",
        "stream_index",
        "codec_type",
    },
    id_field="id",
)

ARTWORK_SORT = SortConfig(
    model=ArtworkORM,
    allowed_fields={
        "id",
        "created_at",
        "entity_id",
        "is_primary",
    },
    id_field="id",
)

TRANSFORM_REQUEST_SORT = SortConfig(
    model=TransformRequestORM,
    allowed_fields={
        "id",
        "created_at",
        "processed_at",
    },
    id_field="id",
    sentinels={
        "processed_at": {
            "asc": DT_MAX,
            "desc": DT_MIN,
        },
    },
)
