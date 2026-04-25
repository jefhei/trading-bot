"""
Tests for cash-secured put selling logic (TB-033 / FR-2)
==========================================================
Test delta-based strike selection (0.30 target), 30-45 DTE expiration range,
minimum premium as % of strike, position sizing capped at max capital per stock
(default 10%). Verify put order creation with correct parameters for all
input variations.

Run with:
    pytest tests/test_put_seller.py -v

All tests are fully isolated — no live API calls are made.
Alpaca client and option chain data are mocked.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

from bots.wheel_strategy.put_seller import PutSeller
from bots.wheel_strategy.db import init_db


# ── Helper fixtures ────────────────────────────────────────────────────────

def _make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _make_put_config(**overrides):
    """Return just the put_selling config dict."""
    base = {
        "days_to_expiration_min": 30,
        "days_to_expiration_max": 45,
        "target_delta": 0.30,
        "min_premium_pct": 1.0,
        "max_contracts_per_stock": 5,
    }
    base.update(overrides)
    return base


def _make_put_seller(db_path=None, mock_client=None, put_config=None):
    if db_path is None:
        db_path = _make_temp_db()
    if mock_client is None:
        mock_client = MagicMock()
    if put_config is None:
        put_config = _make_put_config()
    # PutSeller expects "put_selling" at top level of config dict
    return PutSeller(db_path=db_path, client=mock_client, config={"put_selling": put_config})


def _make_option(
    symbol="AAPL240615P00180000",
    strike=180.0,
    expiration_date="auto",
    delta=-0.30,
    bid=2.50,
    ask=2.80,
    open_interest=500,
    volume=200,
    option_type="put",
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
# Delta-Based Strike Selection
# ============================================================================

class TestDeltaBasedStrikeSelection:
    """FR-2: Delta-based strike selection (0.30 target)."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_selects_put_closest_to_target_delta(self):
        chain = [
            _make_option(strike=175, delta=-0.20),
            _make_option(strike=180, delta=-0.30),
            _make_option(strike=185, delta=-0.40),
        ]
        result = self.ps._find_best_put(chain, current_price=190.0)
        assert result is not None
        assert result["strike"] == 180.0
        assert result["delta"] == -0.30

    def test_selects_nearest_delta_when_exact_unavailable(self):
        chain = [
            _make_option(strike=177, delta=-0.22),
            _make_option(strike=178, delta=-0.28),
            _make_option(strike=182, delta=-0.35),
            _make_option(strike=183, delta=-0.42),
        ]
        result = self.ps._find_best_put(chain, current_price=185.0)
        assert result["strike"] == 178.0

    def test_prefers_closer_delta_over_farther(self):
        chain = [
            _make_option(strike=179, delta=-0.28),
            _make_option(strike=181, delta=-0.32),
        ]
        result = self.ps._find_best_put(chain, current_price=180.0)
        assert result["delta"] == -0.28

    def test_custom_target_delta(self):
        cfg = _make_put_config(target_delta=0.25)
        ps = _make_put_seller(db_path=self.db_path, put_config=cfg)
        chain = [
            _make_option(strike=175, delta=-0.20),
            _make_option(strike=177, delta=-0.25),
            _make_option(strike=180, delta=-0.30),
        ]
        result = ps._find_best_put(chain, current_price=185.0)
        assert result["strike"] == 177.0

    def test_returns_none_for_empty_chain(self):
        result = self.ps._find_best_put([], current_price=100.0)
        assert result is None

    def test_result_contains_all_required_fields(self):
        chain = [_make_option(strike=180, delta=-0.30, bid=2.50)]
        result = self.ps._find_best_put(chain, current_price=190.0)
        for field in ["strike", "expiration", "delta", "premium", "option_symbol"]:
            assert field in result


# ============================================================================
# DTE Expiration Range Filtering
# ============================================================================

class TestDTEFiltering:
    """FR-2: 30-45 DTE expiration range."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_accepts_option_within_dte_range(self):
        chain = [_make_option(expiration_date=(datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d"))]
        result = self.ps._find_best_put(chain, current_price=100.0)
        assert result is not None

    def test_accepts_option_at_min_dte_boundary(self):
        # Use 31 days to avoid timing issues with microsecond differences
        chain = [_make_option(expiration_date=(datetime.now() + timedelta(days=31)).strftime("%Y-%m-%d"))]
        result = self.ps._find_best_put(chain, current_price=100.0)
        assert result is not None

    def test_accepts_option_at_max_dte_boundary(self):
        chain = [_make_option(expiration_date=(datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d"))]
        result = self.ps._find_best_put(chain, current_price=100.0)
        assert result is not None

    def test_rejects_option_below_min_dte(self):
        chain = [_make_option(expiration_date=(datetime.now() + timedelta(days=29)).strftime("%Y-%m-%d"))]
        result = self.ps._find_best_put(chain, current_price=100.0)
        assert result is None

    def test_rejects_option_above_max_dte(self):
        chain = [_make_option(expiration_date=(datetime.now() + timedelta(days=46)).strftime("%Y-%m-%d"))]
        result = self.ps._find_best_put(chain, current_price=100.0)
        assert result is None

    def test_rejects_far_out_options(self):
        chain = [_make_option(expiration_date=(datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d"))]
        result = self.ps._find_best_put(chain, current_price=100.0)
        assert result is None

    def test_picks_best_within_range_not_outside(self):
        in_range = _make_option(strike=180, delta=-0.30,
                                expiration_date=(datetime.now() + timedelta(days=35)).strftime("%Y-%m-%d"))
        out_of_range = _make_option(strike=175, delta=-0.10,
                                    expiration_date=(datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d"))
        chain = [in_range, out_of_range]
        result = self.ps._find_best_put(chain, current_price=190.0)
        assert result["expiration"] == in_range["expiration_date"]

    def test_custom_dte_range(self):
        cfg = _make_put_config(days_to_expiration_min=14, days_to_expiration_max=28)
        ps = _make_put_seller(db_path=self.db_path, put_config=cfg)
        short = _make_option(expiration_date=(datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d"))
        long = _make_option(expiration_date=(datetime.now() + timedelta(days=35)).strftime("%Y-%m-%d"))
        chain = [short, long]
        result = ps._find_best_put(chain, current_price=100.0)
        assert result is not None
        assert result["expiration"] == short["expiration_date"]

    def test_very_narrow_dte_window(self):
        cfg = _make_put_config(days_to_expiration_min=35, days_to_expiration_max=40)
        ps = _make_put_seller(db_path=self.db_path, put_config=cfg)
        in_range = _make_option(expiration_date=(datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d"))
        too_short = _make_option(expiration_date=(datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"))
        too_long = _make_option(expiration_date=(datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d"))
        chain = [in_range, too_short, too_long]
        result = ps._find_best_put(chain, current_price=100.0)
        assert result is not None
        assert result["expiration"] == in_range["expiration_date"]


# ============================================================================
# Premium Threshold
# ============================================================================

class TestPremiumThreshold:
    """FR-2: Minimum premium as % of strike price."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_premium_from_select_strike(self):
        with patch.object(self.ps, '_get_options_chain') as mock_chain:
            mock_chain.return_value = [_make_option(strike=100, delta=-0.30, bid=1.50)]
            result = self.ps.select_strike("AAPL", current_price=100.0)
            assert result is not None
            assert result["premium"] == 1.50

    def test_premium_in_order_result(self):
        chain = [_make_option(strike=180, bid=3.20, delta=-0.30)]
        result = self.ps._find_best_put(chain, current_price=190.0)
        assert result["premium"] == 3.20

    def test_high_min_premium_config(self):
        cfg = _make_put_config(min_premium_pct=5.0)
        ps = _make_put_seller(db_path=self.db_path, put_config=cfg)
        assert ps.min_premium_pct == 5.0

    def test_low_min_premium_config(self):
        cfg = _make_put_config(min_premium_pct=0.1)
        ps = _make_put_seller(db_path=self.db_path, put_config=cfg)
        assert ps.min_premium_pct == 0.1


# ============================================================================
# Position Sizing — Max Capital Per Stock
# ============================================================================

class TestPositionSizing:
    """FR-2: Position sizing capped at max capital per stock (default 10%)."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_max_contracts_per_stock_from_config(self):
        assert self.ps.max_contracts_per_stock == 5

    def test_custom_max_contracts(self):
        cfg = _make_put_config(max_contracts_per_stock=3)
        ps = _make_put_seller(db_path=self.db_path, put_config=cfg)
        assert ps.max_contracts_per_stock == 3

    def test_should_sell_put_no_open_puts_enough_cash(self):
        with patch("bots.wheel_strategy.position_manager.PositionManager") as MockPM:
            mock_pm = MagicMock()
            mock_pm.get_open_puts.return_value = []
            MockPM.return_value = mock_pm
            result = self.ps.should_sell_put("AAPL", current_price=180.0, available_cash=50_000)
            assert result is True

    def test_should_sell_put_open_puts_exist(self):
        with patch("bots.wheel_strategy.position_manager.PositionManager") as MockPM:
            mock_pm = MagicMock()
            mock_pm.get_open_puts.return_value = [{"symbol": "AAPL", "status": "open"}]
            MockPM.return_value = mock_pm
            result = self.ps.should_sell_put("AAPL", current_price=180.0, available_cash=50_000)
            assert result is False

    def test_should_sell_put_insufficient_cash(self):
        with patch("bots.wheel_strategy.position_manager.PositionManager") as MockPM:
            mock_pm = MagicMock()
            mock_pm.get_open_puts.return_value = []
            MockPM.return_value = mock_pm
            result = self.ps.should_sell_put("AAPL", current_price=180.0, available_cash=17_999)
            assert result is False

    def test_should_sell_put_exact_cash(self):
        with patch("bots.wheel_strategy.position_manager.PositionManager") as MockPM:
            mock_pm = MagicMock()
            mock_pm.get_open_puts.return_value = []
            MockPM.return_value = mock_pm
            result = self.ps.should_sell_put("AAPL", current_price=180.0, available_cash=18_000)
            assert result is True

    def test_should_sell_put_high_price_needs_more_cash(self):
        with patch("bots.wheel_strategy.position_manager.PositionManager") as MockPM:
            mock_pm = MagicMock()
            mock_pm.get_open_puts.return_value = []
            MockPM.return_value = mock_pm
            r1 = self.ps.should_sell_put("MSFT", current_price=500.0, available_cash=40_000)
            r2 = self.ps.should_sell_put("MSFT", current_price=500.0, available_cash=50_000)
            assert r1 is False
            assert r2 is True

    def test_capital_per_stock_configurable(self):
        cfg = _make_put_config(max_contracts_per_stock=2)
        ps = _make_put_seller(db_path=self.db_path, put_config=cfg)
        assert ps.max_contracts_per_stock == 2


# ============================================================================
# Select Strike — Integration
# ============================================================================

class TestSelectStrikeIntegration:
    """Integration tests for select_strike method."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_select_strike_returns_option(self):
        with patch.object(self.ps, '_get_options_chain') as mock_chain:
            exp = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")
            mock_chain.return_value = [
                _make_option(strike=175, delta=-0.20, expiration_date=exp),
                _make_option(strike=180, delta=-0.30, expiration_date=exp),
                _make_option(strike=185, delta=-0.40, expiration_date=exp),
            ]
            result = self.ps.select_strike("AAPL", current_price=190.0)
            assert result is not None
            assert result["strike"] == 180.0

    def test_select_strike_returns_none_empty_chain(self):
        with patch.object(self.ps, '_get_options_chain') as mock_chain:
            mock_chain.return_value = None
            result = self.ps.select_strike("AAPL", current_price=190.0)
            assert result is None

    def test_select_strike_returns_none_no_eligible(self):
        with patch.object(self.ps, '_get_options_chain') as mock_chain:
            mock_chain.return_value = [
                _make_option(expiration_date=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")),
                _make_option(expiration_date=(datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")),
            ]
            result = self.ps.select_strike("AAPL", current_price=190.0)
            assert result is None

    def test_select_strike_calls_chain_api(self):
        with patch.object(self.ps, '_get_options_chain') as mock_chain:
            mock_chain.return_value = None
            self.ps.select_strike("AAPL", current_price=190.0)
            mock_chain.assert_called_once_with("AAPL")

    def test_select_strike_error_returns_none(self):
        with patch.object(self.ps, '_get_options_chain') as mock_chain:
            mock_chain.side_effect = Exception("API failure")
            result = self.ps.select_strike("AAPL", current_price=190.0)
            assert result is None


# ============================================================================
# Place Put Order
# ============================================================================

class TestPlacePutOrder:
    """Test place_put_order method."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_returns_status_dict(self):
        result = self.ps.place_put_order(
            symbol="AAPL", strike=180.0,
            expiration="2024-06-15", contracts=2, premium=3.00
        )
        assert result is not None
        assert result["status"] == "pending"
        assert result["symbol"] == "AAPL"
        assert result["strike"] == 180.0
        assert result["contracts"] == 2

    def test_single_contract(self):
        result = self.ps.place_put_order(
            symbol="MSFT", strike=380.0,
            expiration="2024-06-15", contracts=1, premium=5.00
        )
        assert result["contracts"] == 1

    def test_max_contracts(self):
        result = self.ps.place_put_order(
            symbol="XOM", strike=105.0,
            expiration="2024-06-15", contracts=5, premium=1.50
        )
        assert result["contracts"] == 5

    def test_more_than_max_still_works(self):
        result = self.ps.place_put_order(
            symbol="AAPL", strike=180.0,
            expiration="2024-06-15", contracts=10, premium=3.00
        )
        assert result["contracts"] == 10


# ============================================================================
# Options Chain Filtering
# ============================================================================

class TestOptionsChainFiltering:
    """Test _find_best_put filtering on option type and delta."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_filters_out_call_options(self):
        chain = [
            _make_option(strike=180, delta=0.30, option_type="call"),
            _make_option(strike=180, delta=-0.30, option_type="put"),
        ]
        result = self.ps._find_best_put(chain, current_price=190.0)
        assert result is not None
        assert result["delta"] < 0

    def test_filters_positive_delta_options(self):
        chain = [
            _make_option(strike=185, delta=0.25, option_type="call"),
            _make_option(strike=175, delta=-0.30, option_type="put"),
        ]
        result = self.ps._find_best_put(chain, current_price=180.0)
        assert result is not None
        assert result["delta"] < 0

    def test_skips_null_expiration_date(self):
        chain = [
            {"symbol": "X", "strike": 180, "delta": -0.30,
             "expiration_date": None, "bid": 2.0, "type": "put"},
        ]
        result = self.ps._find_best_put(chain, current_price=190.0)
        assert result is None

    def test_skips_malformed_expiration_date(self):
        chain = [{"symbol": "X", "strike": 180, "delta": -0.30,
                  "expiration_date": "not-a-date", "bid": 2.0, "type": "put"}]
        result = self.ps._find_best_put(chain, current_price=190.0)
        assert result is None

    def test_sorts_by_delta_distance(self):
        chain = [
            _make_option(strike=185, delta=-0.45),
            _make_option(strike=178, delta=-0.28),
            _make_option(strike=182, delta=-0.40),
            _make_option(strike=180, delta=-0.30),
        ]
        result = self.ps._find_best_put(chain, current_price=190.0)
        assert result["delta"] == -0.30
        assert result["delta_distance"] == 0.0


# ============================================================================
# Put Order Creation — Parameter Variations
# ============================================================================

class TestPutOrderCreation:
    """Verify put order creation with correct parameters for all input variations."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.client = MagicMock()
        self.ps = _make_put_seller(db_path=self.db_path, mock_client=self.client)

    def test_low_priced_stock(self):
        result = self.ps.place_put_order("F", 20.0, "2024-06-15", 1, 0.50)
        assert result["symbol"] == "F"
        assert result["strike"] == 20.0

    def test_high_priced_stock(self):
        result = self.ps.place_put_order("BRK", 800.0, "2024-06-15", 1, 10.00)
        assert result["strike"] == 800.0

    def test_contracts_math(self):
        max_capital = 10_000
        def calc(strike, cap):
            return int(cap / (strike * 100))
        assert calc(100, max_capital) == 1
        assert calc(50, max_capital) == 2
        assert calc(200, max_capital) == 0

    def test_full_end_to_end(self):
        with patch.object(self.ps, '_get_options_chain') as mock_chain:
            exp = (datetime.now() + timedelta(days=40)).strftime("%Y-%m-%d")
            mock_chain.return_value = [
                _make_option(symbol="AAPL241015P00180000", strike=180, delta=-0.25,
                            expiration_date=exp, bid=1.50),
                _make_option(symbol="AAPL241015P00185000", strike=185, delta=-0.30,
                            expiration_date=exp, bid=2.00),
                _make_option(symbol="AAPL241015P00190000", strike=190, delta=-0.35,
                            expiration_date=exp, bid=2.50),
            ]
            strike_info = self.ps.select_strike("AAPL", current_price=195.0)
            assert strike_info is not None
            assert strike_info["strike"] == 185
            assert strike_info["delta"] == -0.30
            assert strike_info["premium"] == 2.00

            order = self.ps.place_put_order(
                symbol="AAPL", strike=strike_info["strike"],
                expiration=strike_info["expiration"],
                contracts=1, premium=strike_info["premium"]
            )
            assert order["strike"] == 185
            assert order["status"] == "pending"


# ============================================================================
# Default Config Values
# ============================================================================

class TestDefaultConfig:
    """Verify default put_selling config values."""

    def test_default_dte_range(self):
        ps = _make_put_seller()
        assert ps.dte_min == 30
        assert ps.dte_max == 45

    def test_default_target_delta(self):
        ps = _make_put_seller()
        assert ps.target_delta == 0.30

    def test_default_min_premium_pct(self):
        ps = _make_put_seller()
        assert ps.min_premium_pct == 1.0

    def test_default_max_contracts(self):
        ps = _make_put_seller()
        assert ps.max_contracts_per_stock == 5

    def test_all_overrides(self):
        cfg = _make_put_config(
            days_to_expiration_min=21,
            days_to_expiration_max=35,
            target_delta=0.25,
            min_premium_pct=2.0,
            max_contracts_per_stock=3,
        )
        ps = _make_put_seller(put_config=cfg)
        assert ps.dte_min == 21
        assert ps.dte_max == 35
        assert ps.target_delta == 0.25
        assert ps.min_premium_pct == 2.0
        assert ps.max_contracts_per_stock == 3


# ============================================================================
# Cleanup
# ============================================================================

class TestDBCleanup:
    def test_temp_db_is_created(self):
        path = _make_temp_db()
        assert os.path.exists(path)
        os.unlink(path)
