"""Widget base class, registry and the output format shared by every widget.

A widget returns a dict of *sections*; the frontend knows how to draw each
section kind, so new widgets never need frontend changes:

    {"sections": [
        {"heading": "Optional heading",
         "stats": [{"label": "Blue", "value": "$1,230", "sub": "Compra $1,210", "trend": "up"}],
         "table": {"columns": ["Casa", "Compra", "Venta"], "rows": [["Blue", "1,210", "1,230"]]},
         "items": [{"title": "...", "url": "...", "subtitle": "...", "meta": "...",
                    "badge": "NEW", "image": "https://...", "summary": "one-line summary"}],
         "text": "Free text (e.g. an AI summary). Lines starting with '- ' become bullets.",
         "empty": "Shown when the section has nothing else"}
    ]}
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import httpx

from ..config import WidgetConfig

_REGISTRY: dict[str, type["Widget"]] = {}


def register(type_name: str):
    """Class decorator: makes a widget available as `type: <type_name>` in config.yaml."""

    def deco(cls: type[Widget]) -> type[Widget]:
        if type_name in _REGISTRY:
            raise ValueError(f"widget type '{type_name}' registered twice")
        cls.type_name = type_name
        _REGISTRY[type_name] = cls
        return cls

    return deco


def get_widget_class(type_name: str) -> type["Widget"]:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown widget type '{type_name}' (known: {known})") from None


def registered_types() -> list[str]:
    return sorted(_REGISTRY)


class WidgetError(Exception):
    """Raise for expected, user-facing problems (missing token, bad response...)."""


@dataclass
class Context:
    """Shared services handed to every widget."""

    http: httpx.AsyncClient
    tz: ZoneInfo
    summarizer: Any  # app.summarizer.Summarizer

    def now(self) -> datetime:
        return datetime.now(self.tz)


class Widget:
    type_name: ClassVar[str] = ""
    default_refresh_minutes: ClassVar[float] = 15

    def __init__(self, config: WidgetConfig, ctx: Context):
        self.config = config
        self.ctx = ctx
        self.options = config.options
        self.validate()

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def refresh_minutes(self) -> float:
        return self.config.refresh_minutes or self.default_refresh_minutes

    def option(self, name: str, default: Any = None, required: bool = False) -> Any:
        value = self.options.get(name, default)
        if required and value in (None, "", []):
            raise WidgetError(f"Not set up yet: '{name}' is missing. Add it in Settings (⚙).")
        return value

    def validate(self) -> None:
        """Override to check options at startup (raise WidgetError)."""

    async def fetch(self) -> dict[str, Any]:
        raise NotImplementedError


# ---- small helpers for building output -------------------------------------

def stat(label: str, value: Any, sub: str | None = None, trend: str | None = None) -> dict:
    return {"label": label, "value": value, "sub": sub, "trend": trend}


def item(title: str, url: str | None = None, subtitle: str | None = None,
         meta: str | None = None, badge: str | None = None, image: str | None = None,
         summary: str | None = None) -> dict:
    return {"title": title, "url": url, "subtitle": subtitle, "meta": meta,
            "badge": badge, "image": image, "summary": summary}


def trend_of(value: float | None) -> str | None:
    if value is None:
        return None
    return "up" if value > 0 else "down" if value < 0 else "flat"


def fmt_money(value: float | None, currency: str = "$", decimals: int = 2) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}{currency}{abs(value):,.{decimals}f}"


def fmt_signed_money(value: float | None, currency: str = "$") -> str:
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + fmt_money(value, currency)


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def relative_time(dt: datetime, now: datetime) -> str:
    seconds = (now - dt).total_seconds()
    if seconds < 0:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"
