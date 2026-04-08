"""Annotation API 단위 테스트.

GET/PUT/DELETE 엔드포인트, XFDF 유틸리티 함수, RBAC 검증을 테스트한다.
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

from app.routers.annotations import router
from app.utils.xfdf import validate_xfdf, empty_xfdf, remove_annotation_from_xfdf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_ID = "00000000-0000-0000-0000-000000000010"
USER_ID = "00000000-0000-0000-0000-000000000001"
DOC_ID = "00000000-0000-0000-0000-000000000100"
ANNOT_ID = "00000000-0000-0000-0000-000000000200"

VALID_XFDF = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<xfdf xmlns="http://ns.adobe.com/xfdf/" xml:space="preserve">'
    "<annots>"
    '<highlight name="annot1" page="0" rect="100,200,300,400" color="#FFFF00"/>'
    "</annots>"
    "</xfdf>"
)


@dataclass
class FakeUser:
    user_id: str = USER_ID
    tenant_id: str = TENANT_ID
    role: str = "editor"
    email: str = "editor@example.com"


class FakeAnnotation:
    """DB annotation row를 시뮬레이션한다."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.UUID(ANNOT_ID))
        self.document_id = kwargs.get("document_id", uuid.UUID(DOC_ID))
        self.tenant_id = kwargs.get("tenant_id", uuid.UUID(TENANT_ID))
        self.user_id = kwargs.get("user_id", uuid.UUID(USER_ID))
        self.xfdf_data = kwargs.get("xfdf_data", VALID_XFDF)
        self.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        self.updated_at = kwargs.get("updated_at", datetime.now(timezone.utc))


class FakeDocument:
    """DB document row를 시뮬레이션한다."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.UUID(DOC_ID))
        self.tenant_id = kwargs.get("tenant_id", uuid.UUID(TENANT_ID))


class FakeScalarsResult:
    def __init__(self, items):
        self._items = items
        self._idx = 0

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return self._items


class FakeExecuteResult:
    def __init__(self, items):
        self._scalars = FakeScalarsResult(items)

    def scalars(self):
        return self._scalars


class FakeDBSession:
    """테스트용 비동기 DB 세션."""

    def __init__(
        self,
        document: FakeDocument | None = None,
        annotation: FakeAnnotation | None = None,
    ):
        self._document = document
        self._annotation = annotation
        self._call_count = 0
        self.added: list = []
        self.deleted: list = []
        self.committed = False

    async def execute(self, stmt):
        self._call_count += 1
        # First query is always the document lookup
        if self._call_count == 1:
            items = [self._document] if self._document else []
        else:
            items = [self._annotation] if self._annotation else []
        return FakeExecuteResult(items)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if not hasattr(obj, "updated_at") or obj.updated_at is None:
            obj.updated_at = datetime.now(timezone.utc)

    async def rollback(self):
        pass


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
# XFDF Utility Tests
# ---------------------------------------------------------------------------

class TestValidateXfdf:
    """XFDF 검증 유틸리티 테스트."""

    def test_valid_xfdf(self):
        assert validate_xfdf(VALID_XFDF) is True

    def test_empty_xfdf_template(self):
        assert validate_xfdf(empty_xfdf()) is True

    def test_invalid_xml(self):
        assert validate_xfdf("not xml at all") is False

    def test_wrong_root_element(self):
        assert validate_xfdf("<html><body/></html>") is False

    def test_xfdf_without_namespace(self):
        assert validate_xfdf("<xfdf><annots/></xfdf>") is True


class TestRemoveAnnotationFromXfdf:
    """XFDF에서 개별 annotation 제거 테스트."""

    def test_remove_existing_annotation(self):
        result = remove_annotation_from_xfdf(VALID_XFDF, "annot1")
        assert result is not None
        assert "annot1" not in result

    def test_remove_nonexistent_annotation(self):
        result = remove_annotation_from_xfdf(VALID_XFDF, "nonexistent")
        assert result is None

    def test_remove_from_invalid_xml(self):
        result = remove_annotation_from_xfdf("not xml", "annot1")
        assert result is None


class TestEmptyXfdf:
    """빈 XFDF 생성 테스트."""

    def test_empty_xfdf_is_valid(self):
        xfdf = empty_xfdf()
        assert validate_xfdf(xfdf) is True
        assert "annots" in xfdf


# ---------------------------------------------------------------------------
# GET /api/documents/{id}/annotations
# ---------------------------------------------------------------------------

class TestGetAnnotations:
    """GET 주석 조회 엔드포인트 테스트."""

    def test_returns_existing_annotation(self):
        """저장된 주석이 있으면 XFDF 데이터를 반환한다."""
        db = FakeDBSession(
            document=FakeDocument(),
            annotation=FakeAnnotation(),
        )
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.get(f"/api/documents/{DOC_ID}/annotations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == DOC_ID
        assert "xfdf" in data["xfdf_data"].lower()

    def test_returns_empty_xfdf_when_no_annotation(self):
        """저장된 주석이 없으면 빈 XFDF를 반환한다."""
        db = FakeDBSession(document=FakeDocument(), annotation=None)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.get(f"/api/documents/{DOC_ID}/annotations")
        assert resp.status_code == 200
        data = resp.json()
        assert "annots" in data["xfdf_data"]

    def test_document_not_found(self):
        """존재하지 않는 문서는 404를 반환한다."""
        db = FakeDBSession(document=None)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.get(f"/api/documents/{DOC_ID}/annotations")
        assert resp.status_code == 404

    def test_invalid_document_id(self):
        """잘못된 문서 ID는 404를 반환한다."""
        db = FakeDBSession(document=None)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.get("/api/documents/not-a-uuid/annotations")
        assert resp.status_code == 404

    def test_viewer_can_read(self):
        """Viewer 역할도 주석을 조회할 수 있다."""
        db = FakeDBSession(document=FakeDocument(), annotation=None)
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)
        resp = client.get(f"/api/documents/{DOC_ID}/annotations")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PUT /api/documents/{id}/annotations
# ---------------------------------------------------------------------------

class TestSaveAnnotations:
    """PUT 주석 저장 엔드포인트 테스트."""

    def test_create_new_annotation(self):
        """주석이 없을 때 새로 생성한다."""
        db = FakeDBSession(document=FakeDocument(), annotation=None)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.put(
            f"/api/documents/{DOC_ID}/annotations",
            json={"xfdf_data": VALID_XFDF},
        )
        assert resp.status_code == 200
        assert db.committed is True
        assert len(db.added) == 1

    def test_update_existing_annotation(self):
        """기존 주석이 있으면 업데이트한다."""
        annot = FakeAnnotation()
        db = FakeDBSession(document=FakeDocument(), annotation=annot)
        client = _build_test_app(user=FakeUser(), db_session=db)
        new_xfdf = empty_xfdf()
        resp = client.put(
            f"/api/documents/{DOC_ID}/annotations",
            json={"xfdf_data": new_xfdf},
        )
        assert resp.status_code == 200
        assert annot.xfdf_data == new_xfdf

    def test_invalid_xfdf_rejected(self):
        """유효하지 않은 XFDF 데이터는 400을 반환한다."""
        db = FakeDBSession(document=FakeDocument())
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.put(
            f"/api/documents/{DOC_ID}/annotations",
            json={"xfdf_data": "not valid xml"},
        )
        assert resp.status_code == 400

    def test_empty_xfdf_rejected(self):
        """빈 xfdf_data는 422를 반환한다 (pydantic validation)."""
        db = FakeDBSession(document=FakeDocument())
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.put(
            f"/api/documents/{DOC_ID}/annotations",
            json={"xfdf_data": ""},
        )
        assert resp.status_code == 422

    def test_viewer_cannot_save(self):
        """Viewer 역할은 주석을 저장할 수 없다."""
        db = FakeDBSession(document=FakeDocument())
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)
        resp = client.put(
            f"/api/documents/{DOC_ID}/annotations",
            json={"xfdf_data": VALID_XFDF},
        )
        assert resp.status_code == 403

    def test_document_not_found(self):
        """존재하지 않는 문서는 404를 반환한다."""
        db = FakeDBSession(document=None)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.put(
            f"/api/documents/{DOC_ID}/annotations",
            json={"xfdf_data": VALID_XFDF},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/documents/{id}/annotations/{annotation_id}
# ---------------------------------------------------------------------------

class TestDeleteAnnotation:
    """DELETE 개별 주석 삭제 엔드포인트 테스트."""

    def test_delete_existing_annotation(self):
        """존재하는 주석을 삭제한다."""
        annot = FakeAnnotation()
        db = FakeDBSession(document=FakeDocument(), annotation=annot)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.delete(f"/api/documents/{DOC_ID}/annotations/{ANNOT_ID}")
        assert resp.status_code == 204
        assert db.committed is True
        assert len(db.deleted) == 1

    def test_delete_nonexistent_annotation(self):
        """존재하지 않는 주석 삭제 시 404를 반환한다."""
        db = FakeDBSession(document=FakeDocument(), annotation=None)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.delete(f"/api/documents/{DOC_ID}/annotations/{ANNOT_ID}")
        assert resp.status_code == 404

    def test_invalid_annotation_id(self):
        """잘못된 annotation ID는 404를 반환한다."""
        db = FakeDBSession(document=FakeDocument())
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.delete(f"/api/documents/{DOC_ID}/annotations/not-a-uuid")
        assert resp.status_code == 404

    def test_viewer_cannot_delete(self):
        """Viewer 역할은 주석을 삭제할 수 없다."""
        db = FakeDBSession(document=FakeDocument(), annotation=FakeAnnotation())
        client = _build_test_app(user=FakeUser(role="viewer"), db_session=db)
        resp = client.delete(f"/api/documents/{DOC_ID}/annotations/{ANNOT_ID}")
        assert resp.status_code == 403

    def test_document_not_found(self):
        """존재하지 않는 문서는 404를 반환한다."""
        db = FakeDBSession(document=None)
        client = _build_test_app(user=FakeUser(), db_session=db)
        resp = client.delete(f"/api/documents/{DOC_ID}/annotations/{ANNOT_ID}")
        assert resp.status_code == 404
