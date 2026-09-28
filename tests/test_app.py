import textwrap

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import ConfigError, parse_config
from app.main import create_app


def test_env_expansion_and_inline_options(monkeypatch):
    monkeypatch.setenv("TOKEN_X", "secret")
    cfg = parse_config({
        "widgets": [
            {"id": "a", "type": "calendly", "token": "${TOKEN_X}", "days": 2},
            {"type": "dolar", "options": {"casas": ["blue"]}, "api_url": "${MISSING:-https://fallback}"},
        ]
    })
    assert cfg.widgets[0].options == {"token": "secret", "days": 2}
    assert cfg.widgets[1].id == "dolar-1"
    assert cfg.widgets[1].options == {"casas": ["blue"], "api_url": "https://fallback"}


def test_duplicate_ids_rejected():
    with pytest.raises(ConfigError):
        parse_config({"widgets": [{"id": "x", "type": "dolar"}, {"id": "x", "type": "dolar"}]})


@pytest.fixture
def client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent("""
        title: Test
        widgets:
          - {id: dolar, type: dolar, title: Dólar, casas: [blue]}
          - {id: cal, type: calendly, title: Cal}          # missing token -> setup error
          - {id: nope, type: does_not_exist}
          - {id: off, type: dolar, enabled: false}
    """))
    with TestClient(create_app(cfg, tmp_path / "settings.json")) as c:
        yield c


def test_layout(client):
    layout = client.get("/api/layout").json()
    assert layout["title"] == "Test"
    assert [w["id"] for w in layout["widgets"]] == ["dolar", "cal", "nope"]
    assert layout["login"] is False


def test_setup_errors_are_per_widget(client):
    assert "token" in client.get("/api/widgets/cal").json()["error"]
    assert "unknown widget type" in client.get("/api/widgets/nope").json()["error"]
    assert client.get("/api/widgets/missing").status_code == 404


def test_widget_fetch_cache_and_stale_data(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        route = mock.get("https://dolarapi.com/v1/dolares").mock(return_value=httpx.Response(
            200, json=[{"casa": "blue", "nombre": "Blue", "compra": 1, "venta": 2}]))
        first = client.get("/api/widgets/dolar").json()
        assert first["error"] is None and first["data"]["sections"]
        client.get("/api/widgets/dolar")
        assert route.call_count == 1  # served from cache

        route.mock(return_value=httpx.Response(500))
        forced = client.get("/api/widgets/dolar?force=true").json()
        assert forced["error"] == "HTTP 500 from dolarapi.com"
        assert forced["data"] == first["data"]  # last good data is kept


def test_index_served(client):
    assert "<main id=\"grid\"" in client.get("/").text


# ---- settings added from the app ----------------------------------------------

from app.settings import apply_settings  # noqa: E402


def test_apply_settings_merges_and_adds_missing_cards():
    raw = {"widgets": [
        {"id": "yt", "type": "youtube", "channels": [{"name": "Cfg", "channel_id": "UC1"}]},
        {"id": "cal", "type": "calendly", "token": "old"},
    ]}
    settings = {
        "youtube_channels": [{"uid": "a", "name": "App", "channel_id": "UC2"}],
        "podcasts": [{"uid": "b", "name": "Pod", "url": "https://p/rss"}],
        "calendly": {"token": "new"},
        "ai": {"provider": "gemini", "api_key": "k", "model": ""},
    }
    out = apply_settings(raw, settings)
    yt, cal, pod = out["widgets"]
    assert [c["name"] for c in yt["channels"]] == ["Cfg", "App"]
    assert "uid" not in yt["channels"][1]
    assert cal["token"] == "new"
    assert pod["type"] == "podcast" and pod["feeds"][0]["url"] == "https://p/rss"
    assert out["ai"] == {"provider": "gemini", "api_key": "k", "enabled": True}
    assert raw["widgets"][0]["channels"] == [{"name": "Cfg", "channel_id": "UC1"}]  # input untouched


YT_FEED = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Veritasium</title></feed>'
CID = "UC" + "b" * 22


def test_settings_add_list_delete(client, tmp_path):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get("https://www.youtube.com/@veritasium").mock(
            return_value=httpx.Response(200, text=f'"externalId":"{CID}"'))
        mock.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={CID}").mock(
            return_value=httpx.Response(200, text=YT_FEED))
        res = client.post("/api/settings/youtube_channels", json={"channel": "https://www.youtube.com/@veritasium"})
        assert res.status_code == 200, res.text
        [entry] = res.json()["youtube_channels"]
        assert entry["name"] == "Veritasium" and entry["channel_id"] == CID

        dup = client.post("/api/settings/youtube_channels", json={"channel": "@veritasium"})
        assert dup.status_code == 400 and "already" in dup.json()["detail"]

    # A YouTube card appears even though config.yaml had none, and it survives a restart.
    assert "youtube" in [w["type"] for w in client.get("/api/layout").json()["widgets"]]
    assert CID in (tmp_path / "settings.json").read_text()

    assert client.delete(f"/api/settings/youtube_channels/{entry['uid']}").json()["youtube_channels"] == []
    assert client.delete(f"/api/settings/youtube_channels/{entry['uid']}").status_code == 404
    assert "youtube" not in [w["type"] for w in client.get("/api/layout").json()["widgets"]]


def test_settings_never_return_secrets(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get("https://api.calendly.com/users/me").mock(return_value=httpx.Response(200, json={}))
        res = client.put("/api/settings/calendly", json={"token": "SECRET-TOKEN"})
    assert res.status_code == 200
    assert res.json()["calendly"] == {"has_token": True}
    assert "SECRET-TOKEN" not in client.get("/api/settings").text
    # The token from Settings replaces the missing one in config.yaml.
    assert "token" not in (client.get("/api/widgets/cal").json()["error"] or "")


def test_settings_bad_calendly_token(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host="testserver").pass_through()
        mock.get("https://api.calendly.com/users/me").mock(return_value=httpx.Response(401))
        res = client.put("/api/settings/calendly", json={"token": "bad"})
    assert res.status_code == 400 and "rejected" in res.json()["detail"]


def test_email_login_failure_is_reported(client, monkeypatch):
    import imaplib

    import app.settings_api as settings_api

    def fail(account):
        assert account["host"] == "imap.gmail.com" and account["password"] == "abcdabcdabcdabcd"
        raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials")

    monkeypatch.setattr(settings_api, "test_login", fail)
    res = client.post("/api/settings/email_accounts", json={
        "provider": "gmail", "username": "me@gmail.com", "password": "abcd abcd abcd abcd"})
    assert res.status_code == 400
    assert "Invalid credentials" in res.json()["detail"] and "App Password" in res.json()["detail"]

    monkeypatch.setattr(settings_api, "test_login", lambda account: None)
    res = client.post("/api/settings/email_accounts", json={
        "provider": "gmail", "username": "me@gmail.com", "password": "abcd abcd abcd abcd"})
    [acc] = res.json()["email_accounts"]
    assert acc["has_password"] is True and "password" not in acc and acc["gmail_primary"] is True


def test_password_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("widgets: []\n")
    with TestClient(create_app(cfg, tmp_path / "s.json")) as c:
        assert c.get("/api/layout").status_code == 401
        assert c.get("/", follow_redirects=False).headers["location"] == "/login"
        assert c.get("/static/style.css").status_code == 200
        assert c.post("/login", json={"password": "nope"}).status_code == 401
        assert c.post("/login", json={"password": "hunter2"}).status_code == 200
        assert c.get("/api/layout").json()["login"] is True
        assert c.get("/settings").status_code == 200
