# Product Requirements Document: Stop Strategy Bot

## Overview

**Product Name:** Stop Strategy Bot  
**Version:** 1.0  
**Status:** Draft  

The Stop Strategy Bot is an automated trading system that implements stop-loss and take-profit mechanisms to protect capital and lock in gains. It uses Alpaca's bracket order functionality to automatically manage position exits.

## Goals

- Automatically place stop-loss orders to limit downside risk on every trade
- Automatically place take-profit orders to realize gains at predetermined levels
- Support trailing stop orders to capture profits during strong trends
- Provide configurable risk parameters per trade or strategy
- Ensure all orders have defined exit strategies before entry

## Target Users

- Retail traders using Alpaca paper or live trading accounts
- Algorithmic traders seeking automated risk management
- Users who want disciplined exit strategies without emotional decision-making

## Functional Requirements

### FR-1: Bracket Order Support

The bot must place bracket orders (OTO - One-Triggers-Other) that automatically create:
- Initial entry order (market or limit)
- Take-profit limit order
- Stop-loss order

**API Reference:** Alpaca supports `bracket` order class for equity trading with `take_profit` and `stop_loss` parameters.

### FR-2: Configurable Stop-Loss Types

Support multiple stop-loss types:
- **Fixed Stop-Loss:** Static price level (e.g., 5% below entry)
- **Trailing Stop:** Percentage or dollar amount trailing stop that follows price upward
- **ATR-Based Stop:** Dynamic stop based on Average True Range volatility

### FR-3: Configurable Take-Profit Levels

Support multiple take-profit configurations:
- **Single Target:** One price level for full position exit
- **Multiple Targets:** Scale out at multiple price levels (e.g., 50% at 10%, 50% at 20%)
- **Risk-Reward Ratio:** Auto-calculate take-profit based on stop-loss distance (e.g., 2:1 R:R)

### FR-4: Position Sizing

Calculate position size based on:
- Account equity percentage (e.g., 2% risk per trade)
- Fixed dollar amount per trade
- Stop distance to determine share quantity

### FR-5: Order Management

- Monitor order status via Alpaca trade update events (SSE streaming)
- Allow manual adjustment of stop-loss to breakeven after price moves favorably
- Support canceling take-profit while keeping stop-loss active (and vice versa)

### FR-6: Risk Parameters Configuration

Provide configuration for:
- Maximum stop-loss percentage per trade (default: 5%)
- Minimum take-profit percentage (default: 10%)
- Trailing stop percentage (default: 3%)
- Maximum position size as % of portfolio (default: 10%)
- Daily loss limit to halt trading (default: 5% of account)

## Technical Requirements

### TR-1: Alpaca API Integration

- Use `alpaca-trade-api` Python SDK (v3.2.0+)
- Connect to paper trading endpoint for testing: `https://paper-alpaca.markets/v2`
- Connect to live trading endpoint for production: `https://api.alpaca.markets`
- Use WebSocket streaming for real-time order updates

### TR-2: Order Class Support

Utilize Alpaca's order classes:
- `bracket` - For OTO orders with take-profit and stop-loss
- `oco` - For One-Cancels-Other exit orders
- `trailing_stop` - For trailing stop order type

### TR-3: Data Storage

- Store trade history and performance metrics in local database (SQLite)
- Cache current positions and open orders
- Log all order events with timestamps

### TR-4: Configuration File

YAML or JSON configuration file containing:
```yaml
stop_strategy:
  default_stop_loss_pct: 5.0
  default_take_profit_pct: 10.0
  trailing_stop_pct: 3.0
  max_position_size_pct: 10.0
  daily_loss_limit_pct: 5.0
  use_trailing_stop: false
  risk_reward_ratio: 2.0
```

## User Stories

### US-1: Place Bracket Order

**As a** trader  
**I want to** place a bracket order with automatic stop-loss and take-profit  
**So that** my risk is defined before I enter the trade

**Acceptance Criteria:**
- Entry order triggers both stop-loss and take-profit orders
- Stop-loss set at configured percentage below entry
- Take-profit set at configured percentage above entry
- All three orders visible in Alpaca dashboard

### US-2: Trailing Stop Activation

**As a** trend trader  
**I want to** use a trailing stop order  
**So that** I can capture profits during strong upward moves

**Acceptance Criteria:**
- Trailing stop follows price upward
- Locks in when price reverses by trailing amount
- Can be combined with separate take-profit level

### US-3: Position Size Calculation

**As a** risk-conscious trader  
**I want to** auto-calculate position size based on my stop distance  
**So that** I never risk more than my defined percentage per trade

**Acceptance Criteria:**
- Input: account value, risk %, stop price
- Output: number of shares to buy
- Formula: `shares = (account_value * risk_pct) / (entry_price - stop_price)`

### US-4: Breakeven Stop Adjustment

**As a** trader  
**I want to** move my stop-loss to breakeven after price moves in my favor  
**So that** I eliminate risk on the trade

**Acceptance Criteria:**
- Trigger when price reaches 50% of take-profit distance
- Automatically cancel original stop-loss
- Place new stop-loss at entry price + commissions

## Metrics & KPIs

- Win rate (% of trades that hit take-profit vs stop-loss)
- Average win/loss ratio
- Maximum drawdown during trading period
- Total return vs buy-and-hold benchmark
- Number of trades executed per day/week

## Dependencies

- Alpaca Trading API account (paper or live)
- Python 3.8+
- `alpaca-trade-api` package (v3.2.0)
- pandas for data manipulation
- SQLite for local data storage

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| API rate limits | Medium | Implement request throttling and exponential backoff |
| Gap risk (overnight) | High | Use mental stops; avoid earnings plays; consider options hedging |
| Slippage on stop execution | Medium | Use stop-limit instead of stop-market for large positions |
| Technical failures | High | Implement heartbeat monitoring; alerting; manual override capability |

## Future Enhancements

- Integration with technical indicators for dynamic stop placement
- Machine learning for optimal stop-loss levels based on historical data
- Multi-portfolio support for managing separate strategies
- Web dashboard for real-time monitoring and manual intervention
- Backtesting module to optimize stop-loss parameters
