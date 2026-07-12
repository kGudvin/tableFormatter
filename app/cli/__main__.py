from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import make_session_factory
from app.services.processor import ProcurementProcessor
from app.sheets.client import (
    GoogleSheetsClient,
    ensure_review_sheet,
    ensure_service_columns,
    inspect_main_sheet,
)
from app.sources.eis44 import Eis44Source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-sheets")
    sub.add_parser("doctor")
    sub.add_parser("test-google-access")
    sub.add_parser("inspect-sheet")
    sub.add_parser("backfill-2026")
    sub.add_parser("process-due")
    sub.add_parser("sync-review-sheet")
    check = sub.add_parser("check-purchase")
    check.add_argument("purchase_number")
    check.add_argument("--write", action="store_true")
    inspect_xlsx = sub.add_parser("inspect-xlsx")
    inspect_xlsx.add_argument("path", type=Path)
    return parser


async def main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    if args.command == "doctor":
        await _doctor(settings)
        return

    client = GoogleSheetsClient(
        spreadsheet_id=settings.google_spreadsheet_id,
        credentials_path=str(settings.google_application_credentials),
    )
    if args.command == "init-sheets":
        schema = await ensure_service_columns(client, settings.google_main_sheet)
        await ensure_review_sheet(client, settings.google_review_sheet)
        print(json.dumps({"header_row": schema.header_row, "columns": schema.columns}, ensure_ascii=False, indent=2))
        return
    if args.command == "test-google-access":
        schema = await inspect_main_sheet(client, settings.google_main_sheet)
        print(json.dumps({"ok": True, "header_row": schema.header_row}, ensure_ascii=False))
        return
    if args.command == "inspect-sheet":
        schema = await inspect_main_sheet(client, settings.google_main_sheet)
        print(json.dumps({"header_row": schema.header_row, "columns": schema.columns}, ensure_ascii=False, indent=2))
        return
    if args.command == "inspect-xlsx":
        from app.cli.inspect_xlsx import inspect_xlsx

        print(json.dumps(inspect_xlsx(args.path), ensure_ascii=False, indent=2))
        return

    factory = make_session_factory(settings.database_url)
    async with factory() as session:
        await _run_processing_command(args, settings, client, session)


async def _run_processing_command(
    args: argparse.Namespace, settings: Any, client: GoogleSheetsClient, session: AsyncSession
) -> None:
    source = Eis44Source(
        base_url=settings.eis_base_url,
        cache_dir=settings.document_cache_dir,
        min_interval_seconds=settings.eis_min_request_interval_seconds,
        verify_ssl=settings.eis_verify_ssl,
        ca_bundle=settings.eis_ca_bundle,
        proxy_url=settings.eis_proxy_url,
    )
    processor = ProcurementProcessor(
        source=source,
        sheets=client,
        db=session,
        spreadsheet_id=settings.google_spreadsheet_id,
        sheet_name=settings.google_main_sheet,
        review_sheet_name=settings.google_review_sheet,
    )
    try:
        if args.command == "backfill-2026":
            await processor.backfill_empty_rows()
            print("backfill completed")
        elif args.command == "process-due":
            await processor.backfill_empty_rows()
            print("due processing completed")
        elif args.command == "check-purchase":
            result = await processor.process_purchase(args.purchase_number, write=args.write)
            print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
        elif args.command == "sync-review-sheet":
            from app.review.sync import sync_review_sheet

            count = await sync_review_sheet(session, client, settings.google_review_sheet)
            print(json.dumps({"closed": count}, ensure_ascii=False))
    finally:
        await source.aclose()


def main() -> None:
    asyncio.run(main_async(build_parser().parse_args()))


async def _doctor(settings: Any) -> None:
    checks: dict[str, str] = {}
    checks["GOOGLE_SPREADSHEET_ID"] = "ok" if settings.google_spreadsheet_id else "missing"
    checks["GOOGLE_APPLICATION_CREDENTIALS"] = (
        "ok" if settings.google_application_credentials.exists() else "missing"
    )
    checks["DATABASE_URL"] = "ok" if settings.database_url else "missing"
    if settings.google_spreadsheet_id and settings.google_application_credentials.exists():
        try:
            client = GoogleSheetsClient(
                spreadsheet_id=settings.google_spreadsheet_id,
                credentials_path=str(settings.google_application_credentials),
            )
            schema = await inspect_main_sheet(client, settings.google_main_sheet)
            checks["GOOGLE_SHEETS_ACCESS"] = f"ok, header_row={schema.header_row}"
        except Exception as exc:
            checks["GOOGLE_SHEETS_ACCESS"] = f"failed: {type(exc).__name__}"
    else:
        checks["GOOGLE_SHEETS_ACCESS"] = "skipped"
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
