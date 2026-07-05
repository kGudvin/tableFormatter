import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReviewQueue
from app.sheets.client import SheetsClient
from app.sheets.schema import REVIEW_COLUMNS

REVIEW_DONE_STATUS = "Проверка завершена"
REVIEW_OPEN_STATUS = "Открыта"


async def publish_open_reviews(db: AsyncSession, sheets: SheetsClient, review_sheet_name: str) -> int:
    rows = await sheets.read_values(f"'{review_sheet_name}'!A:F")
    existing = {str(row[0]).strip() for row in rows[1:] if row}
    result = await db.execute(
        select(ReviewQueue).where(ReviewQueue.status == "OPEN").order_by(ReviewQueue.created_at)
    )
    new_rows: list[list[str]] = []
    for item in result.scalars():
        if item.purchase_number in existing:
            continue
        new_rows.append(
            [
                item.purchase_number,
                item.reason,
                REVIEW_OPEN_STATUS,
                json.dumps(item.payload or {}, ensure_ascii=False),
                item.created_at.astimezone().strftime("%d.%m.%Y %H:%M:%S"),
                item.updated_at.astimezone().strftime("%d.%m.%Y %H:%M:%S"),
            ]
        )
    if not rows:
        await sheets.write_values(f"'{review_sheet_name}'!A1", [REVIEW_COLUMNS])
        start_row = 2
    else:
        start_row = len(rows) + 1
    if new_rows:
        await sheets.write_values(f"'{review_sheet_name}'!A{start_row}", new_rows)
    return len(new_rows)


async def sync_review_sheet(db: AsyncSession, sheets: SheetsClient, review_sheet_name: str) -> int:
    rows = await sheets.read_values(f"'{review_sheet_name}'!A:F")
    completed = {
        str(row[0]).strip()
        for row in rows[1:]
        if len(row) >= 3 and str(row[2]).strip().casefold() == REVIEW_DONE_STATUS.casefold()
    }
    if not completed:
        return 0
    result = await db.execute(
        select(ReviewQueue).where(ReviewQueue.purchase_number.in_(completed), ReviewQueue.status == "OPEN")
    )
    count = 0
    for item in result.scalars():
        item.status = "DONE"
        item.closed_at = datetime.now(UTC)
        count += 1
    await db.commit()
    return count
