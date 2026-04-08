"""Annotation API - XFDF 형식 주석 데이터 관리 (/api/documents/{id}/annotations).

- GET: 문서의 현재 사용자 주석 조회 (XFDF 형식) — Viewer 이상
- PUT: 주석 저장/업데이트 (XFDF 전체 교체, upsert) — Editor 이상
- DELETE: 개별 주석 삭제 — Editor 이상
"""

from __future__ import annotations

import logging
import uuid

from app.utils.uuid_helper import to_uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.rbac import Role, require_role
from app.models.annotation import Annotation
from app.models.document import Document
from app.schemas.annotation import (
    AnnotationResponse,
    AnnotationSaveRequest,
    AnnotationSaveResponse,
)
from app.utils.xfdf import empty_xfdf, remove_annotation_from_xfdf, validate_xfdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["annotations"])


async def _get_document_or_404(document_id: str, db: AsyncSession) -> Document:
    """문서를 조회하거나 404를 반환한다."""
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "요청한 문서를 찾을 수 없습니다"}},
        )
    stmt = select(Document).where(Document.id == doc_uuid)
    result = await db.execute(stmt)
    doc = result.scalars().first()
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "요청한 문서를 찾을 수 없습니다"}},
        )
    return doc


@router.get(
    "/{document_id}/annotations",
    response_model=AnnotationResponse,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_annotations(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """문서의 현재 사용자 주석 조회 (XFDF 형식)."""
    doc = await _get_document_or_404(document_id, db)
    user = request.state.user

    stmt = select(Annotation).where(
        Annotation.document_id == doc.id,
        Annotation.user_id == to_uuid(user.user_id),
    )
    result = await db.execute(stmt)
    annotation = result.scalars().first()

    if annotation is None:
        return AnnotationResponse(
            document_id=doc.id,
            xfdf_data=empty_xfdf(),
            updated_at=datetime.now(timezone.utc),
        )

    return AnnotationResponse(
        document_id=doc.id,
        xfdf_data=annotation.xfdf_data,
        updated_at=annotation.updated_at,
    )


@router.put(
    "/{document_id}/annotations",
    response_model=AnnotationSaveResponse,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def save_annotations(
    document_id: str,
    body: AnnotationSaveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """주석 저장/업데이트 (XFDF 전체 교체, upsert)."""
    doc = await _get_document_or_404(document_id, db)
    user = request.state.user

    if not validate_xfdf(body.xfdf_data):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_XFDF", "message": "유효하지 않은 XFDF 데이터입니다"}},
        )

    user_uuid = to_uuid(user.user_id)
    tenant_uuid = to_uuid(user.tenant_id)

    # Upsert: find existing or create new
    stmt = select(Annotation).where(
        Annotation.document_id == doc.id,
        Annotation.user_id == user_uuid,
    )
    result = await db.execute(stmt)
    annotation = result.scalars().first()

    now = datetime.now(timezone.utc)

    if annotation is not None:
        annotation.xfdf_data = body.xfdf_data
        annotation.updated_at = now
    else:
        annotation = Annotation(
            document_id=doc.id,
            tenant_id=tenant_uuid,
            user_id=user_uuid,
            xfdf_data=body.xfdf_data,
        )
        db.add(annotation)

    await db.commit()
    await db.refresh(annotation)

    return AnnotationSaveResponse(
        document_id=doc.id,
        xfdf_data=annotation.xfdf_data,
        updated_at=annotation.updated_at,
    )


@router.delete(
    "/{document_id}/annotations/{annotation_id}",
    status_code=204,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def delete_annotation(
    document_id: str,
    annotation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """개별 주석 삭제.

    annotation_id는 DB의 annotation row ID이다.
    해당 annotation이 현재 사용자의 것인지 확인 후 삭제한다.
    """
    doc = await _get_document_or_404(document_id, db)
    user = request.state.user

    try:
        annot_uuid = uuid.UUID(annotation_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "ANNOTATION_NOT_FOUND", "message": "주석을 찾을 수 없습니다"}},
        )

    stmt = select(Annotation).where(
        Annotation.id == annot_uuid,
        Annotation.document_id == doc.id,
        Annotation.user_id == to_uuid(user.user_id),
    )
    result = await db.execute(stmt)
    annotation = result.scalars().first()

    if annotation is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "ANNOTATION_NOT_FOUND", "message": "주석을 찾을 수 없습니다"}},
        )

    await db.delete(annotation)
    await db.commit()
