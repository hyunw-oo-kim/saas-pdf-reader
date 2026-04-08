"""Auth API 엔드포인트 단위 테스트.

POST /api/auth/callback  — OIDC 콜백 처리 + JWT 발급
POST /api/auth/refresh   — 리프레시 토큰으로 새 액세스 토큰 발급
POST /api/auth/logout    — 세션 종료
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from jose import jwt

from app.config import settings
from app.routers.auth import (
    _INTERNAL_ALGORITHM,
    _create_access_token,
    _create_refresh_token,
    _decode_internal_token,
    _extract_user_claims,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_internal_claims(**overrides) -> dict:
    """테스트용 내부 JWT claims."""
    base = {
        "sub": "user-123",
        "tenant_id": "tenant-abc",
        "email": "test@example.com",
        "role": "editor",
    }
    base.update(overrides)
    return base


def _make_idp_id_token(claims: dict) -> str:
    """테스트용 IdP id_token (서명 없이 HS256으로 생성)."""
    return jwt.encode(claims, "fake-secret", algorithm="HS256")


# ---------------------------------------------------------------------------
# Internal JWT helper tests
# ---------------------------------------------------------------------------

class TestCreateAccessToken:
    def test_creates_valid_token(self):
        claims = _make_internal_claims()
        token, expires_in = _create_access_token(claims)

        assert isinstance(token, str)
        assert expires_in == settings.jwt_access_token_expire_minutes * 60

        decoded = jwt.decode(token, settings.jwt_secret_key, algorithms=[_INTERNAL_ALGORITHM])
        assert decoded["sub"] == "user-123"
        assert decoded["tenant_id"] == "tenant-abc"
        assert decoded["type"] == "access"
        assert "jti" in decoded
        assert "exp" in decoded
        assert "iat" in decoded

    def test_token_contains_all_claims(self):
        claims = _make_internal_claims(role="admin")
        token, _ = _create_access_token(claims)
        decoded = jwt.decode(token, settings.jwt_secret_key, algorithms=[_INTERNAL_ALGORITHM])
        assert decoded["role"] == "admin"


class TestCreateRefreshToken:
    def test_creates_valid_refresh_token(self):
        claims = _make_internal_claims()
        token = _create_refresh_token(claims)

        decoded = jwt.decode(token, settings.jwt_secret_key, algorithms=[_INTERNAL_ALGORITHM])
        assert decoded["sub"] == "user-123"
        assert decoded["type"] == "refresh"
        assert "jti" in decoded

    def test_refresh_token_has_longer_expiry(self):
        claims = _make_internal_claims()
        access_token, _ = _create_access_token(claims)
        refresh_token = _create_refresh_token(claims)

        access_decoded = jwt.decode(access_token, settings.jwt_secret_key, algorithms=[_INTERNAL_ALGORITHM])
        refresh_decoded = jwt.decode(refresh_token, settings.jwt_secret_key, algorithms=[_INTERNAL_ALGORITHM])

        assert refresh_decoded["exp"] > access_decoded["exp"]


class TestDecodeInternalToken:
    def test_decodes_valid_token(self):
        claims = _make_internal_claims()
        token, _ = _create_access_token(claims)
        decoded = _decode_internal_token(token)
        assert decoded["sub"] == "user-123"

    def test_raises_on_expired_token(self):
        from jose import JWTError

        payload = {
            **_make_internal_claims(),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "jti": str(uuid4()),
            "type": "access",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=_INTERNAL_ALGORITHM)

        with pytest.raises(JWTError):
            _decode_internal_token(token)

    def test_raises_on_invalid_token(self):
        from jose import JWTError

        with pytest.raises(JWTError):
            _decode_internal_token("not.a.valid.token")


# ---------------------------------------------------------------------------
# _extract_user_claims tests
# ---------------------------------------------------------------------------

class TestExtractUserClaims:
    def test_auth0_claims(self):
        id_token = _make_idp_id_token({
            "sub": "auth0|user-1",
            "tenant_id": "tenant-1",
            "email": "user@example.com",
            "role": "viewer",
        })
        idp_response = {"id_token": id_token}
        claims = _extract_user_claims(idp_response)

        assert claims["sub"] == "auth0|user-1"
        assert claims["tenant_id"] == "tenant-1"
        assert claims["email"] == "user@example.com"
        assert claims["role"] == "viewer"

    def test_default_role_when_missing(self):
        id_token = _make_idp_id_token({
            "sub": "user-no-role",
            "tenant_id": "t-x",
            "email": "norole@example.com",
        })
        idp_response = {"id_token": id_token}
        claims = _extract_user_claims(idp_response)
        assert claims["role"] == "editor"

    def test_default_tenant_when_missing(self):
        """tenant_id가 없으면 기본 UUID를 사용한다."""
        id_token = _make_idp_id_token({
            "sub": "user-no-tenant",
            "email": "notenant@example.com",
        })
        idp_response = {"id_token": id_token}
        claims = _extract_user_claims(idp_response)
        assert claims["tenant_id"] == "00000000-0000-0000-0000-000000000001"

    def test_missing_id_token_uses_defaults(self):
        """id_token이 없으면 빈 sub와 기본값을 반환한다."""
        claims = _extract_user_claims({})
        assert claims["sub"] == ""
        assert claims["tenant_id"] == "00000000-0000-0000-0000-000000000001"
        assert claims["role"] == "editor"

    def test_invalid_id_token_uses_defaults(self):
        """유효하지 않은 id_token이면 기본값을 반환한다."""
        claims = _extract_user_claims({"id_token": "garbage"})
        assert claims["sub"] == ""
        assert claims["tenant_id"] == "00000000-0000-0000-0000-000000000001"

    def test_auth0_namespace_claims(self):
        """Auth0 custom namespace claim을 인식한다."""
        id_token = _make_idp_id_token({
            "sub": "auth0|ns-user",
            "https://pdf-reader/tenant_id": "ns-tenant",
            "https://pdf-reader/role": "admin",
            "email": "ns@example.com",
        })
        idp_response = {"id_token": id_token}
        claims = _extract_user_claims(idp_response)
        assert claims["tenant_id"] == "ns-tenant"
        assert claims["role"] == "admin"

    def test_userinfo_supplements_claims(self):
        """userinfo로 id_token에 없는 정보를 보충한다."""
        id_token = _make_idp_id_token({
            "sub": "auth0|user-2",
        })
        idp_response = {"id_token": id_token}
        userinfo = {"email": "from-userinfo@example.com"}
        claims = _extract_user_claims(idp_response, userinfo)
        assert claims["email"] == "from-userinfo@example.com"


# ---------------------------------------------------------------------------
# Endpoint integration tests (using FastAPI TestClient)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient
from app.main import app


class TestAuthCallbackEndpoint:
    """POST /api/auth/callback 테스트."""

    def test_callback_auth0_success(self):
        """Auth0 콜백 성공 시 JWT 토큰을 발급한다."""
        id_token = _make_idp_id_token({
            "sub": "auth0|sub-1",
            "tenant_id": "auth0-tenant-1",
            "email": "user@auth0.com",
            "role": "admin",
        })
        mock_token_response = httpx.Response(
            200,
            json={
                "id_token": id_token,
                "access_token": "auth0-access-token",
            },
        )
        mock_userinfo_response = httpx.Response(
            200,
            json={
                "sub": "auth0|sub-1",
                "email": "user@auth0.com",
            },
        )

        with patch("app.routers.auth.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_token_response
            mock_instance.get.return_value = mock_userinfo_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            client = TestClient(app)
            resp = client.post("/api/auth/callback", json={
                "code": "auth0-code-456",
                "redirect_uri": "http://localhost:3000/login/callback",
            })

        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == settings.jwt_access_token_expire_minutes * 60

        decoded = jwt.decode(
            body["access_token"],
            settings.jwt_secret_key,
            algorithms=[_INTERNAL_ALGORITHM],
        )
        assert decoded["sub"] == "auth0|sub-1"
        assert decoded["tenant_id"] == "auth0-tenant-1"
        assert decoded["type"] == "access"

    def test_callback_idp_failure_returns_401(self):
        """IdP 토큰 교환 실패 시 401을 반환한다."""
        mock_idp_response = httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "Code expired"},
            headers={"content-type": "application/json"},
        )

        with patch("app.routers.auth.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_idp_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            client = TestClient(app)
            resp = client.post("/api/auth/callback", json={
                "code": "expired-code",
                "redirect_uri": "http://localhost:3000/login/callback",
            })

        assert resp.status_code == 401

    def test_callback_missing_sub_returns_401(self):
        """id_token에 sub가 없으면 401을 반환한다."""
        id_token = _make_idp_id_token({
            "email": "nosub@example.com",
        })
        mock_token_response = httpx.Response(
            200,
            json={"id_token": id_token, "access_token": "at"},
        )
        mock_userinfo_response = httpx.Response(
            200,
            json={"email": "nosub@example.com"},
        )

        with patch("app.routers.auth.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_token_response
            mock_instance.get.return_value = mock_userinfo_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            client = TestClient(app)
            resp = client.post("/api/auth/callback", json={
                "code": "code-no-sub",
                "redirect_uri": "http://localhost:3000/login/callback",
            })

        assert resp.status_code == 401


class TestRefreshEndpoint:
    """POST /api/auth/refresh 테스트."""

    def test_refresh_success(self):
        """유효한 리프레시 토큰으로 새 액세스 토큰을 발급한다."""
        claims = _make_internal_claims()
        refresh = _create_refresh_token(claims)

        client = TestClient(app)
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": refresh,
        })

        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["expires_in"] == settings.jwt_access_token_expire_minutes * 60

        decoded = jwt.decode(
            body["access_token"],
            settings.jwt_secret_key,
            algorithms=[_INTERNAL_ALGORITHM],
        )
        assert decoded["sub"] == "user-123"
        assert decoded["tenant_id"] == "tenant-abc"
        assert decoded["type"] == "access"

    def test_refresh_with_access_token_returns_401(self):
        """액세스 토큰으로 리프레시를 시도하면 401을 반환한다."""
        claims = _make_internal_claims()
        access, _ = _create_access_token(claims)

        client = TestClient(app)
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": access,
        })

        assert resp.status_code == 401

    def test_refresh_with_expired_token_returns_401(self):
        """만료된 리프레시 토큰은 401을 반환한다."""
        payload = {
            **_make_internal_claims(),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(timezone.utc) - timedelta(days=8),
            "jti": str(uuid4()),
            "type": "refresh",
        }
        expired_refresh = jwt.encode(
            payload, settings.jwt_secret_key, algorithm=_INTERNAL_ALGORITHM
        )

        client = TestClient(app)
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": expired_refresh,
        })

        assert resp.status_code == 401

    def test_refresh_with_invalid_token_returns_401(self):
        """유효하지 않은 토큰은 401을 반환한다."""
        client = TestClient(app)
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": "invalid.token.here",
        })

        assert resp.status_code == 401


class TestLogoutEndpoint:
    """POST /api/auth/logout 테스트."""

    def test_logout_returns_success(self):
        """로그아웃은 성공 메시지를 반환한다."""
        client = TestClient(app)
        resp = client.post("/api/auth/logout")

        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "로그아웃되었습니다"
