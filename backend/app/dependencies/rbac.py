"""RBAC (Role-Based Access Control) 의존성.

Admin, Editor, Viewer 세 가지 역할을 정의하고,
각 역할별 허용 작업 매트릭스를 기반으로 접근 제어를 수행한다.

역할 계층: Admin > Editor > Viewer
- Viewer: 조회만 가능
- Editor: 업로드, 주석 추가, 조회 가능
- Admin: 전체 (사용자 관리 포함)
"""

from __future__ import annotations

from enum import IntEnum
from typing import Callable

from fastapi import Depends, HTTPException, Request


class Role(IntEnum):
    """사용자 역할. 숫자가 클수록 높은 권한을 가진다."""

    VIEWER = 1
    EDITOR = 2
    ADMIN = 3


#: 문자열 → Role 매핑 (JWT claim에서 오는 문자열 처리용)
_ROLE_MAP: dict[str, Role] = {
    "viewer": Role.VIEWER,
    "editor": Role.EDITOR,
    "admin": Role.ADMIN,
}


def parse_role(role_str: str) -> Role:
    """문자열을 Role enum으로 변환한다. 알 수 없는 역할은 VIEWER로 처리."""
    return _ROLE_MAP.get(role_str.lower(), Role.VIEWER)


def require_role(*allowed_roles: Role) -> Callable:
    """지정된 역할 이상의 권한을 요구하는 FastAPI 의존성 팩토리.

    역할 계층(Admin > Editor > Viewer)을 사용하여,
    사용자의 역할이 허용된 역할 중 하나 이상과 같거나 높으면 통과한다.

    Usage::

        @router.get("/admin-only", dependencies=[Depends(require_role(Role.ADMIN))])
        async def admin_endpoint(): ...

        @router.post("/upload", dependencies=[Depends(require_role(Role.EDITOR))])
        async def upload(): ...
    """
    min_role = min(allowed_roles)

    async def _check_role(request: Request) -> None:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "AUTH_REQUIRED", "message": "인증이 필요합니다"}},
            )

        user_role = parse_role(user.role)
        if user_role < min_role:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": "FORBIDDEN", "message": "권한이 부족합니다"}},
            )

    return _check_role
