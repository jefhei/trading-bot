# Product Requirements Document: Copy Trading Bot

## Overview

**Product Name:** Copy Trading Bot  
**Version:** 1.0  
**Status:** Draft  

The Copy Trading Bot automatically replicates trades from designated "master" traders or signal sources. When a master trader executes a trade, the bot automatically places proportional trades in the follower's account based on configured allocation rules.

## Goals

- Automatically replicate trades from selected master traders in real-time
- Scale trade sizes proportionally based on follower account size
- Support multiple master traders with different allocation percentages
- Provide risk controls to limit exposure per master trader
- Enable followers to start/stop copying at any time

## Target Users

- Novice traders who want to learn from experienced traders
- Busy individuals who want exposure to trading without active management
- Investors seeking diversified trading strategies across multiple masters
- Signal service providers looking to automate trade distribution

## Functional Requirements

### FR-1: Master Trader Registration

Support registration of master traders to follow:
- Master trader identifier (account ID, signal service ID, or webhook URL)
- Allocation percentage per master (e.g., 30% of portfolio)
- Maximum position size per master
- Enabled/disabled status

### FR-2: Trade Replication

Automatically replicate master trades with:
- **Proportional Sizing:** Scale quantity based on account value ratio
- **Fixed Sizing:** Use fixed dollar amount per trade regardless of master size
- **Multiplier:** Apply a multiplier to master position size (e.g., 0.5x, 2x)

**Example Calculation:**
```
Master account value: $100,000
Follower account value: $10,000
Master buys 100 shares of AAPL
Follower buys: 100 * (10,000 / 100,000) = 10 shares
```

### FR-3: Trade Filtering

Allow followers to configure which trades to copy:
- By asset class (stocks only, options only, crypto only)
- By symbol whitelist/blacklist
- By minimum/maximum position size
- By long/short direction
- By sector or industry

### FR-4: Latency Requirements

- Receive master trade signals within 5 seconds of execution
- Place follower order within 2 seconds of signal receipt
- Support both polling and push-based signal delivery

### FR-5: Signal Input Methods

Support multiple signal ingestion methods:
- **Alpaca Account Streaming:** Monitor master Alpaca account via trade update events
- **Webhook API:** Receive HTTP POST notifications from external signal services
- **CSV/File Import:** Batch process trades from exported files
- **Manual Entry:** Copy trades entered manually via configuration

### FR-6: Position Management

- Track open positions per master trader
- Automatically close proportional positions when master closes
- Handle partial closes proportionally
- Sync positions on startup to recover from downtime

### FR-7: Risk Controls

Implement risk management features:
- **Maximum Allocation Per Master:** Cap exposure to any single master (default: 30%)
- **Maximum Total Copy Allocation:** Cap total capital deployed to copy trading (default: 80%)
- **Daily Loss Limit Per Master:** Halt copying from master after X% daily loss
- **Maximum Drawdown Limit:** Stop all copying after portfolio drawdown reaches threshold
- **Position Size Limits:** Min/max position size in dollars or percentage

### FR-8: Master Trader Performance Tracking

Track and display master trader statistics:
- Total return since following
- Win rate
- Average win/loss ratio
- Maximum drawdown
- Number of trades copied
- Fees/commissions paid

## Technical Requirements

### TR-1: Alpaca API Integration

- Use `alpaca-trade-api` Python SDK (v3.2.0+)
- Stream trade updates via SSE endpoint: `GET /v2/events/trades`
- Support both paper and live trading endpoints
- Handle API rate limits with exponential backoff

### TR-2: Signal Processing Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Master Trader   │────▶│ Signal Processor │────▶│ Order Executor  │
│ (Alpaca/Webhook)│     │ (Queue/Broker)   │     │ (Alpaca API)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │ Position Manager │
                        │ (State/Tracking) │
                        └──────────────────┘
```

### TR-3: Data Storage

Use SQLite database to store:
- Master trader configurations
- Trade history (master and follower trades)
- Position tracking per master
- Performance metrics
- Error logs and failed trade attempts

### TR-4: Configuration File

YAML configuration structure:
```yaml
copy_trading:
  masters:
    - id: "master_1"
      name: "Aggressive Growth"
      account_id: "xxx-xxx-xxx"  # Alpaca account ID or webhook URL
      allocation_pct: 30.0
      max_position_pct: 10.0
      enabled: true
      filters:
        min_position_size: 100
        max_position_size: 5000
        symbols_blacklist: ["GME", "AMC"]
        asset_classes: ["us_equity"]
    - id: "master_2"
      name: "Conservative Income"
      allocation_pct: 20.0
      max_position_pct: 5.0
      enabled: true
  
  risk_controls:
    max_total_allocation_pct: 80.0
    daily_loss_limit_pct: 5.0
    max_drawdown_pct: 15.0
    min_cash_reserve_pct: 10.0
```

### TR-5: Error Handling

- Queue trades when API is unavailable
- Retry failed orders with exponential backoff
- Alert user when copying fails (email, SMS, or dashboard notification)
- Log all errors with full context for debugging

## User Stories

### US-1: Follow a Master Trader

**As a** novice trader  
**I want to** select and follow an experienced master trader  
**So that** my account automatically replicates their trades

**Acceptance Criteria:**
- Register master trader with allocation percentage
- Bot begins monitoring master's trades immediately
- Follower trades are proportional to account size
- All copied trades logged with reference to master trade

### US-2: Stop Copying a Master

**As a** follower  
**I want to** stop copying a master trader at any time  
**So that** I can exit the relationship if performance declines

**Acceptance Criteria:**
- Disable master trader in configuration
- Option to close all copied positions immediately or let them run
- No new trades copied from disabled master
- Historical performance data preserved

### US-3: Customize Position Sizing

**As a** follower with a small account  
**I want to** set minimum and maximum position sizes  
**So that** I don't end up with odd lot positions or overconcentration

**Acceptance Criteria:**
- Configure min position size (e.g., $100 minimum)
- Configure max position size (e.g., $5,000 maximum or 10% of portfolio)
- Positions outside range are either skipped or adjusted to fit

### US-4: Filter Unwanted Trades

**As a** risk-averse follower  
**I want to** filter out certain symbols or sectors  
**So that** I don't copy trades I'm uncomfortable with

**Acceptance Criteria:**
- Maintain symbol blacklist (e.g., meme stocks, leveraged ETFs)
- Maintain symbol whitelist (optional)
- Filter by asset class (no options, no crypto)
- Filtered trades are logged but not executed

### US-5: View Performance Metrics

**As a** follower  
**I want to** see how each master trader is performing  
**So that** I can make informed decisions about who to follow

**Acceptance Criteria:**
- Dashboard showing each master's return since following
- Win rate and average win/loss ratio
- List of all copied trades with P&L
- Comparison to buy-and-hold benchmark

## Metrics & KPIs

- Total return from copy trading
- Return per master trader
- Win rate of copied trades
- Average latency from master trade to follower trade
- Percentage of trades successfully copied (vs failed)
- Follower retention rate (how long users follow each master)

## Dependencies

- Alpaca Trading API account (paper or live)
- Python 3.8+
- `alpaca-trade-api` package (v3.2.0)
- SQLite for data storage
- Redis or similar for trade queue (optional, for high-volume scenarios)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Latency in trade replication | High | Use WebSocket streaming; co-locate servers; optimize order execution path |
| Master trader stops trading | Medium | Monitor master activity; alert followers of inactive masters |
| Master trader changes strategy | Medium | Track strategy drift metrics; allow master to announce strategy changes |
| Follower account too small for proportional sizing | Low | Implement minimum position size; round to nearest whole share |
| API rate limits exceeded | Medium | Implement request queuing; prioritize entry over exit orders |
| Master trader blow-up (large losses) | High | Enforce per-master loss limits; diversification across multiple masters |

## Future Enhancements

- Social features: master trader profiles, follower forums, performance leaderboards
- Automated master selection based on risk tolerance and goals
- Options support for multi-leg order replication
- Copy trading marketplace for signal providers to monetize their trades
- Mobile app for monitoring and managing copy relationships
- Integration with third-party signal providers (TradingView, etc.)
