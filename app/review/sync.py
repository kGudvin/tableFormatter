from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReviewQueue
from app.sheets.client import SheetsClient


async def sync_review_sheet(db: AsyncSession, sheets: SheetsClient, review_sheet_name: str) -> int:
    rows = await sheets.read_values(f"'{review_sheet_name}'!A:F")
    completed = {
        str(row[0]).strip()
        for row in rows[1:]
        if len(row) >= 3 and str(row[2]).strip().casefold() == "проверка завершена"
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

