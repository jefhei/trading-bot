"""
Stop Strategy Bot - Automated bracket order trading with stop-loss and take-profit.
"""
from .order_placer import place_bracket_order, place_trailing_stop_order
from .position_sizer import calculate_position_size, calculate_atr_stop
from .risk_manager import RiskManager
from .order_monitor import OrderMonitor, OrderState
from .config_loader import load_config
from .db import init_db, log_order_event, get_open_positions

__all__ = [
    'place_bracket_order',
    'place_trailing_stop_order',
    'calculate_position_size',
    'calculate_atr_stop',
    'RiskManager',
    'OrderMonitor',
    'OrderState',
    'load_config',
    'init_db',
    'log_order_event',
    'get_open_positions',
]
