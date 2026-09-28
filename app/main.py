"""FastAPI server: serves the frontend, one JSON endpoint per widget, the
Settings API (add/delete accounts from the app) and an optional password login."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, load_raw_config, parse_config
from .settings import SettingsStore, apply_settings
from .settings_api import router as settings_router
from .summarizer import Summarizer
from .widgets import Context, Widget, WidgetError, get_widget_class

log = logging.getLogger("dashboard")
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


@dataclass
class CacheEntry:
    data: dict[str, Any] | None = None
    error: str | None = None
    fetched_at: float = 0.0
    updated_at: float | None = None  # last *successful* fetch


class Dashboard:
    """Holds the configured widgets and caches their results."""

    def __init__(self, config: AppConfig, http: httpx.AsyncClient):
        self.config = config
        self.ctx = Context(http=http, tz=ZoneInfo(config.timezone), summarizer=Summarizer(config.ai, http))
        self.widgets: dict[str, Widget] = {}
        self.setup_errors: dict[str, str] = {}
        self.cache: dict[str, CacheEntry] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        for wc in config.widgets:
            if not wc.enabled:
                continue
            try:
                self.widgets[wc.id] = get_widget_class(wc.type)(wc, self.ctx)
            except (KeyError, WidgetError) as exc:
                msg = exc.args[0] if exc.args else str(exc)
                log.error("widget %s: %s", wc.id, msg)
                self.setup_errors[wc.id] = msg
            self.cache[wc.id] = CacheEntry()
            self.locks[wc.id] = asyncio.Lock()

    def layout(self) -> dict[str, Any]:
        return {
            "title": self.config.title,
            "timezone": self.config.timezone,
            "ai": self.ctx.summarizer.available,
            "widgets": [
                {
                    "id": wc.id,
                    "type": wc.type,
                    "title": wc.title,
                    "size": wc.size,
                    "refresh_minutes": self.widgets[wc.id].refresh_minutes if wc.id in self.widgets else None,
                }
                for wc in self.config.widgets
                if wc.enabled
            ],
        }

    async def get(self, widget_id: str, force: bool = False) -> dict[str, Any]:
        if widget_id not in self.cache:
            raise KeyError(widget_id)
        if widget_id in self.setup_errors:
            return {"data": None, "error": self.setup_errors[widget_id], "updated_at": None}

        widget = self.widgets[widget_id]
        entry = self.cache[widget_id]
        async with self.locks[widget_id]:
            ttl = widget.refresh_minutes * 60
            # Failed fetches are retried sooner than the normal refresh interval.
            if entry.error and entry.data is None:
                ttl = min(ttl, 60)
            if force or time.time() - entry.fetched_at >= ttl:
                await self._refresh(widget, entry)
        return {"data": entry.data, "error": entry.error, "updated_at": entry.updated_at}

    async def _refresh(self, widget: Widget, entry: CacheEntry) -> None:
        try:
            entry.data = await widget.fetch()
            entry.error = None
            entry.updated_at = time.time()
        except WidgetError as exc:
            entry.error = str(exc)
        except httpx.HTTPStatusError as exc:
            entry.error = f"HTTP {exc.response.status_code} from {exc.request.url.host}"
        except httpx.HTTPError as exc:
            entry.error = f"Network error: {exc.__class__.__name__} {exc}".strip()
        except Exception as exc:  # keep one broken widget from breaking the page
            log.exception("widget %s crashed", widget.id)
            entry.error = f"{exc.__class__.__name__}: {exc}"
        finally:
            entry.fetched_at = time.time()
        # Keep showing the last good data (marked stale by the frontend) on error.


SESSION_COOKIE = "dash_session"


def session_token(password: str) -> str:
    return hmac.new(password.encode(), b"dashboard-session-v1", hashlib.sha256).hexdigest()


def create_app(config_path: str | os.PathLike | None = None,
               settings_path: str | os.PathLike | None = None) -> FastAPI:
    load_dotenv(ROOT / ".env")
    config_path = Path(config_path or os.environ.get("DASHBOARD_CONFIG", ROOT / "config.yaml"))
    settings_path = Path(settings_path or os.environ.get("DASHBOARD_SETTINGS", ROOT / "data" / "settings.json"))
    password = os.environ.get("DASHBOARD_PASSWORD", "")

    def build(app: FastAPI) -> None:
        raw = apply_settings(load_raw_config(config_path), app.state.settings.data)
        app.state.dashboard = Dashboard(parse_config(raw, expand=False), app.state.http)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (personal-dashboard; +https://github.com)"},
        )
        app.state.settings = SettingsStore(settings_path)
        app.state.rebuild = lambda: build(app)
        build(app)
        yield
        await app.state.http.aclose()

    app = FastAPI(title="Dashboard", lifespan=lifespan)

    if password:
        expected = session_token(password)

        @app.middleware("http")
        async def require_login(request: Request, call_next):
            path = request.url.path
            if path in ("/login", "/manifest.webmanifest") or path.startswith("/static/"):
                return await call_next(request)
            if hmac.compare_digest(request.cookies.get(SESSION_COOKIE, ""), expected):
                return await call_next(request)
            if path.startswith("/api/"):
                return JSONResponse({"detail": "login required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    async def login_page():
        return FileResponse(STATIC / "login.html")

    @app.post("/login")
    async def login(request: Request):
        body = await request.json()
        if not password or not hmac.compare_digest(str(body.get("password", "")), password):
            await asyncio.sleep(1)  # slow down guessing
            raise HTTPException(401, "Wrong password")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, session_token(password), max_age=180 * 86400,
                        httponly=True, samesite="lax")
        return resp

    @app.get("/api/layout")
    async def layout():
        return {**app.state.dashboard.layout(), "login": bool(password)}

    @app.get("/api/widgets/{widget_id}")
    async def widget(widget_id: str, force: bool = False):
        try:
            return await app.state.dashboard.get(widget_id, force=force)
        except KeyError:
            raise HTTPException(404, f"no widget '{widget_id}'") from None

    @app.post("/api/reload")
    async def reload():
        """Re-read config.yaml and the settings without restarting the server."""
        build(app)
        return app.state.dashboard.layout()

    app.include_router(settings_router)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/settings")
    async def settings_page():
        return FileResponse(STATIC / "settings.html")

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    load_dotenv(ROOT / ".env")
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    # With a password set, listen on the network so phones/other PCs can connect.
    host = os.environ.get("HOST") or ("0.0.0.0" if password else "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not password:
        log.warning("HOST=%s without DASHBOARD_PASSWORD: anyone on your network can open the dashboard", host)
    uvicorn.run(create_app(), host=host, port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    run()
