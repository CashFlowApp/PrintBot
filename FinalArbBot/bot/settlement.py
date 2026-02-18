"""Auto-cancel 90s before end_time; every 60s check resolved via Gamma then redeem via relayer."""
import threading
import time
from typing import Callable, Optional

from loguru import logger

from bot.config import AUTO_CANCEL_SECONDS_BEFORE_EXPIRY, SETTLEMENT_CHECK_INTERVAL_SECONDS
from bot.discovery import discover_eligible


def _parse_end_time(s: str) -> Optional[float]:
    if not s:
        return None
    try:
        from datetime import datetime
        s = str(s).strip().replace("Z", "+00:00")
        if "+" not in s and len(s) >= 19:
            s = s + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def get_market_end_time(market_id: str) -> Optional[float]:
    """Return end_time as Unix timestamp for market_id, or None."""
    for m in discover_eligible():
        if m.get("market_id") == market_id:
            return _parse_end_time(m.get("end_time") or "")
    return None


class SettlementLoop:
    """Every 60s: check resolved; 90s before expiry: cancel open orders for that market."""

    def __init__(
        self,
        get_open_markets: Callable[[], list[str]],
        cancel_orders_for_market: Callable[[str], None],
        check_resolved_and_redeem: Callable[[], None],
    ):
        self._get_open = get_open_markets
        self._cancel_for_market = cancel_orders_for_market
        self._check_redeem = check_resolved_and_redeem
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = time.time()
                for market_id in self._get_open():
                    end_ts = get_market_end_time(market_id)
                    if end_ts and (end_ts - now) <= AUTO_CANCEL_SECONDS_BEFORE_EXPIRY:
                        logger.info("Auto-cancel orders for market {} (90s before expiry)", market_id[:12] + "...")
                        self._cancel_for_market(market_id)
                self._check_redeem()
            except Exception as e:
                logger.exception("Settlement loop: {}", e)
            self._stop.wait(timeout=SETTLEMENT_CHECK_INTERVAL_SECONDS)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Settlement loop started (interval={}s)", SETTLEMENT_CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=SETTLEMENT_CHECK_INTERVAL_SECONDS * 2)
            self._thread = None
