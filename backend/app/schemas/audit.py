"""Audit Log API 스키마 — 감사 로그 조회."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    """감사 로그 항목."""

    id: uuid.UUID
    user_id: uuid.UUID
    action_type: str
    document_id: Optional[uuid.UUID] = None
    timestamp: datetime
    ip_address: str
    details: Optional[dict] = None


class AuditLogListResponse(BaseModel):
    """감사 로그 목록 응답 (페이지네이션)."""

    items: list[AuditLogEntry]
    total: int
    page: int
    page_size: int
