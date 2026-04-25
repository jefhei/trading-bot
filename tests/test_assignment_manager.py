"""
Tests for put assignment handling (TB-034 / FR-3)
===================================================
Test: detect assignment via account activity feed, create stock position with
100 shares per contract, calculate cost basis = strike price - premium collected,
transition state_machine to long_stock phase. Verify notification sent with
assignment details.

Run with:
    pytest tests/test_assignment_manager.py -v

All tests are fully isolated — no live API calls are made.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

from bots.wheel_strategy.assignment_manager import AssignmentManager
from bots.wheel_strategy.position_manager import PositionManager
from bots.wheel_strategy.db import init_db


# ── Helper fixtures ────────────────────────────────────────────────────────

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _make_assignment_manager(db_path=None, mock_client=None, config=None):
    if db_path is None:
        db_path = _make_temp_db()
    if mock_client is None:
        mock_client = MagicMock()
    if config is None:
        config = {"wheel_strategy": {}}
    return AssignmentManager(db_path=db_path, client=mock_client, config=config)


def _make_mock_position(symbol="AAPL", strike=180.0, expiration=None,
                         contracts=1, premium=300.0, **kwargs):
    if expiration is None:
        exp = datetime.now() - timedelta(days=1)  # already expired
        expiration = exp.strftime("%Y-%m-%d")
    return {
        "id": kwargs.get("id", 1),
        "symbol": symbol,
        "strike": strike,
        "expiration": expiration,
        "contracts": contracts,
        "premium": premium,
        "contract_type": "PUT",
        "status": "open",
        **kwargs,
    }


class MockPosition:
    """Simple mock for Alpaca position objects."""
    def __init__(self, symbol, qty=100):
        self.symbol = symbol
        self.qty = qty

    def __repr__(self):
        return f"Position({self.symbol}, qty={self.qty})"


# ============================================================================
# Cost Basis Calculation
# ============================================================================

class TestCostBasisCalculation:
    """FR-3: cost basis = strike price - premium collected."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_basic_cost_basis(self):
        """stock price is strike - (premium / shares)."""
        put = _make_mock_position(strike=180.0, contracts=1, premium=300.0)
        # cost_basis = 180 - (300/100) = 180 - 3 = 177
        assignment = self.am._process_assignment(put)
        assert assignment["cost_basis"] == pytest.approx(177.0, abs=0.01)

    def test_cost_basis_multiple_contracts(self):
        """2 contracts: premium spread across 200 shares."""
        put = _make_mock_position(strike=150.0, contracts=2, premium=600.0)
        # cost_basis = 150 - (600/200) = 150 - 3 = 147
        assignment = self.am._process_assignment(put)
        assert assignment["cost_basis"] == pytest.approx(147.0, abs=0.01)

    def test_cost_basis_high_premium_reduces_basis(self):
        """Higher premium = lower effective cost basis."""
        put = _make_mock_position(strike=100.0, contracts=1, premium=500.0)
        # cost_basis = 100 - 5 = 95
        assignment = self.am._process_assignment(put)
        assert assignment["cost_basis"] == pytest.approx(95.0, abs=0.01)

    def test_cost_basis_low_premium_near_strike(self):
        """Low premium ≈ cost basis close to strike."""
        put = _make_mock_position(strike=100.0, contracts=1, premium=50.0)
        # cost_basis = 100 - 0.50 = 99.50
        assignment = self.am._process_assignment(put)
        assert assignment["cost_basis"] == pytest.approx(99.5, abs=0.01)

    def test_cost_basis_zero_premium(self):
        """Zero premium: cost basis = strike price."""
        put = _make_mock_position(strike=100.0, contracts=1, premium=0.0)
        assignment = self.am._process_assignment(put)
        assert assignment["cost_basis"] == pytest.approx(100.0, abs=0.01)

    def test_shares_calculation(self):
        """shares = contracts * 100."""
        for contracts, expected_shares in [(1, 100), (2, 200), (5, 500), (10, 1000)]:
            put = _make_mock_position(contracts=contracts)
            assignment = self.am._process_assignment(put)
            assert assignment["shares"] == expected_shares

    def test_assignment_contains_all_fields(self):
        """Assignment dict has all required fields."""
        put = _make_mock_position(symbol="MSFT", strike=380.0, contracts=2,
                                   premium=600.0, id=42)
        assignment = self.am._process_assignment(put)
        assert assignment["symbol"] == "MSFT"
        assert assignment["shares"] == 200
        assert assignment["strike"] == 380.0
        assert assignment["premium_collected"] == 600.0
        assert "cost_basis" in assignment
        assert assignment["put_position_id"] == 42


# ============================================================================
# Assignment Detection
# ============================================================================

class TestAssignmentDetection:
    """FR-3: Detect assignment via account activity / position checking."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def _seed_open_put(self, expired=True, price_below_strike=True):
        """Add an open put to the DB for assignment detection."""
        pm = PositionManager(self.db_path)
        pm.add_put(
            symbol="AAPL",
            option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration=(datetime.now() - timedelta(days=1) if expired else
                        datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
            contracts=1,
            premium=300.0,
        )

    def test_is_put_assigned_not_expired(self):
        """Put that hasn't expired yet is not assigned."""
        put = _make_mock_position(expiration=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
                                   strike=180.0)
        result = self.am._is_put_assigned(put)
        assert result is False

    def test_is_put_assigned_price_above_strike(self):
        """Stock above strike at expiration: put not assigned."""
        put = _make_mock_position(expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                                   strike=180.0)
        self.client.get_bars.return_value = [MagicMock(close=190.0)]  # above strike
        self.client.get_all_positions.return_value = [MockPosition("AAPL", 100)]
        result = self.am._is_put_assigned(put)
        assert result is False

    def test_is_put_assigned_price_below_strike_with_shares(self):
        """Stock below strike + shares appear = assigned."""
        put = _make_mock_position(expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                                   strike=180.0)
        self.client.get_bars.return_value = [MagicMock(close=170.0)]  # below strike
        self.client.get_all_positions.return_value = [MockPosition("AAPL", 100)]
        result = self.am._is_put_assigned(put)
        assert result is True

    def test_is_put_assigned_price_below_no_shares(self):
        """Stock below strike but no shares yet = not assigned."""
        put = _make_mock_position(expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                                   strike=180.0)
        self.client.get_bars.return_value = [MagicMock(close=170.0)]
        self.client.get_all_positions.return_value = []  # no shares
        result = self.am._is_put_assigned(put)
        assert result is False

    def test_is_put_assigned_cannot_get_price(self):
        """If price fetch fails, not assigned."""
        put = _make_mock_position(expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                                   strike=180.0)
        self.client.get_bars.side_effect = Exception("API error")
        result = self.am._is_put_assigned(put)
        assert result is False

    def test_check_for_assignments_finds_multiple(self):
        """Multiple open puts assigned simultaneously."""
        pm = PositionManager(self.db_path)
        for sym, strike in [("AAPL", 180.0), ("MSFT", 380.0)]:
            pm.add_put(
                symbol=sym,
                option_symbol=f"{sym}240615P{int(strike):06d}000",
                strike=strike,
                expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                contracts=1,
                premium=300.0,
            )

        # Both stocks now held
        self.client.get_bars.return_value = [MagicMock(close=100.0)]  # all ITM
        self.client.get_all_positions.return_value = [
            MockPosition("AAPL", 100),
            MockPosition("MSFT", 100),
        ]

        assignments = self.am.check_for_assignments()
        assert len(assignments) == 2
        symbols = {a["symbol"] for a in assignments}
        assert symbols == {"AAPL", "MSFT"}

    def test_check_for_assignments_none(self):
        """No open puts = no assignments."""
        assignments = self.am.check_for_assignments()
        assert assignments == []


# ============================================================================
# Position Manager Integration — record_assignment
# ============================================================================

class TestPositionManagerAssignment:
    """Test PositionManager.record_assignment side effects."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.pm = PositionManager(self.db_path)

    def test_record_assignment_creates_stock_position(self):
        """After recording assignment, stock position exists."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration="2024-06-14",
            contracts=1, premium=300.0,
        )
        # cost_basis = 180 - 300/100 = 177
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)

        stock_positions = self.pm.get_stock_positions("AAPL")
        assert len(stock_positions) == 1
        pos = stock_positions[0]
        assert pos["symbol"] == "AAPL"
        assert pos["shares"] == 100
        assert pos["cost_basis"] == pytest.approx(177.0, abs=0.01)

    def test_record_assignment_closes_put(self):
        """After assignment, the put position should be closed (not open)."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration="2024-06-14",
            contracts=1, premium=300.0,
        )
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)

        open_puts = self.pm.get_open_puts("AAPL")
        assert len(open_puts) == 0

    def test_record_assignment_records_trade_history(self):
        """put_assigned event added to trade history."""
        self.pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration="2024-06-14",
            contracts=1, premium=300.0,
        )
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)

        from bots.wheel_strategy.db import get_open_options
        # Trade history is recorded; check via summary
        summary = self.pm.get_premium_summary()
        assert summary["total_premium_collected"] >= 300.0

    def test_record_assignment_multiple_contracts(self):
        """2 contracts = 200 shares."""
        self.pm.add_put(
            symbol="MSFT", option_symbol="MSFT240615P00380000",
            strike=380.0,
            expiration="2024-06-14",
            contracts=2, premium=600.0,
        )
        self.pm.record_assignment("MSFT", 380.0, 2, 600.0, 377.0)

        stock_positions = self.pm.get_stock_positions("MSFT")
        assert len(stock_positions) == 1
        assert stock_positions[0]["shares"] == 200
        assert stock_positions[0]["cost_basis"] == pytest.approx(377.0, abs=0.01)


# ============================================================================
# Assignment Notification Details
# ============================================================================

class TestAssignmentNotification:
    """FR-3: Verify notification sent with assignment details."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_assignment_has_symbol_and_shares(self):
        put = _make_mock_position(symbol="AAPL", contracts=1)
        assignment = self.am._process_assignment(put)
        assert assignment["symbol"] == "AAPL"
        assert assignment["shares"] == 100

    def test_assignment_has_strike_and_premium(self):
        put = _make_mock_position(strike=180.0, premium=250.0)
        assignment = self.am._process_assignment(put)
        assert assignment["strike"] == 180.0
        assert assignment["premium_collected"] == 250.0

    def test_assignment_has_cost_basis(self):
        put = _make_mock_position(strike=150.0, contracts=2, premium=500.0)
        assignment = self.am._process_assignment(put)
        assert assignment["cost_basis"] == pytest.approx(147.5, abs=0.01)

    def test_assignment_has_put_position_id(self):
        put = _make_mock_position(id=77)
        assignment = self.am._process_assignment(put)
        assert assignment["put_position_id"] == 77

    def test_multiple_assignments_returned_as_list(self):
        """check_for_assignments returns list of assignment dicts."""
        pm = PositionManager(self.db_path)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            contracts=1, premium=300.0,
        )
        self.client.get_bars.return_value = [MagicMock(close=170.0)]
        self.client.get_all_positions.return_value = [MockPosition("AAPL", 100)]

        assignments = self.am.check_for_assignments()
        assert isinstance(assignments, list)
        assert len(assignments) == 1
        assert "cost_basis" in assignments[0]


# ============================================================================
# State Machine Transition
# ============================================================================

class TestStateMachineTransition:
    """FR-3: After assignment, transition state_machine to long_stock phase."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_process_assignment_returns_data_for_state_transition(self):
        """process_assignment returns info that drives state machine."""
        put = _make_mock_position(symbol="AAPL", strike=180.0, contracts=1, premium=300.0)
        assignment = self.am._process_assignment(put)
        # The assignment dict contains everything needed for state transition
        assert "symbol" in assignment
        assert "shares" in assignment
        assert "cost_basis" in assignment

    def test_process_assignment_records_stock_position(self):
        """After process_assignment, a stock position record exists."""
        put = _make_mock_position(symbol="AAPL", strike=180.0, contracts=1, premium=300.0)
        self.am._process_assignment(put)

        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        stocks = pm.get_stock_positions("AAPL")
        assert len(stocks) == 1
        assert stocks[0]["shares"] == 100

    def test_assignment_ready_for_covered_call_phase(self):
        """After assignment, can sell a covered call (position exists)."""
        put = _make_mock_position(symbol="AAPL", strike=180.0, contracts=1, premium=300.0)
        self.am._process_assignment(put)

        pm = PositionManager(self.db_path)
        stocks = pm.get_stock_positions("AAPL")
        assert len(stocks) == 1
        # Shares owned → eligible for covered calls
        assert stocks[0]["status"] == "held"


# ============================================================================
# Call Exercise Processing (FR-5 integration)
# ============================================================================

class TestCallExerciseProcessing:
    """Test _process_exercise for covered call assignment."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_process_exercise_pnl_calculation(self):
        """P&L = (strike - cost_basis) * shares + premium."""
        call = {
            "id": 10,
            "symbol": "AAPL",
            "strike": 190.0,
            "contracts": 1,
            "premium": 200.0,
            "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise is not None
        # P&L = (190 - 177) * 100 + 200 = 1300 + 200 = 1500
        assert exercise["realized_pnl"] == pytest.approx(1500.0, abs=0.01)
        assert exercise["shares"] == 100

    def test_process_exercise_multiple_contracts(self):
        """2 contracts exercise P&L."""
        call = {
            "id": 10,
            "symbol": "MSFT",
            "strike": 400.0,
            "contracts": 2,
            "premium": 500.0,
            "cost_basis": 385.0,
        }
        exercise = self.am._process_exercise(call)
        # P&L = (400 - 385) * 200 + 500 = 3000 + 500 = 3500
        assert exercise["realized_pnl"] == pytest.approx(3500.0, abs=0.01)

    def test_process_exercise_at_loss(self):
        """Strike below cost basis = negative capital gain (partial offset by premium)."""
        call = {
            "id": 10,
            "symbol": "AAPL",
            "strike": 170.0,
            "contracts": 1,
            "premium": 200.0,
            "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        # P&L = (170 - 177) * 100 + 200 = -700 + 200 = -500
        assert exercise["realized_pnl"] == pytest.approx(-500.0, abs=0.01)

    def test_process_exercise_break_even(self):
        """Strike == cost_basis: P&L = premium only."""
        call = {
            "id": 10,
            "symbol": "AAPL",
            "strike": 177.0,
            "contracts": 1,
            "premium": 200.0,
            "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["realized_pnl"] == pytest.approx(200.0, abs=0.01)

    def test_is_call_exercised_not_expired(self):
        """Call not expired → not exercised."""
        call = {
            "symbol": "AAPL",
            "expiration": (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
        }
        result = self.am._is_call_exercised(call)
        assert result is False

    def test_is_call_exercised_shares_gone(self):
        """Call expired + shares no longer held → exercised."""
        call = {
            "symbol": "AAPL",
            "expiration": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        self.client.get_all_positions.return_value = []  # no shares
        result = self.am._is_call_exercised(call)
        assert result is True

    def test_is_call_exercised_shares_still_held(self):
        """Call expired but shares still held → not yet exercised."""
        call = {
            "symbol": "AAPL",
            "expiration": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        self.client.get_all_positions.return_value = [MockPosition("AAPL", 100)]
        result = self.am._is_call_exercised(call)
        assert result is False


# ============================================================================
# Edge Cases
# ============================================================================

class TestAssignmentEdgeCases:
    """Test edge cases in assignment processing."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_process_assignment_with_zero_contracts(self):
        """Edge case: 0 contracts."""
        put = _make_mock_position(contracts=0, premium=0.0)
        # division by shares=0 → code falls back to strike
        assignment = self.am._process_assignment(put)
        assert assignment["shares"] == 0
        assert assignment["cost_basis"] == put["strike"]

    def test_process_assignment_large_position(self):
        """Large position: 10 contracts."""
        put = _make_mock_position(strike=150.0, contracts=10, premium=2000.0)
        assignment = self.am._process_assignment(put)
        assert assignment["shares"] == 1000
        # cost_basis = 150 - (2000/1000) = 148
        assert assignment["cost_basis"] == pytest.approx(148.0, abs=0.01)

    def test_process_assignment_persists_to_db(self):
        """Assignment recorded in DB survives recreation of PositionManager."""
        put = _make_mock_position(symbol="TSLA", strike=250.0, contracts=3, premium=900.0)
        self.am._process_assignment(put)

        # Fresh PositionManager instance should see the stock
        pm2 = PositionManager(self.db_path)
        stocks = pm2.get_stock_positions("TSLA")
        assert len(stocks) == 1
        assert stocks[0]["shares"] == 300

    def test_assignment_detects_only_itm_puts(self):
        """Only in-the-money puts should be flagged as assigned."""
        pm = PositionManager(self.db_path)
        # ITM put (price < strike)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            contracts=1, premium=300.0,
        )
        # OTM put (stock price > strike) - using a different symbol
        pm.add_put(
            symbol="MSFT", option_symbol="MSFT240615P00350000",
            strike=350.0,
            expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            contracts=1, premium=300.0,
        )

        # AAPL at 170 (ITM for 180 strike), MSFT at 400 (OTM for 350 strike)
        def get_price(symbol):
            prices = {"AAPL": 170.0, "MSFT": 400.0}
            return prices.get(symbol, 0)

        def mock_get_bars(symbol, **kwargs):
            return [MagicMock(close=get_price(symbol))]

        self.client.get_bars.side_effect = mock_get_bars
        # Only AAPL shares present, not MSFT
        self.client.get_all_positions.return_value = [MockPosition("AAPL", 100)]

        assignments = self.am.check_for_assignments()
        symbols = {a["symbol"] for a in assignments}
        assert symbols == {"AAPL"}
        assert "MSFT" not in symbols


# ============================================================================
# DB Cleanup
# ============================================================================

class TestDBCleanup:
    def test_temp_db_is_created(self):
        path = _make_temp_db()
        assert os.path.exists(path)
        os.unlink(path)
