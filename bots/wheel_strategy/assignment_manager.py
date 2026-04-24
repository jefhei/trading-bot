"""
Assignment manager for wheel strategy bot.
Handles put assignment detection, cost basis calculation, and transition to covered calls.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class AssignmentManager:
    """
    Manages the assignment process when short puts are exercised.
    Detects assignments, calculates adjusted cost basis, and triggers
    the transition to covered call selling phase.
    """

    def __init__(self, db_path: str, client: Any, config: Dict[str, Any]):
        """
        Initialize assignment manager.

        Args:
            db_path: Path to SQLite database
            client: Alpaca TradingClient
            config: Wheel strategy configuration
        """
        self.db_path = db_path
        self.client = client
        self.config = config

    def check_for_assignments(self) -> List[Dict[str, Any]]:
        """
        Check for new stock assignments from expired/in-the-money puts.

        Compares current stock positions against tracked open puts to
        identify which puts were assigned.

        Returns:
            List of detected assignments with details
        """
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        open_puts = pm.get_open_puts()
        assignments = []

        for put in open_puts:
            # Check if this put has been assigned
            is_assigned = self._is_put_assigned(put)
            if is_assigned:
                assignment = self._process_assignment(put)
                if assignment:
                    assignments.append(assignment)

        if assignments:
            logger.info(f"Detected {len(assignments)} new assignment(s)")
        else:
            logger.debug("No new assignments detected")

        return assignments

    def _is_put_assigned(self, put: Dict[str, Any]) -> bool:
        """
        Check if a specific put position has been assigned.

        In production, this would check:
        1. Account positions API for new stock positions
        2. Settlement records from Alpaca
        3. Order fill history

        For now, uses a simplified check based on expiration date.

        Args:
            put: Put position dictionary

        Returns:
            True if the put has been assigned
        """
        try:
            # Check if the put has expired and is in-the-money
            exp_date = datetime.fromisoformat(put['expiration'])
            if datetime.now() < exp_date:
                return False  # Not expired yet

            # Check current stock price vs strike
            current_price = self._get_current_price(put['symbol'])
            if current_price is None:
                logger.warning(f"Cannot get price for {put['symbol']} to check assignment")
                return False

            # If current price < strike, put is likely assigned
            if current_price < put['strike']:
                # Verify stock position exists
                stock_positions = self._get_account_positions()
                has_stock = any(p.symbol == put['symbol'] for p in stock_positions)
                return has_stock

            return False
        except Exception as e:
            logger.error(f"Error checking assignment for {put['symbol']}: {e}")
            return False

    def _process_assignment(self, put: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process a put assignment: update database and log the event.

        Args:
            put: Put position that was assigned

        Returns:
            Assignment details dictionary
        """
        symbol = put['symbol']
        strike = put['strike']
        contracts = put['contracts']
        premium = put['premium']
        shares = contracts * 100

        # Cost basis = strike price - premium collected
        cost_basis = strike - (premium / shares) if shares > 0 else strike

        # Record the assignment in the database
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        pm.record_assignment(symbol, strike, contracts, premium, cost_basis)

        assignment = {
            'symbol': symbol,
            'shares': shares,
            'strike': strike,
            'premium_collected': premium,
            'cost_basis': cost_basis,
            'put_position_id': put.get('id'),
        }

        logger.info(f"Assignment processed: {symbol} - {shares} shares at ${cost_basis:.2f} "
                   f"(strike ${strike:.2f} - premium ${premium:.2f})")

        return assignment

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current stock price from Alpaca."""
        try:
            bar = self.client.get_bars(symbol, timeframe='1Min', limit=1)[0]
            return bar.close
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return None

    def _get_account_positions(self) -> List:
        """Get current stock positions from Alpaca account."""
        try:
            return self.client.get_all_positions()
        except Exception as e:
            logger.error(f"Failed to get account positions: {e}")
            return []

    def check_for_exercises(self) -> List[Dict[str, Any]]:
        """
        Check for covered call exercises (shares called away).

        Returns:
            List of detected exercises with details
        """
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        open_calls = pm.get_open_calls()
        exercises = []

        for call in open_calls:
            is_exercised = self._is_call_exercised(call)
            if is_exercised:
                exercise = self._process_exercise(call)
                if exercise:
                    exercises.append(exercise)

        if exercises:
            logger.info(f"Detected {len(exercises)} call exercise(s)")

        return exercises

    def _is_call_exercised(self, call: Dict[str, Any]) -> bool:
        """Check if a covered call has been exercised."""
        try:
            exp_date = datetime.fromisoformat(call['expiration'])
            if datetime.now() < exp_date:
                return False

            # Check if shares are still held
            stock_positions = self._get_account_positions()
            has_shares = any(p.symbol == call['symbol'] for p in stock_positions)
            return not has_shares  # If we don't have shares, call was exercised
        except Exception as e:
            logger.error(f"Error checking exercise for {call['symbol']}: {e}")
            return False

    def _process_exercise(self, call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a call exercise (shares called away)."""
        symbol = call['symbol']
        strike = call['strike']
        contracts = call['contracts']
        premium = call['premium']
        cost_basis = call.get('cost_basis', 0)
        shares = contracts * 100

        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        realized_pnl = pm.record_call_exercise(symbol, strike, contracts, premium, cost_basis)

        exercise = {
            'symbol': symbol,
            'shares': shares,
            'strike': strike,
            'premium_collected': premium,
            'cost_basis': cost_basis,
            'realized_pnl': realized_pnl,
            'call_position_id': call.get('id'),
        }

        logger.info(f"Call exercise processed: {symbol} - {shares} shares called away "
                   f"at ${strike:.2f}, P&L: ${realized_pnl:.2f}")

        return exercise
