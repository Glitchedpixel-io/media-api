"""
FastAPI Integration Tests for Titles API

Tests the complete request flow for title management endpoints:
- Title CRUD operations
- Title-Asset relationships
- Title content and references
- Search functionality
- Full-stack integration from API to database
"""

from http import HTTPStatus
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DEFAULT_TITLE_TYPES
from app.repositories import TitleReferenceRepository
from app.repositories.protocols import (
    MediaRepository,
    TagRepository,
    TitleRepository,
)
from app.schemas import (
    AssetCreateInternal,
    ContentKind,
    TagCreateInternal,
    TitleContentInsert,
    TitleCreateInternal,
    TitleReferenceCreateInternal,
)
from tests.factories import (
    AssetReadFactory,
    TitleReadFactory,
    TitleReferenceReadFactory,
    get_title_internal,
    get_title_creation_json,
)


@pytest.mark.api
@pytest.mark.integration
class TestTitlesAPI:
    """Test the /titles API endpoints with full-stack integration."""

    def test_create_title_success(self, client: TestClient, db_session: Session):
        """Test successful title creation through full API stack."""
        # Arrange
        title = TitleReadFactory(release_year=2021, synopsis="A summary of the title")
        title_data = get_title_creation_json(title)  # type: ignore

        # Act
        response = client.post("/api/titles", json=title_data)

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()

        # Verify response structure and data
        assert response_data["name"] == title.name
        assert response_data["release_year"] == title.release_year
        assert response_data["title_type"] == title.title_type
        assert response_data["synopsis"] == title.synopsis
        assert "id" in response_data

        # Verify database persistence
        db_session.commit()
        from app.models import TitleORM

        db_title = db_session.query(TitleORM).filter_by(name=title.name).first()
        assert db_title is not None
        assert db_title.release_year == title.release_year
        assert db_title.title_type == title.title_type
        assert db_title.synopsis == title.synopsis

    def test_get_titles_with_data(
        self, client: TestClient, title_repository: TitleRepository
    ) -> None:
        """Test retrieving titles after creating some through repository."""
        # Arrange - create test data using factory
        title1 = TitleReadFactory()
        title2 = TitleReadFactory()
        title_repository.create(get_title_internal(title1))  # type: ignore
        title_repository.create(get_title_internal(title2))  # type: ignore

        # Act
        response = client.get("/api/titles")

        # Assert
        assert response.status_code == HTTPStatus.OK
        response_data = response.json()

        assert "items" in response_data
        assert "page" in response_data
        page = response_data["page"]
        assert "next" in page
        assert "prev" in page
        assert len(response_data["items"]) == 2

        # Verify title data is correctly serialized
        assert {item.name for item in [title1, title2]} == {
            t["name"] for t in response_data["items"]
        }

    def test_get_titles_with_inclusions(
        self,
        client: TestClient,
        title_repository: TitleRepository,
        title_reference_repository: TitleReferenceRepository,
        tag_repository: TagRepository,
    ) -> None:
        """Test retrieving titles, tags and references after creating some through repository."""
        # Arrange - create test data using factory
        t1 = title_repository.create(get_title_internal(TitleReadFactory(name="A")))
        t2 = title_repository.create(get_title_internal(TitleReadFactory(name="Z")))

        r1 = title_reference_repository.create(
            TitleReferenceCreateInternal(
                title_id=t1.id,
                **TitleReferenceReadFactory().model_dump(exclude={"id", "title_id"}),  # type: ignore
            )
        )
        r2 = title_reference_repository.create(
            TitleReferenceCreateInternal(
                title_id=t2.id,
                **TitleReferenceReadFactory().model_dump(exclude={"id", "title_id"}),  # type: ignore
            )
        )
        # create two tags
        o1 = tag_repository.create(TagCreateInternal(name="tag1"))
        o2 = tag_repository.create(TagCreateInternal(name="tag2"))
        # apply differently to the titles
        tag_repository.add_title_tags(t1.id, [o1.id])
        tag_repository.add_title_tags(t2.id, [o1.id, o2.id])

        # Act
        response_plain = client.get("/api/titles")
        response_references = client.get("/api/titles?include=references")
        response_tags = client.get("/api/titles?include=tags")
        response_both = client.get("/api/titles?include=references,tags&sort=name:desc&page_size=2")

        # Assert - plain response, should not include tags or references
        assert response_plain.status_code == HTTPStatus.OK
        r = response_plain.json()

        assert r and r["items"] and len(r["items"]) == 2
        items = r["items"]
        assert items[0]["name"] == t1.name and not items[0]["references"] and not items[0]["tags"]
        assert items[1]["name"] == t2.name and not items[1]["references"] and not items[1]["tags"]

        # Assert - response with references, should include references but not tags
        assert response_references.status_code == HTTPStatus.OK
        r = response_references.json()

        assert r and r["items"] and len(r["items"]) == 2
        items = r["items"]
        assert (
            items[0]["name"] == t1.name
            and len(items[0]["references"]) == 1
            and items[0]["references"][0]["id"] == r1.id
            and not items[0]["tags"]
        )
        assert (
            items[1]["name"] == t2.name
            and len(items[1]["references"]) == 1
            and items[1]["references"][0]["id"] == r2.id
            and not items[1]["tags"]
        )

        # Assert - response with tags, should include tag but not references
        assert response_tags.status_code == 200
        r = response_tags.json()

        assert r and r["items"] and len(r["items"]) == 2
        items = r["items"]
        assert (
            items[0]["name"] == t1.name
            and len(items[0]["tags"]) == 1
            and items[0]["tags"][0]["id"] == o1.id
            and not items[0]["references"]
        )
        assert (
            items[1]["name"] == t2.name
            and len(items[1]["tags"]) == 2
            and {tag["id"] for tag in items[1]["tags"]} == {r1.id, r2.id}
            and not items[1]["references"]
        )

        # Assert - response should have both tags and references
        assert response_both.status_code == 200
        r = response_both.json()

        assert r and r["items"] and len(r["items"]) == 2
        items = r["items"]
        assert (
            items[1]["name"] == t1.name
            and len(items[1]["tags"]) == 1
            and items[1]["tags"][0]["id"] == o1.id
            and len(items[1]["references"]) == 1
            and items[1]["references"][0]["id"] == r1.id
        )
        assert (
            items[0]["name"] == t2.name
            and len(items[0]["tags"]) == 2
            and {tag["id"] for tag in items[0]["tags"]} == {r1.id, r2.id}
            and len(items[0]["references"]) == 1
            and items[0]["references"][0]["id"] == r2.id
        )

    def test_add_asset_to_title(
        self,
        client: TestClient,
        db_session: Session,
        title_repository: TitleRepository,
        media_repository: MediaRepository,
    ):
        """Test adding an asset to a title."""
        # Arrange
        title = TitleReadFactory()
        created_title = title_repository.create(get_title_internal(title))
        title_id = created_title.id

        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at"}))
        )
        asset_id = created_asset.id

        content_item = TitleContentInsert(kind=ContentKind.asset, asset_id=asset_id)

        # Act
        response = client.post(
            f"/api/titles/{title_id}/contents",
            json=content_item.model_dump(mode="json"),
        )

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        r = response.json()
        assert "id" in r and r["id"] > 0
        assert "kind" in r and r["kind"] == ContentKind.asset.value
        assert "asset_id" in r and r["asset_id"] == asset_id
        assert "child_title_id" in r and r["child_title_id"] is None
        assert "label" in r and r["label"] is None
        assert "order_key" in r and r["order_key"] is not None

        # Verify database persistence
        db_session.commit()
        from app.models import TitleContentORM

        content_record = db_session.query(TitleContentORM).filter_by(id=r["id"]).first()
        assert (
            content_record
            and content_record.parent_title_id == title_id
            and content_record.order_key == r["order_key"]
        )

    @pytest.mark.parametrize("title_type", [code for code, _ in DEFAULT_TITLE_TYPES])
    def test_title_types_validation(self, client: TestClient, title_type: str):
        """Test that different title types are properly validated."""
        # Arrange
        title_data = {
            "name": f"Test {title_type}",
            "release_year": 2023,
            "title_type": title_type,
        }

        # Act
        response = client.post("/api/titles", json=title_data)

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["title_type"] == title_type
