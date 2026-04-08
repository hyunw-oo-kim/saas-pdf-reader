"""Auth API — Auth0 OIDC 콜백 처리 및 JWT 토큰 관리 (/api/auth)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException
from jose import JWTError, jwt

from app.config import settings
from app.schemas.auth import (
    AuthCallbackRequest,
    AuthTokenResponse,
    LogoutResponse,
    RefreshTokenRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_INTERNAL_ALGORITHM = "HS256"


def _create_access_token(claims: dict[str, Any]) -> tuple[str, int]:
    expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        **claims, "exp": expire, "iat": datetime.now(timezone.utc),
        "jti": str(uuid4()), "type": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=_INTERNAL_ALGORITHM)
    return token, int(expires_delta.total_seconds())


def _create_refresh_token(claims: dict[str, Any]) -> str:
    expires_delta = timedelta(days=settings.jwt_refresh_token_expire_days)
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        **claims, "exp": expire, "iat": datetime.now(timezone.utc),
        "jti": str(uuid4()), "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_INTERNAL_ALGORITHM)


def _decode_internal_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[_INTERNAL_ALGORITHM])


def _auth0_token_endpoint() -> str:
    domain = settings.auth0_domain.rstrip("/")
    return f"https://{domain}/oauth/token"


def _auth0_userinfo_endpoint() -> str:
    domain = settings.auth0_domain.rstrip("/")
    return f"https://{domain}/userinfo"


async def _exchange_code_with_auth0(code: str, redirect_uri: str) -> dict[str, Any]:
    """Auth0에 authorization code를 교환하여 토큰을 받는다."""
    token_url = _auth0_token_endpoint()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.auth0_client_id,
        "client_secret": settings.auth0_client_secret,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_url, json=data,
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code != 200:
        detail = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "IDP_TOKEN_EXCHANGE_FAILED", "message": "Auth0 토큰 교환에 실패했습니다", "details": detail}},
        )
    return resp.json()


async def _get_userinfo(access_token: str) -> dict[str, Any]:
    """Auth0 /userinfo 엔드포인트에서 사용자 정보를 가져온다."""
    userinfo_url = _auth0_userinfo_endpoint()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        return {}
    return resp.json()


def _extract_user_claims(idp_response: dict[str, Any], userinfo: dict[str, Any] | None = None) -> dict[str, Any]:
    """Auth0 응답에서 사용자 claims를 추출한다."""
    # id_token에서 기본 정보 추출
    id_token = idp_response.get("id_token", "")
    claims: dict[str, Any] = {}
    if id_token:
        try:
            claims = jwt.get_unverified_claims(id_token)
        except JWTError:
            pass

    # userinfo로 보충
    if userinfo:
        claims.update({k: v for k, v in userinfo.items() if v and k not in claims})

    sub = str(claims.get("sub", ""))
    email = str(claims.get("email", ""))
    # Auth0에서는 tenant_id를 custom claim으로 넣거나, 없으면 default UUID 사용
    tenant_id = str(claims.get("tenant_id") or claims.get("https://pdf-reader/tenant_id", "00000000-0000-0000-0000-000000000001"))
    role = str(claims.get("role") or claims.get("https://pdf-reader/role", "editor"))

    return {
        "sub": sub,
        "tenant_id": tenant_id,
        "email": email,
        "role": role,
    }


@router.post("/callback", response_model=AuthTokenResponse)
async def auth_callback(body: AuthCallbackRequest) -> AuthTokenResponse:
    """Auth0 OIDC 콜백 처리 → 내부 JWT 발급."""
    idp_response = await _exchange_code_with_auth0(body.code, body.redirect_uri)

    # userinfo 가져오기 (email 등 추가 정보)
    auth0_access_token = idp_response.get("access_token", "")
    userinfo = None
    if auth0_access_token:
        userinfo = await _get_userinfo(auth0_access_token)

    user_claims = _extract_user_claims(idp_response, userinfo)
    if not user_claims.get("sub"):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "INVALID_ID_TOKEN", "message": "Auth0에서 유효한 사용자 정보를 추출할 수 없습니다"}},
        )
    access_token, expires_in = _create_access_token(user_claims)
    refresh_token = _create_refresh_token(user_claims)
    return AuthTokenResponse(access_token=access_token, refresh_token=refresh_token, expires_in=expires_in)


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_token(body: RefreshTokenRequest) -> AuthTokenResponse:
    """리프레시 토큰으로 새 액세스 토큰 발급."""
    try:
        claims = _decode_internal_token(body.refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail={"error": {"code": "INVALID_REFRESH_TOKEN", "message": "유효하지 않거나 만료된 리프레시 토큰입니다"}})
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail={"error": {"code": "INVALID_REFRESH_TOKEN", "message": "유효하지 않은 토큰 타입입니다"}})
    user_claims = {k: v for k, v in claims.items() if k not in ("exp", "iat", "jti", "type")}
    access_token, expires_in = _create_access_token(user_claims)
    refresh_token_new = _create_refresh_token(user_claims)
    return AuthTokenResponse(access_token=access_token, refresh_token=refresh_token_new, expires_in=expires_in)


@router.post("/logout", response_model=LogoutResponse)
async def logout() -> LogoutResponse:
    return LogoutResponse(message="로그아웃되었습니다")
