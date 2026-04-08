"""Share API - 문서 공유 링크 생성 및 관리 (/api/documents/{id}/share)."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.rbac import Role, require_role
from app.models.document import Document
from app.models.share_link import ShareLink
from app.schemas.share import (
    ShareLinkCreateRequest,
    ShareLinkResponse,
    SharedDocumentResponse,
)
from app.services.blob_storage import get_blob_service
from app.utils.uuid_helper import to_uuid

router = APIRouter(tags=["share"])

# Expiry string → timedelta mapping
EXPIRY_MAP: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _parse_uuid(value: str, label: str = "ID") -> uuid.UUID:
    """문자열을 UUID로 변환한다. 실패 시 404."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": f"요청한 {label}을(를) 찾을 수 없습니다"}},
        )


@router.post(
    "/api/documents/{document_id}/share",
    response_model=ShareLinkResponse,
    status_code=201,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def create_share_link(
    document_id: str,
    body: ShareLinkCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """공유 링크 생성 (만료 시간 + 권한 설정).

    - Editor 이상 역할 필요
    - 만료: 1h, 1d, 7d, 30d
    - 권한: read_only, annotate
    """
    doc_uuid = _parse_uuid(document_id, "문서")
    user = request.state.user
    tenant_id = request.state.tenant_id

    # 문서 존재 확인
    stmt = select(Document).where(Document.id == doc_uuid)
    result = await db.execute(stmt)
    doc = result.scalars().first()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "요청한 문서를 찾을 수 없습니다"}},
        )

    # 공유 링크 생성
    now = datetime.now(timezone.utc)
    delta = EXPIRY_MAP[body.expiry]
    expires_at = now + delta
    share_token = secrets.token_urlsafe(32)

    share_link = ShareLink(
        id=uuid.uuid4(),
        document_id=doc_uuid,
        tenant_id=to_uuid(tenant_id),
        created_by=to_uuid(user.user_id),
        share_token=share_token,
        permission=body.permission,
        expires_at=expires_at,
        is_active=True,
    )

    db.add(share_link)
    await db.commit()
    await db.refresh(share_link)

    share_url = f"/api/shared/{share_token}"

    return ShareLinkResponse(
        share_id=share_link.id,
        share_url=share_url,
        expires_at=share_link.expires_at,
        permission=share_link.permission,
    )


@router.delete(
    "/api/documents/{document_id}/share/{share_id}",
    status_code=204,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def revoke_share_link(
    document_id: str,
    share_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """공유 링크 즉시 무효화."""
    doc_uuid = _parse_uuid(document_id, "문서")
    share_uuid = _parse_uuid(share_id, "공유 링크")

    stmt = select(ShareLink).where(
        ShareLink.id == share_uuid,
        ShareLink.document_id == doc_uuid,
    )
    result = await db.execute(stmt)
    share_link = result.scalars().first()

    if share_link is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "SHARE_LINK_NOT_FOUND", "message": "공유 링크를 찾을 수 없습니다"}},
        )

    share_link.is_active = False
    await db.commit()

@router.get(
    "/api/documents/{document_id}/share",
    response_model=list[ShareLinkResponse],
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def list_share_links(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """문서의 활성 공유 링크 목록 조회."""
    doc_uuid = _parse_uuid(document_id, "문서")

    stmt = select(ShareLink).where(
        ShareLink.document_id == doc_uuid,
        ShareLink.is_active.is_(True),
    )
    result = await db.execute(stmt)
    links = result.scalars().all()

    return [
        ShareLinkResponse(
            share_id=link.id,
            share_url=f"/api/shared/{link.share_token}",
            expires_at=link.expires_at,
            permission=link.permission,
        )
        for link in links
    ]




@router.get(
    "/api/shared/{share_token}",
    response_model=SharedDocumentResponse,
)
async def access_shared_document(
    share_token: str,
    db: AsyncSession = Depends(get_db),
):
    """공유 링크를 통한 문서 접근 (인증 불필요).

    - is_active=True AND expires_at > now 검증
    - SAS URL 반환
    """
    stmt = select(ShareLink).where(ShareLink.share_token == share_token)
    result = await db.execute(stmt)
    share_link = result.scalars().first()

    if share_link is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "SHARE_LINK_NOT_FOUND", "message": "공유 링크를 찾을 수 없습니다"}},
        )

    # 취소된 링크
    if not share_link.is_active:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "SHARE_LINK_REVOKED", "message": "링크가 무효화되었습니다"}},
        )

    # 만료된 링크
    now = datetime.now(timezone.utc)
    expires_at = share_link.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "SHARE_LINK_EXPIRED", "message": "링크가 만료되었습니다"}},
        )

    # 문서 조회
    doc_stmt = select(Document).where(Document.id == share_link.document_id)
    doc_result = await db.execute(doc_stmt)
    doc = doc_result.scalars().first()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "요청한 문서를 찾을 수 없습니다"}},
        )

    blob_service = get_blob_service()
    sas_url, sas_expires_at = blob_service.generate_sas_url(doc.blob_path)

    return SharedDocumentResponse(
        sas_url=sas_url,
        expires_at=sas_expires_at,
        permission=share_link.permission,
        filename=doc.filename,
    )
