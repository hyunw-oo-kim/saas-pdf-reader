"""Auth Middleware 단위 테스트.

JWT 검증, JWKS 캐싱, 사용자 정보 추출, 오류 처리를 테스트한다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio
from jose import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

from app.middleware.auth import (
    AuthMiddleware,
    AuthenticatedUser,
    JWKSCache,
    verify_jwt_token,
    _find_signing_key,
    _error_response,
)


# ---------------------------------------------------------------------------
# RSA key pair generation for testing
# ---------------------------------------------------------------------------

def _generate_rsa_key_pair():
    """테스트용 RSA 키 쌍을 생성한다."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return private_key


def _private_key_to_pem(private_key) -> str:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _public_key_to_jwk(private_key, kid: str = "test-kid") -> dict:
    """RSA 공개키를 JWK 형식으로 변환한다."""
    from jose.backends import RSAKey as JoseRSAKey

    public_key = private_key.public_key()
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jose_key = JoseRSAKey(pub_pem, "RS256")
    jwk_dict = jose_key.to_dict()
    jwk_dict["kid"] = kid
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "RS256"
    return jwk_dict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def rsa_key():
    """테스트용 RSA 키 쌍."""
    return _generate_rsa_key_pair()


@pytest.fixture
def jwk(rsa_key):
    """테스트용 JWK."""
    return _public_key_to_jwk(rsa_key, kid="test-kid-1")


@pytest.fixture
def jwks(jwk):
    """테스트용 JWKS."""
    return {"keys": [jwk]}


def _make_auth0_token(rsa_key, claims_override: dict | None = None, kid: str = "test-kid-1") -> str:
    """Auth0 형식의 테스트 JWT 토큰을 생성한다."""
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://dev-test.us.auth0.com/",
        "sub": "auth0|user-456",
        "aud": "test-client-id",
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "iat": int(now.timestamp()),
        "tenant_id": "tenant-xyz",
        "role": "admin",
        "email": "auth0@example.com",
    }
    if claims_override:
        claims.update(claims_override)

    pem = _private_key_to_pem(rsa_key)
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# JWKSCache tests
# ---------------------------------------------------------------------------

class TestJWKSCache:
    """JWKS 캐시 테스트."""

    @pytest.mark.asyncio
    async def test_cache_stores_and_returns_jwks(self, jwks):
        """JWKS를 캐시에 저장하고 반환한다."""
        cache = JWKSCache(ttl_seconds=3600)

        async def mock_get(url):
            resp = MagicMock()
            resp.json.return_value = jwks
            resp.raise_for_status.return_value = None
            return resp

        mock_client = MagicMock()
        mock_client.get = mock_get
        mock_client.is_closed = False
        cache._http_client = mock_client

        result = await cache.get_jwks("https://example.com/keys")
        assert result == jwks

        # Second call should use cache — we track by checking cache dict
        result2 = await cache.get_jwks("https://example.com/keys")
        assert result2 == jwks

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self, jwks):
        """TTL 이후 캐시가 만료된다."""
        call_count = 0

        async def mock_get(url):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = jwks
            resp.raise_for_status.return_value = None
            return resp

        cache = JWKSCache(ttl_seconds=1)
        mock_client = MagicMock()
        mock_client.get = mock_get
        mock_client.is_closed = False
        cache._http_client = mock_client

        await cache.get_jwks("https://example.com/keys")
        assert call_count == 1

        # Manually expire the cache
        uri = "https://example.com/keys"
        cached_data, _ = cache._cache[uri]
        cache._cache[uri] = (cached_data, time.monotonic() - 2)

        await cache.get_jwks("https://example.com/keys")
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_specific_uri(self, jwks):
        """특정 URI의 캐시를 무효화한다."""
        cache = JWKSCache(ttl_seconds=3600)
        cache._cache["https://a.com/keys"] = (jwks, time.monotonic())
        cache._cache["https://b.com/keys"] = (jwks, time.monotonic())

        cache.invalidate("https://a.com/keys")
        assert "https://a.com/keys" not in cache._cache
        assert "https://b.com/keys" in cache._cache

    @pytest.mark.asyncio
    async def test_invalidate_all(self, jwks):
        """모든 캐시를 무효화한다."""
        cache = JWKSCache(ttl_seconds=3600)
        cache._cache["https://a.com/keys"] = (jwks, time.monotonic())
        cache._cache["https://b.com/keys"] = (jwks, time.monotonic())

        cache.invalidate()
        assert len(cache._cache) == 0


# ---------------------------------------------------------------------------
# _find_signing_key tests
# ---------------------------------------------------------------------------

class TestFindSigningKey:
    def test_finds_matching_key(self, jwk):
        jwks = {"keys": [jwk]}
        result = _find_signing_key(jwks, "test-kid-1")
        assert result == jwk

    def test_returns_none_for_missing_kid(self, jwk):
        jwks = {"keys": [jwk]}
        result = _find_signing_key(jwks, "nonexistent-kid")
        assert result is None

    def test_returns_none_for_empty_keys(self):
        result = _find_signing_key({"keys": []}, "any-kid")
        assert result is None


# ---------------------------------------------------------------------------
# verify_jwt_token tests
# ---------------------------------------------------------------------------

class TestVerifyJwtToken:
    """JWT 토큰 검증 테스트."""

    @pytest.mark.asyncio
    async def test_verify_auth0_token(self, rsa_key, jwks):
        """Auth0 토큰을 성공적으로 검증한다."""
        token = _make_auth0_token(rsa_key)

        with patch("app.middleware.auth.settings") as mock_settings, \
             patch("app.middleware.auth.get_jwks_cache") as mock_cache_fn:
            mock_settings.auth0_domain = "dev-test.us.auth0.com"
            mock_settings.auth0_client_id = "test-client-id"
            mock_settings.auth0_audience = "test-client-id"

            mock_cache = AsyncMock()
            mock_cache.get_jwks.return_value = jwks
            mock_cache_fn.return_value = mock_cache

            user = await verify_jwt_token(token)

            assert user.user_id == "auth0|user-456"
            assert user.tenant_id == "tenant-xyz"
            assert user.role == "admin"
            assert user.email == "auth0@example.com"

    @pytest.mark.asyncio
    async def test_expired_token_raises(self, rsa_key, jwks):
        """만료된 토큰은 ExpiredSignatureError를 발생시킨다."""
        from jose import ExpiredSignatureError

        token = _make_auth0_token(rsa_key, {
            "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
        })

        with patch("app.middleware.auth.settings") as mock_settings, \
             patch("app.middleware.auth.get_jwks_cache") as mock_cache_fn:
            mock_settings.auth0_domain = "dev-test.us.auth0.com"
            mock_settings.auth0_client_id = "test-client-id"
            mock_settings.auth0_audience = "test-client-id"

            mock_cache = AsyncMock()
            mock_cache.get_jwks.return_value = jwks
            mock_cache_fn.return_value = mock_cache

            with pytest.raises(ExpiredSignatureError):
                await verify_jwt_token(token)

    @pytest.mark.asyncio
    async def test_invalid_token_raises(self, jwks):
        """유효하지 않은 토큰은 JWTError를 발생시킨다."""
        from jose import JWTError

        with pytest.raises(JWTError):
            await verify_jwt_token("not.a.valid.token")

    @pytest.mark.asyncio
    async def test_key_rotation_retry(self, rsa_key, jwks):
        """키가 캐시에 없으면 캐시를 무효화하고 재시도한다."""
        token = _make_auth0_token(rsa_key)

        with patch("app.middleware.auth.settings") as mock_settings, \
             patch("app.middleware.auth.get_jwks_cache") as mock_cache_fn:
            mock_settings.auth0_domain = "dev-test.us.auth0.com"
            mock_settings.auth0_client_id = "test-client-id"
            mock_settings.auth0_audience = "test-client-id"

            mock_cache = AsyncMock()
            # First call returns empty keys, second returns correct keys
            mock_cache.get_jwks.side_effect = [{"keys": []}, jwks]
            mock_cache_fn.return_value = mock_cache

            user = await verify_jwt_token(token)
            assert user.user_id == "auth0|user-456"
            assert mock_cache.invalidate.called

    @pytest.mark.asyncio
    async def test_default_role_when_missing(self, rsa_key, jwks):
        """role claim이 없으면 기본값 'viewer'를 사용한다."""
        token = _make_auth0_token(rsa_key, {"role": None})

        with patch("app.middleware.auth.settings") as mock_settings, \
             patch("app.middleware.auth.get_jwks_cache") as mock_cache_fn:
            mock_settings.auth0_domain = "dev-test.us.auth0.com"
            mock_settings.auth0_client_id = "test-client-id"
            mock_settings.auth0_audience = "test-client-id"

            mock_cache = AsyncMock()
            mock_cache.get_jwks.return_value = jwks
            mock_cache_fn.return_value = mock_cache

            user = await verify_jwt_token(token)
            assert user.role == "viewer"


# ---------------------------------------------------------------------------
# AuthMiddleware dispatch tests
# ---------------------------------------------------------------------------

class TestAuthMiddlewareDispatch:
    """AuthMiddleware의 dispatch 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_public_path_skips_auth(self):
        """공개 경로는 인증을 건너뛴다."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse

        async def homepage(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/health", homepage)])
        app.add_middleware(AuthMiddleware)

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.text == "ok"

    @pytest.mark.asyncio
    async def test_shared_link_prefix_skips_auth(self):
        """공유 링크 경로는 인증을 건너뛴다."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse

        async def shared(request):
            return PlainTextResponse("shared")

        app = Starlette(routes=[Route("/api/shared/{token}", shared)])
        app.add_middleware(AuthMiddleware)

        client = TestClient(app)
        resp = client.get("/api/shared/abc123")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_auth_header_returns_401(self):
        """Authorization 헤더가 없으면 401을 반환한다."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse

        async def protected(request):
            return PlainTextResponse("protected")

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(AuthMiddleware)

        client = TestClient(app)
        resp = client.get("/api/documents")
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] == "AUTH_REQUIRED"
        assert "인증이 필요합니다" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_bearer_format_returns_401(self):
        """잘못된 Bearer 형식은 401을 반환한다."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse

        async def protected(request):
            return PlainTextResponse("protected")

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(AuthMiddleware)

        client = TestClient(app)
        resp = client.get("/api/documents", headers={"Authorization": "Basic abc"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_returns_401_with_expired_code(self, rsa_key, jwks):
        """만료된 토큰은 TOKEN_EXPIRED 코드와 함께 401을 반환한다."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse

        token = _make_auth0_token(rsa_key, {
            "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
        })

        async def protected(request):
            return PlainTextResponse("protected")

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(AuthMiddleware)

        with patch("app.middleware.auth.settings") as mock_settings, \
             patch("app.middleware.auth.get_jwks_cache") as mock_cache_fn:
            mock_settings.auth0_domain = "dev-test.us.auth0.com"
            mock_settings.auth0_client_id = "test-client-id"
            mock_settings.auth0_audience = "test-client-id"

            mock_cache = AsyncMock()
            mock_cache.get_jwks.return_value = jwks
            mock_cache_fn.return_value = mock_cache

            client = TestClient(app)
            resp = client.get(
                "/api/documents",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"]["code"] == "TOKEN_EXPIRED"

    @pytest.mark.asyncio
    async def test_valid_token_injects_user(self, rsa_key, jwks):
        """유효한 토큰은 request.state.user에 사용자 정보를 주입한다."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse

        token = _make_auth0_token(rsa_key)

        async def protected(request):
            user = request.state.user
            return JSONResponse({
                "user_id": user.user_id,
                "tenant_id": user.tenant_id,
                "role": user.role,
                "email": user.email,
            })

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(AuthMiddleware)

        with patch("app.middleware.auth.settings") as mock_settings, \
             patch("app.middleware.auth.get_jwks_cache") as mock_cache_fn:
            mock_settings.auth0_domain = "dev-test.us.auth0.com"
            mock_settings.auth0_client_id = "test-client-id"
            mock_settings.auth0_audience = "test-client-id"

            mock_cache = AsyncMock()
            mock_cache.get_jwks.return_value = jwks
            mock_cache_fn.return_value = mock_cache

            client = TestClient(app)
            resp = client.get(
                "/api/documents",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["user_id"] == "auth0|user-456"
            assert body["tenant_id"] == "tenant-xyz"
            assert body["role"] == "admin"
            assert body["email"] == "auth0@example.com"


# ---------------------------------------------------------------------------
# Error response helper tests
# ---------------------------------------------------------------------------

class TestErrorResponse:
    def test_error_response_format(self):
        resp = _error_response(401, "test message", "TEST_CODE")
        assert resp.status_code == 401
        body = json.loads(resp.body)
        assert body["error"]["code"] == "TEST_CODE"
        assert body["error"]["message"] == "test message"
