"""
Tests for earnings protection (TB-040 / FR-9)
==============================================
Test that the bot avoids selling options through earnings announcements:
- Check earnings calendar before selling new puts or calls
- Skip stocks with earnings dates before option expiration
- Optionally close positions before earnings if risk is too high

Run with:
    pytest tests/test_earnings_protection.py -v

All tests are fully isolated — no live API calls are made.
Earnings calendar data is mocked or seeded in the SQLite DB.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

from bots.wheel_strategy.earnings_checker import EarningsChecker
from bots.wheel_strategy.db import (
    init_db,
    get_upcoming_earnings,
    cache_earnings,
)


# ── Helper fixtures ────────────────────────────────────────────────────────

def _make_temp_db():
    """Create a temporary database with schema initialized."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _make_config(avoid_earnings=True, call_selling=None):
    """Build a wheel strategy config dict."""
    cfg = {
        "put_selling": {
            "avoid_earnings": avoid_earnings,
        }
    }
    if call_selling is not None:
        cfg["call_selling"] = call_selling
    return cfg


def _make_checker(db_path=None, config=None):
    if db_path is None:
        db_path = _make_temp_db()
    if config is None:
        config = _make_config()
    return EarningsChecker(db_path=db_path, config=config)


def _add_earnings(db_path, symbol, earnings_date, etype="quarterly"):
    """Seed an earnings date into the wheel_earnings table."""
    cache_earnings(db_path, symbol, earnings_date, type=etype)


# ── Test: EarningsChecker Initialization ───────────────────────────────────

class TestEarningsCheckerInit:
    """Test initialization and configuration parsing."""

    def test_avoid_earnings_defaults_true(self):
        """When put_selling.avoid_earnings is not set, it should default to True."""
        cfg = {"put_selling": {}}
        checker = _make_checker(config=cfg)
        assert checker.avoid_earnings is True

    def test_avoid_earnings_false_when_explicitly_set(self):
        """Should respect False setting."""
        cfg = _make_config(avoid_earnings=False)
        checker = _make_checker(config=cfg)
        assert checker.avoid_earnings is False

    def test_avoid_earnings_true_when_explicitly_set(self):
        cfg = _make_config(avoid_earnings=True)
        checker = _make_checker(config=cfg)
        assert checker.avoid_earnings is True

    def test_empty_config(self):
        """Should gracefully handle empty config."""
        checker = _make_checker(config={})
        # Should default to True (or not crash)
        assert checker.avoid_earnings is True


# ── Test: has_earnings_before ──────────────────────────────────────────────

class TestHasEarningsBefore:
    """FR-9: Check if a symbol has earnings before option expiration."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.checker = _make_checker(db_path=self.db_path)

    def _date(self, days):
        """Return YYYY-MM-DD string for now + days."""
        return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    # --- No earnings data ---

    def test_returns_none_when_no_earnings_data(self):
        """No earnings cached → should return None."""
        result = self.checker.has_earnings_before("AAPL", self._date(37))
        assert result is None

    def test_returns_none_when_avoid_earnings_disabled(self):
        """When avoid_earnings=False, should always return None."""
        checker = _make_checker(db_path=self.db_path, config=_make_config(avoid_earnings=False))
        _add_earnings(self.db_path, "AAPL", self._date(10))
        result = checker.has_earnings_before("AAPL", self._date(37))
        assert result is None

    # --- Earnings before expiration ---

    def test_returns_earnings_date_when_before_expiration(self):
        """Earnings in 10 days, expiration in 37 → should return date."""
        _add_earnings(self.db_path, "AAPL", self._date(10))
        result = self.checker.has_earnings_before("AAPL", self._date(37))
        assert result is not None
        assert result == self._date(10)

    def test_returns_earliest_earnings_when_multiple(self):
        """Multiple earnings dates → should return the earliest before expiration."""
        _add_earnings(self.db_path, "AAPL", self._date(10))
        _add_earnings(self.db_path, "AAPL", self._date(20))
        _add_earnings(self.db_path, "AAPL", self._date(30))
        result = self.checker.has_earnings_before("AAPL", self._date(45))
        assert result == self._date(10)

    def test_different_symbol_not_matched(self):
        """Other symbol's earnings should not affect the query."""
        _add_earnings(self.db_path, "MSFT", self._date(10))
        result = self.checker.has_earnings_before("AAPL", self._date(37))
        assert result is None

    # --- Earnings after expiration ---

    def test_returns_none_when_earnings_after_expiration(self):
        """Earnings after expiration → should return None."""
        _add_earnings(self.db_path, "AAPL", self._date(50))
        result = self.checker.has_earnings_before("AAPL", self._date(37))
        assert result is None

    # --- Earnings on/boundary ---

    def test_returns_earnings_on_same_day_as_expiration(self):
        """Earnings on expiration date → should detect it."""
        date = self._date(30)
        _add_earnings(self.db_path, "AAPL", date)
        result = self.checker.has_earnings_before("AAPL", date)
        assert result == date


# ── Test: is_safe_to_sell ──────────────────────────────────────────────────

class TestIsSafeToSell:
    """FR-9: Decide whether it's safe to sell options on a symbol."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.checker = _make_checker(db_path=self.db_path)

    def _date(self, days):
        return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    # --- Safe scenarios ---

    def test_safe_when_no_earnings_data(self):
        """No earnings cached → should be safe."""
        assert self.checker.is_safe_to_sell("AAPL") is True

    def test_safe_when_avoid_earnings_disabled(self):
        """avoid_earnings=False → always safe."""
        checker = _make_checker(db_path=self.db_path, config=_make_config(avoid_earnings=False))
        _add_earnings(self.db_path, "AAPL", self._date(5))
        assert checker.is_safe_to_sell("AAPL") is True

    def test_safe_when_earnings_after_max_dte(self):
        """Earnings after the DTE window → safe."""
        _add_earnings(self.db_path, "AAPL", self._date(60))
        assert self.checker.is_safe_to_sell("AAPL", dte_min=30, dte_max=45) is True

    # --- Unsafe scenarios ---

    def test_not_safe_when_earnings_in_dte_range(self):
        """Earnings within the DTE window → NOT safe."""
        _add_earnings(self.db_path, "AAPL", self._date(37))
        assert self.checker.is_safe_to_sell("AAPL", dte_min=30, dte_max=45) is False

    def test_not_safe_when_earnings_very_soon(self):
        """Earnings in 5 days → NOT safe (within max DTE)."""
        _add_earnings(self.db_path, "AAPL", self._date(5))
        assert self.checker.is_safe_to_sell("AAPL", dte_min=30, dte_max=45) is False

    # --- Edge cases ---

    def test_custom_dte_range(self):
        """Custom DTE range should affect safety check."""
        _add_earnings(self.db_path, "AAPL", self._date(20))
        # With dte_max=15, earnings at day 20 is outside window → safe
        assert self.checker.is_safe_to_sell("AAPL", dte_min=7, dte_max=15) is True


# ── Test: refresh_earnings_data ────────────────────────────────────────────

class TestRefreshEarningsData:
    """FR-9: Refresh earnings data from external API into DB cache."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.checker = _make_checker(db_path=self.db_path)

    def _date(self, days):
        return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    # --- Basic refresh ---

    def test_refresh_caches_earnings(self):
        """After refresh, earnings should be queryable from DB."""
        symbols = ["AAPL"]
        fetched = {
            "AAPL": [(self._date(10), "quarterly"), (self._date(100), "quarterly")]
        }
        count = self.checker.refresh_earnings_data(symbols, fetch_func=lambda s: fetched.get(s, []))
        assert count == 2

        result = get_upcoming_earnings(self.db_path, "AAPL", self._date(50))
        assert result == self._date(10)

    def test_refresh_returns_total_count(self):
        """Should return total number of entries cached."""
        symbols = ["AAPL", "MSFT"]
        fetched = {
            "AAPL": [(self._date(10), "quarterly")],
            "MSFT": [(self._date(15), "quarterly"), (self._date(90), "quarterly")],
        }
        count = self.checker.refresh_earnings_data(symbols, fetch_func=lambda s: fetched.get(s, []))
        assert count == 3

    def test_refresh_skips_symbol_with_no_earnings(self):
        """Symbol with no earnings data → count unchanged."""
        fetched = {"AAPL": []}
        count = self.checker.refresh_earnings_data(["AAPL"], fetch_func=lambda s: fetched.get(s, []))
        assert count == 0

    # --- No fetch_func ---

    def test_refresh_without_fetch_func_does_nothing(self):
        """When no fetch_func provided, should skip and return 0."""
        count = self.checker.refresh_earnings_data(["AAPL"])
        assert count == 0

    # --- Fetch func variations ---

    def test_refresh_with_date_strings_only(self):
        """Fetch func returning flat date strings (not tuples)."""
        fetched = {"AAPL": [self._date(20)]}
        count = self.checker.refresh_earnings_data(["AAPL"], fetch_func=lambda s: fetched.get(s, []))
        assert count == 1

        result = get_upcoming_earnings(self.db_path, "AAPL", self._date(30))
        assert result is not None

    def test_refresh_with_mixed_tuple_formats(self):
        """Fetch func with varying tuple lengths."""
        fetched = {
            "AAPL": [
                (self._date(20), "quarterly"),
                (self._date(80),),  # no type
                self._date(120),     # flat string
            ]
        }
        count = self.checker.refresh_earnings_data(["AAPL"], fetch_func=lambda s: fetched.get(s, []))
        assert count == 3


# ── Test: Earnings Protection Integration Scenarios ────────────────────────

class TestEarningsProtectionIntegration:
    """End-to-end scenarios simulating real-world usage."""

    def setup_method(self):
        self.db_path = _make_temp_db()
        self.checker = _make_checker(db_path=self.db_path)

    def _date(self, days):
        return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    def test_sell_put_blocked_by_upcoming_earnings(self):
        """Should block selling a put when earnings occur before expiration."""
        _add_earnings(self.db_path, "AAPL", self._date(15))
        expiration = self._date(37)

        earnings_date = self.checker.has_earnings_before("AAPL", expiration)
        assert earnings_date is not None

        safe = self.checker.is_safe_to_sell("AAPL", dte_min=30, dte_max=45)
        assert safe is False

    def test_sell_put_allowed_when_no_earnings(self):
        """Should allow selling a put when no earnings in range."""
        safe = self.checker.is_safe_to_sell("TSLA", dte_min=30, dte_max=45)
        assert safe is True

    def test_sell_put_allowed_when_earnings_after_expiration(self):
        """Should allow selling when earnings are after the option expires."""
        _add_earnings(self.db_path, "MSFT", self._date(80))
        safe = self.checker.is_safe_to_sell("MSFT", dte_min=30, dte_max=45)
        assert safe is True

    def test_earnings_protection_for_call_selling(self):
        """Same earnings check applies to covered call selling."""
        _add_earnings(self.db_path, "XOM", self._date(20))

        # Selling calls with 30-45 DTE
        safe = self.checker.is_safe_to_sell("XOM", dte_min=30, dte_max=45)
        assert safe is False

    def test_multi_symbol_earnings_check(self):
        """Only symbols with upcoming earnings should be blocked."""
        _add_earnings(self.db_path, "AAPL", self._date(10))
        # MSFT has no earnings data

        aapl_safe = self.checker.is_safe_to_sell("AAPL")
        msft_safe = self.checker.is_safe_to_sell("MSFT")

        assert aapl_safe is False
        assert msft_safe is True

    def test_refresh_then_sell_decision(self):
        """After refreshing data, sell decisions should reflect new earnings."""
        # Initial state: no earnings → safe
        assert self.checker.is_safe_to_sell("AAPL") is True

        # Simulate API refresh adding earnings
        self.checker.refresh_earnings_data(
            ["AAPL"],
            fetch_func=lambda s: [(self._date(37), "quarterly")] if s == "AAPL" else []
        )

        # Now unsafe
        safe = self.checker.is_safe_to_sell("AAPL", dte_min=30, dte_max=45)
        assert safe is False
