# tests/integration/api/test_reorder_depends_on_deferral.py

"""`uq_parent_position` must stay deferrable, or reordering stops working (#180).

A move renumbers a list in place, and every intermediate state of that renumber has two
rows sharing a position. The constraint is `DEFERRABLE INITIALLY DEFERRED` so the
question is asked once, at commit, rather than after each row.

The spike measured what happens without it: replacing the constraint with a plain
`UNIQUE (parent_title_id, position)` makes **every** reorder shape fail with a 409 --
first-to-end, last-to-start and mid-to-mid alike. Reordering does not degrade, it stops.

These tests exist because nothing else would notice. `alembic check` compares models to
the database and does not report deferrability, so swapping the constraint passes CI's
drift gate with "No new upgrade operations detected"; and the suite builds its schema
from the models, so a migration that dropped deferrability without touching the model
would be invisible to both. This pins the model side. The migration side is unguarded --
see the comment on the constraint.
"""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.protocols import MediaRepository, TitleRepository
from app.schemas import AssetCreateInternal
from tests.factories import AssetReadFactory, TitleReadFactory, get_title_internal


def _list_of(client: TestClient, title_repository, media_repository, size: int):
    """A parent holding `size` asset entries, returned as (parent id, entry ids)."""
    parent = title_repository.create(get_title_internal(TitleReadFactory())).id
    entries = []
    for _ in range(size):
        asset = media_repository.create(
            AssetCreateInternal(
                **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})
            )
        ).id
        response = client.post(
            f"/api/titles/{parent}/contents", json={"kind": "asset", "asset_id": asset}
        )
        assert response.status_code == HTTPStatus.CREATED, response.text
        entries.append(response.json()["id"])
    return parent, entries


def _order(client: TestClient, parent: int) -> tuple[list[int], list[int]]:
    rows = client.get(f"/api/titles/{parent}/contents").json()
    return [row["id"] for row in rows], [row["position"] for row in rows]


@pytest.mark.integration
def test_uq_parent_position_is_deferrable(db_session: Session) -> None:
    """The schema attribute the reorder path depends on."""
    row = db_session.execute(
        text(
            "SELECT condeferrable, condeferred FROM pg_constraint "
            "WHERE conname = 'uq_parent_position'"
        )
    ).first()

    assert row is not None, "uq_parent_position is missing entirely"
    assert row[0] is True, "uq_parent_position must be DEFERRABLE -- see #180"
    assert row[1] is True, "uq_parent_position must be INITIALLY DEFERRED -- see #180"


@pytest.mark.api
@pytest.mark.integration
class TestReorderIsArbitraryNotSingleStep:
    """The other half of #180: one drag is one call, over any distance."""

    def test_the_first_entry_reaches_the_end_in_one_call(
        self, client, title_repository, media_repository
    ):
        parent, entries = _list_of(client, title_repository, media_repository, 20)

        response = client.patch(
            f"/api/titles/{parent}/contents/{entries[0]}/reorder", params={"position": "end"}
        )

        assert response.status_code == HTTPStatus.OK, response.text
        ids, positions = _order(client, parent)
        assert ids[-1] == entries[0]
        assert positions == list(range(20))

    def test_an_entry_lands_arbitrarily_deep_in_one_call(
        self, client, title_repository, media_repository
    ):
        parent, entries = _list_of(client, title_repository, media_repository, 20)

        response = client.patch(
            f"/api/titles/{parent}/contents/{entries[5]}/reorder",
            params={"after_id": entries[14]},
        )

        assert response.status_code == HTTPStatus.OK, response.text
        ids, positions = _order(client, parent)
        assert ids.index(entries[5]) == 14
        assert positions == list(range(20))

    def test_a_whole_list_reverses_without_a_set_level_endpoint(
        self, client, title_repository, media_repository
    ):
        """The documented client algorithm: walk the target order, sending each entry to
        the end in turn. N calls, no knowledge of intermediate positions, converges.

        Sized at 20, which is the largest curated collection in production -- curated
        edges are what the design makes reorderable, intrinsic structure is not
        user-editable.
        """
        parent, entries = _list_of(client, title_repository, media_repository, 20)
        before, _ = _order(client, parent)

        for entry in reversed(before):
            response = client.patch(
                f"/api/titles/{parent}/contents/{entry}/reorder", params={"position": "end"}
            )
            assert response.status_code == HTTPStatus.OK, response.text

        ids, positions = _order(client, parent)
        assert ids == list(reversed(before))
        assert positions == list(range(20))
