"""Document Update/Delete API 단위 테스트.

PATCH /api/documents/{id} — 파일명 변경
DELETE /api/documents/{id} — Blob Storage + Metadata_DB 동시 삭제 (트랜잭션)
Blob 삭제 실패 시 롤백 + 오류 로깅
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    role: str = "editor"
    email: str = "editor@example.com"


@dataclass
class FakeDoc:
    """In-memory document row."""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    filename: str = "original.pdf"
    size_bytes: int = 2048
    blob_path: str = ""
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
    """테스트용 Blob Storage 서비스."""

    def __init__(self, fail_delete: bool = False):
        self.deleted: list[str] = []
        self.fail_delete = fail_delete

    def build_blob_path(self, tenant_id: str, document_id: uuid.UUID) -> str:
        return f"{tenant_id}/{document_id}.pdf"

    async def upload_blob(self, blob_path: str, data: bytes, content_type: str = "application/pdf") -> None:
        pass

    async def delete_blob(self, blob_path: str) -> None:
        if self.fail_delete:
            raise Exception("Blob delete simulated failure")
        self.deleted.append(blob_path)


class FakeScalarResult:
    def __init__(self, items: list):
        self._items = items
        self._idx = 0

    def first(self):
        return self._items[0] if self._items else None

    def all(self) -> list:
        return self._items


class FakeExecuteResult:
    def __init__(self, items: list | None = None):
        self._items = items or []

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._items)


class FakeUpdateDeleteDBSession:
    """Fake async DB session for update/delete tests."""

    def __init__(
        self,
        document: FakeDoc | None = None,
        fail_commit: bool = False,
    ):
        self._document = document
        self.fail_commit = fail_commit
        self.committed = False
        self.rolled_back = False
        self.deleted_obj = None

    async def execute(self, stmt):
        if self._document is not None:
            return FakeExecuteResult(items=[self._document])
        return FakeExecuteResult(items=[])

    async def commit(self):
        if self.fail_commit:
            raise Exception("DB commit simulated failure")
        self.committed = True

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        self.deleted_obj = obj

    async def rollback(self):
        self.rolled_back = True


def _build_app(
    user: FakeUser | None = None,
    blob_service: FakeBlobService | None = None,
    db_session: FakeUpdateDeleteDBSession | None = None,
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
# PATCH /api/documents/{id} Tests
# ---------------------------------------------------------------------------

class TestUpdateDocument:
    """PATCH /api/documents/{id} — 파일명 변경 테스트."""

    def test_successful_rename(self):
        """유효한 파일명 변경이 성공한다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.patch(
            f"/api/documents/{doc.id}",
            json={"filename": "renamed.pdf"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "renamed.pdf"
        assert db.committed is True

    def test_rename_updates_document_filename(self):
        """파일명이 실제로 문서 객체에 반영된다."""
        doc = FakeDoc(filename="old_name.pdf", blob_path="tenant/doc.pdf")
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(user=FakeUser(role="editor"), db_session=db)

        client.patch(
            f"/api/documents/{doc.id}",
            json={"filename": "new_name.pdf"},
        )

        assert doc.filename == "new_name.pdf"

    def test_rename_nonexistent_document_returns_404(self):
        """존재하지 않는 문서 수정 시 404를 반환한다."""
        db = FakeUpdateDeleteDBSession(document=None)
        client = _build_app(user=FakeUser(role="editor"), db_session=db)

        fake_id = uuid.uuid4()
        resp = client.patch(
            f"/api/documents/{fake_id}",
            json={"filename": "new.pdf"},
        )

        assert resp.status_code == 404
        assert "DOCUMENT_NOT_FOUND" in resp.text

    def test_rename_invalid_uuid_returns_404(self):
        """잘못된 UUID 형식은 404를 반환한다."""
        db = FakeUpdateDeleteDBSession(document=None)
        client = _build_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.patch(
            "/api/documents/not-a-uuid",
            json={"filename": "new.pdf"},
        )

        assert resp.status_code == 404

    def test_rename_empty_filename_rejected(self):
        """빈 파일명은 422 유효성 검증 오류를 반환한다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.patch(
            f"/api/documents/{doc.id}",
            json={"filename": ""},
        )

        assert resp.status_code == 422

    def test_viewer_cannot_rename(self):
        """Viewer 역할은 파일명을 변경할 수 없다 (403)."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.patch(
            f"/api/documents/{doc.id}",
            json={"filename": "new.pdf"},
        )

        assert resp.status_code == 403

    def test_admin_can_rename(self):
        """Admin 역할은 파일명을 변경할 수 있다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(user=FakeUser(role="admin"), db_session=db)

        resp = client.patch(
            f"/api/documents/{doc.id}",
            json={"filename": "admin_renamed.pdf"},
        )

        assert resp.status_code == 200
        assert resp.json()["filename"] == "admin_renamed.pdf"

    def test_response_contains_all_meta_fields(self):
        """응답에 DocumentMeta의 모든 필드가 포함된다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.patch(
            f"/api/documents/{doc.id}",
            json={"filename": "check_fields.pdf"},
        )

        assert resp.status_code == 200
        body = resp.json()
        for key in ("id", "filename", "size_bytes", "content_type", "ocr_completed", "uploaded_at", "updated_at", "owner_id"):
            assert key in body


# ---------------------------------------------------------------------------
# DELETE /api/documents/{id} Tests
# ---------------------------------------------------------------------------

class TestDeleteDocument:
    """DELETE /api/documents/{id} — Blob + DB 삭제 테스트."""

    def test_successful_delete(self):
        """문서 삭제가 성공하면 204를 반환한다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        blob_svc = FakeBlobService()
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(
            user=FakeUser(role="editor"),
            blob_service=blob_svc,
            db_session=db,
        )

        resp = client.delete(f"/api/documents/{doc.id}")

        assert resp.status_code == 204
        assert blob_svc.deleted == ["tenant/doc.pdf"]
        assert db.deleted_obj is doc
        assert db.committed is True

    def test_delete_nonexistent_document_returns_404(self):
        """존재하지 않는 문서 삭제 시 404를 반환한다."""
        blob_svc = FakeBlobService()
        db = FakeUpdateDeleteDBSession(document=None)
        client = _build_app(
            user=FakeUser(role="editor"),
            blob_service=blob_svc,
            db_session=db,
        )

        fake_id = uuid.uuid4()
        resp = client.delete(f"/api/documents/{fake_id}")

        assert resp.status_code == 404
        assert "DOCUMENT_NOT_FOUND" in resp.text

    def test_delete_invalid_uuid_returns_404(self):
        """잘못된 UUID 형식은 404를 반환한다."""
        blob_svc = FakeBlobService()
        db = FakeUpdateDeleteDBSession(document=None)
        client = _build_app(
            user=FakeUser(role="editor"),
            blob_service=blob_svc,
            db_session=db,
        )

        resp = client.delete("/api/documents/not-a-uuid")

        assert resp.status_code == 404

    def test_blob_delete_failure_returns_502_and_no_db_delete(self):
        """Blob 삭제 실패 시 502를 반환하고 DB 삭제를 수행하지 않는다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        blob_svc = FakeBlobService(fail_delete=True)
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(
            user=FakeUser(role="editor"),
            blob_service=blob_svc,
            db_session=db,
        )

        resp = client.delete(f"/api/documents/{doc.id}")

        assert resp.status_code == 502
        assert "STORAGE_DELETE_FAILED" in resp.text
        # DB delete should NOT have been called
        assert db.deleted_obj is None
        assert db.committed is False

    def test_db_delete_failure_after_blob_delete_returns_500(self):
        """Blob 삭제 성공 후 DB 삭제 실패 시 500을 반환한다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        blob_svc = FakeBlobService()
        db = FakeUpdateDeleteDBSession(document=doc, fail_commit=True)
        client = _build_app(
            user=FakeUser(role="editor"),
            blob_service=blob_svc,
            db_session=db,
        )

        resp = client.delete(f"/api/documents/{doc.id}")

        assert resp.status_code == 500
        assert "DELETE_INCONSISTENCY" in resp.text
        # Blob was deleted
        assert blob_svc.deleted == ["tenant/doc.pdf"]
        # DB was rolled back
        assert db.rolled_back is True

    def test_viewer_cannot_delete(self):
        """Viewer 역할은 문서를 삭제할 수 없다 (403)."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        blob_svc = FakeBlobService()
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(
            user=FakeUser(role="viewer"),
            blob_service=blob_svc,
            db_session=db,
        )

        resp = client.delete(f"/api/documents/{doc.id}")

        assert resp.status_code == 403

    def test_admin_can_delete(self):
        """Admin 역할은 문서를 삭제할 수 있다."""
        doc = FakeDoc(blob_path="tenant/doc.pdf")
        blob_svc = FakeBlobService()
        db = FakeUpdateDeleteDBSession(document=doc)
        client = _build_app(
            user=FakeUser(role="admin"),
            blob_service=blob_svc,
            db_session=db,
        )

        resp = client.delete(f"/api/documents/{doc.id}")

        assert resp.status_code == 204
