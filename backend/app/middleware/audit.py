"""Audit Middleware - 상태 변경 API 호출 시 감사 로그 자동 기록.

Post-response 미들웨어로, 요청 처리 후 감사 로그를 자동으로 기록한다.
- 상태 변경 작업 (POST, PUT, PATCH, DELETE) 및 문서 열람 (GET /view) 로깅
- 공개 경로 및 인증 엔드포인트는 건너뜀
- 사용자 ID, 작업 유형, 대상 문서, 타임스탬프, IP 주소 기록
"""

from __future__ import annotations

import logging
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.utils.uuid_helper import to_uuid
from app.database import async_session
from app.middleware.auth import DEFAULT_PUBLIC_PATHS, DEFAULT_PUBLIC_PREFIXES
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action type detection from HTTP method + URL pattern
# ---------------------------------------------------------------------------

# URL patterns → action_type mapping
_ACTION_RULES: list[tuple[str, str, str]] = [
    # (method, url_regex, action_type)
    ("GET", r"/api/documents/[^/]+/view$", "view"),
    ("POST", r"/api/documents/upload$", "upload"),
    ("DELETE", r"/api/documents/[^/]+$", "delete"),
    ("POST", r"/api/documents/[^/]+/share$", "share"),
    ("DELETE", r"/api/documents/[^/]+/share/", "share"),
    ("PUT", r"/api/documents/[^/]+/annotations$", "annotate"),
    ("DELETE", r"/api/documents/[^/]+/annotations/", "annotate"),
    ("PATCH", r"/api/documents/[^/]+$", "rename"),
    ("POST", r"/api/documents/[^/]+/ocr$", "ocr"),
]

# Compiled regex cache
_COMPILED_RULES: list[tuple[str, re.Pattern, str]] = [
    (method, re.compile(pattern), action) for method, pattern, action in _ACTION_RULES
]

# Regex to extract document_id from URL paths like /api/documents/{uuid}/...
_DOC_ID_PATTERN = re.compile(r"/api/documents/([0-9a-fA-F\-]{36})")


def detect_action_type(method: str, path: str) -> str | None:
    """HTTP method와 URL 경로에서 감사 로그 작업 유형을 결정한다.

    Returns:
        action_type 문자열 또는 None (로깅 대상이 아닌 경우)
    """
    method_upper = method.upper()
    for rule_method, rule_pattern, action_type in _COMPILED_RULES:
        if method_upper == rule_method and rule_pattern.search(path):
            return action_type
    return None


def extract_document_id(path: str) -> uuid.UUID | None:
    """URL 경로에서 document_id를 추출한다."""
    match = _DOC_ID_PATTERN.search(path)
    if match:
        try:
            return uuid.UUID(match.group(1))
        except ValueError:
            return None
    return None


def get_client_ip(request: Request) -> str:
    """요청에서 클라이언트 IP 주소를 추출한다."""
    # X-Forwarded-For 헤더 우선 (프록시/로드밸런서 뒤에 있을 때)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# Audit Middleware
# ---------------------------------------------------------------------------

class AuditMiddleware(BaseHTTPMiddleware):
    """감사 로그 미들웨어.

    요청 처리 후(post-response) 감사 로그를 자동으로 기록한다.
    - 상태 변경 작업 (POST, PUT, PATCH, DELETE) 및 문서 열람 (GET /view)만 로깅
    - 공개 경로 및 인증 엔드포인트는 건너뜀
    - 성공 응답(2xx, 3xx)만 로깅
    """

    def __init__(
        self,
        app,
        public_paths: set[str] | None = None,
        public_prefixes: tuple[str, ...] | None = None,
    ):
        super().__init__(app)
        self.public_paths = public_paths or DEFAULT_PUBLIC_PATHS
        self.public_prefixes = public_prefixes or DEFAULT_PUBLIC_PREFIXES

    def _is_public(self, path: str) -> bool:
        """감사 로깅을 건너뛸 경로인지 확인한다."""
        if path in self.public_paths:
            return True
        return any(path.startswith(prefix) for prefix in self.public_prefixes)

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. 요청을 먼저 처리한다 (post-response 방식)
        response = await call_next(request)

        # 2. 공개 경로는 건너뛴다
        path = request.url.path
        if self._is_public(path):
            return response

        # 3. 실패 응답은 로깅하지 않는다 (2xx, 3xx만 로깅)
        if response.status_code >= 400:
            return response

        # 4. 작업 유형을 결정한다
        action_type = detect_action_type(request.method, path)
        if action_type is None:
            return response

        # 5. 사용자 정보를 추출한다
        user = getattr(request.state, "user", None)
        if user is None:
            return response

        user_id = getattr(user, "user_id", None)
        tenant_id = getattr(user, "tenant_id", None)
        if not user_id or not tenant_id:
            return response

        # 6. 문서 ID를 추출한다
        document_id = extract_document_id(path)

        # 7. 클라이언트 IP를 추출한다
        ip_address = get_client_ip(request)

        # 8. 감사 로그를 비동기로 기록한다
        try:
            async with async_session() as session:
                log_entry = AuditLog(
                    tenant_id=to_uuid(str(tenant_id)),
                    user_id=to_uuid(str(user_id)),
                    action_type=action_type,
                    document_id=document_id,
                    ip_address=ip_address,
                )
                session.add(log_entry)
                await session.commit()
        except Exception:
            # 감사 로그 기록 실패가 요청 처리에 영향을 주지 않도록 한다
            logger.exception("Failed to record audit log for %s %s", request.method, path)

        return response
