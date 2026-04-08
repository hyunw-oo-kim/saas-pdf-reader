"""OCR API — Azure AI Document Intelligence 연동 (/api/documents/{id}/ocr).

POST: OCR 작업 시작 (비동기, 백그라운드 워커)
GET /status: 진행 상태 조회
GET /result: OCR 결과 조회 (텍스트 + 좌표)
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.dependencies.rbac import Role, require_role
from app.models.document import Document
from app.models.ocr_job import OCRJob
from app.models.ocr_result import OCRResult
from app.schemas.ocr import (
    OCRJobResponse,
    OCRPageResult,
    OCRResultResponse,
    OCRStatusResponse,
    OCRWord,
)
from app.services.blob_storage import get_blob_service
from app.utils.uuid_helper import to_uuid
from app.services.ocr_service import (
    get_ocr_client,
    transition_job,
    InvalidStateTransition,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["ocr"])


def _parse_uuid(document_id: str) -> uuid.UUID:
    """문서 ID를 UUID로 파싱한다."""
    try:
        return uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "요청한 문서를 찾을 수 없습니다"}},
        )


async def _get_document(document_id: uuid.UUID, db: AsyncSession) -> Document:
    """문서를 조회한다. 없으면 404."""
    stmt = select(Document).where(Document.id == document_id)
    result = await db.execute(stmt)
    doc = result.scalars().first()
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "DOCUMENT_NOT_FOUND", "message": "요청한 문서를 찾을 수 없습니다"}},
        )
    return doc


async def _get_latest_job(document_id: uuid.UUID, db: AsyncSession) -> OCRJob:
    """문서의 최신 OCR 작업을 조회한다. 없으면 404."""
    stmt = (
        select(OCRJob)
        .where(OCRJob.document_id == document_id)
        .order_by(OCRJob.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    job = result.scalars().first()
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "OCR_JOB_NOT_FOUND", "message": "OCR 작업을 찾을 수 없습니다"}},
        )
    return job


async def _run_ocr_background(job_id: uuid.UUID, document_id: uuid.UUID, tenant_id: str) -> None:
    """백그라운드에서 OCR 처리를 수행한다.

    별도의 DB 세션을 사용하여 상태를 업데이트한다.
    """
    async with async_session() as db:
        # Fetch job
        stmt = select(OCRJob).where(OCRJob.id == job_id)
        result = await db.execute(stmt)
        job = result.scalars().first()
        if job is None:
            logger.error("OCR background: job %s not found", job_id)
            return

        # Transition to processing
        try:
            await transition_job(job, "processing", db, progress_percent=0)
        except InvalidStateTransition:
            logger.error("OCR background: invalid transition for job %s", job_id)
            return

        # Get document blob path for SAS URL
        doc_stmt = select(Document).where(Document.id == document_id)
        doc_result = await db.execute(doc_stmt)
        doc = doc_result.scalars().first()
        if doc is None:
            await transition_job(job, "failed", db, error_message="문서를 찾을 수 없습니다")
            return

        try:
            # 로컬 파일 경로를 사용하여 OCR 처리
            blob_service = get_blob_service()
            file_path = str(blob_service.get_file_path(doc.blob_path))

            # Call Azure AI Document Intelligence (or local OCR)
            ocr_client = get_ocr_client()
            pages = await ocr_client.analyze(file_path)

            # Save results
            for page_data in pages:
                ocr_result = OCRResult(
                    document_id=document_id,
                    tenant_id=to_uuid(tenant_id),
                    page_number=page_data["page_number"],
                    extracted_text=page_data["text"],
                    words=page_data["words"],
                )
                db.add(ocr_result)

            # Update document ocr_completed flag
            doc.ocr_completed = True

            # Transition to completed
            await transition_job(job, "completed", db, progress_percent=100)

        except Exception as exc:
            logger.exception("OCR processing failed for job %s", job_id)
            await db.rollback()
            # Re-fetch job after rollback
            result = await db.execute(select(OCRJob).where(OCRJob.id == job_id))
            job = result.scalars().first()
            if job:
                try:
                    await transition_job(
                        job, "failed", db,
                        error_message=f"OCR 처리 실패: {str(exc)[:500]}",
                    )
                except Exception:
                    logger.exception("Failed to transition job %s to failed state", job_id)


@router.post(
    "/{document_id}/ocr",
    response_model=OCRJobResponse,
    status_code=202,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def start_ocr(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """OCR 작업 시작 (비동기).

    - Editor 이상 역할 필요
    - 문서가 존재하는지 확인
    - OCR 작업을 queued 상태로 생성
    - 백그라운드 태스크로 OCR 처리 시작
    """
    doc_uuid = _parse_uuid(document_id)
    doc = await _get_document(doc_uuid, db)
    tenant_id = request.state.tenant_id

    # Create OCR job
    job = OCRJob(
        document_id=doc_uuid,
        tenant_id=to_uuid(tenant_id),
        status="queued",
        progress_percent=0,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Start background OCR processing
    asyncio.create_task(_run_ocr_background(job.id, doc_uuid, tenant_id))

    return OCRJobResponse(job_id=job.id, status=job.status)


@router.get(
    "/{document_id}/ocr/status",
    response_model=OCRStatusResponse,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_ocr_status(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """OCR 진행 상태 조회.

    - Viewer 이상 역할 필요
    - 문서의 최신 OCR 작업 상태를 반환
    """
    doc_uuid = _parse_uuid(document_id)
    await _get_document(doc_uuid, db)
    job = await _get_latest_job(doc_uuid, db)

    return OCRStatusResponse(
        job_id=job.id,
        status=job.status,
        progress_percent=job.progress_percent,
        error_message=job.error_message,
    )


@router.get(
    "/{document_id}/ocr/result",
    response_model=OCRResultResponse,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_ocr_result(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """OCR 결과 조회 (텍스트 + 좌표).

    - Viewer 이상 역할 필요
    - OCR이 완료된 문서의 결과를 반환
    - 완료되지 않은 경우 404 반환
    """
    doc_uuid = _parse_uuid(document_id)
    await _get_document(doc_uuid, db)

    # Check if OCR is completed
    job = await _get_latest_job(doc_uuid, db)
    if job.status != "completed":
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "OCR_NOT_COMPLETED",
                    "message": "OCR 처리가 완료되지 않았습니다",
                    "retry": job.status == "failed",
                }
            },
        )

    # Fetch OCR results
    stmt = (
        select(OCRResult)
        .where(OCRResult.document_id == doc_uuid)
        .order_by(OCRResult.page_number)
    )
    result = await db.execute(stmt)
    ocr_results = result.scalars().all()

    pages = [
        OCRPageResult(
            page_number=r.page_number,
            text=r.extracted_text,
            words=[OCRWord(**w) for w in (r.words or [])],
        )
        for r in ocr_results
    ]

    return OCRResultResponse(document_id=doc_uuid, pages=pages)
