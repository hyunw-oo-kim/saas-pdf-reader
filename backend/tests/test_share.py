"""Share API 단위 테스트.

POST /api/documents/{id}/share — 공유 링크 생성
DELETE /api/documents/{id}/share/{share_id} — 공유 링크 무효화
GET /api/shared/{share_token} — 공유 링크를 통한 문서 접근
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

from app.routers.share import router, EXPIRY_MAP


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


@dataclass
class FakeShareLink:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    document_id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(
        default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000010")
    )
    created_by: uuid.UUID = field(
        default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000001")
    )
    share_token: str = "test-share-token"
    permission: str = "read_only"
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=1)
    )
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FakeBlobService:
    """테스트용 Blob Storage 서비스."""

    def __init__(self):
        self.sas_calls: list[str] = []

    def generate_sas_url(self, blob_path: str, expire_minutes: int | None = None) -> tuple[str, datetime]:
        self.sas_calls.append(blob_path)
        minutes = expire_minutes or 15
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        sas_url = f"https://fakestorage.blob.core.windows.net/documents/{blob_path}?sig=fake"
        return sas_url, expires_at


class FakeScalarResult:
    def __init__(self, items: list):
        self._items = items

    def first(self):
        return self._items[0] if self._items else None


class FakeExecuteResult:
    def __init__(self, items: list | None = None):
        self._items = items or []

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._items)


class FakeShareDBSession:
    """Fake async DB session for share tests."""

    def __init__(
        self,
        document: FakeDoc | None = None,
        share_link: FakeShareLink | None = None,
        fail_commit: bool = False,
    ):
        self._document = document
        self._share_link = share_link
        self.added: list = []
        self.committed = False
        self.fail_commit = fail_commit
        self._query_count = 0

    async def execute(self, stmt):
        self._query_count += 1
        # Determine what to return based on query context
        stmt_str = str(stmt)
        if "share_links" in stmt_str:
            if self._share_link is not None:
                return FakeExecuteResult(items=[self._share_link])
            return FakeExecuteResult(items=[])
        if "documents" in stmt_str:
            if self._document is not None:
                return FakeExecuteResult(items=[self._document])
            return FakeExecuteResult(items=[])
        return FakeExecuteResult(items=[])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.fail_commit:
            raise Exception("DB commit simulated failure")
        self.committed = True

    async def refresh(self, obj):
        if not hasattr(obj, "created_at") or obj.created_at is None:
            obj.created_at = datetime.now(timezone.utc)

    async def rollback(self):
        pass


def _build_share_app(
    user: FakeUser | None = None,
    blob_service: FakeBlobService | None = None,
    db_session: FakeShareDBSession | None = None,
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
# POST /api/documents/{id}/share — 공유 링크 생성
# ---------------------------------------------------------------------------

class TestCreateShareLink:
    """공유 링크 생성 테스트."""

    def test_create_share_link_success(self):
        """유효한 요청으로 공유 링크를 생성한다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "1h", "permission": "read_only"},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert "share_id" in body
        assert "share_url" in body
        assert "expires_at" in body
        assert body["permission"] == "read_only"
        assert body["share_url"].startswith("/api/shared/")

    def test_create_share_link_all_expiry_options(self):
        """모든 만료 옵션(1h, 1d, 7d, 30d)이 동작한다."""
        for expiry in ["1h", "1d", "7d", "30d"]:
            doc = FakeDoc()
            db = FakeShareDBSession(document=doc)
            client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

            resp = client.post(
                f"/api/documents/{doc.id}/share",
                json={"expiry": expiry, "permission": "read_only"},
            )

            assert resp.status_code == 201, f"Failed for expiry={expiry}"

    def test_create_share_link_expiry_time_correct(self):
        """만료 시간이 올바르게 설정된다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        before = datetime.now(timezone.utc)
        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "7d", "permission": "annotate"},
        )
        after = datetime.now(timezone.utc)

        assert resp.status_code == 201
        expires_at_str = resp.json()["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))

        expected_min = before + timedelta(days=7) - timedelta(seconds=5)
        expected_max = after + timedelta(days=7) + timedelta(seconds=5)
        assert expected_min <= expires_at <= expected_max

    def test_create_share_link_annotate_permission(self):
        """annotate 권한으로 공유 링크를 생성한다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "1d", "permission": "annotate"},
        )

        assert resp.status_code == 201
        assert resp.json()["permission"] == "annotate"

    def test_create_share_link_document_not_found(self):
        """존재하지 않는 문서에 대해 404를 반환한다."""
        db = FakeShareDBSession(document=None)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        fake_id = uuid.uuid4()
        resp = client.post(
            f"/api/documents/{fake_id}/share",
            json={"expiry": "1h", "permission": "read_only"},
        )

        assert resp.status_code == 404

    def test_viewer_cannot_create_share_link(self):
        """Viewer 역할은 공유 링크를 생성할 수 없다 (403)."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "1h", "permission": "read_only"},
        )

        assert resp.status_code == 403

    def test_admin_can_create_share_link(self):
        """Admin 역할은 공유 링크를 생성할 수 있다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="admin"), db_session=db)

        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "1h", "permission": "read_only"},
        )

        assert resp.status_code == 201

    def test_invalid_expiry_rejected(self):
        """잘못된 만료 옵션은 422를 반환한다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "2h", "permission": "read_only"},
        )

        assert resp.status_code == 422

    def test_invalid_permission_rejected(self):
        """잘못된 권한 옵션은 422를 반환한다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "1h", "permission": "write"},
        )

        assert resp.status_code == 422

    def test_share_link_saved_to_db(self):
        """공유 링크가 DB에 저장된다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post(
            f"/api/documents/{doc.id}/share",
            json={"expiry": "1h", "permission": "read_only"},
        )

        assert resp.status_code == 201
        assert len(db.added) == 1
        assert db.committed is True
        saved = db.added[0]
        assert saved.permission == "read_only"
        assert saved.is_active is True


# ---------------------------------------------------------------------------
# DELETE /api/documents/{id}/share/{share_id} — 공유 링크 무효화
# ---------------------------------------------------------------------------

class TestRevokeShareLink:
    """공유 링크 무효화 테스트."""

    def test_revoke_share_link_success(self):
        """공유 링크를 성공적으로 무효화한다."""
        doc = FakeDoc()
        share = FakeShareLink(document_id=doc.id, is_active=True)
        db = FakeShareDBSession(document=doc, share_link=share)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.delete(f"/api/documents/{doc.id}/share/{share.id}")

        assert resp.status_code == 204
        assert share.is_active is False
        assert db.committed is True

    def test_revoke_nonexistent_share_link_returns_404(self):
        """존재하지 않는 공유 링크 무효화 시 404를 반환한다."""
        doc = FakeDoc()
        db = FakeShareDBSession(document=doc, share_link=None)
        client = _build_share_app(user=FakeUser(role="editor"), db_session=db)

        fake_share_id = uuid.uuid4()
        resp = client.delete(f"/api/documents/{doc.id}/share/{fake_share_id}")

        assert resp.status_code == 404

    def test_viewer_cannot_revoke(self):
        """Viewer 역할은 공유 링크를 무효화할 수 없다 (403)."""
        doc = FakeDoc()
        share = FakeShareLink(document_id=doc.id)
        db = FakeShareDBSession(document=doc, share_link=share)
        client = _build_share_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.delete(f"/api/documents/{doc.id}/share/{share.id}")

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/shared/{share_token} — 공유 링크를 통한 문서 접근
# ---------------------------------------------------------------------------

class TestAccessSharedDocument:
    """공유 링크를 통한 문서 접근 테스트."""

    def test_access_valid_share_link(self):
        """유효한 공유 링크로 문서에 접근한다."""
        doc = FakeDoc()
        share = FakeShareLink(
            document_id=doc.id,
            share_token="valid-token",
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            permission="read_only",
        )
        blob_svc = FakeBlobService()
        db = FakeShareDBSession(document=doc, share_link=share)
        client = _build_share_app(blob_service=blob_svc, db_session=db)

        resp = client.get("/api/shared/valid-token")

        assert resp.status_code == 200
        body = resp.json()
        assert "sas_url" in body
        assert "expires_at" in body
        assert body["permission"] == "read_only"
        assert body["filename"] == doc.filename

    def test_access_expired_share_link_returns_403(self):
        """만료된 공유 링크 접근 시 403을 반환한다."""
        doc = FakeDoc()
        share = FakeShareLink(
            document_id=doc.id,
            share_token="expired-token",
            is_active=True,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db = FakeShareDBSession(document=doc, share_link=share)
        client = _build_share_app(db_session=db)

        resp = client.get("/api/shared/expired-token")

        assert resp.status_code == 403
        assert "만료" in resp.text

    def test_access_revoked_share_link_returns_403(self):
        """무효화된 공유 링크 접근 시 403을 반환한다."""
        doc = FakeDoc()
        share = FakeShareLink(
            document_id=doc.id,
            share_token="revoked-token",
            is_active=False,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db = FakeShareDBSession(document=doc, share_link=share)
        client = _build_share_app(db_session=db)

        resp = client.get("/api/shared/revoked-token")

        assert resp.status_code == 403
        assert "무효화" in resp.text

    def test_access_nonexistent_token_returns_404(self):
        """존재하지 않는 토큰 접근 시 404를 반환한다."""
        db = FakeShareDBSession(document=None, share_link=None)
        client = _build_share_app(db_session=db)

        resp = client.get("/api/shared/nonexistent-token")

        assert resp.status_code == 404

    def test_access_share_link_no_auth_required(self):
        """공유 링크 접근에 인증이 필요하지 않다 (user=None)."""
        doc = FakeDoc()
        share = FakeShareLink(
            document_id=doc.id,
            share_token="public-token",
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        blob_svc = FakeBlobService()
        db = FakeShareDBSession(document=doc, share_link=share)
        # user=None → no auth middleware injection
        client = _build_share_app(user=None, blob_service=blob_svc, db_session=db)

        resp = client.get("/api/shared/public-token")

        assert resp.status_code == 200

    def test_access_share_link_returns_sas_url(self):
        """공유 링크 접근 시 SAS URL이 반환된다."""
        doc = FakeDoc(blob_path="tenant-x/doc-y.pdf")
        share = FakeShareLink(
            document_id=doc.id,
            share_token="sas-token-test",
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        blob_svc = FakeBlobService()
        db = FakeShareDBSession(document=doc, share_link=share)
        client = _build_share_app(blob_service=blob_svc, db_session=db)

        resp = client.get("/api/shared/sas-token-test")

        assert resp.status_code == 200
        assert "tenant-x/doc-y.pdf" in resp.json()["sas_url"]
        assert len(blob_svc.sas_calls) == 1
