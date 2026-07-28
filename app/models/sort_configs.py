# app/models/sort_configs.py
from app.models import AssetORM, TagORM, TitleORM, TransformRequestORM
from app.utils.sorting import DT_MAX, DT_MIN, SortConfig

TITLE_SORT = SortConfig(
    model=TitleORM,
    allowed_fields={
        "id",
        "name",
        "title_type",
    },
    id_field="id",
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
