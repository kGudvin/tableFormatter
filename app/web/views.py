from __future__ import annotations

import re
import secrets
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


@router.get("/login", response_class=HTMLResponse, response_model=None)
async def login_page(request: Request, error: str | None = None) -> HTMLResponse | RedirectResponse:
    settings = get_settings()
    if not settings.web_ui_enabled:
        return RedirectResponse("/", status_code=303)
    if not settings.web_ui_token:
        return RedirectResponse("/ui/", status_code=303)
    return HTMLResponse(
        _page(
            title="Вход",
            active="login",
            body=f"""
            <section class="login-panel">
              <span class="eyebrow">Защищённый доступ</span>
              <h2>Вход в панель</h2>
              {_login_error(error)}
              <form method="post" action="/ui/login">
                <label class="field"><span>Пароль панели</span><input name="password" type="password" autocomplete="current-password" required autofocus></label>
                <button type="submit">Войти</button>
              </form>
            </section>
            """,
        ),
        status_code=401 if error else 200,
    )


@router.post("/login")
async def login(password: str = Form(...)) -> RedirectResponse:
    settings = get_settings()
    if not settings.web_ui_token or not secrets.compare_digest(password, settings.web_ui_token):
        return RedirectResponse("/ui/login?error=1", status_code=303)
    response = RedirectResponse("/ui/", status_code=303)
    response.set_cookie(
        "admin_token",
        settings.web_ui_token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/ui/login", status_code=303)
    response.delete_cookie("admin_token")
    return response


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, message: str | None = None) -> HTMLResponse:
    settings = get_settings()
    require_web_auth(request, settings)
    configured = bool(settings.google_spreadsheet_id)
    sheet_url = _google_sheet_url(settings.google_spreadsheet_id)
    latest_run = await _latest_processing_run()
    latest_job = await _latest_processing_job()
    processing_active = bool(latest_job and latest_job.status in {"QUEUED", "RUNNING"})
    disabled = " disabled" if processing_active else ""
    return HTMLResponse(
        _page(
            title="Форматтер таблиц",
            active="dashboard",
            body=f"""
            {_notice(message)}
            <section class="page-head">
              <div>
                <span class="eyebrow">kGudvin tools</span>
                <h2>Форматтер таблиц</h2>
                <p>Проверка закупок, заполнение результатов из ЕИС и синхронизация рабочих листов Google.</p>
              </div>
              <div class="page-actions">
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
            {_processing_status_panel(latest_job, latest_run)}
            <section class="panel">
              <div class="panel-title"><h2>Быстрые действия</h2></div>
              <div class="actions">
                {_form_button("/ui/actions/init-sheets", "Инициализировать листы", "layout")}
                {_form_button("/ui/actions/inspect-sheet", "Проверить схему", "search")}
                {_form_button("/ui/actions/backfill", "Обработать незаполненные", "play", processing_active)}
                {_form_button("/ui/actions/sync-review", "Синхронизировать проверки", "check")}
              </div>
            </section>
            <section class="panel">
              <div class="panel-title"><h2>Проверка закупки</h2><span class="section-tag">один номер</span></div>
              <form class="check-form" method="post" action="/ui/actions/check-purchase">
                <label class="field"><span>Номер закупки</span><input name="purchase_number" inputmode="numeric" pattern="[0-9]{{11,20}}" placeholder="Например, 0128200000125008979" autocomplete="off" required></label>
                <label class="switch"><input type="checkbox" name="write" value="1"><span></span>Записать в таблицу</label>
                <button type="submit">▶ Выполнить</button>
              </form>
            </section>
            <section class="panel">
              <div class="panel-title"><h2>Ручная обработка</h2><span class="section-tag">диапазон или список</span></div>
              <form class="range-form" method="post" action="/ui/actions/process-range">
                <label class="field"><span>Строка с</span><input name="start_row" type="number" min="1" placeholder="1" required></label>
                <label class="field"><span>Строка по</span><input name="end_row" type="number" min="1" placeholder="100" required></label>
                <label class="switch"><input type="checkbox" name="force" value="1"><span></span>Перепроверить заполненные</label>
                <button type="submit"{disabled}>▶ Запустить диапазон</button>
              </form>
              <form class="numbers-form" method="post" action="/ui/actions/process-numbers">
                <label class="field"><span>Номера закупок</span><textarea name="purchase_numbers" placeholder="Через пробел, запятую или с новой строки" required></textarea></label>
                <label class="switch"><input type="checkbox" name="force" value="1" checked><span></span>Перепроверить найденные</label>
                <button type="submit"{disabled}>▶ Запустить номера</button>
              </form>
            </section>
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
        proxy_url=settings.eis_proxy_url,
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


def _login_error(error: str | None) -> str:
    return "<div class='login-error'>Неверный пароль.</div>" if error else ""


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
    raw_status = job.status if job is not None else (run.status if run is not None else "IDLE")
    active = raw_status in {"QUEUED", "RUNNING"}
    status_text = _job_status(raw_status) if raw_status != "IDLE" else "ещё не запускалось"
    state = {
        "QUEUED": "active",
        "RUNNING": "active",
        "COMPLETED": "success",
        "FAILED": "danger",
    }.get(raw_status, "neutral")
    checked = str(run.checked_rows) if run is not None else "0"
    updated = str(run.updates_count) if run is not None else "0"
    errors = str(run.errors_count) if run is not None else "0"
    timestamp = "—"
    if job is not None:
        timestamp_value = job.started_at or job.requested_at
        timestamp = timestamp_value.astimezone().strftime("%d.%m.%Y %H:%M")
    elif run is not None:
        timestamp = run.started_at.astimezone().strftime("%d.%m.%Y %H:%M")
    error = f'<div class="status-error">{escape(job.error)}</div>' if job and job.error else ""
    return f"""
    <section class="panel status-panel" data-processing-active="{str(active).lower()}">
      <div class="panel-title">
        <h2>Статус обработки</h2>
        <span class="status-badge {state}"><i></i>{escape(status_text)}</span>
      </div>
      <dl class="status-grid">
        <div><dt>Последний запуск</dt><dd>{timestamp}</dd></div>
        <div><dt>Проверено</dt><dd>{checked}</dd></div>
        <div><dt>Обновлено</dt><dd>{updated}</dd></div>
        <div><dt>Ошибок</dt><dd class="{'has-errors' if errors != '0' else ''}">{errors}</dd></div>
      </dl>
      {error}
    </section>
    """


def _metric(label: str, value: str, ok: bool) -> str:
    state = "ok" if ok else "warn"
    return f"<div class='metric {state}'><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"


def _form_button(action: str, label: str, icon: str, disabled: bool = False) -> str:
    icons = {"layout": "▦", "search": "⌕", "play": "▶", "refresh": "↻", "check": "✓"}
    disabled_attr = " disabled" if disabled else ""
    return f"""
    <form method="post" action="{action}">
      <button type="submit" title="{escape(label)}"{disabled_attr}><span class="icon">{icons[icon]}</span>{escape(label)}</button>
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
          <tr><td>Обработать незаполненные</td><td>Запускает проход по рабочим строкам таблицы, где не заполнены победитель или поставляемый товар.</td><td>Для массового заполнения и повторного дозаполнения.</td></tr>
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
          <tr><td>Статус</td><td>Показывает, ожидает ли задание запуска, выполняется, завершено или требует внимания.</td></tr>
          <tr><td>Проверено строк</td><td>Сколько строк или номеров программа взяла в работу.</td></tr>
          <tr><td>Обновлено</td><td>Сколько строк успешно записано в таблицу.</td></tr>
          <tr><td>Ошибок</td><td>Сколько строк не удалось обработать. Подробности смотрятся в логах контейнера.</td></tr>
        </tbody>
      </table>
    </section>
    """


def _page(title: str, active: str, body: str, status_code: int = 200) -> str:
    del status_code
    logout = ""
    if get_settings().web_ui_token and active != "login":
        logout = '<form class="logout-form" method="post" action="/ui/logout"><button type="submit">Выйти</button></form>'
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
    body {{ margin: 0; font: 15px/1.45 Arial, sans-serif; background: var(--bg); color: var(--text); }}
    header {{ min-height: 60px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 0 32px; background: #0c111b; border-bottom: 1px solid var(--line); }}
    h1 {{ margin: 0; font-size: 20px; font-weight: 800; letter-spacing: 0; }}
    nav {{ display: flex; align-items: center; gap: 10px; }}
    nav a {{ color: var(--muted); text-decoration: none; font-weight: 700; border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; }}
    nav a:hover, nav a.active {{ color: var(--text); border-color: rgba(64, 205, 183, .55); background: rgba(64, 205, 183, .08); }}
    .logout-form {{ margin: 0; }}
    .logout-form button {{ width: auto; min-height: 38px; padding: 8px 12px; color: var(--muted); background: transparent; border-color: var(--line); }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 24px 24px 40px; }}
    .page-head {{ margin-bottom: 18px; display: flex; justify-content: space-between; gap: 24px; align-items: end; }}
    .page-head h2 {{ margin: 4px 0 5px; font-size: 26px; line-height: 1.15; }}
    .page-head p {{ margin: 0; max-width: 680px; color: var(--muted); font-size: 15px; }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
    .page-actions {{ display: flex; gap: 8px; align-items: center; }}
    .button-link {{ min-height: 44px; border: 1px solid var(--line); border-radius: 8px; color: var(--text); text-decoration: none; display: inline-flex; align-items: center; justify-content: center; font-weight: 800; padding: 10px 14px; background: rgba(255, 255, 255, .03); }}
    .button-link:hover {{ border-color: rgba(64, 205, 183, .55); background: rgba(64, 205, 183, .08); }}
    .button-link.primary {{ background: var(--accent); color: var(--accent-text); border-color: var(--accent); }}
    .button-link.primary:hover {{ background: var(--accent-dark); border-color: var(--accent-dark); }}
    .toolbar {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .metric {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--ok); border-radius: 8px; padding: 14px; min-height: 78px; box-shadow: 0 16px 36px rgba(0, 0, 0, .16); }}
    .metric.warn {{ border-left-color: var(--warn); }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 17px; overflow-wrap: anywhere; color: var(--text); }}
    .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    h2 {{ margin: 0 0 14px; font-size: 19px; }}
    .panel-title {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }}
    .panel-title h2 {{ margin: 0; }}
    .section-tag {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    .actions {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    button {{ width: 100%; min-height: 44px; border: 1px solid rgba(64, 205, 183, .35); border-radius: 8px; background: rgba(64, 205, 183, .12); color: var(--text); font-weight: 800; cursor: pointer; padding: 10px 12px; }}
    button:hover {{ background: var(--accent); color: var(--accent-text); border-color: var(--accent); }}
    button:disabled {{ cursor: not-allowed; opacity: .42; background: rgba(159, 176, 200, .07); border-color: var(--line); color: var(--muted); }}
    .icon {{ margin-right: 8px; }}
    .check-form {{ display: grid; grid-template-columns: minmax(260px, 1fr) 220px 180px; gap: 12px; align-items: end; }}
    .range-form {{ display: grid; grid-template-columns: 150px 150px 240px minmax(180px, 1fr); gap: 12px; align-items: end; margin-bottom: 16px; }}
    .numbers-form {{ display: grid; grid-template-columns: minmax(320px, 1fr) 240px 220px; gap: 12px; align-items: end; }}
    .field {{ display: grid; gap: 6px; min-width: 0; }}
    .field > span {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    input, textarea {{ border: 1px solid var(--line); border-radius: 8px; padding: 0 12px; font: inherit; background: #0b111d; color: var(--text); outline: none; }}
    input::placeholder, textarea::placeholder {{ color: #7587a3; }}
    input:focus, textarea:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(64, 205, 183, .12); }}
    input {{ min-height: 42px; }}
    textarea {{ min-height: 86px; padding-top: 10px; resize: vertical; }}
    .switch {{ color: var(--muted); display: flex; align-items: center; gap: 8px; font-weight: 700; }}
    .switch input {{ width: 18px; height: 18px; accent-color: var(--accent); }}
    .status-panel {{ border-left: 4px solid var(--accent); }}
    .status-grid {{ margin: 0; display: grid; grid-template-columns: 1.4fr repeat(3, 1fr); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    .status-grid div {{ padding: 12px 14px; background: #0b111d; border-right: 1px solid var(--line); }}
    .status-grid div:last-child {{ border-right: 0; }}
    .status-grid dt {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    .status-grid dd {{ margin: 5px 0 0; color: var(--text); font-size: 18px; font-weight: 800; }}
    .status-grid .has-errors {{ color: var(--danger); }}
    .status-badge {{ display: inline-flex; align-items: center; gap: 7px; min-height: 28px; padding: 4px 9px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font-size: 12px; font-weight: 800; }}
    .status-badge i {{ width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }}
    .status-badge.active {{ color: #ffe2a3; border-color: rgba(255, 193, 90, .45); background: rgba(255, 193, 90, .08); }}
    .status-badge.active i {{ background: var(--warn); box-shadow: 0 0 0 4px rgba(255, 193, 90, .12); }}
    .status-badge.success {{ color: #bff4e9; border-color: rgba(64, 205, 183, .42); background: rgba(64, 205, 183, .08); }}
    .status-badge.success i {{ background: var(--ok); }}
    .status-badge.danger {{ color: #ffc4c8; border-color: rgba(240, 108, 117, .45); background: rgba(240, 108, 117, .08); }}
    .status-badge.danger i {{ background: var(--danger); }}
    .status-error {{ margin-top: 12px; border: 1px solid rgba(240, 108, 117, .35); border-radius: 8px; padding: 10px 12px; color: #ffc4c8; background: rgba(240, 108, 117, .07); overflow-wrap: anywhere; }}
    .login-panel {{ width: min(420px, 100%); margin: 12vh auto 0; padding: 24px; border: 1px solid var(--line); border-top: 4px solid var(--accent); border-radius: 8px; background: var(--surface); }}
    .login-panel h2 {{ margin: 6px 0 18px; font-size: 24px; }}
    .login-panel form {{ display: grid; gap: 16px; }}
    .login-error {{ margin-bottom: 14px; padding: 10px 12px; border: 1px solid rgba(240, 108, 117, .4); border-radius: 8px; color: #ffc4c8; background: rgba(240, 108, 117, .07); }}
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
    @media (max-width: 980px) {{
      .toolbar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .actions {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .check-form, .range-form, .numbers-form {{ grid-template-columns: 1fr 1fr; }}
      .check-form .field, .numbers-form .field {{ grid-column: 1 / -1; }}
    }}
    @media (max-width: 640px) {{
      header {{ padding: 12px 16px; align-items: flex-start; flex-direction: column; }}
      main {{ padding: 16px; }}
      .page-head {{ flex-direction: column; align-items: stretch; }}
      .page-head h2 {{ font-size: 24px; }}
      .page-actions {{ display: grid; grid-template-columns: 1fr 1fr; }}
      .toolbar, .actions, .check-form, .range-form, .numbers-form {{ grid-template-columns: 1fr; }}
      .check-form .field, .numbers-form .field {{ grid-column: auto; }}
      .status-grid {{ grid-template-columns: 1fr 1fr; }}
      .status-grid div {{ border-bottom: 1px solid var(--line); }}
      .status-grid div:nth-child(2n) {{ border-right: 0; }}
      .status-grid div:nth-last-child(-n+2) {{ border-bottom: 0; }}
      .section-tag {{ display: none; }}
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
      {logout}
    </nav>
  </header>
  <main>{body}</main>
  <script>
    const activeJob = document.querySelector('[data-processing-active="true"]');
    if (activeJob) window.setTimeout(() => window.location.replace('/ui/'), 5000);
  </script>
</body>
</html>"""
