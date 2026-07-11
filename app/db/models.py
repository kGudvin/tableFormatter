from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Tender(TimestampMixin, Base):
    __tablename__ = "tenders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purchase_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    spreadsheet_id: Mapped[str] = mapped_column(String(128))
    sheet_name: Mapped[str] = mapped_column(String(128))
    row_number_cache: Mapped[int | None] = mapped_column(Integer)
    application_deadline: Mapped[date | None] = mapped_column(Date)
    final_protocol_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_status: Mapped[str] = mapped_column(String(64), default="NEW")
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    result: Mapped["OfficialResultRow | None"] = relationship(back_populates="tender")
    products: Mapped[list["ProductRow"]] = relationship(back_populates="tender")


class OfficialResultRow(TimestampMixin, Base):
    __tablename__ = "official_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purchase_number: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenders.purchase_number", ondelete="CASCADE"), unique=True, index=True
    )
    winner_name: Mapped[str | None] = mapped_column(Text)
    winner_inn: Mapped[str | None] = mapped_column(String(12))
    winning_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    current_contract_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    initial_winner_name: Mapped[str | None] = mapped_column(Text)
    result_status: Mapped[str] = mapped_column(String(64))
    data_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    protocol_url: Mapped[str | None] = mapped_column(Text)
    contract_url: Mapped[str | None] = mapped_column(Text)
    specification_url: Mapped[str | None] = mapped_column(Text)

    tender: Mapped[Tender] = relationship(back_populates="result")


class ProductRow(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("purchase_number", "position", "specification_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purchase_number: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenders.purchase_number", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    trademark: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    registry_number: Mapped[str | None] = mapped_column(Text)
    registry_url: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    specification_version: Mapped[str | None] = mapped_column(String(128))

    tender: Mapped[Tender] = relationship(back_populates="products")


class FieldSnapshot(TimestampMixin, Base):
    __tablename__ = "field_snapshots"
    __table_args__ = (UniqueConstraint("purchase_number", "field_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purchase_number: Mapped[str] = mapped_column(String(64), index=True)
    field_name: Mapped[str] = mapped_column(String(128))
    last_value: Mapped[str | None] = mapped_column(Text)
    normalized_hash: Mapped[str] = mapped_column(String(64), index=True)
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    source: Mapped[str | None] = mapped_column(Text)


class ReviewQueue(TimestampMixin, Base):
    __tablename__ = "review_queue"
    __table_args__ = (UniqueConstraint("purchase_number", "reason", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    purchase_number: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    purchase_number: Mapped[str | None] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(Text)
    field_name: Mapped[str | None] = mapped_column(String(128))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    document_url: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_rows: Mapped[int] = mapped_column(Integer, default=0)
    updates_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
