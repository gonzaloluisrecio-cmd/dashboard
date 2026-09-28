"""Today's Calendly meetings (Calendly API v2, personal access token)."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Any

from .base import Widget, WidgetError, item, register, stat

API = "https://api.calendly.com"


def parse_events(events: list[dict[str, Any]], invitees: list[list[dict[str, Any]]], tz, now: datetime) -> list[dict]:
    out = []
    for ev, people in zip(events, invitees):
        start = datetime.fromisoformat(ev["start_time"].replace("Z", "+00:00")).astimezone(tz)
        end = datetime.fromisoformat(ev["end_time"].replace("Z", "+00:00")).astimezone(tz)
        location = ev.get("location") or {}
        url = location.get("join_url") or None
        where = location.get("location") or location.get("type", "").replace("_", " ")
        names = ", ".join(p.get("name") or p.get("email", "") for p in people) or None
        if end < now:
            badge = "done"
        elif start <= now:
            badge = "NOW"
        else:
            badge = None
        out.append(
            item(
                f"{start:%H:%M}–{end:%H:%M} · {ev.get('name', 'Meeting')}",
                url=url,
                subtitle=names,
                meta=where or None,
                badge=badge,
            )
        )
    return out


@register("calendly")
class CalendlyWidget(Widget):
    """Options:
      token: Calendly personal access token (Integrations → API & Webhooks)
      days: how many days to show starting today (default 1)
    """

    default_refresh_minutes = 15

    def validate(self) -> None:
        self.option("token", required=True)

    async def _get(self, url: str, params: dict | None = None) -> dict:
        resp = await self.ctx.http.get(
            url if url.startswith("http") else API + url,
            params=params,
            headers={"Authorization": f"Bearer {self.option('token')}"},
        )
        if resp.status_code == 401:
            raise WidgetError("Calendly rejected the token (401)")
        resp.raise_for_status()
        return resp.json()

    async def fetch(self) -> dict[str, Any]:
        me = (await self._get("/users/me"))["resource"]
        tz = self.ctx.tz
        now = self.ctx.now()
        start = datetime.combine(now.date(), time.min, tzinfo=tz)
        days = int(self.option("days", 1))
        end = start + timedelta(days=days)
        data = await self._get(
            "/scheduled_events",
            {
                "user": me["uri"],
                "status": "active",
                "min_start_time": start.isoformat(),
                "max_start_time": end.isoformat(),
                "sort": "start_time:asc",
                "count": 100,
            },
        )
        events = data.get("collection", [])
        invitee_pages = await asyncio.gather(
            *(self._get(f"{ev['uri']}/invitees", {"status": "active"}) for ev in events)
        )
        invitees = [p.get("collection", []) for p in invitee_pages]
        items = parse_events(events, invitees, tz, now)
        upcoming = [ev for ev in events
                    if datetime.fromisoformat(ev["end_time"].replace("Z", "+00:00")) > now]
        nxt = None
        if upcoming:
            first = datetime.fromisoformat(upcoming[0]["start_time"].replace("Z", "+00:00")).astimezone(tz)
            nxt = f"Next: {first:%H:%M} {upcoming[0].get('name', '')}"
        people = list(dict.fromkeys(
            p.get("name") or p.get("email", "") for page in invitees for p in page if p.get("name") or p.get("email")
        ))
        return {
            "sections": [
                {"stats": [stat("Meetings today" if days == 1 else f"Meetings ({days} days)", len(events)),
                           stat("Still to go", len(upcoming), sub=nxt)],
                 "text": ("Meeting with: " + ", ".join(people)) if people else None},
                {"items": items, "empty": "Nothing scheduled — enjoy the free day."},
            ]
        }
