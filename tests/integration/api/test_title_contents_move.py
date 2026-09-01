"""Integration tests for the atomic move route (issue #178).

Reparenting used to mean `DELETE` then `POST` -- two requests, no transaction. A failure
in the gap leaves the item attached to nothing, and the front end cannot tell that from a
successful move it failed to observe. This is the primary gesture of a drag-and-drop
tree, so it has to be one call.

The capability was already in the repository: `reorder` applies the parent to the row and
renumbers both lists, locking both parents in one `FOR UPDATE` statement, and
`uq_parent_position` is `DEFERRABLE INITIALLY DEFERRED` so the intermediate states of
that renumber are never checked. What did not exist was a route that reached it *safely*
-- the old path skipped both containment guards, which is #185. These tests pin the
route, the guards, and the discriminators a UI needs to tell the refusals apart.

`{destination_title_id}` is the destination here, and the edge's current parent
everywhere else in the file. That asymmetry is the reason this is a route of its own,
and `TestTheDestinationIsTheDestination` is what holds it.
"""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import MediaRepository, TitleRepository
from app.schemas import AssetCreateInternal
from tests.factories import AssetReadFactory, TitleReadFactory, get_title_internal


def _title(title_repository: TitleRepository) -> int:
    return title_repository.create(get_title_internal(TitleReadFactory())).id


def _asset(media_repository: MediaRepository) -> int:
    return media_repository.create(
        AssetCreateInternal(
            **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})
        )
    ).id


def _attach(client: TestClient, parent: int, child: int, membership: str = "intrinsic") -> int:
    response = client.post(
        f"/api/titles/{parent}/contents",
        json={"kind": "title", "child_title_id": child, "membership": membership},
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return response.json()["id"]


def _attach_asset(client: TestClient, parent: int, asset_id: int) -> int:
    response = client.post(
        f"/api/titles/{parent}/contents",
        json={"kind": "asset", "asset_id": asset_id},
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return response.json()["id"]


def _contents(client: TestClient, parent: int) -> list[dict]:
    response = client.get(f"/api/titles/{parent}/contents")
    assert response.status_code == HTTPStatus.OK, response.text
    return response.json()


def _move(client: TestClient, destination: int, edge: int, **params):
    return client.post(f"/api/titles/{destination}/contents/{edge}/move", params=params)


def _code(response) -> str:
    """The machine-readable discriminator a 409 carries."""
    detail = response.json()["detail"]
    assert isinstance(detail, list), detail
    return detail[0]["type"]


@pytest.mark.api
@pytest.mark.integration
class TestTheDestinationIsTheDestination:
    def test_a_move_reparents_the_edge_in_one_call(self, client, title_repository):
        source = _title(title_repository)
        destination = _title(title_repository)
        child = _title(title_repository)
        edge = _attach(client, source, child)

        response = _move(client, destination, edge)

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["parent_title_id"] == destination
        assert _contents(client, source) == []
        assert [row["id"] for row in _contents(client, destination)] == [edge]

    def test_the_edge_never_passes_through_being_attached_to_nothing(
        self, client, title_repository
    ):
        """The whole point. Every edge is under exactly one parent before and after --
        detach-then-attach has a window where it is under none, and that window is what
        a front end cannot distinguish from a move it failed to observe."""
        source = _title(title_repository)
        destination = _title(title_repository)
        edge = _attach(client, source, _title(title_repository))

        _move(client, destination, edge)

        both = _contents(client, source) + _contents(client, destination)
        assert [row["id"] for row in both] == [edge]

    def test_an_unknown_destination_is_404(self, client, title_repository):
        source = _title(title_repository)
        edge = _attach(client, source, _title(title_repository))

        response = _move(client, 2**31 - 1, edge)

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert [row["id"] for row in _contents(client, source)] == [edge]

    def test_an_unknown_edge_is_404(self, client, title_repository):
        destination = _title(title_repository)

        response = _move(client, destination, 2**31 - 1)

        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.api
@pytest.mark.integration
class TestPositionUnderTheNewParent:
    def test_it_appends_when_no_anchor_is_given(self, client, title_repository, media_repository):
        source = _title(title_repository)
        destination = _title(title_repository)
        sitting = [_attach_asset(client, destination, _asset(media_repository)) for _ in range(2)]
        edge = _attach(client, source, _title(title_repository))

        response = _move(client, destination, edge)

        assert response.status_code == HTTPStatus.OK, response.text
        assert [row["id"] for row in _contents(client, destination)] == [*sitting, edge]
        assert response.json()["position"] == 2

    def test_it_lands_on_an_anchor_when_one_is_given(
        self, client, title_repository, media_repository
    ):
        source = _title(title_repository)
        destination = _title(title_repository)
        sitting = [_attach_asset(client, destination, _asset(media_repository)) for _ in range(2)]
        edge = _attach(client, source, _title(title_repository))

        response = _move(client, destination, edge, position="start")

        assert response.status_code == HTTPStatus.OK, response.text
        assert [row["id"] for row in _contents(client, destination)] == [edge, *sitting]
        assert response.json()["position"] == 0

    def test_it_lands_before_a_named_sibling(self, client, title_repository, media_repository):
        source = _title(title_repository)
        destination = _title(title_repository)
        first = _attach_asset(client, destination, _asset(media_repository))
        second = _attach_asset(client, destination, _asset(media_repository))
        edge = _attach(client, source, _title(title_repository))

        response = _move(client, destination, edge, before_id=second)

        assert response.status_code == HTTPStatus.OK, response.text
        assert [row["id"] for row in _contents(client, destination)] == [first, edge, second]

    def test_the_source_list_closes_the_gap(self, client, title_repository, media_repository):
        """Contiguous positions have to stay contiguous on *both* sides."""
        source = _title(title_repository)
        destination = _title(title_repository)
        staying = [_attach_asset(client, source, _asset(media_repository)) for _ in range(3)]
        edge = staying.pop(0)

        _move(client, destination, edge)

        remaining = _contents(client, source)
        assert [row["id"] for row in remaining] == staying
        assert [row["position"] for row in remaining] == [0, 1]

    def test_the_old_position_is_not_carried_across(
        self, client, title_repository, media_repository
    ):
        """A position means "the nth entry in this list", so it cannot survive the move
        to a different list -- and if it did it would collide under uq_parent_position."""
        source = _title(title_repository)
        destination = _title(title_repository)
        for _ in range(4):
            _attach_asset(client, source, _asset(media_repository))
        edge = _attach(client, source, _title(title_repository))
        assert _contents(client, source)[-1]["position"] == 4

        response = _move(client, destination, edge)

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["position"] == 0


@pytest.mark.api
@pytest.mark.integration
class TestItRejectsWhatAnAttachWouldReject:
    """A move reaches exactly the states an insert can, so it applies the same guards.
    Skipping them is what made the old cross-parent path a defect (#185)."""

    def test_a_move_that_would_close_a_cycle_is_refused(self, client, title_repository):
        a = _title(title_repository)
        b = _title(title_repository)
        c = _title(title_repository)
        _attach(client, a, b)
        parked = _attach(client, c, a, membership="curated")

        response = _move(client, b, parked)

        assert response.status_code == HTTPStatus.CONFLICT, response.text
        assert _code(response) == "containment_cycle"
        # And nothing moved.
        assert [row["id"] for row in _contents(client, c)] == [parked]
        assert _contents(client, b) == []

    def test_a_move_onto_the_edges_own_child_is_refused(self, client, title_repository):
        parent = _title(title_repository)
        child = _title(title_repository)
        edge = _attach(client, parent, child)

        response = _move(client, child, edge)

        assert response.status_code == HTTPStatus.CONFLICT, response.text
        assert _code(response) == "containment_cycle"

    def test_moving_a_curated_edge_of_a_title_that_has_a_home_is_allowed(
        self, client, title_repository
    ):
        """Curated membership is unlimited -- appearing in many lists is the point of
        the distinction -- so a title that already lives somewhere can still be dragged
        between collections."""
        child = _title(title_repository)
        _attach(client, _title(title_repository), child)
        curated_edge = _attach(client, _title(title_repository), child, membership="curated")
        destination = _title(title_repository)

        response = _move(client, destination, curated_edge)

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["membership"] == "curated"
        assert response.json()["parent_title_id"] == destination

    def test_moving_an_edge_that_is_the_childs_only_home_does_not_conflict_with_itself(
        self, client, title_repository
    ):
        """The intrinsic guard has to exclude the row being moved, or every move of a
        home would collide with the home it already is.

        The opposite case -- a child with a *second* intrinsic edge to collide against
        -- is not constructible here, by either route: `uq_one_intrinsic_parent` is a
        partial unique index, so the state cannot exist to be moved into. That is why
        `_reject_second_intrinsic_parent` is defensive rather than load-bearing on this
        path; it earns its place against the producer that writes to this table without
        going through the service (#125), and the index is what actually holds the rule.
        """
        child = _title(title_repository)
        edge = _attach(client, _title(title_repository), child)
        destination = _title(title_repository)

        response = _move(client, destination, edge)

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["membership"] == "intrinsic"
        assert response.json()["parent_title_id"] == destination

    def test_a_cycle_409_carries_a_code_and_keeps_its_explanation(self, client, title_repository):
        """The DoD's fourth bullet, as far as it can be tested from outside.

        A drag-and-drop UI has to tell a refusal it should explain from one it should
        retry, and both are 409 -- so the discriminator has to be a field, not the
        prose. This covers the cycle code; `position_conflict` is asserted only in the
        unit test, because reaching it through the API needs a concurrent writer or rows
        that predate the service. The repository renumbers the whole list, so its own
        arithmetic cannot produce a collision.
        """
        a = _title(title_repository)
        b = _title(title_repository)
        _attach(client, a, b)
        parked = _attach(client, _title(title_repository), a, membership="curated")

        cycle = _move(client, b, parked)

        assert cycle.status_code == HTTPStatus.CONFLICT
        assert _code(cycle) == "containment_cycle"
        # The guard's message survives the recoding, so the explanation is still there
        # and the two halves cannot drift apart.
        assert "cycle" in cycle.json()["detail"][0]["msg"]
        assert str(a) in cycle.json()["detail"][0]["msg"]


@pytest.mark.api
@pytest.mark.integration
class TestIdempotency:
    def test_moving_to_the_parent_it_already_has_repositions_rather_than_erroring(
        self, client, title_repository, media_repository
    ):
        """A move onto the edge's own parent must not 409 against its own edge.

        It is not a no-op, and the test says so rather than picking an arrangement
        where the difference cannot be seen: with no anchor the edge appends, so a
        mid-list edge moved to its own parent lands at the end. That is the same rule
        every other destination follows -- position is reassigned, never carried -- and
        the destination happening to be the current parent does not exempt it.
        """
        parent = _title(title_repository)
        first_edge = _attach_asset(client, parent, _asset(media_repository))
        middle = _attach(client, parent, _title(title_repository))
        last = _attach_asset(client, parent, _asset(media_repository))

        response = _move(client, parent, middle)

        assert response.status_code == HTTPStatus.OK, response.text
        assert [row["id"] for row in _contents(client, parent)] == [first_edge, last, middle]

    def test_repeating_a_same_parent_move_converges(
        self, client, title_repository, media_repository
    ):
        """What a client retrying after a dropped connection does: the second identical
        request must leave the list exactly as the first did, not shuffle again."""
        parent = _title(title_repository)
        sitting = _attach_asset(client, parent, _asset(media_repository))
        edge = _attach(client, parent, _title(title_repository))

        first = _move(client, parent, edge, position="start")
        after_first = [row["id"] for row in _contents(client, parent)]
        second = _move(client, parent, edge, position="start")

        assert first.status_code == HTTPStatus.OK, first.text
        assert second.status_code == HTTPStatus.OK, second.text
        assert after_first == [edge, sitting]
        assert [row["id"] for row in _contents(client, parent)] == after_first

    def test_repeating_a_move_leaves_one_edge(self, client, title_repository):
        source = _title(title_repository)
        destination = _title(title_repository)
        edge = _attach(client, source, _title(title_repository))

        _move(client, destination, edge)
        again = _move(client, destination, edge)

        assert again.status_code == HTTPStatus.OK, again.text
        assert [row["id"] for row in _contents(client, destination)] == [edge]
        assert _contents(client, source) == []

    def test_an_asset_edge_moves_the_same_way(self, client, title_repository, media_repository):
        """Assets are leaves, so no cycle is possible -- but the list arithmetic on both
        sides is the same, and an asset edge is the common case a UI drags."""
        source = _title(title_repository)
        destination = _title(title_repository)
        edge = _attach_asset(client, source, _asset(media_repository))

        response = _move(client, destination, edge)

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["parent_title_id"] == destination
        assert _contents(client, source) == []
