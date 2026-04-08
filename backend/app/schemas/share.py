"""Share API 스키마 — 공유 링크 생성, 조회, 접근."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ShareLinkCreateRequest(BaseModel):
    """공유 링크 생성 요청."""

    expiry: Literal["1h", "1d", "7d", "30d"]
    permission: Literal["read_only", "annotate"]


class ShareLinkResponse(BaseModel):
    """공유 링크 생성/조회 응답."""

    share_id: uuid.UUID
    share_url: str
    expires_at: datetime
    permission: str


class SharedDocumentResponse(BaseModel):
    """공유 링크를 통한 문서 접근 응답."""

    sas_url: str
    expires_at: datetime
    permission: str
    filename: str
