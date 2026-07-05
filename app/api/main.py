from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.web.views import router as web_router

app = FastAPI(title="Procurement Autofill Health API", docs_url=None, redoc_url=None)
app.include_router(web_router, prefix="/ui")


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
