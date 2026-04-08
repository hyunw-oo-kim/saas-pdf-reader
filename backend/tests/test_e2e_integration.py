"""E2E 통합 테스트 — 실제 SQLite DB + 로컬 파일시스템으로 전체 사용자 흐름 검증.

각 테스트 클래스가 독립적인 DB와 스토리지를 사용한다.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

# 테스트 전용 환경변수 (import 전에 설정)
_test_db = os.path.join(tempfile.gettempdir(), f"test_e2e_{uuid.uuid4().hex}.db")
_test_storage = tempfile.mkdtemp(prefix="pdf_storage_")

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_test_db}"
os.environ["LOCAL_STORAGE_PATH"] = _test_storage
os.environ["BACKEND_BASE_URL"] = "http://testserver"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["AUTH0_DOMAIN"] = "test.auth0.com"
os.environ["AUTH0_CLIENT_ID"] = "test-client-id"
os.environ["AUTH0_CLIENT_SECRET"] = "test-client-secret"

from fastapi.testclient import TestClient
from app.routers.auth import _create_access_token


def _token(role: str = "editor") -> str:
    t, _ = _create_access_token({
        "sub": "e2e-user",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "email": "e2e@test.com",
        "role": role,
    })
    return t


def _auth(role: str = "editor") -> dict:
    return {"Authorization": f"Bearer {_token(role)}"}


def _pdf(size: int = 1024) -> bytes:
    return b"%PDF-1.4 " + b"x" * max(0, size - 9)


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    import asyncio
    from app.database import engine, Base
    import app.models  # noqa

    asyncio.run(_create_tables(engine, Base))
    yield
    try:
        os.unlink(_test_db)
    except OSError:
        pass


async def _create_tables(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
def c():
    from app.main import app
    with TestClient(app) as client:
        yield client


# ── 문서 CRUD 전체 흐름 ──────────────────────────────────────────

class TestDocumentCRUD:
    def test_full_lifecycle(self, c):
        """업로드 → 목록 → 열람 → 파일서빙 → 이름변경 → 삭제 → 삭제확인."""
        h = _auth()

        # 1. 업로드
        pdf = _pdf()
        r = c.post("/api/documents/upload", headers=h, files={"file": ("test.pdf", pdf, "application/pdf")})
        assert r.status_code == 201, r.text
        doc_id = r.json()["id"]
        assert r.json()["filename"] == "test.pdf"

        # 2. 목록
        r = c.get("/api/documents", headers=h)
        assert r.status_code == 200
        assert any(item["id"] == doc_id for item in r.json()["items"])

        # 3. 열람 (파일 서빙 URL)
        r = c.get(f"/api/documents/{doc_id}/view", headers=h)
        assert r.status_code == 200
        sas_url = r.json()["sas_url"]
        assert "api/files" in sas_url

        # 4. 파일 서빙
        path = sas_url.replace("http://testserver", "")
        r = c.get(path)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

        # 5. 이름변경
        r = c.patch(f"/api/documents/{doc_id}", headers={**h, "Content-Type": "application/json"}, json={"filename": "renamed.pdf"})
        assert r.status_code == 200
        assert r.json()["filename"] == "renamed.pdf"

        # 6. 삭제
        r = c.delete(f"/api/documents/{doc_id}", headers=h)
        assert r.status_code == 204

        # 7. 삭제 후 목록에서 사라짐
        r = c.get("/api/documents", headers=h)
        assert r.status_code == 200
        assert not any(item["id"] == doc_id for item in r.json()["items"])

        # 8. 삭제 후 열람 404
        r = c.get(f"/api/documents/{doc_id}/view", headers=h)
        assert r.status_code == 404


# ── 공유 링크 전체 흐름 ──────────────────────────────────────────

class TestShareFlow:
    def test_share_lifecycle(self, c):
        """공유 생성 → 접근 → 목록 → 취소 → 취소 후 접근 거부."""
        h = _auth()

        # 문서 업로드
        r = c.post("/api/documents/upload", headers=h, files={"file": ("share_test.pdf", _pdf(), "application/pdf")})
        assert r.status_code == 201
        doc_id = r.json()["id"]

        # 1. 공유 링크 생성
        r = c.post(f"/api/documents/{doc_id}/share", headers={**h, "Content-Type": "application/json"}, json={"expiry": "1d", "permission": "read_only"})
        assert r.status_code == 201
        share_id = r.json()["share_id"]
        share_token = r.json()["share_url"].split("/")[-1]

        # 2. 공유 링크로 접근 (인증 없이)
        r = c.get(f"/api/shared/{share_token}")
        assert r.status_code == 200
        assert r.json()["permission"] == "read_only"
        assert r.json()["filename"] == "share_test.pdf"
        assert "sas_url" in r.json()

        # 3. 공유 링크 목록
        r = c.get(f"/api/documents/{doc_id}/share", headers=h)
        assert r.status_code == 200
        assert any(link["share_id"] == share_id for link in r.json())

        # 4. 공유 링크 취소
        r = c.delete(f"/api/documents/{doc_id}/share/{share_id}", headers=h)
        assert r.status_code == 204

        # 5. 취소된 링크 접근 거부
        r = c.get(f"/api/shared/{share_token}")
        assert r.status_code == 403

        # Cleanup
        c.delete(f"/api/documents/{doc_id}", headers=h)


# ── 업로드 검증 ──────────────────────────────────────────────────

class TestUploadValidation:
    def test_non_pdf_rejected(self, c):
        r = c.post("/api/documents/upload", headers=_auth(), files={"file": ("test.txt", b"not a pdf", "text/plain")})
        assert r.status_code == 400

    def test_oversized_rejected(self, c):
        big = _pdf(100 * 1024 * 1024 + 1)
        r = c.post("/api/documents/upload", headers=_auth(), files={"file": ("big.pdf", big, "application/pdf")})
        assert r.status_code == 413


# ── 역할 기반 접근 제어 ──────────────────────────────────────────

class TestRoleAccess:
    def test_viewer_cannot_upload(self, c):
        r = c.post("/api/documents/upload", headers=_auth("viewer"), files={"file": ("t.pdf", _pdf(), "application/pdf")})
        assert r.status_code == 403

    def test_viewer_can_list(self, c):
        r = c.get("/api/documents", headers=_auth("viewer"))
        assert r.status_code == 200

    def test_no_auth_rejected(self, c):
        r = c.get("/api/documents")
        assert r.status_code == 401

    def test_viewer_cannot_delete(self, c):
        # 먼저 editor로 업로드
        h = _auth("editor")
        r = c.post("/api/documents/upload", headers=h, files={"file": ("del.pdf", _pdf(), "application/pdf")})
        doc_id = r.json()["id"]
        # viewer로 삭제 시도
        r = c.delete(f"/api/documents/{doc_id}", headers=_auth("viewer"))
        assert r.status_code == 403
        # Cleanup
        c.delete(f"/api/documents/{doc_id}", headers=h)


class TestDeleteWithRelatedRecords:
    """연관 레코드(공유 링크 등)가 있는 문서 삭제."""

    def test_delete_document_with_share_links(self, c):
        """공유 링크가 있는 문서도 삭제 가능."""
        h = _auth()
        # 업로드
        r = c.post("/api/documents/upload", headers=h, files={"file": ("shared.pdf", _pdf(), "application/pdf")})
        doc_id = r.json()["id"]
        # 공유 링크 생성
        r = c.post(f"/api/documents/{doc_id}/share", headers={**h, "Content-Type": "application/json"}, json={"expiry": "1d", "permission": "read_only"})
        assert r.status_code == 201
        # 삭제
        r = c.delete(f"/api/documents/{doc_id}", headers=h)
        assert r.status_code == 204
        # 목록에서 사라짐
        r = c.get("/api/documents", headers=h)
        assert not any(item["id"] == doc_id for item in r.json()["items"])
