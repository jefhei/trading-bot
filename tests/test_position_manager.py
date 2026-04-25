"""
Tests for position management (TB-037 / FR-6)
===============================================
Test: CRUD for open options positions, stock position cost basis tracking and
adjustments, total premium collection per position and overall, return metric
calculations (annualized ROIC, yield on cost). Position tracker must persist
to SQLite.

Run with:
    pytest tests/test_position_manager.py -v

All tests are fully isolated — no live API calls are made.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from bots.wheel_strategy.position_manager import PositionManager
from bots.wheel_strategy.db import init_db


# ── Helper fixtures ────────────────────────────────────────────────────────

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _make_pm():
    return PositionManager(_make_temp_db())


# ============================================================================
# CRUD — Open Options Positions (Puts)
# ============================================================================

class TestOpenOptionsCRUDPuts:
    """FR-6: CRUD for open options positions — puts."""

    def setup_method(self):
        self.pm = _make_pm()

    def test_add_put_returns_position_id(self):
        pos_id = self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        assert isinstance(pos_id, int)
        assert pos_id >= 1

    def test_get_open_puts_returns_newly_added(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        puts = self.pm.get_open_puts()
        assert len(puts) == 1
        assert puts[0]["symbol"] == "AAPL"
        assert puts[0]["strike"] == 180.0
        assert puts[0]["contracts"] == 1
        assert puts[0]["premium"] == 300.0

    def test_get_open_puts_filtered_by_symbol(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        self.pm.add_put(
            symbol="MSFT", option_symbol="MSFT240615P00380000",
            strike=380.0, expiration="2024-06-15",
            contracts=1, premium=400.0,
        )
        aapl_puts = self.pm.get_open_puts("AAPL")
        assert len(aapl_puts) == 1
        assert aapl_puts[0]["symbol"] == "AAPL"

    def test_get_open_puts_empty(self):
        assert self.pm.get_open_puts() == []

    def test_multiple_puts_for_same_symbol(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL241215P00175000",
            strike=175.0, expiration="2024-12-15",
            contracts=2, premium=500.0,
        )
        puts = self.pm.get_open_puts("AAPL")
        assert len(puts) == 2

    def test_put_has_correct_contract_type(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        puts = self.pm.get_open_puts("AAPL")
        assert puts[0]["contract_type"] == "PUT"


# ============================================================================
# CRUD — Open Options Positions (Calls)
# ============================================================================

class TestOpenOptionsCRUDCalls:

    def setup_method(self):
        self.pm = _make_pm()

    def test_add_call_returns_position_id(self):
        pos_id = self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        assert isinstance(pos_id, int)
        assert pos_id >= 1

    def test_get_open_calls_returns_newly_added(self):
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        calls = self.pm.get_open_calls()
        assert len(calls) == 1
        assert calls[0]["strike"] == 190.0
        assert calls[0]["premium"] == 200.0

    def test_get_open_calls_filtered_by_symbol(self):
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        self.pm.add_call(
            symbol="MSFT", option_symbol="MSFT240715C00400000",
            strike=400.0, expiration="2024-07-15",
            contracts=1, premium=250.0, cost_basis=380.0,
        )
        msft_calls = self.pm.get_open_calls("MSFT")
        assert len(msft_calls) == 1
        assert msft_calls[0]["symbol"] == "MSFT"

    def test_get_open_calls_empty(self):
        assert self.pm.get_open_calls() == []

    def test_call_has_correct_contract_type(self):
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        calls = self.pm.get_open_calls("AAPL")
        assert calls[0]["contract_type"] == "CALL"

    def test_call_stores_cost_basis(self):
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.50,
        )
        calls = self.pm.get_open_calls("AAPL")
        assert calls[0]["cost_basis"] == pytest.approx(177.50, abs=0.01)


# ============================================================================
# CRUD — Stock Positions
# ============================================================================

class TestStockPositionsCRUD:

    def setup_method(self):
        self.pm = _make_pm()

    def _setup_assignment(self, symbol="AAPL", strike=180.0, contracts=1, premium=300.0):
        self.pm.add_put(
            symbol=symbol, option_symbol=f"{symbol}240615P00{int(strike):03d}000",
            strike=strike, expiration="2024-06-14",
            contracts=contracts, premium=premium,
        )
        cost_basis = strike - (premium / (contracts * 100))
        self.pm.record_assignment(symbol, strike, contracts, premium, cost_basis)

    def test_get_stock_positions_returns_assigned_stock(self):
        self._setup_assignment()
        stocks = self.pm.get_stock_positions("AAPL")
        assert len(stocks) == 1
        assert stocks[0]["symbol"] == "AAPL"
        assert stocks[0]["shares"] == 100

    def test_get_stock_positions_empty(self):
        assert self.pm.get_stock_positions() == []

    def test_stock_position_has_cost_basis(self):
        self._setup_assignment(strike=180.0, contracts=1, premium=300.0)
        stocks = self.pm.get_stock_positions("AAPL")
        # cost_basis = 180 - 300/100 = 177
        assert stocks[0]["cost_basis"] == pytest.approx(177.0, abs=0.01)

    def test_stock_position_shares_matches_contracts(self):
        self._setup_assignment(contracts=3)
        stocks = self.pm.get_stock_positions("AAPL")
        assert stocks[0]["shares"] == 300

    def test_multiple_stock_positions_same_symbol(self):
        """Two separate assignments for the same symbol."""
        self._setup_assignment(strike=180.0, contracts=1, premium=300.0)
        self._setup_assignment(strike=175.0, contracts=1, premium=250.0)
        stocks = self.pm.get_stock_positions("AAPL")
        assert len(stocks) == 2

    def test_stock_position_status_is_held(self):
        self._setup_assignment()
        stocks = self.pm.get_stock_positions("AAPL")
        assert stocks[0]["status"] == "held"

    def test_stock_position_has_premium_collected(self):
        self._setup_assignment(premium=350.0)
        stocks = self.pm.get_stock_positions("AAPL")
        assert stocks[0]["premium_collected"] == pytest.approx(350.0, abs=0.01)


# ============================================================================
# Premium Collection — Per Position and Overall
# ============================================================================

class TestPremiumCollection:

    def setup_method(self):
        self.pm = _make_pm()

    def test_premium_summary_empty(self):
        summary = self.pm.get_premium_summary()
        assert summary["total_premium_collected"] == 0.0
        assert summary["total_realized_pnl"] == 0.0
        assert summary["total_return"] == 0.0

    def test_premium_from_single_put(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        summary = self.pm.get_premium_summary()
        # put_sold: 300
        assert summary["total_premium_collected"] == pytest.approx(300.0, abs=0.01)

    def test_premium_from_multiple_puts(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        self.pm.add_put(
            symbol="MSFT", option_symbol="MSFT240615P00380000",
            strike=380.0, expiration="2024-06-15",
            contracts=1, premium=450.0,
        )
        summary = self.pm.get_premium_summary()
        # put_sold: 300 + 450 = 750
        assert summary["total_premium_collected"] == pytest.approx(750.0, abs=0.01)

    def test_premium_from_puts_and_calls(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        summary = self.pm.get_premium_summary()
        # put_sold: 300 + call_sold: 200 = 500
        assert summary["total_premium_collected"] == pytest.approx(500.0, abs=0.01)

    def test_premium_after_assignment_includes_both(self):
        """After assignment, put_sold + put_assigned both record premium."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        cost_basis = 177.0
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, cost_basis)

        summary = self.pm.get_premium_summary()
        # put_sold: 300 + put_assigned: 300 = 600
        assert summary["total_premium_collected"] == pytest.approx(600.0, abs=0.01)

    def test_total_return_formula(self):
        """total_return = total_premium + total_realized_pnl."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        self.pm.record_call_exercise("AAPL", 190.0, 1, 200.0, 177.0)

        summary = self.pm.get_premium_summary()
        expected_return = summary["total_premium_collected"] + summary["total_realized_pnl"]
        assert summary["total_return"] == pytest.approx(expected_return, abs=0.01)


# ============================================================================
# Cost Basis Tracking and Adjustments
# ============================================================================

class TestCostBasisTracking:

    def setup_method(self):
        self.pm = _make_pm()

    def test_cost_basis_from_single_assignment(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)
        stocks = self.pm.get_stock_positions("AAPL")
        assert stocks[0]["cost_basis"] == pytest.approx(177.0, abs=0.01)

    def test_cost_basis_from_high_premium(self):
        """Higher premium → lower cost basis."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=500.0,
        )
        self.pm.record_assignment("AAPL", 180.0, 1, 500.0, 175.0)
        stocks = self.pm.get_stock_positions("AAPL")
        assert stocks[0]["cost_basis"] == pytest.approx(175.0, abs=0.01)

    def test_cost_basis_multiple_contracts(self):
        """Premium is spread across all shares."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=2, premium=600.0,
        )
        self.pm.record_assignment("AAPL", 180.0, 2, 600.0, 177.0)
        stocks = self.pm.get_stock_positions("AAPL")
        assert stocks[0]["cost_basis"] == pytest.approx(177.0, abs=0.01)
        assert stocks[0]["shares"] == 200

    def test_cost_basis_persists_across_new_position_manager(self):
        """Cost basis survives in SQLite DB."""
        db_path = _make_temp_db()
        pm1 = PositionManager(db_path)
        pm1.add_put(
            symbol="MSFT", option_symbol="MSFT240615P00380000",
            strike=380.0, expiration="2024-06-15",
            contracts=1, premium=400.0,
        )
        pm1.record_assignment("MSFT", 380.0, 1, 400.0, 376.0)

        # Fresh PositionManager pointing to same DB
        pm2 = PositionManager(db_path)
        stocks = pm2.get_stock_positions("MSFT")
        assert len(stocks) == 1
        assert stocks[0]["cost_basis"] == pytest.approx(376.0, abs=0.01)

        os.unlink(db_path)


# ============================================================================
# Return Metric Calculations
# ============================================================================

class TestReturnMetrics:
    """Test annualized ROIC and yield on cost calculations."""

    def setup_method(self):
        self.pm = _make_pm()

    def _simulate_full_cycle(self, put_premium=300.0, call_premium=200.0,
                              put_strike=180.0, call_strike=190.0, contracts=1):
        """Set up a complete wheel cycle for metrics testing."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=put_strike, expiration="2024-06-14",
            contracts=contracts, premium=put_premium,
        )
        cost_basis = put_strike - (put_premium / (contracts * 100))
        self.pm.record_assignment("AAPL", put_strike, contracts, put_premium, cost_basis)
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=call_strike, expiration="2024-07-15",
            contracts=contracts, premium=call_premium, cost_basis=cost_basis,
        )
        self.pm.record_call_exercise("AAPL", call_strike, contracts, call_premium, cost_basis)

    def test_premium_summary_after_full_cycle(self):
        self._simulate_full_cycle(put_premium=300.0, call_premium=200.0)
        summary = self.pm.get_premium_summary()
        # put_sold:300 + put_assigned:300 + call_sold:200 + call_exercised:200 = 1000
        assert summary["total_premium_collected"] == pytest.approx(1000.0, abs=0.01)
        # realized_pnl = (190-177)*100 + 200 = 1500
        assert summary["total_realized_pnl"] == pytest.approx(1500.0, abs=0.01)
        assert summary["total_return"] == pytest.approx(2500.0, abs=0.01)

    def test_return_metrics_loss_cycle(self):
        """Cycle with strike below cost basis."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-14",
            contracts=1, premium=300.0,
        )
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00170000",
            strike=170.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        self.pm.record_call_exercise("AAPL", 170.0, 1, 200.0, 177.0)

        summary = self.pm.get_premium_summary()
        # put_sold:300 + put_assigned:300 + call_sold:200 + call_exercised:200 = 1000
        assert summary["total_premium_collected"] == pytest.approx(1000.0, abs=0.01)
        # realized_pnl = (170-177)*100 + 200 = -500
        assert summary["total_realized_pnl"] == pytest.approx(-500.0, abs=0.01)
        assert summary["total_return"] == pytest.approx(500.0, abs=0.01)

    def test_metrics_accumulate_across_cycles(self):
        """Two cycles: metrics should accumulate."""
        # Cycle 1
        self._simulate_full_cycle(put_premium=300.0, call_premium=200.0,
                                   put_strike=180.0, call_strike=190.0)
        # Cycle 2 (different symbol)
        self.pm.add_put(
            symbol="MSFT", option_symbol="MSFT240615P00370000",
            strike=370.0, expiration="2024-06-14",
            contracts=1, premium=400.0,
        )
        self.pm.record_assignment("MSFT", 370.0, 1, 400.0, 366.0)
        self.pm.add_call(
            symbol="MSFT", option_symbol="MSFT240715C00390000",
            strike=390.0, expiration="2024-07-15",
            contracts=1, premium=300.0, cost_basis=366.0,
        )
        self.pm.record_call_exercise("MSFT", 390.0, 1, 300.0, 366.0)

        summary = self.pm.get_premium_summary()
        # Cycle 1 premium: 1000, Cycle 2 premium: put_sold:400+put_assigned:400+call_sold:300+call_exercised:300 = 1400
        # total = 2400
        assert summary["total_premium_collected"] == pytest.approx(2400.0, abs=0.01)
        # Cycle 1 P&L: 1500, Cycle 2 P&L: (390-366)*100+300 = 2700
        assert summary["total_realized_pnl"] == pytest.approx(4200.0, abs=0.01)

    def test_empty_db_zero_metrics(self):
        """Fresh database should return zero for all metrics."""
        summary = self.pm.get_premium_summary()
        assert summary["total_premium_collected"] == 0.0
        assert summary["total_realized_pnl"] == 0.0
        assert summary["total_return"] == 0.0


# ============================================================================
# Record Roll
# ============================================================================

class TestRecordRoll:

    def setup_method(self):
        self.pm = _make_pm()

    def test_put_roll_closes_old_position(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        puts = self.pm.get_open_puts("AAPL")
        old_id = puts[0]["id"]

        self.pm.record_roll("AAPL", old_id, new_premium=350.0,
                            new_strike=175.0, new_expiration="2024-07-15")

        # Old put should be closed (not open)
        open_puts = self.pm.get_open_puts("AAPL")
        assert len(open_puts) == 0

    def test_put_roll_records_trade_history(self):
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        puts = self.pm.get_open_puts("AAPL")
        old_id = puts[0]["id"]

        self.pm.record_roll("AAPL", old_id, new_premium=350.0,
                            new_strike=175.0, new_expiration="2024-07-15",
                            notes="Rolling down and out")

        conn = sqlite3.connect(self.pm.db_path)
        cur = conn.execute(
            "SELECT event_type, strike, premium FROM wheel_trade_history WHERE event_type = 'put_rolled'"
        )
        rows = cur.fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][1] == 175.0
        assert rows[0][2] == 350.0

    def test_call_roll_closes_old_position(self):
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        calls = self.pm.get_open_calls("AAPL")
        old_id = calls[0]["id"]

        self.pm.record_roll("AAPL", old_id, new_premium=250.0,
                            new_strike=195.0, new_expiration="2024-08-15")

        open_calls = self.pm.get_open_calls("AAPL")
        assert len(open_calls) == 0

    def test_call_roll_records_call_rolled_event(self):
        self.pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        calls = self.pm.get_open_calls("AAPL")
        old_id = calls[0]["id"]

        self.pm.record_roll("AAPL", old_id, new_premium=250.0,
                            new_strike=195.0, new_expiration="2024-08-15")

        conn = sqlite3.connect(self.pm.db_path)
        cur = conn.execute(
            "SELECT event_type, strike, premium FROM wheel_trade_history WHERE event_type = 'call_rolled'"
        )
        rows = cur.fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][1] == 195.0
        assert rows[0][2] == 250.0

    def test_roll_with_nonexistent_id_is_noop(self):
        """Rolling a non-existent position should not crash."""
        self.pm.record_roll("AAPL", 99999, new_premium=350.0,
                            new_strike=175.0, new_expiration="2024-07-15")
        # Should not raise, and no trade should be recorded
        conn = sqlite3.connect(self.pm.db_path)
        cur = conn.execute("SELECT COUNT(*) FROM wheel_trade_history")
        count = cur.fetchone()[0]
        conn.close()
        assert count == 0


# ============================================================================
# SQLite Persistence
# ============================================================================

class TestSQLitePersistence:

    def test_positions_persist_across_connections(self):
        db_path = _make_temp_db()
        try:
            pm1 = PositionManager(db_path)
            pm1.add_put(
                symbol="AAPL", option_symbol="AAPL240615P00180000",
                strike=180.0, expiration="2024-06-15",
                contracts=1, premium=300.0,
            )

            # Fresh connection
            pm2 = PositionManager(db_path)
            puts = pm2.get_open_puts("AAPL")
            assert len(puts) == 1
            assert puts[0]["symbol"] == "AAPL"
            assert puts[0]["strike"] == 180.0
        finally:
            os.unlink(db_path)

    def test_stock_positions_persist(self):
        db_path = _make_temp_db()
        try:
            pm1 = PositionManager(db_path)
            pm1.add_put(
                symbol="AAPL", option_symbol="AAPL240615P00180000",
                strike=180.0, expiration="2024-06-15",
                contracts=1, premium=300.0,
            )
            pm1.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)

            pm2 = PositionManager(db_path)
            stocks = pm2.get_stock_positions("AAPL")
            assert len(stocks) == 1
            assert stocks[0]["shares"] == 100
            assert stocks[0]["cost_basis"] == pytest.approx(177.0, abs=0.01)
        finally:
            os.unlink(db_path)

    def test_trade_history_persisted(self):
        db_path = _make_temp_db()
        try:
            pm1 = PositionManager(db_path)
            pm1.add_put(
                symbol="AAPL", option_symbol="AAPL240615P00180000",
                strike=180.0, expiration="2024-06-15",
                contracts=1, premium=300.0,
            )

            pm2 = PositionManager(db_path)
            conn = sqlite3.connect(db_path)
            cur = conn.execute(
                "SELECT event_type, symbol FROM wheel_trade_history"
            )
            rows = cur.fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0][0] == "put_sold"
            assert rows[0][1] == "AAPL"
        finally:
            os.unlink(db_path)


# ============================================================================
# DB Cleanup
# ============================================================================

class TestDBCleanup:
    def test_temp_db_is_created(self):
        path = _make_temp_db()
        assert os.path.exists(path)
        os.unlink(path)
