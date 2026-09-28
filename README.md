# Personal Dashboard

A free, self-hosted dashboard for your laptop, PC and Android phone. It shows:

| Card | What it tells you | Source (all free) |
|---|---|---|
| ✉️ Email | New (unread) mail in the main inbox of each account, with a one-line summary per email | IMAP + app password |
| 📅 Calendly | How many meetings you have today and who you're meeting | Calendly API (free plans included) |
| 🗓️ Google Calendar | What's on your calendar today | Your calendar's secret iCal link |
| ▶️ YouTube | Which of your channels uploaded a new video | YouTube's public RSS feeds |
| 🎙️ Podcasts | Which of your podcasts published a new episode | Podcast RSS feeds and Apple's podcast directory |
| 📰 News | Argentina and world headlines, plus an AI digest if enabled | Newspaper RSS feeds |
| 💵 Dólar | USD → ARS right now (oficial, blue, MEP, CCL, tarjeta, cripto) | [dolarapi.com](https://dolarapi.com) |

There are also optional Interactive Brokers and blog cards.

**You add and delete everything inside the app.** Click **⚙ Settings** to add
email accounts, the Calendly token, Google calendars, YouTube channels, podcasts
and the optional AI key. Each one is checked when you add it (the email login
works, the channel exists...), and the dashboard updates right away.

## 1. Run it on your PC

1. **Install Python** (3.10 or newer) from https://www.python.org/downloads/.
   On Windows, tick **"Add python.exe to PATH"** in the installer.
2. **Download this project**: on GitHub, click the green **Code** button →
   **Download ZIP**, then unzip it anywhere (e.g. your Desktop).
3. **Start it**:
   - **Windows**: double-click `start.bat`.
   - **Mac/Linux**: open a terminal in the folder and run `./start.sh`.

   The first run takes a minute to install what it needs. Then your browser opens
   **http://127.0.0.1:8000**. Keep the black window open while you use the
   dashboard; closing it stops the dashboard.
4. Click **⚙ Settings** and add your accounts. The Settings page explains, for
   each one, where to get the password, token or link.

The dollar and news cards work right away.

## 2. Open it from your phone and other computers

The dashboard runs on your PC, so the PC has to be on. Pick one of these:

### A. From anywhere, private (recommended): Tailscale, free
[Tailscale](https://tailscale.com) makes a private network between your own
devices. Nobody else can reach the dashboard, and you don't need to open any
ports on your router.

1. Install Tailscale on the PC (tailscale.com/download) and on your Android phone
   (Play Store). Log in with the same account on both.
2. On the PC, open a terminal (Windows: the "Command Prompt") and run:
   ```
   tailscale serve --bg 8000
   ```
   It prints an address like `https://my-pc.tail1234.ts.net`. If it asks you to
   enable HTTPS or Serve for your network, follow the link it shows and turn it on.
3. Open that address on your phone or laptop (with Tailscale on). It works on
   Wi-Fi and mobile data.
4. On Android, in Chrome: **⋮ → Add to Home screen / Install app**. The dashboard
   then opens like a normal app.

### B. Only at home, same Wi-Fi
1. Open `.env` in Notepad and set a password: `DASHBOARD_PASSWORD=something-long`.
2. Restart the dashboard. With a password set, it accepts connections from
   other devices on your Wi-Fi.
3. Find your PC's local address (Windows: run `ipconfig` and look for "IPv4
   Address", e.g. `192.168.1.20`) and open `http://192.168.1.20:8000` on the
   phone. Log in once and it remembers you.
   Windows may ask whether to allow Python through the firewall. Allow it on
   **private** networks.

You can also set `DASHBOARD_PASSWORD` with option A for an extra lock.

> **Keep the PC from sleeping.** Windows: Settings → System → Power → Sleep = Never
> (when plugged in). To start the dashboard automatically, put a shortcut to
> `start.bat` in the Startup folder (Win+R → `shell:startup`).

## Setting up each source

All of these are added in **⚙ Settings**. The notes below cover the details.

### Email
Uses IMAP and only reads. Nothing gets marked as read. Add as many accounts as you like.
- **Gmail**: turn on 2-Step Verification, then create an **App Password** at
  https://myaccount.google.com/apppasswords and paste it. By default only the
  **Primary** tab counts (Promotions, Social etc. are ignored).
- **Yahoo / iCloud**: create an app password in the account's security settings.
- **Outlook/Hotmail**: Microsoft has turned off password IMAP logins for many
  personal accounts. If adding it fails, forward that mailbox to Gmail.
- **Other**: pick "Other" and enter the IMAP server (port 993, SSL).

Each unread email shows the sender, subject and a one-line summary. Without AI,
the summary is the first line of the message. With AI on, it's a real summary.

### Calendly
Calendly → [Integrations → API & Webhooks](https://calendly.com/integrations/api_webhooks)
→ generate a personal access token and paste it. The card shows how many
meetings you have today, who with, and the join links.

### Google Calendar
Google Calendar on a computer → ⚙ Settings → click your calendar → **Integrate
calendar** → copy **Secret address in iCal format** and paste it. You can add
several calendars. Recurring events are included. Keep that link private.

### YouTube
Paste the channel link (`https://www.youtube.com/@name`) or just `@name`. A
video counts as NEW for 24 hours. Change `new_within_hours` in `config.yaml` to
adjust this.

### Podcasts
Search by name (this uses Apple's free podcast directory), or paste an RSS feed or
Apple Podcasts link. Spotify-only shows have no public feed. The card lists the
latest episode of each show and marks episodes from the last 3 days as NEW
(`new_within_hours: 72`).

### News
Argentina (La Nación, Infobae, Clarín, Ámbito) and world news (BBC, The
Guardian, Al Jazeera, NYT). Headlines that several outlets share are shown once.
With AI on, each section gets a 4–6 bullet summary. Edit the feeds in `config.yaml`.

### Dólar
No setup needed. `casas` in `config.yaml` picks the rates and their order:
`oficial`, `blue`, `bolsa` (MEP), `contadoconliqui`, `mayorista`, `tarjeta`, `cripto`.

### AI summaries (optional, free)
Get a free Gemini API key at https://aistudio.google.com/apikey and paste it in
Settings. You then get a one-line AI summary of each email and a short news
digest. The free tier easily covers a personal dashboard. Google may use
free-tier requests to improve its products, so leave AI off if you'd rather not
send email subjects and snippets to Google. Everything else works without it.
(The Claude API is also supported via `ai.provider: anthropic` in `config.yaml`,
but it's paid.)

### Interactive Brokers (`ibkr`, optional)
**Flex Web Service (default, recommended):** no software to keep running.
1. In Client Portal go to **Performance & Reports → Flex Queries**. Create an
   **Activity Flex Query** with the sections **Change in NAV** (all fields) and
   **Open Positions** (all fields, "Summary" level). Set Period = **Last Business Day**
   and Format = **XML**. Note the **Query ID**.
2. On the same page, under **Flex Web Service Configuration**, enable it and
   generate a **token**.
3. Put both in `.env` (`IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`) and set
   `enabled: true` on the `portfolio` card in `config.yaml`.

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

## Where things are stored

- `data/settings.json`: everything added in Settings, including email app
  passwords and tokens. It lives only on your PC and is never uploaded. Don't
  share it.
- `config.yaml`: which cards exist, their order, size and options. Restart the
  dashboard after editing it.
- `.env`: `DASHBOARD_PASSWORD`, plus optional keys referenced from
  `config.yaml` as `${VAR}`.

Cards are cached on the server. If a source fails, the card keeps its last good
data and shows the error above it. One broken source never breaks the page.

## Configuration (`config.yaml`)

```yaml
title: Mi Dashboard
timezone: America/Argentina/Buenos_Aires

widgets:
  - id: dolar            # unique id
    type: dolar          # email | calendly | gcal | youtube | podcast | news | dolar | blog | feed | ibkr
    title: Dólar hoy     # card title
    size: medium         # small | medium | large (2 columns) | full (whole row)
    refresh_minutes: 10  # optional
    enabled: true        # false hides it without deleting it
    casas: [oficial, blue, bolsa]   # widget-specific options
```

Items you add in Settings are merged into the card of the same type. If
`config.yaml` has no such card, one is added automatically. You can also list
sources directly in `config.yaml` (e.g. `channels:` under the YouTube card), but
those can't be deleted from the app. See `config.example.yaml`.

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
app/main.py          FastAPI server, per-widget cache, login, /api/layout, /api/widgets/<id>
app/settings.py      data/settings.json (sources added in the app) + merging into config
app/settings_api.py  /api/settings: add (with validation) and delete sources
app/config.py        config.yaml loading + ${ENV} expansion
app/summarizer.py    optional AI summaries (Gemini free tier or Claude)
app/widgets/         one file per widget type (auto-discovered)
static/              frontend (plain HTML/CSS/JS, no build step)
tests/               tests against fixture responses (no network needed)
```
