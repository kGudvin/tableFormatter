import asyncio

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import make_session_factory
from app.logging import configure_logging
from app.services.processor import ProcurementProcessor
from app.sheets.client import GoogleSheetsClient
from app.sources.eis44 import Eis44Source

logger = structlog.get_logger(__name__)


async def process_due_once(session: AsyncSession) -> None:
    settings = get_settings()
    sheets = GoogleSheetsClient(
        spreadsheet_id=settings.google_spreadsheet_id,
        credentials_path=str(settings.google_application_credentials),
    )
    source = Eis44Source(
        base_url=settings.eis_base_url,
        cache_dir=settings.document_cache_dir,
        min_interval_seconds=settings.eis_min_request_interval_seconds,
        verify_ssl=settings.eis_verify_ssl,
        ca_bundle=settings.eis_ca_bundle,
    )
    try:
        processor = ProcurementProcessor(
            source=source,
            sheets=sheets,
            db=session,
            spreadsheet_id=settings.google_spreadsheet_id,
            sheet_name=settings.google_main_sheet,
        )
        await processor.backfill_empty_rows()
    finally:
        await source.aclose()


async def guarded_process_due() -> None:
    settings = get_settings()
    factory = make_session_factory(settings.database_url)
    async with factory() as session:
        try:
            await process_due_once(session)
        except Exception:
            logger.exception("scheduled_processing_failed", task_type="process-due", stage="scheduler")


async def run_forever() -> None:
    configure_logging()
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=settings.app_timezone)
    scheduler.add_job(
        guarded_process_due,
        "interval",
        minutes=settings.scheduler_interval_minutes,
        id="process-due",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    await guarded_process_due()
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
