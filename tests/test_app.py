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
    with TestClient(create_app(cfg)) as c:
        yield c


def test_layout(client):
    layout = client.get("/api/layout").json()
    assert layout["title"] == "Test"
    assert [w["id"] for w in layout["widgets"]] == ["dolar", "cal", "nope"]


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
