"""OCR API 단위 테스트.

OCR 작업 시작, 상태 조회, 결과 조회, 상태 전이 로직을 테스트한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.routers.ocr import router
from app.services.ocr_service import (
    validate_transition,
    InvalidStateTransition,
    VALID_TRANSITIONS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeUser:
    user_id: str = "00000000-0000-0000-0000-000000000001"
    tenant_id: str = "00000000-0000-0000-0000-000000000010"
    role: str = "editor"
    email: str = "editor@example.com"


FAKE_DOC_ID = uuid.UUID("00000000-0000-0000-0000-000000000100")
FAKE_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
FAKE_JOB_ID = uuid.UUID("00000000-0000-0000-0000-000000000200")


class FakeDocument:
    """테스트용 Document 모델."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", FAKE_DOC_ID)
        self.tenant_id = kwargs.get("tenant_id", FAKE_TENANT_ID)
        self.blob_path = kwargs.get("blob_path", f"{FAKE_TENANT_ID}/{FAKE_DOC_ID}.pdf")
        self.ocr_completed = kwargs.get("ocr_completed", False)


class FakeOCRJob:
    """테스트용 OCRJob 모델."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", FAKE_JOB_ID)
        self.document_id = kwargs.get("document_id", FAKE_DOC_ID)
        self.tenant_id = kwargs.get("tenant_id", FAKE_TENANT_ID)
        self.status = kwargs.get("status", "queued")
        self.progress_percent = kwargs.get("progress_percent", 0)
        self.error_message = kwargs.get("error_message", None)
        self.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        self.completed_at = kwargs.get("completed_at", None)


class FakeOCRResult:
    """테스트용 OCRResult 모델."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.document_id = kwargs.get("document_id", FAKE_DOC_ID)
        self.tenant_id = kwargs.get("tenant_id", FAKE_TENANT_ID)
        self.page_number = kwargs.get("page_number", 1)
        self.extracted_text = kwargs.get("extracted_text", "Hello World")
        self.words = kwargs.get("words", [
            {"text": "Hello", "bounding_box": [0.0, 0.0, 1.0, 1.0], "confidence": 0.99},
            {"text": "World", "bounding_box": [1.5, 0.0, 2.5, 1.0], "confidence": 0.98},
        ])


class FakeScalarResult:
    """Fake scalar result for SQLAlchemy queries."""

    def __init__(self, items):
        self._items = items if isinstance(items, list) else [items]
        self._idx = 0

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return self._items


class FakeExecuteResult:
    """Fake execute result."""

    def __init__(self, items):
        self._items = items

    def scalars(self):
        return FakeScalarResult(self._items)


class FakeDBSession:
    """테스트용 DB 세션."""

    def __init__(self, document=None, ocr_job=None, ocr_results=None):
        self._document = document
        self._ocr_job = ocr_job
        self._ocr_results = ocr_results or []
        self.added = []
        self.committed = False
        self._query_count = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        # Assign a fake ID if it's an OCR job
        if hasattr(obj, "status") and not hasattr(obj, "_refreshed"):
            obj.id = FAKE_JOB_ID
            obj._refreshed = True

    async def rollback(self):
        pass

    async def execute(self, stmt):
        self._query_count += 1
        # Determine what's being queried based on the statement
        stmt_str = str(stmt)
        if "ocr_results" in stmt_str:
            return FakeExecuteResult(self._ocr_results)
        elif "ocr_jobs" in stmt_str:
            return FakeExecuteResult([self._ocr_job] if self._ocr_job else [])
        elif "documents" in stmt_str:
            return FakeExecuteResult([self._document] if self._document else [])
        return FakeExecuteResult([])


def _build_test_app(
    user: FakeUser | None = None,
    db_session: FakeDBSession | None = None,
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

    if db_session is not None:
        from app.database import get_db
        app.dependency_overrides[get_db] = lambda: db_session

    return TestClient(app)


# ---------------------------------------------------------------------------
# State transition tests
# ---------------------------------------------------------------------------

class TestStateTransition:
    """OCR 상태 전이 유효성 테스트."""

    def test_queued_to_processing_valid(self):
        """queued → processing 전이는 유효하다."""
        validate_transition("queued", "processing")  # Should not raise

    def test_processing_to_completed_valid(self):
        """processing → completed 전이는 유효하다."""
        validate_transition("processing", "completed")

    def test_processing_to_failed_valid(self):
        """processing → failed 전이는 유효하다."""
        validate_transition("processing", "failed")

    def test_queued_to_completed_invalid(self):
        """queued → completed 전이는 유효하지 않다."""
        with pytest.raises(InvalidStateTransition):
            validate_transition("queued", "completed")

    def test_queued_to_failed_invalid(self):
        """queued → failed 전이는 유효하지 않다."""
        with pytest.raises(InvalidStateTransition):
            validate_transition("queued", "failed")

    def test_completed_to_processing_invalid(self):
        """completed → processing 역방향 전이는 유효하지 않다."""
        with pytest.raises(InvalidStateTransition):
            validate_transition("completed", "processing")

    def test_completed_to_queued_invalid(self):
        """completed → queued 역방향 전이는 유효하지 않다."""
        with pytest.raises(InvalidStateTransition):
            validate_transition("completed", "queued")

    def test_failed_to_processing_invalid(self):
        """failed → processing 전이는 유효하지 않다."""
        with pytest.raises(InvalidStateTransition):
            validate_transition("failed", "processing")

    def test_failed_to_queued_invalid(self):
        """failed → queued 전이는 유효하지 않다."""
        with pytest.raises(InvalidStateTransition):
            validate_transition("failed", "queued")

    def test_same_state_transition_invalid(self):
        """동일 상태로의 전이는 유효하지 않다."""
        for state in ("queued", "processing", "completed", "failed"):
            with pytest.raises(InvalidStateTransition):
                validate_transition(state, state)


# ---------------------------------------------------------------------------
# POST /api/documents/{id}/ocr tests
# ---------------------------------------------------------------------------

class TestStartOCR:
    """POST /api/documents/{id}/ocr 엔드포인트 테스트."""

    def test_start_ocr_success(self):
        """유효한 문서에 대해 OCR 작업이 생성된다."""
        doc = FakeDocument()
        db = FakeDBSession(document=doc)
        client = _build_test_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post(f"/api/documents/{FAKE_DOC_ID}/ocr")

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert "job_id" in body
        assert db.committed is True
        assert len(db.added) == 1

    def test_start_ocr_document_not_found(self):
        """존재하지 않는 문서에 대해 404를 반환한다."""
        db = FakeDBSession(document=None)
        client = _build_test_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post(f"/api/documents/{FAKE_DOC_ID}/ocr")

        assert resp.status_code == 404

    def test_start_ocr_invalid_uuid(self):
        """유효하지 않은 UUID에 대해 404를 반환한다."""
        db = FakeDBSession()
        client = _build_test_app(user=FakeUser(role="editor"), db_session=db)

        resp = client.post("/api/documents/not-a-uuid/ocr")

        assert resp.status_code == 404

    def test_viewer_cannot_start_ocr(self):
        """Viewer 역할은 OCR을 시작할 수 없다."""
        doc = FakeDocument()
        db = FakeDBSession(document=doc)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.post(f"/api/documents/{FAKE_DOC_ID}/ocr")

        assert resp.status_code == 403

    def test_admin_can_start_ocr(self):
        """Admin 역할은 OCR을 시작할 수 있다."""
        doc = FakeDocument()
        db = FakeDBSession(document=doc)
        client = _build_test_app(user=FakeUser(role="admin"), db_session=db)

        resp = client.post(f"/api/documents/{FAKE_DOC_ID}/ocr")

        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# GET /api/documents/{id}/ocr/status tests
# ---------------------------------------------------------------------------

class TestGetOCRStatus:
    """GET /api/documents/{id}/ocr/status 엔드포인트 테스트."""

    def test_get_status_queued(self):
        """queued 상태의 OCR 작업 상태를 조회한다."""
        doc = FakeDocument()
        job = FakeOCRJob(status="queued", progress_percent=0)
        db = FakeDBSession(document=doc, ocr_job=job)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert body["progress_percent"] == 0
        assert body["error_message"] is None

    def test_get_status_processing(self):
        """processing 상태의 OCR 작업 상태를 조회한다."""
        doc = FakeDocument()
        job = FakeOCRJob(status="processing", progress_percent=50)
        db = FakeDBSession(document=doc, ocr_job=job)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "processing"

    def test_get_status_completed(self):
        """completed 상태의 OCR 작업 상태를 조회한다."""
        doc = FakeDocument()
        job = FakeOCRJob(status="completed", progress_percent=100)
        db = FakeDBSession(document=doc, ocr_job=job)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["progress_percent"] == 100

    def test_get_status_failed(self):
        """failed 상태의 OCR 작업 상태를 조회한다."""
        doc = FakeDocument()
        job = FakeOCRJob(status="failed", error_message="OCR 서비스 오류")
        db = FakeDBSession(document=doc, ocr_job=job)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error_message"] == "OCR 서비스 오류"

    def test_get_status_no_job(self):
        """OCR 작업이 없는 문서에 대해 404를 반환한다."""
        doc = FakeDocument()
        db = FakeDBSession(document=doc, ocr_job=None)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/status")

        assert resp.status_code == 404

    def test_get_status_document_not_found(self):
        """존재하지 않는 문서에 대해 404를 반환한다."""
        db = FakeDBSession(document=None)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/status")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/documents/{id}/ocr/result tests
# ---------------------------------------------------------------------------

class TestGetOCRResult:
    """GET /api/documents/{id}/ocr/result 엔드포인트 테스트."""

    def test_get_result_completed(self):
        """완료된 OCR 결과를 조회한다."""
        doc = FakeDocument(ocr_completed=True)
        job = FakeOCRJob(status="completed")
        results = [
            FakeOCRResult(page_number=1, extracted_text="Page 1 text"),
            FakeOCRResult(page_number=2, extracted_text="Page 2 text"),
        ]
        db = FakeDBSession(document=doc, ocr_job=job, ocr_results=results)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/result")

        assert resp.status_code == 200
        body = resp.json()
        assert body["document_id"] == str(FAKE_DOC_ID)
        assert len(body["pages"]) == 2
        assert body["pages"][0]["page_number"] == 1
        assert body["pages"][0]["text"] == "Page 1 text"
        assert len(body["pages"][0]["words"]) == 2

    def test_get_result_not_completed(self):
        """OCR이 완료되지 않은 경우 404를 반환한다."""
        doc = FakeDocument()
        job = FakeOCRJob(status="processing")
        db = FakeDBSession(document=doc, ocr_job=job)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/result")

        assert resp.status_code == 404

    def test_get_result_failed_includes_retry(self):
        """실패한 OCR 결과 조회 시 retry 옵션을 포함한다."""
        doc = FakeDocument()
        job = FakeOCRJob(status="failed", error_message="서비스 오류")
        db = FakeDBSession(document=doc, ocr_job=job)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/result")

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["error"]["retry"] is True

    def test_get_result_no_job(self):
        """OCR 작업이 없는 문서에 대해 404를 반환한다."""
        doc = FakeDocument()
        db = FakeDBSession(document=doc, ocr_job=None)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/result")

        assert resp.status_code == 404

    def test_viewer_can_get_result(self):
        """Viewer 역할도 OCR 결과를 조회할 수 있다."""
        doc = FakeDocument(ocr_completed=True)
        job = FakeOCRJob(status="completed")
        results = [FakeOCRResult()]
        db = FakeDBSession(document=doc, ocr_job=job, ocr_results=results)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/result")

        assert resp.status_code == 200

    def test_get_result_empty_pages(self):
        """OCR 결과가 빈 경우 빈 페이지 리스트를 반환한다."""
        doc = FakeDocument(ocr_completed=True)
        job = FakeOCRJob(status="completed")
        db = FakeDBSession(document=doc, ocr_job=job, ocr_results=[])
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)

        resp = client.get(f"/api/documents/{FAKE_DOC_ID}/ocr/result")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pages"] == []
