"""Token verification in ``app/auth/jwt.py``, independent of the JWT library behind it.

Tokens are signed here with ``cryptography`` directly rather than with any JWT library,
so these tests describe what the API accepts and refuses, not how one library does it.
They hold against whichever library ``verify_jwt`` is built on.
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import jwt as jwt_module
from app.config.schema import AuthConfig

pytestmark = pytest.mark.unit

ISSUER = "https://idp.example.test/realms/media"
AUDIENCE = "media-api"
KID = "key-1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _uint(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _uint(numbers.n),
        "e": _uint(numbers.e),
    }


def _sign_rs256(key: rsa.RSAPrivateKey, signing_input: bytes) -> bytes:
    return key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())


def _token(
    key: rsa.RSAPrivateKey,
    claims: dict[str, Any] | None = None,
    *,
    header: dict[str, Any] | None = None,
    sign: Callable[[bytes], bytes] | None = None,
) -> str:
    """Build a compact JWT.

    Args:
        key (rsa.RSAPrivateKey): Signs with RS256 unless ``sign`` is given.
        claims (dict[str, Any] | None): Overrides of the default, valid claims;
            a value of ``None`` removes that claim.
        header (dict[str, Any] | None): The header, RS256 with ``KID`` by default.
        sign (Callable[[bytes], bytes] | None): Produces the signature from the
            signing input instead of RS256 with ``key``.

    Returns:
        str: The token.
    """
    now = int(time.time())
    body: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "azp": "media-ui",
        "iat": now,
        "exp": now + 300,
        "email": "user@example.test",
        "preferred_username": "user",
        "realm_access": {"roles": ["viewer"]},
        "resource_access": {"media-api": {"roles": ["editor", "viewer"]}},
    }
    for name, value in (claims or {}).items():
        if value is None:
            body.pop(name, None)
        else:
            body[name] = value
    head = header if header is not None else {"alg": "RS256", "typ": "JWT", "kid": KID}
    signing_input = (
        f"{_b64url(json.dumps(head).encode())}.{_b64url(json.dumps(body).encode())}".encode()
    )
    signature = sign(signing_input) if sign else _sign_rs256(key, signing_input)
    return f"{signing_input.decode()}.{_b64url(signature)}"


class _Jwks:
    """Stands in for the cached JWKS fetch, counting refreshes."""

    def __init__(self, *keys: dict[str, str]) -> None:
        self.keys = list(keys)
        self.refreshes = 0

    def __call__(self) -> dict[str, Any]:
        return {"keys": self.keys}

    def cache_clear(self) -> None:
        self.refreshes += 1


@pytest.fixture
def jwks(monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey) -> _Jwks:
    stub = _Jwks(_jwk(signing_key))
    monkeypatch.setattr(jwt_module, "get_cached_jwks", stub)
    monkeypatch.setattr(
        jwt_module,
        "get_auth_config",
        lambda: AuthConfig(oidc_issuer=ISSUER, oidc_audience=AUDIENCE, oidc_algorithms="RS256"),
    )
    return stub


def _refused(token: str) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        jwt_module.verify_jwt(token)
    assert caught.value.status_code == 401
    return caught.value


class TestAccepted:
    def test_a_valid_token(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        header, payload = jwt_module.verify_jwt(_token(signing_key))
        assert header["kid"] == KID
        assert payload["sub"] == "user-1"

    def test_audience_in_a_list(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        token = _token(signing_key, {"aud": ["account", AUDIENCE]})
        assert jwt_module.verify_jwt(token)[1]["sub"] == "user-1"

    def test_audience_named_only_by_azp(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        token = _token(signing_key, {"aud": "account", "azp": AUDIENCE})
        assert jwt_module.verify_jwt(token)[1]["azp"] == AUDIENCE

    def test_no_audience_claim_but_azp_matches(
        self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey
    ) -> None:
        token = _token(signing_key, {"aud": None, "azp": AUDIENCE})
        assert jwt_module.verify_jwt(token)[1]["azp"] == AUDIENCE

    def test_the_key_is_chosen_by_kid(
        self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey
    ) -> None:
        jwks.keys.append(_jwk(other_key, kid="key-2"))
        token = _token(other_key, header={"alg": "RS256", "typ": "JWT", "kid": "key-2"})
        assert jwt_module.verify_jwt(token)[1]["sub"] == "user-1"
        assert jwks.refreshes == 0


class TestRefused:
    def test_wrong_audience_and_azp(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        error = _refused(_token(signing_key, {"aud": "account", "azp": "someone-else"}))
        assert "audience" in error.detail

    def test_wrong_issuer(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        _refused(_token(signing_key, {"iss": "https://elsewhere.test/realms/media"}))

    def test_wrong_issuer_even_when_azp_matches(
        self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey
    ) -> None:
        _refused(
            _token(
                signing_key,
                {"iss": "https://elsewhere.test/realms/media", "aud": "x", "azp": AUDIENCE},
            )
        )

    def test_expired(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        now = int(time.time())
        _refused(_token(signing_key, {"iat": now - 600, "exp": now - 300}))

    def test_expired_even_when_azp_matches(
        self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey
    ) -> None:
        now = int(time.time())
        _refused(
            _token(signing_key, {"iat": now - 600, "exp": now - 300, "aud": "x", "azp": AUDIENCE})
        )

    def test_not_yet_valid(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        _refused(_token(signing_key, {"nbf": int(time.time()) + 300}))

    def test_signed_by_another_key(
        self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey, other_key: rsa.RSAPrivateKey
    ) -> None:
        _refused(_token(other_key))

    def test_a_tampered_payload(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        head, _body, signature = _token(signing_key).split(".")
        forged = _b64url(json.dumps({"iss": ISSUER, "aud": AUDIENCE, "sub": "admin"}).encode())
        _refused(f"{head}.{forged}.{signature}")

    def test_unknown_kid_after_one_refresh(
        self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey
    ) -> None:
        token = _token(signing_key, header={"alg": "RS256", "typ": "JWT", "kid": "missing"})
        error = _refused(token)
        assert error.detail == "Signing key not found"
        assert jwks.refreshes == 1

    def test_no_kid(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        error = _refused(_token(signing_key, header={"alg": "RS256", "typ": "JWT"}))
        assert error.detail == "Signing key not found"

    @pytest.mark.parametrize("token", ["", "not-a-token", "%%%.e30.sig", "e30"])
    def test_a_malformed_token(self, jwks: _Jwks, token: str) -> None:
        _refused(token)

    def test_alg_none(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        token = _token(
            signing_key, header={"alg": "none", "typ": "JWT", "kid": KID}, sign=lambda _: b""
        )
        _refused(token)

    def test_hs256_signed_with_the_public_key(
        self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey
    ) -> None:
        """The classic algorithm-confusion forgery: HMAC keyed with the public key."""
        public_pem = signing_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        token = _token(
            signing_key,
            header={"alg": "HS256", "typ": "JWT", "kid": KID},
            sign=lambda data: hmac.new(public_pem, data, hashlib.sha256).digest(),
        )
        _refused(token)


class TestGetCurrentUser:
    def test_builds_the_principal(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        token = _token(signing_key)
        principal = jwt_module.get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
        assert principal.sub == "user-1"
        assert principal.email == "user@example.test"
        assert principal.preferred_username == "user"
        assert principal.azp == "media-ui"
        assert principal.roles == ["editor", "viewer"]
        assert principal.token == token

    def test_refuses_a_non_bearer_scheme(self, jwks: _Jwks, signing_key: rsa.RSAPrivateKey) -> None:
        with pytest.raises(HTTPException) as caught:
            jwt_module.get_current_user(
                HTTPAuthorizationCredentials(scheme="Basic", credentials=_token(signing_key))
            )
        assert caught.value.status_code == 401
