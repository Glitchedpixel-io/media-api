"""Relationships gated behind ``include=`` serialise as empty, with no query, unless asked for.

``AssetORM.tags``, ``AssetORM.master_asset``, ``TitleORM.tags`` and ``TitleORM.references``
are loaded only when the list endpoints' ``include=`` names them. When it does not, the
response still carries the field, and its value is part of the API: ``[]`` for a list,
``null`` for the master asset. These tests pin each endpoint's exact output, and that
answering it issued no query against the relationship's table, so changing how the
relationships are loaded cannot change either one unnoticed.

The detail endpoints differ from the lists, and that is pinned too rather than
normalised: an asset's detail eager-loads its master asset but not its tags, and a
title's detail is built from ``TitleRead``, which has neither field, so both are ``null``.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from app.models import TitleORM
from app.repositories import TitleReferenceRepository
from app.repositories.protocols import MediaRepository, TagRepository, TitleRepository
from app.schemas import AssetCreateInternal, TagCreateInternal, TitleReferenceCreateInternal
from tests.factories import (
    AssetReadFactory,
    TitleReadFactory,
    TitleReferenceReadFactory,
    get_title_internal,
)

pytestmark = [pytest.mark.api, pytest.mark.integration]

_ASSET_FIELDS = {"id", "created_at", "master_asset_id"}


@contextmanager
def _statements_touching(engine: Engine, *tables: str) -> Iterator[list[str]]:
    """Collect every SQL statement that names any of ``tables`` inside the block.

    Args:
        engine (Engine): The engine the request's session is bound to.
        *tables (str): Table names to match, case-insensitively.

    Yields:
        list[str]: The matching statements, appended as they are executed.
    """
    seen: list[str] = []

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if any(table in statement.lower() for table in tables):
            seen.append(statement)

    event.listen(engine, "after_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine, "after_cursor_execute", _record)


@pytest.fixture
def tagged_derivative(media_repository: MediaRepository, tag_repository: TagRepository) -> dict:
    """A tagged asset derived from a tagged master asset.

    Args:
        media_repository (MediaRepository): Creates the assets.
        tag_repository (TagRepository): Creates and applies the tag.

    Returns:
        dict: The ``master`` and ``derivative`` asset ids, and the ``tag`` id.
    """
    master = media_repository.create(
        AssetCreateInternal(**AssetReadFactory().model_dump(exclude=_ASSET_FIELDS))  # type: ignore[arg-type]
    )
    derivative = media_repository.create(
        AssetCreateInternal(
            master_asset_id=master.id,
            **AssetReadFactory().model_dump(exclude=_ASSET_FIELDS),  # type: ignore[arg-type]
        )
    )
    tag = tag_repository.create(TagCreateInternal(name="pinned"))
    tag_repository.add_asset_tags(master.id, [tag.id])
    tag_repository.add_asset_tags(derivative.id, [tag.id])
    return {"master": master.id, "derivative": derivative.id, "tag": tag.id}


@pytest.fixture
def tagged_title(
    title_repository: TitleRepository,
    title_reference_repository: TitleReferenceRepository,
    tag_repository: TagRepository,
) -> dict:
    """A title with one tag and one reference.

    Args:
        title_repository (TitleRepository): Creates the title.
        title_reference_repository (TitleReferenceRepository): Creates the reference.
        tag_repository (TagRepository): Creates and applies the tag.

    Returns:
        dict: The ``title``, ``reference`` and ``tag`` ids.
    """
    title = title_repository.create(get_title_internal(TitleReadFactory()))
    reference = title_reference_repository.create(
        TitleReferenceCreateInternal(
            title_id=title.id,
            **TitleReferenceReadFactory().model_dump(exclude={"id", "title_id"}),  # type: ignore[arg-type]
        )
    )
    tag = tag_repository.create(TagCreateInternal(name="pinned"))
    tag_repository.add_title_tags(title.id, [tag.id])
    return {"title": title.id, "reference": reference.id, "tag": tag.id}


class TestAssetList:
    def test_unrequested_relationships_are_empty_and_unqueried(
        self, client: TestClient, _test_engine: Engine, tagged_derivative: dict
    ) -> None:
        with _statements_touching(_test_engine, "asset_tags") as statements:
            response = client.get("/api/assets")

        assert response.status_code == HTTPStatus.OK
        items = response.json()["items"]
        assert len(items) == 2
        for item in items:
            assert item["tags"] == []
            assert item["master_asset"] is None
        assert statements == []

    def test_master_asset_id_is_still_reported(
        self, client: TestClient, tagged_derivative: dict
    ) -> None:
        items = client.get("/api/assets").json()["items"]
        row = next(i for i in items if i["id"] == tagged_derivative["derivative"])
        assert row["master_asset_id"] == tagged_derivative["master"]

    def test_include_tags_loads_them(
        self, client: TestClient, _test_engine: Engine, tagged_derivative: dict
    ) -> None:
        with _statements_touching(_test_engine, "asset_tags") as statements:
            items = client.get("/api/assets?include=tags").json()["items"]

        assert [[t["id"] for t in i["tags"]] for i in items] == [[tagged_derivative["tag"]]] * 2
        assert all(i["master_asset"] is None for i in items)
        # Control for the empty capture above: the same listener does see the load
        assert statements != []

    def test_include_master_asset_loads_it(
        self, client: TestClient, tagged_derivative: dict
    ) -> None:
        items = client.get("/api/assets?include=master_asset").json()["items"]
        by_id = {i["id"]: i for i in items}
        derivative = by_id[tagged_derivative["derivative"]]
        assert derivative["master_asset"]["id"] == tagged_derivative["master"]
        assert by_id[tagged_derivative["master"]]["master_asset"] is None
        assert all(i["tags"] == [] for i in items)


class TestAssetDetail:
    def test_master_asset_is_loaded_and_tags_are_empty(
        self, client: TestClient, _test_engine: Engine, tagged_derivative: dict
    ) -> None:
        with _statements_touching(_test_engine, "asset_tags") as statements:
            response = client.get(f"/api/assets/{tagged_derivative['derivative']}")

        assert response.status_code == HTTPStatus.OK
        body = response.json()
        assert body["master_asset"]["id"] == tagged_derivative["master"]
        assert body["tags"] == []
        assert statements == []


class TestTitleList:
    def test_unrequested_relationships_are_empty_and_unqueried(
        self, client: TestClient, _test_engine: Engine, tagged_title: dict
    ) -> None:
        with _statements_touching(_test_engine, "title_tags", "title_references") as statements:
            response = client.get("/api/titles")

        assert response.status_code == HTTPStatus.OK
        row = next(i for i in response.json()["items"] if i["id"] == tagged_title["title"])
        assert row["tags"] == []
        assert row["references"] == []
        assert statements == []

    def test_include_loads_each_relationship(
        self, client: TestClient, _test_engine: Engine, tagged_title: dict
    ) -> None:
        def row(include: str) -> dict:
            with _statements_touching(_test_engine, "title_tags", "title_references") as seen:
                items = client.get(f"/api/titles?include={include}").json()["items"]
            # Control for the empty capture above: the same listener does see the load
            assert seen != []
            return next(i for i in items if i["id"] == tagged_title["title"])

        tags_only = row("tags")
        assert [t["id"] for t in tags_only["tags"]] == [tagged_title["tag"]]
        assert tags_only["references"] == []

        references_only = row("references")
        assert [r["id"] for r in references_only["references"]] == [tagged_title["reference"]]
        assert references_only["tags"] == []


class TestTitleDetail:
    def test_relationships_are_null(self, client: TestClient, tagged_title: dict) -> None:
        response = client.get(f"/api/titles/{tagged_title['title']}")

        assert response.status_code == HTTPStatus.OK
        body = response.json()
        assert body["tags"] is None
        assert body["references"] is None


def test_an_accidental_load_raises(db_session: Session, tagged_title: dict) -> None:
    """Nothing can load a gated relationship by touching it; only ``include=`` does.

    Args:
        db_session (Session): A session on the test database.
        tagged_title (dict): The title to read.
    """
    title = db_session.get(TitleORM, tagged_title["title"])
    assert title is not None
    with pytest.raises(InvalidRequestError, match="lazy='raise'"):
        _ = title.tags
