"""One-side protection: 72% threshold + 30s timer + adverse move. Hedge by selling filled side."""
import threading
import time
from typing import Callable, Optional

from loguru import logger

from bot.config import (
    ADVERSE_MOVE_PCT,
    ONE_SIDE_PROTECTION_PCT,
    PAPER_MODE,
    PROTECTION_TIMER_SECONDS,
)


class OneSideProtection:
    """
    If one side fills and the other does not:
    - If filled-side price >= ONE_SIDE_PROTECTION_PCT → hedge immediately (sell filled side).
    - Else after PROTECTION_TIMER_SECONDS → auto-sell.
    - Also sell if unfilled side moves adversely by > ADVERSE_MOVE_PCT.
    """

    def __init__(
        self,
        place_sell_order: Callable[[str, str, float, bool], None],  # token_id, side "YES"|"NO", size, is_market
    ):
        self._place_sell = place_sell_order
        self._lock = threading.Lock()
        self._timers: dict[str, float] = {}  # market_id -> fill time
        self._partial_fills: dict[str, dict] = {}  # market_id -> {yes_filled, no_filled, fill_price_yes, fill_price_no, ...}

    def register_partial_fill(
        self,
        market_id: str,
        yes_filled: float,
        no_filled: float,
        fill_price_yes: float,
        fill_price_no: float,
    ) -> None:
        with self._lock:
            self._partial_fills[market_id] = {
                "yes_filled": yes_filled,
                "no_filled": no_filled,
                "fill_price_yes": fill_price_yes,
                "fill_price_no": fill_price_no,
                "start_time": time.monotonic(),
            }
            if market_id not in self._timers:
                self._timers[market_id] = time.monotonic()

    def check_and_hedge(
        self,
        market_id: str,
        yes_token: str,
        no_token: str,
        current_ask_yes: Optional[float],
        current_ask_no: Optional[float],
    ) -> bool:
        """Returns True if a hedge was triggered (or would have been in paper mode)."""
        with self._lock:
            pf = self._partial_fills.get(market_id)
            if not pf:
                return False
            yes_f, no_f = pf["yes_filled"], pf["no_filled"]
            price_yes, price_no = pf["fill_price_yes"], pf["fill_price_no"]
            start = pf["start_time"]

        # Both filled → no one-side risk
        if yes_f > 0 and no_f > 0:
            with self._lock:
                self._partial_fills.pop(market_id, None)
                self._timers.pop(market_id, None)
            return False

        # One side filled
        filled_side = "YES" if yes_f > 0 else "NO"
        filled_price = price_yes if yes_f > 0 else price_no
        filled_size = yes_f if yes_f > 0 else no_f
        token_id = yes_token if yes_f > 0 else no_token

        # 1) Filled side >= 72% → hedge immediately
        if filled_price >= ONE_SIDE_PROTECTION_PCT:
            logger.info("One-side protection: filled price {:.2%} >= {}% → hedge", filled_price, ONE_SIDE_PROTECTION_PCT * 100)
            self._do_hedge(market_id, token_id, filled_side, filled_size)
            with self._lock:
                self._partial_fills.pop(market_id, None)
                self._timers.pop(market_id, None)
            return True

        # 2) Timer elapsed
        if (time.monotonic() - start) >= PROTECTION_TIMER_SECONDS:
            logger.info("One-side protection: timer {}s elapsed → hedge", PROTECTION_TIMER_SECONDS)
            self._do_hedge(market_id, token_id, filled_side, filled_size)
            with self._lock:
                self._partial_fills.pop(market_id, None)
                self._timers.pop(market_id, None)
            return True

        # 3) Adverse move on unfilled side (>3%)
        other_ask = current_ask_no if yes_f > 0 else current_ask_yes
        if other_ask is not None:
            other_price_at_fill = price_no if yes_f > 0 else price_yes
            if other_price_at_fill > 0 and (other_ask - other_price_at_fill) / other_price_at_fill >= ADVERSE_MOVE_PCT:
                logger.info("One-side protection: adverse move on unfilled side → hedge")
                self._do_hedge(market_id, token_id, filled_side, filled_size)
                with self._lock:
                    self._partial_fills.pop(market_id, None)
                    self._timers.pop(market_id, None)
                return True

        return False

    def _do_hedge(self, market_id: str, token_id: str, side: str, size: float) -> None:
        if PAPER_MODE:
            logger.info("[PAPER MODE] Would have placed hedge sell order: token_id={} side={} size={}", token_id[:8] + "...", side, size)
            return
        self._place_sell(token_id, side, size, is_market=True)

    def clear_market(self, market_id: str) -> None:
        with self._lock:
            self._partial_fills.pop(market_id, None)
            self._timers.pop(market_id, None)
