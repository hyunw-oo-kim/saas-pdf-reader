"""Seed default tenant and user.

Revision ID: 002
Revises: 001
Create Date: 2025-01-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: str = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Default tenant
    op.execute(
        "INSERT INTO tenants (id, name, slug) "
        "VALUES ('00000000-0000-0000-0000-000000000001', 'Default', 'default') "
        "ON CONFLICT DO NOTHING"
    )
    # Default user (for Auth0 users mapped via to_uuid)
    op.execute(
        "INSERT INTO users (id, tenant_id, external_id, email, name, role, idp_provider) "
        "VALUES ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', "
        "'default-user', 'default@pdf-reader.app', 'Default User', 'editor', 'auth0') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE id = '00000000-0000-0000-0000-000000000002'")
    op.execute("DELETE FROM tenants WHERE id = '00000000-0000-0000-0000-000000000001'")
