"""Market WebSocket: subscribe to discovered YES/NO token IDs (assets_ids), maintain best_ask per token."""
import json
import threading
from typing import Callable, Dict, List, Optional

import websocket
from loguru import logger

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _normalize_frames(message: str):
    """Parse message; yield each frame as dict. Incoming can be a single dict or a list (batch)."""
    try:
        data = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return
    if isinstance(data, dict):
        yield data
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
        return


def _extract_best_ask(frame: dict) -> Optional[float]:
    """Get best ask price from best_bid_ask or book frame."""
    event = frame.get("event_type") or frame.get("type")
    if event == "best_bid_ask":
        ask = frame.get("ask_price") or frame.get("best_ask")
        if ask is not None:
            return float(ask) if not isinstance(ask, list) else (float(ask[0]) if ask else None)
    elif event == "book":
        asks = frame.get("asks") or frame.get("ask") or []
        if isinstance(asks, list) and asks:
            a = asks[0]
            if isinstance(a, (list, tuple)) and len(a) >= 1:
                return float(a[0])
            if isinstance(a, dict) and a.get("price") is not None:
                return float(a["price"])
    return None


class PolymarketWSMarket:
    """Market channel: subscribe only to assets_ids (discovered YES/NO token IDs). Maintains best_ask_by_token."""

    def __init__(
        self,
        token_ids: List[str],
        on_message: Callable[[dict], None],
    ):
        self.token_ids = list(token_ids) if token_ids else []
        self.on_message = on_message
        self.ws: Optional[websocket.WebSocketApp] = None
        self._stop = threading.Event()
        self._ping_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Current best ask per token_id (from market WS updates)
        self.best_ask_by_token: Dict[str, float] = {}

    def get_best_ask(self, token_id: str) -> Optional[float]:
        """Return current best ask for token_id, or None."""
        with self._lock:
            return self.best_ask_by_token.get(token_id)

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        # Subscribe with assets_ids (discovered YES/NO token IDs only)
        payload = {"assets_ids": self.token_ids, "type": "market"}
        ws.send(json.dumps(payload))
        self._stop.clear()
        self._ping_thread = threading.Thread(target=self._ping_loop, args=(ws,), daemon=True)
        self._ping_thread.start()
        logger.info("Market WS connected, subscribed to {} tokens", len(self.token_ids))

    def _ping_loop(self, ws: websocket.WebSocketApp) -> None:
        while not self._stop.is_set():
            try:
                ws.send("PING")
            except Exception as e:
                logger.debug("Market WS ping error: {}", e)
                return
            self._stop.wait(timeout=10)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        if isinstance(message, str) and message.strip().upper() in ("PING", "PONG", ""):
            return
        for frame in _normalize_frames(message):
            asset_id = frame.get("asset_id") or frame.get("token_id")
            best_ask = _extract_best_ask(frame)
            if asset_id is not None and best_ask is not None:
                with self._lock:
                    self.best_ask_by_token[asset_id] = best_ask
            event = frame.get("event_type") or frame.get("type")
            if event:
                logger.debug("Market WS: {}", event)
            self.on_message(frame)

    def _on_error(self, ws: websocket.WebSocketApp, err: Exception) -> None:
        logger.error("Market WS error: {}", err)

    def _on_close(self, ws: websocket.WebSocketApp, *args) -> None:
        self._stop.set()
        logger.warning("Market WS closed")

    def run(self) -> None:
        self.ws = websocket.WebSocketApp(
            WS_MARKET_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever(ping_interval=30, ping_timeout=10)

    def run_forever_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t
