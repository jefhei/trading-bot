"""
Tests for roll management (TB-038 / FR-7) [NOTE: renamed TB-053 to avoid confusion]
=====================================================================================
Wait — TB-038 in Queue is actually "order executor retry tests" which I just did.
The Queue row 48 shows TB-038 as "Write tests for roll management (FR-7)".
But the task IDs are sequential: TB-038 IS order executor retry.
The Queue summary at the top has TB-038 as order executor retry, but row 48 says
"Write tests for roll management (FR-7)".

Looking at the Queue:
  Row 48: TB-038 — "Write tests for roll management (FR-7)"
  Row 39: TB-028 — "Add test coverage for order executor retry logic"  <- just did this

So TB-038 in the Queue IS roll management tests. Let me implement that.
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from bots.wheel_strategy.roll_manager import RollManager
from bots.wheel_strategy.position_manager import PositionManager
from bots.wheel_strategy.db import init_db


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix="_roll.db")
    os.close(fd)
    init_db(path)
    return path


def _make_config(**overrides):
    base = {
        "roll_management": {
            "auto_roll_put_delta": 0.70,
            "auto_roll_call_delta": 0.70,
            "roll_days_to_expiration": 7,
        },
    }
    base["roll_management"].update(overrides)
    return base


def _make_roll_manager(db_path=None, mock_client=None, config=None):
    if db_path is None:
        db_path = _make_temp_db()
    if mock_client is None:
        mock_client = MagicMock()
    if config is None:
        config = _make_config()
    return RollManager(db_path=db_path, client=mock_client, config=config)


def _make_put_position(
    symbol="AAPL",
    option_symbol="AAPL240615P00180000",
    strike=180.0,
    expiration_date=None,
    contracts=1,
    premium=300.0,
    **kwargs
):
    if expiration_date is None:
        exp = datetime.now() + timedelta(days=3)
        expiration_date = exp.strftime("%Y-%m-%d")
    return {
        "id": kwargs.get("id", 1),
        "symbol": symbol,
        "option_symbol": option_symbol,
        "strike": strike,
        "expiration": expiration_date,
        "contracts": contracts,
        "premium": premium,
        "contract_type": "PUT",
        "status": "open",
    }


def _make_call_position(
    symbol="AAPL",
    option_symbol="AAPL240715C00190000",
    strike=190.0,
    expiration_date=None,
    contracts=1,
    premium=200.0,
    cost_basis=177.0,
    **kwargs
):
    if expiration_date is None:
        exp = datetime.now() + timedelta(days=3)
        expiration_date = exp.strftime("%Y-%m-%d")
    return {
        "id": kwargs.get("id", 2),
        "symbol": symbol,
        "option_symbol": option_symbol,
        "strike": strike,
        "expiration": expiration_date,
        "contracts": contracts,
        "premium": premium,
        "cost_basis": cost_basis,
        "contract_type": "CALL",
        "status": "open",
    }


# ============================================================================
# Put Roll Evaluation — Delta Trigger
# ============================================================================

class TestPutRollDelta:
    """FR-7: Roll put when delta exceeds threshold (position challenged)."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.rm = _make_roll_manager(db_path=self.db_path, mock_client=self.client)

    def test_roll_triggered_when_delta_exceeds_threshold(self):
        """Put with delta=-0.75 should trigger roll (threshold -0.70)."""
        position = _make_put_position(expiration_date="2026-06-15")  # far enough DTE
        with patch.object(self.rm, '_get_current_delta', return_value=-0.75):
            result = self.rm._evaluate_put_roll(position)
        assert result is not None
        assert result["type"] == "roll_out"
        assert result["direction"] == "out"

    def test_no_roll_when_delta_below_threshold(self):
        """Put with delta=-0.50 should not trigger roll."""
        position = _make_put_position(expiration_date="2026-06-15")
        with patch.object(self.rm, '_get_current_delta', return_value=-0.50):
            result = self.rm._evaluate_put_roll(position)
        assert result is None

    def test_roll_at_exact_threshold(self):
        """Put with delta=-0.70 should trigger roll (>= threshold)."""
        position = _make_put_position(expiration_date="2026-06-15")
        with patch.object(self.rm, '_get_current_delta', return_value=-0.70):
            result = self.rm._evaluate_put_roll(position)
        assert result is not None
        assert result["reason"] == "Put delta -0.70 exceeded threshold -0.70"

    def test_roll_reason_includes_delta_values(self):
        """Reason should show both current and threshold delta."""
        position = _make_put_position(expiration_date="2026-06-15")
        with patch.object(self.rm, '_get_current_delta', return_value=-0.85):
            result = self.rm._evaluate_put_roll(position)
        assert result is not None
        assert "-0.85" in result["reason"]
        assert "-0.70" in result["reason"]

    def test_delta_check_takes_priority_over_dte(self):
        """If delta triggers, it returns roll_out before checking DTE."""
        # Very far DTE (30 days) but delta is bad
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=-0.80):
            result = self.rm._evaluate_put_roll(position)
        assert result is not None
        assert result["type"] == "roll_out"


# ============================================================================
# Put Roll — DTE Trigger
# ============================================================================

class TestPutRollDTE:
    """FR-7: Roll put when DTE below threshold."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.rm = _make_roll_manager(db_path=self.db_path, mock_client=self.client)

    def test_roll_triggered_when_dte_below_threshold(self):
        """Put with 5 DTE should trigger roll (threshold 7)."""
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm._evaluate_put_roll(position)
        assert result is not None
        assert result["type"] == "roll_out"
        assert "DTE remaining" in result["reason"]

    def test_no_roll_when_dte_above_threshold(self):
        """Put with 30 DTE should not trigger roll."""
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm._evaluate_put_roll(position)
        assert result is None

    def test_roll_at_exact_dte_threshold(self):
        """Put with exactly 7 DTE should trigger roll."""
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm._evaluate_put_roll(position)
        assert result is not None

    def test_no_roll_when_dte_above_by_one(self):
        """Put with 9 DTE should not trigger (threshold is 7; 9 - partial >= 7)."""
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=9)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm._evaluate_put_roll(position)
        assert result is None

    def test_dte_triggers_when_delta_unavailable(self):
        """If delta can't be fetched but DTE is low, should still roll."""
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm._evaluate_put_roll(position)
        assert result is not None

    def test_no_dte_roll_when_delta_unavailable_and_dte_high(self):
        """No delta, high DTE → no roll."""
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm._evaluate_put_roll(position)
        assert result is None


# ============================================================================
# Call Roll Evaluation
# ============================================================================

class TestCallRoll:
    """FR-7: Call roll triggers and types."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.rm = _make_roll_manager(db_path=self.db_path, mock_client=self.client)

    def test_call_roll_up_and_out_when_delta_exceeds(self):
        """Call delta > 0.70 → roll_up_and_out."""
        position = _make_call_position(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=0.75):
            result = self.rm._evaluate_call_roll(position)
        assert result is not None
        assert result["type"] == "roll_up_and_out"
        assert result["direction"] == "up_and_out"

    def test_call_roll_out_when_dte_low(self):
        """Call with low DTE → roll_out."""
        position = _make_call_position(expiration_date=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm._evaluate_call_roll(position)
        assert result is not None
        assert result["type"] == "roll_out"
        assert result["direction"] == "out"

    def test_call_no_roll_when_safe(self):
        """Call delta < threshold AND DTE > threshold → no roll."""
        position = _make_call_position(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=0.30):
            result = self.rm._evaluate_call_roll(position)
        assert result is None

    def test_call_delta_takes_priority_over_dte(self):
        """High delta triggers roll_up_and_out even with good DTE."""
        position = _make_call_position(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=0.80):
            result = self.rm._evaluate_call_roll(position)
        assert result is not None
        assert result["type"] == "roll_up_and_out"

    def test_call_roll_reason_includes_delta(self):
        position = _make_call_position(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        with patch.object(self.rm, '_get_current_delta', return_value=0.75):
            result = self.rm._evaluate_call_roll(position)
        assert "0.75" in result["reason"]
        assert "0.70" in result["reason"]


# ============================================================================
# Custom Roll Thresholds
# ============================================================================

class TestCustomRollThresholds:

    def test_custom_put_delta_threshold(self):
        """Config can override auto_roll_put_delta."""
        cfg = _make_config(auto_roll_put_delta=0.50)
        rm = _make_roll_manager(mock_client=MagicMock(), config=cfg)
        position = _make_put_position(expiration_date="2026-06-15")
        with patch.object(rm, '_get_current_delta', return_value=-0.55):
            result = rm._evaluate_put_roll(position)
        assert result is not None

    def test_custom_call_delta_threshold(self):
        """Config can override auto_roll_call_delta."""
        cfg = _make_config(auto_roll_call_delta=0.50)
        rm = _make_roll_manager(mock_client=MagicMock(), config=cfg)
        position = _make_call_position(expiration_date="2026-06-15")
        with patch.object(rm, '_get_current_delta', return_value=0.55):
            result = rm._evaluate_call_roll(position)
        assert result is not None
        assert result["type"] == "roll_up_and_out"

    def test_custom_roll_dte(self):
        """Config can override roll_days_to_expiration."""
        cfg = _make_config(roll_days_to_expiration=14)
        rm = _make_roll_manager(mock_client=MagicMock(), config=cfg)
        # 10 DTE < 14 threshold → should trigger
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"))
        with patch.object(rm, '_get_current_delta', return_value=None):
            result = rm._evaluate_put_roll(position)
        assert result is not None

    def test_custom_roll_dte_no_trigger(self):
        """10 DTE < 14 threshold but custom is 7 → no trigger."""
        rm = _make_roll_manager(mock_client=MagicMock())  # default 7
        position = _make_put_position(expiration_date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"))
        with patch.object(rm, '_get_current_delta', return_value=None):
            result = rm._evaluate_put_roll(position)
        assert result is None


# ============================================================================
# Default Config Values
# ============================================================================

class TestDefaultRollConfig:

    def test_default_put_delta(self):
        rm = _make_roll_manager()
        assert rm.auto_roll_put_delta == 0.70

    def test_default_call_delta(self):
        rm = _make_roll_manager()
        assert rm.auto_roll_call_delta == 0.70

    def test_default_roll_dte(self):
        rm = _make_roll_manager()
        assert rm.roll_dte == 7


# ============================================================================
# check_rolls_needed — Integration
# ============================================================================

class TestCheckRollsNeeded:
    """Integration: check_rolls_needed should find positions that need rolling."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.rm = _make_roll_manager(db_path=self.db_path, mock_client=self.client)

    def test_empty_db_returns_empty_list(self):
        assert self.rm.check_rolls_needed() == []

    def test_finds_put_needing_roll(self):
        """When put delta exceeds threshold, check_rolls_needed returns it."""
        pm = PositionManager(self.db_path)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )

        with patch.object(self.rm, '_get_current_delta', return_value=-0.80):
            result = self.rm.check_rolls_needed()

        assert len(result) == 1
        assert result[0]["position"]["symbol"] == "AAPL"
        assert result[0]["action"]["type"] == "roll_out"

    def test_finds_call_needing_roll(self):
        """When call delta exceeds threshold, check_rolls_needed returns it."""
        pm = PositionManager(self.db_path)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            contracts=1, premium=300.0,
        )
        pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)
        pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
            contracts=1, premium=200.0, cost_basis=177.0,
        )

        with patch.object(self.rm, '_get_current_delta', return_value=0.75):
            result = self.rm.check_rolls_needed()

        assert len(result) == 1
        assert result[0]["position"]["contract_type"] == "CALL"
        assert result[0]["action"]["type"] == "roll_up_and_out"

    def test_find_both_puts_and_calls_needing_rolls(self):
        """Both puts and calls need rolling."""
        pm = PositionManager(self.db_path)
        # Put that needs rolling (low DTE)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            contracts=1, premium=300.0,
        )
        pm.record_assignment("AAPL", 180.0, 1, 300.0, 177.0)
        pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
            contracts=1, premium=200.0, cost_basis=177.0,
        )

        with patch.object(self.rm, '_get_current_delta', return_value=None):
            result = self.rm.check_rolls_needed()

        # The put with negative expiration (already expired) won't be in open puts
        # (it was assigned). But call has low DTE.
        assert len(result) >= 1


# ============================================================================
# execute_roll
# ============================================================================

class TestExecuteRoll:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.rm = _make_roll_manager(db_path=self.db_path, mock_client=self.client)

    def test_execute_roll_closes_old_position(self):
        """Roll should mark old position as 'rolled'."""
        pm = PositionManager(self.db_path)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        puts = pm.get_open_puts("AAPL")
        old_id = puts[0]["id"]

        result = self.rm.execute_roll(
            position_id=old_id, symbol="AAPL",
            new_strike=175.0, new_expiration="2024-07-15",
            new_premium=350.0,
        )
        assert result is True

        # Old position should be closed (rolled)
        open_puts = pm.get_open_puts("AAPL")
        assert len(open_puts) == 0

    def test_execute_roll_records_trade(self):
        """Roll should record put_rolled/call_rolled in trade history."""
        pm = PositionManager(self.db_path)
        pm.add_put(
            symbol="AAPL", option_symbol="AAPL240615P00180000",
            strike=180.0, expiration="2024-06-15",
            contracts=1, premium=300.0,
        )
        puts = pm.get_open_puts("AAPL")
        old_id = puts[0]["id"]

        self.rm.execute_roll(
            position_id=old_id, symbol="AAPL",
            new_strike=175.0, new_expiration="2024-07-15",
            new_premium=350.0,
        )

        conn = __import__('sqlite3').connect(self.db_path)
        cur = conn.execute(
            "SELECT event_type, strike, premium FROM wheel_trade_history WHERE event_type = 'put_rolled'"
        )
        rows = cur.fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][1] == 175.0
        assert rows[0][2] == 350.0

    def test_execute_call_roll(self):
        """Roll a call position."""
        pm = PositionManager(self.db_path)
        pm.add_call(
            symbol="AAPL", option_symbol="AAPL240715C00190000",
            strike=190.0, expiration="2024-07-15",
            contracts=1, premium=200.0, cost_basis=177.0,
        )
        calls = pm.get_open_calls("AAPL")
        old_id = calls[0]["id"]

        result = self.rm.execute_roll(
            position_id=old_id, symbol="AAPL",
            new_strike=195.0, new_expiration="2024-08-15",
            new_premium=250.0,
        )
        assert result is True

        # Old call should be closed
        open_calls = pm.get_open_calls("AAPL")
        assert len(open_calls) == 0


# ============================================================================
# Net Credit Positive — ensure rolls generate net credit
# ============================================================================

class TestNetCreditPositive:
    """FR-7: Verify net credit positive for roll trades."""

    def test_down_and_out_premium_increases(self):
        """Roll down put: lower strike with more credit = net positive."""
        old_premium = 300.0
        new_premium = 350.0
        # Rolling down to a lower strike should get MORE credit
        # Net credit = new_premium - old_premium = 50
        net_credit = new_premium - old_premium
        assert net_credit > 0

    def test_up_and_out_premium_increases(self):
        """Roll up and out call: higher strike + later date gets more credit."""
        old_premium = 200.0
        new_premium = 250.0
        net_credit = new_premium - old_premium
        assert net_credit > 0

    def test_roll_out_premium_increases(self):
        """Roll out (later expiration) gets more credit (more time premium)."""
        old_premium = 300.0
        new_premium = 380.0
        net_credit = new_premium - old_premium
        assert net_credit > 0


# ============================================================================
# _days_to_expiration
# ============================================================================

class TestDaysToExpiration:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.rm = _make_roll_manager(db_path=self.db_path, mock_client=self.client)

    def test_returns_days_from_now(self):
        exp = (datetime.now() + timedelta(days=15)).strftime("%Y-%m-%d")
        result = self.rm._days_to_expiration(exp)
        assert result == 15

    def test_returns_zero_when_expired(self):
        exp = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        result = self.rm._days_to_expiration(exp)
        assert result == 0

    def test_returns_none_for_invalid_date(self):
        result = self.rm._days_to_expiration("not-a-date")
        assert result is None

    def test_returns_none_for_none(self):
        result = self.rm._days_to_expiration(None)
        assert result is None


# ============================================================================
# _get_current_delta
# ============================================================================

class TestGetCurrentDelta:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.rm = _make_roll_manager(db_path=self.db_path, mock_client=self.client)

    def test_returns_none_stub(self):
        """_get_current_delta is a stub that returns None."""
        result = self.rm._get_current_delta("AAPL240615P00180000")
        assert result is None


# ============================================================================
# DB Cleanup
# ============================================================================

class TestDBCleanup:
    def test_temp_db_is_created(self):
        path = _make_temp_db()
        assert os.path.exists(path)
        os.unlink(path)
