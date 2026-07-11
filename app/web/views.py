from __future__ import annotations

import re
from html import escape
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select

from app.config import get_settings
from app.db.models import ProcessingJob, ProcessingRun
from app.db.session import make_session_factory
from app.domain.products import format_products
from app.review.sync import publish_open_reviews, sync_review_sheet
from app.services.job_queue import enqueue_job
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


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, message: str | None = None) -> HTMLResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    configured = bool(settings.google_spreadsheet_id)
    sheet_url = _google_sheet_url(settings.google_spreadsheet_id)
    latest_run = await _latest_processing_run()
    latest_job = await _latest_processing_job()
    return HTMLResponse(
        _page(
            title="Форматтер таблиц",
            active="dashboard",
            body=f"""
            {_notice(message)}
            <section class="hero-panel">
              <div>
                <span class="eyebrow">kGudvin tools</span>
                <h2>Форматтер таблиц</h2>
                <p>Проверка закупок, заполнение результатов из ЕИС и синхронизация рабочих листов Google.</p>
              </div>
              <div class="hero-actions">
                <a class="button-link primary" href="{sheet_url}" target="_blank" rel="noopener">Открыть Google Таблицу</a>
                <a class="button-link" href="/ui/help">Справка</a>
              </div>
            </section>
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
            <section class="panel">
              <h2>Ручная обработка</h2>
              <form class="range-form" method="post" action="/ui/actions/process-range">
                <input name="start_row" type="number" min="1" placeholder="Строка с" required>
                <input name="end_row" type="number" min="1" placeholder="Строка по" required>
                <label class="switch"><input type="checkbox" name="force" value="1"><span></span>Перепроверить заполненные</label>
                <button type="submit">▶ Запустить диапазон</button>
              </form>
              <form class="numbers-form" method="post" action="/ui/actions/process-numbers">
                <textarea name="purchase_numbers" placeholder="Номера закупок через пробел, запятую или с новой строки" required></textarea>
                <label class="switch"><input type="checkbox" name="force" value="1" checked><span></span>Перепроверить найденные</label>
                <button type="submit">▶ Запустить номера</button>
              </form>
            </section>
            {_processing_status_panel(latest_job, latest_run)}
            """,
        )
    )


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    return HTMLResponse(
        _page(
            title="Справка",
            active="help",
            body=_help_body(),
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
    job, created = await _enqueue("backfill")
    return _redirect(_enqueue_message(job, created))


@router.post("/actions/process-due")
async def process_due(request: Request) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    job, created = await _enqueue("backfill")
    return _redirect(_enqueue_message(job, created))


@router.post("/actions/sync-review")
async def sync_review(request: Request) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    async with make_session_factory(settings.database_url)() as session:
        client = _sheets_client()
        published = await publish_open_reviews(session, client, settings.google_review_sheet)
        closed = await sync_review_sheet(session, client, settings.google_review_sheet)
    return _redirect(f"Добавлено проверок: {published}. Закрыто проверок: {closed}.")


@router.post("/actions/process-range")
async def process_range(
    request: Request,
    start_row: int = Form(...),
    end_row: int = Form(...),
    force: str | None = Form(None),
) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    if start_row < 1 or end_row < 1:
        return _redirect("Номера строк должны быть больше нуля.")
    lower, upper = sorted((start_row, end_row))
    job, created = await _enqueue("range", {"start_row": lower, "end_row": upper, "force": bool(force)})
    return _redirect(_enqueue_message(job, created))


@router.post("/actions/process-numbers")
async def process_numbers(
    request: Request,
    purchase_numbers: str = Form(...),
    force: str | None = Form(None),
) -> RedirectResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    numbers = _parse_purchase_numbers(purchase_numbers)
    if not numbers:
        return _redirect("Не найдено ни одного номера закупки.")
    job, created = await _enqueue("numbers", {"purchase_numbers": numbers, "force": bool(force)})
    return _redirect(_enqueue_message(job, created))


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
            review_sheet_name=settings.google_review_sheet,
        ),
        source,
    )


def _redirect(message: str) -> RedirectResponse:
    return RedirectResponse(f"/ui/?message={escape(message)}", status_code=303)


def _google_sheet_url(spreadsheet_id: str) -> str:
    if not spreadsheet_id:
        return "https://docs.google.com/spreadsheets/"
    return f"https://docs.google.com/spreadsheets/d/{escape(spreadsheet_id)}/edit?hl=ru&gid=1386397104#gid=1386397104"


def _parse_purchase_numbers(value: str) -> list[str]:
    seen: set[str] = set()
    numbers: list[str] = []
    for item in re.findall(r"\d{11,20}", value):
        if item not in seen:
            seen.add(item)
            numbers.append(item)
    return numbers


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


async def _latest_processing_job() -> ProcessingJob | None:
    settings = get_settings()
    try:
        async with make_session_factory(settings.database_url)() as session:
            result = await session.execute(
                select(ProcessingJob).order_by(desc(ProcessingJob.requested_at)).limit(1)
            )
            return result.scalar_one_or_none()
    except Exception:
        return None


async def _enqueue(job_type: str, payload: dict[str, Any] | None = None) -> tuple[ProcessingJob, bool]:
    settings = get_settings()
    async with make_session_factory(settings.database_url)() as session:
        return await enqueue_job(session, job_type, payload)


def _enqueue_message(job: ProcessingJob, created: bool) -> str:
    if created:
        return "Задание принято. Оно продолжит выполняться, даже если закрыть страницу."
    return f"Обработка уже запущена ({_job_status(job.status)}). Новое задание не добавлено."


def _job_status(status: str) -> str:
    return {
        "QUEUED": "ожидает запуска",
        "RUNNING": "выполняется",
        "COMPLETED": "завершено",
        "FAILED": "завершено с ошибкой",
    }.get(status, status)


def _processing_status_panel(job: ProcessingJob | None, run: ProcessingRun | None) -> str:
    rows: list[tuple[str, str]] = []
    if job is not None:
        rows.append(("Задание", _job_status(job.status)))
        rows.append(("Поставлено в очередь", job.requested_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")))
        if job.started_at:
            rows.append(("Начато", job.started_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")))
        if job.finished_at:
            rows.append(("Завершено", job.finished_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")))
        if job.error:
            rows.append(("Ошибка задания", job.error))
    else:
        rows.append(("Задание", "ещё не запускалось"))
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


def _help_body() -> str:
    return """
    <section class="panel help">
      <h2>Как работать с панелью</h2>
      <div class="steps">
        <div><strong>1</strong><span>Проверьте, что Google таблица настроена, а основной лист указан верно.</span></div>
        <div><strong>2</strong><span>Для обычной работы используйте ручную обработку по диапазону строк или списку номеров.</span></div>
        <div><strong>3</strong><span>После запуска смотрите блок статуса и саму Google Таблицу: результат записывается туда.</span></div>
      </div>
    </section>
    <section class="panel help">
      <h2>Быстрые действия</h2>
      <table>
        <thead><tr><th>Кнопка</th><th>Что делает</th><th>Когда нажимать</th></tr></thead>
        <tbody>
          <tr><td>Инициализировать листы</td><td>Добавляет служебные колонки в основной лист и создает лист проверки, если их нет.</td><td>При первом запуске или после изменения структуры таблицы.</td></tr>
          <tr><td>Проверить схему</td><td>Показывает, какие колонки программа нашла в Google Таблице и в какой строке находятся заголовки.</td><td>Если программа пишет не туда или не видит нужные поля.</td></tr>
          <tr><td>Первичная обработка</td><td>Запускает проход по рабочим строкам таблицы, где не заполнены победитель или поставляемый товар.</td><td>Для массового заполнения, лучше запускать осторожно.</td></tr>
          <tr><td>Обработать очередь</td><td>Запускает тот же фоновый проход по строкам, которые требуют дозаполнения.</td><td>Для повторного ручного запуска обработки.</td></tr>
          <tr><td>Синхронизировать проверки</td><td>Добавляет открытые проверки из базы на лист проверки и закрывает те, которые отмечены завершенными.</td><td>После обработки закупок или после работы с листом “Требуется проверка”.</td></tr>
        </tbody>
      </table>
    </section>
    <section class="panel help">
      <h2>Проверка закупки</h2>
      <table>
        <tbody>
          <tr><td>Поле “Номер закупки”</td><td>Введите один номер процедуры. Панель покажет найденный результат по этой закупке.</td></tr>
          <tr><td>Записать в таблицу</td><td>Если галочка включена, результат будет записан в строку этой закупки в Google Таблице. Если выключена, это только просмотр результата.</td></tr>
          <tr><td>Выполнить</td><td>Запускает проверку одного номера сразу, без фоновой очереди.</td></tr>
        </tbody>
      </table>
    </section>
    <section class="panel help">
      <h2>Ручная обработка</h2>
      <table>
        <thead><tr><th>Режим</th><th>Что заполнить</th><th>Как работает</th></tr></thead>
        <tbody>
          <tr><td>Диапазон строк</td><td>“Строка с” и “Строка по”. Например: 120 и 180.</td><td>Программа смотрит только этот кусок таблицы, пропускает строки-разделители и строки без номера закупки.</td></tr>
          <tr><td>Запустить диапазон</td><td>Кнопка запускает обработку указанного диапазона строк.</td><td>Используйте, когда нужно проверить конкретный участок таблицы.</td></tr>
          <tr><td>Перепроверить заполненные</td><td>Галочка рядом с диапазоном строк.</td><td>Если выключена, строки с заполненными “Кто выиграл” и “Поставляемый товар” пропускаются. Если включена, найденные строки проверяются заново.</td></tr>
          <tr><td>Список номеров</td><td>Вставьте номера через пробел, запятую или с новой строки.</td><td>Программа ищет эти номера в таблице и обрабатывает только найденные строки.</td></tr>
          <tr><td>Запустить номера</td><td>Кнопка запускает обработку вставленного списка закупок.</td><td>Используйте, когда нужно точечно проверить несколько процедур.</td></tr>
          <tr><td>Перепроверить найденные</td><td>Галочка рядом со списком номеров.</td><td>Если включена, выбранные номера обрабатываются даже при уже заполненных полях.</td></tr>
        </tbody>
      </table>
    </section>
    <section class="panel help">
      <h2>Статус обработки</h2>
      <table>
        <tbody>
          <tr><td>Фоновая обработка</td><td>Показывает, идет ли сейчас ручной или автоматический запуск.</td></tr>
          <tr><td>Последний запуск</td><td>Время последнего прохода.</td></tr>
          <tr><td>Статус</td><td>RUNNING — идет, COMPLETED — завершено без ошибок, FAILED — были ошибки по отдельным строкам.</td></tr>
          <tr><td>Проверено строк</td><td>Сколько строк или номеров программа взяла в работу.</td></tr>
          <tr><td>Обновлено</td><td>Сколько строк успешно записано в таблицу.</td></tr>
          <tr><td>Ошибок</td><td>Сколько строк не удалось обработать. Подробности смотрятся в логах контейнера.</td></tr>
        </tbody>
      </table>
    </section>
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
      --bg: #050810;
      --bg-soft: #08110f;
      --surface: #101722;
      --surface-strong: #151f2e;
      --text: #f7f9fc;
      --muted: #9fb0c8;
      --line: #26344a;
      --accent: #40cdb7;
      --accent-dark: #2ea994;
      --accent-text: #03120f;
      --ok: #40cdb7;
      --warn: #ffc15a;
      --danger: #f06c75;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 15px/1.45 Arial, sans-serif; background: radial-gradient(circle at top left, rgba(64, 205, 183, .1), transparent 34%), linear-gradient(180deg, var(--bg-soft), var(--bg) 320px); color: var(--text); }}
    header {{ min-height: 66px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 0 32px; background: rgba(12, 17, 27, .9); border-bottom: 1px solid var(--line); backdrop-filter: blur(12px); }}
    h1 {{ margin: 0; font-size: 20px; font-weight: 800; letter-spacing: 0; }}
    nav {{ display: flex; align-items: center; gap: 10px; }}
    nav a {{ color: var(--muted); text-decoration: none; font-weight: 700; border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; }}
    nav a:hover, nav a.active {{ color: var(--text); border-color: rgba(64, 205, 183, .55); background: rgba(64, 205, 183, .08); }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 28px 24px 40px; }}
    .hero-panel {{ background: linear-gradient(135deg, rgba(64, 205, 183, .12), rgba(255, 193, 90, .08)), var(--surface); border: 1px solid var(--line); border-top: 4px solid var(--accent); border-radius: 8px; padding: 24px; margin-bottom: 18px; display: flex; justify-content: space-between; gap: 24px; align-items: center; }}
    .hero-panel h2 {{ margin: 6px 0 8px; font-size: 30px; line-height: 1.12; }}
    .hero-panel p {{ margin: 0; max-width: 650px; color: var(--muted); font-size: 17px; }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
    .hero-actions {{ display: grid; gap: 10px; min-width: 230px; }}
    .button-link {{ min-height: 44px; border: 1px solid var(--line); border-radius: 8px; color: var(--text); text-decoration: none; display: inline-flex; align-items: center; justify-content: center; font-weight: 800; padding: 10px 14px; background: rgba(255, 255, 255, .03); }}
    .button-link:hover {{ border-color: rgba(64, 205, 183, .55); background: rgba(64, 205, 183, .08); }}
    .button-link.primary {{ background: var(--accent); color: var(--accent-text); border-color: var(--accent); }}
    .button-link.primary:hover {{ background: var(--accent-dark); border-color: var(--accent-dark); }}
    .toolbar {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .metric {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--ok); border-radius: 8px; padding: 14px; min-height: 78px; box-shadow: 0 16px 36px rgba(0, 0, 0, .16); }}
    .metric.warn {{ border-left-color: var(--warn); }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 17px; overflow-wrap: anywhere; color: var(--text); }}
    .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 18px; box-shadow: 0 16px 36px rgba(0, 0, 0, .16); }}
    h2 {{ margin: 0 0 14px; font-size: 19px; }}
    .actions {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }}
    button {{ width: 100%; min-height: 44px; border: 1px solid rgba(64, 205, 183, .35); border-radius: 8px; background: rgba(64, 205, 183, .12); color: var(--text); font-weight: 800; cursor: pointer; padding: 10px 12px; }}
    button:hover {{ background: var(--accent); color: var(--accent-text); border-color: var(--accent); }}
    .icon {{ margin-right: 8px; }}
    .check-form {{ display: grid; grid-template-columns: minmax(260px, 1fr) 220px 180px; gap: 12px; align-items: center; }}
    .range-form {{ display: grid; grid-template-columns: 150px 150px 240px minmax(180px, 1fr); gap: 12px; align-items: center; margin-bottom: 14px; }}
    .numbers-form {{ display: grid; grid-template-columns: minmax(320px, 1fr) 240px 220px; gap: 12px; align-items: stretch; }}
    input, textarea {{ border: 1px solid var(--line); border-radius: 8px; padding: 0 12px; font: inherit; background: #0b111d; color: var(--text); outline: none; }}
    input::placeholder, textarea::placeholder {{ color: #7587a3; }}
    input:focus, textarea:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(64, 205, 183, .12); }}
    input {{ min-height: 42px; }}
    textarea {{ min-height: 86px; padding-top: 10px; resize: vertical; }}
    .switch {{ color: var(--muted); display: flex; align-items: center; gap: 8px; font-weight: 700; }}
    .switch input {{ width: 18px; height: 18px; accent-color: var(--accent); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 13px; background: var(--surface-strong); }}
    td {{ color: #dce5f4; }}
    .notice {{ border: 1px solid rgba(64, 205, 183, .38); background: rgba(64, 205, 183, .1); color: #bff4e9; padding: 12px 14px; border-radius: 8px; margin-bottom: 16px; }}
    .error-panel {{ border-left: 4px solid var(--danger); }}
    .help table td:first-child {{ width: 230px; font-weight: 700; }}
    .steps {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .steps div {{ border: 1px solid var(--line); border-radius: 8px; padding: 14px; display: grid; grid-template-columns: 36px 1fr; gap: 10px; align-items: start; background: #0b111d; }}
    .steps strong {{ width: 28px; height: 28px; border-radius: 8px; background: var(--accent); color: var(--accent-text); display: inline-flex; align-items: center; justify-content: center; }}
    .steps span {{ color: var(--text); }}
    @media (max-width: 860px) {{
      header {{ padding: 12px 16px; align-items: flex-start; flex-direction: column; }}
      main {{ padding: 16px; }}
      .hero-panel {{ flex-direction: column; align-items: stretch; padding: 18px; }}
      .hero-panel h2 {{ font-size: 26px; }}
      .toolbar, .actions, .check-form, .range-form, .numbers-form {{ grid-template-columns: 1fr; }}
      .steps {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Форматтер таблиц</h1>
    <nav>
      <a class="{"active" if active == "dashboard" else ""}" href="/ui/">Панель</a>
      <a class="{"active" if active == "help" else ""}" href="/ui/help">Справка</a>
    </nav>
  </header>
  <main>{body}</main>
</body>
</html>"""
