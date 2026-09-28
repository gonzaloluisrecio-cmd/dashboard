"""RSS/Atom based widgets: blogs, YouTube channels, podcasts and news.

All of them share the same feed fetching + parsing code, so any site with a
feed can be tracked. "New" means published within `new_within_hours`.
"""
from __future__ import annotations

import asyncio
import calendar
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from .base import Widget, WidgetError, item, register, relative_time, stat

log = logging.getLogger(__name__)

_ALT_LINK = re.compile(
    r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", re.IGNORECASE
)
_HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_YT_CHANNEL_ID = re.compile(r"(UC[0-9A-Za-z_-]{22})")


@dataclass
class Entry:
    title: str
    link: str | None
    published: datetime | None
    source: str
    image: str | None = None
    summary: str = ""


def _struct_to_dt(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)


def _clean(text: str, limit: int = 300) -> str:
    text = html.unescape(_TAGS.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_feed(content: bytes | str, source: str) -> list[Entry]:
    parsed = feedparser.parse(content)
    feed_image = (parsed.feed.get("image") or {}).get("href")  # e.g. podcast artwork
    entries = []
    for e in parsed.entries:
        image = None
        if e.get("media_thumbnail"):
            image = e.media_thumbnail[0].get("url")
        elif e.get("media_content"):
            image = next((m.get("url") for m in e.media_content if m.get("medium") == "image"), None)
        elif isinstance(e.get("image"), dict):
            image = e.image.get("href")
        image = image or feed_image
        entries.append(
            Entry(
                title=_clean(e.get("title", "(untitled)"), 200),
                link=e.get("link"),
                published=_struct_to_dt(e.get("published_parsed") or e.get("updated_parsed")),
                source=source or parsed.feed.get("title", ""),
                image=image,
                summary=_clean(e.get("summary", "")),
            )
        )
    return entries


def discover_feed_url(page_html: str, base_url: str) -> str | None:
    """Find <link rel="alternate" type="application/rss+xml"> in an HTML page."""
    for tag in _ALT_LINK.findall(page_html):
        href = _HREF.search(tag)
        if href:
            return urljoin(base_url, html.unescape(href.group(1)))
    return None


def _looks_like_html(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "")
    return "html" in ctype and "xml" not in ctype


async def fetch_entries(http: httpx.AsyncClient, url: str, source: str) -> list[Entry]:
    resp = await http.get(url)
    resp.raise_for_status()
    if _looks_like_html(resp):
        # A normal web page was configured: try feed autodiscovery.
        feed_url = discover_feed_url(resp.text, str(resp.url))
        if not feed_url:
            raise WidgetError(f"{source or url}: no RSS/Atom feed found on that page")
        resp = await http.get(feed_url)
        resp.raise_for_status()
    return parse_feed(resp.content, source)


async def fetch_many(http: httpx.AsyncClient, feeds: list[tuple[str, str]]) -> tuple[list[Entry], list[str]]:
    """Fetch (name, url) feeds concurrently. Returns entries plus per-feed error messages."""
    results = await asyncio.gather(
        *(fetch_entries(http, url, name) for name, url in feeds), return_exceptions=True
    )
    entries: list[Entry] = []
    errors: list[str] = []
    for (name, url), result in zip(feeds, results):
        if isinstance(result, BaseException):
            log.warning("feed %s failed: %s", url, result)
            errors.append(f"{name or url}: {result}")
        else:
            entries.extend(result)
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    entries.sort(key=lambda e: e.published or _epoch, reverse=True)
    return entries, errors


def _feed_list(raw: list[Any]) -> list[tuple[str, str]]:
    out = []
    for f in raw or []:
        if isinstance(f, str):
            out.append(("", f))
        else:
            out.append((f.get("name", ""), f["url"]))
    return out


class _FeedWidgetBase(Widget):
    default_refresh_minutes = 30
    default_new_hours = 24

    @property
    def new_window_hours(self) -> float:
        return float(self.option("new_within_hours", self.default_new_hours))

    def is_new(self, e: Entry, now: datetime) -> bool:
        return bool(e.published and now - e.published <= timedelta(hours=self.new_window_hours))

    def entry_items(self, entries: list[Entry], limit: int, show_source: bool = True,
                    show_images: bool = False) -> tuple[list[dict], int]:
        """Render the first `limit` entries; also return how many of *all* entries are new."""
        now = self.ctx.now()
        items = [
            item(
                e.title,
                url=e.link,
                subtitle=e.source if show_source else None,
                meta=relative_time(e.published, now) if e.published else None,
                badge="NEW" if self.is_new(e, now) else None,
                image=e.image if show_images else None,
            )
            for e in entries[:limit]
        ]
        return items, sum(self.is_new(e, now) for e in entries)


@register("feed")
class FeedWidget(_FeedWidgetBase):
    """Generic RSS/Atom tracker (blogs, podcasts, changelogs...).

    Options:
      feeds: list of {name, url} (url may be the feed or the site's homepage)
      limit: max items shown (default 10)
      new_within_hours: badge window (default 24)
    """

    def validate(self) -> None:
        self.option("feeds", required=True)

    async def fetch(self) -> dict[str, Any]:
        feeds = _feed_list(self.option("feeds"))
        entries, errors = await fetch_many(self.ctx.http, feeds)
        if not entries and errors:
            raise WidgetError("; ".join(errors))
        items, new_count = self.entry_items(entries, int(self.option("limit", 10)), show_source=len(feeds) > 1)
        hours = f"{self.new_window_hours:g}"
        sections = [
            {"stats": [stat(f"New in last {hours}h", new_count, trend="up" if new_count else None)]},
            {"items": items, "empty": "No posts yet."},
        ]
        if errors:
            sections.append({"text": "Some feeds failed:\n" + "\n".join(f"- {e}" for e in errors)})
        return {"sections": sections}


# "blog" is just a friendlier name for the generic feed widget.
register("blog")(type("BlogWidget", (FeedWidget,), {}))


@register("youtube")
class YouTubeWidget(_FeedWidgetBase):
    """New uploads from YouTube channels, via YouTube's public RSS (no API key).

    Options:
      channels: list of {name, channel_id} or {name, handle: "@somechannel"}
      limit: max videos shown (default 12)
      new_within_hours: badge window (default 24)
      thumbnails: show thumbnails (default true)
    """

    default_refresh_minutes = 30
    _handle_cache: dict[str, str] = {}

    def validate(self) -> None:
        for ch in self.option("channels", required=True):
            if not (ch.get("channel_id") or ch.get("handle")):
                raise WidgetError(f"youtube channel {ch} needs 'channel_id' or 'handle'")

    async def _channel_id(self, ch: dict) -> str:
        if ch.get("channel_id"):
            return ch["channel_id"]
        handle = ch["handle"].lstrip("@")
        if handle not in self._handle_cache:
            resp = await self.ctx.http.get(f"https://www.youtube.com/@{handle}")
            resp.raise_for_status()
            m = re.search(r'"(?:channelId|externalId)":"(UC[0-9A-Za-z_-]{22})"', resp.text) \
                or _YT_CHANNEL_ID.search(resp.text)
            if not m:
                raise WidgetError(f"could not resolve YouTube handle @{handle}; use channel_id instead")
            self._handle_cache[handle] = m.group(1)
        return self._handle_cache[handle]

    async def fetch(self) -> dict[str, Any]:
        channels = self.option("channels")
        ids = await asyncio.gather(*(self._channel_id(ch) for ch in channels), return_exceptions=True)
        feeds, errors = [], []
        for ch, cid in zip(channels, ids):
            name = ch.get("name") or ch.get("handle") or ""
            if isinstance(cid, BaseException):
                errors.append(f"{name}: {cid}")
            else:
                feeds.append((name, f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))
        entries, fetch_errors = await fetch_many(self.ctx.http, feeds)
        errors += fetch_errors
        if not entries and errors:
            raise WidgetError("; ".join(errors))

        items, new_count = self.entry_items(
            entries, int(self.option("limit", 12)), show_images=self.option("thumbnails", True)
        )
        hours = f"{self.new_window_hours:g}"
        now = self.ctx.now()
        channels_with_new = sorted({e.source for e in entries if self.is_new(e, now)})
        sections = [
            {
                "stats": [stat(f"New videos ({hours}h)", new_count, trend="up" if new_count else None)],
                "text": ("From: " + ", ".join(channels_with_new)) if channels_with_new else "No new uploads.",
            },
            {"items": items, "empty": "No videos found."},
        ]
        if errors:
            sections.append({"text": "Some channels failed:\n" + "\n".join(f"- {e}" for e in errors)})
        return {"sections": sections}


async def resolve_youtube_channel(http: httpx.AsyncClient, text: str) -> tuple[str, str]:
    """Turn a channel URL, @handle or channel id into (channel_id, channel name)."""
    text = text.strip()
    m = re.fullmatch(r"(?:https?://(?:www\.|m\.)?youtube\.com/channel/)?(UC[0-9A-Za-z_-]{22})/?", text)
    if m:
        channel_id = m.group(1)
    else:
        if text.startswith("@"):
            page = f"https://www.youtube.com/{text}"
        elif re.match(r"^(https?://)?(www\.|m\.)?youtube\.com/", text):
            page = text if text.startswith("http") else "https://" + text
        elif re.fullmatch(r"[\w.\-]+", text):
            page = f"https://www.youtube.com/@{text}"
        else:
            raise WidgetError("Paste a YouTube channel link, an @handle or a channel id (UC...)")
        resp = await http.get(page, headers={"Accept-Language": "en", "Cookie": "CONSENT=YES+1"})
        if resp.status_code == 404:
            raise WidgetError(f"YouTube says that channel does not exist: {text}")
        resp.raise_for_status()
        found = re.search(r'"(?:channelId|externalId)":"(UC[0-9A-Za-z_-]{22})"', resp.text) \
            or re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"',
                         resp.text)
        if not found:
            raise WidgetError(f"Could not find the channel id on {page}")
        channel_id = found.group(1)
    resp = await http.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    resp.raise_for_status()
    name = feedparser.parse(resp.content).feed.get("title") or channel_id
    return channel_id, name


async def resolve_podcast(http: httpx.AsyncClient, text: str) -> tuple[str, str]:
    """Turn an RSS URL or an Apple Podcasts link into (feed url, podcast name)."""
    text = text.strip()
    if not re.match(r"^https?://", text):
        raise WidgetError("Paste the podcast's RSS feed or Apple Podcasts link, or search by name")
    apple = re.search(r"podcasts\.apple\.com/.*/id(\d+)", text)
    if apple:
        resp = await http.get("https://itunes.apple.com/lookup", params={"id": apple.group(1), "entity": "podcast"})
        resp.raise_for_status()
        results = [r for r in resp.json().get("results", []) if r.get("feedUrl")]
        if not results:
            raise WidgetError("Apple Podcasts does not publish a feed for that show")
        return results[0]["feedUrl"], results[0].get("collectionName") or ""
    if urlparse(text).hostname in ("open.spotify.com", "spotify.com"):
        raise WidgetError("Spotify links have no public feed. Search the podcast by name instead.")
    resp = await http.get(text)
    resp.raise_for_status()
    feed_url = text
    if _looks_like_html(resp):
        feed_url = discover_feed_url(resp.text, str(resp.url))
        if not feed_url:
            raise WidgetError("No podcast feed found at that address")
        resp = await http.get(feed_url)
        resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if not parsed.entries and not parsed.feed.get("title"):
        raise WidgetError("That address is not a podcast feed")
    return feed_url, parsed.feed.get("title") or feed_url


async def search_podcasts(http: httpx.AsyncClient, term: str, limit: int = 8) -> list[dict[str, str]]:
    """Search Apple's free podcast directory (no key). Returns name/author/feed/image."""
    resp = await http.get("https://itunes.apple.com/search",
                          params={"media": "podcast", "term": term, "limit": limit})
    resp.raise_for_status()
    return [
        {"name": r.get("collectionName", ""), "author": r.get("artistName", ""),
         "url": r["feedUrl"], "image": r.get("artworkUrl100", "")}
        for r in resp.json().get("results", []) if r.get("feedUrl")
    ]


@register("podcast")
class PodcastWidget(_FeedWidgetBase):
    """New episodes from podcasts (their public RSS feeds).

    Options:
      feeds: list of {name, url}
      new_within_hours: badge window (default 72)
      per_podcast: latest episodes listed per show (default 1)
      artwork: show cover art (default true)
    """

    default_refresh_minutes = 60
    default_new_hours = 72

    def validate(self) -> None:
        self.option("feeds", required=True)

    async def fetch(self) -> dict[str, Any]:
        feeds = _feed_list(self.option("feeds"))
        results = await asyncio.gather(
            *(fetch_entries(self.ctx.http, url, name) for name, url in feeds), return_exceptions=True
        )
        now = self.ctx.now()
        per = int(self.option("per_podcast", 1))
        latest: list[Entry] = []
        errors, with_new = [], []
        new_count = 0
        _epoch = datetime.min.replace(tzinfo=timezone.utc)
        for (name, url), result in zip(feeds, results):
            if isinstance(result, BaseException):
                log.warning("podcast %s failed: %s", url, result)
                errors.append(f"{name or url}: {result}")
                continue
            new = [e for e in result if self.is_new(e, now)]
            new_count += len(new)
            if new:
                with_new.append(name or result[0].source)
            latest += sorted(result, key=lambda e: e.published or _epoch, reverse=True)[:per]
        if not latest and errors:
            raise WidgetError("; ".join(errors))
        latest.sort(key=lambda e: e.published or _epoch, reverse=True)
        items, _ = self.entry_items(latest, len(latest), show_images=self.option("artwork", True))
        window = self.new_window_hours
        label = f"{window / 24:g} days" if window % 24 == 0 else f"{window:g}h"
        sections = [
            {"stats": [stat(f"New episodes ({label})", new_count, trend="up" if new_count else None)],
             "text": ("From: " + ", ".join(with_new)) if with_new else "No new episodes."},
            {"heading": "Latest episodes", "items": items, "empty": "No episodes found."},
        ]
        if errors:
            sections.append({"text": "Some podcasts failed:\n" + "\n".join(f"- {e}" for e in errors)})
        return {"sections": sections}


@register("news")
class NewsWidget(_FeedWidgetBase):
    """Headlines grouped into sections (e.g. Argentina / International), with an
    optional AI summary per section.

    Options:
      sections: list of {heading, feeds: [{name, url}], limit}
      summarize: true/false (needs `ai.enabled`), default true
      new_within_hours: default 6
    """

    default_refresh_minutes = 30
    default_new_hours = 6

    def validate(self) -> None:
        for s in self.option("sections", required=True):
            if not s.get("feeds"):
                raise WidgetError(f"news section '{s.get('heading')}' has no feeds")

    async def _section(self, spec: dict) -> dict:
        feeds = _feed_list(spec["feeds"])
        entries, errors = await fetch_many(self.ctx.http, feeds)
        # Drop near-duplicate titles coming from different outlets.
        seen, unique = set(), []
        for e in entries:
            key = e.title.lower()[:60]
            if key not in seen:
                seen.add(key)
                unique.append(e)
        limit = int(spec.get("limit", 8))
        items, _ = self.entry_items(unique, limit)
        section: dict[str, Any] = {"heading": spec.get("heading"), "items": items,
                                   "empty": "No headlines available."}

        summarizer = self.ctx.summarizer
        if self.option("summarize", True) and summarizer and summarizer.available and unique:
            digest = "\n".join(
                f"- [{e.source}] {e.title}" + (f" — {e.summary}" if e.summary else "")
                for e in unique[:30]
            )
            section["text"] = await summarizer.summarize(
                f"Summarize the most important news stories in these headlines ({spec.get('heading', '')}) "
                "in 4-6 bullets. Merge stories that several outlets cover.",
                digest,
            )
        if errors:
            extra = "Some feeds failed: " + "; ".join(errors)
            section["text"] = f"{section['text']}\n\n{extra}" if section.get("text") else extra
        return section

    async def fetch(self) -> dict[str, Any]:
        sections = await asyncio.gather(*(self._section(s) for s in self.option("sections")))
        return {"sections": list(sections)}
