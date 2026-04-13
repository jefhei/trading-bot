# Product Requirements Document: Wheel Strategy Bot

## Overview

**Product Name:** Wheel Strategy Bot  
**Version:** 1.0  
**Status:** Draft  

The Wheel Strategy Bot automates "The Wheel" options trading strategy, also known as the "1-2-3 Strategy." This income-generating strategy involves:
1. Selling cash-secured puts on stocks you want to own
2. If assigned, owning the stock and selling covered calls
3. If shares are called away, repeat from step 1

The bot manages this cycle automatically, handling options sales, assignment, and position management.

## Goals

- Automate the complete wheel strategy cycle without manual intervention
- Generate consistent income through options premium collection
- Manage risk through disciplined stock selection and position sizing
- Track cost basis adjustments from premium collected
- Handle assignment and exercise events automatically

## Target Users

- Income-focused investors seeking monthly cash flow
- Options traders who want to automate repetitive wheel management
- Investors comfortable owning underlying stocks at lower prices
- Users with Alpaca options trading approval (Level 1 minimum)

## Functional Requirements

### FR-1: Stock Universe Management

Maintain a list of stocks eligible for wheel strategy:
- **Watchlist:** Stocks user wants to own long-term
- **Fundamental Filters:** Minimum market cap, dividend yield, sector constraints
- **Technical Filters:** Price above moving average, relative strength, etc.
- **IV Rank Filter:** Only sell options when Implied Volatility Rank is high (>50%)

### FR-2: Cash-Secured Put Selling (Phase 1)

Automatically sell cash-secured puts:
- **Strike Selection:** Delta-based (0.30 delta) or technical support levels
- **Expiration Selection:** 30-45 days to expiration (DTE)
- **Premium Target:** Minimum premium as % of strike price (e.g., 1-2%)
- **Position Sizing:** Maximum capital allocated per stock (e.g., 10% of portfolio)

### FR-3: Assignment Handling

Automatically handle put assignment:
- Detect when shares are assigned (100 shares per contract)
- Update position tracking with new stock ownership
- Calculate cost basis: strike price - premium collected
- Transition to covered call phase

### FR-4: Covered Call Selling (Phase 2)

Automatically sell covered calls on assigned stock:
- **Strike Selection:** Above cost basis to ensure profitable exit
- **Expiration Selection:** 30-45 DTE
- **Premium Target:** Minimum premium as % of strike (e.g., 1-2%)
- **Call Protection:** Avoid selling calls below cost basis unless rolling

### FR-5: Share Call-Away Handling

Automatically handle when shares are called away:
- Detect when covered call is exercised
- Calculate total return: premium + capital gains
- Reset position tracking for stock
- Return to cash-secured put phase

### FR-6: Position Management

Track and manage all positions:
- **Open Options:** List of sold puts/calls with days to expiration
- **Stock Positions:** Shares owned with cost basis and break-even price
- **Premium Tracking:** Total premium collected per position and overall
- **Return Metrics:** Annualized return on capital, yield on cost

### FR-7: Roll Management

Support rolling options positions:
- **Roll Down:** Put is tested, roll to lower strike for more credit
- **Roll Out:** More time needed, roll to later expiration
- **Roll Up and Out:** Covered call challenged, roll to higher strike + later date
- **Auto-Roll Triggers:** Based on delta threshold or days to expiration

### FR-8: Risk Controls

Implement risk management features:
- **Maximum Capital Per Stock:** Cap exposure to single underlying (default: 20%)
- **Maximum Put Contracts:** Limit number of open puts at once (default: 5)
- **Sector Concentration:** Limit exposure to single sector (default: 30%)
- **Cash Reserve:** Maintain minimum cash for new put sales (default: 20%)
- **Stop-Loss on Stock:** Sell stock if it falls X% below cost basis (optional)

### FR-9: Earnings Protection

Avoid selling options through earnings announcements:
- Check earnings calendar before selling new options
- Skip stocks with earnings before expiration
- Optionally close positions before earnings if risk is too high

## Technical Requirements

### TR-1: Alpaca Options API Integration

- Use `alpaca-trade-api` Python SDK (v3.2.0+)
- Options trading approval Level 1 (Covered Calls/Cash-Secured Puts) minimum
- Support multi-leg orders (`mleg` order class) for spreads if needed
- Query options chain for strike/expiration selection

### TR-2: Options Chain Data

Integrate with market data provider for:
- Real-time options chain data
- Greeks (delta, theta, gamma, vega)
- Implied Volatility Rank (IV Rank)
- Open interest and volume

**Data Sources:**
- Alpaca Market Data API
- Polygon.io (Alpaca's data partner)
- ThetaGang/IvyBot for IV calculations

### TR-3: Data Storage

Use SQLite database to store:
- Stock watchlist with configuration per stock
- Open options positions with entry details
- Stock positions with cost basis
- Trade history (premium collected, assignments, exercises)
- Performance metrics over time

### TR-4: Configuration File

YAML configuration structure:
```yaml
wheel_strategy:
  watchlist:
    - symbol: "AAPL"
      max_contracts: 5
      max_capital: 10000
      min_premium_pct: 1.0
      target_delta: 0.30
      enabled: true
    - symbol: "MSFT"
      max_contracts: 3
      max_capital: 15000
      min_premium_pct: 1.5
      target_delta: 0.30
      enabled: true
  
  put_selling:
    days_to_expiration_min: 30
    days_to_expiration_max: 45
    target_delta: 0.30
    min_premium_pct: 1.0
    max_contracts_per_stock: 5
    avoid_earnings: true
  
  call_selling:
    days_to_expiration_min: 30
    days_to_expiration_max: 45
    target_delta: 0.30
    min_premium_pct: 1.0
    strike_min_above_cost_basis: 0.0
  
  risk_controls:
    max_capital_per_stock_pct: 20.0
    max_total_puts: 10
    max_sector_concentration_pct: 30.0
    min_cash_reserve_pct: 20.0
    stock_stop_loss_pct: 15.0
  
  roll_management:
    auto_roll_put_delta: 0.70
    auto_roll_call_delta: 0.70
    roll_days_to_expiration: 7
```

### TR-5: Scheduling

Run strategy checks on schedule:
- **Daily Check:** Market open - scan for new positions to open
- **Daily Check:** Market close - manage expiring options
- **Intraday Check:** Monitor for roll triggers (every 30 min)
- **Earnings Check:** Weekly update of earnings calendar

### TR-6: Notifications

Alert user on important events:
- New put/call sold (symbol, strike, expiration, premium)
- Assignment notification (shares purchased)
- Shares called away (capital gains realized)
- Position rolled (old strike/exp → new strike/exp)
- Error conditions (insufficient capital, API failures)

## User Stories

### US-1: Start Wheel on a Stock

**As an** investor  
**I want to** start the wheel strategy on a stock I want to own  
**So that** I generate income while waiting to buy at a lower price

**Acceptance Criteria:**
- Add stock to watchlist with configuration
- Bot sells cash-secured puts at next available opportunity
- Premium collected reduces effective cost basis
- If assigned, transition to covered call phase automatically

### US-2: Manage Assignment

**As a** wheel trader  
**I want to** be notified when I'm assigned shares  
**So that** I know I now own the stock and should sell calls

**Acceptance Criteria:**
- Bot detects assignment via account activity feed
- Notification sent with assignment details (price, shares, cost basis)
- Covered call phase begins automatically at next opportunity
- Cost basis displayed as strike - premium collected

### US-3: Roll a Tested Put

**As a** wheel trader  
**I want to** roll my put to a later date if it's threatened  
**So that** I avoid assignment and collect more premium

**Acceptance Criteria:**
- Bot monitors put delta; triggers roll when delta > 0.70
- Closes current put at market price
- Sells new put at later expiration for net credit
- Cost basis adjusted for roll credit/debit

### US-4: Track Returns

**As an** investor  
**I want to** see my total returns from the wheel strategy  
**So that** I can evaluate if it's meeting my income goals

**Acceptance Criteria:**
- Dashboard showing premium collected YTD
- Annualized return on capital deployed
- Yield on cost for each position
- Total return including capital gains/losses

### US-5: Pause Strategy

**As a** wheel trader  
**I want to** pause the wheel strategy  
**So that** I can stop trading during market uncertainty

**Acceptance Criteria:**
- Toggle to disable new position openings
- Existing positions continue to be managed
- Can resume at any time to restart new trades

## Metrics & KPIs

- **Monthly Income:** Premium collected per month
- **Annualized Return:** Return on capital deployed (APY)
- **Assignment Rate:** % of puts that result in assignment
- **Call-Away Rate:** % of covered calls that result in share sale
- **Win Rate:** % of positions closed at profit
- **Average Days in Trade:** How long capital is deployed per cycle
- **Yield on Cost:** Annual income / cost basis of stock positions

## Dependencies

- Alpaca Trading API account with options approval (Level 1+)
- Python 3.8+
- `alpaca-trade-api` package (v3.2.0)
- Options market data subscription (Alpaca or Polygon.io)
- SQLite for data storage
- Earnings calendar API (optional, for earnings protection)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Stock crashes below put strike | High | Only wheel stocks you want to own; diversify across sectors; maintain cash reserves |
| Stock runs up rapidly (opportunity cost) | Medium | Set strike targets above cost basis; accept missing some upside for income |
| Early assignment on short puts | Low | Avoid deep ITM strikes; roll before expiration if needed |
| Early assignment on short calls | Low | Strike above cost basis; roll up and out if challenged |
| Earnings gap risk | High | Avoid selling options through earnings; close before announcement |
| Margin call / insufficient capital | High | Maintain cash reserves; limit total put contracts; monitor buying power |
| IV crush after selling puts | Medium | Sell when IV Rank is high (>50); accept risk as part of strategy |

## Options Approval Requirements

To execute the wheel strategy on Alpaca, users need:
- **Level 1 Options Approval:** Covered calls and cash-secured puts
- Application via API: `POST /v1/accounts/{account_id}/options/approval` with `level: 1`
- Account must meet minimum requirements (varies by broker)

## Future Enhancements

- Diagonal spreads: Sell near-term, buy longer-dated for reduced capital requirement
- Ratio spreads: Sell more options than bought for enhanced income
- Automatic ETF wheel: Apply strategy to broad market ETFs (SPY, QQQ, IWM)
- Tax optimization: Track cost basis for tax lot selection (FIFO, LIFO, specific)
- Backtesting module: Test wheel parameters on historical data
- Integration with portfolio margin accounts for capital efficiency
