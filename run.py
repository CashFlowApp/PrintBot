#!/usr/bin/env python3
"""Entry point for the Polymarket YES/NO arbitrage bot."""
import sys

if __name__ == "__main__":
    from bot.main import main
    sys.exit(main())
