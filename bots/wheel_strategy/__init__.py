"""
Wheel Strategy Bot - Automated options income strategy.

Implements "The Wheel" strategy:
1. Sell cash-secured puts on stocks you want to own
2. If assigned, own the stock and sell covered calls
3. If shares are called away, repeat from step 1
"""
from .config_loader import load_config
from .db import init_db
from .watchlist_manager import WatchlistManager
from .position_manager import PositionManager
from .risk_manager import RiskManager
from .earnings_checker import EarningsChecker
from .put_seller import PutSeller
from .call_seller import CallSeller
from .roll_manager import RollManager
from .assignment_manager import AssignmentManager
from .wheel_bot import WheelStrategyBot

__all__ = [
    'load_config',
    'init_db',
    'WatchlistManager',
    'PositionManager',
    'RiskManager',
    'EarningsChecker',
    'PutSeller',
    'CallSeller',
    'RollManager',
    'AssignmentManager',
    'WheelStrategyBot',
]
