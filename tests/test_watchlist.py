"""
Tests for watchlist management (TB-032 / FR-1)
================================================
Tests for stock universe filtering:
- Fundamental filters (min market cap, dividend yield, sector)
- Technical filters (price above MA, relative strength)
- IV Rank filter (>50%)
- Watchlist CRUD operations
- Verify only eligible stocks appear when filters applied

Run with:
    pytest tests/test_watchlist.py -v

All tests are fully isolated — no live API calls are made.
Market data is mocked via dictionaries passed to filter methods.
"""
import pytest
import sqlite3
import tempfile
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from bots.wheel_strategy.watchlist_manager import WatchlistManager
from bots.wheel_strategy.db import init_db, add_watchlist_entry, get_watchlist


# ── Helper: create a temp database with seeded watchlist ────────────────────

def _make_temp_db():
    """Create a temporary wheel database with the full schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _seed_watchlist(db_path):
    """Add a standard set of watchlist stocks for testing."""
    stocks = [
        {"symbol": "AAPL", "sector": "Technology", "max_contracts": 5,
         "max_capital": 15000, "min_premium_pct": 1.0, "target_delta": 0.30, "enabled": 1},
        {"symbol": "MSFT", "sector": "Technology", "max_contracts": 3,
         "max_capital": 20000, "min_premium_pct": 1.5, "target_delta": 0.30, "enabled": 1},
        {"symbol": "JNJ", "sector": "Healthcare", "max_contracts": 4,
         "max_capital": 12000, "min_premium_pct": 1.0, "target_delta": 0.30, "enabled": 1},
        {"symbol": "KO", "sector": "Consumer Staples", "max_contracts": 6,
         "max_capital": 8000, "min_premium_pct": 2.0, "target_delta": 0.30, "enabled": 1},
        {"symbol": "XOM", "sector": "Energy", "max_contracts": 3,
         "max_capital": 10000, "min_premium_pct": 1.5, "target_delta": 0.30, "enabled": 1},
        {"symbol": "BAC", "sector": "Financials", "max_contracts": 5,
         "max_capital": 10000, "min_premium_pct": 1.0, "target_delta": 0.30, "enabled": 1},
        # Disabled stock — should not appear in enabled-only queries
        {"symbol": "DIS", "sector": "Entertainment", "max_contracts": 2,
         "max_capital": 5000, "min_premium_pct": 1.0, "target_delta": 0.30, "enabled": 0},
    ]
    for s in stocks:
        add_watchlist_entry(db_path, **s)


# Mock fundamental + technical data for each stock
def _default_market_data():
    """Return default market data dict for filter tests."""
    return {
        "AAPL": {"market_cap": 2_800_000_000_000, "dividend_yield": 0.5,
                  "current_price": 185.0, "ma50": 180.0, "ma200": 170.0,
                  "relative_strength": 85, "iv_rank": 45},
        "MSFT": {"market_cap": 2_500_000_000_000, "dividend_yield": 0.8,
                  "current_price": 380.0, "ma50": 375.0, "ma200": 350.0,
                  "relative_strength": 80, "iv_rank": 35},
        "JNJ": {"market_cap": 380_000_000_000, "dividend_yield": 3.0,
                 "current_price": 155.0, "ma50": 150.0, "ma200": 148.0,
                 "relative_strength": 60, "iv_rank": 25},
        "KO": {"market_cap": 260_000_000_000, "dividend_yield": 3.2,
               "current_price": 59.0, "ma50": 58.0, "ma200": 56.0,
               "relative_strength": 55, "iv_rank": 20},
        "XOM": {"market_cap": 410_000_000_000, "dividend_yield": 3.5,
                "current_price": 105.0, "ma50": 103.0, "ma200": 100.0,
                "relative_strength": 72, "iv_rank": 55},
        "BAC": {"market_cap": 250_000_000_000, "dividend_yield": 2.8,
                "current_price": 33.0, "ma50": 32.0, "ma200": 30.0,
                "relative_strength": 65, "iv_rank": 40},
        "DIS": {"market_cap": 170_000_000_000, "dividend_yield": 0.0,
                "current_price": 95.0, "ma50": 90.0, "ma200": 88.0,
                "relative_strength": 50, "iv_rank": 30},
        # Stocks with no market data (should be skipped by filters)
        "NEW1": {},
        "NEW2": {},
    }


# ============================================================================
# CRUD Operations
# ============================================================================

class TestWatchlistCRUD:
    """Test add, remove, update, and list operations on watchlist."""

    def _make_manager(self) -> WatchlistManager:
        path = _make_temp_db()
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    def test_add_symbol(self):
        mgr = self._make_manager()
        mgr.add_symbol("AAPL", sector="Technology")
        candidates = mgr.get_candidates()
        assert len(candidates) == 1
        assert candidates[0]["symbol"] == "AAPL"
        assert candidates[0]["sector"] == "Technology"

    def test_add_symbol_uppercase(self):
        mgr = self._make_manager()
        mgr.add_symbol("aapl")
        candidates = mgr.get_candidates()
        assert candidates[0]["symbol"] == "AAPL"

    def test_add_symbol_with_params(self):
        mgr = self._make_manager()
        mgr.add_symbol("TSLA", max_contracts=2, max_capital=5000,
                        min_premium_pct=2.0, target_delta=0.20)
        candidates = mgr.get_candidates()
        assert candidates[0]["max_contracts"] == 2
        assert candidates[0]["max_capital"] == 5000
        assert candidates[0]["min_premium_pct"] == 2.0
        assert candidates[0]["target_delta"] == 0.20

    def test_add_multiple_symbols(self):
        mgr = self._make_manager()
        for sym in ["AAPL", "MSFT", "JNJ"]:
            mgr.add_symbol(sym)
        candidates = mgr.get_candidates()
        assert len(candidates) == 3
        symbols = {c["symbol"] for c in candidates}
        assert symbols == {"AAPL", "MSFT", "JNJ"}

    def test_add_disabled_symbol(self):
        mgr = self._make_manager()
        mgr.add_symbol("BAD", enabled=False)
        candidates = mgr.get_candidates()
        # get_candidates only returns enabled entries
        assert len(candidates) == 0

    def test_remove_symbol(self):
        mgr = self._make_manager()
        mgr.add_symbol("AAPL")
        mgr.add_symbol("MSFT")
        assert mgr.remove_symbol("AAPL") is True
        candidates = mgr.get_candidates()
        assert len(candidates) == 1
        assert candidates[0]["symbol"] == "MSFT"

    def test_remove_nonexistent_symbol(self):
        mgr = self._make_manager()
        assert mgr.remove_symbol("NOPE") is False

    def test_remove_symbol_case_insensitive(self):
        mgr = self._make_manager()
        mgr.add_symbol("AAPL")
        assert mgr.remove_symbol("aapl") is True

    def test_get_candidates_empty(self):
        mgr = self._make_manager()
        assert mgr.get_candidates() == []

    def test_get_candidates_returns_copies(self):
        """Mutating a returned candidate should not affect the cache."""
        mgr = self._make_manager()
        mgr.add_symbol("AAPL", sector="Tech")
        c = mgr.get_candidates()
        c[0]["symbol"] = "MUTATED"
        assert mgr.get_candidates()[0]["symbol"] == "AAPL"

    def test_refresh_reloads_cache(self):
        mgr = self._make_manager()
        mgr.add_symbol("AAPL")
        mgr.add_symbol("MSFT")
        # Directly insert another entry bypassing the manager
        add_watchlist_entry(mgr.db_path, symbol="JNJ", sector="Healthcare")
        # Before refresh, manager cache should still have 2
        assert len(mgr.get_candidates()) == 2
        mgr.refresh()
        assert len(mgr.get_candidates()) == 3

    def test_sector_exposure(self):
        mgr = self._make_manager()
        mgr.add_symbol("AAPL", sector="Technology")
        mgr.add_symbol("MSFT", sector="Technology")
        mgr.add_symbol("JNJ", sector="Healthcare")
        exposure = mgr.get_sector_exposure()
        assert exposure["Technology"] == 2
        assert exposure["Healthcare"] == 1

    def test_sector_exposure_unknown_when_missing(self):
        mgr = self._make_manager()
        mgr.add_symbol("AAPL")  # no sector
        assert mgr.get_sector_exposure() == {"unknown": 1}

    def test_disabled_not_in_candidates(self):
        mgr = self._make_manager()
        mgr.add_symbol("AAPL", enabled=True)
        mgr.add_symbol("BAD", enabled=False)
        symbols = {c["symbol"] for c in mgr.get_candidates()}
        assert "AAPL" in symbols
        assert "BAD" not in symbols

    def test_add_watchlist_updates_underlying_db(self):
        """Adding via manager should persist to a fresh manager instance."""
        mgr = self._make_manager()
        mgr.add_symbol("AAPL", sector="Tech", max_contracts=3)
        # Create a new manager pointing to same DB
        mgr2 = WatchlistManager(db_path=mgr.db_path, config={"wheel_strategy": {}})
        candidates = mgr2.get_candidates()
        assert len(candidates) == 1
        assert candidates[0]["max_contracts"] == 3


# ============================================================================
# IV Rank Filter
# ============================================================================

class TestIVRankFilter:
    """Test filter_by_iv_rank — only high-IV stocks should pass."""

    def _make_seeded_manager(self):
        path = _make_temp_db()
        _seed_watchlist(path)
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    def test_default_threshold_50(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()
        filtered = mgr.filter_by_iv_rank(candidates, market_data)
        # Only XOM has iv_rank >= 50 (55)
        symbols = {c["symbol"] for c in filtered}
        assert symbols == {"XOM"}

    def test_custom_threshold(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()
        filtered = mgr.filter_by_iv_rank(candidates, market_data, min_iv_rank=30)
        # AAPL(45), MSFT(35), XOM(55), BAC(40) >= 30 (DIS is disabled)
        symbols = {c["symbol"] for c in filtered}
        assert symbols == {"AAPL", "MSFT", "XOM", "BAC"}

    def test_empty_candidates(self):
        mgr = self._make_seeded_manager()
        market_data = _default_market_data()
        filtered = mgr.filter_by_iv_rank([], market_data)
        assert filtered == []

    def test_missing_iv_rank_treated_as_zero(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = {"AAPL": {}, "XOM": {"iv_rank": 60}}
        filtered = mgr.filter_by_iv_rank(candidates, market_data, min_iv_rank=50)
        symbols = {c["symbol"] for c in filtered}
        # AAPL has no iv_rank, treated as 0; XOM has 60
        assert symbols == {"XOM"}

    def test_filter_attaches_iv_rank_to_result(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()
        filtered = mgr.filter_by_iv_rank(candidates, market_data)
        for c in filtered:
            assert "iv_rank" in c

    def test_all_rejected_below_threshold(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()
        filtered = mgr.filter_by_iv_rank(candidates, market_data, min_iv_rank=99)
        assert filtered == []

    def test_threshold_zero_accepts_all_with_data(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()
        filtered = mgr.filter_by_iv_rank(candidates, market_data, min_iv_rank=0)
        # All 6 enabled stocks have iv_rank defined
        assert len(filtered) == 6


# ============================================================================
# Earnings Filter
# ============================================================================

class TestEarningsFilter:
    """Test filter_by_earnings — skip stocks with earnings before expiration."""

    def _make_seeded_manager(self):
        path = _make_temp_db()
        _seed_watchlist(path)
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    def test_skip_stock_with_upcoming_earnings(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        now = datetime.now()
        earnings = {
            "AAPL": now + timedelta(days=10),   # within 45-day max_dte
            "MSFT": now + timedelta(days=60),   # beyond max_dte
        }
        filtered = mgr.filter_by_earnings(candidates, earnings, max_dte=45)
        symbols = {c["symbol"] for c in filtered}
        assert "AAPL" not in symbols
        assert "MSFT" in symbols

    def test_no_earnings_data_passes_through(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        # No earnings data for any stock
        filtered = mgr.filter_by_earnings(candidates, {}, max_dte=45)
        # All candidates should pass through
        assert len(filtered) == len(candidates)

    def test_earnings_exactly_on_cutoff(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        now = datetime.now()
        cutoff = now + timedelta(days=45)
        earnings = {"AAPL": cutoff}
        filtered = mgr.filter_by_earnings(candidates, earnings, max_dte=45)
        # Earnings exactly on cutoff should be skipped (<=)
        symbols = {c["symbol"] for c in filtered}
        assert "AAPL" not in symbols

    def test_earnings_one_day_past_cutoff(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        now = datetime.now()
        cutoff = now + timedelta(days=45)
        earnings = {"AAPL": cutoff + timedelta(days=1)}
        filtered = mgr.filter_by_earnings(candidates, earnings, max_dte=45)
        symbols = {c["symbol"] for c in filtered}
        assert "AAPL" in symbols

    def test_combined_iv_and_earnings_filter(self):
        """Chaining filters should give intersection of both."""
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # Step 1: IV rank filter (XOM = 55, only one >= 50)
        iv_filtered = mgr.filter_by_iv_rank(candidates, market_data, min_iv_rank=50)
        iv_symbols = {c["symbol"] for c in iv_filtered}
        assert iv_symbols == {"XOM"}

        # Step 2: earnings filter
        now = datetime.now()
        earnings = {"XOM": now + timedelta(days=20)}
        final = mgr.filter_by_earnings(iv_filtered, earnings, max_dte=45)
        assert len(final) == 0  # XOM has upcoming earnings, gets filtered out

        # If earnings are far out, XOM passes
        earnings2 = {"XOM": now + timedelta(days=90)}
        final2 = mgr.filter_by_earnings(iv_filtered, earnings2, max_dte=45)
        assert len(final2) == 1
        assert final2[0]["symbol"] == "XOM"


# ============================================================================
# Fundamental Filters
# ============================================================================

class TestFundamentalFilter:
    """Test fundamental filters: market cap, dividend yield, sector."""

    def _make_seeded_manager(self):
        path = _make_temp_db()
        _seed_watchlist(path)
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    # --- Market Cap ---

    def test_min_market_cap_filters_small_stocks(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # Filter: min market cap of 300B
        filtered = mgr.filter_by_fundamentals(candidates, market_data, min_market_cap=300_000_000_000)
        symbols = {c["symbol"] for c in filtered}
        # AAPL(2.8T), MSFT(2.5T), JNJ(380B), XOM(410B) >= 300B
        assert symbols == {"AAPL", "MSFT", "JNJ", "XOM"}

    def test_min_market_cap_exclusion(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # Filter: min market cap of 500B
        filtered = mgr.filter_by_fundamentals(candidates, market_data, min_market_cap=500_000_000_000)
        symbols = {c["symbol"] for c in filtered}
        # Only AAPL(2.8T) and MSFT(2.5T) >= 500B
        assert symbols == {"AAPL", "MSFT"}

    def test_min_market_cap_no_data_treated_as_reject(self):
        mgr = self._make_seeded_manager()
        mgr.add_symbol("NEW1")
        add_watchlist_entry(mgr.db_path, symbol="NEW2")
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_fundamentals(candidates, market_data, min_market_cap=1_000_000)
        symbols = {c["symbol"] for c in filtered}
        # NEW1 and NEW2 have no market data, should be rejected
        assert "NEW1" not in symbols
        assert "NEW2" not in symbols

    # --- Dividend Yield ---

    def test_min_dividend_yield_filters(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # Filter: min 3.0% dividend yield
        filtered = mgr.filter_by_fundamentals(candidates, market_data, min_dividend_yield=3.0)
        symbols = {c["symbol"] for c in filtered}
        # JNJ(3.0), KO(3.2), XOM(3.5) >= 3.0
        assert symbols == {"JNJ", "KO", "XOM"}

    def test_min_dividend_yield_exclusive(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # Filter: > 3.0% (strict >)
        filtered = mgr.filter_by_fundamentals(candidates, market_data, min_dividend_yield=3.01)
        symbols = {c["symbol"] for c in filtered}
        # KO(3.2), XOM(3.5) > 3.01
        assert symbols == {"KO", "XOM"}

    # --- Sector Filter ---

    def test_allowed_sectors(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_fundamentals(candidates, market_data, allowed_sectors=["Technology"])
        symbols = {c["symbol"] for c in filtered}
        assert symbols == {"AAPL", "MSFT"}

    def test_blocked_sectors(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_fundamentals(candidates, market_data, blocked_sectors=["Technology"])
        symbols = {c["symbol"] for c in filtered}
        assert "AAPL" not in symbols
        assert "MSFT" not in symbols
        assert "JNJ" in symbols
        assert "XOM" in symbols

    def test_allowed_and_blocked_combined(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # allowed_sectors takes precedence: only Technology passes,
        # then blocked_sectors removes nothing from that subset
        filtered = mgr.filter_by_fundamentals(
            candidates, market_data,
            allowed_sectors=["Technology", "Healthcare"],
            blocked_sectors=["Healthcare"],
        )
        # allowed_sectors filters to {AAPL, MSFT, JNJ}, then blocked removes JNJ
        symbols = {c["symbol"] for c in filtered}
        assert symbols == {"AAPL", "MSFT"}

    def test_sector_unknown_not_in_allowed(self):
        mgr = self._make_seeded_manager()
        mgr.add_symbol("UNKNOWN")  # no sector
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_fundamentals(candidates, market_data, allowed_sectors=["Technology"])
        symbols = {c["symbol"] for c in filtered}
        assert "UNKNOWN" not in symbols

    # --- Combined Fundamental Filters ---

    def test_multiple_fundamental_filters_intersection(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # min_market_cap=300B AND min_dividend_yield=3.0 AND allowed_sectors=Energy
        filtered = mgr.filter_by_fundamentals(
            candidates, market_data,
            min_market_cap=300_000_000_000,
            min_dividend_yield=3.0,
            allowed_sectors=["Energy"],
        )
        symbols = {c["symbol"] for c in filtered}
        # XOM: market_cap=410B (>= 300B), div_yield=3.5 (>= 3.0), sector=Energy (allowed)
        assert symbols == {"XOM"}

    def test_no_filters_returns_all_with_data(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_fundamentals(candidates, market_data)
        # All enabled stocks should pass
        assert len(filtered) == len(candidates)

    def test_empty_candidates(self):
        mgr = self._make_seeded_manager()
        market_data = _default_market_data()
        filtered = mgr.filter_by_fundamentals([], market_data, min_market_cap=1_000_000)
        assert filtered == []


# ============================================================================
# Technical Filters
# ============================================================================

class TestTechnicalFilter:
    """Test technical filters: price vs MA, relative strength."""

    def _make_seeded_manager(self):
        path = _make_temp_db()
        _seed_watchlist(path)
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    # --- Price vs Moving Average ---

    def test_price_above_ma50(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # All enabled stocks have current_price > ma50 in our test data
        filtered = mgr.filter_by_technicals(candidates, market_data, min_price_vs_ma50=1.0)
        symbols = {c["symbol"] for c in filtered}
        # AAPL(185>180), MSFT(380>375), JNJ(155>150), KO(59>58), XOM(105>103), BAC(33>32)
        assert symbols == {"AAPL", "MSFT", "JNJ", "KO", "XOM", "BAC"}

    def test_price_below_ma50_filtered_out(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()
        # Set AAPL price below MA50
        market_data["AAPL"]["current_price"] = 175.0

        filtered = mgr.filter_by_technicals(candidates, market_data, min_price_vs_ma50=1.0)
        symbols = {c["symbol"] for c in filtered}
        assert "AAPL" not in symbols
        assert "MSFT" in symbols  # still above MA50

    def test_price_within_percent_of_ma50(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # Allow price >= 98% of MA50 (within 2% below)
        market_data["AAPL"]["current_price"] = 178.0  # 178 / 180 = 0.989
        filtered = mgr.filter_by_technicals(candidates, market_data, min_price_vs_ma50=0.98)
        symbols = {c["symbol"] for c in filtered}
        assert "AAPL" in symbols  # 0.989 >= 0.98

    def test_price_vs_ma200(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_technicals(candidates, market_data, min_price_vs_ma200=1.05)
        # AAPL: 185/170=1.088, MSFT: 380/350=1.086, JNJ: 155/148=1.047, KO: 59/56=1.054
        # XOM: 105/100=1.05, BAC: 33/30=1.1
        # Only those with ratio >= 1.05
        symbols = {c["symbol"] for c in filtered}
        # AAPL(1.088), MSFT(1.086), KO(1.054), XOM(1.05), BAC(1.1)
        # JNJ(1.047) fails
        assert "JNJ" not in symbols
        assert "AAPL" in symbols
        assert "BAC" in symbols

    # --- Relative Strength ---

    def test_min_relative_strength(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_technicals(candidates, market_data, min_relative_strength=70)
        symbols = {c["symbol"] for c in filtered}
        # AAPL(85), MSFT(80), XOM(72) >= 70
        assert symbols == {"AAPL", "MSFT", "XOM"}

    def test_min_relative_strength_rejects_low(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_technicals(candidates, market_data, min_relative_strength=90)
        # None have RS >= 90
        assert filtered == []

    # --- Combined Technical Filters ---

    def test_technical_filters_combined(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        # price above ma50 (1.0) AND min RS >= 75
        filtered = mgr.filter_by_technicals(
            candidates, market_data,
            min_price_vs_ma50=1.0,
            min_relative_strength=75,
        )
        symbols = {c["symbol"] for c in filtered}
        # AAPL(price>ma50, RS=85), MSFT(price>ma50, RS=80), XOM(price>ma50, RS=72)
        # XOM RS=72 < 75, fails RS
        assert symbols == {"AAPL", "MSFT"}

    def test_no_technical_filters_returns_all_with_data(self):
        mgr = self._make_seeded_manager()
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_technicals(candidates, market_data)
        assert len(filtered) == len(candidates)

    def test_empty_candidates_technical(self):
        mgr = self._make_seeded_manager()
        market_data = _default_market_data()
        filtered = mgr.filter_by_technicals([], market_data, min_relative_strength=70)
        assert filtered == []

    def test_missing_price_data_rejects(self):
        mgr = self._make_seeded_manager()
        mgr.add_symbol("NOPRICE")
        candidates = mgr.get_candidates()
        market_data = _default_market_data()

        filtered = mgr.filter_by_technicals(candidates, market_data, min_price_vs_ma50=1.0)
        symbols = {c["symbol"] for c in filtered}
        assert "NOPRICE" not in symbols


# ============================================================================
# Eligible Stocks — Full Pipeline
# ============================================================================

class TestEligibleStocksFullPipeline:
    """Test the full filter pipeline: get_eligible_stocks applies all filters."""

    def _make_seeded_manager(self):
        path = _make_temp_db()
        _seed_watchlist(path)
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    def test_no_filters_returns_all_enabled(self):
        mgr = self._make_seeded_manager()
        eligible = mgr.get_eligible_stocks(
            market_data=_default_market_data(),
            min_iv_rank=None,  # skip IV filter
        )
        # 6 enabled stocks with market data
        assert len(eligible) == 6

    def test_iv_only_filter(self):
        mgr = self._make_seeded_manager()
        eligible = mgr.get_eligible_stocks(
            market_data=_default_market_data(),
            min_iv_rank=50,
        )
        symbols = {c["symbol"] for c in eligible}
        assert symbols == {"XOM"}

    def test_all_filters_combined(self):
        """Apply fundamental + technical + IV rank filters together."""
        mgr = self._make_seeded_manager()
        eligible = mgr.get_eligible_stocks(
            market_data=_default_market_data(),
            min_market_cap=200_000_000_000,
            min_dividend_yield=3.0,
            allowed_sectors=["Energy", "Consumer Staples"],
            min_price_vs_ma50=1.0,
            min_relative_strength=50,
            min_iv_rank=20,
        )
        symbols = {c["symbol"] for c in eligible}
        # XOM: mc=410B, div=3.5, sector=Energy, price>ma50, RS=72, iv=55
        # KO: mc=260B, div=3.2, sector=Consumer Staples, price>ma50, RS=55, iv=20
        assert symbols == {"XOM", "KO"}

    def test_strict_filters_yield_empty(self):
        mgr = self._make_seeded_manager()
        eligible = mgr.get_eligible_stocks(
            market_data=_default_market_data(),
            min_market_cap=3_000_000_000_000,  # only AAPL qualifies
            min_dividend_yield=2.0,              # AAPL only has 0.5
        )
        assert len(eligible) == 0

    def test_missing_market_data_excludes_from_eligible(self):
        mgr = self._make_seeded_manager()
        mgr.add_symbol("NOMDATA")
        eligible = mgr.get_eligible_stocks(
            market_data=_default_market_data(),
        )
        symbols = {c["symbol"] for c in eligible}
        # Enabled stocks with market data
        assert "NOMDATA" not in symbols

    def test_disabled_stock_never_eligible(self):
        mgr = self._make_seeded_manager()
        # DIS is disabled but has market data
        eligible = mgr.get_eligible_stocks(market_data=_default_market_data())
        symbols = {c["symbol"] for c in eligible}
        assert "DIS" not in symbols

    def test_fundamental_alone(self):
        """Filter only by fundamentals, no IV or technical constraints."""
        mgr = self._make_seeded_manager()
        eligible = mgr.get_eligible_stocks(
            market_data=_default_market_data(),
            min_market_cap=200_000_000_000,
            min_iv_rank=None,  # disable IV
            min_price_vs_ma50=None,
            min_price_vs_ma200=None,
            min_relative_strength=None,
        )
        symbols = {c["symbol"] for c in eligible}
        # All stocks with market_cap >= 200B
        # AAPL(2.8T), MSFT(2.5T), JNJ(380B), KO(260B), XOM(410B), BAC(250B)
        assert symbols == {"AAPL", "MSFT", "JNJ", "KO", "XOM", "BAC"}

    def test_technical_alone(self):
        """Filter only by technicals, no fundamental or IV constraints."""
        mgr = self._make_seeded_manager()
        eligible = mgr.get_eligible_stocks(
            market_data=_default_market_data(),
            min_market_cap=None,
            min_dividend_yield=None,
            min_iv_rank=None,
            min_relative_strength=65,
        )
        symbols = {c["symbol"] for c in eligible}
        # AAPL(85), MSFT(80), XOM(72), BAC(65) >= 65
        assert symbols == {"AAPL", "MSFT", "XOM", "BAC"}


# ============================================================================
# Sector Concentration Check
# ============================================================================

class TestSectorConcentration:
    """Verify sector exposure calculations used for concentration limits."""

    def _make_manager(self):
        path = _make_temp_db()
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    def _make_seeded_manager(self):
        path = _make_temp_db()
        _seed_watchlist(path)
        return WatchlistManager(db_path=path, config={"wheel_strategy": {}})

    def test_sector_counts(self):
        mgr = self._make_seeded_manager()
        exposure = mgr.get_sector_exposure()
        assert exposure["Technology"] == 2
        assert exposure["Healthcare"] == 1
        assert exposure["Consumer Staples"] == 1
        assert exposure["Energy"] == 1
        assert exposure["Financials"] == 1

    def test_sector_concentration_check(self):
        """Test that get_sector_exposure correctly counts sectors."""
        mgr = self._make_seeded_manager()
        exposure = mgr.get_sector_exposure()
        tech_count = exposure.get("Technology", 0)
        total = sum(exposure.values())
        pct = tech_count / total * 100
        # Technology = 2 out of 6 = 33.3%
        assert tech_count == 2
        assert pct == pytest.approx(33.33, abs=0.1)

    def test_sector_concentration_empty(self):
        mgr = self._make_manager()
        ok, _ = mgr.check_sector_concentration(max_concentration_pct=30, total_positions=5)
        assert ok is True


# ============================================================================
# Cleanup
# ============================================================================

class TestDatabaseCleanup:
    """Verify database lifecycle — files are created and can be removed."""

    def test_temp_db_file_is_created(self):
        path = _make_temp_db()
        assert os.path.exists(path)
        os.unlink(path)

    def test_watchlist_persists_across_connections(self):
        path = _make_temp_db()
        add_watchlist_entry(path, symbol="AAPL", sector="Technology")
        # Verify in a fresh connection
        entries = get_watchlist(path)
        assert len(entries) == 1
        assert entries[0]["symbol"] == "AAPL"
        os.unlink(path)
