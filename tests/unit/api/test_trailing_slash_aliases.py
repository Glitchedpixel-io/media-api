"""Both spellings of the collection paths are served, and neither is a back door.

The assets and titles collections are declared with a trailing slash; the other
fourteen collection routes are not. Starlette answered the unslashed form with a
307, which a preflighted cross-origin request may not follow with its Authorization
header intact.

The alias is copied from the canonical route, so what matters is that the copy is
faithful -- above all that it did not shed the router-level auth dependency.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.unit, pytest.mark.api]

COLLECTIONS = ["/api/assets", "/api/titles"]


@pytest.mark.parametrize("path", COLLECTIONS)
def test_both_spellings_are_served_without_a_redirect(client: TestClient, path: str) -> None:
    """The point of the change: no 307 either way."""
    unslashed = client.get(path, follow_redirects=False)
    slashed = client.get(f"{path}/", follow_redirects=False)

    assert unslashed.status_code == HTTPStatus.OK, f"{path} redirected or failed"
    assert slashed.status_code == HTTPStatus.OK


@pytest.mark.parametrize("path", COLLECTIONS)
def test_the_alias_is_not_an_unauthenticated_back_door(api_app: FastAPI, path: str) -> None:
    """The failure that would matter.

    The alias is built by copying `route.dependencies` from the canonical route,
    which is where `include_router(..., dependencies=[Depends(get_current_user)])`
    puts it. If that copy were ever dropped, the unslashed spelling would be an
    unauthenticated copy of an authenticated endpoint -- and every other test here
    would still pass, because the suite overrides auth.
    """
    for method in ("GET", "POST"):
        canonical = _route(api_app, f"{path}/", method)
        alias = _route(api_app, path, method)

        assert alias is not None, f"{method} {path} has no alias"
        assert [d.dependency for d in alias.dependencies] == [
            d.dependency for d in canonical.dependencies
        ], f"{method} {path} alias does not carry the canonical route's dependencies"


@pytest.mark.parametrize("path", COLLECTIONS)
def test_the_alias_stays_out_of_the_published_schema(api_app: FastAPI, path: str) -> None:
    """Generated clients must keep the path they already use."""
    spec = api_app.openapi()

    assert f"{path}/" in spec["paths"]
    assert path not in spec["paths"], "publishing the alias would regenerate every client"


@pytest.mark.parametrize("path", COLLECTIONS)
def test_the_alias_matches_the_canonical_route(api_app: FastAPI, path: str) -> None:
    """Response model, status code and route class are copied, not restated."""
    for method in ("GET", "POST"):
        canonical = _route(api_app, f"{path}/", method)
        alias = _route(api_app, path, method)

        assert alias is not None
        assert alias.endpoint is canonical.endpoint
        assert alias.response_model == canonical.response_model
        assert alias.status_code == canonical.status_code
        assert type(alias) is type(canonical), "the alias must keep QuietClientErrorRoute"


def _route(api_app: FastAPI, path: str, method: str) -> APIRoute | None:
    for route in api_app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    return None
