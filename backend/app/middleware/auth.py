"""Auth Middleware - JWT 검증 및 사용자 정보 추출.

Auth0 JWKS 공개키 캐싱을 포함한 JWT 검증 미들웨어.
request.state.user에 사용자 정보(user_id, tenant_id, role, email)를 주입한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from jose import JWTError, ExpiredSignatureError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings


@dataclass
class AuthenticatedUser:
    """JWT에서 추출한 사용자 정보."""
    user_id: str
    tenant_id: str
    role: str
    email: str
    raw_claims: dict[str, Any] = field(default_factory=dict)


class JWKSCache:
    """JWKS 공개키를 TTL 기반으로 캐싱한다."""

    def __init__(self, ttl_seconds: int = 3600):
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def get_jwks(self, jwks_uri: str) -> dict[str, Any]:
        now = time.monotonic()
        if jwks_uri in self._cache:
            cached_keys, cached_at = self._cache[jwks_uri]
            if now - cached_at < self._ttl:
                return cached_keys
        client = await self._get_client()
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks_data = resp.json()
        self._cache[jwks_uri] = (jwks_data, now)
        return jwks_data

    def invalidate(self, jwks_uri: str | None = None) -> None:
        if jwks_uri:
            self._cache.pop(jwks_uri, None)
        else:
            self._cache.clear()

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


_jwks_cache = JWKSCache(ttl_seconds=settings.jwks_cache_ttl_seconds)


def get_jwks_cache() -> JWKSCache:
    return _jwks_cache


# --- Auth0 JWKS/Issuer ---

def _auth0_jwks_uri() -> str:
    domain = settings.auth0_domain.rstrip("/")
    return f"https://{domain}/.well-known/jwks.json"


def _auth0_issuer() -> str:
    domain = settings.auth0_domain.rstrip("/")
    return f"https://{domain}/"


# --- JWT verification ---

def _find_signing_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def verify_jwt_token(token: str) -> AuthenticatedUser:
    """JWT 토큰을 검증하고 사용자 정보를 추출한다.

    Auth0에서 발급한 access_token 또는 내부 HS256 토큰 모두 처리한다.
    """
    unverified_header = jwt.get_unverified_header(token)
    alg = unverified_header.get("alg", "RS256")

    # 내부 HS256 토큰 (auth 라우터에서 발급한 토큰)
    if alg == "HS256":
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
        )
        return AuthenticatedUser(
            user_id=str(claims.get("sub", "")),
            tenant_id=str(claims.get("tenant_id", "")),
            role=str(claims.get("role", "viewer")),
            email=str(claims.get("email", "")),
            raw_claims=claims,
        )

    # Auth0 RS256 토큰
    kid = unverified_header.get("kid")
    jwks_uri = _auth0_jwks_uri()
    audience = settings.auth0_audience or settings.auth0_client_id
    issuer = _auth0_issuer()

    cache = get_jwks_cache()
    jwks = await cache.get_jwks(jwks_uri)
    signing_key = _find_signing_key(jwks, kid) if kid else None

    if signing_key is None:
        cache.invalidate(jwks_uri)
        jwks = await cache.get_jwks(jwks_uri)
        signing_key = _find_signing_key(jwks, kid) if kid else None
        if signing_key is None:
            raise JWTError("Unable to find matching signing key")

    claims = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
    )

    user_id = claims.get("sub", "")
    # Auth0 custom claims (namespace 사용)
    tenant_id = claims.get("tenant_id") or claims.get("https://pdf-reader/tenant_id", "00000000-0000-0000-0000-000000000001")
    role = claims.get("role") or claims.get("https://pdf-reader/role", "viewer")
    email = claims.get("email") or claims.get("https://pdf-reader/email", "")

    return AuthenticatedUser(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        role=str(role) if role else "viewer",
        email=str(email) if email else "",
        raw_claims=claims,
    )


def _error_response(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


DEFAULT_PUBLIC_PATHS: set[str] = {
    "/health", "/docs", "/redoc", "/openapi.json",
    "/api/auth/callback", "/api/auth/refresh", "/api/auth/logout",
}

DEFAULT_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/shared/",
    "/api/files/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, public_paths=None, public_prefixes=None):
        super().__init__(app)
        self.public_paths = public_paths or DEFAULT_PUBLIC_PATHS
        self.public_prefixes = public_prefixes or DEFAULT_PUBLIC_PREFIXES

    def _is_public(self, path: str) -> bool:
        if path in self.public_paths:
            return True
        return any(path.startswith(p) for p in self.public_prefixes)

    async def dispatch(self, request: Request, call_next):
        # CORS preflight (OPTIONS) 요청은 인증 없이 통과
        if request.method == "OPTIONS":
            return await call_next(request)

        if self._is_public(request.url.path):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            return _error_response(401, "인증이 필요합니다", "AUTH_REQUIRED")

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return _error_response(401, "인증이 필요합니다", "AUTH_REQUIRED")

        try:
            user = await verify_jwt_token(parts[1])
        except ExpiredSignatureError:
            return _error_response(401, "토큰이 만료되었습니다", "TOKEN_EXPIRED")
        except JWTError:
            return _error_response(401, "유효하지 않은 토큰입니다", "INVALID_TOKEN")
        except httpx.HTTPError:
            return _error_response(401, "인증 서비스에 연결할 수 없습니다", "AUTH_SERVICE_UNAVAILABLE")

        request.state.user = user
        return await call_next(request)
