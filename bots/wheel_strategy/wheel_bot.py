"""
Main orchestrator for the wheel strategy bot.
Coordinates all components: watchlist, put/call selling, risk management,
earnings checks, assignment handling, and roll management.
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class WheelStrategyBot:
    """
    Orchestrates the complete wheel strategy cycle:
    1. Scan watchlist for put-selling opportunities
    2. Sell cash-secured puts on selected stocks
    3. Monitor for assignments → transition to covered calls
    4. Sell covered calls on assigned positions
    5. Handle call exercises → return to put selling
    6. Monitor and roll threatened positions
    """

    def __init__(self, db_path: str, client: Any, config: Dict[str, Any]):
        """
        Initialize the wheel strategy bot.

        Args:
            db_path: Path to SQLite database
            client: Alpaca TradingClient (options-approved)
            config: Full wheel strategy configuration
        """
        self.db_path = db_path
        self.client = client
        self.config = config
        self.paused = False

        # Initialize all components
        from bots.wheel_strategy.db import init_db
        self._init_db(db_path)

        from bots.wheel_strategy.watchlist_manager import WatchlistManager
        self.watchlist = WatchlistManager(db_path, config)

        from bots.wheel_strategy.position_manager import PositionManager
        self.positions = PositionManager(db_path)

        from bots.wheel_strategy.risk_manager import RiskManager
        self.risk = RiskManager(db_path, client, config)

        from bots.wheel_strategy.earnings_checker import EarningsChecker
        self.earnings = EarningsChecker(db_path, config)

        from bots.wheel_strategy.put_seller import PutSeller
        self.put_seller = PutSeller(db_path, client, config)

        from bots.wheel_strategy.call_seller import CallSeller
        self.call_seller = CallSeller(db_path, client, config)

        from bots.wheel_strategy.roll_manager import RollManager
        self.roll_manager = RollManager(db_path, client, config)

        from bots.wheel_strategy.assignment_manager import AssignmentManager
        self.assignment_mgr = AssignmentManager(db_path, client, config)

        logger.info("WheelStrategyBot initialized with all components")

    def _init_db(self, db_path: str) -> None:
        """Initialize database schema."""
        from bots.wheel_strategy.db import init_db
        init_db(db_path)

    def run_cycle(self) -> Dict[str, Any]:
        """
        Run one complete wheel strategy cycle.

        This should be called on a schedule (e.g., every 30 minutes during market hours).

        Returns:
            Summary of actions taken during this cycle
        """
        results = {
            'timestamp': datetime.now().isoformat(),
            'puts_sold': 0,
            'calls_sold': 0,
            'assignments': 0,
            'exercises': 0,
            'rolls': 0,
            'errors': []
        }

        if self.paused:
            logger.info("Wheel strategy is paused — skipping cycle")
            results['status'] = 'paused'
            return results

        # Step 1: Check for assignments and exercises first
        try:
            assignments = self.assignment_mgr.check_for_assignments()
            results['assignments'] = len(assignments)
        except Exception as e:
            results['errors'].append(f"Assignment check failed: {e}")
            logger.error(f"Assignment check error: {e}")

        try:
            exercises = self.assignment_mgr.check_for_exercises()
            results['exercises'] = len(exercises)
        except Exception as e:
            results['errors'].append(f"Exercise check failed: {e}")
            logger.error(f"Exercise check error: {e}")

        # Step 2: Sell covered calls on assigned stock
        try:
            results['calls_sold'] = self._sell_covered_calls()
        except Exception as e:
            results['errors'].append(f"Covered call selling failed: {e}")
            logger.error(f"Covered call selling error: {e}")

        # Step 3: Sell new puts on watchlist candidates
        try:
            results['puts_sold'] = self._sell_new_puts()
        except Exception as e:
            results['errors'].append(f"Put selling failed: {e}")
            logger.error(f"Put selling error: {e}")

        # Step 4: Check for rolls needed
        try:
            roll_candidates = self.roll_manager.check_rolls_needed()
            results['rolls'] = len(roll_candidates)
        except Exception as e:
            results['errors'].append(f"Roll check failed: {e}")
            logger.error(f"Roll check error: {e}")

        results['status'] = 'completed'
        logger.info(f"Wheel cycle complete: {results}")
        return results

    def _sell_covered_calls(self) -> int:
        """Sell covered calls on all eligible stock positions."""
        calls_sold = 0
        positions = self.positions.get_stock_positions()

        for stock_pos in positions:
            symbol = stock_pos['symbol']
            shares = stock_pos['shares']
            cost_basis = stock_pos['cost_basis']

            if not self.call_seller.should_sell_call(symbol, shares):
                continue

            price = self._get_current_price(symbol)
            if price is None:
                continue

            selection = self.call_seller.select_strike(symbol, price, cost_basis, shares)
            if not selection:
                logger.debug(f"No suitable call strike found for {symbol}")
                continue

            result = self.call_seller.place_call_order(
                symbol, selection['strike'], selection['expiration'],
                stock_pos['shares'] // 100, selection['premium']
            )

            if result:
                self.positions.add_call(
                    symbol, selection['option_symbol'],
                    selection['strike'], selection['expiration'],
                    stock_pos['shares'] // 100, selection['premium'],
                    cost_basis
                )
                calls_sold += 1
                logger.info(f"Sold covered call: {symbol} strike={selection['strike']} "
                          f"exp={selection['expiration']}")

        return calls_sold

    def _sell_new_puts(self) -> int:
        """Sell new puts on eligible watchlist symbols."""
        puts_sold = 0
        candidates = self.watchlist.get_candidates()

        for candidate in candidates:
            symbol = candidate['symbol']

            if not self.earnings.is_safe_to_sell(
                symbol, candidate.get('min_dte', 30), candidate.get('max_dte', 45)
            ):
                continue

            price = self._get_current_price(symbol)
            if price is None:
                continue

            available_cash = self._get_available_cash()
            if not self.put_seller.should_sell_put(symbol, price, available_cash):
                continue

            selection = self.put_seller.select_strike(symbol, price)
            if not selection:
                logger.debug(f"No suitable put strike found for {symbol}")
                continue

            contracts = min(
                candidate.get('max_contracts', 5),
                int(available_cash / (selection['strike'] * 100))
            )

            if contracts < 1:
                logger.debug(f"Not enough cash for {symbol} put (needs ${selection['strike'] * 100:.0f})")
                continue

            if not self.risk.can_open_put(symbol, selection['strike'], contracts):
                continue

            result = self.put_seller.place_put_order(
                symbol, selection['strike'], selection['expiration'],
                contracts, selection['premium']
            )

            if result:
                self.positions.add_put(
                    symbol, selection['option_symbol'],
                    selection['strike'], selection['expiration'],
                    contracts, selection['premium']
                )
                puts_sold += 1
                logger.info(f"Sold put: {symbol} strike={selection['strike']} "
                          f"exp={selection['expiration']} contracts={contracts}")

        return puts_sold

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current stock price."""
        try:
            bar = self.client.get_latest_bar(symbol)
            return bar.close if bar else None
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return None

    def _get_available_cash(self) -> float:
        """Get available cash from account."""
        try:
            account = self.client.get_account()
            return float(account.cash)
        except Exception as e:
            logger.error(f"Failed to get account cash: {e}")
            return 0.0

    def pause(self) -> None:
        """Pause new position openings (existing positions continue to be managed)."""
        self.paused = True
        logger.info("Wheel strategy paused — no new positions will be opened")

    def resume(self) -> None:
        """Resume wheel strategy operations."""
        self.paused = False
        logger.info("Wheel strategy resumed")

    def get_status(self) -> Dict[str, Any]:
        """Get current bot status and position summary."""
        return {
            'paused': self.paused,
            'premium_summary': self.positions.get_premium_summary(),
            'risk_status': self.risk.get_risk_status(),
            'watchlist_count': len(self.watchlist.get_candidates()),
            'open_puts': len(self.positions.get_open_puts()),
            'open_calls': len(self.positions.get_open_calls()),
            'stock_positions': len(self.positions.get_stock_positions()),
        }
