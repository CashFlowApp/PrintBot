"""Telegram alerts and daily P&L report. No secrets logged."""
import threading
from typing import Any, Optional

from loguru import logger

from bot.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def _send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.warning("Telegram send failed: {}", e)
        return False


def alert_fill(market_id: str, side: str, size: float, price: float) -> None:
    msg = f"Fill: {side} size={size:.2f} @ {price:.2%} (market {market_id[:12]}...)"
    if _send_telegram(msg):
        logger.debug("Telegram sent: fill alert")


def alert_error(title: str, detail: str) -> None:
    msg = f"Error: {title}\n{detail}"
    if _send_telegram(msg):
        logger.debug("Telegram sent: error alert")


def alert_circuit_breaker(reason: str) -> None:
    msg = f"Circuit breaker: {reason}"
    if _send_telegram(msg):
        logger.debug("Telegram sent: circuit breaker")


def alert_cooldown(seconds: int) -> None:
    msg = f"Cooldown: 3 consecutive order failures; paused {seconds}s"
    if _send_telegram(msg):
        logger.debug("Telegram sent: cooldown alert")


def alert_daily_summary(fills: int, pnl_usd: float, edge_captured: float, max_drawdown_pct: Optional[float]) -> None:
    msg = f"Daily summary: fills={fills} PnL=${pnl_usd:.2f} edge={edge_captured:.2%}"
    if max_drawdown_pct is not None:
        msg += f" max_dd={max_drawdown_pct:.1%}"
    if _send_telegram(msg):
        logger.debug("Telegram sent: daily summary")
