# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python project using the Alpaca Trading API (v3.2.0, legacy SDK) for algorithmic trading. 
Currently building three trading bots:
1. **Stop Strategy Bot** - Bracket orders with stop-loss and take-profit
2. **Copy Trading Bot** - Replicate trades from master traders
3. **Wheel Strategy Bot** - Options income strategy (cash-secured puts + covered calls)

## Setup

1. Copy `.env.example` to `.env` and add your Alpaca API credentials
2. Install dependencies: `pip install -r requirements.txt`
3. Run health check: `python -c "from core.alpaca_client import get_trading_client; print(get_trading_client().get_account())"`

## Project Structure

```
.
├── core/                      # Shared infrastructure
│   ├── alpaca_client.py       # Authenticated client wrapper
│   ├── logger.py              # Structured logging
│   └── db.py                  # SQLite utilities
├── bots/
│   ├── stop_strategy/         # Stop Strategy Bot
│   │   ├── order_placer.py    # Bracket/trailing stop orders
│   │   ├── position_sizer.py  # Risk-based position sizing
│   │   ├── risk_manager.py    # Daily loss limits
│   │   ├── order_monitor.py   # State machine + breakeven
│   │   ├── config_loader.py   # YAML config handling
│   │   └── db.py              # Bot-specific DB ops
│   ├── copy_trading/          # (future)
│   └── wheel_strategy/        # (future)
├── tests/                     # Test suite
├── config/
│   └── settings.yaml          # Bot configuration
└── .env                       # API credentials (not committed)
```

## Commands

```bash
# Run tests
pytest tests/test_stop_strategy_bot.py -v

# Test API connection
python alpaca_connect_test.py

# Execute test trade
python alpaca_trade_test.py

# Create default config
python -c "from bots.stop_strategy.config_loader import create_default_config; create_default_config('config/settings.yaml')"
```

## Credentials

API keys are loaded from `.env` file (see `.env.example`). Never commit credentials to git.

## Testing

All tests use mocked Alpaca clients — no live API calls during test runs.
Run `pip install -r requirements.txt` to install test dependencies.

## Architecture Notes

- Uses paper trading by default (set `ALPACA_PAPER=true` in `.env`)
- SQLite for local data persistence
- Environment variables for sensitive config, YAML for strategy parameters
- All bots share `core/` infrastructure
