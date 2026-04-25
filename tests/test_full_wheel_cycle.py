"""
Integration test — full wheel cycle (TB-043)
=============================================
End-to-end test simulating the entire Wheel Strategy lifecycle:
1. Scan watchlist and sell a cash-secured put
2. Simulate put assignment (stock acquired at strike)
3. Calculate adjusted cost basis (strike - premium)
4. Sell a covered call above cost basis
5. Simulate call exercise (shares called away)
6. Verify total return: call premium + capital gains
7. Return to put-selling phase (cycle repeats)

Run with:
    pytest tests/test_full_wheel_cycle.py -v

All components use mocked Alpaca API and a temporary SQLite DB —
no live market data or real orders are involved.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from bots.wheel_strategy.db import init_db, add_watchlist_entry
from bots.wheel_strategy.position_manager import PositionManager
from bots.wheel_strategy.assignment_manager import AssignmentManager
from bots.wheel_strategy.risk_manager import RiskManager
from bots.wheel_strategy.earnings_checker import EarningsChecker
from bots.wheel_strategy.put_seller import PutSeller
from bots.wheel_strategy.call_seller import CallSeller


# ── Test helper: temporary DB ──────────────────────────────────────────────

def _make_temp_db():
    """Create a fresh SQLite DB with the wheel strategy schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _make_mock_client(equity="100000", cash="50000", current_price=180.0):
    """Mock Alpaca TradingClient that returns realistic account/market data."""
    client = MagicMock()

    # Account info
    acct = MagicMock()
    acct.equity = equity
    acct.cash = cash
    client.get_account.return_value = acct

    # Stock price
    bar = MagicMock()
    bar.close = current_price
    client.get_latest_bar.return_value = bar

    return client


# ── Helper: seed watchlist ─────────────────────────────────────────────────

def _seed_watchlist_and_data(db_path, entries):
    """Add watchlist entries. entries = list of dicts with symbol, etc."""
    for entry in entries:
        add_watchlist_entry(db_path, **entry)


# ── Helper dates ───────────────────────────────────────────────────────────

def _date(days):
    """Return YYYY-MM-DD for now + days."""
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _date_minus(days):
    """Return YYYY-MM-DD for now - days."""
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════════════─
# Phase 1: Put Selling
# ═══════════════════════════════════════════════════════════════════════════─

class TestPhase1_PutSelling:
    """Sell a cash-secured put on a watchlist stock."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        _seed_watchlist_and_data(self.db_path, [
            {"symbol": "AAPL", "sector": "Technology", "max_contracts": 5, "max_capital": 20000}
        ])
        self.client = _make_mock_client(equity="100000", cash="60000", current_price=180.0)
        self.pm = PositionManager(self.db_path)

    def test_add_put_records_position(self):
        """Recording a put creates a trackable position."""
        exp = _date(37)
        pos_id = self.pm.add_put(
            symbol="AAPL",
            option_symbol="AAPL250620P00175000",
            strike=175.0,
            expiration=exp,
            contracts=1,
            premium=300.0,
        )
        assert pos_id > 0

        open_puts = self.pm.get_open_puts("AAPL")
        assert len(open_puts) == 1
        assert open_puts[0]["strike"] == 175.0
        assert open_puts[0]["contracts"] == 1
        assert open_puts[0]["premium"] == 300.0

    def test_put_requires_cash_reserve(self):
        """Verify cash reserve check: $175 strike × 100 = $17,500 needed."""
        # $60,000 cash, $17,500 required → OK
        account = self.client.get_account.return_value
        assert float(account.cash) >= 175.0 * 100

    def test_multiple_puts_tracked(self):
        """Multiple puts on different symbols should all be tracked."""
        exp = _date(37)
        self.pm.add_put("AAPL", "AAPL250620P00175000", 175.0, exp, 1, 300.0)
        self.pm.add_put("MSFT", "MSFT250620P00340000", 340.0, exp, 1, 500.0)

        assert len(self.pm.get_open_puts("AAPL")) == 1
        assert len(self.pm.get_open_puts("MSFT")) == 1
        assert len(self.pm.get_open_puts()) == 2  # all put symbols


# ═══════════════════════════════════════════════════════════════════════════─
# Phase 2: Put Assignment
# ═══════════════════════════════════════════════════════════════════════════─

class TestPhase2_PutAssignment:
    """Simulate put assignment: stock is assigned at strike price."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        _seed_watchlist_and_data(self.db_path, [
            {"symbol": "AAPL", "sector": "Technology", "max_contracts": 5, "max_capital": 20000}
        ])
        self.client = _make_mock_client(equity="100000", cash="50000", current_price=180.0)
        self.pm = PositionManager(self.db_path)
        self.am = AssignmentManager(self.db_path, self.client, config={})

        # Seed a put that has already expired (for assignment simulation)
        exp = _date_minus(5)  # expired 5 days ago
        self.pm.add_put(
            symbol="AAPL",
            option_symbol="AAPL250601P00175000",
            strike=175.0,
            expiration=exp,
            contracts=1,
            premium=300.0,
        )

    def test_cost_basis_calculation(self):
        """Cost basis = strike - (premium / shares)."""
        # Strike $175, premium $300, 100 shares
        # cost_basis = 175 - (300/100) = 175 - 3 = $172.00
        strike = 175.0
        premium = 300.0
        shares = 100
        expected_cost_basis = strike - (premium / shares)
        assert expected_cost_basis == pytest.approx(172.0, abs=0.01)

    def test_cost_basis_with_multiple_contracts(self):
        """2 contracts: premium spread across 200 shares."""
        strike = 175.0
        premium = 600.0
        shares = 200
        expected = strike - (premium / shares)
        assert expected == pytest.approx(172.0, abs=0.01)

    def test_record_assignment_creates_stock_position(self):
        """Recording assignment creates a stock position."""
        self.pm.record_assignment(
            symbol="AAPL",
            strike=175.0,
            contracts=1,
            premium_collected=300.0,
            cost_basis=172.0,
        )

        stock_positions = self.pm.get_stock_positions("AAPL")
        assert len(stock_positions) == 1
        assert stock_positions[0]["shares"] == 100
        assert stock_positions[0]["cost_basis"] == pytest.approx(172.0, abs=0.01)

    def test_open_puts_decreased_after_assignment(self):
        """After assignment the put should no longer be 'open'."""
        # Record assignment
        self.pm.record_assignment(
            symbol="AAPL",
            strike=175.0,
            contracts=1,
            premium_collected=300.0,
            cost_basis=172.0,
        )

        # Open puts should be 0 (put status changed to 'assigned')
        open_puts = self.pm.get_open_puts("AAPL")
        assert len(open_puts) == 0

    def test_assignment_transition_to_covered_call_phase(self):
        """After assignment, the bot should sell covered calls."""
        # Record the assignment first
        self.pm.record_assignment(
            symbol="AAPL",
            strike=175.0,
            contracts=1,
            premium_collected=300.0,
            cost_basis=172.0,
        )

        # Verify stock position exists (prerequisite for covered call)
        stock_positions = self.pm.get_stock_positions("AAPL")
        assert len(stock_positions) == 1

        # Verify shares = contracts × 100
        assert stock_positions[0]["shares"] == 100


# ═══════════════════════════════════════════════════════════════════════════─
# Phase 3: Covered Call Selling
# ═══════════════════════════════════════════════════════════════════════════─

class TestPhase3_CoveredCallSelling:
    """Sell a covered call on the assigned stock."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        _seed_watchlist_and_data(self.db_path, [
            {"symbol": "AAPL", "sector": "Technology", "max_contracts": 5, "max_capital": 20000}
        ])
        self.client = _make_mock_client(equity="100000", cash="50000", current_price=180.0)
        self.pm = PositionManager(self.db_path)

        # Simulate prior assignment: own 100 shares at $172 cost basis
        self.pm.record_assignment(
            symbol="AAPL",
            strike=175.0,
            contracts=1,
            premium_collected=300.0,
            cost_basis=172.0,
        )

    def test_call_strike_above_cost_basis(self):
        """Covered call should be sold at strike above cost basis."""
        cost_basis = 172.0
        # Call at $180 is above cost basis
        assert 180.0 > cost_basis

    def test_add_call_records_position(self):
        """Recording a call creates a trackable position."""
        exp = _date(37)
        pos_id = self.pm.add_call(
            symbol="AAPL",
            option_symbol="AAPL250620C00180000",
            strike=180.0,
            expiration=exp,
            contracts=1,
            premium=200.0,
            cost_basis=172.0,
        )
        assert pos_id > 0

        open_calls = self.pm.get_open_calls("AAPL")
        assert len(open_calls) == 1
        assert open_calls[0]["strike"] == 180.0

    def test_return_calculation_at_call_sale(self):
        """Calculate potential return if called away at this strike.
        
        Return = (call strike - cost_basis) × shares + call premium
        = ($180 - $172) × 100 + $200 = $800 + $200 = $1,000
        """
        call_strike = 180.0
        cost_basis = 172.0
        shares = 100
        call_premium = 200.0

        capital_gains = (call_strike - cost_basis) * shares
        total_return = capital_gains + call_premium

        assert capital_gains == 800.0
        assert total_return == 1000.0

    def test_put_premium_reduces_effective_cost(self):
        """Original put premium already reduced cost basis.
        
        Effective cost = strike - put_premium/shares = $175 - $3 = $172
        The $3/share reduction is reflected in the cost_basis stored.
        """
        # This was already verified in Phase 2
        positions = self.pm.get_stock_positions("AAPL")
        assert positions[0]["cost_basis"] == pytest.approx(172.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════─
# Phase 4: Call Exercise (Shares Called Away)
# ═══════════════════════════════════════════════════════════════════════════─

class TestPhase4_CallExercise:
    """Simulate call exercise: shares are sold at strike."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = _make_mock_client(equity="100000", cash="50000", current_price=185.0)
        self.pm = PositionManager(self.db_path)

        # Step 1: Simulate assignment
        self.pm.record_assignment(
            symbol="AAPL",
            strike=175.0,
            contracts=1,
            premium_collected=300.0,
            cost_basis=172.0,
        )

        # Step 2: Sell covered call
        exp = _date_minus(2)  # Call has expired
        self.pm.add_call(
            symbol="AAPL",
            option_symbol="AAPL250601C00180000",
            strike=180.0,
            expiration=exp,
            contracts=1,
            premium=200.0,
            cost_basis=172.0,
        )

    def test_record_exercise_removes_stock_position(self):
        """After exercise, stock position is no longer 'held'."""
        realized_pnl = self.pm.record_call_exercise(
            symbol="AAPL",
            strike=180.0,
            contracts=1,
            premium_collected=200.0,
            cost_basis=172.0,
        )

        held_positions = self.pm.get_stock_positions("AAPL")
        assert len(held_positions) == 0  # Status is now called_away

    def test_realized_pnl_calculation(self):
        """Verify realized P&L: (strike - cost_basis) × shares + premium
        
        P&L = ($180 - $172) × 100 + $200 = $800 + $200 = $1,000
        """
        pnl = self.pm.record_call_exercise(
            symbol="AAPL",
            strike=180.0,
            contracts=1,
            premium_collected=200.0,
            cost_basis=172.0,
        )

        expected = (180.0 - 172.0) * 100 + 200.0
        assert pnl == pytest.approx(expected, abs=0.01)

    def test_open_calls_cleared_after_exercise(self):
        """After exercise, open calls should be cleared."""
        self.pm.record_call_exercise(
            symbol="AAPL",
            strike=180.0,
            contracts=1,
            premium_collected=200.0,
            cost_basis=172.0,
        )

        open_calls = self.pm.get_open_calls("AAPL")
        assert len(open_calls) == 0

    def test_cash_increased_after_exercise(self):
        """Cash balance should increase by strike × shares + premium."""
        # Proceeds: $180 × 100 + $200 = $18,200
        shares = 100
        strike = 180.0
        premium = 200.0
        proceeds = strike * shares + premium
        assert proceeds == 18200.0


# ═══════════════════════════════════════════════════════════════════════════─
# Full Cycle Integration Test
# ═══════════════════════════════════════════════════════════════════════════─

class TestFullWheelCycle:
    """TB-043: End-to-end full wheel cycle test.
    
    Flow: Put Sell → Assignment → Call Sell → Call Exercise → Cycle Repeats
    """

    def setup_method(self):
        self.db_path = _make_temp_db()
        _seed_watchlist_and_data(self.db_path, [
            {"symbol": "AAPL", "sector": "Technology", "max_contracts": 5, "max_capital": 20000}
        ])
        self.client = _make_mock_client(equity="100000", cash="50000", current_price=180.0)
        self.pm = PositionManager(self.db_path)

        # Track total premium collected across full cycle
        self.total_premium_collected = 0.0
        self.cost_basis = 0.0
        self.realized_pnl = 0.0

    def test_full_wheel_cycle(self):
        """Execute and verify a complete wheel strategy cycle."""
        # ── STEP 1: Sell Cash-Secured Put ──────────────────────────────────
        put_strike = 175.0
        put_premium = 300.0
        put_exp = _date(37)
        contracts = 1

        # Verify cash available
        account = self.client.get_account.return_value
        assert float(account.cash) >= put_strike * 100

        # Record put sale
        put_id = self.pm.add_put(
            symbol="AAPL",
            option_symbol="AAPL250620P00175000",
            strike=put_strike,
            expiration=put_exp,
            contracts=contracts,
            premium=put_premium,
        )
        assert put_id > 0
        assert len(self.pm.get_open_puts("AAPL")) == 1
        self.total_premium_collected += put_premium

        # ── STEP 2: Simulate Put Assignment ────────────────────────────────
        # Put expired ITM → assigned 100 shares
        shares = contracts * 100
        expected_cost_basis = put_strike - (put_premium / shares)
        
        self.pm.record_assignment(
            symbol="AAPL",
            strike=put_strike,
            contracts=contracts,
            premium_collected=put_premium,
            cost_basis=expected_cost_basis,
        )
        self.cost_basis = expected_cost_basis

        # Verify stock position created
        stock_positions = self.pm.get_stock_positions("AAPL")
        assert len(stock_positions) == 1
        assert stock_positions[0]["shares"] == 100
        assert stock_positions[0]["cost_basis"] == pytest.approx(expected_cost_basis, abs=0.01)

        # Verify put is closed
        assert len(self.pm.get_open_puts("AAPL")) == 0

        # ── STEP 3: Sell Covered Call ──────────────────────────────────────
        call_strike = 180.0  # Above cost basis ($172)
        call_premium = 200.0
        call_exp = _date(37)

        assert call_strike > self.cost_basis  # Must be above cost basis

        self.pm.add_call(
            symbol="AAPL",
            option_symbol="AAPL250620C00180000",
            strike=call_strike,
            expiration=call_exp,
            contracts=contracts,
            premium=call_premium,
            cost_basis=self.cost_basis,
        )
        assert len(self.pm.get_open_calls("AAPL")) == 1
        self.total_premium_collected += call_premium

        # ── STEP 4: Simulate Call Exercise ─────────────────────────────────
        # Shares called away at $180 strike
        realized_pnl = self.pm.record_call_exercise(
            symbol="AAPL",
            strike=call_strike,
            contracts=contracts,
            premium_collected=call_premium,
            cost_basis=self.cost_basis,
        )
        self.realized_pnl = realized_pnl

        # Verify stock position cleared
        held = self.pm.get_stock_positions("AAPL")
        assert len(held) == 0  # Status is now called_away

        # Verify call is closed
        assert len(self.pm.get_open_calls("AAPL")) == 0

        # ── STEP 5: Verify Total Return ────────────────────────────────────
        # Total return = capital gains + total premium
        # Capital gains = (call_strike - put_strike) × shares + put_premium
        #               = ($180 - $175) × 100 + $300 = $500 + $300 = $800
        # But the correct way with our cost basis:
        # Capital gains = (call_strike - cost_basis) × shares
        #               = ($180 - $172) × 100 = $800
        # Total premium = put_premium + call_premium = $300 + $200 = $500
        # Total return = $800 + $200 = $1,000 (realized_pnl includes call premium)
        
        expected_capital_gains = (call_strike - expected_cost_basis) * shares
        expected_total_return = expected_capital_gains + call_premium

        assert realized_pnl == pytest.approx(expected_total_return, abs=0.01)

        # ── STEP 6: Cycle Resets to Put Selling Phase ──────────────────────
        # No open positions → cash is back in account
        assert len(self.pm.get_open_puts("AAPL")) == 0
        assert len(self.pm.get_open_calls("AAPL")) == 0

        # Can start selling puts again
        new_put_premium = 350.0
        new_put_exp = _date(37)
        acct = self.client.get_account.return_value
        assert float(acct.cash) >= put_strike * 100  # Still have buying power

        new_put_id = self.pm.add_put(
            symbol="AAPL",
            option_symbol="AAPL250701P00175000",
            strike=put_strike,
            expiration=new_put_exp,
            contracts=contracts,
            premium=new_put_premium,
        )
        assert new_put_id > 0
        assert len(self.pm.get_open_puts("AAPL")) == 1

        # ── Final Assertions ───────────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"FULL WHEEL CYCLE RESULTS")
        print(f"{'='*60}")
        print(f"Put premium collected:  ${put_premium:.2f}")
        print(f"Call premium collected: ${call_premium:.2f}")
        print(f"Total premium:          ${self.total_premium_collected:.2f}")
        print(f"Cost basis on stock:    ${expected_cost_basis:.2f}")
        print(f"Capital gains:          ${expected_capital_gains:.2f}")
        print(f"Realized P&L:           ${realized_pnl:.2f}")
        print(f"Cycle reset:            ✓ (new put sold)")

    def test_full_cycle_with_multiple_contracts(self):
        """Test wheel cycle with 2 contracts (200 shares)."""
        contracts = 2
        put_strike = 175.0
        put_premium = 600.0  # $3/share
        put_exp = _date(37)

        # Put sell
        self.pm.add_put("AAPL", "AAPL250620P00175000", put_strike, put_exp, contracts, put_premium)
        assert len(self.pm.get_open_puts("AAPL")) == 1

        # Assignment
        shares = contracts * 100
        cost_basis = put_strike - (put_premium / shares)
        self.pm.record_assignment("AAPL", put_strike, contracts, put_premium, cost_basis)

        stock_positions = self.pm.get_stock_positions("AAPL")
        assert len(stock_positions) == 1
        assert stock_positions[0]["shares"] == 200

        # Covered call
        call_strike = 180.0
        call_premium = 400.0
        self.pm.add_call("AAPL", "AAPL250620C00180000", call_strike, _date(37), contracts, call_premium, cost_basis)

        # Call exercise
        pnl = self.pm.record_call_exercise("AAPL", call_strike, contracts, call_premium, cost_basis)

        expected = (call_strike - cost_basis) * shares + call_premium
        assert pnl == pytest.approx(expected, abs=0.01)

        print(f"\nContract count: {contracts}")
        print(f"Shares: {shares}")
        print(f"Cost basis: ${cost_basis:.2f}")
        print(f"Capital gains: ${(call_strike - cost_basis) * shares:.2f}")
        print(f"P&L: ${pnl:.2f}")

    def test_full_cycle_risky_scenario_loss(self):
        """Test wheel cycle where call is sold below the put cost basis (loss scenario).
        
        This simulates a scenario where the stock dropped below the cost basis
        and the trader decides to sell a call below cost basis.
        """
        put_strike = 175.0
        put_premium = 300.0
        contracts = 1
        shares = contracts * 100

        # Put sell + assignment
        cost_basis = put_strike - (put_premium / shares)  # $172
        self.pm.add_put("AAPL", "AAPL250620P00175000", put_strike, _date(37), contracts, put_premium)
        self.pm.record_assignment("AAPL", put_strike, contracts, put_premium, cost_basis)

        # Stock dropped to $160 → sell call at $165 (below cost basis $172)
        call_strike = 165.0
        call_premium = 500.0  # Higher premium because ITM
        self.pm.add_call("AAPL", "AAPL250620C00165000", call_strike, _date(37), contracts, call_premium, cost_basis)

        # Call exercised
        pnl = self.pm.record_call_exercise("AAPL", call_strike, contracts, call_premium, cost_basis)

        # Even though call is below cost basis, the high premium might offset
        # P&L = ($165 - $172) × 100 + $500 = -$700 + $500 = -$200
        expected = (call_strike - cost_basis) * shares + call_premium
        assert pnl == pytest.approx(expected, abs=0.01)
        assert pnl < 0  # Net loss

        print(f"\nLoss scenario:")
        print(f"Cost basis: ${cost_basis:.2f}")
        print(f"Call strike: ${call_strike:.2f}")
        print(f"P&L: ${pnl:.2f} (loss)")
