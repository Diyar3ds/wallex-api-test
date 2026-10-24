#!/usr/bin/env python3
"""Fetch Wallex market prices (read-only) and write the latest JSON feed."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BASE_URL = "https://api.wallex.ir"
TIMEOUT = 18
PUBLIC_PATHS = ("/v1/markets", "/hector/web/v1/markets")
AUTH_PATH = "/v1/otc/markets"
ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "wallex_rates.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def api_get(path: str, api_key: str | None = None) -> tuple[int | None, Any, str | None]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "wallex-readonly-rate-feed/1.0",
    }
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(raw), None
            except json.JSONDecodeError:
                return response.status, None, "Response was not valid JSON"
    except urllib.error.HTTPError as exc:
        # Do not print response headers, request headers, or secrets.
        return exc.code, None, f"HTTP error {exc.code}"
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def extract_markets(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        return {}
    raw = result.get("symbols") or result.get("markets")
    if isinstance(raw, dict):
        return {
            str(value.get("symbol") or key).upper(): value
            for key, value in raw.items()
            if isinstance(value, dict)
        }
    if isinstance(raw, list):
        return {
            str(value.get("symbol") or "").upper(): value
            for value in raw
            if isinstance(value, dict) and value.get("symbol")
        }
    return {
        str(value.get("symbol") or key).upper(): value
        for key, value in result.items()
        if isinstance(value, dict)
        and (value.get("symbol") or value.get("baseAsset") or value.get("stats"))
    }


def numeric(value: Any) -> Decimal | None:
    if value is None or str(value).strip() in ("", "-", "—", "null", "None"):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def merge_markets(target: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]]) -> None:
    """Merge sources while never replacing a valid numeric quote with '-' or null."""
    for symbol, market in incoming.items():
        dest = target.setdefault(symbol, {})
        for key, value in market.items():
            if key == "stats" and isinstance(value, dict):
                stats = dest.setdefault("stats", {})
                for stat_name, stat_value in value.items():
                    if numeric(stat_value) is not None or stat_name not in stats:
                        stats[stat_name] = stat_value
            elif value is not None:
                dest[key] = value


def market_value(markets: dict[str, dict[str, Any]], symbol: str, field: str) -> Decimal | None:
    market = markets.get(symbol)
    if not isinstance(market, dict):
        return None
    stats = market.get("stats")
    if isinstance(stats, dict):
        value = numeric(stats.get(field))
        if value is not None:
            return value
    return numeric(market.get(field))


def json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def market_record(markets: dict[str, dict[str, Any]], symbol: str) -> dict[str, Any]:
    market = markets.get(symbol, {})
    return {
        "symbol": symbol,
        "name_fa": market.get("faName"),
        "name_en": market.get("enName"),
        "last": json_number(market_value(markets, symbol, "lastPrice")),
        "bid": json_number(market_value(markets, symbol, "bidPrice")),
        "ask": json_number(market_value(markets, symbol, "askPrice")),
        "buy_status": market.get("buyStatus"),
        "sell_status": market.get("sellStatus"),
    }


def main() -> int:
    print("=== WALLEX RATE FEED (READ ONLY) ===")
    print("Started UTC:", utc_now())
    print("No order, trade, or wallet endpoints are called.")

    markets: dict[str, dict[str, Any]] = {}
    public_success = False
    for path in PUBLIC_PATHS:
        code, payload, error = api_get(path)
        found = extract_markets(payload)
        ok = code == 200 and isinstance(payload, dict) and payload.get("success") is True
        print(f"PUBLIC {path}: HTTP={code}; success={ok}; markets={len(found)}")
        if error:
            print("Detail:", error)
        if found:
            merge_markets(markets, found)
        public_success = public_success or ok

    api_key = os.environ.get("WALLEX_API_KEY", "").strip()
    auth_success = False
    if api_key:
        code, payload, error = api_get(AUTH_PATH, api_key)
        found = extract_markets(payload)
        auth_success = code == 200 and isinstance(payload, dict) and payload.get("success") is True
        print(f"AUTH {AUTH_PATH}: HTTP={code}; success={auth_success}; markets={len(found)}")
        if error:
            print("Authenticated detail:", error)
        if found:
            merge_markets(markets, found)
    else:
        print("AUTH skipped: WALLEX_API_KEY secret is missing.")

    gram_usdt = market_value(markets, "GRAMUSDT", "lastPrice")
    usdt_tmn = market_value(markets, "USDTTMN", "lastPrice") or market_value(markets, "USDTIRT", "lastPrice")
    gram_tmn = market_value(markets, "GRAMTMN", "lastPrice") or market_value(markets, "GRAMIRT", "lastPrice")
    ton_usdt = market_value(markets, "TONUSDT", "lastPrice")
    ton_tmn = market_value(markets, "TONTMN", "lastPrice") or market_value(markets, "TONIRT", "lastPrice")
    gram_tmn_estimate = gram_usdt * usdt_tmn if gram_usdt is not None and usdt_tmn is not None else None
    ton_tmn_estimate = ton_usdt * usdt_tmn if ton_usdt is not None and usdt_tmn is not None else None

    print("\n=== SELECTED MARKET PRICES ===")
    for symbol in ("GRAMUSDT", "USDTTMN", "GRAMTMN", "TONUSDT", "TONTMN"):
        rec = market_record(markets, symbol)
        print(f"{symbol}: last={rec['last']}; bid={rec['bid']}; ask={rec['ask']}; buy={rec['buy_status']}; sell={rec['sell_status']}")
    print("GRAM/TMN estimated from GRAMUSDT × USDTTMN:", json_number(gram_tmn_estimate))

    if not public_success and not auth_success:
        print("FAIL: no market endpoint succeeded; previous published JSON is left unchanged.")
        return 1
    if gram_usdt is None or gram_usdt <= 0:
        print("FAIL: GRAMUSDT has no valid last price; previous published JSON is left unchanged.")
        return 1

    generated = utc_now()
    feed = {
        "schema_version": 1,
        "source": "Wallex API",
        "generated_utc": generated,
        "timestamp_utc": generated,
        # Compatibility-friendly top-level field for an existing USD-price parser.
        # Value is GRAMUSDT's latest traded price (USDT, approximately USD), not USD/TMN.
        "price_usd": json_number(gram_usdt),
        "gram": {
            "symbol": "GRAMUSDT",
            "price_usd": json_number(gram_usdt),
            "price_usdt": json_number(gram_usdt),
            "last": json_number(gram_usdt),
            "bid": json_number(market_value(markets, "GRAMUSDT", "bidPrice")),
            "ask": json_number(market_value(markets, "GRAMUSDT", "askPrice")),
        },
        "prices": {
            "gram_usdt_last": json_number(gram_usdt),
            "usdt_tmn_last": json_number(usdt_tmn),
            "gram_tmn_direct_last": json_number(gram_tmn),
            "gram_tmn_estimate": json_number(gram_tmn_estimate),
            "ton_usdt_last": json_number(ton_usdt),
            "ton_tmn_direct_last": json_number(ton_tmn),
            "ton_tmn_estimate": json_number(ton_tmn_estimate),
        },
        "markets": {
            symbol: market_record(markets, symbol)
            for symbol in ("GRAMUSDT", "USDTTMN", "GRAMTMN", "TONUSDT", "TONTMN")
        },
        "checks": {
            "public_endpoint_success": public_success,
            "authenticated_endpoint_success": auth_success,
        },
    }

    temp = OUTPUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(feed, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, OUTPUT)
    print(f"\nSaved validated rate feed: {OUTPUT.name}")
    print("GRAM price_usd:", feed["price_usd"])
    print("generated_utc:", generated)
    print("JSON contains prices and timestamps only; API key is not written to the file.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.")
        sys.exit(130)
