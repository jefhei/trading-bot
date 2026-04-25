# Alpaca Trading Bot

Algorithmic trading system using the Alpaca Trading API. Supports multiple trading strategies with automated risk management.

**Repository:** https://github.com/jefhei/trading-bot

## Features

- **Stop Strategy Bot** - Automated bracket orders with stop-loss and take-profit
  - Fixed stop-loss (percentage-based)
  - Trailing stops
  - ATR based dynamic stops
  - Breakeven stop adjustment (50% of take-profit trigger)
  - Risk/reward ratio targeting

- **Copy Trading Bot** - Replicate trades from master accounts
  - Signal processing and trade filtering
  - Position sizing based on allocation rules
  - Risk management with per-master limits
  - Performance tracking
  - SQL-backed position history

- **Wheel Strategy Bot** - Automated "Wheel" options income strategy
  - **Cash-Secured Put Selling** - Delta-based strike selection (0.30 target), 30-45 DTE expiration range, minimum premium targets, earnings-aware filtering
  - **Covered Call Selling** - Strike above cost basis, premium optimization
  - **Assignment Handling** - Auto-detects put assignments, calculates cost basis (strike - premium), transitions to long stock phase
  - **Call-Away Handling** - Detects share exercises, calculates total return, resets for next cycle
  - **Position Management** - Tracks open options, stock positions, premium collection, and return metrics
  - **Roll Management** - Auto-roll puts (down/out) and calls (up/out) based on delta thresholds
  - **Risk Controls** - Max capital per stock (20%), max open puts, sector concentration (30%), cash reserves (20%), optional stock stop-loss
  - **Earnings Protection** - Checks earnings calendar before selling options, skips symbols with earnings before expiration
  - **Watchlist Management** - Fundamental filters (market cap, dividend, sector), IV Rank filter, technical filters

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
pytest tests/ -v

# Execute a test trade (paper trading)
python alpaca_trade_test.py
```

## Configuration

Copy `.env.example` to `.env` and add your Alpaca credentials:

```bash
ALPACA_API_KEY=***
ALPACA_SECRET_KEY=***
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
│   │   ├── order_placer.py    # Bracket/trailing stop orders
│   │   ├── position_sizer.py  # Risk-based position sizing
│   │   ├── risk_manager.py    # Daily loss limits
│   │   ├── order_monitor.py   # State machine + breakeven
│   │   ├── config_loader.py   # YAML config handling
│   │   └── db.py              # Bot-specific DB ops
│   ├── copy_trading/          # Copy Trading Bot
│   │   ├── signal_processor.py
│   │   ├── trade_filter.py
│   │   ├── position_sizer.py
│   │   ├── order_executor.py
│   │   ├── risk_manager.py
│   │   ├── position_tracker.py
│   │   ├── performance_tracker.py
│   │   └── config_loader.py
│   └── wheel_strategy/        # Wheel Strategy Bot
│       ├── wheel_bot.py       # Main bot orchestrator
│       ├── state_machine.py   # Phase transitions (put → stock → call)
│       ├── watchlist_manager.py
│       ├── put_seller.py
│       ├── call_seller.py
│       ├── assignment_manager.py
│       ├── position_manager.py
│       ├── roll_manager.py
│       ├── risk_manager.py
│       ├── earnings_checker.py
│       ├── config_loader.py
│       └── db.py              # SQLite schema and queries
├── tests/                     # Test suite (579 tests)
├── config/                    # Configuration files
├── PRD_*.md                   # Product Requirements Documents
└── CLAUDE.md                  # Development guide
```

## API Testing

The test suite uses mocked Alpaca clients - no live API calls during test runs.

```bash
# Run all tests
pytest tests/ -v

# Run specific bot tests
pytest tests/test_stop_strategy_bot.py -v          # 43 tests
pytest tests/test_copy_trading.py -v               # Copy Trading tests
pytest tests/test_copy_trading_bot.py -v

# Wheel Strategy Bot tests
pytest tests/test_wheel_risk_controls.py -v        # Risk controls (FR-8)
pytest tests/test_wheel_earnings.py -v             # Earnings protection (FR-9)
pytest tests/test_state_machine.py -v              # State machine (FR-4)
pytest tests/test_watchlist.py -v                  # Watchlist management (FR-1)
pytest tests/test_put_seller.py -v                 # Put selling (FR-2)
pytest tests/test_call_seller.py -v                # Call selling (FR-4)
pytest tests/test_assignment_manager.py -v         # Assignment handling (FR-3)
pytest tests/test_call_away.py -v                  # Call-away handling (FR-5)
pytest tests/test_position_manager.py -v           # Position management (FR-6)
pytest tests/test_roll_manager.py -v               # Roll management (FR-7)
```

## Trading Modes

- **Paper Trading**: Default, uses simulated money
- **Live Trading**: Set `ALPACA_PAPER=false` in `.env` (not recommended for testing)

## Risk Management

- **Stop Strategy**: Daily loss limits, maximum position size, ATR-based stops
- **Copy Trading**: Per-master allocation limits, trade filtering
- **Wheel Strategy**: Capital limits per stock (20%), max open puts, sector concentration (30%), cash reserves (20%), earnings-aware option selling, optional stock stop-loss

## License

Private repository - see LICENSE file for details.
