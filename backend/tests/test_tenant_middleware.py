"""Tenant Middleware 단위 테스트.

tenant_id 추출, RLS 세션 변수 설정, 403 반환 케이스를 테스트한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.auth import AuthenticatedUser
from app.middleware.tenant import TenantMiddleware, _error_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(
    tenant_id: str = "tenant-abc",
    user_id: str = "user-123",
    role: str = "editor",
    email: str = "test@example.com",
) -> AuthenticatedUser:
    """테스트용 AuthenticatedUser를 생성한다."""
    return AuthenticatedUser(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
    )


def _build_app_with_tenant_middleware(handler, path="/api/documents"):
    """TenantMiddleware만 적용된 테스트 앱을 생성한다.

    AuthMiddleware는 적용하지 않고, 대신 handler에서 직접
    request.state.user를 설정하거나 설정하지 않는 방식으로 테스트한다.
    """
    app = Starlette(routes=[Route(path, handler)])
    app.add_middleware(TenantMiddleware)
    return app


# ---------------------------------------------------------------------------
# _error_response tests
# ---------------------------------------------------------------------------

class TestErrorResponse:
    def test_error_response_format(self):
        resp = _error_response(403, "접근 권한이 없습니다", "TENANT_REQUIRED")
        assert resp.status_code == 403
        body = json.loads(resp.body)
        assert body["error"]["code"] == "TENANT_REQUIRED"
        assert body["error"]["message"] == "접근 권한이 없습니다"


# ---------------------------------------------------------------------------
# Public path skip tests
# ---------------------------------------------------------------------------

class TestPublicPathSkip:
    """공개 경로는 테넌트 설정을 건너뛴다."""

    def test_health_endpoint_skips_tenant(self):
        """헬스 체크 경로는 테넌트 미들웨어를 건너뛴다."""
        async def health(request):
            return PlainTextResponse("ok")

        app = _build_app_with_tenant_middleware(health, path="/health")
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.text == "ok"

    def test_docs_endpoint_skips_tenant(self):
        """API 문서 경로는 테넌트 미들웨어를 건너뛴다."""
        async def docs(request):
            return PlainTextResponse("docs")

        app = _build_app_with_tenant_middleware(docs, path="/docs")
        client = TestClient(app)
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_shared_link_prefix_skips_tenant(self):
        """공유 링크 경로는 테넌트 미들웨어를 건너뛴다."""
        async def shared(request):
            return PlainTextResponse("shared")

        app = _build_app_with_tenant_middleware(shared, path="/api/shared/{token}")
        client = TestClient(app)
        resp = client.get("/api/shared/abc123")
        assert resp.status_code == 200

    def test_auth_callback_skips_tenant(self):
        """인증 콜백 경로는 테넌트 미들웨어를 건너뛴다."""
        async def callback(request):
            return PlainTextResponse("callback")

        app = _build_app_with_tenant_middleware(callback, path="/api/auth/callback")
        client = TestClient(app)
        resp = client.get("/api/auth/callback")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Missing user / tenant_id → 403 tests
# ---------------------------------------------------------------------------

class TestMissingTenantReturns403:
    """tenant_id가 없거나 비어 있으면 403을 반환한다."""

    def test_no_user_on_request_state_returns_403(self):
        """request.state.user가 없으면 403을 반환한다."""
        async def protected(request):
            return PlainTextResponse("protected")

        app = _build_app_with_tenant_middleware(protected)
        client = TestClient(app)
        resp = client.get("/api/documents")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "TENANT_REQUIRED"

    def test_empty_tenant_id_returns_403(self):
        """tenant_id가 빈 문자열이면 403을 반환한다."""
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectEmptyTenantUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = _make_user(tenant_id="")
                return await call_next(request)

        async def protected(request):
            return PlainTextResponse("protected")

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(TenantMiddleware)
        app.add_middleware(InjectEmptyTenantUser)

        client = TestClient(app)
        resp = client.get("/api/documents")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "TENANT_REQUIRED"

    def test_whitespace_tenant_id_returns_403(self):
        """tenant_id가 공백만 있으면 403을 반환한다."""
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectWhitespaceTenantUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = _make_user(tenant_id="   ")
                return await call_next(request)

        async def protected(request):
            return PlainTextResponse("protected")

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(TenantMiddleware)
        app.add_middleware(InjectWhitespaceTenantUser)

        client = TestClient(app)
        resp = client.get("/api/documents")
        assert resp.status_code == 403

    def test_none_tenant_id_returns_403(self):
        """tenant_id가 None이면 403을 반환한다."""
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectNoneTenantUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                user = _make_user()
                user.tenant_id = None  # type: ignore
                request.state.user = user
                return await call_next(request)

        async def protected(request):
            return PlainTextResponse("protected")

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(TenantMiddleware)
        app.add_middleware(InjectNoneTenantUser)

        client = TestClient(app)
        resp = client.get("/api/documents")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Valid tenant_id → RLS session variable set + request passes through
# ---------------------------------------------------------------------------

class TestValidTenantSetup:
    """유효한 tenant_id가 있으면 request.state.tenant_id를 설정하고 요청을 통과시킨다."""

    def test_valid_tenant_sets_state_and_passes_through(self):
        """유효한 tenant_id로 request.state.tenant_id를 설정하고 요청을 통과시킨다."""
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = _make_user(tenant_id="tenant-abc")
                return await call_next(request)

        async def protected(request):
            tenant_id = getattr(request.state, "tenant_id", None)
            return JSONResponse({"tenant_id": tenant_id})

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(TenantMiddleware)
        app.add_middleware(InjectUser)

        client = TestClient(app)
        resp = client.get("/api/documents")

        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "tenant-abc"

    def test_tenant_id_is_trimmed(self):
        """tenant_id 앞뒤 공백이 제거된다."""
        from starlette.middleware.base import BaseHTTPMiddleware

        class InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = _make_user(tenant_id="  tenant-xyz  ")
                return await call_next(request)

        async def protected(request):
            tenant_id = getattr(request.state, "tenant_id", None)
            return JSONResponse({"tenant_id": tenant_id})

        app = Starlette(routes=[Route("/api/documents", protected)])
        app.add_middleware(TenantMiddleware)
        app.add_middleware(InjectUser)

        client = TestClient(app)
        resp = client.get("/api/documents")

        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "tenant-xyz"
