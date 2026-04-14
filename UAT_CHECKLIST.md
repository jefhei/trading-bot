# User Acceptance Testing (UAT) Checklist
## Stop Strategy Bot - Pre-Production Validation

**Purpose:** Verify the bot behaves correctly with real market data and API before live trading.
**Environment:** Alpaca Paper Trading Account
**Prerequisites:** API keys configured, tests passing (`pytest tests/test_stop_strategy_bot.py -v`)

---

## Phase 1: Basic Connectivity & Setup

| # | Test | Command/Action | Expected Result | Status |
|---|------|----------------|-----------------|--------|
| 1.1 | API Connection | `python alpaca_connect_test.py` | Account info displays, equity shows ~$100K | ☐ |
| 1.2 | Config Loading | `python -c "from bots.stop_strategy.config_loader import load_config; print(load_config('config/settings.yaml'))"` | Config dict prints without errors | ☐ |
| 1.3 | Market Hours Check | `python -c "from core.alpaca_client import is_market_open; print(is_market_open())"` | Returns True during market hours, False after hours | ☐ |
| 1.4 | Risk Manager Init | `python -c "from core.alpaca_client import get_trading_client; from bots.stop_strategy.risk_manager import RiskManager; from bots.stop_strategy.config_loader import load_config; c=get_trading_client(); cfg=load_config('config/settings.yaml'); rm=RiskManager(c, cfg['stop_strategy']); print(rm.get_account_summary())"` | Account summary displays correctly | ☐ |

**Sign-off:** _________________ Date: _______

---

## Phase 2: Order Execution (Critical)

### 2.1 Basic Bracket Order
| # | Step | Expected Result | Status |
|---|------|-----------------|--------|
| 2.1.1 | Run: `python run_stop_bot.py --symbol AAPL --entry 150.00 --dry-run` | Dry run shows order details, qty calculated | ☐ |
| 2.1.2 | Check: Review calculated quantity vs manual calculation | Position sizing formula matches: `qty = (equity × risk%) / (entry - stop)` | ☐ |
| 2.1.3 | Execute: Run without `--dry-run`, confirm with 'y' | Order ID returned, status shows "accepted" or "pending_new" | ☐ |
| 2.1.4 | Verify: Log into Alpaca dashboard (paper) | Order appears with 3 legs: Entry (limit), Stop-loss, Take-profit | ☐ |
| 2.1.5 | Document: Record order ID, entry price, stop price, TP price | All prices match bot output | ☐ |
| 2.1.6 | Cancel: Cancel order in Alpaca dashboard or via API | Order cancels without errors | ☐ |

### 2.2 Trailing Stop Order
| # | Step | Expected Result | Status |
|---|------|-----------------|--------|
| 2.2.1 | Run: `python run_stop_bot.py --symbol TSLA --entry 250.00 --trailing 3.0 --dry-run` | Shows trailing stop parameters | ☐ |
| 2.2.2 | Execute with `--yes` flag | Order submitted successfully | ☐ |
| 2.2.3 | Verify in Alpaca dashboard | Order shows "trailing_stop" type with 3% trail | ☐ |
| 2.2.4 | Cancel order | Order cancels cleanly | ☐ |

### 2.3 Risk/Reward Ratio Order
| # | Step | Expected Result | Status |
|---|------|-----------------|--------|
| 2.3.1 | Run: `python run_stop_bot.py --symbol NVDA --entry 120.00 --stop-pct 4.0 --risk-reward 2.5 --dry-run` | TP calculated as: `entry + (entry × stop%) × RR` | ☐ |
| 2.3.2 | Verify TP price: For $120 entry, 4% stop, 2.5 RR | TP should be $120 + ($4.80 × 2.5) = $132.00 | ☐ |
| 2.3.3 | Execute and verify in dashboard | Order legs match calculated prices | ☐ |
| 2.3.4 | Cancel order | Order cancels cleanly | ☐ |

**Sign-off:** _________________ Date: _______

---

## Phase 3: Risk Management (Critical)

### 3.1 Daily Loss Limit
| # | Step | Expected Result | Status |
|---|------|-----------------|--------|
| 3.1.1 | Check current daily P&L in Alpaca | Note starting equity | ☐ |
| 3.1.2 | If needed, manually place losing trades to approach 5% loss | (Skip if already near limit from prior testing) | ☐ |
| 3.1.3 | Attempt to place new order via bot when near limit | Bot should display: "Trading halted: Daily loss limit reached" | ☐ |
| 3.1.4 | Verify no order was submitted | Check Alpaca dashboard - no new orders | ☐ |

### 3.2 Position Size Cap
| # | Step | Expected Result | Status |
|---|------|-----------------|--------|
| 3.2.1 | Set `--qty 999999` with a valid symbol/entry | Bot should calculate and cap position to max_position_size_pct (default 10%) | ☐ |
| 3.2.2 | Calculate expected max: `equity × 10% / entry_price` | Verify bot doesn't exceed this | ☐ |

**Sign-off:** _________________ Date: _______

---

## Phase 4: Market Hours & Queueing

| # | Test | Action | Expected Result | Status |
|---|------|--------|-----------------|--------|
| 4.1 | After-hours submission | Run bot when market closed (4:01 PM ET or weekend) | Bot warns "Market is closed" and prompts y/N | ☐ |
| 4.2 | Queue behavior | Respond 'y' to proceed | Order submits with status "pending_new" or "accepted" | ☐ |
| 4.3 | Auto-confirm flag | Run with `--yes` flag after hours | Bot proceeds without prompt, warns about queueing | ☐ |
| 4.4 | Cancel queued order | Cancel via dashboard | Order cancels while queued, no errors | ☐ |

**Sign-off:** _________________ Date: _______

---

## Phase 5: Error Handling & Edge Cases

| # | Test | Action | Expected Result | Status |
|---|------|--------|-----------------|--------|
| 5.1 | Invalid symbol | `python run_stop_bot.py --symbol INVALID123 --entry 100.00` | API error handled gracefully, clear error message | ☐ |
| 5.2 | Negative price | `python run_stop_bot.py --symbol AAPL --entry -50.00` | Validation error, order not submitted | ☐ |
| 5.3 | Zero quantity | `python run_stop_bot.py --symbol AAPL --entry 150.00 --qty 0` | Validation error or calculated qty used | ☐ |
| 5.4 | Stop above entry | `python run_stop_bot.py --symbol AAPL --entry 150.00 --stop-pct -5.0` | Validation error (stop would be above entry) | ☐ |
| 5.5 | Insufficient funds | Attempt order exceeding buying power | API error handled gracefully | ☐ |
| 5.6 | Network interruption | Disconnect network mid-order submission | Bot times out, doesn't hang indefinitely | ☐ |

**Sign-off:** _________________ Date: _______

---

## Phase 6: Multi-Day Paper Trading (Recommended)

Run the bot over 3-5 market days with small test orders:

| Day | Action | Verify |
|-----|--------|--------|
| 1 | Place 2-3 bracket orders | All orders appear correctly in dashboard |
| 1 | Let one order fill, observe breakeven | Stop-loss moves to entry after 50% TP reached |
| 2 | Test trailing stop in volatile stock | Trailing stop adjusts with price movement |
| 3 | Test stop-loss trigger | Position closes when stop hit, no orphaned orders |
| 4 | Test take-profit trigger | Position closes when TP hit, all legs cancelled |
| 5 | Review all logs | Every action is logged with timestamp and order ID |

**Daily checklist:**
- ☐ No unexpected errors in console
- ☐ All orders have matching order IDs in logs
- ☐ Position sizes match risk parameters
- ☐ No duplicate orders submitted
- ☐ Daily loss limit respected

**Sign-off:** _________________ Date: _______

---

## Phase 7: Final Validation

| # | Item | Criteria | Status |
|---|------|----------|--------|
| 7.1 | All Phase 1 tests | Pass | ☐ |
| 7.2 | All Phase 2 tests | Pass | ☐ |
| 7.3 | All Phase 3 tests | Pass | ☐ |
| 7.4 | All Phase 4 tests | Pass | ☐ |
| 7.5 | All Phase 5 tests | Pass | ☐ |
| 7.6 | Multi-day run | 3+ days without critical errors | ☐ |
| 7.7 | Documentation | README and CLAUDE.md updated with findings | ☐ |

---

## UAT Completion Sign-off

**Tester Name:** _______________________________

**Date Started:** _______________________________

**Date Completed:** _____________________________

**Critical Issues Found:**
- 
- 
- 

**Recommendations:**
- 
- 

**Approved for Live Trading:** ☐ Yes  ☐ No (continue paper trading)

**Signature:** _______________________________

---

## Quick Reference: Test Commands

```bash
# Dry run examples
python run_stop_bot.py --symbol AAPL --entry 150.00 --dry-run
python run_stop_bot.py --symbol TSLA --entry 250.00 --trailing 3.0 --dry-run
python run_stop_bot.py --symbol NVDA --entry 120.00 --risk-reward 2.5 --dry-run

# Live execution with auto-confirm
python run_stop_bot.py --symbol AAPL --entry 150.00 --yes

# Verify account
python alpaca_connect_test.py

# Run unit tests
pytest tests/test_stop_strategy_bot.py -v
```

---

**Note:** This UAT checklist must be completed and signed off before transitioning from paper to live trading.
