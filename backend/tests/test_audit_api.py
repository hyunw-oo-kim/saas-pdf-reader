"""Audit Log API 단위 테스트.

관리자 전용 엔드포인트, 날짜 범위/사용자/작업 유형 필터링,
50건 단위 페이지네이션을 테스트한다.
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

from app.routers.audit import router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeUser:
    user_id: str = "00000000-0000-0000-0000-000000000001"
    tenant_id: str = "00000000-0000-0000-0000-000000000010"
    role: str = "admin"
    email: str = "admin@example.com"


@dataclass
class FakeAuditLog:
    """In-memory audit log entry for testing."""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(
        default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000010")
    )
    user_id: uuid.UUID = field(
        default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000001")
    )
    action_type: str = "view"
    document_id: uuid.UUID | None = None
    ip_address: str = "127.0.0.1"
    details: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _make_logs(n: int, base_time: datetime | None = None, **overrides) -> list[FakeAuditLog]:
    """N개의 테스트 감사 로그를 생성한다."""
    if base_time is None:
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    actions = ["view", "upload", "delete", "share", "annotate", "rename", "ocr"]
    logs = []
    for i in range(n):
        kwargs = dict(
            id=uuid.uuid4(),
            action_type=actions[i % len(actions)],
            created_at=base_time + timedelta(hours=i),
        )
        kwargs.update(overrides)
        logs.append(FakeAuditLog(**kwargs))
    return logs


class FakeScalarResult:
    def __init__(self, items: list):
        self._items = items

    def all(self) -> list:
        return self._items


class FakeExecuteResult:
    def __init__(self, scalar_value=None, items: list | None = None):
        self._scalar_value = scalar_value
        self._items = items or []

    def scalar(self):
        return self._scalar_value

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._items)


class FakeAuditDBSession:
    """Fake async DB session for audit log queries."""

    def __init__(self, logs: list[FakeAuditLog]):
        self._logs = logs
        self._call_count = 0

    async def execute(self, stmt):
        self._call_count += 1

        # Compile statement to inspect filters
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))

        # Apply filters in-memory
        filtered = list(self._logs)

        # Filter by action_type
        if "action_type" in compiled and "=" in compiled:
            for action in ["view", "upload", "delete", "share", "annotate", "rename", "ocr"]:
                if f"'{action}'" in compiled:
                    filtered = [l for l in filtered if l.action_type == action]
                    break

        # Filter by user_id — UUID rendered without hyphens in compiled SQL
        if "user_id" in compiled and "WHERE" in compiled.upper():
            for uid in set(str(l.user_id) for l in self._logs):
                uid_no_hyphens = uid.replace("-", "")
                if uid_no_hyphens in compiled:
                    filtered = [l for l in filtered if str(l.user_id) == uid]
                    break

        # First call is COUNT, second is SELECT
        if self._call_count % 2 == 1:
            return FakeExecuteResult(scalar_value=len(filtered))

        # Sort by created_at desc (default)
        filtered.sort(key=lambda l: l.created_at, reverse=True)

        # Apply offset and limit
        offset = 0
        limit = len(filtered)
        if hasattr(stmt, '_offset_clause') and stmt._offset_clause is not None:
            offset = stmt._offset_clause.value
        if hasattr(stmt, '_limit_clause') and stmt._limit_clause is not None:
            limit = stmt._limit_clause.value

        sliced = filtered[offset:offset + limit]
        return FakeExecuteResult(items=sliced)

    def reset(self):
        self._call_count = 0


def _build_app(
    user: FakeUser | None = None,
    db_session: FakeAuditDBSession | None = None,
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

class TestAuditLogAccess:
    """관리자 전용 접근 제어 테스트."""

    def test_admin_can_access(self):
        """Admin 역할은 감사 로그를 조회할 수 있다."""
        db = FakeAuditDBSession([])
        client = _build_app(user=FakeUser(role="admin"), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 200

    def test_editor_forbidden(self):
        """Editor 역할은 감사 로그 조회가 거부된다."""
        db = FakeAuditDBSession([])
        client = _build_app(user=FakeUser(role="editor"), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 403

    def test_viewer_forbidden(self):
        """Viewer 역할은 감사 로그 조회가 거부된다."""
        db = FakeAuditDBSession([])
        client = _build_app(user=FakeUser(role="viewer"), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 403

    def test_unauthenticated_returns_401(self):
        """인증되지 않은 요청은 401을 반환한다."""
        db = FakeAuditDBSession([])
        client = _build_app(user=None, db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 401


class TestAuditLogPagination:
    """페이지네이션 테스트 (50건 단위)."""

    def test_empty_list(self):
        """로그가 없으면 빈 목록을 반환한다."""
        db = FakeAuditDBSession([])
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 50

    def test_default_page_size_is_50(self):
        """기본 페이지 크기는 50이다."""
        logs = _make_logs(60)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 50
        assert data["total"] == 60
        assert data["page_size"] == 50

    def test_second_page(self):
        """두 번째 페이지 조회 시 나머지 로그를 반환한다."""
        logs = _make_logs(60)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs?page=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 10
        assert data["page"] == 2

    def test_custom_page_size(self):
        """page_size 파라미터로 페이지 크기를 변경할 수 있다."""
        logs = _make_logs(20)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs?page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 10
        assert data["page_size"] == 10

    def test_beyond_last_page_returns_empty(self):
        """마지막 페이지를 넘어가면 빈 목록을 반환한다."""
        logs = _make_logs(5)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs?page=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 5


class TestAuditLogFiltering:
    """필터링 테스트 (작업 유형, 사용자 ID)."""

    def test_filter_by_action_type(self):
        """작업 유형으로 필터링할 수 있다."""
        logs = _make_logs(14)  # 2 of each action type
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs?action_type=view")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        for item in data["items"]:
            assert item["action_type"] == "view"

    def test_filter_by_user_id(self):
        """사용자 ID로 필터링할 수 있다."""
        user_a = uuid.UUID("00000000-0000-0000-0000-00000000000a")
        user_b = uuid.UUID("00000000-0000-0000-0000-00000000000b")
        logs = _make_logs(3, user_id=user_a) + _make_logs(2, user_id=user_b)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get(f"/api/audit-logs?user_id={user_a}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3

    def test_no_filter_returns_all(self):
        """필터 없이 조회하면 전체 로그를 반환한다."""
        logs = _make_logs(5)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5


class TestAuditLogResponseFormat:
    """응답 형식 테스트."""

    def test_response_contains_required_fields(self):
        """응답의 각 항목에 필수 필드가 포함된다."""
        logs = _make_logs(1)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert "id" in item
        assert "user_id" in item
        assert "action_type" in item
        assert "timestamp" in item
        assert "ip_address" in item

    def test_newest_first_ordering(self):
        """로그는 최신순으로 정렬된다."""
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        logs = _make_logs(5, base_time=base)
        db = FakeAuditDBSession(logs)
        client = _build_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/audit-logs")
        assert resp.status_code == 200
        items = resp.json()["items"]
        timestamps = [item["timestamp"] for item in items]
        assert timestamps == sorted(timestamps, reverse=True)
