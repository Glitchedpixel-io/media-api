# tests/integration/api/test_title_contents_api.py

from http import HTTPStatus

"""
FastAPI Integration Tests for Title Contents API
- Full-stack integration from API to database
"""

import pytest
from fastapi.testclient import TestClient

from app.models.title_contents import ContentKind
from app.repositories.protocols import (
    MediaRepository,
    TitleContentRepository,
    TitleRepository,
)
from app.schemas import (
    AssetCreateInternal,
    TitleContentInsert,
    TitleCreateInternal,
)
from tests.factories import (
    AssetReadFactory,
    TitleReadFactory,
    get_title_internal,
)


@pytest.mark.api
@pytest.mark.integration
class TestTitleContentsAPI:
    """Tests the title contents part of the API stack"""

    def test_get_empty_contents_list(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        title_repository: TitleRepository,
        title_content_repository: TitleContentRepository,
    ):
        """Test that retrieving the contents of a title returns an empty list"""
        # Arrange
        title = TitleReadFactory()
        created_title = title_repository.create(get_title_internal(title))
        title_id = created_title.id
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id
        for _ in range(3):
            other_title = TitleReadFactory()
            created_other_title = title_repository.create(get_title_internal(other_title))
            title_content_repository.create_positioned(
                parent_title_id=created_other_title.id,
                title_content=TitleContentInsert(kind=ContentKind.asset, asset_id=asset_id),
                anchor="start",
            )

        # Act
        response = client.get(f"/api/titles/{title_id}/contents")

        # Assert
        assert response.status_code == HTTPStatus.OK
        assert response.json() == []

    def test_get_contents_list(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        title_repository: TitleRepository,
        title_content_repository: TitleContentRepository,
    ):
        """Test that retrieving the contents of a title returns the correct items"""
        # Arrange
        title = TitleReadFactory()
        created_title = title_repository.create(get_title_internal(title))
        title_id = created_title.id
        ids, asset_ids = [], []
        for _ in range(3):
            asset = AssetReadFactory()
            created_asset = media_repository.create(
                AssetCreateInternal(
                    **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})
                )
            )
            asset_id = created_asset.id
            asset_ids.append(asset_id)
            content_item = title_content_repository.create_positioned(
                parent_title_id=title_id,
                title_content=TitleContentInsert(kind=ContentKind.asset, asset_id=asset_id),
                anchor="end",
            )
            ids.append(content_item.id)

        # Act
        response = client.get(f"/api/titles/{title_id}/contents")

        # Assert
        assert response.status_code == HTTPStatus.OK
        assert isinstance(response.json(), list)
        contents = response.json()

        assert len(contents) == 3
        # Verify title data is correctly serialized
        assert (
            all(item["asset_id"] in asset_ids for item in contents)
            and all(item["id"] in ids for item in contents)
            and all(
                "asset" in item and item["asset"]["id"] == item["asset_id"] for item in contents
            )
        )

    def test_add_to_title_contents_list(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        title_repository: TitleRepository,
        title_content_repository: TitleContentRepository,
    ):
        """Test that adding items to a title's contents works as expected"""
        # Arrange
        title = TitleReadFactory()
        created_title = title_repository.create(get_title_internal(title))
        title_id = created_title.id
        assets = [
            media_repository.create(
                AssetCreateInternal(
                    **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})
                )
            )
            for _ in range(10)
        ]

        for asset in assets:
            # Act
            response = client.post(
                f"/api/titles/{title_id}/contents",
                json={
                    "kind": "asset",
                    "asset_id": asset.id,
                },
            )
            # Assert
            assert response.status_code == HTTPStatus.CREATED
            r = response.json()
            assert r["parent_title_id"] == title_id
            assert r["asset_id"] == asset.id
            assert r["child_title_id"] is None

    def test_add_to_title_contents_list_at_position(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        title_repository: TitleRepository,
        title_content_repository: TitleContentRepository,
    ):
        """Test that items may be added to the start of a list of contents"""
        # Arrange
        title = TitleReadFactory()
        created_title = title_repository.create(get_title_internal(title))
        title_id = created_title.id
        assets = [
            media_repository.create(
                AssetCreateInternal(
                    **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})
                )
            )
            for _ in range(10)
        ]

        for asset in assets:
            # Act
            response = client.post(
                f"/api/titles/{title_id}/contents/positioned?position=start",
                json={
                    "kind": "asset",
                    "asset_id": asset.id,
                },
            )
            # Assert
            assert response.status_code == HTTPStatus.CREATED
            r = response.json()
            assert r["parent_title_id"] == title_id
            assert r["asset_id"] == asset.id
            assert r["child_title_id"] is None

    def test_contents_reordering(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        title_repository: TitleRepository,
        title_content_repository: TitleContentRepository,
    ):
        """Test that items can be added a reordered in a title's contents"""
        # Arrange
        title = TitleReadFactory()
        created_title = title_repository.create(get_title_internal(title))
        title_id = created_title.id
        assets = [
            media_repository.create(
                AssetCreateInternal(
                    **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})
                )
            )
            for _ in range(3)
        ]

        # Phase 1 - adding the contents
        for asset in assets:
            # Act
            response = client.post(
                f"/api/titles/{title_id}/contents",
                json={
                    "kind": "asset",
                    "asset_id": asset.id,
                },
            )
            # Assert
            assert response.status_code == HTTPStatus.CREATED
            r = response.json()
            assert r["parent_title_id"] == title_id
            assert r["asset_id"] == asset.id
            assert r["child_title_id"] is None
            last_asset_id = asset.id

        # Phase 2 - reordering the contents
        for i in range(100):
            # Act - get the contents in their current order
            response = client.get(f"/api/titles/{title_id}/contents")
            # Assert it is as expected
            assert response.status_code == HTTPStatus.OK
            r = response.json()
            assert isinstance(r, list) and len(r) == 3
            assert r[2]["asset_id"] == last_asset_id

            # Act - reorder the contents
            response = client.patch(
                f"/api/titles/{title_id}/contents/{r[2]['id']}/reorder?before_id={r[1]['id']}&after_id={r[0]['id']}",
            )
            # Assert
            assert response.status_code == HTTPStatus.OK
            r2 = response.json()
            # we should get the same title content item back
            assert r2["id"] == r[2]["id"]

            # Act - get the contents in their new order
            response = client.get(f"/api/titles/{title_id}/contents")
            # Assert it is as expected
            assert response.status_code == HTTPStatus.OK
            r3 = response.json()
            assert isinstance(r3, list) and len(r3) == 3
            # the item that was at the end should now have moved up one space
            assert r3[1]["asset_id"] == last_asset_id
            # get the asset_id of the new item at the end
            last_asset_id = r3[2]["asset_id"]
