"""
Trade filtering for copy trading.
Provides configurable filters for symbols, asset classes, position sizes, etc.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Set
from datetime import datetime

from .signal_processor import Signal


@dataclass
class FilterCriteria:
    """Criteria for filtering trade signals."""
    symbols_whitelist: Optional[List[str]] = None
    symbols_blacklist: Optional[List[str]] = field(default_factory=list)
    asset_classes: Optional[List[str]] = field(default_factory=lambda: ["us_equity"])
    min_position_size: Optional[float] = None  # Minimum dollar value
    max_position_size: Optional[float] = None  # Maximum dollar value
    allow_short: bool = True
    allow_long: bool = True


class TradeFilter:
    """
    Filters trade signals based on configured criteria.
    """

    def __init__(self, criteria: FilterCriteria):
        """
        Initialize trade filter with criteria.

        Args:
            criteria: FilterCriteria instance defining allowed trades
        """
        self.criteria = criteria
        # Convert to sets for faster lookup
        self._whitelist = set(criteria.symbols_whitelist) if criteria.symbols_whitelist else None
        self._blacklist = set(criteria.symbols_blacklist) if criteria.symbols_blacklist else set()
        self._asset_classes = set(criteria.asset_classes) if criteria.asset_classes else None

    def should_process(self, signal: Signal) -> bool:
        """
        Determine if a trade signal should be processed.

        Args:
            signal: Trade signal to evaluate

        Returns:
            bool: True if signal passes all filters, False otherwise
        """
        # Check whitelist first (if specified, symbol must be in list)
        if self._whitelist is not None:
            if signal.symbol not in self._whitelist:
                return False

        # Check blacklist
        if signal.symbol in self._blacklist:
            return False

        # Check asset class
        if self._asset_classes is not None:
            if signal.asset_class not in self._asset_classes:
                return False

        # Check position size constraints
        position_value = signal.qty * signal.price

        if self.criteria.min_position_size is not None:
            if position_value < self.criteria.min_position_size:
                return False

        if self.criteria.max_position_size is not None:
            if position_value > self.criteria.max_position_size:
                return False

        # Check direction (long/short)
        is_short = signal.side in ('sell_short', 'short')
        is_long = signal.side in ('buy', 'long')

        if is_short and not self.criteria.allow_short:
            return False

        if is_long and not self.criteria.allow_long:
            return False

        return True

    def get_filter_reason(self, signal: Signal) -> Optional[str]:
        """
        Get the reason a signal was filtered out.

        Args:
            signal: Trade signal to evaluate

        Returns:
            str: Reason for filtering, or None if signal passes
        """
        # Check whitelist
        if self._whitelist is not None:
            if signal.symbol not in self._whitelist:
                return f"Symbol {signal.symbol} not in whitelist"

        # Check blacklist
        if signal.symbol in self._blacklist:
            return f"Symbol {signal.symbol} is blacklisted"

        # Check asset class
        if self._asset_classes is not None:
            if signal.asset_class not in self._asset_classes:
                return f"Asset class {signal.asset_class} not allowed"

        # Check position size
        position_value = signal.qty * signal.price

        if self.criteria.min_position_size is not None:
            if position_value < self.criteria.min_position_size:
                return f"Position size ${position_value:.2f} below minimum ${self.criteria.min_position_size:.2f}"

        if self.criteria.max_position_size is not None:
            if position_value > self.criteria.max_position_size:
                return f"Position size ${position_value:.2f} exceeds maximum ${self.criteria.max_position_size:.2f}"

        # Check direction
        is_short = signal.side in ('sell_short', 'short')
        is_long = signal.side in ('buy', 'long')

        if is_short and not self.criteria.allow_short:
            return "Short selling not allowed"

        if is_long and not self.criteria.allow_long:
            return "Long positions not allowed"

        return None
