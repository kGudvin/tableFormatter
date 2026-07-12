from fastapi import HTTPException, Request, status

from app.config import Settings


def require_web_auth(request: Request, settings: Settings) -> None:
    if not settings.web_ui_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not settings.web_ui_token:
        return
    token = request.headers.get("x-admin-token") or request.cookies.get("admin_token")
    if token != settings.web_ui_token:
        if "text/html" in request.headers.get("accept", ""):
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/ui/login"},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
