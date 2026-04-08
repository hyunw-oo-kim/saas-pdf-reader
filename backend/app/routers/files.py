"""File Serving API — 로컬 파일시스템에서 PDF 파일 서빙 (/api/files)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.blob_storage import get_blob_service

router = APIRouter(tags=["files"])


@router.get("/api/files/{tenant_id}/{filename}")
async def serve_file(tenant_id: str, filename: str):
    """로컬 스토리지에서 파일을 서빙한다.

    Azure Blob SAS URL을 대체하는 엔드포인트.
    """
    blob_path = f"{tenant_id}/{filename}"
    blob_service = get_blob_service()
    file_path = blob_service.get_file_path(blob_path)

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=filename,
    )
