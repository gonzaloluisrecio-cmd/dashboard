"""Sources managed from the in-app Settings page (stored in data/settings.json).

config.yaml decides which cards exist and how they look; this file holds the
things you add and delete from the app: email accounts, the Calendly token,
YouTube channels, podcasts, Google calendars and the AI key. When the dashboard
is built they are merged into the widgets of the matching type.
"""
from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

# settings key -> (widget type, widget option it fills, default card to add if config.yaml has none)
LIST_SOURCES: dict[str, tuple[str, str, dict[str, Any]]] = {
    "email_accounts": ("email", "accounts", {"id": "email", "type": "email", "title": "Correo", "size": "large"}),
    "youtube_channels": ("youtube", "channels", {"id": "youtube", "type": "youtube", "title": "YouTube",
                                                 "size": "large"}),
    "podcasts": ("podcast", "feeds", {"id": "podcasts", "type": "podcast", "title": "Podcasts",
                                      "size": "medium"}),
    "google_calendars": ("gcal", "calendars", {"id": "gcal", "type": "gcal", "title": "Google Calendar",
                                               "size": "medium"}),
}
CALENDLY_CARD = {"id": "calendly", "type": "calendly", "title": "Calendly", "size": "medium"}

# Fields never sent back to the browser.
SECRET_FIELDS = {"password", "token", "api_key", "ics_url"}

EMPTY: dict[str, Any] = {
    "email_accounts": [],
    "youtube_channels": [],
    "podcasts": [],
    "google_calendars": [],
    "calendly": {"token": ""},
    "ai": {"provider": "", "api_key": "", "model": "", "language": ""},
}


class SettingsStore:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        data = copy.deepcopy(EMPTY)
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                stored = json.load(fh)
            for key, default in EMPTY.items():
                if isinstance(default, dict):
                    data[key].update(stored.get(key) or {})
                else:
                    data[key] = list(stored.get(key) or [])
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)  # it holds passwords
        except OSError:
            pass

    def add(self, key: str, entry: dict[str, Any]) -> dict[str, Any]:
        entry = {**entry, "uid": secrets.token_hex(4)}
        self.data[key].append(entry)
        self.save()
        return entry

    def delete(self, key: str, uid: str) -> bool:
        before = len(self.data[key])
        self.data[key] = [e for e in self.data[key] if e.get("uid") != uid]
        if len(self.data[key]) == before:
            return False
        self.save()
        return True

    def set_section(self, key: str, values: dict[str, Any]) -> None:
        self.data[key].update({k: v for k, v in values.items() if k in EMPTY[key]})
        self.save()

    def public(self) -> dict[str, Any]:
        """Settings safe to send to the browser: secrets become `has_<field>: true`."""

        def scrub(entry: dict[str, Any]) -> dict[str, Any]:
            out = {}
            for k, v in entry.items():
                if k in SECRET_FIELDS:
                    out[f"has_{k}"] = bool(v)
                else:
                    out[k] = v
            return out

        return {k: [scrub(e) for e in v] if isinstance(v, list) else scrub(v) for k, v in self.data.items()}


def apply_settings(raw_config: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Merge UI-managed sources into a raw (not yet parsed) config.yaml dict.

    Sources are appended to every widget of the matching type. If config.yaml has
    no such widget, a default card is added so things added in the app always show.
    """
    raw = copy.deepcopy(raw_config or {})
    widgets = raw.setdefault("widgets", [])

    def widgets_of(type_name: str, default_card: dict[str, Any]) -> list[dict[str, Any]]:
        found = [w for w in widgets if isinstance(w, dict) and w.get("type") == type_name]
        if not found:
            card = copy.deepcopy(default_card)
            taken = {w.get("id") for w in widgets if isinstance(w, dict)}
            while card["id"] in taken:
                card["id"] += "-app"
            widgets.append(card)
            found = [card]
        return found

    for key, (type_name, option, default_card) in LIST_SOURCES.items():
        entries = [{k: v for k, v in e.items() if k != "uid"} for e in settings.get(key) or []]
        if not entries:
            continue
        for w in widgets_of(type_name, default_card):
            target = w.get("options") if isinstance(w.get("options"), dict) and option in w["options"] else w
            target[option] = list(target.get(option) or []) + entries

    token = (settings.get("calendly") or {}).get("token")
    if token:
        for w in widgets_of("calendly", CALENDLY_CARD):
            w.pop("token", None)
            if isinstance(w.get("options"), dict):
                w["options"].pop("token", None)
            w["token"] = token

    ai = {k: v for k, v in (settings.get("ai") or {}).items() if v}
    if ai.get("api_key"):
        raw["ai"] = {**(raw.get("ai") or {}), **ai, "enabled": True}
    return raw
