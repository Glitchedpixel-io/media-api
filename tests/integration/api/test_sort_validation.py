"""An unsupported sort key is a client error, not a server error.

`normalize_sort` raises `EnumViolation` for a field an endpoint does not allow. None
of the paginated list services translated it, so it escaped as a 500: a caller
asking for a sort that does not exist was told the server had failed, and the route
class could not help -- `QuietClientErrorRoute` only converts an `HTTPException`
that already carries the right status.

Found while removing `mtime` from the asset sort keys (#62), which turned a
previously-valid sort into an invalid one and so made the 500 reachable by a client
that had been working.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.api, pytest.mark.integration]

# Every endpoint returning a keyset page, and a sort field it does not allow.
PAGINATED = [
    "/api/assets",
    "/api/titles",
    "/api/tags",
    "/api/streams",
    "/api/transform_requests",
]


@pytest.mark.parametrize("path", PAGINATED)
def test_an_unsupported_sort_field_is_a_422(client: TestClient, path: str) -> None:
    response = client.get(f"{path}?limit=10&sort=not_a_field:desc")

    assert (
        response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    ), f"{path} returned {response.status_code} for an unsupported sort field"


@pytest.mark.parametrize("path", PAGINATED)
def test_an_invalid_sort_direction_is_a_422(client: TestClient, path: str) -> None:
    """The other half of what normalize_sort validates."""
    response = client.get(f"{path}?limit=10&sort=id:sideways")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize("path", PAGINATED)
def test_a_supported_sort_still_works(client: TestClient, path: str) -> None:
    """The translation must not swallow valid requests."""
    assert client.get(f"{path}?limit=10&sort=id:desc").status_code == HTTPStatus.OK
