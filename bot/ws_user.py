"""User WebSocket: broad user updates (empty markets), retry with backoff, redacted auth log."""
import json
import threading
import time
from typing import Callable, List, Optional

import websocket
from loguru import logger

from bot.config import get_creds

WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
MAX_RETRIES = 5
BACKOFF_SECONDS = [2, 5, 10, 10, 10]  # wait before retry 1..5
FAIL_THRESHOLD_LOG = 3  # log "continuing without fill tracking" after this many failures


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


def _redacted_auth(auth: dict) -> str:
    """Return auth payload with values redacted for logging."""
    return "apiKey=***, secret=***, passphrase=***" if auth else "none"


class PolymarketWSUser:
    """User channel: subscribe with empty markets (broad updates). Retry with exponential backoff on close."""

    def __init__(
        self,
        token_ids: List[str],
        on_message: Callable[[dict], None],
    ):
        self.token_ids = token_ids  # kept for API; we always subscribe with []
        self.on_message = on_message
        self.creds = get_creds()
        self.ws: Optional[websocket.WebSocketApp] = None
        self._stop = threading.Event()
        self._ping_thread: Optional[threading.Thread] = None
        self._gave_up = False  # True after max retries exhausted

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        auth = {
            "apiKey": self.creds["key"],
            "secret": self.creds["secret"],
            "passphrase": self.creds["passphrase"],
        }
        # Broad user updates (empty markets list is more reliable)
        payload = {"type": "user", "markets": [], "auth": auth}
        logger.debug("User WS subscribe (redacted): type=user, markets=[], auth=({})", _redacted_auth(auth))
        ws.send(json.dumps(payload))
        self._stop.clear()
        self._ping_thread = threading.Thread(target=self._ping_loop, args=(ws,), daemon=True)
        self._ping_thread.start()
        logger.info("User WS connected, subscribed for broad user updates (markets=[])")

    def _ping_loop(self, ws: websocket.WebSocketApp) -> None:
        while not self._stop.is_set():
            try:
                ws.send("PING")
            except Exception as e:
                logger.debug("User WS ping error: {}", e)
                return
            self._stop.wait(timeout=10)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        if isinstance(message, str) and message.strip().upper() in ("PING", "PONG", ""):
            return
        for frame in _normalize_frames(message):
            event = frame.get("event_type") or frame.get("type")
            if event:
                logger.debug("User WS: {}", event)
            self.on_message(frame)

    def _on_error(self, ws: websocket.WebSocketApp, err: Exception) -> None:
        logger.error("User WS error: {}", err)

    def _on_close(self, ws: websocket.WebSocketApp, *args) -> None:
        self._stop.set()
        logger.warning("User WS closed")

    def run(self) -> None:
        """Single run: connect and run_forever until closed."""
        self.ws = websocket.WebSocketApp(
            WS_USER_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever(ping_interval=30, ping_timeout=10)

    def run_with_retries(self) -> None:
        """Run with retries: on close, wait backoff and reconnect up to MAX_RETRIES."""
        for attempt in range(MAX_RETRIES):
            if self._stop.is_set():
                break
            self.run()
            # run_forever returned = connection closed
            if attempt >= FAIL_THRESHOLD_LOG - 1 and attempt == FAIL_THRESHOLD_LOG - 1:
                logger.warning("User WS failed to stay open — continuing without fill tracking")
            if attempt < MAX_RETRIES - 1:
                delay = BACKOFF_SECONDS[attempt] if attempt < len(BACKOFF_SECONDS) else 10
                logger.info("User WS retry in {}s (attempt {}/{})", delay, attempt + 2, MAX_RETRIES)
                self._stop.clear()
                self._stop.wait(timeout=delay)
        self._gave_up = True

    def run_forever_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_with_retries, daemon=True)
        t.start()
        return t
