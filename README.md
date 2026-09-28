# Personal Dashboard

A self-hosted dashboard that shows, on one page:

| Card | Source | Widget `type` |
|---|---|---|
| USD → ARS (buy/sell: oficial, blue, MEP, CCL, tarjeta, cripto) | [dolarapi.com](https://dolarapi.com) (free) | `dolar` |
| New uploads from YouTube channels you follow | YouTube's public RSS feeds (no API key) | `youtube` |
| New posts on blogs you follow | Any RSS/Atom feed (or the blog's homepage) | `blog` / `feed` |
| Unread mail in your main inbox, plus an optional AI summary | IMAP (Gmail, Outlook, etc.) | `email` |
| Interactive Brokers: net value, daily P&L, all-time performance | IBKR Flex Web Service or Client Portal API | `ibkr` |
| Argentine and international headlines, plus an optional AI summary | News RSS feeds | `news` |
| Today's Calendly meetings | Calendly API v2 | `calendly` |

Everything is set in **one file, `config.yaml`**: which cards to show, in what
order, how big, how often they refresh, and what they track. Adding a card
type means adding one Python file (see [Adding a new widget](#adding-a-new-widget)).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml    # choose widgets, channels, feeds...
cp .env.example .env                  # add tokens and passwords
python -m app.main                    # open http://127.0.0.1:8000
```

`HOST`/`PORT` env vars change the address. To open it from your phone on the
same network, use `HOST=0.0.0.0`. The dashboard has no login, so only do this on
a network you trust.

After editing `config.yaml`, restart the server or run `curl -X POST localhost:8000/api/reload`.

## Configuration

```yaml
title: Mi Dashboard
timezone: America/Argentina/Buenos_Aires

widgets:
  - id: dolar            # unique id
    type: dolar          # which widget
    title: Dólar hoy     # card title
    size: medium         # small | medium | large (2 columns) | full (whole row)
    refresh_minutes: 10  # optional
    enabled: true        # false hides it without deleting it
    casas: [oficial, blue, bolsa]   # widget-specific options
```

- Write secrets as `${VAR}` or `${VAR:-default}` and put the values in `.env`.
- Widget options can go directly on the widget (as above) or under `options:`.
- Cards are cached on the server. If a source fails, the card keeps its last
  good data and shows the error above it. One broken source never breaks the page.

See `config.example.yaml` for a full working example.

## Setting up each source

### Dollar (`dolar`)
No setup needed. `casas` picks the rates and their order. Valid values:
`oficial`, `blue`, `bolsa` (MEP), `contadoconliqui`, `mayorista`, `tarjeta`, `cripto`.

### YouTube (`youtube`)
List channels by handle (`handle: "@veritasium"`) or by id
(`channel_id: UC...`). Ids are the most reliable. You can find a channel's id in
the channel's "About" page → Share → Copy channel ID. A video counts as NEW when
it was published within `new_within_hours` (default 24).

### Blogs (`blog`)
`feeds:` takes a list of `{name, url}` entries. The URL can be the RSS/Atom feed,
or the blog's homepage if the site advertises its feed. Substack, WordPress,
Medium, Ghost, Blogger and most other platforms do.

### Email (`email`)
Uses IMAP, read-only. Nothing gets marked as read.
- **Gmail**: turn on 2-Step Verification, then create an **App Password**
  (Google Account → Security → App passwords) and use that as `password`.
  `gmail_primary: true` limits the card to the "Primary" tab.
- **Outlook/Hotmail**: host `outlook.office365.com`. Microsoft has been disabling
  password IMAP login for many accounts, so this may not work for yours.
- Any other provider: set its IMAP `host` (port 993, SSL).

Add as many accounts as you want under `accounts:`. Each one gets its own section.

### Interactive Brokers (`ibkr`)
**Flex Web Service (default, recommended):** no software to keep running.
1. In Client Portal go to **Performance & Reports → Flex Queries**. Create an
   **Activity Flex Query** with the sections **Change in NAV** (all fields) and
   **Open Positions** (all fields, "Summary" level). Set Period = **Last Business Day**
   and Format = **XML**. Note the **Query ID**.
2. On the same page, under **Flex Web Service Configuration**, enable it and
   generate a **token**.
3. Put both in `.env` (`IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`).

The card shows:
- **Net liquidation**
- **Last session P&L** (money and time-weighted %)
- **Unrealized P&L of your current holdings vs cost basis**
- Optionally, **all-time gain vs deposits**: set `net_deposits` to the total you
  have deposited minus withdrawals. IBKR's Flex service can't cover your whole
  account history in one query, so this is the most reliable "all-time" figure.

Flex data refreshes once a day after the market closes. The card refreshes
hourly by default.

**Client Portal API (real-time):** run IBKR's
[Client Portal Gateway](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)
on the same machine, log in, and set `mode: client_portal`. You then get the
intraday day P&L, but the gateway session expires and needs a daily re-login.

### News (`news`)
`sections:` each have a `heading`, a list of `feeds`, and a `limit`. The example
config includes La Nación, Infobae, Clarín and Ámbito for Argentina, and BBC, The
Guardian, Al Jazeera and NYT for international news. Headlines that several
outlets share are shown once. With AI enabled, each section also gets a
4–6 bullet summary.

### Calendly (`calendly`)
Create a **personal access token** in Calendly → Integrations → API & Webhooks,
and put it in `CALENDLY_TOKEN`. The card lists today's meetings with the
invitees and join links. Set `days: 2` to include tomorrow too.

### AI summaries (optional)
Set `ai.enabled: true` and put an Anthropic API key in `ANTHROPIC_API_KEY`
(from [console.anthropic.com](https://console.anthropic.com)). The news and email
cards then get a short summary; `ai.language` controls the language it's written in.
Summaries are cached, so the same headlines aren't summarized twice. Set
`summarize: false` on a widget to turn summaries off for that card only.

## Adding a new widget

1. Create `app/widgets/my_widget.py`:

   ```python
   from .base import Widget, register, stat, item

   @register("weather")
   class WeatherWidget(Widget):
       default_refresh_minutes = 30

       def validate(self):
           self.option("city", required=True)       # checked at startup

       async def fetch(self):
           resp = await self.ctx.http.get("https://wttr.in/" + self.option("city"),
                                          params={"format": "j1"})
           resp.raise_for_status()
           now = resp.json()["current_condition"][0]
           return {"sections": [{"stats": [stat("Temp", f"{now['temp_C']}°C",
                                                sub=now["weatherDesc"][0]["value"])]}]}
   ```

2. Add it to `config.yaml`:

   ```yaml
   - {id: weather, type: weather, title: Clima, city: Buenos Aires}
   ```

That's it. The file is picked up automatically, and the frontend draws the
sections (`stats`, `table`, `items`, `text`) without any changes. The full output
format is documented at the top of `app/widgets/base.py`.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

Layout:

```
app/main.py          FastAPI server, per-widget cache, /api/layout, /api/widgets/<id>
app/config.py        config.yaml loading + ${ENV} expansion
app/summarizer.py    optional Claude summaries
app/widgets/         one file per widget type (auto-discovered)
static/              frontend (plain HTML/CSS/JS, no build step)
tests/               tests against fixture responses (no network needed)
```
