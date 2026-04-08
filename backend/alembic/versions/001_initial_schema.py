"""Initial schema - all 8 tables.

Revision ID: 001
Revises: None
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- tenants ---
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("external_id", sa.String(255), unique=True, nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="viewer"),
        sa.Column("idp_provider", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- documents ---
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("owner_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("blob_path", sa.String(1000), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False, server_default="application/pdf"),
        sa.Column("ocr_completed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- annotations ---
    op.create_table(
        "annotations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("document_id", sa.Uuid, sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("xfdf_data", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- share_links ---
    op.create_table(
        "share_links",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("document_id", sa.Uuid, sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("share_token", sa.String(255), unique=True, nullable=False),
        sa.Column("permission", sa.String(50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- ocr_jobs ---
    op.create_table(
        "ocr_jobs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("document_id", sa.Uuid, sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("progress_percent", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- ocr_results ---
    op.create_table(
        "ocr_results",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("document_id", sa.Uuid, sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("extracted_text", sa.Text, nullable=False),
        sa.Column("words", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("document_id", sa.Uuid, nullable=True, index=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("details", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("ocr_results")
    op.drop_table("ocr_jobs")
    op.drop_table("share_links")
    op.drop_table("annotations")
    op.drop_table("documents")
    op.drop_table("users")
    op.drop_table("tenants")
