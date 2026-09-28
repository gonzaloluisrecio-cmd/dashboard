from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
import pytest
import respx

from app.widgets import WidgetError, registered_types
from app.widgets.calendly import CalendlyWidget
from app.widgets.dolar import DolarWidget
from app.widgets.email_imap import parse_message
from app.widgets.feeds import (NewsWidget, YouTubeWidget, FeedWidget, discover_feed_url,
                               parse_feed)
from app.widgets.ibkr import (IBKRWidget, parse_client_portal, parse_flex_statement,
                              render_snapshot)

from .conftest import make


def test_all_types_registered():
    assert {"dolar", "youtube", "blog", "feed", "news", "email", "ibkr", "calendly"} <= set(registered_types())


# ---- dolar ------------------------------------------------------------------

DOLARES = [
    {"moneda": "USD", "casa": "oficial", "nombre": "Oficial", "compra": 1000, "venta": 1050,
     "fechaActualizacion": "2026-09-28T14:00:00.000Z"},
    {"moneda": "USD", "casa": "blue", "nombre": "Blue", "compra": 1200, "venta": 1220,
     "fechaActualizacion": "2026-09-28T14:05:00.000Z"},
]


@respx.mock
async def test_dolar(ctx):
    respx.get("https://dolarapi.com/v1/dolares").mock(return_value=httpx.Response(200, json=DOLARES))
    data = await make(DolarWidget, ctx, casas=["blue", "oficial"]).fetch()
    stats, table = data["sections"][0]["stats"], data["sections"][1]["table"]
    assert stats[0]["value"] == "$1,200.00" and stats[1]["value"] == "$1,220.00"
    assert [r[0] for r in table["rows"]] == ["Blue", "Oficial"]
    assert table["rows"][1][3] == "5.0%"


# ---- feeds ------------------------------------------------------------------

def _rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>https://ex.com/{i}</link><pubDate>{d}</pubDate></item>"
        for i, (t, d) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Ex</title>{body}</channel></rss>'


def _rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


YT_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>Chan</title>
 <entry>
  <yt:videoId>abc</yt:videoId><title>Fresh video</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=abc"/>
  <published>{published}</published>
  <media:group><media:thumbnail url="https://i.ytimg.com/vi/abc/hqdefault.jpg" width="480" height="360"/></media:group>
 </entry>
</feed>"""


def test_parse_youtube_feed():
    entries = parse_feed(YT_ATOM.format(published="2026-09-28T10:00:00+00:00"), "Chan")
    assert entries[0].title == "Fresh video"
    assert entries[0].image.endswith("hqdefault.jpg")
    assert entries[0].published == datetime(2026, 9, 28, 10, tzinfo=timezone.utc)


def test_discover_feed_url():
    page = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
    assert discover_feed_url(page, "https://blog.example.com/post") == "https://blog.example.com/feed.xml"


@respx.mock
async def test_blog_autodiscovery_and_new_badge(ctx):
    now = datetime.now(timezone.utc)
    respx.get("https://blog.example.com/").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/html"},
        text='<link rel="alternate" type="application/atom+xml" href="https://blog.example.com/rss">'))
    respx.get("https://blog.example.com/rss").mock(return_value=httpx.Response(
        200, headers={"content-type": "application/rss+xml"},
        text=_rss([("New post", _rfc822(now - timedelta(hours=2))),
                   ("Old post", _rfc822(now - timedelta(days=10)))])))
    data = await make(FeedWidget, ctx, feeds=[{"name": "Blog", "url": "https://blog.example.com/"}]).fetch()
    assert data["sections"][0]["stats"][0]["value"] == 1
    items = data["sections"][1]["items"]
    assert [i["badge"] for i in items] == ["NEW", None]


@respx.mock
async def test_youtube_handle_and_partial_failure(ctx):
    YouTubeWidget._handle_cache.clear()
    now = datetime.now(timezone.utc)
    cid = "UC" + "a" * 22
    respx.get("https://www.youtube.com/@good").mock(
        return_value=httpx.Response(200, text=f'..."channelId":"{cid}"...'))
    respx.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}").mock(
        return_value=httpx.Response(200, text=YT_ATOM.format(published=now.isoformat())))
    respx.get("https://www.youtube.com/feeds/videos.xml?channel_id=UCbroken").mock(
        return_value=httpx.Response(404))
    w = make(YouTubeWidget, ctx, channels=[{"name": "Good", "handle": "@good"},
                                           {"name": "Broken", "channel_id": "UCbroken"}])
    data = await w.fetch()
    assert data["sections"][0]["stats"][0]["value"] == 1
    assert data["sections"][0]["text"] == "From: Good"
    assert "Broken" in data["sections"][-1]["text"]


def test_youtube_requires_id_or_handle(ctx):
    with pytest.raises(WidgetError):
        make(YouTubeWidget, ctx, channels=[{"name": "x"}])


@respx.mock
async def test_news_dedup_and_summary(ctx):
    now = datetime.now(timezone.utc)
    d = _rfc822(now)
    respx.get("https://a.com/rss").mock(return_value=httpx.Response(200, text=_rss([("Big story", d), ("Only A", d)])))
    respx.get("https://b.com/rss").mock(return_value=httpx.Response(200, text=_rss([("Big Story", d)])))
    w = make(NewsWidget, ctx, sections=[{"heading": "Argentina",
                                         "feeds": [{"name": "A", "url": "https://a.com/rss"},
                                                   {"name": "B", "url": "https://b.com/rss"}]}])
    data = await w.fetch()
    sec = data["sections"][0]
    assert sec["heading"] == "Argentina"
    assert len(sec["items"]) == 2
    assert sec["text"] == "- summary bullet"
    assert "Big story" in ctx.summarizer.calls[0][1]


# ---- email ------------------------------------------------------------------

def test_parse_message():
    msg = EmailMessage()
    msg["From"] = "=?utf-8?q?Mar=C3=ADa_P=C3=A9rez?= <maria@example.com>"
    msg["Subject"] = "=?utf-8?q?Reuni=C3=B3n_ma=C3=B1ana?="
    msg["Date"] = "Mon, 28 Sep 2026 10:00:00 -0300"
    msg.set_content("Hola,   ¿podemos\n\nvernos a las 10?")
    msg.add_alternative("<p>Hola</p>", subtype="html")
    parsed = parse_message(msg.as_bytes())
    assert parsed["from"] == "María Pérez"
    assert parsed["subject"] == "Reunión mañana"
    assert parsed["snippet"] == "Hola, ¿podemos vernos a las 10?"
    assert parsed["date"].utcoffset() == timedelta(hours=-3)


# ---- ibkr -------------------------------------------------------------------

FLEX_STATEMENT = """<FlexQueryResponse queryName="dash" type="AF"><FlexStatements count="1">
<FlexStatement accountId="U1" fromDate="20260925" toDate="20260925">
  <ChangeInNAV accountId="U1" currency="USD" startingValue="10000" endingValue="10150" twr="1.5"/>
  <OpenPositions>
    <OpenPosition symbol="AAPL" position="10" positionValue="2000" costBasisMoney="1500"
                  fifoPnlUnrealized="500" fxRateToBase="1" levelOfDetail="SUMMARY"/>
    <OpenPosition symbol="AAPL" position="10" positionValue="2000" costBasisMoney="1500"
                  fifoPnlUnrealized="500" fxRateToBase="1" levelOfDetail="LOT"/>
    <OpenPosition symbol="GGAL" position="100" positionValue="1000" costBasisMoney="1200"
                  fifoPnlUnrealized="-200" fxRateToBase="1" levelOfDetail="SUMMARY"/>
  </OpenPositions>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def test_parse_flex_statement():
    snap = parse_flex_statement(FLEX_STATEMENT)
    assert snap.nav == 10150 and snap.day_pnl == 150 and snap.day_pct == 1.5
    assert len(snap.positions) == 2
    assert snap.unrealized == 300 and snap.cost_basis == 2700
    out = render_snapshot(snap, net_deposits=8000, top=5, mode="flex")
    stats = {s["label"]: s for s in out["sections"][0]["stats"]}
    assert stats["Last session P&L"]["value"] == "+$150.00"
    assert stats["All-time vs deposits"]["value"] == "+$2,150.00"
    assert stats["Unrealized P&L (holdings)"]["sub"] == "+11.11%"
    assert out["sections"][1]["table"]["rows"][0][0] == "AAPL"


@respx.mock
async def test_flex_flow_waits_for_statement(ctx, monkeypatch):
    import app.widgets.ibkr as ibkr
    real_sleep = ibkr.asyncio.sleep
    monkeypatch.setattr(ibkr.asyncio, "sleep", lambda s: real_sleep(0))
    respx.get(ibkr.FLEX_SEND_URL).mock(return_value=httpx.Response(200, text=(
        "<FlexStatementResponse><Status>Success</Status><ReferenceCode>42</ReferenceCode>"
        "<Url>https://ibkr.test/GetStatement</Url></FlexStatementResponse>")))
    respx.get("https://ibkr.test/GetStatement").mock(side_effect=[
        httpx.Response(200, text="<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>"
                                 "<ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>"),
        httpx.Response(200, text=FLEX_STATEMENT),
    ])
    data = await make(IBKRWidget, ctx, flex_token="t", flex_query_id="q").fetch()
    assert data["sections"][0]["stats"][0]["value"] == "$10,150.00"


def test_flex_error_is_reported():
    with pytest.raises(WidgetError, match="1020"):
        parse_flex_statement("<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode>"
                             "<ErrorMessage>Invalid request</ErrorMessage></FlexStatementResponse>")


def test_ibkr_requires_flex_credentials(ctx):
    with pytest.raises(WidgetError):
        make(IBKRWidget, ctx)


def test_parse_client_portal():
    snap = parse_client_portal(
        {"upnl": {"U1.Core": {"dpl": -100.0, "nl": 9900.0, "upl": 50.0}}},
        [{"contractDesc": "SPY", "position": 5, "mktValue": 2500, "unrealizedPnl": 50, "currency": "USD"}],
        "U1",
    )
    assert snap.nav == 9900 and snap.day_pnl == -100
    assert snap.day_pct == pytest.approx(-1.0)
    assert snap.positions[0].cost_basis == 2450


# ---- calendly ---------------------------------------------------------------

@respx.mock
async def test_calendly(ctx):
    now = datetime.now(timezone.utc)
    past = {"uri": "https://api.calendly.com/scheduled_events/1", "name": "Standup",
            "start_time": (now - timedelta(hours=2)).isoformat(), "end_time": (now - timedelta(hours=1)).isoformat(),
            "location": {"type": "zoom", "join_url": "https://zoom.us/j/1"}}
    future = {"uri": "https://api.calendly.com/scheduled_events/2", "name": "Client call",
              "start_time": (now + timedelta(minutes=30)).isoformat(),
              "end_time": (now + timedelta(minutes=60)).isoformat(), "location": {}}
    respx.get("https://api.calendly.com/users/me").mock(
        return_value=httpx.Response(200, json={"resource": {"uri": "https://api.calendly.com/users/me1"}}))
    events = respx.get("https://api.calendly.com/scheduled_events").mock(
        return_value=httpx.Response(200, json={"collection": [past, future]}))
    respx.get(past["uri"] + "/invitees").mock(
        return_value=httpx.Response(200, json={"collection": [{"name": "Ana", "email": "a@x.com"}]}))
    respx.get(future["uri"] + "/invitees").mock(return_value=httpx.Response(200, json={"collection": []}))

    data = await make(CalendlyWidget, ctx, token="tok").fetch()
    assert events.calls[0].request.headers["Authorization"] == "Bearer tok"
    stats = data["sections"][0]["stats"]
    assert stats[0]["value"] == 2 and stats[1]["value"] == 1
    items = data["sections"][1]["items"]
    assert items[0]["badge"] == "done" and items[0]["subtitle"] == "Ana"
    assert items[0]["url"] == "https://zoom.us/j/1"


@respx.mock
async def test_calendly_bad_token(ctx):
    respx.get("https://api.calendly.com/users/me").mock(return_value=httpx.Response(401))
    with pytest.raises(WidgetError, match="401"):
        await make(CalendlyWidget, ctx, token="bad").fetch()
