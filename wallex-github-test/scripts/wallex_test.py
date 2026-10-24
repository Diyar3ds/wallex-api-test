#!/usr/bin/env python3
"""Read-only Wallex API smoke test for GitHub Actions. Never submits trades."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any

BASE_URL = "https://api.wallex.ir"
TIMEOUT = 20


def api_get(path: str, api_key: str | None = None) -> tuple[int | None, Any, str | None]:
    headers = {"Accept": "application/json", "User-Agent": "wallex-readonly-github-smoke-test/1.0"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body), None
            except json.JSONDecodeError:
                return response.status, None, f"Response was not valid JSON: {body[:200]}"
    except urllib.error.HTTPError as exc:
        # Do not print headers or secrets; only a short response body is shown.
        body = exc.read().decode("utf-8", "replace")[:300]
        return exc.code, None, body
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
        out: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                symbol = str(value.get("symbol") or key).upper()
                out[symbol] = value
        return out
    if isinstance(raw, list):
        out = {}
        for value in raw:
            if isinstance(value, dict):
                symbol = str(value.get("symbol") or "").upper()
                if symbol:
                    out[symbol] = value
        return out
    # Some Wallex OTC responses put markets directly under result, e.g.
    # result: {"USDTTMN": {...}, "BTCUSDT": {...}}.
    out = {}
    for key, value in result.items():
        if isinstance(value, dict) and (
            value.get("symbol") or value.get("baseAsset") or value.get("stats")
        ):
            symbol = str(value.get("symbol") or key).upper()
            out[symbol] = value
    return out


def market_last(market: dict[str, Any] | None) -> Decimal | None:
    if not market:
        return None
    stats = market.get("stats")
    value = stats.get("lastPrice") if isinstance(stats, dict) else None
    if value is None:
        value = market.get("lastPrice")
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None


def printable(value: Decimal | None) -> str:
    return format(value, "f") if value is not None else "NOT_FOUND"


def main() -> int:
    print("=== WALLEX READ-ONLY API TEST ===")
    print("UTC started:", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    print("No order/trade endpoints are called.")

    public_paths = ("/v1/markets", "/hector/web/v1/markets")
    public_markets: dict[str, dict[str, Any]] = {}
    public_ok = False
    for path in public_paths:
        code, payload, error = api_get(path)
        found = extract_markets(payload)
        ok = code == 200 and isinstance(payload, dict) and payload.get("success") is True
        print(f"\nPUBLIC {path}: HTTP={code}; success={ok}; markets={len(found)}")
        if error:
            print("Public detail:", error)
        elif isinstance(payload, dict) and payload.get("message"):
            print("Public message:", payload.get("message"))
        # Merge public endpoints while preserving stats returned by either API path.
        for symbol, market in found.items():
            previous = public_markets.get(symbol, {})
            merged = dict(previous)
            merged.update(market)
            previous_stats = previous.get("stats") if isinstance(previous.get("stats"), dict) else {}
            new_stats = market.get("stats") if isinstance(market.get("stats"), dict) else {}
            if previous_stats or new_stats:
                merged["stats"] = {**previous_stats, **new_stats}
            public_markets[symbol] = merged
        public_ok = public_ok or ok

    api_key = os.environ.get("WALLEX_API_KEY", "").strip()
    auth_code: int | None = None
    auth_payload: Any = None
    auth_markets: dict[str, dict[str, Any]] = {}
    auth_ok = False
    if api_key:
        auth_code, auth_payload, auth_error = api_get("/v1/otc/markets", api_key)
        auth_markets = extract_markets(auth_payload)
        auth_ok = auth_code == 200 and isinstance(auth_payload, dict) and auth_payload.get("success") is True
        print(f"AUTH /v1/otc/markets: HTTP={auth_code}; success={auth_ok}; markets={len(auth_markets)}")
        if auth_error:
            print("Authenticated detail:", auth_error)
        elif isinstance(auth_payload, dict) and auth_payload.get("message"):
            print("Authenticated message:", auth_payload.get("message"))
    else:
        print("AUTH /v1/otc/markets: SKIPPED (GitHub Actions secret WALLEX_API_KEY is missing)")

    # Prefer public pricing where available while preserving OTC market statuses.
    markets = dict(auth_markets)
    for symbol, market in public_markets.items():
        previous = markets.get(symbol, {})
        merged = dict(previous)
        merged.update(market)
        previous_stats = previous.get("stats") if isinstance(previous.get("stats"), dict) else {}
        new_stats = market.get("stats") if isinstance(market.get("stats"), dict) else {}
        if previous_stats or new_stats:
            merged["stats"] = {**previous_stats, **new_stats}
        markets[symbol] = merged

    print("\n=== DISCOVERED RELEVANT MARKETS ===")
    relevant = []
    for symbol, market in markets.items():
        base = str(market.get("baseAsset") or "").upper()
        quote = str(market.get("quoteAsset") or "").upper()
        if (base in {"USDT", "TON", "GRAM"} and quote in {"TMN", "IRT", "USDT"}) or any(x in symbol for x in ("USDT", "TON", "GRAM")):
            relevant.append((symbol, market))
    for symbol, market in sorted(relevant):
        stats = market.get("stats") if isinstance(market.get("stats"), dict) else {}
        print(
            f"{symbol} | {market.get('faName') or market.get('enName') or '-'} | "
            f"last={stats.get('lastPrice', market.get('lastPrice', '-'))} | "
            f"bid={stats.get('bidPrice', '-')} | ask={stats.get('askPrice', '-')} | "
            f"buy={market.get('buyStatus', '-')} | sell={market.get('sellStatus', '-')}"
        )

    print("\n=== PRICE SUMMARY ===")
    usdt_tmn = market_last(markets.get("USDTTMN")) or market_last(markets.get("USDTIRT"))
    ton_tmn = market_last(markets.get("TONTMN")) or market_last(markets.get("TONIRT"))
    gram_tmn = market_last(markets.get("GRAMTMN")) or market_last(markets.get("GRAMIRT"))
    print("USDT/TMN (market last price):", printable(usdt_tmn))
    print("TON/TMN direct market:", printable(ton_tmn))
    print("GRAM/TMN direct market:", printable(gram_tmn))

    if ton_tmn is None:
        ton_usdt = market_last(markets.get("TONUSDT"))
        if ton_usdt is not None and usdt_tmn is not None:
            ton_tmn = ton_usdt * usdt_tmn
            print("TON/TMN estimate (TONUSDT last × USDTTMN last):", printable(ton_tmn))
        else:
            print("TON/TMN estimate: NOT_AVAILABLE (missing pair(s))")
    if gram_tmn is None:
        gram_usdt = market_last(markets.get("GRAMUSDT"))
        if gram_usdt is not None and usdt_tmn is not None:
            gram_tmn = gram_usdt * usdt_tmn
            print("GRAM/TMN estimate (GRAMUSDT last × USDTTMN last):", printable(gram_tmn))
        else:
            print("GRAM/TMN estimate: NOT_AVAILABLE (missing pair(s))")

    print("\n=== RESULT ===")
    if not public_ok and not auth_ok:
        print("FAIL: neither market endpoint returned a successful JSON response.")
        return 1
    if not api_key:
        print("PARTIAL: public API tested; authenticated OTC test needs WALLEX_API_KEY secret.")
        return 0 if public_ok else 1
    if public_ok and auth_ok:
        print("PASS: both public and authenticated market endpoints responded successfully.")
        return 0
    print("PARTIAL: at least one endpoint worked; inspect HTTP codes and details above.")
    return 0 if public_ok or auth_ok else 1


if __name__ == "__main__":
    sys.exit(main())
