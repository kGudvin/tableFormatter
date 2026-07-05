from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.web.security import require_web_auth
from app.web.views import _page


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


def test_web_auth_hides_disabled_ui() -> None:
    app = FastAPI()

    @app.get("/")
    async def route(request: Request) -> dict[str, bool]:
        require_web_auth(request, Settings(web_ui_enabled=False))
        return {"ok": True}

    assert TestClient(app).get("/").status_code == 404


def test_page_renders_admin_shell() -> None:
    html = _page("Тест", "dashboard", "<section>ok</section>")
    assert "Закупки 44-ФЗ" in html
    assert "<section>ok</section>" in html
    assert "/ui/" in html
