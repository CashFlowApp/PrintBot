"""One-side protection: 72% upside spike, 10s timer, 2% adverse move stop-loss. Market-sell filled side on first trigger."""
import threading
import time
from typing import Callable, Optional

from loguru import logger

from bot.alerts import alert_protection
from bot.config import (
    ADVERSE_MOVE_PCT,
    ONE_SIDE_PROTECTION_PCT,
    PAPER_MODE,
    PROTECTION_TIMER_SECONDS,
)


class OneSideProtection:
    """
    If one side fills and the other does not, sell the filled side on the FIRST of:
    - Upside spike: filled side's price >= 72% → lock profit immediately.
    - Adverse move: filled side's current best ask drops >2% from fill price → stop-loss.
    - Timer: 10 seconds since fill → market-sell regardless of price.
    """

    def __init__(
        self,
        place_sell_order: Callable[[str, str, float, bool], None],  # token_id, side "YES"|"NO", size, is_market
    ):
        self._place_sell = place_sell_order
        self._lock = threading.Lock()
        self._partial_fills: dict[str, dict] = {}  # market_id -> {yes_filled, no_filled, fill_price_yes, fill_price_no, start_time}

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

    def check_and_hedge(
        self,
        market_id: str,
        yes_token: str,
        no_token: str,
        current_ask_yes: Optional[float],
        current_ask_no: Optional[float],
    ) -> bool:
        """Returns True if a hedge was triggered (or would have been in paper mode). First trigger wins."""
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
            return False

        # One side filled
        filled_side = "YES" if yes_f > 0 else "NO"
        filled_price = price_yes if yes_f > 0 else price_no
        filled_size = yes_f if yes_f > 0 else no_f
        token_id = yes_token if yes_f > 0 else no_token
        # Current best ask for the filled token (used for adverse check and exit price display)
        filled_side_ask = current_ask_yes if yes_f > 0 else current_ask_no
        exit_price = filled_side_ask if filled_side_ask is not None else filled_price

        # 1) Upside spike: filled side >= 72% → sell to lock profit
        if filled_price >= ONE_SIDE_PROTECTION_PCT:
            msg = f"Sold filled {filled_side} at {exit_price:.2f} after 72% upside spike"
            self._do_hedge(market_id, token_id, filled_side, filled_size, exit_price, msg)
            with self._lock:
                self._partial_fills.pop(market_id, None)
            return True

        # 2) Adverse move: filled side's best ask dropped >2% from fill price → stop-loss
        if filled_side_ask is not None and filled_price > 0:
            if filled_side_ask <= filled_price * (1.0 - ADVERSE_MOVE_PCT):
                msg = (
                    f"Sold filled {filled_side} at {exit_price:.2f} after adverse move "
                    f"(>2% drop from fill {filled_price:.2f})"
                )
                self._do_hedge(market_id, token_id, filled_side, filled_size, exit_price, msg)
                with self._lock:
                    self._partial_fills.pop(market_id, None)
                return True

        # 3) Timer: 10s elapsed → market-sell regardless of price
        if (time.monotonic() - start) >= PROTECTION_TIMER_SECONDS:
            msg = f"Sold filled {filled_side} at {exit_price:.2f} after {PROTECTION_TIMER_SECONDS}s timer"
            self._do_hedge(market_id, token_id, filled_side, filled_size, exit_price, msg)
            with self._lock:
                self._partial_fills.pop(market_id, None)
            return True

        return False

    def _do_hedge(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size: float,
        exit_price: float,
        trigger_msg: str,
    ) -> None:
        if PAPER_MODE:
            log_msg = f"[PAPER MODE] Would have sold filled side at {exit_price:.2f} after trigger: {trigger_msg}"
            logger.info(log_msg)
            alert_protection(log_msg)
            return
        logger.info("[PROTECTION] {}", trigger_msg)
        alert_protection(trigger_msg)
        self._place_sell(token_id, side, size, is_market=True)

    def clear_market(self, market_id: str) -> None:
        with self._lock:
            self._partial_fills.pop(market_id, None)
