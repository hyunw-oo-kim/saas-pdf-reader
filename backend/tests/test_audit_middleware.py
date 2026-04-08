"""Audit Middleware 단위 테스트.

감사 로그 자동 기록, 작업 유형 감지, 공개 경로 건너뛰기, IP 추출을 테스트한다.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.audit import (
    AuditMiddleware,
    detect_action_type,
    extract_document_id,
    get_client_ip,
)
from app.middleware.auth import AuthenticatedUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(
    user_id: str = "11111111-1111-1111-1111-111111111111",
    tenant_id: str = "22222222-2222-2222-2222-222222222222",
    role: str = "editor",
    email: str = "test@example.com",
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
    )


class InjectUser(BaseHTTPMiddleware):
    """테스트용: request.state.user를 주입하는 미들웨어."""

    def __init__(self, app, user: AuthenticatedUser | None = None):
        super().__init__(app)
        self.user = user or _make_user()

    async def dispatch(self, request, call_next):
        request.state.user = self.user
        request.state.tenant_id = self.user.tenant_id
        return await call_next(request)


def _mock_async_session():
    """async_session 컨텍스트 매니저를 모킹한다."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session_factory = MagicMock(return_value=mock_session)
    return mock_session_factory, mock_session


# ---------------------------------------------------------------------------
# detect_action_type 테스트
# ---------------------------------------------------------------------------

class TestDetectActionType:
    """HTTP method + URL 경로에서 작업 유형을 올바르게 감지한다."""

    def test_view_document(self):
        doc_id = str(uuid.uuid4())
        assert detect_action_type("GET", f"/api/documents/{doc_id}/view") == "view"

    def test_upload_document(self):
        assert detect_action_type("POST", "/api/documents/upload") == "upload"

    def test_delete_document(self):
        doc_id = str(uuid.uuid4())
        assert detect_action_type("DELETE", f"/api/documents/{doc_id}") == "delete"

    def test_share_create(self):
        doc_id = str(uuid.uuid4())
        assert detect_action_type("POST", f"/api/documents/{doc_id}/share") == "share"

    def test_share_revoke(self):
        doc_id = str(uuid.uuid4())
        share_id = str(uuid.uuid4())
        assert detect_action_type("DELETE", f"/api/documents/{doc_id}/share/{share_id}") == "share"

    def test_annotate_save(self):
        doc_id = str(uuid.uuid4())
        assert detect_action_type("PUT", f"/api/documents/{doc_id}/annotations") == "annotate"

    def test_annotate_delete(self):
        doc_id = str(uuid.uuid4())
        annot_id = str(uuid.uuid4())
        assert detect_action_type("DELETE", f"/api/documents/{doc_id}/annotations/{annot_id}") == "annotate"

    def test_rename_document(self):
        doc_id = str(uuid.uuid4())
        assert detect_action_type("PATCH", f"/api/documents/{doc_id}") == "rename"

    def test_ocr_start(self):
        doc_id = str(uuid.uuid4())
        assert detect_action_type("POST", f"/api/documents/{doc_id}/ocr") == "ocr"

    def test_get_document_list_returns_none(self):
        """GET /api/documents (목록 조회)는 로깅 대상이 아니다."""
        assert detect_action_type("GET", "/api/documents") is None

    def test_get_ocr_status_returns_none(self):
        """GET /api/documents/{id}/ocr/status는 로깅 대상이 아니다."""
        doc_id = str(uuid.uuid4())
        assert detect_action_type("GET", f"/api/documents/{doc_id}/ocr/status") is None

    def test_unknown_path_returns_none(self):
        assert detect_action_type("POST", "/api/unknown") is None


# ---------------------------------------------------------------------------
# extract_document_id 테스트
# ---------------------------------------------------------------------------

class TestExtractDocumentId:
    def test_extracts_uuid_from_path(self):
        doc_id = uuid.uuid4()
        result = extract_document_id(f"/api/documents/{doc_id}/view")
        assert result == doc_id

    def test_returns_none_for_no_uuid(self):
        assert extract_document_id("/api/documents/upload") is None

    def test_returns_none_for_invalid_uuid(self):
        assert extract_document_id("/api/documents/not-a-uuid/view") is None


# ---------------------------------------------------------------------------
# get_client_ip 테스트
# ---------------------------------------------------------------------------

class TestGetClientIp:
    def test_uses_x_forwarded_for(self):
        """X-Forwarded-For 헤더가 있으면 첫 번째 IP를 사용한다."""
        async def handler(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/test", handler)])
        client = TestClient(app)

        # TestClient에서 직접 헤더를 설정하여 테스트
        # get_client_ip를 직접 호출하는 대신 Request 객체를 만들어 테스트
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")],
            "query_string": b"",
        }
        request = Request(scope)
        assert get_client_ip(request) == "1.2.3.4"

    def test_falls_back_to_client_host(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "client": ("10.0.0.1", 12345),
        }
        request = Request(scope)
        assert get_client_ip(request) == "10.0.0.1"

    def test_returns_unknown_when_no_client(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        assert get_client_ip(request) == "unknown"


# ---------------------------------------------------------------------------
# AuditMiddleware 통합 테스트
# ---------------------------------------------------------------------------

class TestAuditMiddlewareLogging:
    """AuditMiddleware가 올바르게 감사 로그를 기록한다."""

    def _build_app(self, handler, path: str, user: AuthenticatedUser | None = None):
        """AuditMiddleware + InjectUser가 적용된 테스트 앱을 생성한다."""
        app = Starlette(routes=[Route(path, handler, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])])
        app.add_middleware(AuditMiddleware)
        app.add_middleware(InjectUser, user=user or _make_user())
        return app

    def test_logs_document_upload(self):
        """POST /api/documents/upload 성공 시 감사 로그를 기록한다."""
        async def handler(request):
            return JSONResponse({"id": "test"}, status_code=201)

        app = self._build_app(handler, "/api/documents/upload")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.post("/api/documents/upload")

        assert resp.status_code == 201
        # Verify audit log was written
        mock_session.add.assert_called_once()
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.action_type == "upload"
        assert str(log_entry.user_id) == "11111111-1111-1111-1111-111111111111"
        assert str(log_entry.tenant_id) == "22222222-2222-2222-2222-222222222222"
        mock_session.commit.assert_called_once()

    def test_logs_document_view(self):
        """GET /api/documents/{id}/view 성공 시 감사 로그를 기록한다."""
        doc_id = str(uuid.uuid4())

        async def handler(request):
            return JSONResponse({"sas_url": "https://example.com"})

        app = self._build_app(handler, f"/api/documents/{doc_id}/view")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.get(f"/api/documents/{doc_id}/view")

        assert resp.status_code == 200
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.action_type == "view"
        assert log_entry.document_id == uuid.UUID(doc_id)

    def test_logs_document_delete(self):
        """DELETE /api/documents/{id} 성공 시 감사 로그를 기록한다."""
        doc_id = str(uuid.uuid4())

        async def handler(request):
            return Response(status_code=204)

        app = self._build_app(handler, f"/api/documents/{doc_id}")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.delete(f"/api/documents/{doc_id}")

        assert resp.status_code == 204
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.action_type == "delete"
        assert log_entry.document_id == uuid.UUID(doc_id)

    def test_logs_rename(self):
        """PATCH /api/documents/{id} 성공 시 rename 감사 로그를 기록한다."""
        doc_id = str(uuid.uuid4())

        async def handler(request):
            return JSONResponse({"filename": "new.pdf"})

        app = self._build_app(handler, f"/api/documents/{doc_id}")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.patch(f"/api/documents/{doc_id}")

        assert resp.status_code == 200
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.action_type == "rename"

    def test_logs_share_create(self):
        """POST /api/documents/{id}/share 성공 시 share 감사 로그를 기록한다."""
        doc_id = str(uuid.uuid4())

        async def handler(request):
            return JSONResponse({"share_url": "/api/shared/abc"}, status_code=201)

        app = self._build_app(handler, f"/api/documents/{doc_id}/share")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.post(f"/api/documents/{doc_id}/share")

        assert resp.status_code == 201
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.action_type == "share"

    def test_logs_annotate(self):
        """PUT /api/documents/{id}/annotations 성공 시 annotate 감사 로그를 기록한다."""
        doc_id = str(uuid.uuid4())

        async def handler(request):
            return JSONResponse({"xfdf_data": "<xfdf/>"})

        app = self._build_app(handler, f"/api/documents/{doc_id}/annotations")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.put(f"/api/documents/{doc_id}/annotations")

        assert resp.status_code == 200
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.action_type == "annotate"

    def test_logs_ocr_start(self):
        """POST /api/documents/{id}/ocr 성공 시 ocr 감사 로그를 기록한다."""
        doc_id = str(uuid.uuid4())

        async def handler(request):
            return JSONResponse({"job_id": "test"}, status_code=202)

        app = self._build_app(handler, f"/api/documents/{doc_id}/ocr")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.post(f"/api/documents/{doc_id}/ocr")

        assert resp.status_code == 202
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.action_type == "ocr"

    def test_captures_ip_address(self):
        """감사 로그에 클라이언트 IP 주소가 기록된다."""
        async def handler(request):
            return JSONResponse({"id": "test"}, status_code=201)

        app = self._build_app(handler, "/api/documents/upload")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.post(
                "/api/documents/upload",
                headers={"X-Forwarded-For": "203.0.113.50"},
            )

        assert resp.status_code == 201
        log_entry = mock_session.add.call_args[0][0]
        assert log_entry.ip_address == "203.0.113.50"


class TestAuditMiddlewareSkips:
    """AuditMiddleware가 로깅을 건너뛰는 케이스."""

    def _build_app(self, handler, path: str, user: AuthenticatedUser | None = None):
        app = Starlette(routes=[Route(path, handler, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])])
        app.add_middleware(AuditMiddleware)
        if user is not None:
            app.add_middleware(InjectUser, user=user)
        return app

    def test_skips_public_paths(self):
        """공개 경로는 감사 로그를 기록하지 않는다."""
        async def handler(request):
            return PlainTextResponse("ok")

        app = self._build_app(handler, "/health")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.get("/health")

        assert resp.status_code == 200
        mock_session.add.assert_not_called()

    def test_skips_auth_endpoints(self):
        """인증 엔드포인트는 감사 로그를 기록하지 않는다."""
        async def handler(request):
            return JSONResponse({"token": "abc"})

        app = self._build_app(handler, "/api/auth/callback")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.get("/api/auth/callback")

        assert resp.status_code == 200
        mock_session.add.assert_not_called()

    def test_skips_shared_link_paths(self):
        """공유 링크 경로는 감사 로그를 기록하지 않는다."""
        async def handler(request):
            return JSONResponse({"sas_url": "https://example.com"})

        app = self._build_app(handler, "/api/shared/{token}")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.get("/api/shared/some-token")

        assert resp.status_code == 200
        mock_session.add.assert_not_called()

    def test_skips_error_responses(self):
        """4xx/5xx 응답은 감사 로그를 기록하지 않는다."""
        async def handler(request):
            return JSONResponse({"error": "not found"}, status_code=404)

        doc_id = str(uuid.uuid4())
        app = self._build_app(handler, f"/api/documents/{doc_id}/view", user=_make_user())
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.get(f"/api/documents/{doc_id}/view")

        assert resp.status_code == 404
        mock_session.add.assert_not_called()

    def test_skips_non_auditable_get(self):
        """GET /api/documents (목록 조회)는 감사 로그를 기록하지 않는다."""
        async def handler(request):
            return JSONResponse({"items": []})

        app = self._build_app(handler, "/api/documents", user=_make_user())
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.get("/api/documents")

        assert resp.status_code == 200
        mock_session.add.assert_not_called()

    def test_skips_when_no_user(self):
        """request.state.user가 없으면 감사 로그를 기록하지 않는다."""
        async def handler(request):
            return JSONResponse({"id": "test"}, status_code=201)

        # InjectUser 미들웨어를 추가하지 않음
        app = self._build_app(handler, "/api/documents/upload")
        mock_factory, mock_session = _mock_async_session()

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.post("/api/documents/upload")

        assert resp.status_code == 201
        mock_session.add.assert_not_called()

    def test_db_error_does_not_break_response(self):
        """감사 로그 DB 기록 실패 시에도 원래 응답은 정상 반환된다."""
        async def handler(request):
            return JSONResponse({"id": "test"}, status_code=201)

        app = Starlette(routes=[Route("/api/documents/upload", handler, methods=["POST"])])
        app.add_middleware(AuditMiddleware)
        app.add_middleware(InjectUser, user=_make_user())

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit.side_effect = Exception("DB connection error")
        mock_factory = MagicMock(return_value=mock_session)

        with patch("app.middleware.audit.async_session", mock_factory):
            client = TestClient(app)
            resp = client.post("/api/documents/upload")

        # 응답은 정상적으로 반환되어야 한다
        assert resp.status_code == 201
