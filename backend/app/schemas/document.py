"""Document API 스키마 — 업로드, 목록 조회, 수정, 삭제."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    """문서 업로드 응답."""

    id: uuid.UUID
    filename: str
    size_bytes: int
    uploaded_at: datetime


class DocumentMeta(BaseModel):
    """문서 메타데이터 (목록 조회용)."""

    id: uuid.UUID
    filename: str
    size_bytes: int
    content_type: str
    ocr_completed: bool
    uploaded_at: datetime
    updated_at: datetime
    owner_id: uuid.UUID


class DocumentListResponse(BaseModel):
    """문서 목록 응답 (페이지네이션)."""

    items: list[DocumentMeta]
    total: int
    page: int
    page_size: int


class DocumentUpdateRequest(BaseModel):
    """문서 수정 요청 (파일명 변경)."""

    filename: str = Field(..., min_length=1, max_length=500)

class DocumentViewResponse(BaseModel):
    """문서 열람 응답 (SAS Token URL)."""

    sas_url: str
    expires_at: datetime

