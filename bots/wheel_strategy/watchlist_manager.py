"""
Watchlist manager for wheel strategy bot.
Maintains and filters stocks eligible for the wheel strategy.
Handles filtering by fundamentals (market cap, dividend, sector),
technicals (price vs MA, relative strength), and IV rank.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class WatchlistManager:
    """
    Manages the watchlist of stocks eligible for the wheel strategy.
    Handles filtering by fundamentals, technicals, IV rank, and earnings.
    """

    def __init__(self, db_path: str, config: Dict[str, Any]):
        """
        Initialize watchlist manager.

        Args:
            db_path: Path to SQLite database
            config: Wheel strategy configuration dictionary
        """
        self.db_path = db_path
        self.config = config
        self._watchlist_cache: List[Dict[str, Any]] = []
        self._load_watchlist()

    def _load_watchlist(self) -> None:
        """Load watchlist from database."""
        from bots.wheel_strategy.db import get_watchlist

        self._watchlist_cache = get_watchlist(self.db_path, enabled_only=True)
        logger.info(f"Loaded {len(self._watchlist_cache)} watchlist entries")

    def get_candidates(self) -> List[Dict[str, Any]]:
        """
        Get all enabled watchlist entries as wheel candidates.

        Returns:
            List of watchlist entries with wheel strategy parameters
        """
        return [entry.copy() for entry in self._watchlist_cache]

    # ── Fundamental Filters ────────────────────────────────────────────

    def filter_by_fundamentals(
        self,
        candidates: List[Dict[str, Any]],
        market_data: Dict[str, Dict[str, Any]],
        min_market_cap: Optional[float] = None,
        min_dividend_yield: Optional[float] = None,
        allowed_sectors: Optional[List[str]] = None,
        blocked_sectors: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter candidates by fundamental criteria.

        Args:
            candidates: List of watchlist entries
            market_data: Dict mapping symbol -> fundamental data dict
            min_market_cap: Minimum market cap threshold
            min_dividend_yield: Minimum dividend yield percentage
            allowed_sectors: Only allow these sectors (if specified)
            blocked_sectors: Exclude these sectors (if specified)

        Returns:
            Filtered list of candidates
        """
        filtered = []
        for candidate in candidates:
            symbol = candidate["symbol"]
            data = market_data.get(symbol)

            if data is None:
                continue

            # Market cap check
            if min_market_cap is not None:
                mc = data.get("market_cap")
                if mc is None or mc < min_market_cap:
                    continue

            # Dividend yield check
            if min_dividend_yield is not None:
                dy = data.get("dividend_yield")
                if dy is None or dy < min_dividend_yield:
                    continue

            # Sector filter: allowed
            if allowed_sectors is not None:
                sector = candidate.get("sector", None)
                if sector is None or sector not in allowed_sectors:
                    continue

            # Sector filter: blocked
            if blocked_sectors is not None:
                sector = candidate.get("sector", None)
                if sector is not None and sector in blocked_sectors:
                    continue

            filtered.append(candidate)

        logger.debug(
            f"Fundamental filter: {len(candidates)} -> {len(filtered)} candidates "
            f"(cap={min_market_cap}, yield={min_dividend_yield}, "
            f"allowed={allowed_sectors}, blocked={blocked_sectors})"
        )
        return filtered

    # ── Technical Filters ──────────────────────────────────────────────

    def filter_by_technicals(
        self,
        candidates: List[Dict[str, Any]],
        market_data: Dict[str, Dict[str, Any]],
        min_price_vs_ma50: Optional[float] = None,
        min_price_vs_ma200: Optional[float] = None,
        min_relative_strength: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter candidates by technical criteria.

        Args:
            candidates: List of watchlist entries
            market_data: Dict mapping symbol -> technical data dict
            min_price_vs_ma50: Stock price must be >= this ratio of 50-day MA
            min_price_vs_ma200: Stock price must be >= this ratio of 200-day MA
            min_relative_strength: Minimum RS score (e.g., 70 for top 30%)

        Returns:
            Filtered list of candidates
        """
        filtered = []
        for candidate in candidates:
            symbol = candidate["symbol"]
            data = market_data.get(symbol)

            if data is None:
                continue

            price = data.get("current_price")
            if price is None:
                continue

            # Price vs 50-day MA
            if min_price_vs_ma50 is not None:
                ma50 = data.get("ma50")
                if ma50 is None or price < ma50 * min_price_vs_ma50:
                    continue

            # Price vs 200-day MA
            if min_price_vs_ma200 is not None:
                ma200 = data.get("ma200")
                if ma200 is None or price < ma200 * min_price_vs_ma200:
                    continue

            # Relative strength
            if min_relative_strength is not None:
                rs = data.get("relative_strength")
                if rs is None or rs < min_relative_strength:
                    continue

            filtered.append(candidate)

        logger.debug(
            f"Technical filter: {len(candidates)} -> {len(filtered)} candidates "
            f"(ma50={min_price_vs_ma50}, ma200={min_price_vs_ma200}, "
            f"rs={min_relative_strength})"
        )
        return filtered

    # ── Combined Eligibility Pipeline ──────────────────────────────────

    def get_eligible_stocks(
        self,
        market_data: Dict[str, Dict[str, Any]],
        min_market_cap: Optional[float] = None,
        min_dividend_yield: Optional[float] = None,
        allowed_sectors: Optional[List[str]] = None,
        blocked_sectors: Optional[List[str]] = None,
        min_price_vs_ma50: Optional[float] = None,
        min_price_vs_ma200: Optional[float] = None,
        min_relative_strength: Optional[float] = None,
        min_iv_rank: Optional[float] = 50.0,
    ) -> List[Dict[str, Any]]:
        """
        Get stocks that pass ALL filters — the main entry point for
        determining eligible wheel candidates.

        Args:
            market_data: Dict mapping symbol -> {market_cap, dividend_yield,
                current_price, ma50, ma200, relative_strength, iv_rank}
            min_market_cap: Minimum market cap
            min_dividend_yield: Minimum dividend yield %
            allowed_sectors / blocked_sectors: Sector constraints
            min_price_vs_ma50 / min_price_vs_ma200: Price/MA ratios
            min_relative_strength: Minimum RS score
            min_iv_rank: Minimum IV rank % (None to skip)

        Returns:
            List of eligible watchlist entries
        """
        candidates = self.get_candidates()

        # Fundamental filter
        candidates = self.filter_by_fundamentals(
            candidates, market_data,
            min_market_cap=min_market_cap,
            min_dividend_yield=min_dividend_yield,
            allowed_sectors=allowed_sectors,
            blocked_sectors=blocked_sectors,
        )

        # Technical filter
        candidates = self.filter_by_technicals(
            candidates, market_data,
            min_price_vs_ma50=min_price_vs_ma50,
            min_price_vs_ma200=min_price_vs_ma200,
            min_relative_strength=min_relative_strength,
        )

        # IV rank filter
        if min_iv_rank is not None:
            candidates = self.filter_by_iv_rank(candidates, market_data, min_iv_rank=min_iv_rank)

        logger.info(f"Eligible stocks: {len(candidates)} candidates pass all filters")
        return candidates

    # ── Sector Concentration ─────────────────────────────────────────────

    def check_sector_concentration(
        self,
        max_concentration_pct: float,
        total_positions: Optional[int] = None,
    ) -> tuple:
        """
        Check if any sector exceeds the concentration limit.

        Args:
            max_concentration_pct: Maximum % of positions in one sector
            total_positions: Total position count (if None, uses watchlist count)

        Returns:
            (ok: bool, sector: str or None) — (False, sector_name) if violated
        """
        from bots.wheel_strategy.db import get_open_options, get_open_stock_positions

        # Count positions by sector across open options + stock positions
        sector_counts: Dict[str, int] = {}

        # Count from open options
        open_options = get_open_options(self.db_path)
        for pos in open_options:
            symbol = pos["symbol"]
            # Find sector from watchlist
            for entry in self._watchlist_cache:
                if entry["symbol"] == symbol:
                    sector = entry.get("sector", "unknown")
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1
                    break

        # Count from stock positions
        stock_positions = get_open_stock_positions(self.db_path)
        for pos in stock_positions:
            symbol = pos["symbol"]
            for entry in self._watchlist_cache:
                if entry["symbol"] == symbol:
                    sector = entry.get("sector", "unknown")
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1
                    break

        total = total_positions or sum(sector_counts.values(), 1)  # avoid div by 0
        for sector, count in sector_counts.items():
            if (count / total * 100) > max_concentration_pct:
                return (False, sector)

        return (True, None)

    # ── Existing Methods ────────────────────────────────────────────────

    def filter_by_iv_rank(
        self,
        candidates: List[Dict[str, Any]],
        market_data: Dict[str, Dict[str, Any]],
        min_iv_rank: float = 50.0,
    ) -> List[Dict[str, Any]]:
        """
        Filter candidates by minimum IV rank.

        Args:
            candidates: List of watchlist entries
            market_data: Dict mapping symbol -> data dict (must have iv_rank)
            min_iv_rank: Minimum IV rank threshold (default 50%)

        Returns:
            Filtered list of candidates
        """
        filtered = []
        for candidate in candidates:
            symbol = candidate["symbol"]
            data = market_data.get(symbol, {})
            iv_rank = data.get("iv_rank", 0)
            if iv_rank >= min_iv_rank:
                candidate["iv_rank"] = iv_rank
                filtered.append(candidate)
                logger.info(
                    f"Watchlist candidate {symbol} accepted (IV rank: {iv_rank:.1f}%)"
                )
            else:
                logger.debug(
                    f"Watchlist candidate {symbol} rejected (IV rank: {iv_rank:.1f}% < {min_iv_rank}%)"
                )
        return filtered

    def filter_by_earnings(
        self,
        candidates: List[Dict[str, Any]],
        earnings_dates: Dict[str, datetime],
        max_dte: int = 45,
    ) -> List[Dict[str, Any]]:
        """
        Filter out candidates with earnings before option expiration.

        Args:
            candidates: List of watchlist entries
            earnings_dates: Dict mapping symbol -> next earnings date
            max_dte: Maximum days to expiration to check against

        Returns:
            Filtered list of candidates
        """
        from datetime import timedelta

        filtered = []
        cutoff = datetime.now() + timedelta(days=max_dte)

        for candidate in candidates:
            symbol = candidate["symbol"]
            earnings = earnings_dates.get(symbol)
            if earnings and earnings <= cutoff:
                logger.info(
                    f"Skipping {symbol} — earnings on "
                    f"{earnings.strftime('%Y-%m-%d')} before expiration"
                )
                continue
            filtered.append(candidate)

        return filtered

    def get_sector_exposure(self) -> Dict[str, int]:
        """
        Get current count of watchlist entries per sector.

        Returns:
            Dict mapping sector -> count
        """
        exposure: Dict[str, int] = {}
        for entry in self._watchlist_cache:
            sector = entry.get("sector") or "unknown"
            exposure[sector] = exposure.get(sector, 0) + 1
        return exposure

    def add_symbol(self, symbol: str, **kwargs) -> None:
        """
        Add a symbol to the watchlist.

        Args:
            symbol: Stock ticker symbol
            **kwargs: Optional parameters (max_contracts, max_capital, etc.)
        """
        symbol = symbol.upper()
        from bots.wheel_strategy.db import add_watchlist_entry

        add_watchlist_entry(self.db_path, symbol, **kwargs)
        self._load_watchlist()
        logger.info(f"Added {symbol} to wheel watchlist")

    def remove_symbol(self, symbol: str) -> bool:
        """Remove a symbol from the watchlist."""
        import sqlite3

        symbol = symbol.upper()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM wheel_watchlist WHERE symbol = ?", (symbol,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        if deleted:
            self._load_watchlist()
            logger.info(f"Removed {symbol} from wheel watchlist")
        return deleted

    def refresh(self) -> None:
        """Reload watchlist from database."""
        self._load_watchlist()
