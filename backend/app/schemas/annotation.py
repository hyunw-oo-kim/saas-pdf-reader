"""Annotation API 스키마 — XFDF 형식 주석 데이터 관리."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AnnotationResponse(BaseModel):
    """주석 조회 응답."""

    document_id: uuid.UUID
    xfdf_data: str  # XFDF XML string
    updated_at: datetime


class AnnotationSaveRequest(BaseModel):
    """주석 저장/업데이트 요청 (XFDF 전체 교체)."""

    xfdf_data: str = Field(..., min_length=1)


class AnnotationSaveResponse(BaseModel):
    """주석 저장 응답."""

    document_id: uuid.UUID
    xfdf_data: str
    updated_at: datetime
