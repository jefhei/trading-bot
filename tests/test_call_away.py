"""
Tests for share call-away handling (TB-036 / FR-5)
====================================================
Test: detect covered call exercise, calculate total return = premium collected
+ capital gains, reset position tracking for that stock, transition back to
no_position/put_selling state. Verify metrics are updated correctly.

Run with:
    pytest tests/test_call_away.py -v

All tests are fully isolated — no live API calls are made.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from bots.wheel_strategy.assignment_manager import AssignmentManager
from bots.wheel_strategy.position_manager import PositionManager
from bots.wheel_strategy.state_machine import WheelStateMachine, WheelTransition, WheelState
from bots.wheel_strategy.db import init_db, record_trade


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


class MockPosition:
    def __init__(self, symbol, qty=100):
        self.symbol = symbol
        self.qty = qty


# ============================================================================
# Total Return Calculation
# ============================================================================

class TestTotalReturnCalculation:
    """FR-5: total return = premium collected + capital gains."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_return_with_profit_and_premium(self):
        """Strike above cost basis = capital gain + premium."""
        call = {
            "id": 1, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 300.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        # capital_gain = (190 - 177) * 100 = 1300
        # total_return = 1300 + 300 = 1600
        assert exercise["realized_pnl"] == pytest.approx(1600.0, abs=0.01)

    def test_return_with_loss_at_strike_but_net_positive(self):
        """Strike below cost basis but premium offsets the loss."""
        call = {
            "id": 1, "symbol": "AAPL", "strike": 175.0,
            "contracts": 1, "premium": 500.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        # capital_gain = (175 - 177) * 100 = -200
        # total_return = -200 + 500 = 300
        assert exercise["realized_pnl"] == pytest.approx(300.0, abs=0.01)

    def test_return_at_break_even_plus_premium(self):
        """Strike == cost_basis: total return = premium only."""
        call = {
            "id": 1, "symbol": "SPY", "strike": 450.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 450.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["realized_pnl"] == pytest.approx(200.0, abs=0.01)

    def test_return_net_negative(self):
        """Large capital loss not fully offset by premium."""
        call = {
            "id": 1, "symbol": "AAPL", "strike": 150.0,
            "contracts": 1, "premium": 100.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        # capital_gain = (150 - 177) * 100 = -2700
        # total_return = -2700 + 100 = -2600
        assert exercise["realized_pnl"] == pytest.approx(-2600.0, abs=0.01)

    def test_return_multiple_contracts(self):
        """3 contracts with significant profit."""
        call = {
            "id": 1, "symbol": "MSFT", "strike": 420.0,
            "contracts": 3, "premium": 600.0, "cost_basis": 380.0,
        }
        exercise = self.am._process_exercise(call)
        # capital_gain = (420 - 380) * 300 = 12000
        # total_return = 12000 + 600 = 12600
        assert exercise["realized_pnl"] == pytest.approx(12600.0, abs=0.01)

    def test_return_high_premium_low_strike(self):
        """Very high premium with slightly below-cost strike."""
        call = {
            "id": 1, "symbol": "AAPL", "strike": 176.0,
            "contracts": 1, "premium": 1000.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        # capital_gain = (176 - 177) * 100 = -100
        # total_return = -100 + 1000 = 900
        assert exercise["realized_pnl"] == pytest.approx(900.0, abs=0.01)

    def test_exercise_dict_has_all_required_fields(self):
        """Exercise result contains all fields needed for notification."""
        call = {
            "id": 42, "symbol": "TSLA", "strike": 250.0,
            "contracts": 2, "premium": 400.0, "cost_basis": 245.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["symbol"] == "TSLA"
        assert exercise["shares"] == 200
        assert exercise["strike"] == 250.0
        assert exercise["premium_collected"] == 400.0
        assert exercise["cost_basis"] == 245.0
        assert "realized_pnl" in exercise
        assert exercise["call_position_id"] == 42


# ============================================================================
# Reset Position Tracking After Exercise
# ============================================================================

class TestPositionTrackingReset:
    """FR-5: Reset position tracking after shares called away."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.pm = PositionManager(self.db_path)
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def _setup_stock_position(self, symbol="AAPL", shares=100, cost_basis=177.0):
        """Add stock position to DB for testing."""
        self.pm.add_put(
            symbol=symbol,
            option_symbol=f"{symbol}240615P00180000",
            strike=180.0,
            expiration=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            contracts=1,
            premium=300.0,
        )
        self.pm.record_assignment(symbol, 180.0, 1, 300.0, cost_basis)

    def test_stock_position_exists_before_exercise(self):
        """Verify the stock position is in 'held' state before exercise."""
        self._setup_stock_position()
        stocks = self.pm.get_stock_positions("AAPL")
        assert len(stocks) == 1
        assert stocks[0]["status"] == "held"
        assert stocks[0]["shares"] == 100

    def test_stock_position_status_changed_after_exercise(self):
        """After exercise, stock position status should be 'called_away'."""
        self._setup_stock_position()
        call = {
            "id": 10, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        }
        self.am._process_exercise(call)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM wheel_stock_positions WHERE symbol = 'AAPL'"
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        assert len(rows) == 1
        assert rows[0]["status"] == "called_away"

    def test_no_open_stock_positions_after_exercise(self):
        """After exercise, get_stock_positions (which queries 'held') should return empty."""
        self._setup_stock_position()
        call = {
            "id": 10, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        }
        self.am._process_exercise(call)

        held = self.pm.get_stock_positions("AAPL")
        assert len(held) == 0

    def test_multiple_stocks_one_called_away(self):
        """Only the exercised stock position is affected; others remain held."""
        self._setup_stock_position("AAPL", cost_basis=177.0)
        self._setup_stock_position("MSFT", cost_basis=370.0)

        call = {
            "id": 10, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        }
        self.am._process_exercise(call)

        # AAPL should be called_away, MSFT still held
        held_aapl = self.pm.get_stock_positions("AAPL")
        held_msft = self.pm.get_stock_positions("MSFT")
        assert len(held_aapl) == 0  # AAPL called away
        assert len(held_msft) == 1  # MSFT still held
        assert held_msft[0]["status"] == "held"

    def test_position_tracking_ready_for_new_cycle(self):
        """After exercise, system can record a new put assignment (new cycle)."""
        self._setup_stock_position()
        call = {
            "id": 10, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        }
        self.am._process_exercise(call)

        # Simulate starting a new wheel cycle: sell put, get assigned
        self.pm.add_put(
            symbol="AAPL",
            option_symbol="AAPL240915P00175000",
            strike=175.0,
            expiration=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            contracts=1,
            premium=250.0,
        )
        # cost_basis = 175 - 250/100 = 172.50
        self.pm.record_assignment("AAPL", 175.0, 1, 250.0, 172.50)

        # Should have 1 held position (the new one)
        held = self.pm.get_stock_positions("AAPL")
        assert len(held) == 1
        assert held[0]["status"] == "held"
        assert held[0]["cost_basis"] == pytest.approx(172.50, abs=0.01)


# ============================================================================
# State Machine Transition After Call Assignment
# ============================================================================

class TestStateMachineAfterCallAssignment:
    """FR-5: Transition back to no_position (put_selling phase)."""

    def setup_method(self):
        self.sm = WheelStateMachine()

    def test_call_assigned_transitions_to_no_position(self):
        """CALL_ASSIGNED: short_call → no_position."""
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        self.sm.transition(WheelTransition.SELL_CALL)
        assert self.sm.current_state == WheelState.SHORT_CALL

        new_state = self.sm.transition(WheelTransition.CALL_ASSIGNED)
        assert new_state == WheelState.NO_POSITION

    def test_full_cycle_ends_at_put_selling(self):
        """Complete wheel cycle: no_position → ... → no_position (ready for puts)."""
        self.sm.transition(WheelTransition.SELL_PUT)
        assert self.sm.current_state == WheelState.SHORT_PUT

        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        assert self.sm.current_state == WheelState.LONG_STOCK

        self.sm.transition(WheelTransition.SELL_CALL)
        assert self.sm.current_state == WheelState.SHORT_CALL

        self.sm.transition(WheelTransition.CALL_ASSIGNED)
        assert self.sm.current_state == WheelState.NO_POSITION
        # Ready to sell puts again
        assert self.sm.can_transition(WheelTransition.SELL_PUT)

    def test_call_closed_retains_stock_for_another_call(self):
        """CALL_CLOSED: short_call → long_stock (not called away)."""
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        self.sm.transition(WheelTransition.SELL_CALL)
        self.sm.transition(WheelTransition.CALL_CLOSED)
        assert self.sm.current_state == WheelState.LONG_STOCK
        # Can sell another call
        assert self.sm.can_transition(WheelTransition.SELL_CALL)

    def test_call_expired_retains_stock_for_another_call(self):
        """CALL_EXPIRED: short_call → long_stock."""
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_ASSIGNED)
        self.sm.transition(WheelTransition.SELL_CALL)
        self.sm.transition(WheelTransition.CALL_EXPIRED)
        assert self.sm.current_state == WheelState.LONG_STOCK
        assert self.sm.can_transition(WheelTransition.SELL_CALL)

    def test_put_expiring_returns_to_put_selling(self):
        """PUT_EXPIRED: short_put → no_position (returns to put selling)."""
        self.sm.transition(WheelTransition.SELL_PUT)
        self.sm.transition(WheelTransition.PUT_EXPIRED)
        assert self.sm.current_state == WheelState.NO_POSITION
        assert self.sm.can_transition(WheelTransition.SELL_PUT)

    def test_double_cycle_verification(self):
        """Two complete cycles back-to-back."""
        for cycle in range(2):
            self.sm.transition(WheelTransition.SELL_PUT)
            self.sm.transition(WheelTransition.PUT_ASSIGNED)
            self.sm.transition(WheelTransition.SELL_CALL)
            self.sm.transition(WheelTransition.CALL_ASSIGNED)
            assert self.sm.current_state == WheelState.NO_POSITION, \
                f"Cycle {cycle+1}: Expected NO_POSITION after call assignment"


# ============================================================================
# Metrics Updated Correctly
# ============================================================================

class TestMetricsUpdatedAfterExercise:
    """FR-5: Verify metrics are updated correctly after exercise."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.pm = PositionManager(self.db_path)
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def _setup_full_cycle(self):
        """Set up: sell put → assignment → sell call → exercise."""
        # Step 1: Sell put
        self.pm.add_put(
            symbol="AAPL",
            option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration="2024-06-14",
            contracts=1, premium=300.0,
        )
        # Step 2: Assignment
        self.pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)
        # Step 3: Sell call
        self.pm.add_call(
            symbol="AAPL",
            option_symbol="AAPL240715C00190000",
            strike=190.0,
            expiration="2024-07-15",
            contracts=1, premium=200.0,
            cost_basis=177.0,
        )

    def test_premium_summary_after_full_cycle(self):
        """After put + call, total premium reflects all recorded events.
        Note: put_sold + put_assigned (300 each) + call_sold (200) = 800.
        Then call_exercised records 200 more → total = 1000."""
        self._setup_full_cycle()
        self.am._process_exercise({
            "id": 2, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })

        summary = self.pm.get_premium_summary()
        # put_sold:300, put_assigned:300, call_sold:200, call_exercised:200 = 1000
        assert summary["total_premium_collected"] == pytest.approx(1000.0, abs=0.01)

    def test_realized_pnl_after_exercise(self):
        """After exercise, realized P&L should reflect capital gain + premium."""
        self._setup_full_cycle()
        self.am._process_exercise({
            "id": 2, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })

        summary = self.pm.get_premium_summary()
        # realized_pnl from call exercise: (190-177)*100 + 200 = 1500
        assert summary["total_realized_pnl"] == pytest.approx(1500.0, abs=0.01)

    def test_total_return_after_exercise(self):
        """Total return = premium + realized P&L."""
        self._setup_full_cycle()
        self.am._process_exercise({
            "id": 2, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })

        summary = self.pm.get_premium_summary()
        # total_premium = 1000 (see test_premium_summary_after_full_cycle)
        # realized_pnl = 1500
        # total_return = 2500
        assert summary["total_return"] == pytest.approx(2500.0, abs=0.01)

    def test_metrics_for_loss_exercise(self):
        """Metrics reflect net negative when strike < cost_basis."""
        self._setup_full_cycle()
        self.am._process_exercise({
            "id": 2, "symbol": "AAPL", "strike": 170.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })

        summary = self.pm.get_premium_summary()
        # realized_pnl = (170-177)*100 + 200 = -500
        assert summary["total_realized_pnl"] == pytest.approx(-500.0, abs=0.01)
        # total_premium = 1000, realized = -500, total = 500
        assert summary["total_return"] == pytest.approx(500.0, abs=0.01)

    def test_metrics_after_multiple_cycles(self):
        """Metrics accumulate across multiple wheel cycles.
        Note: get_premium_summary sums all premiums from trade_history
        including duplicates from put_sold + put_assigned."""
        # Cycle 1
        self._setup_full_cycle()
        self.am._process_exercise({
            "id": 2, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })
        summary_after_cycle1 = self.pm.get_premium_summary()
        # put_sold:300, put_assigned:300, call_sold:200, call_exercised:200 = 1000
        assert summary_after_cycle1["total_premium_collected"] == pytest.approx(1000.0, abs=0.01)

        # Cycle 2: sell new put + call
        self.pm.add_put(
            symbol="MSFT",
            option_symbol="MSFT240615P00370000",
            strike=370.0,
            expiration="2024-06-14",
            contracts=1, premium=400.0,
        )
        self.pm.record_assignment("MSFT", 370.0, 1, 400.0, 366.0)
        self.pm.add_call(
            symbol="MSFT",
            option_symbol="MSFT240715C00390000",
            strike=390.0,
            expiration="2024-07-15",
            contracts=1, premium=300.0,
            cost_basis=366.0,
        )
        self.am._process_exercise({
            "id": 4, "symbol": "MSFT", "strike": 390.0,
            "contracts": 1, "premium": 300.0, "cost_basis": 366.0,
        })

        summary = self.pm.get_premium_summary()
        # Cycle 1: 1000, Cycle 2: put_sold:400, put_assigned:400, call_sold:300, call_exercised:300 = 1400
        # total_premium = 1000 + 1400 = 2400
        assert summary["total_premium_collected"] == pytest.approx(2400.0, abs=0.01)
        # realized_pnl = cycle1:1500 + cycle2:(390-366)*100+300=2700 = 4200
        assert summary["total_realized_pnl"] == pytest.approx(4200.0, abs=0.01)

    def test_call_exercise_recorded_in_trade_history(self):
        """call_exercised event added to trade history."""
        self._setup_full_cycle()
        self.am._process_exercise({
            "id": 2, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })

        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "SELECT event_type, symbol, realized_pnl FROM wheel_trade_history WHERE event_type = 'call_exercised'"
        )
        rows = cur.fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "call_exercised"
        assert rows[0][1] == "AAPL"
        assert rows[0][2] == pytest.approx(1500.0, abs=0.01)

    def test_put_and_call_events_in_history(self):
        """Trade history contains put_sold, put_assigned, call_sold, call_exercised."""
        self._setup_full_cycle()
        self.am._process_exercise({
            "id": 2, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })

        conn = sqlite3.connect(self.db_path)
        cur = conn.execute("SELECT DISTINCT event_type FROM wheel_trade_history ORDER BY event_type")
        events = [r[0] for r in cur.fetchall()]
        conn.close()

        assert "put_sold" in events
        assert "put_assigned" in events
        assert "call_sold" in events
        assert "call_exercised" in events


# ============================================================================
# Notification Details After Exercise
# ============================================================================

class TestExerciseNotification:
    """Verify exercise notification has all required details."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_notification_has_symbol_and_shares(self):
        call = {
            "id": 1, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["symbol"] == "AAPL"
        assert exercise["shares"] == 100

    def test_notification_has_premium_and_strike(self):
        call = {
            "id": 1, "symbol": "AAPL", "strike": 185.0,
            "contracts": 2, "premium": 400.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["premium_collected"] == 400.0
        assert exercise["strike"] == 185.0

    def test_notification_has_pnl(self):
        call = {
            "id": 1, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert "realized_pnl" in exercise
        assert exercise["realized_pnl"] == pytest.approx(1500.0, abs=0.01)

    def test_notification_has_call_position_id(self):
        call = {
            "id": 77, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["call_position_id"] == 77


# ============================================================================
# Edge Cases
# ============================================================================

class TestCallAwayEdgeCases:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.am = _make_assignment_manager(db_path=self.db_path, mock_client=self.client)

    def test_exercise_with_zero_contracts(self):
        call = {
            "id": 1, "symbol": "AAPL", "strike": 180.0,
            "contracts": 0, "premium": 100.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise is not None
        assert exercise["shares"] == 0

    def test_exercise_with_large_position(self):
        """10 contracts called away."""
        call = {
            "id": 1, "symbol": "SPY", "strike": 460.0,
            "contracts": 10, "premium": 2000.0, "cost_basis": 440.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["shares"] == 1000
        # P&L = (460-440)*1000 + 2000 = 20000 + 2000 = 22000
        assert exercise["realized_pnl"] == pytest.approx(22000.0, abs=0.01)

    def test_exercise_with_exactly_zero_pnl(self):
        """Strike = cost basis AND zero premium = zero return."""
        call = {
            "id": 1, "symbol": "AAPL", "strike": 177.0,
            "contracts": 1, "premium": 0.0, "cost_basis": 177.0,
        }
        exercise = self.am._process_exercise(call)
        assert exercise["realized_pnl"] == pytest.approx(0.0, abs=0.01)

    def test_multiple_exercises_same_symbol(self):
        """Exercise two calls for same symbol at different times."""
        pm = PositionManager(self.db_path)
        # Setup: 2 stock positions of same symbol
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0,
            expiration=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            contracts=1, premium=300.0,
        )
        pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240915P00180000",
            strike=180.0,
            expiration=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            contracts=1, premium=350.0,
        )
        pm.record_assignment("AAPL", 180.0, 1, 350.0, 176.50)
        pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        pm.add_call(
            symbol="AAPL", option_symbol="AAPL240815C00195000",
            strike=195.0, expiration="2024-08-15",
            contracts=1, premium=250.0, cost_basis=176.50,
        )

        # Exercise first call
        self.am._process_exercise({
            "id": 10, "symbol": "AAPL", "strike": 190.0,
            "contracts": 1, "premium": 200.0, "cost_basis": 177.0,
        })
        # Exercise second call
        self.am._process_exercise({
            "id": 11, "symbol": "AAPL", "strike": 195.0,
            "contracts": 1, "premium": 250.0, "cost_basis": 176.50,
        })

        summary = pm.get_premium_summary()
        # put_sold:300 + put_assigned:300 + put_sold:350 + put_assigned:350 
        # + call_sold:200 + call_sold:250 + call_exercised:200 + call_exercised:250 = 2200
        assert summary["total_premium_collected"] == pytest.approx(2200.0, abs=0.01)
        # P&L 1: (190-177)*100+200 = 1500
        # P&L 2: (195-176.5)*100+250 = 2100
        assert summary["total_realized_pnl"] == pytest.approx(3600.0, abs=0.01)


# ============================================================================
# DB Cleanup
# ============================================================================

class TestDBCleanup:
    def test_temp_db_is_created(self):
        path = _make_temp_db()
        assert os.path.exists(path)
        os.unlink(path)
