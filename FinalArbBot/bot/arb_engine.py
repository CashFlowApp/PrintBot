"""Arb engine: dynamic edge (best_ask_yes + best_ask_no < 1 - MIN_EDGE_DYNAMIC), depth filter, 0.40 x thinner side sizing."""
import threading
import time
from typing import Callable, Optional

from loguru import logger

from bot.config import (
    DEPTH_PRICE_BAND_PCT,
    LIVE_TRADING,
    MAX_OPEN_MARKETS,
    MAX_POSITION_USD,
    MIN_DEPTH_USD,
    ORDER_FAILURE_COOLDOWN_SECONDS,
    PAPER_MODE,
    SIZING_DEPTH_FRACTION,
)
from bot.fees import FeeCache
from bot.positions import PositionStore
from bot.protection import OneSideProtection


# Best ask and depth per token_id (from market WS)
_book_state: dict[str, dict] = {}  # token_id -> {best_ask, depth_usd within band}
_book_lock = threading.Lock()


def update_book(token_id: str, best_ask: Optional[float], depth_usd: float) -> None:
    with _book_lock:
        if token_id not in _book_state:
            _book_state[token_id] = {}
        _book_state[token_id]["best_ask"] = best_ask
        _book_state[token_id]["depth_usd"] = depth_usd


def get_book(token_id: str) -> tuple[Optional[float], float]:
    with _book_lock:
        s = _book_state.get(token_id, {})
        return s.get("best_ask"), s.get("depth_usd") or 0.0


def _depth_within_band(asks: list[tuple[float, float]], best_ask: float, band_pct: float) -> float:
    """Sum size (in USD) for price <= best_ask * (1 + band_pct)."""
    if not asks or best_ask is None or best_ask <= 0:
        return 0.0
    ceiling = best_ask * (1.0 + band_pct)
    total = 0.0
    for price, size in asks:
        if price <= ceiling:
            total += price * size
    return total


class ArbEngine:
    """
    Trigger when best_ask_yes + best_ask_no < 1.0 - MIN_EDGE_DYNAMIC;
    both sides depth >= MIN_DEPTH_USD; size_usd = min(MAX_POSITION_USD, 0.40 * thinner_side_depth).
    """

    def __init__(
        self,
        fee_cache: FeeCache,
        position_store: PositionStore,
        protection: OneSideProtection,
        place_orders: Callable[[str, str, float, float, float, float], bool],  # market_id, yes_tok, no_tok, price_yes, price_no, size_usd
        on_partial_fill: Callable[[str, str, str, float, float, float, float], None],
    ):
        self._fee_cache = fee_cache
        self._position_store = position_store
        self._protection = protection
        self._place_orders = place_orders
        self._on_partial_fill = on_partial_fill
        self._consecutive_failures = 0
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def on_market_message(self, frame: dict) -> None:
        """Handle book / best_bid_ask from market WS. Extract best_ask and depth, then maybe trigger."""
        event = frame.get("event_type") or frame.get("type")
        if not event:
            return
        # best_bid_ask: fast trigger; book: depth
        asset_id = frame.get("asset_id") or frame.get("token_id")
        if not asset_id:
            return
        best_ask = None
        depth_usd = 0.0
        if event == "best_bid_ask":
            asks = frame.get("ask_price") or frame.get("best_ask")
            if asks is not None:
                best_ask = float(asks) if not isinstance(asks, list) else (float(asks[0]) if asks else None)
            # Some payloads have size at best
            ask_size = frame.get("ask_size") or frame.get("best_ask_size")
            if best_ask is not None and ask_size is not None:
                depth_usd = best_ask * float(ask_size)
        elif event == "book":
            asks = frame.get("asks") or frame.get("ask") or []
            if isinstance(asks, list) and asks:
                normalized = []
                for a in asks:
                    if isinstance(a, (list, tuple)) and len(a) >= 2:
                        normalized.append((float(a[0]), float(a[1])))
                    elif isinstance(a, dict):
                        p, s = a.get("price"), a.get("size")
                        if p is not None and s is not None:
                            normalized.append((float(p), float(s)))
                if normalized:
                    best_ask = normalized[0][0]
                    depth_usd = _depth_within_band(normalized, best_ask, DEPTH_PRICE_BAND_PCT)
        if best_ask is not None:
            update_book(asset_id, best_ask, depth_usd)

    def try_arb(self, market_id: str, yes_token: str, no_token: str) -> None:
        """Check edge + depth + limits; if ok, place equal-size limit buys (or log in paper mode)."""
        with self._lock:
            if time.monotonic() < self._cooldown_until:
                return
            if self._consecutive_failures >= 3:
                self._cooldown_until = time.monotonic() + ORDER_FAILURE_COOLDOWN_SECONDS
                logger.warning("3 consecutive order failures; cooldown {}s", ORDER_FAILURE_COOLDOWN_SECONDS)
                return
        if self._position_store.count_open_markets() >= MAX_OPEN_MARKETS:
            return
        best_yes, depth_yes = get_book(yes_token)
        best_no, depth_no = get_book(no_token)
        if best_yes is None or best_no is None:
            return
        min_edge = self._fee_cache.get_min_edge_dynamic(yes_token, no_token)
        if best_yes + best_no >= 1.0 - min_edge:
            return
        if depth_yes < MIN_DEPTH_USD or depth_no < MIN_DEPTH_USD:
            return
        thinner = min(depth_yes, depth_no)
        size_usd = min(MAX_POSITION_USD, SIZING_DEPTH_FRACTION * thinner)
        avg_price = (best_yes + best_no) / 2.0
        if avg_price <= 0:
            return
        shares = size_usd / avg_price
        if PAPER_MODE:
            logger.info(
                "[PAPER MODE] Would have placed limit buy YES token_id={} price={} size={}",
                yes_token[:8] + "...",
                best_yes,
                shares,
            )
            logger.info(
                "[PAPER MODE] Would have placed limit buy NO token_id={} price={} size={}",
                no_token[:8] + "...",
                best_no,
                shares,
            )
            return
        ok = self._place_orders(market_id, yes_token, no_token, best_yes, best_no, shares, size_usd)
        with self._lock:
            if ok:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1

    def register_partial_fill(self, market_id: str, yes_token: str, no_token: str, yes_filled: float, no_filled: float, price_yes: float, price_no: float) -> None:
        self._protection.register_partial_fill(market_id, yes_filled, no_filled, price_yes, price_no)
        self._on_partial_fill(market_id, yes_token, no_token, yes_filled, no_filled, price_yes, price_no)

    def check_protection(self, market_id: str, yes_token: str, no_token: str) -> None:
        best_yes, _ = get_book(yes_token)
        best_no, _ = get_book(no_token)
        self._protection.check_and_hedge(market_id, yes_token, no_token, best_yes, best_no)
