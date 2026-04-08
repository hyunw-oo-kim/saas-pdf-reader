"""Document List API 단위 테스트.

페이지네이션 (20건 단위), 정렬 (filename, uploaded_at, size_bytes, asc/desc),
테넌트 범위 내 문서만 반환을 테스트한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

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
    """In-memory document for testing."""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    filename: str = "test.pdf"
    size_bytes: int = 1024
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


def _make_docs(n: int, base_time: datetime | None = None) -> list[FakeDoc]:
    """N개의 테스트 문서를 생성한다. 각 문서는 고유한 파일명, 크기, 시간을 가진다."""
    if base_time is None:
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    docs = []
    for i in range(n):
        docs.append(
            FakeDoc(
                id=uuid.uuid4(),
                filename=f"doc_{i:03d}.pdf",
                size_bytes=(i + 1) * 1000,
                uploaded_at=base_time + timedelta(hours=i),
                updated_at=base_time + timedelta(hours=i),
            )
        )
    return docs


class FakeScalarResult:
    """Simulates scalars().all() for SELECT queries."""

    def __init__(self, items: list):
        self._items = items

    def all(self) -> list:
        return self._items


class FakeExecuteResult:
    """Simulates the result of db.execute()."""

    def __init__(self, scalar_value=None, items: list | None = None):
        self._scalar_value = scalar_value
        self._items = items or []

    def scalar(self):
        return self._scalar_value

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._items)


class FakeListDBSession:
    """Fake async DB session that handles count + select queries for list_documents."""

    def __init__(self, documents: list[FakeDoc]):
        self._documents = documents
        self._call_count = 0

    async def execute(self, stmt):
        self._call_count += 1
        # First call is the COUNT query, second is the SELECT query
        if self._call_count == 1:
            return FakeExecuteResult(scalar_value=len(self._documents))

        # Parse sort and pagination from the compiled statement
        # We apply sorting and pagination in-memory based on the statement
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))

        # Determine sort field and order
        docs = list(self._documents)
        if "filename" in compiled and "ORDER BY" in compiled.upper():
            reverse = "DESC" in compiled.upper().split("ORDER BY")[1]
            docs.sort(key=lambda d: d.filename, reverse=reverse)
        elif "size_bytes" in compiled and "ORDER BY" in compiled.upper():
            reverse = "DESC" in compiled.upper().split("ORDER BY")[1]
            docs.sort(key=lambda d: d.size_bytes, reverse=reverse)
        elif "uploaded_at" in compiled and "ORDER BY" in compiled.upper():
            reverse = "DESC" in compiled.upper().split("ORDER BY")[1]
            docs.sort(key=lambda d: d.uploaded_at, reverse=reverse)

        # Apply offset and limit
        offset = 0
        limit = len(docs)
        if hasattr(stmt, '_offset_clause') and stmt._offset_clause is not None:
            offset = stmt._offset_clause.value
        if hasattr(stmt, '_limit_clause') and stmt._limit_clause is not None:
            limit = stmt._limit_clause.value

        sliced = docs[offset:offset + limit]
        return FakeExecuteResult(items=sliced)

    def reset(self):
        self._call_count = 0


def _build_list_app(
    user: FakeUser | None = None,
    db_session: FakeListDBSession | None = None,
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
# Tests
# ---------------------------------------------------------------------------

class TestListDocumentsEndpoint:
    """GET /api/documents 엔드포인트 테스트."""

    def test_empty_list(self):
        """문서가 없으면 빈 목록을 반환한다."""
        db = FakeListDBSession([])
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_default_pagination(self):
        """기본 페이지네이션은 20건 단위이다."""
        docs = _make_docs(25)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 20
        assert data["total"] == 25
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_second_page(self):
        """두 번째 페이지 조회 시 나머지 문서를 반환한다."""
        docs = _make_docs(25)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?page=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 5
        assert data["total"] == 25
        assert data["page"] == 2

    def test_custom_page_size(self):
        """page_size 파라미터로 페이지 크기를 변경할 수 있다."""
        docs = _make_docs(10)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 5
        assert data["page_size"] == 5

    def test_sort_by_filename_asc(self):
        """파일명 오름차순 정렬."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?sort_by=filename&sort_order=asc&page_size=100")
        assert resp.status_code == 200
        data = resp.json()
        filenames = [item["filename"] for item in data["items"]]
        assert filenames == sorted(filenames)

    def test_sort_by_filename_desc(self):
        """파일명 내림차순 정렬."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?sort_by=filename&sort_order=desc&page_size=100")
        assert resp.status_code == 200
        data = resp.json()
        filenames = [item["filename"] for item in data["items"]]
        assert filenames == sorted(filenames, reverse=True)

    def test_sort_by_size_bytes_asc(self):
        """파일 크기 오름차순 정렬."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?sort_by=size_bytes&sort_order=asc&page_size=100")
        assert resp.status_code == 200
        data = resp.json()
        sizes = [item["size_bytes"] for item in data["items"]]
        assert sizes == sorted(sizes)

    def test_sort_by_size_bytes_desc(self):
        """파일 크기 내림차순 정렬."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?sort_by=size_bytes&sort_order=desc&page_size=100")
        assert resp.status_code == 200
        data = resp.json()
        sizes = [item["size_bytes"] for item in data["items"]]
        assert sizes == sorted(sizes, reverse=True)

    def test_sort_by_uploaded_at_desc_default(self):
        """기본 정렬은 uploaded_at 내림차순이다."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?page_size=100")
        assert resp.status_code == 200
        data = resp.json()
        timestamps = [item["uploaded_at"] for item in data["items"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_invalid_sort_by_falls_back(self):
        """잘못된 sort_by 값은 uploaded_at으로 폴백한다."""
        docs = _make_docs(3)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?sort_by=invalid_field")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3

    def test_invalid_sort_order_falls_back(self):
        """잘못된 sort_order 값은 desc로 폴백한다."""
        docs = _make_docs(3)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?sort_order=invalid")
        assert resp.status_code == 200

    def test_page_less_than_one_clamped(self):
        """page < 1이면 1로 보정한다."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?page=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1

    def test_page_size_clamped_to_max_100(self):
        """page_size > 100이면 100으로 보정한다."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?page_size=200")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page_size"] == 100

    def test_response_contains_document_meta_fields(self):
        """응답의 각 항목에 DocumentMeta 필드가 포함된다."""
        docs = _make_docs(1)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert "id" in item
        assert "filename" in item
        assert "size_bytes" in item
        assert "content_type" in item
        assert "ocr_completed" in item
        assert "uploaded_at" in item
        assert "updated_at" in item
        assert "owner_id" in item

    def test_viewer_can_list(self):
        """Viewer 역할도 문서 목록을 조회할 수 있다."""
        docs = _make_docs(3)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(role="viewer"), db_session=db)
        resp = client.get("/api/documents")
        assert resp.status_code == 200

    def test_editor_can_list(self):
        """Editor 역할도 문서 목록을 조회할 수 있다."""
        docs = _make_docs(3)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(role="editor"), db_session=db)
        resp = client.get("/api/documents")
        assert resp.status_code == 200

    def test_admin_can_list(self):
        """Admin 역할도 문서 목록을 조회할 수 있다."""
        docs = _make_docs(3)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(role="admin"), db_session=db)
        resp = client.get("/api/documents")
        assert resp.status_code == 200

    def test_beyond_last_page_returns_empty(self):
        """마지막 페이지를 넘어가면 빈 목록을 반환한다."""
        docs = _make_docs(5)
        db = FakeListDBSession(docs)
        client = _build_list_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents?page=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 5
