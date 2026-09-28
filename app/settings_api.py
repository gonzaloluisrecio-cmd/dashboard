"""/api/settings: add and delete sources from the app.

Every add is checked first (the email login works, the channel exists, the feed
parses...) so mistakes show up in the Settings page instead of on a card.
"""
from __future__ import annotations

import asyncio
import imaplib
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from .widgets import WidgetError
from .widgets.email_imap import test_login
from .widgets.feeds import resolve_podcast, resolve_youtube_channel, search_podcasts
from .widgets.gcal import normalize_ics_url, parse_calendar

router = APIRouter(prefix="/api")

EMAIL_PROVIDERS = {
    "gmail": {"host": "imap.gmail.com", "gmail_primary": True},
    "outlook": {"host": "outlook.office365.com"},
    "yahoo": {"host": "imap.mail.yahoo.com"},
    "icloud": {"host": "imap.mail.me.com"},
}


def _text(body: dict[str, Any], field: str, required: bool = True) -> str:
    value = str(body.get(field) or "").strip()
    if required and not value:
        raise HTTPException(400, f"'{field}' is required")
    return value


def _save(request: Request, key: str, entry: dict[str, Any]) -> dict[str, Any]:
    store = request.app.state.settings
    entry = store.add(key, entry)
    request.app.state.rebuild()
    return store.public()


async def _checked(coro):
    """Run a validation step, turning expected failures into a 400 for the page."""
    try:
        return await coro
    except WidgetError as exc:
        raise HTTPException(400, str(exc)) from None
    except httpx.HTTPStatusError as exc:
        raise HTTPException(400, f"HTTP {exc.response.status_code} from {exc.request.url.host}") from None
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"Network error: {exc.__class__.__name__}") from None


@router.get("/settings")
async def get_settings(request: Request):
    return request.app.state.settings.public()


@router.delete("/settings/{key}/{uid}")
async def delete_entry(key: str, uid: str, request: Request):
    store = request.app.state.settings
    if key not in ("email_accounts", "youtube_channels", "podcasts", "google_calendars"):
        raise HTTPException(404, "unknown settings list")
    if not store.delete(key, uid):
        raise HTTPException(404, "not found")
    request.app.state.rebuild()
    return store.public()


@router.post("/settings/email_accounts")
async def add_email(request: Request):
    body = await request.json()
    provider = _text(body, "provider", required=False) or "other"
    account: dict[str, Any] = {**EMAIL_PROVIDERS.get(provider, {})}
    account.update({
        "name": _text(body, "name", required=False) or _text(body, "username"),
        "username": _text(body, "username"),
        "password": str(body.get("password") or "").replace(" ", "") if provider == "gmail"
        else str(body.get("password") or ""),
        "provider": provider,
    })
    if not account["password"]:
        raise HTTPException(400, "'password' is required")
    if provider not in EMAIL_PROVIDERS:
        account["host"] = _text(body, "host")
        account["port"] = int(body.get("port") or 993)
    if provider == "gmail" and body.get("gmail_primary") is False:
        account["gmail_primary"] = False
    existing = request.app.state.settings.data["email_accounts"]
    if any(a["username"].lower() == account["username"].lower() for a in existing):
        raise HTTPException(400, f"{account['username']} is already added")
    try:
        await asyncio.wait_for(asyncio.to_thread(test_login, account), timeout=30)
    except imaplib.IMAP4.error as exc:
        msg = exc.args[0].decode(errors="replace") if exc.args and isinstance(exc.args[0], bytes) else str(exc)
        hint = " For Gmail, use an App Password, not your normal password." if provider == "gmail" else ""
        raise HTTPException(400, f"Login failed: {msg}.{hint}") from None
    except (OSError, asyncio.TimeoutError, WidgetError) as exc:
        raise HTTPException(400, f"Could not connect to {account['host']}: {exc or 'timeout'}") from None
    return _save(request, "email_accounts", account)


@router.post("/settings/youtube_channels")
async def add_youtube(request: Request):
    body = await request.json()
    channel_id, name = await _checked(resolve_youtube_channel(request.app.state.http, _text(body, "channel")))
    if any(c["channel_id"] == channel_id for c in request.app.state.settings.data["youtube_channels"]):
        raise HTTPException(400, f"{name} is already added")
    return _save(request, "youtube_channels", {"name": _text(body, "name", required=False) or name,
                                               "channel_id": channel_id})


@router.get("/podcast-search")
async def podcast_search(q: str, request: Request):
    if len(q.strip()) < 2:
        return []
    return await _checked(search_podcasts(request.app.state.http, q.strip()))


@router.post("/settings/podcasts")
async def add_podcast(request: Request):
    body = await request.json()
    url, name = await _checked(resolve_podcast(request.app.state.http, _text(body, "url")))
    if any(p["url"] == url for p in request.app.state.settings.data["podcasts"]):
        raise HTTPException(400, f"{name} is already added")
    return _save(request, "podcasts", {"name": _text(body, "name", required=False) or name, "url": url})


@router.post("/settings/google_calendars")
async def add_calendar(request: Request):
    body = await request.json()

    async def check() -> str:
        url = normalize_ics_url(_text(body, "ics_url"))
        resp = await request.app.state.http.get(url)
        if resp.status_code in (401, 403, 404):
            raise WidgetError(f"Google rejected that link (HTTP {resp.status_code}). "
                              "Use the 'Secret address in iCal format'.")
        resp.raise_for_status()
        cal = await asyncio.to_thread(parse_calendar, resp.content)
        return str(cal.get("X-WR-CALNAME") or "")

    cal_name = await _checked(check())
    return _save(request, "google_calendars", {
        "name": _text(body, "name", required=False) or cal_name or "Google Calendar",
        "ics_url": normalize_ics_url(_text(body, "ics_url")),
    })


@router.put("/settings/calendly")
async def set_calendly(request: Request):
    body = await request.json()
    token = _text(body, "token", required=False)
    if token:
        resp = await _checked(request.app.state.http.get(
            "https://api.calendly.com/users/me", headers={"Authorization": f"Bearer {token}"}))
        if resp.status_code == 401:
            raise HTTPException(400, "Calendly rejected that token")
        if resp.status_code != 200:
            raise HTTPException(400, f"Calendly returned HTTP {resp.status_code}")
    request.app.state.settings.set_section("calendly", {"token": token})
    request.app.state.rebuild()
    return request.app.state.settings.public()


@router.put("/settings/ai")
async def set_ai(request: Request):
    body = await request.json()
    provider = _text(body, "provider", required=False) or "gemini"
    if provider not in ("gemini", "anthropic"):
        raise HTTPException(400, "provider must be gemini or anthropic")
    values = {"provider": provider, "api_key": _text(body, "api_key", required=False)}
    for field in ("language", "model"):
        if field in body:
            values[field] = _text(body, field, required=False)
    request.app.state.settings.set_section("ai", values)
    request.app.state.rebuild()
    return request.app.state.settings.public()
