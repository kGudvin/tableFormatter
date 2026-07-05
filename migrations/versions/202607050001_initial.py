"""initial schema

Revision ID: 202607050001
Revises:
Create Date: 2026-07-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607050001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purchase_number", sa.String(64), nullable=False, unique=True),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        sa.Column("sheet_name", sa.String(128), nullable=False),
        sa.Column("row_number_cache", sa.Integer),
        sa.Column("application_deadline", sa.Date),
        sa.Column("final_protocol_published_at", sa.DateTime(timezone=True)),
        sa.Column("current_status", sa.String(64), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_successful_update_at", sa.DateTime(timezone=True)),
        sa.Column("error_count", sa.Integer, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenders_purchase_number", "tenders", ["purchase_number"])
    op.create_index("ix_tenders_next_check_at", "tenders", ["next_check_at"])

    op.create_table(
        "official_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purchase_number", sa.String(64), sa.ForeignKey("tenders.purchase_number", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("winner_name", sa.Text),
        sa.Column("winner_inn", sa.String(12)),
        sa.Column("winning_price", sa.Numeric(18, 2)),
        sa.Column("current_contract_price", sa.Numeric(18, 2)),
        sa.Column("initial_winner_name", sa.Text),
        sa.Column("result_status", sa.String(64), nullable=False),
        sa.Column("data_versions", sa.JSON, nullable=False),
        sa.Column("protocol_url", sa.Text),
        sa.Column("contract_url", sa.Text),
        sa.Column("specification_url", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_official_results_purchase_number", "official_results", ["purchase_number"])

    op.create_table(
        "products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purchase_number", sa.String(64), sa.ForeignKey("tenders.purchase_number", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("country", sa.Text),
        sa.Column("manufacturer", sa.Text),
        sa.Column("trademark", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("registry_number", sa.Text),
        sa.Column("registry_url", sa.Text),
        sa.Column("quantity", sa.Numeric(18, 4)),
        sa.Column("unit", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("specification_version", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("purchase_number", "position", "specification_version"),
    )
    op.create_index("ix_products_purchase_number", "products", ["purchase_number"])

    op.create_table(
        "field_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purchase_number", sa.String(64), nullable=False),
        sa.Column("field_name", sa.String(128), nullable=False),
        sa.Column("last_value", sa.Text),
        sa.Column("normalized_hash", sa.String(64), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("purchase_number", "field_name"),
    )
    op.create_index("ix_field_snapshots_purchase_number", "field_snapshots", ["purchase_number"])
    op.create_index("ix_field_snapshots_normalized_hash", "field_snapshots", ["normalized_hash"])

    op.create_table(
        "review_queue",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("purchase_number", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("purchase_number", "reason", "status"),
    )
    op.create_index("ix_review_queue_purchase_number", "review_queue", ["purchase_number"])
    op.create_index("ix_review_queue_status", "review_queue", ["status"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purchase_number", sa.String(64)),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("source", sa.Text),
        sa.Column("field_name", sa.String(128)),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("document_url", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("run_id", sa.String(36)),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_purchase_number", "audit_log", ["purchase_number"])
    op.create_index("ix_audit_log_run_id", "audit_log", ["run_id"])

    op.create_table(
        "processing_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("checked_rows", sa.Integer, nullable=False),
        sa.Column("updates_count", sa.Integer, nullable=False),
        sa.Column("errors_count", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
    )
    op.create_index("ix_processing_runs_job_type", "processing_runs", ["job_type"])


def downgrade() -> None:
    op.drop_table("processing_runs")
    op.drop_table("audit_log")
    op.drop_table("review_queue")
    op.drop_table("field_snapshots")
    op.drop_table("products")
    op.drop_table("official_results")
    op.drop_table("tenders")

