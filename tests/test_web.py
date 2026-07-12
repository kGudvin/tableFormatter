from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.models import ProcessingRun
from app.web.security import require_web_auth
from app.web.views import (
    _help_body,
    _login_error,
    _page,
    _parse_purchase_numbers,
    _processing_status_panel,
)


def test_web_auth_allows_when_token_empty() -> None:
    app = FastAPI()

    @app.get("/")
    async def route(request: Request) -> dict[str, bool]:
        require_web_auth(request, Settings(web_ui_token=""))
        return {"ok": True}

    assert TestClient(app).get("/").json() == {"ok": True}


def test_web_auth_rejects_wrong_token() -> None:
    app = FastAPI()

    @app.get("/")
    async def route(request: Request) -> dict[str, bool]:
        require_web_auth(request, Settings(web_ui_token="secret"))
        return {"ok": True}

    assert TestClient(app).get("/").status_code == 401
    assert TestClient(app).get("/", headers={"x-admin-token": "secret"}).status_code == 200


def test_web_auth_redirects_browser_to_login() -> None:
    app = FastAPI()

    @app.get("/")
    async def route(request: Request) -> dict[str, bool]:
        require_web_auth(request, Settings(web_ui_token="secret"))
        return {"ok": True}

    response = TestClient(app).get(
        "/", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login"


def test_web_auth_hides_disabled_ui() -> None:
    app = FastAPI()

    @app.get("/")
    async def route(request: Request) -> dict[str, bool]:
        require_web_auth(request, Settings(web_ui_enabled=False))
        return {"ok": True}

    assert TestClient(app).get("/").status_code == 404


def test_page_renders_admin_shell() -> None:
    html = _page("Тест", "dashboard", "<section>ok</section>")
    assert "Форматтер таблиц" in html
    assert "<section>ok</section>" in html
    assert "/ui/" in html
    assert "/ui/help" in html


def test_help_body_describes_main_controls() -> None:
    html = _help_body()
    assert "Инициализировать листы" in html
    assert "Запустить диапазон" in html
    assert "Запустить номера" in html
    assert "Статус обработки" in html


def test_parse_purchase_numbers_deduplicates_input() -> None:
    assert _parse_purchase_numbers("0372200113126000006, text\n0372200113126000006 0128200000126003312") == [
        "0372200113126000006",
        "0128200000126003312",
    ]


def test_processing_status_panel_uses_readable_status_and_counters() -> None:
    run = ProcessingRun(
        job_type="backfill",
        status="COMPLETED",
        started_at=datetime(2026, 7, 12, tzinfo=UTC),
        checked_rows=15,
        updates_count=12,
        errors_count=0,
    )

    html = _processing_status_panel(None, run)

    assert "завершено" in html
    assert "COMPLETED" not in html
    assert ">15<" in html
    assert 'data-processing-active="false"' in html


def test_login_error_is_safe_and_readable() -> None:
    assert _login_error(None) == ""
    assert "Неверный пароль" in _login_error("1")
