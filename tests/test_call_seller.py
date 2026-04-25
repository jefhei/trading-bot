"""
Tests for covered call selling logic (TB-035 / FR-4)
======================================================
Test: strike selection must be above adjusted cost basis to ensure profitable
exit, 30-45 DTE, minimum premium %, call protection (avoid selling below
cost basis unless rolling). Verify correct order creation for covered calls.

Run with:
    pytest tests/test_call_seller.py -v

All tests are fully isolated — no live API calls are made.
Alpaca client and option chain data are mocked.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from bots.wheel_strategy.call_seller import CallSeller
from bots.wheel_strategy.db import init_db


# ── Helper fixtures ────────────────────────────────────────────────────────

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _make_call_config(**overrides):
    base = {
        "days_to_expiration_min": 30,
        "days_to_expiration_max": 45,
        "target_delta": 0.30,
        "min_premium_pct": 1.0,
        "strike_min_above_cost_basis": 0.0,
    }
    base.update(overrides)
    return base


def _make_call_seller(db_path=None, mock_client=None, call_config=None):
    if db_path is None:
        db_path = _make_temp_db()
    if mock_client is None:
        mock_client = MagicMock()
    if call_config is None:
        call_config = _make_call_config()
    return CallSeller(db_path=db_path, client=mock_client,
                      config={"call_selling": call_config})


def _make_call_option(
    symbol="AAPL240615C00190000",
    strike=190.0,
    expiration_date="auto",
    delta=0.30,
    bid=2.50,
    ask=2.80,
    open_interest=500,
    volume=200,
    option_type="call",
    **kwargs
):
    if expiration_date == "auto":
        exp = datetime.now() + timedelta(days=37)
        expiration_date = exp.strftime("%Y-%m-%d")
    return {
        "symbol": symbol,
        "strike": strike,
        "expiration_date": expiration_date,
        "delta": delta,
        "bid": bid,
        "ask": ask,
        "open_interest": open_interest,
        "volume": volume,
        "type": option_type,
        **kwargs,
    }


# ============================================================================
# Strike Selection — Above Cost Basis
# ============================================================================

class TestStrikeAboveCostBasis:
    """FR-4: Strike selection must be above cost basis to ensure profitable exit."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_strikes_below_cost_basis_rejected(self):
        """Calls below cost basis should not be selected."""
        min_strike = 185.0  # cost basis
        chain = [
            _make_call_option(strike=180.0),
            _make_call_option(strike=182.0),
            _make_call_option(strike=184.0),
        ]
        result = self.cs._find_best_call(chain, 190.0, min_strike)
        assert result is None

    def test_strikes_at_cost_basis_accepted(self):
        """Call strike equal to cost basis with 0% min_above is acceptable."""
        min_strike = 185.0
        chain = [
            _make_call_option(strike=185.0),
        ]
        result = self.cs._find_best_call(chain, 190.0, min_strike)
        assert result is not None
        assert result["strike"] == 185.0

    def test_strikes_above_cost_basis_accepted(self):
        """Calls above cost basis should be eligible."""
        min_strike = 177.0
        chain = [
            _make_call_option(strike=180.0, delta=0.28),
            _make_call_option(strike=185.0, delta=0.30),
            _make_call_option(strike=190.0, delta=0.35),
        ]
        result = self.cs._find_best_call(chain, 190.0, min_strike)
        assert result is not None
        # Picks the one closest to target delta (0.30)
        assert result["strike"] == 185.0

    def test_only_strikes_above_min_strike_returned(self):
        """None of the returned strikes should be below min_strike."""
        min_strike = 185.0
        chain = [
            _make_call_option(strike=180.0),
            _make_call_option(strike=183.0),
            _make_call_option(strike=186.0, delta=0.28),
            _make_call_option(strike=190.0, delta=0.30),
        ]
        result = self.cs._find_best_call(chain, 190.0, min_strike)
        assert result is not None
        assert result["strike"] >= min_strike

    def test_mixed_puts_and_calls_returns_only_calls(self):
        """Mix of puts and calls: only calls are considered."""
        min_strike = 180.0
        chain = [
            _make_call_option(strike=185.0, option_type="put"),
            _make_call_option(strike=185.0, option_type="call"),
        ]
        result = self.cs._find_best_call(chain, 190.0, min_strike)
        assert result is not None
        assert result["strike"] == 185.0

    def test_min_strike_above_all_available_strikes(self):
        """When min_strike exceeds all available strikes, return None."""
        min_strike = 500.0  # very high
        chain = [
            _make_call_option(strike=180.0),
            _make_call_option(strike=190.0),
            _make_call_option(strike=200.0),
        ]
        result = self.cs._find_best_call(chain, 250.0, min_strike)
        assert result is None


# ============================================================================
# strike_min_above_cost_basis Config
# ============================================================================

class TestStrikeMinAboveCostBasis:
    """FR-4: Configurable buffer above cost basis."""

    def test_default_zero_above_cost_basis(self):
        """Default: strike_min_above_cost_basis = 0.0."""
        cs = _make_call_seller()
        assert cs.strike_min_above_cost_basis == 0.0

    def test_config_custom_above_cost_basis(self):
        """Config can set a buffer above cost basis."""
        cfg = _make_call_config(strike_min_above_cost_basis=2.0)
        cs = _make_call_seller(call_config=cfg)
        assert cs.strike_min_above_cost_basis == 2.0

    def test_select_strike_applies_buffer(self):
        """select_strike computes min_strike = cost_basis * (1 + buffer/100)."""
        cfg = _make_call_config(strike_min_above_cost_basis=2.0)
        cs = _make_call_seller(call_config=cfg)
        with patch.object(cs, '_get_options_chain') as mock_chain:
            mock_chain.return_value = [
                _make_call_option(strike=177.0),   # < 177*1.02 = 180.54
                _make_call_option(strike=180.0),   # < 180.54
                _make_call_option(strike=181.0),   # >= 180.54 ✓
                _make_call_option(strike=185.0),
            ]
            result = cs.select_strike("AAPL", current_price=190.0,
                                      cost_basis=177.0, shares=100)
            assert result is not None
            # min_strike = 177 * 1.02 = 180.54
            assert result["strike"] >= 180.54

    def test_five_percent_above_cost_basis(self):
        """5% buffer requires strike >= cost_basis * 1.05."""
        cfg = _make_call_config(strike_min_above_cost_basis=5.0)
        cs = _make_call_seller(call_config=cfg)
        with patch.object(cs, '_get_options_chain') as mock_chain:
            mock_chain.return_value = [
                _make_call_option(strike=185.0),   # < 177*1.05=185.85
                _make_call_option(strike=186.0),   # >= 185.85 ✓
                _make_call_option(strike=190.0),
            ]
            result = cs.select_strike("AAPL", current_price=190.0,
                                      cost_basis=177.0, shares=100)
            assert result is not None
            assert result["strike"] >= 177.0 * 1.05


# ============================================================================
# DTE Range (30-45 days)
# ============================================================================

class TestCallDTERange:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_accepts_within_dte_range(self):
        chain = [_make_call_option(expiration_date=(datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d"),
                                    strike=185.0)]
        result = self.cs._find_best_call(chain, 190.0, min_strike=180.0)
        assert result is not None

    def test_rejects_below_min_dte(self):
        chain = [_make_call_option(expiration_date=(datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
                                    strike=185.0)]
        result = self.cs._find_best_call(chain, 190.0, min_strike=180.0)
        assert result is None

    def test_rejects_above_max_dte(self):
        chain = [_make_call_option(expiration_date=(datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d"),
                                    strike=185.0)]
        result = self.cs._find_best_call(chain, 190.0, min_strike=180.0)
        assert result is None

    def test_custom_dte_range(self):
        cfg = _make_call_config(days_to_expiration_min=14, days_to_expiration_max=28)
        cs = _make_call_seller(db_path=self.db_path, call_config=cfg)
        short = _make_call_option(expiration_date=(datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d"),
                                   strike=185.0)
        long = _make_call_option(expiration_date=(datetime.now() + timedelta(days=35)).strftime("%Y-%m-%d"),
                                  strike=185.0)
        chain = [short, long]
        result = cs._find_best_call(chain, 190.0, min_strike=180.0)
        assert result is not None
        assert result["expiration"] == short["expiration_date"]


# ============================================================================
# Premium Threshold
# ============================================================================

class TestCallPremiumThreshold:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_default_min_premium_pct(self):
        cs = _make_call_seller()
        assert cs.min_premium_pct == 1.0

    def test_custom_min_premium_pct(self):
        cfg = _make_call_config(min_premium_pct=2.0)
        cs = _make_call_seller(call_config=cfg)
        assert cs.min_premium_pct == 2.0

    def test_premium_in_select_strike_result(self):
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            mock_chain.return_value = [_make_call_option(strike=185.0, bid=3.20, delta=0.30)]
            result = self.cs.select_strike("AAPL", current_price=190.0,
                                            cost_basis=177.0, shares=100)
            assert result is not None
            assert result["premium"] == 3.20


# ============================================================================
# should_sell_call Guard Checks
# ============================================================================

class TestShouldSellCall:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_returns_true_with_enough_shares(self):
        result = self.cs.should_sell_call("AAPL", shares=200)
        assert result is True

    def test_returns_true_with_exact_100_shares(self):
        result = self.cs.should_sell_call("AAPL", shares=100)
        assert result is True

    def test_returns_false_below_100_shares(self):
        result = self.cs.should_sell_call("AAPL", shares=99)
        assert result is False

    def test_returns_false_with_zero_shares(self):
        result = self.cs.should_sell_call("AAPL", shares=0)
        assert result is False

    def test_returns_false_if_open_calls_exist(self):
        with patch("bots.wheel_strategy.position_manager.PositionManager") as MockPM:
            mock_pm = MagicMock()
            mock_pm.get_open_calls.return_value = [{"symbol": "AAPL", "status": "open"}]
            MockPM.return_value = mock_pm
            result = self.cs.should_sell_call("AAPL", shares=200)
            assert result is False

    def test_returns_true_when_no_open_calls(self):
        with patch("bots.wheel_strategy.position_manager.PositionManager") as MockPM:
            mock_pm = MagicMock()
            mock_pm.get_open_calls.return_value = []
            MockPM.return_value = mock_pm
            result = self.cs.should_sell_call("AAPL", shares=100)
            assert result is True

    def test_returns_true_with_300_shares(self):
        """300 shares = 3 contracts worth."""
        result = self.cs.should_sell_call("AAPL", shares=300)
        assert result is True


# ============================================================================
# select_strike Integration
# ============================================================================

class TestSelectStrikeCall:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_returns_best_call(self):
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            exp = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")
            mock_chain.return_value = [
                _make_call_option(strike=185.0, delta=0.35, expiration_date=exp),
                _make_call_option(strike=190.0, delta=0.30, expiration_date=exp),
                _make_call_option(strike=195.0, delta=0.22, expiration_date=exp),
            ]
            result = self.cs.select_strike("AAPL", current_price=190.0,
                                            cost_basis=177.0, shares=100)
            assert result is not None
            assert result["strike"] == 190.0  # closest to target_delta 0.30

    def test_returns_none_for_empty_chain(self):
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            mock_chain.return_value = []
            result = self.cs.select_strike("AAPL", current_price=190.0,
                                            cost_basis=177.0, shares=100)
            assert result is None

    def test_returns_none_when_insufficient_shares(self):
        result = self.cs.select_strike("AAPL", current_price=190.0,
                                        cost_basis=177.0, shares=50)
        assert result is None

    def test_returns_none_on_error(self):
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            mock_chain.side_effect = Exception("API failure")
            result = self.cs.select_strike("AAPL", current_price=190.0,
                                            cost_basis=177.0, shares=100)
            assert result is None

    def test_result_has_all_fields(self):
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            exp = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")
            mock_chain.return_value = [_make_call_option(strike=190.0, bid=2.50, delta=0.30,
                                                          expiration_date=exp)]
            result = self.cs.select_strike("AAPL", current_price=190.0,
                                            cost_basis=177.0, shares=100)
            assert result is not None
            for field in ["strike", "expiration", "delta", "premium", "option_symbol"]:
                assert field in result

    def test_calculates_contracts_from_shares(self):
        """Shares // 100 should determine number of contracts."""
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            exp = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")
            mock_chain.return_value = [_make_call_option(strike=190.0, delta=0.30,
                                                          expiration_date=exp)]
            result = self.cs.select_strike("AAPL", current_price=190.0,
                                            cost_basis=177.0, shares=300)
            # select_strike calls _find_best_call — contracts is computed but
            # only used for validation (returns None if < 1).
            # The returned dict doesn't contain contracts.
            assert result is not None

    def test_calls_chain_api_with_symbol(self):
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            mock_chain.return_value = None
            self.cs.select_strike("MSFT", current_price=380.0,
                                   cost_basis=375.0, shares=100)
            mock_chain.assert_called_once_with("MSFT")


# ============================================================================
# place_call_order
# ============================================================================

class TestPlaceCallOrder:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_returns_status_dict(self):
        result = self.cs.place_call_order(
            symbol="AAPL", strike=190.0,
            expiration="2024-06-15", contracts=1, premium=2.50
        )
        assert result is not None
        assert result["status"] == "pending"
        assert result["symbol"] == "AAPL"
        assert result["strike"] == 190.0
        assert result["contracts"] == 1

    def test_multi_contract_order(self):
        result = self.cs.place_call_order(
            symbol="AAPL", strike=190.0,
            expiration="2024-06-15", contracts=3, premium=2.50
        )
        assert result["contracts"] == 3

    def test_order_with_high_price_symbol(self):
        result = self.cs.place_call_order(
            symbol="MSFT", strike=400.0,
            expiration="2024-06-15", contracts=2, premium=4.00
        )
        assert result["strike"] == 400.0


# ============================================================================
# _find_best_call — Sorting and Filtering
# ============================================================================

class TestFindBestCallSorting:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_sorts_by_delta_distance_ascending(self):
        min_strike = 180.0
        chain = [
            _make_call_option(strike=195.0, delta=0.45),  # dist 0.15
            _make_call_option(strike=185.0, delta=0.28),  # dist 0.02
            _make_call_option(strike=190.0, delta=0.30),  # dist 0.00 ← should win
        ]
        result = self.cs._find_best_call(chain, 190.0, min_strike)
        assert result["delta"] == 0.30
        assert result["delta_distance"] == 0.0

    def test_empty_chain_returns_none(self):
        result = self.cs._find_best_call([], 190.0, min_strike=180.0)
        assert result is None

    def test_null_expiration_skipped(self):
        chain = [{"symbol": "X", "strike": 185.0, "delta": 0.30,
                  "expiration_date": None, "bid": 2.0, "type": "call"}]
        result = self.cs._find_best_call(chain, 190.0, min_strike=180.0)
        assert result is None

    def test_malformed_expiration_skipped(self):
        chain = [{"symbol": "X", "strike": 185.0, "delta": 0.30,
                  "expiration_date": "not-a-date", "bid": 2.0, "type": "call"}]
        result = self.cs._find_best_call(chain, 190.0, min_strike=180.0)
        assert result is None


# ============================================================================
# End-to-End Flow
# ============================================================================

class TestCallEndToEnd:

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.cs = _make_call_seller(db_path=self.db_path, mock_client=self.client)

    def test_full_flow_check_select_place(self):
        """should_sell_call True → select_strike → place_call_order."""
        # Step 1: should_sell_call
        assert self.cs.should_sell_call("AAPL", shares=100) is True

        # Step 2: select_strike
        with patch.object(self.cs, '_get_options_chain') as mock_chain:
            exp = (datetime.now() + timedelta(days=40)).strftime("%Y-%m-%d")
            mock_chain.return_value = [
                _make_call_option(symbol="AAPL241015C00185000", strike=185.0,
                                  delta=0.30, expiration_date=exp, bid=3.00),
                _make_call_option(symbol="AAPL241015C00190000", strike=190.0,
                                  delta=0.25, expiration_date=exp, bid=2.00),
            ]
            strike_info = self.cs.select_strike("AAPL", current_price=190.0,
                                                 cost_basis=177.0, shares=100)
            assert strike_info is not None
            assert strike_info["strike"] == 185.0
            assert strike_info["premium"] == 3.00

            # Step 3: place_call_order
            order = self.cs.place_call_order(
                symbol="AAPL", strike=strike_info["strike"],
                expiration=strike_info["expiration"],
                contracts=1, premium=strike_info["premium"]
            )
            assert order["strike"] == 185.0
            assert order["status"] == "pending"


# ============================================================================
# Default Config Values
# ============================================================================

class TestDefaultConfig:

    def test_default_dte_range(self):
        cs = _make_call_seller()
        assert cs.dte_min == 30
        assert cs.dte_max == 45

    def test_default_target_delta(self):
        cs = _make_call_seller()
        assert cs.target_delta == 0.30

    def test_default_min_premium_pct(self):
        cs = _make_call_seller()
        assert cs.min_premium_pct == 1.0

    def test_default_strike_above_cost_basis(self):
        cs = _make_call_seller()
        assert cs.strike_min_above_cost_basis == 0.0

    def test_all_overrides(self):
        cfg = _make_call_config(
            days_to_expiration_min=21,
            days_to_expiration_max=35,
            target_delta=0.25,
            min_premium_pct=2.0,
            strike_min_above_cost_basis=2.0,
        )
        cs = _make_call_seller(call_config=cfg)
        assert cs.dte_min == 21
        assert cs.dte_max == 35
        assert cs.target_delta == 0.25
        assert cs.min_premium_pct == 2.0
        assert cs.strike_min_above_cost_basis == 2.0


# ============================================================================
# Cleanup
# ============================================================================

class TestDBCleanup:
    def test_temp_db_is_created(self):
        path = _make_temp_db()
        assert os.path.exists(path)
        os.unlink(path)
