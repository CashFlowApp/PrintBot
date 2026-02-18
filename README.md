# Polymarket YES/NO Arbitrage Bot

Production-ready arbitrage bot for Polymarket short-term (5/15 min) BTC/ETH/SOL binary markets. Locks in profit when best YES ask + best NO ask < 1.0 − edge (after fees).

## Requirements

- Python 3.11+
- Windows PowerShell or Linux (VPS)

## Quick Start

1. Copy `.env.example` to `.env` and set your API keys and parameters (never commit `.env`).
2. **Validate only** (no trading):
   ```bash
   python -m bot.validate_only
   ```
3. Run the bot (default: paper mode):
   ```bash
   python run.py
   ```

## Modes

- **Paper mode** (default): `PAPER_MODE=true` — logs `[PAPER MODE] Would have placed...` only; no real orders.
- **Live trading**: Set `PAPER_MODE=false` and `LIVE_TRADING=true` in `.env` (use with caution).

## Project Layout

- `bot/` — Python package (config, discovery, fees, WebSockets, arb engine, protection, positions, settlement, alerts, main).
- `run.py` — Entry point.
- `docker/` — Dockerfile for containerized run.
- `systemd/` — systemd service file for Linux.

## Risk Controls

- Daily loss limit, max open markets, one-side protection (72% + 30s timer), auto-cancel 90s before expiry.
- Circuit breaker on drawdown; `KILL` file in runtime directory stops the bot cleanly.

## Manual short-term token pairs

Discovery uses **manual pairs first** (so short-term markets are reliable even when Gamma ordering buries them):

1. **Set in `.env`** (recommended): add a single line with a JSON array of objects, each with `market_id`, `yes_token`, and `no_token`:
   ```env
   SHORT_TERM_TOKEN_PAIRS_JSON=[{"market_id":"0x...","yes_token":"123...","no_token":"456..."}]
   ```
   Add more objects for multiple 5M/15M markets. Get current token IDs from Polymarket:
   - Open [polymarket.com/crypto](https://polymarket.com/crypto) and the **5M** or **15M** sections.
   - For each market, use the CLOB token IDs (e.g. from the API or from the market page / network tab).
   - Paste them into the JSON array above.

2. **If `SHORT_TERM_TOKEN_PAIRS_JSON` is empty or unset**, the bot falls back to one Gamma API call with `tag_id=crypto` and newest-first ordering, then filters for questions containing "up or down" and orderbook-enabled markets with exactly two tokens.

With manual pairs set, the bot subscribes only to those YES/NO token IDs and checks edge + depth for each pair. Paper mode and protections are unchanged.

See spec PDF and `WINDOWS_RUN.txt` for full details.
