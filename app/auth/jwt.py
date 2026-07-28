# app/auth/jwt.py
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
import logfire

import requests
from fastapi import HTTPException, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from jose.utils import base64url_decode

from app.config import get_auth_config


@dataclass
class Principal:
    sub: str
    email: str | None
    preferred_username: str | None
    azp: str | None
    roles: list[str]
    token: str
    token_payload: dict[str, Any]


def _issuer() -> str:  # pragma: no cover
    iss: str = get_auth_config().oidc_issuer
    if not iss:
        raise RuntimeError("OIDC_ISSUER is not configured")
    return iss.rstrip("/")


def _jwks_url() -> str:  # pragma: no cover
    jwks = get_auth_config().oidc_jwks_url
    if jwks:
        return str(jwks)
    return f"{_issuer()}/protocol/openid-connect/certs"


def _audience() -> str:  # pragma: no cover
    aud = get_auth_config().oidc_audience
    if not aud:
        raise RuntimeError("OIDC_AUDIENCE is not configured")
    return str(aud)


def _algorithms() -> list[str]:  # pragma: no cover
    algs = get_auth_config().oidc_algorithms
    if isinstance(algs, str):
        return [a.strip() for a in algs.split(",") if a.strip()]
    return list(algs)


@lru_cache(maxsize=1)
def get_cached_jwks() -> dict[str, Any]:  # pragma: no cover
    url = _jwks_url()
    resp = requests.get(url, timeout=5)
    if resp.status_code != 200:
        logfire.error("Failed to fetch JWKS", extra={"status": resp.status_code, "url": url})
        raise HTTPException(status_code=500, detail="Failed to fetch JWKS")
    return resp.json()  # type: ignore


def _find_key_for_kid(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:  # pragma: no cover
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key  # type: ignore
    return None


def verify_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    # Decode header to select JWK by kid
    try:
        header_segment = token.split(".")[0]
        header_bytes = base64url_decode(header_segment.encode())
        header = json.loads(header_bytes.decode("utf-8"))
        kid = header.get("kid")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token header") from e

    jwks = get_cached_jwks()
    key = _find_key_for_kid(jwks, kid) if kid else None
    if not key:
        # Refresh cache once and retry
        get_cached_jwks.cache_clear()
        jwks = get_cached_jwks()
        key = _find_key_for_kid(jwks, kid) if kid else None
        if not key:
            raise HTTPException(status_code=401, detail="Signing key not found")

    issuer = _issuer()
    audience = _audience()
    algorithms = _algorithms()

    # First attempt: normal decode with audience verification
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options={
                "verify_aud": True,
                "verify_iss": True,
            },
        )
        return header, payload
    except Exception:
        # If audience verification failed, retry with custom azp fallback logic
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=algorithms,
                issuer=issuer,
                options={
                    # We'll manually check audience/azp below
                    "verify_aud": False,
                    "verify_iss": True,
                },
            )
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid token, {e}") from e

        # Custom audience/azp check: accept if aud matches OR azp matches required audience
        aud_claim = payload.get("aud")
        azp_claim = payload.get("azp")

        def _aud_matches(aud_value: Any, required: str) -> bool:
            if aud_value is None:
                return False
            if isinstance(aud_value, str):
                return aud_value == required
            if isinstance(aud_value, list):
                return required in aud_value
            return False

        if _aud_matches(aud_claim, audience) or (
            isinstance(azp_claim, str) and azp_claim == audience
        ):
            return header, payload

        # Neither aud nor azp contained required audience; surface original audience error if any
        raise HTTPException(
            status_code=401,
            detail="Invalid token, audience/azp does not include required audience",
        )


def _extract_roles(payload: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    realm_roles = (payload.get("realm_access") or {}).get("roles") or []
    if isinstance(realm_roles, list):
        roles.extend([str(r) for r in realm_roles])
    resource_access = payload.get("resource_access") or {}
    if isinstance(resource_access, dict):
        for _client, data in resource_access.items():
            cr = (data or {}).get("roles") or []
            if isinstance(cr, list):
                roles.extend([str(r) for r in cr])
    # De-duplicate
    return sorted(set(roles))


http_bearer = HTTPBearer(auto_error=False)

# Returned by get_current_user when AUTH_DISABLED=true (local dev only —
# refused at settings load time when APP_ENV=production).
_DEV_PRINCIPAL = Principal(
    sub="dev-user",
    email="dev@localhost",
    preferred_username="dev",
    azp="dev",
    roles=["admin"],
    token="auth-disabled",
    token_payload={},
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> Principal:
    if get_auth_config().disabled:
        return _DEV_PRINCIPAL

    if not creds or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = creds.credentials

    _header, payload = verify_jwt(token)

    # Basic time validation (jose already checks exp/nbf)
    now = int(time.time())
    if int(payload.get("exp", now)) < now:
        raise HTTPException(status_code=401, detail="Token expired")

    principal = Principal(
        sub=str(payload.get("sub")),
        email=payload.get("email"),
        preferred_username=payload.get("preferred_username"),
        azp=payload.get("azp"),
        roles=_extract_roles(payload),
        token=token,
        token_payload=payload,
    )
    return principal


def require_roles(required: list[str]):  # type: ignore
    required_set = set(required)

    def _dep(user: Principal = Depends(get_current_user)) -> Principal:
        if not required_set.issubset(set(user.roles)):
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return _dep
