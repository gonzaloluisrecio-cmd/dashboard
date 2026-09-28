"""Unread mail in the main inbox of one or more IMAP accounts, with an optional AI summary.

Messages are read with BODY.PEEK so nothing gets marked as read.
For Gmail, use an App Password (Google Account → Security → App passwords) and
`gmail_primary: true` to only look at the "Primary" tab.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from .base import Widget, WidgetError, item, register, relative_time, stat

_TAGS = re.compile(r"<[^>]+>")


def decode_str(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def body_snippet(msg: Message, limit: int = 600) -> str:
    """Best-effort plain-text snippet of a (possibly truncated) message."""
    parts = msg.walk() if msg.is_multipart() else [msg]
    text, html_text = "", ""
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            decoded = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and not text:
            text = decoded
        elif ctype == "text/html" and not html_text:
            html_text = _TAGS.sub(" ", decoded)
    snippet = re.sub(r"\s+", " ", text or html_text).strip()
    return snippet[:limit]


def parse_message(raw: bytes) -> dict[str, Any]:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(decode_str(msg.get("From")))
    try:
        date = parsedate_to_datetime(msg.get("Date"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        date = None
    return {
        "from": name or addr,
        "from_addr": addr,
        "subject": decode_str(msg.get("Subject")) or "(no subject)",
        "date": date,
        "snippet": body_snippet(msg),
    }


def fetch_unread(account: dict[str, Any], max_messages: int) -> tuple[int, list[dict[str, Any]]]:
    """Blocking IMAP fetch. Returns (total unread, newest `max_messages` messages)."""
    host = account["host"]
    port = int(account.get("port", 993))
    conn = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        conn.login(account["username"], account["password"])
        conn.select(account.get("folder", "INBOX"), readonly=True)
        if account.get("gmail_primary"):
            typ, data = conn.search(None, "X-GM-RAW", '"category:primary is:unread"')
        else:
            typ, data = conn.search(None, account.get("search", "UNSEEN"))
        if typ != "OK":
            raise WidgetError(f"IMAP search failed: {data}")
        ids = data[0].split()
        newest = ids[-max_messages:][::-1]
        messages = []
        for mid in newest:
            # First ~20KB is plenty for headers + a snippet.
            typ, parts = conn.fetch(mid, "(BODY.PEEK[]<0.20000>)")
            if typ != "OK":
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw:
                messages.append(parse_message(raw))
        return len(ids), messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass


@register("email")
class EmailWidget(Widget):
    """Options:
      accounts: list of {name, host, port (993), username, password,
                         folder (INBOX), gmail_primary (false), search (UNSEEN)}
      max_messages: messages listed per account (default 8)
      summarize: AI summary per account (needs `ai.enabled`), default true
    """

    default_refresh_minutes = 10

    def validate(self) -> None:
        for acc in self.option("accounts", required=True):
            missing = [k for k in ("host", "username", "password") if not acc.get(k)]
            if missing:
                raise WidgetError(f"Not set up yet: email account '{acc.get('name', '?')}' is missing "
                                  f"{', '.join(missing)}. Add it in .env and restart.")

    async def _account_section(self, acc: dict[str, Any]) -> dict[str, Any]:
        name = acc.get("name") or acc["username"]
        max_messages = int(self.option("max_messages", 8))
        try:
            total, messages = await asyncio.to_thread(fetch_unread, acc, max_messages)
        except (imaplib.IMAP4.error, OSError, WidgetError) as exc:
            return {"heading": name, "text": f"Could not read mailbox: {exc}"}

        now = self.ctx.now()
        items = [
            item(
                m["subject"],
                subtitle=m["from"],
                meta=relative_time(m["date"], now) if isinstance(m["date"], datetime) else None,
            )
            for m in messages
        ]
        section: dict[str, Any] = {
            "heading": name,
            "stats": [stat("Unread", total, trend="up" if total else None)],
            "items": items,
            "empty": "Inbox zero 🎉",
        }
        summarizer = self.ctx.summarizer
        if self.option("summarize", True) and summarizer and summarizer.available and messages:
            digest = "\n".join(
                f"- From: {m['from']} <{m['from_addr']}> | Subject: {m['subject']} | {m['snippet'][:400]}"
                for m in messages
            )
            section["text"] = await summarizer.summarize(
                "These are my unread emails. In 3-5 bullets, tell me what needs my attention "
                "(requests, deadlines, money, people waiting on me) and group the rest "
                "(newsletters, notifications) in one line.",
                digest,
            )
        return section

    async def fetch(self) -> dict[str, Any]:
        sections = await asyncio.gather(*(self._account_section(a) for a in self.option("accounts")))
        return {"sections": list(sections)}
