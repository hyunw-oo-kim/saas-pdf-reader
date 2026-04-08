"""Audit Log API - 감사 로그 조회 (/api/audit-logs).

관리자 전용 엔드포인트. 날짜 범위, 사용자, 작업 유형 기준 필터링 및
50건 단위 페이지네이션을 지원한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.rbac import Role, require_role
from app.models.audit_log import AuditLog
from app.schemas.audit import AuditLogEntry, AuditLogListResponse

router = APIRouter(prefix="/api/audit-logs", tags=["audit"])


@router.get(
    "",
    response_model=AuditLogListResponse,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def list_audit_logs(
    request: Request,
    start_date: Optional[datetime] = Query(None, description="시작 날짜 (ISO 8601)"),
    end_date: Optional[datetime] = Query(None, description="종료 날짜 (ISO 8601)"),
    user_id: Optional[uuid.UUID] = Query(None, description="사용자 ID"),
    action_type: Optional[str] = Query(None, description="작업 유형"),
    page: int = Query(1, ge=1, description="페이지 번호"),
    page_size: int = Query(50, ge=1, le=100, description="페이지 크기"),
    db: AsyncSession = Depends(get_db),
):
    """감사 로그 조회 (관리자 전용, 필터링 지원).

    - 날짜 범위 (start_date, end_date) 필터
    - 사용자 ID 필터
    - 작업 유형 필터 (view, upload, delete, share, annotate, rename, ocr)
    - 50건 단위 페이지네이션
    """
    # Build base filter conditions
    conditions = []
    if start_date is not None:
        conditions.append(AuditLog.created_at >= start_date)
    if end_date is not None:
        conditions.append(AuditLog.created_at <= end_date)
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if action_type is not None:
        conditions.append(AuditLog.action_type == action_type)

    # Count query
    count_stmt = select(func.count()).select_from(AuditLog)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Fetch paginated logs (newest first)
    offset = (page - 1) * page_size
    items_stmt = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    for cond in conditions:
        items_stmt = items_stmt.where(cond)
    items_result = await db.execute(items_stmt)
    logs = items_result.scalars().all()

    return AuditLogListResponse(
        items=[
            AuditLogEntry(
                id=log.id,
                user_id=log.user_id,
                action_type=log.action_type,
                document_id=log.document_id,
                timestamp=log.created_at,
                ip_address=log.ip_address,
                details=log.details,
            )
            for log in logs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
