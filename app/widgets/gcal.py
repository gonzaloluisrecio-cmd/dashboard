"""Today's events from Google Calendar (or any calendar with an iCal/ICS link).

No Google login or API key is needed: in Google Calendar open Settings → your
calendar → "Secret address in iCal format" and paste that link. Anyone with the
link can read the calendar, so keep it private.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Any

import icalendar
import recurring_ical_events

from .base import Widget, WidgetError, item, register, stat


def normalize_ics_url(url: str) -> str:
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith(("https://", "http://")):
        raise WidgetError("The calendar link must start with https:// (use the iCal / .ics address)")
    return url


def parse_calendar(content: bytes | str) -> icalendar.Calendar:
    try:
        return icalendar.Calendar.from_ical(content)
    except ValueError as exc:
        raise WidgetError(f"that link did not return a calendar ({exc})") from None


def events_between(cal: icalendar.Calendar, start: datetime, end: datetime, tz) -> list[dict[str, Any]]:
    """Expand recurring events and return plain dicts sorted by start time."""
    out = []
    for ev in recurring_ical_events.of(cal, skip_bad_series=True).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = ev.get("DTSTART").dt
        dtend = ev.get("DTEND").dt if ev.get("DTEND") else None
        all_day = not isinstance(dtstart, datetime)
        if all_day:
            s = datetime.combine(dtstart, time.min, tzinfo=tz)
            e = datetime.combine(dtend if isinstance(dtend, date) else dtstart + timedelta(days=1),
                                 time.min, tzinfo=tz)
        else:
            s = (dtstart if dtstart.tzinfo else dtstart.replace(tzinfo=tz)).astimezone(tz)
            e = dtend if isinstance(dtend, datetime) else s + timedelta(hours=1)
            e = (e if e.tzinfo else e.replace(tzinfo=tz)).astimezone(tz)
        out.append({
            "title": str(ev.get("SUMMARY") or "(no title)"),
            "start": s,
            "end": e,
            "all_day": all_day,
            "location": str(ev.get("LOCATION") or "") or None,
        })
    out.sort(key=lambda x: (not x["all_day"], x["start"]))
    return out


@register("gcal")
class GoogleCalendarWidget(Widget):
    """Options:
      calendars: list of {name, ics_url}
      days: how many days to show starting today (default 1)
    """

    default_refresh_minutes = 15

    def validate(self) -> None:
        for c in self.option("calendars", required=True):
            if not c.get("ics_url"):
                raise WidgetError(f"calendar '{c.get('name', '?')}' has no ics_url")

    async def _load(self, cal: dict[str, Any]) -> icalendar.Calendar:
        resp = await self.ctx.http.get(normalize_ics_url(cal["ics_url"]))
        if resp.status_code in (401, 403, 404):
            raise WidgetError(f"{cal.get('name', 'calendar')}: link rejected (HTTP {resp.status_code}). "
                              "Copy the secret iCal address again.")
        resp.raise_for_status()
        return await asyncio.to_thread(parse_calendar, resp.content)

    async def fetch(self) -> dict[str, Any]:
        calendars = self.option("calendars")
        tz = self.ctx.tz
        now = self.ctx.now()
        days = int(self.option("days", 1))
        start = datetime.combine(now.date(), time.min, tzinfo=tz)
        end = start + timedelta(days=days)

        loaded = await asyncio.gather(*(self._load(c) for c in calendars), return_exceptions=True)
        events, errors = [], []
        for cal, result in zip(calendars, loaded):
            name = cal.get("name") or "Calendar"
            if isinstance(result, BaseException):
                errors.append(f"{name}: {result}")
                continue
            for ev in events_between(result, start, end, tz):
                events.append({**ev, "calendar": name})
        if errors and not events and len(errors) == len(calendars):
            raise WidgetError("; ".join(errors))
        events.sort(key=lambda x: (x["start"].date(), not x["all_day"], x["start"]))

        show_cal = len(calendars) > 1
        items = []
        for ev in events:
            when = "All day" if ev["all_day"] else f"{ev['start']:%H:%M}–{ev['end']:%H:%M}"
            if days > 1:
                when = f"{ev['start']:%a %d} · {when}"
            badge = None
            if not ev["all_day"]:
                badge = "done" if ev["end"] < now else "NOW" if ev["start"] <= now else None
            items.append(item(
                f"{when} · {ev['title']}",
                subtitle=ev["calendar"] if show_cal else None,
                meta=ev["location"],
                badge=badge,
            ))
        timed = [e for e in events if not e["all_day"]]
        upcoming = [e for e in timed if e["end"] > now]
        nxt = f"Next: {upcoming[0]['start']:%H:%M} {upcoming[0]['title']}" if upcoming else None
        sections = [
            {"stats": [stat("Events today" if days == 1 else f"Events ({days} days)", len(events)),
                       stat("Still to go", len(upcoming), sub=nxt)]},
            {"items": items, "empty": "Nothing on your calendar today."},
        ]
        if errors:
            sections.append({"text": "Some calendars failed:\n" + "\n".join(f"- {e}" for e in errors)})
        return {"sections": sections}
