"""UUID 변환 헬퍼 — Auth0 sub 등 비-UUID 문자열을 deterministic UUID로 변환."""

import uuid


def to_uuid(value: str) -> uuid.UUID:
    """문자열을 UUID로 변환한다. 유효한 UUID가 아니면 deterministic UUID5로 변환."""
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, value)
