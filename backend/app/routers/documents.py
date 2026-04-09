"""Document API - 문서 CRUD 및 Blob Storage 연동 (/api/documents)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.rbac import Role, require_role
from app.models.document import Document
from app.schemas.document import (
    DocumentListResponse,
    DocumentMeta,
    DocumentUpdateRequest,
    DocumentUploadResponse,
    DocumentViewResponse,
)
from app.services.blob_storage import get_blob_service
from app.utils.uuid_helper import to_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


# PDF magic bytes: %PDF (hex: 25 50 44 46)
PDF_MAGIC_BYTES = b"%PDF"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def _validate_pdf(content_type: str | None, file_header: bytes) -> None:
    """PDF 형식을 검증한다 (MIME 타입 + 매직 바이트).

    Raises:
        HTTPException(400): PDF 형식이 아닌 경우
    """
    # MIME type check
    if content_type and content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_FILE_FORMAT",
                    "message": "PDF 형식만 업로드 가능합니다",
                }
            },
        )

    # Magic bytes check (first 4 bytes must be %PDF)
    if len(file_header) < 4 or file_header[:4] != PDF_MAGIC_BYTES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "INVALID_FILE_FORMAT",
                    "message": "PDF 형식만 업로드 가능합니다",
                }
            },
        )


def _validate_file_size(size: int) -> None:
    """파일 크기를 검증한다 (100MB 제한).

    Raises:
        HTTPException(413): 100MB 초과 시
    """
    if size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail={
                "error": {
                    "code": "FILE_TOO_LARGE",
                    "message": "파일 크기가 100MB를 초과합니다",
                }
            },
        )


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=201,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """PDF 문서 업로드.

    - PDF 형식 검증 (MIME 타입 + 매직 바이트)
    - 100MB 파일 크기 제한
    - 10MB 초과 시 멀티파트(블록) 업로드
    - Azure Blob Storage에 테넌트별 경로로 저장
    - Metadata_DB에 메타데이터 기록
    - 실패 시 Blob/DB 정리
    """
    user = request.state.user
    tenant_id = request.state.tenant_id

    # Read file content
    file_data = await file.read()
    file_size = len(file_data)

    # Validate file size
    _validate_file_size(file_size)

    # Validate PDF format (MIME type + magic bytes)
    _validate_pdf(file.content_type, file_data)

    # Generate document ID and blob path
    document_id = uuid.uuid4()
    blob_service = get_blob_service()
    blob_path = blob_service.build_blob_path(tenant_id, document_id)

    # Upload to Blob Storage
    try:
        await blob_service.upload_blob(blob_path, file_data)
    except Exception:
        logger.exception("Blob upload failed for document %s", document_id)
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "STORAGE_ERROR",
                    "message": "스토리지 서비스 오류",
                }
            },
        )

    # Save metadata to DB
    doc = Document(
        id=document_id,
        tenant_id=to_uuid(tenant_id),
        owner_id=to_uuid(user.user_id),
        filename=file.filename or "untitled.pdf",
        size_bytes=file_size,
        blob_path=blob_path,
        content_type="application/pdf",
    )

    try:
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
    except Exception:
        logger.exception("DB insert failed for document %s, cleaning up blob", document_id)
        # Clean up blob if DB insert fails
        try:
            await blob_service.delete_blob(blob_path)
        except Exception:
            logger.exception("Blob cleanup also failed for document %s", document_id)
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "UPLOAD_FAILED",
                    "message": "업로드 중 오류가 발생했습니다",
                }
            },
        )

    return DocumentUploadResponse(
        id=doc.id,
        filename=doc.filename,
        size_bytes=doc.size_bytes,
        uploaded_at=doc.uploaded_at,
    )



@router.get(
    "",
    response_model=DocumentListResponse,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def list_documents(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "uploaded_at",
    sort_order: str = "desc",
    db: AsyncSession = Depends(get_db),
):
    """문서 목록 조회 (페이지네이션, 정렬).

    - 페이지네이션: 기본 20건 단위
    - 정렬: filename, uploaded_at, size_bytes 기준 asc/desc
    - 테넌트 범위 내 문서만 반환 (RLS 자동 적용)
    """
    # Validate query params
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    elif page_size > 100:
        page_size = 100

    allowed_sort_fields = {"filename", "uploaded_at", "size_bytes"}
    if sort_by not in allowed_sort_fields:
        sort_by = "uploaded_at"

    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    # Determine sort column
    sort_column = getattr(Document, sort_by)
    if sort_order == "desc":
        sort_column = sort_column.desc()
    else:
        sort_column = sort_column.asc()

    # Filter by current user's owner_id
    owner_uuid = to_uuid(request.state.user.user_id)

    # Count total documents for this owner
    count_stmt = select(func.count()).select_from(Document).where(
        Document.owner_id == owner_uuid
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Fetch paginated + sorted documents for this owner
    offset = (page - 1) * page_size
    items_stmt = (
        select(Document)
        .where(Document.owner_id == owner_uuid)
        .order_by(sort_column)
        .offset(offset)
        .limit(page_size)
    )
    items_result = await db.execute(items_stmt)
    documents = items_result.scalars().all()

    return DocumentListResponse(
        items=[
            DocumentMeta(
                id=doc.id,
                filename=doc.filename,
                size_bytes=doc.size_bytes,
                content_type=doc.content_type,
                ocr_completed=doc.ocr_completed,
                uploaded_at=doc.uploaded_at,
                updated_at=doc.updated_at,
                owner_id=doc.owner_id,
            )
            for doc in documents
        ],
        total=total,
        page=page,
        page_size=page_size,
    )




@router.get(
    "/{document_id}/view",
    response_model=DocumentViewResponse,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def view_document(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """문서 열람용 SAS Token URL 생성.

    - Viewer 이상 역할 필요 (모든 역할 가능)
    - 테넌트 범위 내 문서만 조회 (RLS 자동 적용)
    - 15분 TTL의 SAS Token 생성
    """
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "요청한 문서를 찾을 수 없습니다",
                }
            },
        )

    stmt = select(Document).where(Document.id == doc_uuid)
    result = await db.execute(stmt)
    doc = result.scalars().first()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "요청한 문서를 찾을 수 없습니다",
                }
            },
        )

    blob_service = get_blob_service()
    sas_url, expires_at = blob_service.generate_sas_url(doc.blob_path)

    return DocumentViewResponse(sas_url=sas_url, expires_at=expires_at)



@router.patch(
    "/{document_id}",
    response_model=DocumentMeta,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def update_document(
    document_id: str,
    body: DocumentUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """문서 메타데이터 수정 (파일명 변경).

    - Editor 이상 역할 필요
    - 테넌트 범위 내 문서만 수정 가능 (RLS 자동 적용)
    - 존재하지 않는 문서는 404 반환
    """
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "요청한 문서를 찾을 수 없습니다",
                }
            },
        )

    stmt = select(Document).where(Document.id == doc_uuid)
    result = await db.execute(stmt)
    doc = result.scalars().first()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "요청한 문서를 찾을 수 없습니다",
                }
            },
        )

    doc.filename = body.filename
    doc.updated_at = datetime.now()

    await db.commit()
    await db.refresh(doc)

    return DocumentMeta(
        id=doc.id,
        filename=doc.filename,
        size_bytes=doc.size_bytes,
        content_type=doc.content_type,
        ocr_completed=doc.ocr_completed,
        uploaded_at=doc.uploaded_at,
        updated_at=doc.updated_at,
        owner_id=doc.owner_id,
    )


@router.delete(
    "/{document_id}",
    status_code=204,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def delete_document(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """문서 삭제 (Blob Storage + Metadata_DB).

    삭제 순서:
    1. DB에서 문서 조회
    2. Blob Storage에서 파일 삭제
    3. DB에서 메타데이터 삭제

    Blob 삭제 실패 시: DB 삭제를 수행하지 않고 롤백 + 오류 로깅
    DB 삭제 실패 시 (Blob 삭제 성공 후): 불일치 로깅
    """
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "요청한 문서를 찾을 수 없습니다",
                }
            },
        )

    stmt = select(Document).where(Document.id == doc_uuid)
    result = await db.execute(stmt)
    doc = result.scalars().first()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "DOCUMENT_NOT_FOUND",
                    "message": "요청한 문서를 찾을 수 없습니다",
                }
            },
        )

    blob_path = doc.blob_path
    blob_service = get_blob_service()

    # Step 1: Delete from Blob Storage first
    try:
        await blob_service.delete_blob(blob_path)
    except Exception:
        logger.exception(
            "Blob delete failed for document %s (path: %s). Aborting delete — rolling back.",
            document_id,
            blob_path,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "code": "STORAGE_DELETE_FAILED",
                    "message": "스토리지에서 파일 삭제에 실패했습니다. 삭제가 취소되었습니다.",
                }
            },
        )

    # Step 2: Delete related records and document from DB
    try:
        from app.models.share_link import ShareLink
        from app.models.annotation import Annotation
        from app.models.ocr_job import OCRJob
        from app.models.ocr_result import OCRResult

        for model in (ShareLink, Annotation, OCRJob, OCRResult):
            stmt = select(model).where(model.document_id == doc.id)
            result = await db.execute(stmt)
            for row in result.scalars().all():
                await db.delete(row)

        await db.delete(doc)
        await db.commit()
    except Exception:
        logger.exception(
            "DB delete failed for document %s after Blob was already deleted (path: %s). "
            "DATA INCONSISTENCY: Blob deleted but DB record remains.",
            document_id,
            blob_path,
        )
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": "DELETE_INCONSISTENCY",
                    "message": "문서 삭제 중 오류가 발생했습니다. 관리자에게 문의하세요.",
                }
            },
        )
