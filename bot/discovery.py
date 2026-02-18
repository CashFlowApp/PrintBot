"""Short-term BTC/ETH/SOL 5/15-min market discovery: manual override + Gamma (tag=crypto, newest first)."""
import json
import threading
from typing import Callable, Optional

import requests
from loguru import logger

from bot.config import (
    GAMMA_MARKETS_URL,
    GAMMA_POLL_INTERVAL_SECONDS,
    SHORT_TERM_TOKEN_PAIRS,
)

# Gamma: tag "cryptocurrency" for crypto markets; fetch newest first
GAMMA_TAG_CRYPTO = "744"
# Cache: market_id -> {market_id, yes_token, no_token, end_time, ...}
_token_pair_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _one_token_id(x) -> str:
    """Normalize to a single token ID string."""
    if x is None:
        return ""
    if isinstance(x, list):
        return str(x[0]) if x else ""
    s = str(x).strip()
    if s.startswith("[") and "]" in s:
        try:
            parsed = json.loads(s)
            return str(parsed[0]) if isinstance(parsed, list) and parsed else s
        except (json.JSONDecodeError, TypeError):
            pass
    return s


def _clob_token_ids(market: dict) -> list[str]:
    raw = market.get("clobTokenIds")
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for x in raw:
            t = _one_token_id(x)
            if t:
                out.append(t)
        return out
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [_one_token_id(x) for x in parsed if _one_token_id(x)]
        except (json.JSONDecodeError, TypeError):
            pass
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def _normalize_manual_pair(p: dict) -> dict:
    """Ensure market_id, yes_token, no_token present."""
    return {
        "market_id": str(p.get("market_id", "")),
        "yes_token": str(p.get("yes_token", "")),
        "no_token": str(p.get("no_token", "")),
        "end_time": str(p.get("end_time", "")),
        "question": str(p.get("question", "")),
    }


def _fetch_gamma_newest(limit: int = 100, tag_id: Optional[str] = None) -> list[dict]:
    """One-time Gamma call: newest markets, optionally by tag. No pagination."""
    params = {"closed": "false", "limit": limit, "ascending": "false", "order": "id"}
    if tag_id:
        params["tag_id"] = tag_id
    try:
        resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Gamma fetch failed: {}", e)
        return []


def _filter_short_term(market: dict) -> bool:
    """Keep only short-term crypto YES/NO: 'up or down' in question, enableOrderBook, 2 clobTokenIds."""
    q = (market.get("question") or "").lower()
    if not q and market.get("conditions"):
        q = ((market["conditions"][0].get("question")) or "").lower()
    if "up or down" not in q:
        return False
    if market.get("enableOrderBook") is not True:
        return False
    ids = _clob_token_ids(market)
    if len(ids) != 2:
        return False
    return True


def discover_eligible() -> list[dict]:
    """
    Discover short-term crypto pairs: manual SHORT_TERM_TOKEN_PAIRS first, then Gamma (tag=crypto, newest).
    Cache result in _token_pair_cache.
    """
    # 1) Manual override from config (user can paste from polymarket.com/crypto)
    manual = [p for p in SHORT_TERM_TOKEN_PAIRS if p.get("market_id") and p.get("yes_token") and p.get("no_token")]
    if manual:
        out = [_normalize_manual_pair(p) for p in manual]
        with _cache_lock:
            _token_pair_cache.clear()
            for m in out:
                _token_pair_cache[m["market_id"]] = m
        logger.info("Discovery: using {} manual short-term token pairs", len(out))
        return out

    # 2) Gamma: tag=crypto, newest first
    markets = _fetch_gamma_newest(limit=100, tag_id=GAMMA_TAG_CRYPTO)
    logger.info("Gamma (tag=crypto): {} markets returned", len(markets))

    # 3) Fallback: no tag, still newest first
    if not markets:
        markets = _fetch_gamma_newest(limit=100, tag_id=None)
        logger.info("Gamma (no tag): {} markets returned", len(markets))

    eligible = []
    for m in markets:
        if not _filter_short_term(m):
            continue
        ids = _clob_token_ids(m)
        end_time = (
            m.get("endDate")
            or m.get("end_date_iso")
            or (m.get("conditions", [{}]) and m["conditions"][0].get("endDate"))
            or ""
        )
        yt = ids[0] if ids else ""
        nt = ids[1] if len(ids) > 1 else ""
        if isinstance(yt, list) and yt:
            yt = str(yt[0])
        else:
            yt = str(yt) if yt else ""
        if isinstance(nt, list) and nt:
            nt = str(nt[0])
        else:
            nt = str(nt) if nt else ""
        rec = {
            "market_id": m.get("id") or m.get("conditionId") or "",
            "yes_token": yt,
            "no_token": nt,
            "end_time": end_time,
            "question": (m.get("question") or "").lower(),
        }
        eligible.append(rec)

    with _cache_lock:
        _token_pair_cache.clear()
        for m in eligible:
            _token_pair_cache[m["market_id"]] = m

    logger.info("Discovery: {} eligible short-term pairs (up or down + orderbook)", len(eligible))
    return eligible


def get_cached_pairs() -> dict[str, dict]:
    """Return current cache of market_id -> pair dict (thread-safe copy)."""
    with _cache_lock:
        return dict(_token_pair_cache)


class DiscoveryLoop:
    """Background loop: poll discover_eligible() every N seconds and call on_update(eligible_list)."""

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
