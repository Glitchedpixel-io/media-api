# tests/contracts/repositories/test_title_reference_repository_contract.py
import pytest

from app.repositories.errors import (
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
)
from app.schemas import TitleReferenceCreateInternal, TitleReferenceUpdateInternal
from tests.contracts.repositories.bundles_impl import (
    make_bundle,
    title_reference_bundler,
)
from tests.factories import TitleCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, title_reference_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle):

    # Need a parent title for FK
    title = bundle.titles.create(TitleCreateFactory())

    tr = TitleReferenceCreateInternal.model_validate(
        {
            "title_id": title.id,
            "reference_type": "review",
            "reference_url": "https://example.com/review/123",
            "label": "IGN Review",
        }
    )
    out = bundle.title_references.create(tr)
    assert out.id is not None
    assert bundle.title_references.exists(out.id) is True
    fetched = bundle.title_references.get(out.id)
    assert fetched is not None
    assert fetched.reference_url == tr.reference_url
    assert fetched.title_id == title.id


@pytest.mark.contract
def test_create_with_invalid_title_id(bundle):

    with pytest.raises(ForeignKeyViolation):
        bundle.title_references.create(
            TitleReferenceCreateInternal.model_validate(
                {
                    "title_id": 0,
                    "reference_type": "review",
                    "reference_url": "https://example.com/invalid",
                    "label": None,
                }
            )
        )


@pytest.mark.contract
def test_list_by_title(bundle):

    t1 = bundle.titles.create(TitleCreateFactory())
    t2 = bundle.titles.create(TitleCreateFactory())

    for i in range(5):
        bundle.title_references.create(
            TitleReferenceCreateInternal.model_validate(
                {
                    "title_id": t1.id,
                    "reference_type": "metadata",
                    "reference_url": f"https://example.com/t1/meta/{i}",
                }
            )
        )
    for i in range(7):
        bundle.title_references.create(
            TitleReferenceCreateInternal.model_validate(
                {
                    "title_id": t2.id,
                    "reference_type": "article",
                    "reference_url": f"https://example.com/t2/article/{i}",
                }
            )
        )

    refs_1 = bundle.title_references.list_title_references(t1.id)
    refs_2 = bundle.title_references.list_title_references(t2.id)
    assert len(refs_1) == 5
    assert len(refs_2) == 7
    assert len(bundle.title_references.list_title_references(0)) == 0


@pytest.mark.contract
def test_allowed_and_partial_updates(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    tr = bundle.title_references.create(
        TitleReferenceCreateInternal.model_validate(
            {
                "title_id": title.id,
                "reference_type": "review",
                "reference_url": "https://ex.com/rev",
                "label": "Initial",
            }
        )
    )

    # Update URL and label only
    updated = bundle.title_references.update(
        tr.id,
        TitleReferenceUpdateInternal.model_validate(
            {"reference_url": "https://ex.com/rev2", "label": "Updated"}
        ),
    )
    assert updated is not None
    assert updated.reference_url == "https://ex.com/rev2"
    assert updated.label == "Updated"
    assert updated.reference_type == tr.reference_type

    # Change type as well
    updated = bundle.title_references.update(
        tr.id,
        TitleReferenceUpdateInternal.model_validate({"reference_type": "article"}),
    )
    assert updated.reference_type == "article"

    # Remove the label
    updated = bundle.title_references.update(
        tr.id,
        TitleReferenceUpdateInternal.model_validate({"label": None}),
    )
    assert updated.label is None


@pytest.mark.contract
def test_invalid_updates_and_not_found(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    tr = bundle.title_references.create(
        TitleReferenceCreateInternal.model_validate(
            {
                "title_id": title.id,
                "reference_type": "review",
                "reference_url": "https://ex.com/rev",
            }
        )
    )

    # Required fields cannot be null
    with pytest.raises(NotNullViolation):
        bundle.title_references.update(
            tr.id, TitleReferenceUpdateInternal.model_validate({"reference_type": None})
        )
    with pytest.raises(NotNullViolation):
        bundle.title_references.update(
            tr.id, TitleReferenceUpdateInternal.model_validate({"reference_url": None})
        )
    with pytest.raises(NotNullViolation):
        bundle.title_references.update(
            tr.id, TitleReferenceUpdateInternal.model_validate({"title_id": None})
        )

    # Update to non-existent title id -> FK violation
    with pytest.raises(ForeignKeyViolation):
        bundle.title_references.update(
            tr.id, TitleReferenceUpdateInternal.model_validate({"title_id": 999999})
        )

    # NotFound when updating missing record
    with pytest.raises(NotFoundError):
        bundle.title_references.update(
            0, TitleReferenceUpdateInternal.model_validate({"label": "X"})
        )
