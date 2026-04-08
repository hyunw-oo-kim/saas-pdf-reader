"""Document View API 단위 테스트.

GET /api/documents/{id}/view — SAS Token URL 생성
- 권한 확인 + 테넌트 검증 (RLS)
- Azure Blob Storage SAS Token 생성 (15분 TTL)
- SAS Token URL 반환
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.routers.documents import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeUser:
    user_id: str = "00000000-0000-0000-0000-000000000001"
    tenant_id: str = "00000000-0000-0000-0000-000000000010"
    role: str = "viewer"
    email: str = "viewer@example.com"


@dataclass
class FakeDoc:
    """In-memory document row."""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    filename: str = "report.pdf"
    size_bytes: int = 2048
    blob_path: str = "00000000-0000-0000-0000-000000000010/some-doc.pdf"
    content_type: str = "application/pdf"
    ocr_completed: bool = False
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    owner_id: uuid.UUID = field(
        default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000001")
    )
    tenant_id: uuid.UUID = field(
        default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000010")
    )


class FakeBlobService:
    """테스트용 Blob Storage 서비스 (SAS URL 생성 포함)."""

    def __init__(self, fail_sas: bool = False):
        self.sas_calls: list[str] = []
        self.fail_sas = fail_sas

    def build_blob_path(self, tenant_id: str, document_id: uuid.UUID) -> str:
        return f"{tenant_id}/{document_id}.pdf"

    def generate_sas_url(self, blob_path: str, expire_minutes: int | None = None) -> tuple[str, datetime]:
        if self.fail_sas:
            raise Exception("SAS generation simulated failure")
        self.sas_calls.append(blob_path)
        minutes = expire_minutes or 15
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        sas_url = f"https://fakestorage.blob.core.windows.net/documents/{blob_path}?sv=2024-01-01&se={expires_at.isoformat()}&sig=fakesig"
        return sas_url, expires_at

    async def upload_blob(self, blob_path: str, data: bytes, content_type: str = "application/pdf") -> None:
        pass

    async def delete_blob(self, blob_path: str) -> None:
        pass


class FakeScalarResult:
    def __init__(self, items: list):
        self._items = items

    def first(self):
        return self._items[0] if self._items else None

    def all(self) -> list:
        return self._items


class FakeExecuteResult:
    def __init__(self, items: list | None = None):
        self._items = items or []

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._items)


class FakeViewDBSession:
    """Fake async DB session for view tests."""

    def __init__(self, document: FakeDoc | None = None):
        self._document = document

    async def execute(self, stmt):
        if self._document is not None:
            return FakeExecuteResult(items=[self._document])
        return FakeExecuteResult(items=[])


def _build_view_app(
    user: FakeUser | None = None,
    blob_service: FakeBlobService | None = None,
    db_session: FakeViewDBSession | None = None,
) -> TestClient:
    """테스트용 FastAPI 앱을 생성한다."""
    app = FastAPI()

    class FakeAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if user is not None:
                request.state.user = user
                request.state.tenant_id = user.tenant_id
            return await call_next(request)

    app.add_middleware(FakeAuthMiddleware)
    app.include_router(router)

    if blob_service is not None:
        from app.services import blob_storage
        blob_storage.set_blob_service(blob_service)

    if db_session is not None:
        from app.database import get_db
        app.dependency_overrides[get_db] = lambda: db_session

    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestViewDocumentEndpoint:
    """GET /api/documents/{id}/view 엔드포인트 테스트."""

    def test_successful_view_returns_sas_url(self):
        """유효한 문서 열람 요청 시 SAS URL과 만료 시간을 반환한다."""
        doc = FakeDoc()
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        resp = client.get(f"/api/documents/{doc.id}/view")

        assert resp.status_code == 200
        body = resp.json()
        assert "sas_url" in body
        assert "expires_at" in body
        assert doc.blob_path in body["sas_url"]

    def test_sas_url_contains_blob_path(self):
        """SAS URL에 문서의 blob_path가 포함된다."""
        doc = FakeDoc(blob_path="tenant-abc/doc-123.pdf")
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        resp = client.get(f"/api/documents/{doc.id}/view")

        assert resp.status_code == 200
        assert "tenant-abc/doc-123.pdf" in resp.json()["sas_url"]

    def test_generate_sas_url_called_with_blob_path(self):
        """generate_sas_url이 문서의 blob_path로 호출된다."""
        doc = FakeDoc(blob_path="my-tenant/my-doc.pdf")
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        client.get(f"/api/documents/{doc.id}/view")

        assert blob_svc.sas_calls == ["my-tenant/my-doc.pdf"]

    def test_expires_at_is_approximately_15_minutes(self):
        """만료 시간이 현재로부터 약 15분 후이다."""
        doc = FakeDoc()
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        before = datetime.now(timezone.utc)
        resp = client.get(f"/api/documents/{doc.id}/view")
        after = datetime.now(timezone.utc)

        assert resp.status_code == 200
        expires_at_str = resp.json()["expires_at"]
        # Python 3.9 fromisoformat doesn't handle trailing 'Z'
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))

        expected_min = before + timedelta(minutes=14, seconds=50)
        expected_max = after + timedelta(minutes=15, seconds=10)
        assert expected_min <= expires_at <= expected_max

    def test_nonexistent_document_returns_404(self):
        """존재하지 않는 문서 열람 시 404를 반환한다."""
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=None)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        fake_id = uuid.uuid4()
        resp = client.get(f"/api/documents/{fake_id}/view")

        assert resp.status_code == 404
        assert "DOCUMENT_NOT_FOUND" in resp.text

    def test_invalid_uuid_returns_404(self):
        """잘못된 UUID 형식은 404를 반환한다."""
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=None)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        resp = client.get("/api/documents/not-a-uuid/view")

        assert resp.status_code == 404

    def test_viewer_can_view(self):
        """Viewer 역할은 문서를 열람할 수 있다."""
        doc = FakeDoc()
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        resp = client.get(f"/api/documents/{doc.id}/view")

        assert resp.status_code == 200

    def test_editor_can_view(self):
        """Editor 역할도 문서를 열람할 수 있다."""
        doc = FakeDoc()
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="editor"), blob_service=blob_svc, db_session=db)

        resp = client.get(f"/api/documents/{doc.id}/view")

        assert resp.status_code == 200

    def test_admin_can_view(self):
        """Admin 역할도 문서를 열람할 수 있다."""
        doc = FakeDoc()
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="admin"), blob_service=blob_svc, db_session=db)

        resp = client.get(f"/api/documents/{doc.id}/view")

        assert resp.status_code == 200

    def test_response_schema_matches_document_view_response(self):
        """응답이 DocumentViewResponse 스키마와 일치한다."""
        doc = FakeDoc()
        blob_svc = FakeBlobService()
        db = FakeViewDBSession(document=doc)
        client = _build_view_app(user=FakeUser(role="viewer"), blob_service=blob_svc, db_session=db)

        resp = client.get(f"/api/documents/{doc.id}/view")

        assert resp.status_code == 200
        body = resp.json()
        # Only sas_url and expires_at should be present
        assert set(body.keys()) == {"sas_url", "expires_at"}
        assert isinstance(body["sas_url"], str)
        assert isinstance(body["expires_at"], str)
