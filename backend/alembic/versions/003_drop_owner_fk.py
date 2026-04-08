"""Drop owner_id foreign key from documents to allow Auth0 users.

Revision ID: 003
Revises: 002
Create Date: 2025-01-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: str = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop FK constraints that reference users table
    # (Auth0 users are mapped via to_uuid() and may not exist in users table)
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("documents_owner_id_fkey", type_="foreignkey")

    with op.batch_alter_table("annotations") as batch_op:
        batch_op.drop_constraint("annotations_user_id_fkey", type_="foreignkey")

    with op.batch_alter_table("share_links") as batch_op:
        batch_op.drop_constraint("share_links_created_by_fkey", type_="foreignkey")

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_constraint("audit_logs_user_id_fkey", type_="foreignkey")


def downgrade() -> None:
    pass  # FK re-creation not needed for rollback
