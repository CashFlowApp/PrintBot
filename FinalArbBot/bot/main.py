"""Orchestration: discovery, market/user WS, arb engine, protection, settlement, kill switch."""
import sys
import threading
import time
from typing import List

from loguru import logger

from bot import config
from bot.alerts import alert_cooldown, alert_error
from bot.arb_engine import ArbEngine
from bot.discovery import DiscoveryLoop
from bot.fees import FeeCache
from bot.positions import PositionStore
from bot.protection import OneSideProtection
from bot.settlement import SettlementLoop
from bot.ws_market import PolymarketWSMarket
from bot.ws_user import PolymarketWSUser

# Optional: ClobClient for live order placement
_clob_client = None


def _get_clob_client():
    global _clob_client
    if _clob_client is not None:
        return _clob_client
    if not config.LIVE_TRADING or not config.PRIVATE_KEY:
        return None
    try:
        from py_clob_client.client import ClobClient
        _clob_client = ClobClient(
            config.HOST,
            key=config.PRIVATE_KEY,
            chain_id=config.CHAIN_ID,
            signature_type=1,
            funder=config.PROXY_WALLET or None,
        )
        _clob_client.set_api_creds(_clob_client.create_or_derive_api_creds())
        return _clob_client
    except Exception as e:
        logger.warning("ClobClient init failed (live orders disabled): {}", e)
        return None


def place_orders_impl(
    market_id: str,
    yes_token: str,
    no_token: str,
    price_yes: float,
    price_no: float,
    shares: float,
    size_usd: float,
) -> bool:
    if config.PAPER_MODE:
        logger.info("[PAPER MODE] Would have placed limit buy YES price={} size={}", price_yes, shares)
        logger.info("[PAPER MODE] Would have placed limit buy NO price={} size={}", price_no, shares)
        return True
    client = _get_clob_client()
    if not client:
        logger.warning("Live trading requested but no ClobClient; skipping order")
        return False
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        o_yes = OrderArgs(token_id=yes_token, price=price_yes, size=shares, side=BUY)
        o_no = OrderArgs(token_id=no_token, price=price_no, size=shares, side=BUY)
        s_yes = client.create_order(o_yes)
        s_no = client.create_order(o_no)
        client.post_order(s_yes, OrderType.GTC)
        client.post_order(s_no, OrderType.GTC)
        return True
    except Exception as e:
        logger.exception("Place orders failed: {}", e)
        return False


def place_sell_impl(token_id: str, side: str, size: float, is_market: bool) -> None:
    if config.PAPER_MODE:
        logger.info("[PAPER MODE] Would have placed hedge sell token_id=... side={} size={}", side, size)
        return
    client = _get_clob_client()
    if not client:
        return
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL
        # Use aggressive limit sell (market sell API can be inconsistent)
        book = client.get_order_book(token_id)
        best_bid = 0.01
        if book and getattr(book, "bids", None) and book.bids:
            b = book.bids[0]
            best_bid = float(b[0]) if isinstance(b, (list, tuple)) else float(b.get("price", 0.01))
        o = OrderArgs(token_id=token_id, price=best_bid, size=size, side=SELL)
        signed = client.create_order(o)
        client.post_order(signed, OrderType.GTC)
    except Exception as e:
        logger.exception("Hedge sell failed: {}", e)


def main() -> int:
    logger.add("polymarket_bot.log", rotation="1 day", level="DEBUG")
    logger.info("PAPER_MODE={} LIVE_TRADING={}", config.PAPER_MODE, config.LIVE_TRADING)

    fee_cache = FeeCache()
    position_store = PositionStore()
    protection = OneSideProtection(place_sell_order=place_sell_impl)

    def on_partial_fill(market_id: str, yes_token: str, no_token: str, yes_filled: float, no_filled: float, price_yes: float, price_no: float):
        position_store.update_fills(market_id, yes_filled, no_filled, price_yes * yes_filled, price_no * no_filled)
        if yes_filled > 0 and no_filled > 0:
            position_store.open_position(market_id, yes_token, no_token, yes_filled, no_filled, price_yes * yes_filled, price_no * no_filled)

    arb = ArbEngine(
        fee_cache=fee_cache,
        position_store=position_store,
        protection=protection,
        place_orders=place_orders_impl,
        on_partial_fill=on_partial_fill,
    )

    def get_open_market_ids() -> List[str]:
        return [p["market_id"] for p in position_store.get_open_positions()]

    def cancel_orders_for_market(market_id: str) -> None:
        if config.PAPER_MODE:
            logger.info("[PAPER MODE] Would have cancelled orders for market {}", market_id[:12] + "...")
            return
        client = _get_clob_client()
        if not client:
            return
        try:
            from py_clob_client.clob_types import OpenOrderParams
            orders = client.get_orders(OpenOrderParams())
            for o in orders or []:
                if o.get("market") == market_id or o.get("asset_id", "").startswith(market_id):
                    client.cancel(o["id"])
        except Exception as e:
            logger.warning("Cancel orders failed: {}", e)

    def check_resolved_and_redeem() -> None:
        # Placeholder: check Gamma for resolved, then call relayer redeem
        pass

    settlement = SettlementLoop(get_open_market_ids, cancel_orders_for_market, check_resolved_and_redeem)

    eligible_markets: List[dict] = []
    all_token_ids: List[str] = []

    def on_discovery_update(markets: List[dict]) -> None:
        nonlocal eligible_markets, all_token_ids
        eligible_markets = markets
        tokens = []
        for m in markets:
            tokens.append(m["yes_token"])
            tokens.append(m["no_token"])
        all_token_ids = list(dict.fromkeys(tokens))

    discovery = DiscoveryLoop(on_update=on_discovery_update)
    discovery.start()
    time.sleep(2)
    eligible_markets = discovery.get_last_markets()
    for m in eligible_markets:
        all_token_ids.append(m["yes_token"])
        all_token_ids.append(m["no_token"])
    all_token_ids = list(dict.fromkeys(all_token_ids))

    def on_market_message(frame: dict) -> None:
        arb.on_market_message(frame)
        asset_id = frame.get("asset_id") or frame.get("token_id")
        if not asset_id:
            return
        for m in eligible_markets:
            if m["yes_token"] == asset_id or m["no_token"] == asset_id:
                arb.try_arb(m["market_id"], m["yes_token"], m["no_token"])
                arb.check_protection(m["market_id"], m["yes_token"], m["no_token"])
                break

    def on_user_message(frame: dict) -> None:
        # Fills / updates for account
        event = frame.get("event_type") or frame.get("type")
        if event in ("fill", "last_trade_price", "trade"):
            # Update position and trigger protection if one-sided
            pass
        logger.debug("User WS: {}", event)

    ws_market = PolymarketWSMarket(all_token_ids or ["dummy"], on_market_message)
    ws_user = PolymarketWSUser(all_token_ids or ["dummy"], on_user_message)
    t_m = ws_market.run_forever_in_thread()
    t_u = ws_user.run_forever_in_thread()
    settlement.start()

    logger.info("Bot running. Create KILL file in project root to stop.")
    heartbeat = 0
    try:
        while not config.is_kill_switch_active():
            time.sleep(5)
            heartbeat += 5
            if heartbeat >= 60:
                logger.info("Heartbeat: bot alive (for UptimeRobot)")
                heartbeat = 0
            # Refresh eligible list for WS re-subscribe would require reconnect; discovery already updates eligible_markets via callback
    except KeyboardInterrupt:
        pass
    logger.info("Shutting down.")
    discovery.stop()
    settlement.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
