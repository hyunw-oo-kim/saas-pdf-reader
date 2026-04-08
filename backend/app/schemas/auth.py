"""Auth API 스키마 — Auth0 OIDC 콜백, 토큰 발급/갱신/로그아웃."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthCallbackRequest(BaseModel):
    """OIDC 콜백 요청 — authorization code."""
    code: str = Field(..., description="Auth0에서 발급한 authorization code")
    redirect_uri: str = Field(..., description="OIDC 콜백 redirect URI")


class AuthTokenResponse(BaseModel):
    """JWT 토큰 응답."""
    access_token: str
    refresh_token: str
    expires_in: int = Field(..., description="액세스 토큰 만료 시간 (초)")
    token_type: str = "Bearer"


class RefreshTokenRequest(BaseModel):
    """리프레시 토큰 요청."""
    refresh_token: str


class LogoutResponse(BaseModel):
    """로그아웃 응답."""
    message: str = "로그아웃되었습니다"
