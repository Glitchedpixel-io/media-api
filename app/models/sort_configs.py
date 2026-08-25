# app/models/sort_configs.py
from app.models import AssetORM, TagORM, TitleORM, TitleTypeORM, TransformRequestORM
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
    sentinels={
        "mtime": {
            "asc": DT_MAX,
            "desc": DT_MIN,
        },
    },
)

TAG_SORT = SortConfig(
    model=TagORM,
    allowed_fields={
        "id",
        "name",
        "color",
    },
    id_field="id",
    sentinels={
        "mtime": {
            "asc": DT_MAX,
            "desc": DT_MIN,
        },
    },
)

ASSET_SORT = SortConfig(
    model=AssetORM,
    allowed_fields={
        "id",
        "created_at",
        "size",
        "filename",
        "path",
        "duration",
        "mtime",
    },
    id_field="id",
    sentinels={
        "mtime": {
            "asc": DT_MAX,
            "desc": DT_MIN,
        },
    },
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
