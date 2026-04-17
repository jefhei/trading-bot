"""
Copy Trading Bot - Automatically replicate trades from master traders.
"""

from .signal_processor import SignalProcessor, Signal
from .position_sizer import (
    calculate_proportional_size,
    calculate_fixed_dollar_size,
    calculate_multiplier_size,
    PositionSizingMethod
)
from .trade_filter import TradeFilter, FilterCriteria
from .position_tracker import PositionTracker, Position
from .risk_manager import CopyTradingRiskManager
from .performance_tracker import PerformanceTracker
from .order_executor import OrderExecutor

__all__ = [
    'SignalProcessor', 'Signal',
    'calculate_proportional_size', 'calculate_fixed_dollar_size',
    'calculate_multiplier_size', 'PositionSizingMethod',
    'TradeFilter', 'FilterCriteria',
    'PositionTracker', 'Position',
    'CopyTradingRiskManager',
    'PerformanceTracker',
    'OrderExecutor'
]
