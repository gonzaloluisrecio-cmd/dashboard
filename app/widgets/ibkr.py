"""Interactive Brokers portfolio: net liquidation value, daily and all-time performance.

Two data sources (`mode` option):

* ``flex`` (default) – IBKR Flex Web Service. No software to run; data is from
  the last closed business day. Needs a Flex token and an Activity Flex Query
  (period "Last Business Day") with the "Change in NAV" and "Open Positions" sections.
* ``client_portal`` – the Client Portal Web API gateway running locally
  (https://localhost:5000). Real-time, but you must keep the gateway logged in.

"All-time" is shown two ways: unrealized P&L of current holdings vs their
cost basis, and — if you set `net_deposits` (total money you put in minus
withdrawals) — the account's total gain vs what you deposited.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import httpx

from .base import (Widget, WidgetError, fmt_money, fmt_pct, fmt_signed_money, register, stat,
                   trend_of)

FLEX_SEND_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
FLEX_IN_PROGRESS_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}


@dataclass
class Position:
    symbol: str
    quantity: float
    value: float
    unrealized: float | None
    cost_basis: float | None

    @property
    def pnl_pct(self) -> float | None:
        if self.unrealized is None or not self.cost_basis:
            return None
        return self.unrealized / abs(self.cost_basis) * 100


@dataclass
class PortfolioSnapshot:
    nav: float | None = None
    day_pnl: float | None = None
    day_pct: float | None = None
    currency: str = "USD"
    as_of: str | None = None
    positions: list[Position] = field(default_factory=list)

    @property
    def unrealized(self) -> float | None:
        vals = [p.unrealized for p in self.positions if p.unrealized is not None]
        return sum(vals) if vals else None

    @property
    def cost_basis(self) -> float | None:
        vals = [p.cost_basis for p in self.positions if p.cost_basis is not None]
        return sum(vals) if vals else None


def _f(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---- Flex -------------------------------------------------------------------

def parse_flex_send_response(xml_text: str) -> tuple[str, str]:
    root = ET.fromstring(xml_text)
    if root.findtext("Status") != "Success":
        raise WidgetError(f"IBKR Flex request failed: {root.findtext('ErrorMessage') or xml_text[:200]}")
    return root.findtext("ReferenceCode") or "", root.findtext("Url") or ""


def parse_flex_statement(xml_text: str) -> PortfolioSnapshot:
    root = ET.fromstring(xml_text)
    if root.tag == "FlexStatementResponse":
        code = root.findtext("ErrorCode") or ""
        msg = root.findtext("ErrorMessage") or "unknown error"
        if code in FLEX_IN_PROGRESS_CODES:
            raise _FlexNotReady(msg)
        raise WidgetError(f"IBKR Flex error {code}: {msg}")

    snap = PortfolioSnapshot()
    start_total = end_total = 0.0
    have_nav = False
    for st in root.iter("FlexStatement"):
        snap.as_of = st.get("toDate") or snap.as_of
        nav = st.find("ChangeInNAV")
        if nav is not None:
            start, end = _f(nav.get("startingValue")), _f(nav.get("endingValue"))
            if end is not None:
                have_nav = True
                end_total += end
                start_total += start or 0.0
                snap.currency = nav.get("currency", snap.currency)
                if snap.day_pct is None:
                    snap.day_pct = _f(nav.get("twr"))
        for op in st.iter("OpenPosition"):
            if op.get("levelOfDetail", "SUMMARY") != "SUMMARY":
                continue
            fx = _f(op.get("fxRateToBase")) or 1.0
            value = (_f(op.get("positionValue")) or 0.0) * fx
            unreal = _f(op.get("fifoPnlUnrealized"))
            cost = _f(op.get("costBasisMoney"))
            snap.positions.append(
                Position(
                    symbol=op.get("symbol", "?"),
                    quantity=_f(op.get("position")) or 0.0,
                    value=value,
                    unrealized=unreal * fx if unreal is not None else None,
                    cost_basis=cost * fx if cost is not None else None,
                )
            )
    if have_nav:
        snap.nav = end_total
        snap.day_pnl = end_total - start_total
        if snap.day_pct is None and start_total:
            snap.day_pct = snap.day_pnl / start_total * 100
    elif snap.positions:
        snap.nav = sum(p.value for p in snap.positions)
    return snap


class _FlexNotReady(Exception):
    pass


async def fetch_flex(http: httpx.AsyncClient, token: str, query_id: str,
                     attempts: int = 6, delay: float = 5.0) -> PortfolioSnapshot:
    resp = await http.get(FLEX_SEND_URL, params={"t": token, "q": query_id, "v": "3"})
    resp.raise_for_status()
    ref, url = parse_flex_send_response(resp.text)
    for attempt in range(attempts):
        resp = await http.get(url, params={"t": token, "q": ref, "v": "3"})
        resp.raise_for_status()
        try:
            return parse_flex_statement(resp.text)
        except _FlexNotReady:
            if attempt == attempts - 1:
                raise WidgetError("IBKR is still generating the statement; will retry on next refresh")
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


# ---- Client Portal ----------------------------------------------------------

def parse_client_portal(pnl: dict[str, Any], positions: list[dict[str, Any]], account_id: str) -> PortfolioSnapshot:
    snap = PortfolioSnapshot()
    for key, row in (pnl.get("upnl") or {}).items():
        if key.startswith(account_id):
            snap.nav = _f(row.get("nl"))
            snap.day_pnl = _f(row.get("dpl"))
            break
    if snap.nav is not None and snap.day_pnl is not None and snap.nav - snap.day_pnl:
        snap.day_pct = snap.day_pnl / (snap.nav - snap.day_pnl) * 100
    for p in positions:
        value = _f(p.get("mktValue")) or 0.0
        unreal = _f(p.get("unrealizedPnl"))
        snap.positions.append(
            Position(
                symbol=p.get("contractDesc") or p.get("ticker") or str(p.get("conid")),
                quantity=_f(p.get("position")) or 0.0,
                value=value,
                unrealized=unreal,
                cost_basis=(value - unreal) if unreal is not None else None,
            )
        )
        snap.currency = p.get("currency", snap.currency)
    return snap


async def fetch_client_portal(base_url: str, account_id: str | None, verify_ssl: bool) -> PortfolioSnapshot:
    # The gateway uses a self-signed certificate on localhost, hence its own client.
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), verify=verify_ssl, timeout=20) as cp:
        if not account_id:
            resp = await cp.get("/portfolio/accounts")
            resp.raise_for_status()
            accounts = resp.json()
            if not accounts:
                raise WidgetError("Client Portal returned no accounts — is the gateway logged in?")
            account_id = accounts[0]["id"]
        await cp.get("/iserver/accounts")  # required before /iserver/* calls
        pnl_resp = await cp.get("/iserver/account/pnl/partitioned")
        pnl_resp.raise_for_status()
        positions: list[dict] = []
        for page in range(10):
            resp = await cp.get(f"/portfolio/{account_id}/positions/{page}")
            resp.raise_for_status()
            batch = resp.json() or []
            positions += batch
            if len(batch) < 100:
                break
    return parse_client_portal(pnl_resp.json(), positions, account_id)


# ---- Widget -----------------------------------------------------------------

def render_snapshot(snap: PortfolioSnapshot, net_deposits: float | None, top: int, mode: str) -> dict[str, Any]:
    cur = "$" if snap.currency == "USD" else f"{snap.currency} "
    day_label = "Day" if mode == "client_portal" else "Last session"
    stats = [
        stat("Net liquidation", fmt_money(snap.nav, cur), sub=f"as of {snap.as_of}" if snap.as_of else None),
        stat(f"{day_label} P&L", fmt_signed_money(snap.day_pnl, cur), sub=fmt_pct(snap.day_pct),
             trend=trend_of(snap.day_pnl)),
    ]
    unreal, cost = snap.unrealized, snap.cost_basis
    if unreal is not None:
        pct = unreal / abs(cost) * 100 if cost else None
        stats.append(stat("Unrealized P&L (holdings)", fmt_signed_money(unreal, cur), sub=fmt_pct(pct),
                          trend=trend_of(unreal)))
    if net_deposits and snap.nav is not None:
        gain = snap.nav - net_deposits
        stats.append(stat("All-time vs deposits", fmt_signed_money(gain, cur),
                          sub=f"{fmt_pct(gain / net_deposits * 100)} on {fmt_money(net_deposits, cur, 0)}",
                          trend=trend_of(gain)))

    positions = sorted(snap.positions, key=lambda p: abs(p.value), reverse=True)[:top]
    rows = [[p.symbol, f"{p.quantity:g}", fmt_money(p.value, cur), fmt_signed_money(p.unrealized, cur),
             fmt_pct(p.pnl_pct)] for p in positions]
    sections: list[dict[str, Any]] = [{"stats": stats}]
    if rows:
        sections.append({"heading": "Top positions",
                         "table": {"columns": ["Symbol", "Qty", "Value", "P&L", "%"], "rows": rows}})
    return {"sections": sections}


@register("ibkr")
class IBKRWidget(Widget):
    """Options:
      mode: flex | client_portal (default flex)
      flex_token, flex_query_id: for mode=flex
      base_url (https://localhost:5000/v1/api), account_id, verify_ssl (false): for mode=client_portal
      net_deposits: optional number for the "all-time vs deposits" figure
      top_positions: rows in the positions table (default 8)
    """

    default_refresh_minutes = 60

    def validate(self) -> None:
        mode = self.option("mode", "flex")
        if mode == "flex":
            self.option("flex_token", required=True)
            self.option("flex_query_id", required=True)
        elif mode != "client_portal":
            raise WidgetError(f"ibkr mode must be 'flex' or 'client_portal', got '{mode}'")

    async def fetch(self) -> dict[str, Any]:
        mode = self.option("mode", "flex")
        if mode == "flex":
            snap = await fetch_flex(self.ctx.http, str(self.option("flex_token")), str(self.option("flex_query_id")))
        else:
            snap = await fetch_client_portal(
                self.option("base_url", "https://localhost:5000/v1/api"),
                self.option("account_id"),
                bool(self.option("verify_ssl", False)),
            )
        net_deposits = _f(self.option("net_deposits"))
        return render_snapshot(snap, net_deposits, int(self.option("top_positions", 8)), mode)
