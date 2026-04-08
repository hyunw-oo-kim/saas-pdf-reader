"""Tenant Middleware - JWT claims에서 tenant_id 추출.

AuthMiddleware 이후에 실행되며, request.state.user에서 tenant_id를 추출하여
request.state.tenant_id에 저장한다.
tenant_id가 없거나 비어 있으면 403 Forbidden을 반환한다.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.middleware.auth import DEFAULT_PUBLIC_PATHS, DEFAULT_PUBLIC_PREFIXES


def _error_response(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


class TenantMiddleware(BaseHTTPMiddleware):
    """테넌트 미들웨어.

    - request.state.user에서 tenant_id를 추출한다.
    - tenant_id가 없거나 비어 있으면 403 Forbidden을 반환한다.
    - request.state.tenant_id에 저장한다.
    """

    def __init__(self, app, public_paths=None, public_prefixes=None):
        super().__init__(app)
        self.public_paths = public_paths or DEFAULT_PUBLIC_PATHS
        self.public_prefixes = public_prefixes or DEFAULT_PUBLIC_PREFIXES

    def _is_public(self, path: str) -> bool:
        if path in self.public_paths:
            return True
        return any(path.startswith(prefix) for prefix in self.public_prefixes)

    async def dispatch(self, request: Request, call_next):
        # CORS preflight (OPTIONS) 요청은 테넌트 설정 없이 통과
        if request.method == "OPTIONS":
            return await call_next(request)

        if self._is_public(request.url.path):
            return await call_next(request)

        user = getattr(request.state, "user", None)
        if user is None:
            return _error_response(403, "접근 권한이 없습니다", "TENANT_REQUIRED")

        tenant_id = getattr(user, "tenant_id", None)
        if not tenant_id or not str(tenant_id).strip():
            return _error_response(403, "접근 권한이 없습니다", "TENANT_REQUIRED")

        request.state.tenant_id = str(tenant_id).strip()
        return await call_next(request)
