"""Fee-rate queries and dynamic edge buffer. MIN_EDGE_DYNAMIC = MIN_BASE_EDGE + fee_buffer + safety_buffer."""
import threading
from typing import Optional

import requests
from loguru import logger

from bot.config import HOST, MIN_BASE_EDGE

# Conservative safety buffer on top of fee (0.5%)
SAFETY_BUFFER = 0.005


def fetch_fee_rate(token_id: str) -> float:
    """GET /fee-rate?token_id=XXX. Returns fee rate as decimal (e.g. 0.001)."""
    url = f"{HOST}/fee-rate"
    try:
        resp = requests.get(url, params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Handle both {"feeRate": 0.001} and direct number
        if isinstance(data, (int, float)):
            return float(data)
        rate = data.get("feeRate") or data.get("fee_rate") or 0.0
        return float(rate)
    except Exception as e:
        logger.warning("Fee rate fetch failed for token: {}", e)
        return 0.0


def compute_min_edge_dynamic(
    fee_rate_yes: float,
    fee_rate_no: float,
    min_base_edge: float = MIN_BASE_EDGE,
    safety_buffer: float = SAFETY_BUFFER,
) -> float:
    """MIN_EDGE_DYNAMIC = MIN_BASE_EDGE + fee_buffer + safety_buffer. Use max of both sides for buffer."""
    fee_buffer = max(fee_rate_yes, fee_rate_no)
    return min_base_edge + fee_buffer + safety_buffer


class FeeCache:
    """Cache fee rates per token and expose get_min_edge_dynamic(yes_token, no_token)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rates: dict[str, float] = {}

    def get_rate(self, token_id: str) -> float:
        with self._lock:
            if token_id in self._rates:
                return self._rates[token_id]
        rate = fetch_fee_rate(token_id)
        with self._lock:
            self._rates[token_id] = rate
        return rate

    def get_min_edge_dynamic(self, yes_token: str, no_token: str) -> float:
        r_yes = self.get_rate(yes_token)
        r_no = self.get_rate(no_token)
        return compute_min_edge_dynamic(r_yes, r_no, MIN_BASE_EDGE, SAFETY_BUFFER)

    def refresh(self, yes_token: str, no_token: str) -> float:
        """Fetch fresh rates for both tokens and return MIN_EDGE_DYNAMIC."""
        with self._lock:
            self._rates.pop(yes_token, None)
            self._rates.pop(no_token, None)
        return self.get_min_edge_dynamic(yes_token, no_token)
