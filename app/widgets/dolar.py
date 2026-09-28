"""USD → ARS quotes (buy / sell) from dolarapi.com (free, no key)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import Widget, WidgetError, fmt_money, register, relative_time

DEFAULT_URL = "https://dolarapi.com/v1/dolares"
DEFAULT_CASAS = ["oficial", "blue", "bolsa", "contadoconliqui", "tarjeta", "cripto"]


def parse_quotes(payload: list[dict[str, Any]], casas: list[str]) -> list[dict[str, Any]]:
    by_casa = {q.get("casa"): q for q in payload if isinstance(q, dict)}
    return [by_casa[c] for c in casas if c in by_casa]


@register("dolar")
class DolarWidget(Widget):
    """Options:
      casas:   which rates to show, in order (default: oficial, blue, bolsa, contadoconliqui, tarjeta, cripto)
      api_url: override the data source (must return dolarapi.com's JSON shape)
    """

    default_refresh_minutes = 10

    async def fetch(self) -> dict[str, Any]:
        url = self.option("api_url", DEFAULT_URL)
        casas = self.option("casas", DEFAULT_CASAS)
        resp = await self.ctx.http.get(url)
        resp.raise_for_status()
        quotes = parse_quotes(resp.json(), casas)
        if not quotes:
            raise WidgetError("no quotes returned for the configured 'casas'")

        now = self.ctx.now()
        rows = []
        latest: datetime | None = None
        for q in quotes:
            buy, sell = q.get("compra"), q.get("venta")
            spread = f"{(sell - buy) / buy * 100:.1f}%" if buy and sell else "—"
            rows.append([q.get("nombre", q.get("casa")), fmt_money(buy), fmt_money(sell), spread])
            try:
                updated = datetime.fromisoformat(q["fechaActualizacion"].replace("Z", "+00:00"))
                latest = max(latest, updated) if latest else updated
            except (KeyError, ValueError, AttributeError):
                pass

        blue = next((q for q in quotes if q.get("casa") == "blue"), quotes[0])
        return {
            "sections": [
                {
                    "stats": [
                        {"label": f"{blue.get('nombre')} · compra", "value": fmt_money(blue.get("compra"))},
                        {"label": f"{blue.get('nombre')} · venta", "value": fmt_money(blue.get("venta"))},
                    ]
                },
                {
                    "table": {"columns": ["", "Compra", "Venta", "Spread"], "rows": rows},
                    "text": f"Source updated {relative_time(latest.astimezone(now.tzinfo), now)}" if latest else None,
                },
            ]
        }
