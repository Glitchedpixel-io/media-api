# tests/integration/api/test_tags_api.py
from __future__ import annotations

from http import HTTPStatus
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.repositories import MediaRepository
from app.repositories.protocols import (
    TagRepository,
    TitleRepository,
)
from app.schemas import (
    AssetCreateInternal,
    TagCreateInternal,
    TitleCreateInternal,
)
from tests.factories import (
    AssetReadFactory,
    TagReadFactory,
    TitleReadFactory,
    get_title_internal,
    get_tag_creation_json,
)


@pytest.mark.api
@pytest.mark.integration
class TestTagsAPI:
    """Test the /tags API endpoints with full-stack integration."""

    def test_create_tag_success(self, client: TestClient, db_session: Session) -> None:
        """Test successful tag creation through full API stack."""
        # Arrange
        tag = TagReadFactory()
        tag_data = get_tag_creation_json(tag)  # type: ignore

        # Act
        response = client.post("/api/tags", json=tag_data)

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        r = response.json()

        # Verify response structure and data
        assert r["name"] == tag.name.lower()  # type: ignore
        assert r["description"] == tag.description
        assert r["color"] is not None and len(r["color"]) == 7
        assert r["created_at"] is not None
        assert r["updated_at"] is not None
        assert r["parent_id"] is None

        # Verify database persistence
        db_session.commit()
        from app.models import TagORM

        db_tag = db_session.query(TagORM).filter_by(name=tag.name.lower()).first()  # type: ignore
        assert db_tag is not None
        assert db_tag.name == tag.name.lower()  # type: ignore
        assert db_tag.description == tag.description  # type: ignore
        assert db_tag.color == tag.color
        assert db_tag.created_at is not None
        assert db_tag.updated_at is not None
        assert db_tag.parent_id is None

    def test_get_tags_with_data(self, client: TestClient, tag_repository: TagRepository) -> None:
        """Test retrieving tags after creating some through repository."""
        # Arrange - create test data using factory
        tag1 = TagReadFactory()
        tag2 = TagReadFactory()
        tag3 = TagReadFactory()
        t1 = tag_repository.create(
            TagCreateInternal(**tag1.model_dump(exclude={"id", "created_at", "updated_at"}))  # type: ignore
        )
        t2 = tag_repository.create(
            TagCreateInternal(**tag2.model_dump(exclude={"id", "created_at", "updated_at"}))  # type: ignore
        )
        t3 = tag_repository.create(
            TagCreateInternal(parent_id=t2.id, **tag3.model_dump(exclude={"id", "created_at", "updated_at", "parent_id"}))  # type: ignore
        )

        # Act
        response = client.get("/api/tags")
        response2 = client.get(f"/api/tags/{t2.id}/tags")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert len(r["items"]) == 2  # since only top-level tags are returned

        # Verify tag data is correctly serialized
        assert {item.name for item in [t1, t2]} == {t["name"] for t in r["items"]}

        assert response2.status_code == HTTPStatus.OK
        r2 = response2.json()
        assert len(r2["items"]) == 1
        assert r2["items"][0]["name"] == t3.name

    def test_add_child_tag(self, client: TestClient, tag_repository: TagRepository) -> None:
        """Test create child tag after creating a parent through repository."""
        # Arrange - create test data using factory
        parent_tag = TagReadFactory()
        parent = tag_repository.create(
            TagCreateInternal(**parent_tag.model_dump(exclude={"id", "created_at", "updated_at"}))  # type: ignore
        )
        child_tag_json_data = get_tag_creation_json(TagReadFactory())  # type: ignore

        # Act
        response = client.post(f"/api/tags/{parent.id}/tags", json=child_tag_json_data)

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        r = response.json()
        assert r["parent_id"] == parent.id

    def test_update_tag_with_patch(
        self, client: TestClient, tag_repository: TagRepository, db_session: Session
    ) -> None:
        """Tests a PATCH update to a tag which should ignore unset fields."""
        # Arrange - create test data using factory
        tag = TagReadFactory(name="old", description="unchanged", color="#ab12ef")
        t = tag_repository.create(
            TagCreateInternal(**tag.model_dump(exclude={"id", "created_at", "updated_at"}))  # type: ignore
        )

        # Act
        response = client.patch(f"/api/tags/{t.id}", json={"name": "new", "description": None})

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["name"] == "new"
        assert r["description"] == tag.description

        # Verify database persistence
        db_session.commit()
        from app.models import TagORM

        db_tag = db_session.query(TagORM).filter_by(id=t.id).first()
        assert db_tag is not None
        assert db_tag.name == "new"
        assert db_tag.description == t.description
        assert db_tag.color == t.color
        assert db_tag.created_at is not None
        assert db_tag.updated_at is not None
        assert db_tag.parent_id is None

    def test_update_tag_with_put(
        self, client: TestClient, tag_repository: TagRepository, db_session: Session
    ) -> None:
        """Tests a PATCH update to a tag which should ignore unset fields."""
        # Arrange - create test data using factory
        tag = TagReadFactory(name="old", description="should change", color="#ab12ef")
        t = tag_repository.create(
            TagCreateInternal(**tag.model_dump(exclude={"id", "created_at", "updated_at"}))  # type: ignore
        )

        # Act
        response = client.put(
            f"/api/tags/{t.id}",
            json={"name": "new", "description": None, "color": "#ab12ef"},
        )

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["name"] == "new"
        assert r["description"] is None
        assert r["color"] == "#ab12ef"

        # Verify database persistence
        db_session.commit()
        from app.models import TagORM

        db_tag = db_session.query(TagORM).filter_by(id=t.id).first()
        assert db_tag is not None
        assert db_tag.name == "new"
        assert db_tag.description is None
        assert db_tag.color == t.color
        assert db_tag.created_at is not None
        assert db_tag.updated_at is not None
        assert db_tag.parent_id is None

    # ----------------------- Tagging assets

    def test_tag_asset_with_untag(
        self,
        client: TestClient,
        tag_repository: TagRepository,
        media_repository: MediaRepository,
        db_session: Session,
    ) -> None:
        """Test adding a tag to an asset."""
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test"))
        a = media_repository.create(
            AssetCreateInternal(
                **AssetReadFactory().model_dump(exclude={"id", "created_at"})  # type: ignore
            )
        )
        asset_id = a.id

        # Act - tag the asset
        response = client.put(f"/api/assets/{asset_id}/tags", json={"tag_ids": [t.id]})

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Act - get the asset's tags
        response = client.get(f"/api/assets/{asset_id}/tags")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Verify database persistence
        db_session.commit()
        from app.models import AssetTagORM

        db_asset_tag = (
            db_session.query(AssetTagORM)
            .where(AssetTagORM.asset_id == asset_id)
            .where(AssetTagORM.tag_id == t.id)
            .first()
        )
        assert db_asset_tag is not None

        # Act 2
        response = client.delete(f"/api/assets/{asset_id}/tags/{t.id}")

        # Assert 2
        assert response.status_code == HTTPStatus.OK
        db_asset_tag = (
            db_session.query(AssetTagORM)
            .where(AssetTagORM.asset_id == asset_id)
            .where(AssetTagORM.tag_id == t.id)
            .first()
        )
        assert db_asset_tag is None

        # Act 3
        response = client.delete(f"/api/assets/{asset_id}/tags/{t.id}")

        # Assert 3
        assert response.status_code == HTTPStatus.NO_CONTENT

    def test_tag_asset_by_name(
        self,
        client: TestClient,
        tag_repository: TagRepository,
        media_repository: MediaRepository,
        db_session: Session,
    ) -> None:
        """Test adding a tag to an asset by name"""
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))
        a = media_repository.create(
            AssetCreateInternal(
                **AssetReadFactory().model_dump(exclude={"id", "created_at"})  # type: ignore
            )
        )
        asset_id = a.id

        # Act - tag the asset
        response = client.post(f"/api/assets/{asset_id}/tags", json={"tag_names": [t.name]})

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert not r["tagging_errors"]
        assert isinstance(r["added_tags"], list)
        added_tags = r["added_tags"]
        assert len(added_tags) == 1
        r0 = added_tags[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Act - get the asset's tags
        response = client.get(f"/api/assets/{asset_id}/tags")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Verify database persistence
        db_session.commit()
        from app.models import AssetTagORM

        db_asset_tag = (
            db_session.query(AssetTagORM)
            .where(AssetTagORM.asset_id == asset_id)
            .where(AssetTagORM.tag_id == t.id)
            .first()
        )
        assert db_asset_tag is not None

    def test_tag_asset_by_name_with_creation(
        self,
        client: TestClient,
        tag_repository: TagRepository,
        media_repository: MediaRepository,
        db_session: Session,
    ) -> None:
        """Test adding a tag to an asset by name"""
        # Arrange
        a = media_repository.create(
            AssetCreateInternal(
                **AssetReadFactory().model_dump(exclude={"id", "created_at"})  # type: ignore
            )
        )
        asset_id = a.id

        # Act - tag the asset
        response = client.post(
            f"/api/assets/{asset_id}/tags", json={"tag_names": ["NEW-tag", "new-tag"]}
        )

        # note: we should only except one tag creation since the two names passed to the API are the same
        #       when lowercased

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert not r["tagging_errors"]
        assert isinstance(r["added_tags"], list)
        added_tags = r["added_tags"]
        assert len(added_tags) == 1
        r0 = added_tags[0]
        assert r0["name"] == "new-tag"  # note that the name is lowercased by the API
        tag_id = r0["id"]

        # Act - get the asset's tags
        response = client.get(f"/api/assets/{asset_id}/tags")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == tag_id and r0["name"] == "new-tag"

        # Verify database persistence
        db_session.commit()
        from app.models import AssetTagORM, TagORM

        db_asset_tag = (
            db_session.query(AssetTagORM)
            .where(AssetTagORM.asset_id == asset_id)
            .where(AssetTagORM.tag_id == tag_id)
            .first()
        )
        assert db_asset_tag is not None

        db_tag = db_session.query(TagORM).filter_by(id=tag_id).first()
        assert db_tag is not None
        assert db_tag.name == "new-tag"

    def test_tag_non_existent_asset(
        self,
        client: TestClient,
        tag_repository: TagRepository,
    ) -> None:
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))

        # Act
        response = client.put("/api/assets/999/tags", json={"tag_ids": [t.id]})

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_tag_non_existent_asset_by_tag_name(
        self,
        client: TestClient,
        tag_repository: TagRepository,
    ) -> None:
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))

        # Act
        response = client.post("/api/assets/999/tags", json={"tag_names": [t.name]})

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_untag_non_existent_asset(
        self,
        client: TestClient,
        tag_repository: TagRepository,
    ) -> None:
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))

        # Act
        response = client.delete(f"/api/assets/999/tags/{t.id}")

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    # ----------------------- Tagging titles

    def test_tag_title_with_untag(
        self,
        client: TestClient,
        tag_repository: TagRepository,
        title_repository: TitleRepository,
        db_session: Session,
    ) -> None:
        """Test adding a tag to a title."""
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test"))
        o = title_repository.create(get_title_internal(TitleReadFactory()))  # type: ignore
        title_id = o.id

        # Act - tag the title
        response = client.put(f"/api/titles/{title_id}/tags", json={"tag_ids": [t.id]})

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Act - get the title's tags
        response = client.get(f"/api/titles/{title_id}/tags")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Verify database persistence
        db_session.commit()
        from app.models import TitleTagORM

        db_title_tag = (
            db_session.query(TitleTagORM)
            .where(TitleTagORM.title_id == title_id)
            .where(TitleTagORM.tag_id == t.id)
            .first()
        )
        assert db_title_tag is not None

        # Act 2
        response = client.delete(f"/api/titles/{title_id}/tags/{t.id}")

        # Assert 2
        assert response.status_code == HTTPStatus.OK
        db_title_tag = (
            db_session.query(TitleTagORM)
            .where(TitleTagORM.title_id == title_id)
            .where(TitleTagORM.tag_id == t.id)
            .first()
        )
        assert db_title_tag is None

        # Act 3
        response = client.delete(f"/api/titles/{title_id}/tags/{t.id}")

        # Assert 3 - should still be ok
        assert response.status_code == HTTPStatus.NO_CONTENT

    def test_tag_title_by_name(
        self,
        client: TestClient,
        tag_repository: TagRepository,
        title_repository: TitleRepository,
        db_session: Session,
    ) -> None:
        """Test adding a tag to a title by name"""
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))
        o = title_repository.create(get_title_internal(TitleReadFactory()))  # type: ignore
        title_id = o.id

        # Act - tag the title
        response = client.post(f"/api/titles/{title_id}/tags", json={"tag_names": [t.name]})

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert not r["tagging_errors"]
        assert isinstance(r["added_tags"], list)
        added_tags = r["added_tags"]
        assert len(added_tags) == 1
        r0 = added_tags[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Act - get the title's tags
        response = client.get(f"/api/titles/{title_id}/tags")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == t.id and r0["name"] == t.name

        # Verify database persistence
        db_session.commit()
        from app.models import TitleTagORM

        db_title_tag = (
            db_session.query(TitleTagORM)
            .where(TitleTagORM.title_id == title_id)
            .where(TitleTagORM.tag_id == t.id)
            .first()
        )
        assert db_title_tag is not None

    def test_tag_asset_by_name_with_creation_v2(
        self,
        client: TestClient,
        tag_repository: TagRepository,
        media_repository: MediaRepository,
        db_session: Session,
    ) -> None:
        """Test adding a tag to an asset by name"""
        # Arrange
        a = media_repository.create(
            AssetCreateInternal(
                **AssetReadFactory().model_dump(exclude={"id", "created_at"})  # type: ignore
            )
        )
        asset_id = a.id

        # Act - tag the asset
        response = client.post(
            f"/api/assets/{asset_id}/tags", json={"tag_names": ["NEW-tag", "new-tag"]}
        )

        # note: we should only except one tag creation since the two names passed to the API are the same
        #       when lowercased

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert not r["tagging_errors"]
        assert isinstance(r["added_tags"], list)
        added_tags = r["added_tags"]
        assert len(added_tags) == 1
        r0 = added_tags[0]
        assert r0["name"] == "new-tag"  # note that the name is lowercased by the API
        tag_id = r0["id"]

        # Act - get the asset's tags
        response = client.get(f"/api/assets/{asset_id}/tags")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert isinstance(r, list)
        assert len(r) == 1
        r0 = r[0]
        assert r0["id"] == tag_id and r0["name"] == "new-tag"

        # Verify database persistence
        db_session.commit()
        from app.models import AssetTagORM, TagORM

        db_asset_tag = (
            db_session.query(AssetTagORM)
            .where(AssetTagORM.asset_id == asset_id)
            .where(AssetTagORM.tag_id == tag_id)
            .first()
        )
        assert db_asset_tag is not None

        db_tag = db_session.query(TagORM).filter_by(id=tag_id).first()
        assert db_tag is not None
        assert db_tag.name == "new-tag"

    def test_tag_non_existent_title(
        self,
        client: TestClient,
        tag_repository: TagRepository,
    ) -> None:
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))

        # Act
        response = client.put("/api/titles/999/tags", json={"tag_ids": [t.id]})

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_tag_non_existent_title_by_tag_name(
        self,
        client: TestClient,
        tag_repository: TagRepository,
    ) -> None:
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))

        # Act
        response = client.post("/api/titles/999/tags", json={"tag_names": [t.name]})

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_untag_non_existent_title(
        self,
        client: TestClient,
        tag_repository: TagRepository,
    ) -> None:
        # Arrange
        t = tag_repository.create(TagCreateInternal(name="test-tag"))

        # Act
        response = client.delete(f"/api/titles/999/tags/{t.id}")

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_tag_asset_by_name_without_creation(
        self,
        client: TestClient,
        tag_repository: TagRepository,
        media_repository: MediaRepository,
        db_session: Session,
    ) -> None:
        """Test adding a tag to an asset by name"""
        # Arrange
        a = media_repository.create(
            AssetCreateInternal(
                **AssetReadFactory().model_dump(exclude={"id", "created_at"})  # type: ignore
            )
        )
        asset_id = a.id

        # Act - tag the asset
        response = client.post(
            f"/api/assets/{asset_id}/tags",
            json={"tag_names": ["NEW-tag", "new-tag"], "auto_tag_create": False},
        )

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert not r["added_tags"]
        assert isinstance(r["tagging_errors"], list)
        tagging_errors = r["tagging_errors"]
        assert len(tagging_errors) == 1
        r0 = tagging_errors[0]
        assert "'new-tag'" in r0  # note that the name is lowercased by the API
