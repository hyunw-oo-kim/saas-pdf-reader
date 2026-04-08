"""OCR API 스키마 — 작업 시작, 상태 조회, 결과 조회."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class OCRJobResponse(BaseModel):
    """OCR 작업 시작 응답."""

    job_id: uuid.UUID
    status: Literal["queued", "processing", "completed", "failed"]


class OCRStatusResponse(BaseModel):
    """OCR 진행 상태 조회 응답."""

    job_id: uuid.UUID
    status: Literal["queued", "processing", "completed", "failed"]
    progress_percent: Optional[int] = None
    error_message: Optional[str] = None


class OCRWord(BaseModel):
    """OCR 추출 단어."""

    text: str
    bounding_box: list[float]  # [x1, y1, x2, y2]
    confidence: float


class OCRPageResult(BaseModel):
    """OCR 페이지 결과."""

    page_number: int
    text: str
    words: list[OCRWord]


class OCRResultResponse(BaseModel):
    """OCR 결과 조회 응답."""

    document_id: uuid.UUID
    pages: list[OCRPageResult]
