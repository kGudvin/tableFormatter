import asyncio
import logging

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import make_session_factory
from app.logging import configure_logging
from app.services.job_queue import claim_next_job, finish_job, recover_interrupted_jobs
from app.services.processor import ProcurementProcessor
from app.sheets.client import GoogleSheetsClient
from app.sources.eis44 import Eis44Source

logger = structlog.get_logger(__name__)
processing_lock = asyncio.Lock()


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
            review_sheet_name=settings.google_review_sheet,
        )
        await processor.backfill_empty_rows()
    finally:
        await source.aclose()


async def guarded_process_due() -> None:
    settings = get_settings()
    factory = make_session_factory(settings.database_url)
    async with processing_lock, factory() as session:
        try:
            await process_due_once(session)
        except Exception:
            logger.exception("scheduled_processing_failed", task_type="process-due", stage="scheduler")


async def process_queued_once() -> None:
    if processing_lock.locked():
        return
    settings = get_settings()
    factory = make_session_factory(settings.database_url)
    async with processing_lock, factory() as session:
        job = await claim_next_job(session)
        if job is None:
            return
        source = Eis44Source(
            base_url=settings.eis_base_url,
            cache_dir=settings.document_cache_dir,
            min_interval_seconds=settings.eis_min_request_interval_seconds,
            verify_ssl=settings.eis_verify_ssl,
            ca_bundle=settings.eis_ca_bundle,
        )
        processor = ProcurementProcessor(
            source=source,
            sheets=GoogleSheetsClient(
                spreadsheet_id=settings.google_spreadsheet_id,
                credentials_path=str(settings.google_application_credentials),
            ),
            db=session,
            spreadsheet_id=settings.google_spreadsheet_id,
            sheet_name=settings.google_main_sheet,
            review_sheet_name=settings.google_review_sheet,
        )
        error: Exception | None = None
        try:
            if job.job_type == "backfill":
                await processor.backfill_empty_rows()
            elif job.job_type == "range":
                await processor.process_row_range(
                    int(job.payload["start_row"]),
                    int(job.payload["end_row"]),
                    force=bool(job.payload.get("force")),
                )
            elif job.job_type == "numbers":
                await processor.process_purchase_numbers(
                    [str(item) for item in job.payload["purchase_numbers"]],
                    force=bool(job.payload.get("force", True)),
                )
            else:
                raise ValueError(f"Unknown processing job type: {job.job_type}")
        except Exception as exc:
            error = exc
            logger.exception("queued_processing_failed", job_id=job.id, job_type=job.job_type)
        finally:
            await source.aclose()
            await finish_job(session, job, error)


async def run_forever() -> None:
    configure_logging()
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    settings = get_settings()
    factory = make_session_factory(settings.database_url)
    async with factory() as session:
        recovered = await recover_interrupted_jobs(session)
        if recovered:
            logger.warning("interrupted_jobs_requeued", count=recovered)
    scheduler = AsyncIOScheduler(timezone=settings.app_timezone)
    scheduler.add_job(
        guarded_process_due,
        "interval",
        minutes=settings.scheduler_interval_minutes,
        id="process-due",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        process_queued_once,
        "interval",
        seconds=3,
        id="process-queue",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    await process_queued_once()
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
