# Alpaca Trading Bot

Algorithmic trading system using the Alpaca Trading API. Supports multiple trading strategies with automated risk management.

**Repository:** https://github.com/jefhei/trading-bot

## Features

- **Stop Strategy Bot** - Automated bracket orders with stop-loss and take-profit
  - Fixed stop-loss (percentage-based)
  - Trailing stops
  - ATR-based dynamic stops
  - Breakeven stop adjustment (50% of take-profit trigger)
  - Risk/reward ratio targeting

- **Copy Trading Bot** (planned) - Replicate trades from master accounts
- **Wheel Strategy Bot** (planned) - Options income strategies

## Quick Start

```bash
# Clone the repository
git clone https://github.com/jefhei/trading-bot.git
cd trading-bot

# Install dependencies
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env with your Alpaca API keys

# Test connection
python alpaca_connect_test.py

# Run tests
pytest tests/test_stop_strategy_bot.py -v

# Execute a test trade (paper trading)
python alpaca_trade_test.py
```

## Configuration

Copy `.env.example` to `.env` and add your Alpaca credentials:

```bash
ALPACA_API_KEY=your_key_here
ALPACA_SECRET_KEY=your_secret_here
ALPACA_PAPER=true
```

Strategy parameters are configured in `config/settings.yaml`.

## Project Structure

```
.
├── core/                      # Shared infrastructure
│   ├── alpaca_client.py       # Authenticated client wrapper
│   ├── logger.py              # Structured logging
│   └── db.py                  # SQLite utilities
├── bots/
│   ├── stop_strategy/         # Stop Strategy Bot
│   ├── copy_trading/          # (planned)
│   └── wheel_strategy/        # (planned)
├── tests/                     # Test suite (43 tests)
├── config/                    # Configuration files
└── CLAUDE.md                  # Development guide
```

## API Testing

The test suite uses mocked Alpaca clients - no live API calls during test runs.

```bash
pytest tests/test_stop_strategy_bot.py -v
```

## Trading Modes

- **Paper Trading**: Default, uses simulated money
- **Live Trading**: Set `ALPACA_PAPER=false` in `.env` (not recommended for testing)

## Risk Management

- Daily loss limits
- Maximum position size limits
- Position sizing based on account risk
- Automatic stop-loss on all positions

## License

Private repository - see LICENSE file for details.
