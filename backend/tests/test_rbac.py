"""RBAC 의존성 단위 테스트.

Admin, Editor, Viewer 역할 정의, 권한 매트릭스, 403 응답을 테스트한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.dependencies.rbac import Role, parse_role, require_role


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeUser:
    """테스트용 사용자 객체 (AuthMiddleware가 주입하는 것과 동일한 인터페이스)."""
    user_id: str = "user-1"
    tenant_id: str = "tenant-1"
    role: str = "viewer"
    email: str = "test@example.com"


def _build_app() -> FastAPI:
    """역할별 엔드포인트를 가진 테스트용 FastAPI 앱을 생성한다."""
    app = FastAPI()

    @app.get("/viewer-ok", dependencies=[Depends(require_role(Role.VIEWER))])
    async def viewer_endpoint():
        return {"access": "viewer"}

    @app.get("/editor-ok", dependencies=[Depends(require_role(Role.EDITOR))])
    async def editor_endpoint():
        return {"access": "editor"}

    @app.get("/admin-ok", dependencies=[Depends(require_role(Role.ADMIN))])
    async def admin_endpoint():
        return {"access": "admin"}

    return app


def _inject_user(app: FastAPI, user: FakeUser | None) -> None:
    """미들웨어 대신 request.state.user를 주입하는 미들웨어를 추가한다."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class _FakeAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if user is not None:
                request.state.user = user
            return await call_next(request)

    app.add_middleware(_FakeAuthMiddleware)


# ---------------------------------------------------------------------------
# Role enum tests
# ---------------------------------------------------------------------------

class TestRoleEnum:
    def test_role_hierarchy_order(self):
        """Admin > Editor > Viewer 계층 순서를 확인한다."""
        assert Role.ADMIN > Role.EDITOR > Role.VIEWER

    def test_role_values(self):
        assert Role.VIEWER == 1
        assert Role.EDITOR == 2
        assert Role.ADMIN == 3


# ---------------------------------------------------------------------------
# parse_role tests
# ---------------------------------------------------------------------------

class TestParseRole:
    def test_parse_viewer(self):
        assert parse_role("viewer") == Role.VIEWER

    def test_parse_editor(self):
        assert parse_role("editor") == Role.EDITOR

    def test_parse_admin(self):
        assert parse_role("admin") == Role.ADMIN

    def test_parse_case_insensitive(self):
        assert parse_role("ADMIN") == Role.ADMIN
        assert parse_role("Editor") == Role.EDITOR

    def test_parse_unknown_defaults_to_viewer(self):
        assert parse_role("unknown") == Role.VIEWER
        assert parse_role("") == Role.VIEWER


# ---------------------------------------------------------------------------
# require_role dependency tests
# ---------------------------------------------------------------------------

class TestRequireRole:
    """역할별 접근 제어 매트릭스 테스트."""

    # --- Viewer 역할 ---

    def test_viewer_can_access_viewer_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="viewer"))
        client = TestClient(app)
        resp = client.get("/viewer-ok")
        assert resp.status_code == 200

    def test_viewer_cannot_access_editor_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="viewer"))
        client = TestClient(app)
        resp = client.get("/editor-ok")
        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error"]["code"] == "FORBIDDEN"
        assert body["detail"]["error"]["message"] == "권한이 부족합니다"

    def test_viewer_cannot_access_admin_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="viewer"))
        client = TestClient(app)
        resp = client.get("/admin-ok")
        assert resp.status_code == 403

    # --- Editor 역할 ---

    def test_editor_can_access_viewer_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="editor"))
        client = TestClient(app)
        resp = client.get("/viewer-ok")
        assert resp.status_code == 200

    def test_editor_can_access_editor_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="editor"))
        client = TestClient(app)
        resp = client.get("/editor-ok")
        assert resp.status_code == 200

    def test_editor_cannot_access_admin_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="editor"))
        client = TestClient(app)
        resp = client.get("/admin-ok")
        assert resp.status_code == 403

    # --- Admin 역할 ---

    def test_admin_can_access_viewer_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="admin"))
        client = TestClient(app)
        resp = client.get("/viewer-ok")
        assert resp.status_code == 200

    def test_admin_can_access_editor_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="admin"))
        client = TestClient(app)
        resp = client.get("/editor-ok")
        assert resp.status_code == 200

    def test_admin_can_access_admin_endpoint(self):
        app = _build_app()
        _inject_user(app, FakeUser(role="admin"))
        client = TestClient(app)
        resp = client.get("/admin-ok")
        assert resp.status_code == 200

    # --- 인증 없는 경우 ---

    def test_no_user_returns_401(self):
        app = _build_app()
        _inject_user(app, None)
        client = TestClient(app)
        resp = client.get("/viewer-ok")
        assert resp.status_code == 401

    # --- 403 응답 형식 검증 ---

    def test_forbidden_response_format(self):
        """403 응답이 설계 문서의 오류 형식을 따르는지 확인한다."""
        app = _build_app()
        _inject_user(app, FakeUser(role="viewer"))
        client = TestClient(app)
        resp = client.get("/admin-ok")
        assert resp.status_code == 403
        body = resp.json()
        assert "detail" in body
        error = body["detail"]["error"]
        assert error["code"] == "FORBIDDEN"
        assert error["message"] == "권한이 부족합니다"
