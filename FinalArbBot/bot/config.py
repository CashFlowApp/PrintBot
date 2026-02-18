"""Load and validate configuration from environment. No secrets logged."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (parent of bot/)
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env")


def _bool(s: str) -> bool:
    if s is None:
        return False
    return str(s).strip().lower() in ("true", "1", "yes", "on")


def _float(s: str, default: float) -> float:
    if s is None or str(s).strip() == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _int(s: str, default: int) -> int:
    if s is None or str(s).strip() == "":
        return default
    try:
        return int(s)
    except ValueError:
        return default


# API (from .env only; never hardcoded or logged)
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
API_PASSPHRASE = os.getenv("API_PASSPHRASE", "")
HOST = os.getenv("HOST", "https://clob.polymarket.com").rstrip("/")
CHAIN_ID = _int(os.getenv("CHAIN_ID"), 137)
PROXY_WALLET = os.getenv("PROXY_WALLET", "")
# Optional: for live order placement (py-clob-client uses key + funder)
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

# Mode: safe defaults
PAPER_MODE = _bool(os.getenv("PAPER_MODE", "true"))
LIVE_TRADING = _bool(os.getenv("LIVE_TRADING", "false"))

# Tunable parameters
MIN_BASE_EDGE = _float(os.getenv("MIN_BASE_EDGE"), 0.025)
MAX_POSITION_USD = _float(os.getenv("MAX_POSITION_USD"), 400)
MIN_DEPTH_USD = _float(os.getenv("MIN_DEPTH_USD"), 300)
ONE_SIDE_PROTECTION_PCT = _float(os.getenv("ONE_SIDE_PROTECTION_PCT"), 0.72)
PROTECTION_TIMER_SECONDS = _int(os.getenv("PROTECTION_TIMER_SECONDS"), 30)
DAILY_LOSS_LIMIT_USD = _float(os.getenv("DAILY_LOSS_LIMIT_USD"), 1200)
MAX_OPEN_MARKETS = _int(os.getenv("MAX_OPEN_MARKETS"), 15)

# Depth filter: within this fraction of best ask to count depth
DEPTH_PRICE_BAND_PCT = 0.005  # 0.5%

# Sizing: fraction of thinner side depth to use
SIZING_DEPTH_FRACTION = 0.40

# One-side: adverse move threshold to force hedge (e.g. 3%)
ADVERSE_MOVE_PCT = 0.03

# Risk
ORDER_FAILURE_COOLDOWN_SECONDS = 300  # 5 min after 3 consecutive failures
CIRCUIT_BREAKER_DRAWDOWN_PCT = 0.08  # 8% drawdown in 1h → pause
AUTO_CANCEL_SECONDS_BEFORE_EXPIRY = 90
SETTLEMENT_CHECK_INTERVAL_SECONDS = 60

# Discovery
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
GAMMA_POLL_INTERVAL_SECONDS = 20

# Alerts
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Runtime: KILL file in project root stops bot cleanly
KILL_FILE_PATH = _root / "KILL"


def get_creds() -> dict:
    """Return credentials dict for WebSocket auth. Do not log this."""
    return {
        "key": API_KEY,
        "secret": API_SECRET,
        "passphrase": API_PASSPHRASE,
    }


def is_kill_switch_active() -> bool:
    return KILL_FILE_PATH.exists()
