from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProcessingJob


async def enqueue_job(
    session: AsyncSession, job_type: str, payload: dict[str, Any] | None = None
) -> tuple[ProcessingJob, bool]:
    # Serialize concurrent button clicks across all web processes.
    await session.execute(text("SELECT pg_advisory_xact_lock(76234001)"))
    active = await session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.status.in_(("QUEUED", "RUNNING")))
        .order_by(ProcessingJob.requested_at)
        .limit(1)
    )
    if active is not None:
        return active, False
    job = ProcessingJob(job_type=job_type, payload=payload or {})
    session.add(job)
    await session.commit()
    return job, True


async def recover_interrupted_jobs(session: AsyncSession) -> int:
    result = await session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.status == "RUNNING")
        .values(status="QUEUED", started_at=None, error="Worker restarted; job returned to queue")
    )
    await session.commit()
    return int(result.rowcount or 0)


async def claim_next_job(session: AsyncSession) -> ProcessingJob | None:
    job = await session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.status == "QUEUED")
        .order_by(ProcessingJob.requested_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "RUNNING"
    job.started_at = datetime.now(UTC)
    await session.commit()
    return job


async def finish_job(session: AsyncSession, job: ProcessingJob, error: Exception | None = None) -> None:
    job.status = "FAILED" if error else "COMPLETED"
    job.error = f"{type(error).__name__}: {error}"[:2000] if error else None
    job.finished_at = datetime.now(UTC)
    await session.commit()
