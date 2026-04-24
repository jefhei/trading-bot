"""
Earnings checker for wheel strategy bot.
Checks earnings calendar to avoid selling options through earnings.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class EarningsChecker:
    """
    Checks earnings calendar data to ensure the wheel strategy
    doesn't sell options that expire around earnings announcements.
    """

    def __init__(self, db_path: str, config: Dict[str, Any]):
        """
        Initialize earnings checker.

        Args:
            db_path: Path to SQLite database
            config: Wheel strategy configuration
        """
        self.db_path = db_path
        self.config = config
        self.avoid_earnings = config.get("put_selling", {}).get("avoid_earnings", True)

    def has_earnings_before(self, symbol: str, expiration_date: str) -> Optional[str]:
        """
        Check if a symbol has earnings before the given expiration date.

        Args:
            symbol: Stock symbol
            expiration_date: Option expiration date as YYYY-MM-DD

        Returns:
            Earnings date string if found, None otherwise
        """
        if not self.avoid_earnings:
            return None

        from bots.wheel_strategy.db import get_upcoming_earnings
        earnings_date = get_upcoming_earnings(self.db_path, symbol, expiration_date)

        if earnings_date:
            logger.info(f"Earnings detected: {symbol} has earnings on {earnings_date} "
                       f"before expiration {expiration_date}")

        return earnings_date

    def is_safe_to_sell(self, symbol: str, dte_min: int = 30, dte_max: int = 45) -> bool:
        """
        Check if it's safe to sell options for a symbol (no earnings in DTE range).

        Args:
            symbol: Stock symbol
            dte_min: Minimum days to expiration
            dte_max: Maximum days to expiration

        Returns:
            True if no earnings detected in the DTE range
        """
        if not self.avoid_earnings:
            return True

        max_expiration = datetime.now() + timedelta(days=dte_max)
        date_str = max_expiration.strftime("%Y-%m-%d")

        earnings_date = self.has_earnings_before(symbol, date_str)
        if earnings_date:
            logger.warning(f"It is NOT safe to sell options on {symbol}: "
                         f"earnings on {earnings_date}")
            return False

        return True

    def refresh_earnings_data(self, symbols: List[str], fetch_func=None) -> int:
        """
        Refresh earnings data for a list of symbols.

        Args:
            symbols: List of stock symbols
            fetch_func: Optional callable(symbol) -> list of (date, type) tuples.
                       If None, uses a placeholder.

        Returns:
            Number of earnings entries cached
        """
        from bots.wheel_strategy.db import cache_earnings
        count = 0

        for symbol in symbols:
            if fetch_func:
                earnings_data = fetch_func(symbol)
            else:
                logger.debug(f"Skipping earnings fetch for {symbol}: no fetch_func provided")
                continue

            for date_info in earnings_data:
                earnings_date = date_info[0] if isinstance(date_info, tuple) else date_info
                date_type = date_info[1] if isinstance(date_info, tuple) and len(date_info) > 1 else 'quarterly'
                cache_earnings(self.db_path, symbol, earnings_date, type=date_type)
                count += 1

        logger.info(f"Refreshed earnings data: {count} entries cached for {len(symbols)} symbols")
        return count
