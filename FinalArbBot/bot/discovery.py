"""Gamma API market discovery: 5/15 min BTC/ETH/SOL, enableOrderBook, exactly 2 clobTokenIds."""
import threading
import time
from typing import Callable, Optional

import requests
from loguru import logger

from bot.config import GAMMA_MARKETS_URL, GAMMA_POLL_INTERVAL_SECONDS

# Filter: 5 or 15 min in question, btc/eth/sol in question, enableOrderBook True, exactly 2 clobTokenIds
SHORT_TERM_KEYWORDS = ("5 min", "15 min")
CRYPTO_KEYWORDS = ("btc", "eth", "sol")


def _question(market: dict) -> str:
    q = market.get("question") or market.get("conditions", [{}])[0].get("question") or ""
    return (q or "").lower()


def _clob_token_ids(market: dict) -> list[str]:
    raw = market.get("clobTokenIds") or ""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def filter_market(market: dict) -> bool:
    q = _question(market)
    if not any(kw in q for kw in SHORT_TERM_KEYWORDS):
        return False
    if not any(c in q for c in CRYPTO_KEYWORDS):
        return False
    if market.get("enableOrderBook") is not True:
        return False
    ids = _clob_token_ids(market)
    if len(ids) != 2:
        return False
    return True


def fetch_markets(limit: int = 200, offset: int = 0, closed: bool = False) -> list[dict]:
    params = {"limit": limit, "offset": offset, "closed": str(closed).lower()}
    resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    return data


def discover_eligible() -> list[dict]:
    """Fetch from Gamma and return list of eligible markets (filter applied)."""
    all_markets = fetch_markets(limit=200, offset=0, closed=False)
    eligible = []
    for m in all_markets:
        if filter_market(m):
            ids = _clob_token_ids(m)
            end_time = (
                m.get("endDate")
                or m.get("end_date_iso")
                or (m.get("conditions", [{}]) and m["conditions"][0].get("endDate"))
                or ""
            )
            eligible.append({
                "market_id": m.get("id") or m.get("conditionId") or "",
                "yes_token": ids[0] if ids else "",
                "no_token": ids[1] if len(ids) > 1 else "",
                "end_time": end_time,
                "fee_enabled": m.get("feeEnabled", True),
                "question": _question(m) or (m.get("question") or ""),
                "resolved": m.get("resolved", False),
                "raw": m,
            })
    return eligible


class DiscoveryLoop:
    """Background loop that polls Gamma every N seconds and calls on_update(eligible_list)."""

    def __init__(self, on_update: Callable[[list[dict]], None], interval_sec: float = GAMMA_POLL_INTERVAL_SECONDS):
        self._on_update = on_update
        self._interval = interval_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_markets: list[dict] = []

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._last_markets = discover_eligible()
                self._on_update(self._last_markets)
            except Exception as e:
                logger.exception("Discovery poll failed: {}", e)
            self._stop.wait(timeout=self._interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Discovery loop started (interval={}s)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None

    def get_last_markets(self) -> list[dict]:
        return list(self._last_markets)
