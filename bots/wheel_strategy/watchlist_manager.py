"""
Watchlist manager for wheel strategy bot.
Maintains and filters stocks eligible for the wheel strategy.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class WatchlistManager:
    """
    Manages the watchlist of stocks eligible for the wheel strategy.
    Handles filtering by IV rank, earnings, and sector concentration.
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

    def filter_by_iv_rank(
        self,
        candidates: List[Dict[str, Any]],
        iv_rank_data: Dict[str, float],
        min_iv_rank: float = 50.0
    ) -> List[Dict[str, Any]]:
        """
        Filter candidates by minimum IV rank.

        Args:
            candidates: List of watchlist entries
            iv_rank_data: Dict mapping symbol -> current IV rank
            min_iv_rank: Minimum IV rank threshold (default 50%)

        Returns:
            Filtered list of candidates
        """
        filtered = []
        for candidate in candidates:
            symbol = candidate['symbol']
            iv_rank = iv_rank_data.get(symbol, 0)
            if iv_rank >= min_iv_rank:
                candidate['iv_rank'] = iv_rank
                filtered.append(candidate)
                logger.info(f"Watchlist candidate {symbol} accepted (IV rank: {iv_rank:.1f}%)")
            else:
                logger.debug(f"Watchlist candidate {symbol} rejected (IV rank: {iv_rank:.1f}% < {min_iv_rank}%)")
        return filtered

    def filter_by_earnings(
        self,
        candidates: List[Dict[str, Any]],
        earnings_dates: Dict[str, datetime],
        max_dte: int = 45
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
        filtered = []
        cutoff = datetime.now() + __import__('datetime', fromlist=['timedelta']).timedelta(days=max_dte)

        for candidate in candidates:
            symbol = candidate['symbol']
            earnings = earnings_dates.get(symbol)
            if earnings and earnings <= cutoff:
                logger.info(f"Skipping {symbol} — earnings on {earnings.strftime('%Y-%m-%d')} before expiration")
                continue
            filtered.append(candidate)

        return filtered

    def get_sector_exposure(self) -> Dict[str, int]:
        """
        Get current count of positions per sector.

        Returns:
            Dict mapping sector -> count of active positions
        """
        exposure: Dict[str, int] = {}
        for entry in self._watchlist_cache:
            sector = entry.get('sector', 'unknown')
            exposure[sector] = exposure.get(sector, 0) + 1
        return exposure

    def add_symbol(self, symbol: str, **kwargs) -> None:
        """
        Add a symbol to the watchlist.

        Args:
            symbol: Stock ticker symbol
            **kwargs: Optional parameters (max_contracts, max_capital, etc.)
        """
        from bots.wheel_strategy.db import add_watchlist_entry
        add_watchlist_entry(self.db_path, symbol, **kwargs)
        self._load_watchlist()
        logger.info(f"Added {symbol} to wheel watchlist")

    def remove_symbol(self, symbol: str) -> bool:
        """Remove a symbol from the watchlist."""
        # Direct removal via SQL since db module doesn't have this helper
        import sqlite3
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
