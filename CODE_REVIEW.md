## Trading-Bot Code Review Summary

**Repository:** `/opt/hermes/trading-bot`
**Scope:** Stop Strategy Bot implementation + shared infrastructure
**Date:** $(date +%Y-%m-%d)

---

### 🔴 Critical Issues

**1. No `.env` file present - Credentials need setup**
- `core/alpaca_client.py` expects `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` from environment
- Copy `.env.example` → `.env` and add real keys before running

**2. Order Validation Gap in `order_placer.py`**
- `place_bracket_order()` validates market is open but allows order if `--yes` flag is set (line 44)
- Validation happens AFTER checking qty/stop prices but BEFORE checking account buying power
- **Risk:** Could submit orders that immediately fail due to insufficient funds

**3. SQL Injection Risk in Order Monitor (Low severity - local SQLite only)**
- `order_monitor.py` lines 221-232: Uses parameterized queries correctly ✓
- But lines 242-244: Same - uses parameters correctly

---

### ⚠️ Warnings

**1. Position Sizing Bug in `run_stop_bot.py`**
```python
# Line 108 - WRONG:
risk_pct = stop_config.get("max_position_size_pct", 10.0) / 100
```
This uses `max_position_size_pct` (e.g., 10%) as risk per trade. Should be using separate `risk_per_trade_pct` config. Currently risking 10% of account per trade, not the intended fraction.

**2. Race Condition in Order State Management**
- `order_monitor.py` keeps state in memory (`self._state`) AND in SQLite
- If bot crashes between DB write and memory update, state is inconsistent
- Recovery (`load_state_from_db`) only happens on init, not mid-operation

**3. Missing Error Handling for API Failures**
- `risk_manager.py` line 35: `client.get_account()` can fail (API error, network) - not wrapped in try/except
- Throughout codebase: Alpaca API errors bubble up unhandled

**4. Trailing Stop Gap**
- `place_bracket_order()` switches to trailing stop (lines 82-89) but doesn't set a take-profit for trailing stop orders
- Trailing stops have no profit target - could ride up then all the way back down

---

### 💡 Suggestions

**1. Add Configuration Validation**
- `config/settings.yaml` exists but no schema validation on load
- Invalid configs fail silently with defaults

**2. Logging Instead of Print Statements**
- `run_stop_bot.py` uses `print()` for all output
- Should use `core/logger.py` for structured logging consistently

**3. Order ID Tracking**
- Bracket orders generate multiple order IDs (entry, stop, take-profit)
- Only parent ID tracked - lose visibility into leg status

**4. Add Unit Tests for Edge Cases**
- Tests exist but missing coverage for:
  - Partial fills
  - API rate limiting
  - Network timeouts
  - Empty market data responses

**5. Use Type Hints Consistently**
- Some functions missing return type hints (e.g., `position_sizer.py:apply_position_cap`)

---

### ✅ Looks Good

1. **Clean separation of concerns** - Core, bots, tests structure is logical
2. **Good test coverage** - 43 tests covering main functionality with mocks
3. **Risk management implemented** - Daily loss limits, position caps, paper trading default
4. **State persistence** - SQLite for order tracking across restarts
5. **Breakeven stop logic** - Properly moves stop to entry + fees at 50% of TP
6. **Input validation** - Solid validation on prices, percentages, quantities
7. **ATR calculation** - Proper true range formula implementation

---

### Immediate Actions Recommended

```bash
# 1. Create .env file
cp .env.example .env
# Edit .env with real Alpaca paper trading keys

# 2. Fix position sizing bug
# In run_stop_bot.py line 108, add separate risk_per_trade_pct to config

# 3. Run tests to verify everything works
pytest tests/test_stop_strategy_bot.py -v

# 4. Test API connection
python alpaca_connect_test.py
```

**Overall Assessment:** Good foundation with solid architecture. Critical fixes needed around config and error handling before live trading. Paper trading ready with caveats.
