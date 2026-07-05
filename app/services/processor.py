from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    FieldSnapshot,
    OfficialResultRow,
    ProcessingRun,
    ProductRow,
    ReviewQueue,
    Tender,
)
from app.domain.models import OfficialResult, ResultStatus
from app.domain.products import format_products
from app.domain.snapshots import field_hash, is_manual_change
from app.review.sync import publish_open_reviews
from app.services.result_builder import build_official_result
from app.sheets.client import SheetsClient, a1_col, inspect_main_sheet
from app.sheets.schema import SheetSchema, is_working_row, normalize_purchase_number
from app.sources.base import ProcurementSource

logger = structlog.get_logger(__name__)


@dataclass
class RowUpdate:
    row_number: int
    values: dict[str, Any]


@dataclass
class ProcessingSummary:
    checked: int = 0
    updates: int = 0
    errors: int = 0
    skipped: int = 0
    missing: int = 0


@dataclass
class ProcurementProcessor:
    source: ProcurementSource
    sheets: SheetsClient
    db: AsyncSession
    spreadsheet_id: str
    sheet_name: str
    review_sheet_name: str | None = None

    async def process_purchase(self, purchase_number: str, write: bool = True) -> OfficialResult:
        result = await self._build_result(purchase_number)
        if write:
            schema = await inspect_main_sheet(self.sheets, self.sheet_name)
            row_number, row = await self._find_row(purchase_number, schema)
            await self._write_result(schema, row_number, row, result)
            await self._persist_result(result, row_number)
        return result

    async def _build_result(self, purchase_number: str) -> OfficialResult:
        protocol = await self.source.get_final_protocol(purchase_number)
        contract = await self.source.get_contract(purchase_number)
        specification = await self.source.get_specification(purchase_number)
        return build_official_result(purchase_number, protocol, contract, specification)

    async def backfill_empty_rows(self) -> None:
        run_id = str(uuid4())
        run = ProcessingRun(id=run_id, job_type="backfill-2026")
        self.db.add(run)
        schema = await inspect_main_sheet(self.sheets, self.sheet_name)
        values = await self.sheets.read_values(f"'{self.sheet_name}'!A:{a1_col(len(schema.original_headers))}")
        summary = ProcessingSummary()
        for row_number, row in enumerate(values, start=1):
            if row_number <= schema.header_row or not is_working_row(row, schema):
                continue
            purchase_number = normalize_purchase_number(row[schema.purchase_number_column - 1])
            if not self._row_needs_autofill(row, schema):
                summary.skipped += 1
                continue
            await self._process_known_row(schema, row_number, row, purchase_number, run_id, run, summary)
        await self._finish_run(run, summary)
        await self._publish_reviews()

    async def process_row_range(self, start_row: int, end_row: int, force: bool = False) -> ProcessingSummary:
        run_id = str(uuid4())
        run = ProcessingRun(id=run_id, job_type=f"web-range-{start_row}-{end_row}")
        self.db.add(run)
        schema = await inspect_main_sheet(self.sheets, self.sheet_name)
        values = await self.sheets.read_values(f"'{self.sheet_name}'!A:{a1_col(len(schema.original_headers))}")
        summary = ProcessingSummary()
        lower, upper = sorted((start_row, end_row))
        for row_number, row in enumerate(values, start=1):
            if row_number < lower or row_number > upper:
                continue
            if row_number <= schema.header_row or not is_working_row(row, schema):
                summary.skipped += 1
                continue
            if not force and not self._row_needs_autofill(row, schema):
                summary.skipped += 1
                continue
            purchase_number = normalize_purchase_number(row[schema.purchase_number_column - 1])
            await self._process_known_row(schema, row_number, row, purchase_number, run_id, run, summary)
        await self._finish_run(run, summary)
        await self._publish_reviews()
        return summary

    async def process_purchase_numbers(
        self, purchase_numbers: list[str], force: bool = True
    ) -> ProcessingSummary:
        run_id = str(uuid4())
        run = ProcessingRun(id=run_id, job_type="web-purchase-list")
        self.db.add(run)
        schema = await inspect_main_sheet(self.sheets, self.sheet_name)
        values = await self.sheets.read_values(f"'{self.sheet_name}'!A:{a1_col(len(schema.original_headers))}")
        rows_by_purchase: dict[str, tuple[int, list[Any]]] = {}
        for row_number, row in enumerate(values, start=1):
            if row_number <= schema.header_row or not is_working_row(row, schema):
                continue
            purchase_number = normalize_purchase_number(row[schema.purchase_number_column - 1])
            rows_by_purchase.setdefault(purchase_number, (row_number, row))

        summary = ProcessingSummary()
        for purchase_number in purchase_numbers:
            row_info = rows_by_purchase.get(purchase_number)
            if row_info is None:
                summary.missing += 1
                continue
            row_number, row = row_info
            if not force and not self._row_needs_autofill(row, schema):
                summary.skipped += 1
                continue
            await self._process_known_row(schema, row_number, row, purchase_number, run_id, run, summary)
        await self._finish_run(run, summary)
        await self._publish_reviews()
        return summary

    async def _process_known_row(
        self,
        schema: SheetSchema,
        row_number: int,
        row: list[Any],
        purchase_number: str,
        run_id: str,
        run: ProcessingRun,
        summary: ProcessingSummary,
    ) -> None:
        summary.checked += 1
        try:
            result = await self._build_result(purchase_number)
            await self._write_result(schema, row_number, row, result)
            await self._persist_result(result, row_number)
            summary.updates += 1
        except Exception as exc:
            summary.errors += 1
            logger.exception(
                "purchase_processing_failed",
                purchase_number=purchase_number,
                run_id=run_id,
                error_type=type(exc).__name__,
                error=str(exc)[:1000],
            )
            await self.db.rollback()
            self.db.add(run)
            self.db.add(
                AuditLog(
                    purchase_number=purchase_number,
                    actor="app",
                    source=run.job_type,
                    reason=type(exc).__name__,
                    run_id=run_id,
                )
            )

    async def _finish_run(self, run: ProcessingRun, summary: ProcessingSummary) -> None:
        run.checked_rows = summary.checked
        run.updates_count = summary.updates
        run.errors_count = summary.errors + summary.missing
        run.status = "FAILED" if run.errors_count else "COMPLETED"
        run.finished_at = datetime.now(UTC)
        await self.db.commit()

    async def _publish_reviews(self) -> None:
        if self.review_sheet_name:
            await publish_open_reviews(self.db, self.sheets, self.review_sheet_name)

    async def _find_row(self, purchase_number: str, schema: SheetSchema) -> tuple[int, list[Any]]:
        values = await self.sheets.read_values(f"'{self.sheet_name}'!A:{a1_col(len(schema.original_headers))}")
        for row_number, row in enumerate(values, start=1):
            if row_number <= schema.header_row or not is_working_row(row, schema):
                continue
            cell_value = normalize_purchase_number(row[schema.purchase_number_column - 1])
            if cell_value == purchase_number:
                return row_number, row
        msg = f"Purchase row was not found: {purchase_number}"
        raise LookupError(msg)

    def _row_needs_autofill(self, row: list[Any], schema: SheetSchema) -> bool:
        return not (
            _cell_has_value(row, schema, "Кто выиграл")
            and _cell_has_value(row, schema, "Поставляемый товар")
        )

    async def _write_result(
        self, schema: SheetSchema, row_number: int, current_row: list[Any], result: OfficialResult
    ) -> None:
        values = self._sheet_values(result)
        writes: dict[int, Any] = {}
        for field_name, new_value in values.items():
            column = schema.column(field_name)
            old_value = current_row[column - 1] if column - 1 < len(current_row) else None
            snapshot = await self._snapshot(result.purchase_number, field_name)
            if snapshot and is_manual_change(old_value, snapshot.last_value):
                await self._enqueue_review(
                    result.purchase_number,
                    f"Конфликт с ручным значением в поле {field_name}",
                    {"old": old_value, "new": new_value},
                )
                continue
            writes[column] = new_value
            await self._save_snapshot(result.purchase_number, field_name, new_value, result.protocol_url or result.contract_url)
            self.db.add(
                AuditLog(
                    purchase_number=result.purchase_number,
                    actor="app",
                    source="processor",
                    field_name=field_name,
                    old_value=str(old_value) if old_value is not None else None,
                    new_value=str(new_value) if new_value is not None else None,
                    document_url=result.protocol_url or result.contract_url or result.specification_url,
                    reason="auto-update",
                )
            )
        if writes:
            start_col = min(writes)
            end_col = max(writes)
            row_values = [
                current_row[column - 1] if column - 1 < len(current_row) else ""
                for column in range(start_col, end_col + 1)
            ]
            for column, value in writes.items():
                row_values[column - start_col] = value
            await self.sheets.write_values(
                f"'{self.sheet_name}'!{a1_col(start_col)}{row_number}:{a1_col(end_col)}{row_number}",
                [row_values],
            )
        if result.attention_required and result.review_reason:
            await self._enqueue_review(result.purchase_number, result.review_reason, self._review_payload(result))

    def _sheet_values(self, result: OfficialResult) -> dict[str, Any]:
        if result.status == ResultStatus.NON_STANDARD:
            return {
                "Победная ставка": "-",
                "Кто выиграл": result.winner_name or "-",
                "ИНН победителя": "-",
                "Поставляемый товар": "-",
                "Автостатус": result.status.value,
                "Дата последней проверки": datetime.now(UTC).isoformat(),
            }
        return {
            "Победная ставка": _decimal_or_empty(result.winning_price),
            "Кто выиграл": result.winner_name or "",
            "ИНН победителя": result.winner_inn or "",
            "Поставляемый товар": format_products(result.products),
            "Текущая цена контракта": _decimal_or_empty(result.current_contract_price),
            "Ко вниманию": result.attention_required,
            "Автостатус": result.status.value,
            "Дата последней проверки": datetime.now(UTC).isoformat(),
            "Дата последнего автообновления": datetime.now(UTC).isoformat(),
            "Источник протокола": result.protocol_url or "",
            "Источник контракта": result.contract_url or "",
            "Источник спецификации": result.specification_url or "",
            "Первоначальный победитель": result.initial_winner_name or "",
            "Ошибка обработки": result.review_reason or "",
        }

    async def _snapshot(self, purchase_number: str, field_name: str) -> FieldSnapshot | None:
        result = await self.db.execute(
            select(FieldSnapshot).where(
                FieldSnapshot.purchase_number == purchase_number,
                FieldSnapshot.field_name == field_name,
            )
        )
        return result.scalar_one_or_none()

    async def _save_snapshot(
        self, purchase_number: str, field_name: str, value: Any, source: str | None
    ) -> None:
        snapshot = await self._snapshot(purchase_number, field_name)
        if snapshot is None:
            snapshot = FieldSnapshot(purchase_number=purchase_number, field_name=field_name)
            self.db.add(snapshot)
        snapshot.last_value = str(value) if value is not None else None
        snapshot.normalized_hash = field_hash(value)
        snapshot.written_at = datetime.now(UTC)
        snapshot.source = source

    async def _enqueue_review(self, purchase_number: str, reason: str, payload: dict[str, Any]) -> None:
        existing = await self.db.scalar(
            select(ReviewQueue).where(
                ReviewQueue.purchase_number == purchase_number,
                ReviewQueue.reason == reason,
                ReviewQueue.status == "OPEN",
            )
        )
        if existing is not None:
            existing.payload = _json_safe(payload)
            return
        self.db.add(
            ReviewQueue(
                purchase_number=purchase_number,
                reason=reason,
                status="OPEN",
                payload=_json_safe(payload),
            )
        )

    def _review_payload(self, result: OfficialResult) -> dict[str, Any]:
        return {
            "winner": result.winner_name,
            "inn": result.winner_inn,
            "winning_price": str(result.winning_price) if result.winning_price is not None else None,
            "protocol_url": result.protocol_url,
            "contract_url": result.contract_url,
            "specification_url": result.specification_url,
        }

    async def _persist_result(self, result: OfficialResult, row_number: int) -> None:
        tender = await self.db.scalar(select(Tender).where(Tender.purchase_number == result.purchase_number))
        if tender is None:
            tender = Tender(
                purchase_number=result.purchase_number,
                spreadsheet_id=self.spreadsheet_id,
                sheet_name=self.sheet_name,
            )
            self.db.add(tender)
        tender.row_number_cache = row_number
        tender.last_checked_at = datetime.now(UTC)
        tender.last_successful_update_at = datetime.now(UTC)
        tender.current_status = "REVIEW_REQUIRED" if result.attention_required else "COMPLETED"

        official = await self.db.scalar(
            select(OfficialResultRow).where(OfficialResultRow.purchase_number == result.purchase_number)
        )
        if official is None:
            official = OfficialResultRow(purchase_number=result.purchase_number, data_versions={})
            self.db.add(official)
        official.winner_name = result.winner_name
        official.winner_inn = result.winner_inn
        official.winning_price = result.winning_price
        official.current_contract_price = result.current_contract_price
        official.initial_winner_name = result.initial_winner_name
        official.result_status = result.status.value
        official.protocol_url = result.protocol_url
        official.contract_url = result.contract_url
        official.specification_url = result.specification_url

        await self.db.execute(delete(ProductRow).where(ProductRow.purchase_number == result.purchase_number))
        for item in result.products:
            self.db.add(
                ProductRow(
                    purchase_number=result.purchase_number,
                    position=item.position,
                    country=item.country,
                    manufacturer=item.manufacturer,
                    trademark=item.trademark,
                    model=item.model,
                    registry_number=item.registry_number,
                    registry_url=item.registry_url,
                    quantity=item.quantity,
                    unit=item.unit,
                    source_url=item.source_url,
                    specification_version=item.specification_version,
                )
            )
        await self.db.commit()


def _decimal_or_empty(value: Decimal | None) -> str:
    return str(value) if value is not None else ""


def _cell_has_value(row: list[Any], schema: SheetSchema, column_name: str) -> bool:
    idx = schema.columns.get(column_name)
    return bool(idx and idx - 1 < len(row) and str(row[idx - 1] or "").strip())


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value
