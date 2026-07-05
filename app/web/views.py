from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select

from app.config import get_settings
from app.db.models import ProcessingRun
from app.db.session import make_session_factory
from app.domain.products import format_products
from app.review.sync import sync_review_sheet
from app.services.processor import ProcurementProcessor
from app.sheets.client import (
    GoogleSheetsClient,
    ensure_review_sheet,
    ensure_service_columns,
    inspect_main_sheet,
)
from app.sources.eis44 import Eis44Source
from app.web.security import require_web_auth

router = APIRouter()
_background_task: asyncio.Task[None] | None = None
_background_started_at: datetime | None = None
_background_error: str | None = None


@dataclass(frozen=True)
class JobSnapshot:
    running: bool
    started_at: datetime | None
    error: str | None


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, message: str | None = None) -> HTMLResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    configured = bool(settings.google_spreadsheet_id)
    latest_run = await _latest_processing_run()
    job_snapshot = _job_snapshot()
    return HTMLResponse(
        _page(
            title="Закупки 44-ФЗ",
            active="dashboard",
            body=f"""
            {_notice(message)}
            <section class="toolbar">
              {_metric("Google таблица", "настроена" if configured else "не настроена", configured)}
              {_metric("Основной лист", settings.google_main_sheet, True)}
              {_metric("Лист проверки", settings.google_review_sheet, True)}
              {_metric("Планировщик", f"каждые {settings.scheduler_interval_minutes} мин", True)}
            </section>
            <section class="panel">
              <h2>Быстрые действия</h2>
              <div class="actions">
                {_form_button("/ui/actions/init-sheets", "Инициализировать листы", "layout")}
                {_form_button("/ui/actions/inspect-sheet", "Проверить схему", "search")}
                {_form_button("/ui/actions/backfill", "Первичная обработка", "play")}
                {_form_button("/ui/actions/process-due", "Обработать очередь", "refresh")}
                {_form_button("/ui/actions/sync-review", "Синхронизировать проверки", "check")}
              </div>
            </section>
            <section class="panel">
              <h2>Проверка закупки</h2>
              <form class="check-form" method="post" action="/ui/actions/check-purchase">
                <input name="purchase_number" placeholder="Номер закупки" autocomplete="off" required>
                <label class="switch"><input type="checkbox" name="write" value="1"><span></span>Записать в таблицу</label>
                <button type="submit">▶ Выполнить</button>
              </form>
            </section>
            {_processing_status_panel(job_snapshot, latest_run)}
            """,
        )
    )


@router.post("/actions/init-sheets")
async def init_sheets(request: Request) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    client = _sheets_client()
    schema = await ensure_service_columns(client, settings.google_main_sheet)
    await ensure_review_sheet(client, settings.google_review_sheet)
    return _redirect(f"Листы готовы. Заголовки найдены в строке {schema.header_row}.")


@router.post("/actions/inspect-sheet")
async def inspect_sheet(request: Request) -> HTMLResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    schema = await inspect_main_sheet(_sheets_client(), settings.google_main_sheet)
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{index}</td></tr>"
        for name, index in sorted(schema.columns.items(), key=lambda item: item[1])
    )
    return HTMLResponse(
        _page(
            title="Схема листа",
            active="schema",
            body=f"""
            <section class="panel">
              <h2>Строка заголовков: {schema.header_row}</h2>
              <table><thead><tr><th>Колонка</th><th>Индекс</th></tr></thead><tbody>{rows}</tbody></table>
            </section>
            """,
        )
    )


@router.post("/actions/backfill")
async def backfill(request: Request) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    if _job_snapshot().running:
        return _redirect("Обработка уже идет. Статус ниже на этой странице.")
    _start_background_processing()
    return _redirect("Первичная обработка запущена в фоне. Можно оставить страницу открытой и обновлять статус.")


@router.post("/actions/process-due")
async def process_due(request: Request) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    if _job_snapshot().running:
        return _redirect("Обработка уже идет. Статус ниже на этой странице.")
    _start_background_processing()
    return _redirect("Очередь запущена в фоне. Можно оставить страницу открытой и обновлять статус.")


async def _run_background_processing() -> None:
    global _background_error
    settings = get_settings()
    async with make_session_factory(settings.database_url)() as session:
        processor, source = _processor(session)
        try:
            await processor.backfill_empty_rows()
            _background_error = None
        except Exception as exc:
            _background_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            await source.aclose()


def _start_background_processing() -> None:
    global _background_started_at, _background_task, _background_error
    _background_started_at = datetime.now(UTC)
    _background_error = None
    _background_task = asyncio.create_task(_run_background_processing())
    _background_task.add_done_callback(_store_background_exception)


def _job_snapshot() -> JobSnapshot:
    task = _background_task
    return JobSnapshot(
        running=bool(task and not task.done()),
        started_at=_background_started_at,
        error=_background_error,
    )


def _store_background_exception(task: asyncio.Task[None]) -> None:
    global _background_error
    try:
        task.result()
    except Exception as exc:
        _background_error = f"{type(exc).__name__}: {exc}"


@router.post("/actions/sync-review")
async def sync_review(request: Request) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    async with make_session_factory(settings.database_url)() as session:
        count = await sync_review_sheet(session, _sheets_client(), settings.google_review_sheet)
    return _redirect(f"Закрыто проверок: {count}.")


@router.post("/actions/check-purchase")
async def check_purchase(
    request: Request,
    purchase_number: str = Form(...),
    write: str | None = Form(None),
) -> HTMLResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    try:
        async with make_session_factory(settings.database_url)() as session:
            processor, source = _processor(session)
            try:
                result = await processor.process_purchase(purchase_number.strip(), write=bool(write))
            finally:
                await source.aclose()
    except Exception as exc:
        return HTMLResponse(
            _page(
                title="Ошибка проверки",
                active="dashboard",
                body=f"""
                <section class="panel error-panel">
                  <h2>Проверка не выполнена</h2>
                  <table>
                    <tbody>
                      <tr><td>Номер закупки</td><td>{escape(purchase_number.strip())}</td></tr>
                      <tr><td>Тип ошибки</td><td>{escape(type(exc).__name__)}</td></tr>
                      <tr><td>Описание</td><td>{escape(str(exc)[:1000])}</td></tr>
                    </tbody>
                  </table>
                </section>
                """,
            ),
            status_code=500,
        )
    items = {
        "Номер": result.purchase_number,
        "Статус": result.status.value,
        "Победитель": result.winner_name or "",
        "ИНН": result.winner_inn or "",
        "Ставка": str(result.winning_price or ""),
        "Текущая цена контракта": str(result.current_contract_price or ""),
        "Поставляемый товар": format_products(result.products),
        "Причина проверки": result.review_reason or "",
        "Источник контракта": result.contract_url or "",
        "Источник спецификации": result.specification_url or "",
    }
    rows = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in items.items())
    return HTMLResponse(
        _page(
            title="Результат проверки",
            active="dashboard",
            body=f"<section class='panel'><h2>Результат</h2><table><tbody>{rows}</tbody></table></section>",
        )
    )


def _sheets_client() -> GoogleSheetsClient:
    settings = get_settings()
    return GoogleSheetsClient(
        spreadsheet_id=settings.google_spreadsheet_id,
        credentials_path=str(settings.google_application_credentials),
    )


def _processor(session: Any) -> tuple[ProcurementProcessor, Eis44Source]:
    settings = get_settings()
    source = Eis44Source(
        base_url=settings.eis_base_url,
        cache_dir=settings.document_cache_dir,
        min_interval_seconds=settings.eis_min_request_interval_seconds,
        verify_ssl=settings.eis_verify_ssl,
        ca_bundle=settings.eis_ca_bundle,
    )
    return (
        ProcurementProcessor(
            source=source,
            sheets=_sheets_client(),
            db=session,
            spreadsheet_id=settings.google_spreadsheet_id,
            sheet_name=settings.google_main_sheet,
        ),
        source,
    )


def _redirect(message: str) -> RedirectResponse:
    return RedirectResponse(f"/ui/?message={escape(message)}", status_code=303)


def _notice(message: str | None) -> str:
    if not message:
        return ""
    return f"<div class='notice'>{escape(message)}</div>"


async def _latest_processing_run() -> ProcessingRun | None:
    settings = get_settings()
    try:
        async with make_session_factory(settings.database_url)() as session:
            result = await session.execute(select(ProcessingRun).order_by(desc(ProcessingRun.started_at)).limit(1))
            return result.scalar_one_or_none()
    except Exception:
        return None


def _processing_status_panel(snapshot: JobSnapshot, run: ProcessingRun | None) -> str:
    rows: list[tuple[str, str]] = []
    if snapshot.running:
        rows.append(("Фоновая обработка", "идет"))
    elif snapshot.error:
        rows.append(("Фоновая обработка", snapshot.error))
    else:
        rows.append(("Фоновая обработка", "не запущена"))
    if snapshot.started_at:
        rows.append(("Запущена", snapshot.started_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")))
    if run is not None:
        rows.extend(
            [
                ("Последний запуск", run.started_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")),
                ("Статус", run.status),
                ("Проверено строк", str(run.checked_rows)),
                ("Обновлено", str(run.updates_count)),
                ("Ошибок", str(run.errors_count)),
            ]
        )
        if run.finished_at:
            rows.append(("Завершен", run.finished_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")))
    body = "".join(f"<tr><td>{escape(label)}</td><td>{escape(value)}</td></tr>" for label, value in rows)
    return f"""
    <section class="panel">
      <h2>Статус обработки</h2>
      <table><tbody>{body}</tbody></table>
    </section>
    """


def _metric(label: str, value: str, ok: bool) -> str:
    state = "ok" if ok else "warn"
    return f"<div class='metric {state}'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"


def _form_button(action: str, label: str, icon: str) -> str:
    icons = {"layout": "▦", "search": "⌕", "play": "▶", "refresh": "↻", "check": "✓"}
    return f"""
    <form method="post" action="{action}">
      <button type="submit" title="{escape(label)}"><span class="icon">{icons[icon]}</span>{escape(label)}</button>
    </form>
    """


def _page(title: str, active: str, body: str, status_code: int = 200) -> str:
    del status_code
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --surface: #ffffff;
      --text: #20242a;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #176b87;
      --accent-dark: #0f4d61;
      --ok: #107c41;
      --warn: #a15c00;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 15px/1.45 Arial, sans-serif; background: var(--bg); color: var(--text); }}
    header {{ height: 60px; display: flex; align-items: center; justify-content: space-between; padding: 0 28px; background: var(--surface); border-bottom: 1px solid var(--line); }}
    h1 {{ margin: 0; font-size: 20px; font-weight: 700; }}
    nav a {{ color: var(--muted); text-decoration: none; margin-left: 18px; font-weight: 600; }}
    nav a.active {{ color: var(--accent); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    .toolbar {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .metric {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--ok); border-radius: 8px; padding: 14px; min-height: 78px; }}
    .metric.warn {{ border-left-color: var(--warn); }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 17px; overflow-wrap: anywhere; }}
    .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; }}
    .actions {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }}
    button {{ width: 100%; min-height: 42px; border: 0; border-radius: 7px; background: var(--accent); color: white; font-weight: 700; cursor: pointer; padding: 10px 12px; }}
    button:hover {{ background: var(--accent-dark); }}
    .icon {{ margin-right: 8px; }}
    .check-form {{ display: grid; grid-template-columns: minmax(260px, 1fr) 220px 180px; gap: 12px; align-items: center; }}
    input {{ min-height: 42px; border: 1px solid var(--line); border-radius: 7px; padding: 0 12px; font: inherit; }}
    .switch {{ color: var(--muted); display: flex; align-items: center; gap: 8px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 13px; }}
    .notice {{ border: 1px solid #b7dfc4; background: #eef9f1; color: #0f5d32; padding: 12px 14px; border-radius: 8px; margin-bottom: 16px; }}
    .error-panel {{ border-left: 4px solid #b42318; }}
    @media (max-width: 860px) {{
      header {{ padding: 0 16px; }}
      main {{ padding: 16px; }}
      .toolbar, .actions, .check-form {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Закупки 44-ФЗ</h1>
    <nav>
      <a class="{"active" if active == "dashboard" else ""}" href="/ui/">Панель</a>
    </nav>
  </header>
  <main>{body}</main>
</body>
</html>"""
