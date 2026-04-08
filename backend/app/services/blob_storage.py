"""로컬 파일시스템 스토리지 서비스 — 문서 업로드, 삭제, 서빙 URL 생성.

Azure Blob Storage 대신 로컬 파일시스템을 사용한다.
테넌트별 경로 접두사({tenant_id}/)로 파일을 격리한다.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class BlobStorageService:
    """로컬 파일시스템 기반 스토리지 서비스.

    BlobStorageService 이름을 유지하여 기존 import를 변경하지 않는다.
    """

    def __init__(self, storage_path: str | None = None):
        self._storage_path = Path(storage_path or settings.local_storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)

    def build_blob_path(self, tenant_id: str, document_id: uuid.UUID) -> str:
        """테넌트별 파일 경로를 생성한다: {tenant_id}/{document_id}.pdf"""
        return f"{tenant_id}/{document_id}.pdf"

    def _full_path(self, blob_path: str) -> Path:
        """blob_path를 실제 파일시스템 경로로 변환한다."""
        return self._storage_path / blob_path

    async def upload_blob(
        self,
        blob_path: str,
        data: bytes,
        content_type: str = "application/pdf",
    ) -> None:
        """로컬 파일시스템에 파일을 저장한다."""
        full = self._full_path(blob_path)
        full.parent.mkdir(parents=True, exist_ok=True)
        try:
            full.write_bytes(data)
        except Exception:
            logger.exception("File write failed for path: %s", blob_path)
            raise

    async def delete_blob(self, blob_path: str) -> None:
        """로컬 파일시스템에서 파일을 삭제한다."""
        full = self._full_path(blob_path)
        try:
            if full.exists():
                full.unlink()
        except Exception:
            logger.exception("File delete failed for path: %s", blob_path)
            raise

    def generate_sas_url(self, blob_path: str, expire_minutes: int | None = None) -> tuple[str, datetime]:
        """로컬 파일 서빙 URL을 생성한다.

        Azure SAS URL 대신 내부 파일 서빙 엔드포인트 URL을 반환한다.
        expires_at은 호환성을 위해 유지하지만 실제 만료 검증은 하지 않는다.
        """
        if expire_minutes is None:
            expire_minutes = settings.sas_token_expire_minutes

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=expire_minutes)

        # 내부 파일 서빙 엔드포인트 URL
        file_url = f"{settings.backend_base_url}/api/files/{blob_path}"

        return file_url, expires_at

    def get_file_path(self, blob_path: str) -> Path:
        """blob_path에 해당하는 실제 파일 경로를 반환한다."""
        return self._full_path(blob_path)


# Module-level singleton
_blob_service: BlobStorageService | None = None


def get_blob_service() -> BlobStorageService:
    """BlobStorageService 싱글턴을 반환한다. 테스트에서 교체 가능."""
    global _blob_service
    if _blob_service is None:
        _blob_service = BlobStorageService()
    return _blob_service


def set_blob_service(service: BlobStorageService) -> None:
    """테스트용: BlobStorageService를 교체한다."""
    global _blob_service
    _blob_service = service
