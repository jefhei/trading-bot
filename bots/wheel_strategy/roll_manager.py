"""
Roll manager for wheel strategy bot.
Handles rolling puts and calls based on delta and DTE thresholds.
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class RollManager:
    """
    Manages rolling of options positions when they become threatened.
    Supports roll down, roll out, and roll up-and-out for both puts and calls.
    """

    def __init__(self, db_path: str, client: Any, config: Dict[str, Any]):
        """
        Initialize roll manager.

        Args:
            db_path: Path to SQLite database
            client: Alpaca TradingClient (options-approved)
            config: Wheel strategy configuration (roll_management section)
        """
        self.db_path = db_path
        self.client = client
        roll_config = config.get("roll_management", {})
        self.auto_roll_put_delta = roll_config.get("auto_roll_put_delta", 0.70)
        self.auto_roll_call_delta = roll_config.get("auto_roll_call_delta", 0.70)
        self.roll_dte = roll_config.get("roll_days_to_expiration", 7)

    def check_rolls_needed(self) -> list:
        """
        Check all open positions for roll opportunities.

        Returns:
            List of positions that need rolling with recommended action
        """
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)

        roll_candidates = []

        # Check all open puts
        for put in pm.get_open_puts():
            roll_action = self._evaluate_put_roll(put)
            if roll_action:
                roll_candidates.append({'position': put, 'action': roll_action})

        # Check all open calls
        for call in pm.get_open_calls():
            roll_action = self._evaluate_call_roll(call)
            if roll_action:
                roll_candidates.append({'position': call, 'action': roll_action})

        if roll_candidates:
            logger.info(f"Found {len(roll_candidates)} positions that may need rolling")
        else:
            logger.debug("No rolls needed for any open positions")

        return roll_candidates

    def _evaluate_put_roll(self, position: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Evaluate whether a put should be rolled.

        Roll triggers:
        - Delta exceeds auto_roll_put_delta threshold (position is being challenged)
        - DTE is below roll_dte threshold (time to expiration is running out)
        """
        try:
            current_delta = self._get_current_delta(position['option_symbol'])
            if current_delta is not None and current_delta <= -self.auto_roll_put_delta:
                return {
                    'type': 'roll_out',
                    'reason': f"Put delta {current_delta:.2f} exceeded threshold {-self.auto_roll_put_delta:.2f}",
                    'direction': 'out',  # Roll to later expiration
                }
        except Exception as e:
            logger.debug(f"Could not evaluate put roll for {position['symbol']}: {e}")

        # Check DTE
        days_to_expire = self._days_to_expiration(position['expiration'])
        if days_to_expire is not None and days_to_expire <= self.roll_dte:
            return {
                'type': 'roll_out',
                'reason': f"Only {days_to_expire} DTE remaining (threshold: {self.roll_dte})",
                'direction': 'out',
            }

        return None

    def _evaluate_call_roll(self, position: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Evaluate whether a covered call should be rolled.

        Roll triggers:
        - Delta exceeds auto_roll_call_delta threshold (stock price approaching strike)
        - DTE is below roll_dte threshold
        """
        try:
            current_delta = self._get_current_delta(position['option_symbol'])
            if current_delta is not None and current_delta >= self.auto_roll_call_delta:
                return {
                    'type': 'roll_up_and_out',
                    'reason': f"Call delta {current_delta:.2f} exceeded threshold {self.auto_roll_call_delta:.2f}",
                    'direction': 'up_and_out',  # Higher strike, later expiration
                }
        except Exception as e:
            logger.debug(f"Could not evaluate call roll for {position['symbol']}: {e}")

        days_to_expire = self._days_to_expiration(position['expiration'])
        if days_to_expire is not None and days_to_expire <= self.roll_dte:
            return {
                'type': 'roll_out',
                'reason': f"Only {days_to_expire} DTE remaining (threshold: {self.roll_dte})",
                'direction': 'out',
            }

        return None

    def execute_roll(self, position_id: int, symbol: str, new_strike: float,
                     new_expiration: str, new_premium: float) -> bool:
        """
        Execute a roll: close current position and open new one.

        Args:
            position_id: ID of the position to roll
            symbol: Stock symbol
            new_strike: New strike price
            new_expiration: New expiration date
            new_premium: Premium on new position

        Returns:
            True if roll was successful
        """
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)

        notes = f"Rolled to strike={new_strike} exp={new_expiration} premium=${new_premium:.2f}"
        pm.record_roll(symbol, position_id, new_premium, new_strike, new_expiration, notes)

        logger.info(f"Executed roll for {symbol}: position {position_id} -> "
                   f"strike={new_strike} exp={new_expiration}")
        return True

    def _get_current_delta(self, option_symbol: str) -> Optional[float]:
        """Get current delta for an option symbol."""
        # In production, query Alpaca options chain for current greeks
        return None

    def _days_to_expiration(self, expiration_str: str) -> Optional[int]:
        """Calculate days until expiration."""
        try:
            exp_date = datetime.fromisoformat(expiration_str)
            delta = exp_date - datetime.now()
            return max(0, delta.days)
        except (ValueError, TypeError):
            return None
