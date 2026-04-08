"""SaaS PDF Reader - FastAPI 앱 엔트리포인트."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.middleware.audit import AuditMiddleware
from app.middleware.auth import AuthMiddleware
from app.middleware.tenant import TenantMiddleware
from app.routers import annotations, audit, auth, documents, files, ocr, share

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# --- CORS 설정 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 미들웨어 등록 ---
# Starlette executes middleware in LIFO order:
# AuditMiddleware registered first → runs last (post-response logging)
# TenantMiddleware registered second → runs after AuthMiddleware
# AuthMiddleware registered last → runs first
app.add_middleware(AuditMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(AuthMiddleware)

# --- 라우터 등록 ---
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(annotations.router)
app.include_router(share.router)
app.include_router(ocr.router)
app.include_router(audit.router)
app.include_router(files.router)


@app.get("/health", tags=["health"])
async def health_check():
    """헬스 체크 엔드포인트."""
    return {"status": "ok"}
