from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.sheets.schema import REVIEW_COLUMNS, SERVICE_COLUMNS, SheetSchema, find_header_row

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
WRITE_MIN_INTERVAL_SECONDS = 1.1
_write_lock = asyncio.Lock()
_next_write_at = 0.0


class SheetsClient(Protocol):
    async def read_values(self, range_name: str) -> list[list[Any]]:
        ...

    async def write_values(self, range_name: str, values: list[list[Any]]) -> None:
        ...

    async def batch_update(self, requests: list[dict[str, Any]]) -> None:
        ...


@dataclass
class GoogleSheetsClient:
    spreadsheet_id: str
    credentials_path: str

    def __post_init__(self) -> None:
        credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            self.credentials_path,
            scopes=SCOPES,
        )
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    async def read_values(self, range_name: str) -> list[list[Any]]:
        def _read() -> list[list[Any]]:
            result = (
                self._service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                    valueRenderOption="UNFORMATTED_VALUE",
                    dateTimeRenderOption="FORMATTED_STRING",
                )
                .execute()
            )
            return cast(list[list[Any]], result.get("values", []))

        return await _with_google_retry(lambda: asyncio.to_thread(_read))

    async def write_values(self, range_name: str, values: list[list[Any]]) -> None:
        def _write() -> None:
            (
                self._service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                )
                .execute()
            )

        await _with_google_retry(lambda: _rate_limited_write(_write))

    async def batch_update(self, requests: list[dict[str, Any]]) -> None:
        if not requests:
            return

        def _update() -> None:
            (
                self._service.spreadsheets()
                .batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": requests})
                .execute()
            )

        await _with_google_retry(lambda: _rate_limited_write(_update))

    async def spreadsheet_metadata(self) -> dict[str, Any]:
        def _read() -> dict[str, Any]:
            result = self._service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
            return cast(dict[str, Any], result)

        return await _with_google_retry(lambda: asyncio.to_thread(_read))

    async def ensure_column_capacity(self, sheet_name: str, required_columns: int) -> None:
        metadata = await self.spreadsheet_metadata()
        sheet = _find_sheet(metadata, sheet_name)
        if sheet is None:
            msg = f"Sheet not found: {sheet_name}"
            raise LookupError(msg)
        properties = cast(dict[str, Any], sheet["properties"])
        grid = cast(dict[str, Any], properties.get("gridProperties", {}))
        current_columns = int(grid.get("columnCount", 0))
        if current_columns >= required_columns:
            return
        await self.batch_update(
            [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": properties["sheetId"],
                            "gridProperties": {"columnCount": required_columns},
                        },
                        "fields": "gridProperties.columnCount",
                    }
                }
            ]
        )

    async def ensure_sheet_exists(self, sheet_name: str) -> None:
        metadata = await self.spreadsheet_metadata()
        if _find_sheet(metadata, sheet_name) is not None:
            return
        await self.batch_update([{"addSheet": {"properties": {"title": sheet_name}}}])


def a1_col(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _find_sheet(metadata: dict[str, Any], sheet_name: str) -> dict[str, Any] | None:
    sheets = cast(list[dict[str, Any]], metadata.get("sheets", []))
    return next(
        (
            item
            for item in sheets
            if item.get("properties", {}).get("title") == sheet_name
        ),
        None,
    )


async def _rate_limited_write[T](operation: Callable[[], T]) -> T:
    global _next_write_at
    async with _write_lock:
        loop = asyncio.get_running_loop()
        delay = max(0.0, _next_write_at - loop.time())
        if delay:
            await asyncio.sleep(delay)
        try:
            return await asyncio.to_thread(operation)
        finally:
            _next_write_at = loop.time() + WRITE_MIN_INTERVAL_SECONDS


async def _with_google_retry[T](operation: Callable[[], Awaitable[T]], attempts: int = 7) -> T:
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status not in {429, 500, 502, 503, 504} or attempt == attempts:
                raise
            retry_after = exc.resp.get("retry-after")
            wait = float(retry_after) if retry_after else delay + random.uniform(0, 0.5)
            await asyncio.sleep(wait)
            delay = min(delay * 2, 60.0)
    raise RuntimeError("Google Sheets retry loop exhausted")


async def inspect_main_sheet(client: SheetsClient, sheet_name: str) -> SheetSchema:
    values = await client.read_values(f"'{sheet_name}'!1:50")
    return find_header_row(values)


async def ensure_service_columns(client: SheetsClient, sheet_name: str) -> SheetSchema:
    values = await client.read_values(f"'{sheet_name}'!1:50")
    schema = find_header_row(values)
    missing = schema.missing_service_columns
    if not missing:
        return schema
    start_col = len(schema.original_headers) + 1
    if isinstance(client, GoogleSheetsClient):
        await client.ensure_column_capacity(sheet_name, start_col + len(missing) - 1)
    range_name = f"'{sheet_name}'!{a1_col(start_col)}{schema.header_row}"
    await client.write_values(range_name, [missing])
    values = await client.read_values(f"'{sheet_name}'!1:50")
    return find_header_row(values, required=[*SERVICE_COLUMNS, *schema.columns.keys()])


async def ensure_review_sheet(client: SheetsClient, review_sheet_name: str) -> None:
    if isinstance(client, GoogleSheetsClient):
        await client.ensure_sheet_exists(review_sheet_name)
    try:
        values = await client.read_values(f"'{review_sheet_name}'!1:1")
    except Exception:
        values = []
    if values and [str(cell).strip() for cell in values[0][: len(REVIEW_COLUMNS)]] == REVIEW_COLUMNS:
        return
    await client.write_values(f"'{review_sheet_name}'!A1", [REVIEW_COLUMNS])
