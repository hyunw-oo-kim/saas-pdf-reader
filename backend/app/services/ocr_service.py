"""OCR Service — Azure AI Document Intelligence 연동.

OCR 상태 전이 로직과 Azure SDK 호출을 캡슐화한다.
상태 전이: queued → processing → completed/failed
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.models.ocr_job import OCRJob
from app.models.ocr_result import OCRResult

logger = logging.getLogger(__name__)

# Valid state transitions
VALID_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"processing"},
    "processing": {"completed", "failed"},
}


class InvalidStateTransition(Exception):
    """유효하지 않은 OCR 상태 전이."""
    pass


def validate_transition(current: str, target: str) -> None:
    """상태 전이가 유효한지 검증한다.

    Raises:
        InvalidStateTransition: 유효하지 않은 전이인 경우
    """
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransition(
            f"Invalid transition: {current} → {target}"
        )


async def transition_job(job: OCRJob, target_status: str, db: AsyncSession, **kwargs: Any) -> None:
    """OCR 작업의 상태를 전이한다.

    Args:
        job: OCR 작업 모델
        target_status: 목표 상태
        db: DB 세션
        **kwargs: 추가 필드 (error_message, progress_percent 등)
    """
    validate_transition(job.status, target_status)
    job.status = target_status

    if target_status in ("completed", "failed"):
        job.completed_at = datetime.now(timezone.utc)

    if "error_message" in kwargs:
        job.error_message = kwargs["error_message"]
    if "progress_percent" in kwargs:
        job.progress_percent = kwargs["progress_percent"]

    await db.commit()


class DocumentIntelligenceClient(Protocol):
    """Azure AI Document Intelligence 클라이언트 프로토콜."""

    async def analyze(self, blob_url: str) -> list[dict]:
        """문서를 분석하고 페이지별 결과를 반환한다."""
        ...


class AzureDocumentIntelligenceClient:
    """Azure AI Document Intelligence SDK 래퍼."""

    def __init__(self, endpoint: str | None = None, key: str | None = None):
        self._endpoint = endpoint or settings.azure_doc_intelligence_endpoint
        self._key = key or settings.azure_doc_intelligence_key

    async def analyze(self, blob_url: str) -> list[dict]:
        """Azure AI Document Intelligence로 문서를 분석한다.

        Returns:
            페이지별 결과 리스트: [{"page_number": int, "text": str, "words": [...]}]
        """
        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient as AzureClient
        from azure.core.credentials import AzureKeyCredential

        client = AzureClient(
            endpoint=self._endpoint,
            credential=AzureKeyCredential(self._key),
        )

        try:
            poller = await client.begin_analyze_document(
                "prebuilt-read",
                analyze_request={"url_source": blob_url},
            )
            result = await poller.result()
        finally:
            await client.close()

        pages = []
        if result.pages:
            for page in result.pages:
                words = []
                if page.words:
                    for word in page.words:
                        polygon = word.polygon or []
                        # Convert polygon to bounding box [x1, y1, x2, y2]
                        if len(polygon) >= 4:
                            xs = [polygon[i] for i in range(0, len(polygon), 2)]
                            ys = [polygon[i] for i in range(1, len(polygon), 2)]
                            bbox = [min(xs), min(ys), max(xs), max(ys)]
                        else:
                            bbox = [0.0, 0.0, 0.0, 0.0]

                        words.append({
                            "text": word.content or "",
                            "bounding_box": bbox,
                            "confidence": word.confidence or 0.0,
                        })

                page_text = page.lines[0].content if page.lines else "" if not page.words else " ".join(
                    w.content for w in page.words if w.content
                )
                pages.append({
                    "page_number": page.page_number or (len(pages) + 1),
                    "text": page_text,
                    "words": words,
                })

        return pages


# Module-level singleton
_ocr_client: DocumentIntelligenceClient | None = None


def get_ocr_client() -> DocumentIntelligenceClient:
    """OCR 클라이언트 싱글턴을 반환한다."""
    global _ocr_client
    if _ocr_client is None:
        _ocr_client = AzureDocumentIntelligenceClient()
    return _ocr_client


def set_ocr_client(client: DocumentIntelligenceClient) -> None:
    """테스트용: OCR 클라이언트를 교체한다."""
    global _ocr_client
    _ocr_client = client
