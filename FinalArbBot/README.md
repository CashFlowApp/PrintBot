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

See spec PDF and `WINDOWS_RUN.txt` for full details.
