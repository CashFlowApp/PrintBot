"""Market WebSocket: orderbook, best_bid_ask, last_trade_price. Handles dict or list (batch), PING/PONG."""
import json
import threading
import time
from typing import Callable, List, Optional

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


class PolymarketWSMarket:
    """Market channel WebSocket. Robust: dict or list messages, ignore non-dict, PING/PONG safe."""

    def __init__(
        self,
        token_ids: List[str],
        on_message: Callable[[dict], None],
    ):
        self.token_ids = token_ids
        self.on_message = on_message
        self.ws: Optional[websocket.WebSocketApp] = None
        self._stop = threading.Event()
        self._ping_thread: Optional[threading.Thread] = None

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        # Subscribe with assets_ids for market channel
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
        # Keep-alive / PONG: server may send raw "PONG" or similar; do not parse as JSON
        if isinstance(message, str) and message.strip().upper() in ("PING", "PONG", ""):
            return
        for frame in _normalize_frames(message):
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
