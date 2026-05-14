#!/usr/bin/env python3
"""
server.py — Remote MCP server for stock technical analysis
Deployed to Render; connected to claude.ai Projects as an MCP integration.
"""

import json
import os
import urllib.request
from mcp.server.fastmcp import FastMCP

API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "N4PODJ9VXOBBL34R")
BASE_URL = "https://www.alphavantage.co/query"
PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP("Stock Technicals", host="0.0.0.0", port=PORT)


# ── data fetching ──────────────────────────────────────────────────────────────

def _fetch_daily(ticker: str) -> list[dict]:
    url = f"{BASE_URL}?function=TIME_SERIES_DAILY&symbol={ticker}&outputsize=compact&apikey={API_KEY}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read())
    if "Time Series (Daily)" not in data:
        msg = data.get("Note") or data.get("Information") or data.get("Error Message") or str(data)
        raise ValueError(f"Alpha Vantage error: {msg}")
    series = data["Time Series (Daily)"]
    rows = []
    for d in sorted(series.keys(), reverse=True):
        v = series[d]
        rows.append({
            "date":   d,
            "open":   float(v["1. open"]),
            "high":   float(v["2. high"]),
            "low":    float(v["3. low"]),
            "close":  float(v["4. close"]),
            "volume": int(v["5. volume"]),
        })
    return rows


# ── indicators ────────────────────────────────────────────────────────────────

def _sma(closes, period):
    if len(closes) < period:
        return None
    return round(sum(closes[:period]) / period, 4)


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(period):
        delta = closes[i] - closes[i + 1]
        (gains if delta > 0 else losses).append(abs(delta))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def _atr(rows, period=14):
    if len(rows) < period + 1:
        return None
    trs = []
    for i in range(period):
        h, l, pc = rows[i]["high"], rows[i]["low"], rows[i + 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(sum(trs) / period, 4)


def _levels(rows, lookback=60, proximity=0.015):
    def cluster(prices, min_hits=2):
        prices = sorted(prices)
        used = [False] * len(prices)
        zones = []
        for i, p in enumerate(prices):
            if used[i]:
                continue
            grp = [p]
            for j in range(i + 1, len(prices)):
                if abs(prices[j] - p) / p <= proximity:
                    grp.append(prices[j])
                    used[j] = True
            if len(grp) >= min_hits:
                zones.append(round(sum(grp) / len(grp), 2))
        return zones

    subset = rows[:lookback]
    return (
        cluster([r["low"]  for r in subset]),
        cluster([r["high"] for r in subset]),
    )


# ── MCP tool ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_technicals(ticker: str) -> str:
    """
    Fetch daily OHLC data from Alpha Vantage and return a full technical
    summary: 50MA, RSI(14), ATR(14), 52-week range, and support/resistance zones.

    Args:
        ticker: Stock ticker symbol, e.g. TSLA, OKLO, NVDA, NOW
    """
    ticker = ticker.upper().strip()
    rows   = _fetch_daily(ticker)
    closes = [r["close"] for r in rows]
    price  = closes[0]
    date   = rows[0]["date"]

    ma50        = _sma(closes, 50)
    ma200       = _sma(closes, 200)
    rsi_val     = _rsi(closes, 14)
    atr_val     = _atr(rows, 14)
    support, resistance = _levels(rows)

    year_rows   = rows[:252]
    week52_high = max(r["high"] for r in year_rows)
    week52_low  = min(r["low"]  for r in year_rows)
    avg_vol     = sum(r["volume"] for r in rows[:20]) // 20

    def fmt_rsi(r):
        if r is None: return "n/a"
        tag = " (OVERBOUGHT)" if r >= 70 else " (OVERSOLD)" if r <= 30 else ""
        return f"{r}{tag}"

    def fmt_trend(p, m50, m200):
        if m50 and m200:
            if p > m50 > m200: return "BULLISH — price > 50MA > 200MA"
            if p < m50 < m200: return "BEARISH — price < 50MA < 200MA"
            return "MIXED"
        if m50:
            return "BULLISH vs 50MA" if p > m50 else "BEARISH vs 50MA"
        return "INSUFFICIENT DATA"

    sep = "=" * 54
    lines = [
        "",
        sep,
        f"  TECHNICAL SUMMARY: {ticker}   ({date})",
        sep,
        "",
        "PRICE ACTION",
        f"  Last Close    : ${price:,.2f}",
        f"  52-Week High  : ${week52_high:,.2f}",
        f"  52-Week Low   : ${week52_low:,.2f}",
        f"  Avg Vol (20d) : {avg_vol:,}",
        "",
        "MOVING AVERAGES",
        f"  50 MA         : {'${:,.2f}'.format(ma50)  if ma50  else 'n/a'}",
        f"  200 MA        : {'${:,.2f}'.format(ma200) if ma200 else 'n/a (free key = 100 bars max)'}",
        f"  Trend         : {fmt_trend(price, ma50, ma200)}",
        "",
        "MOMENTUM",
        f"  RSI(14)       : {fmt_rsi(rsi_val)}",
        f"  ATR(14)       : {'${:,.4f}'.format(atr_val) if atr_val else 'n/a'}",
        "",
        "SUPPORT & RESISTANCE  (60-day swing clusters)",
        f"  Resistance    : {', '.join('$'+str(x) for x in sorted(resistance, reverse=True)) or 'none'}",
        f"  Support       : {', '.join('$'+str(x) for x in sorted(support,     reverse=True)) or 'none'}",
        "",
        sep,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="sse")
