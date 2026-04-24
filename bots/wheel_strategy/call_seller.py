"""
Covered call seller for wheel strategy bot.
Handles covered call order execution with strike above cost basis.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class CallSeller:
    """
    Manages covered call selling for the wheel strategy.
    Selects strikes above cost basis and places orders.
    """

    def __init__(self, db_path: str, client: Any, config: Dict[str, Any]):
        """
        Initialize call seller.

        Args:
            db_path: Path to SQLite database
            client: Alpaca TradingClient (options-approved)
            config: Wheel strategy configuration
        """
        self.db_path = db_path
        self.client = client
        self.config = config.get("call_selling", {})
        self.dte_min = self.config.get("days_to_expiration_min", 30)
        self.dte_max = self.config.get("days_to_expiration_max", 45)
        self.target_delta = self.config.get("target_delta", 0.30)
        self.min_premium_pct = self.config.get("min_premium_pct", 1.0)
        self.strike_min_above_cost_basis = self.config.get("strike_min_above_cost_basis", 0.0)

    def select_strike(
        self,
        symbol: str,
        current_price: float,
        cost_basis: float,
        shares: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Select an optimal call strike for a symbol.

        Must be above cost basis (configurable minimum) and in the DTE range.

        Args:
            symbol: Stock symbol
            current_price: Current stock price
            cost_basis: Per-share cost basis of the stock position
            shares: Number of shares owned

        Returns:
            Dict with strike, expiration, delta, premium, and option_symbol
            or None if no suitable strike found.
        """
        min_strike = cost_basis * (1 + self.strike_min_above_cost_basis / 100)
        contracts = shares // 100

        if contracts < 1:
            logger.warning(f"Not enough shares for covered call on {symbol}: {shares} shares")
            return None

        try:
            chain = self._get_options_chain(symbol)
            if not chain:
                return None

            best = self._find_best_call(chain, current_price, min_strike)
            if best:
                logger.info(f"Selected call for {symbol}: strike={best['strike']} "
                           f"exp={best['expiration']} delta={best['delta']} "
                           f"premium=${best['premium']:.2f}")
            return best
        except Exception as e:
            logger.error(f"Error selecting call strike for {symbol}: {e}")
            return None

    def place_call_order(self, symbol: str, strike: float, expiration: str,
                         contracts: int, premium: float) -> Optional[Dict[str, Any]]:
        """
        Sell a covered call.

        Args:
            symbol: Stock symbol
            strike: Call strike price
            expiration: Expiration date (YYYY-MM-DD)
            contracts: Number of contracts to sell
            premium: Premium received per share

        Returns:
            Order result dict or None if failed.
        """
        try:
            logger.info(f"Placing CALL order: {symbol} strike={strike} exp={expiration} "
                       f"contracts={contracts} premium=${premium:.2f}")

            # Placeholder for actual order submission
            return {"status": "pending", "symbol": symbol, "strike": strike,
                    "contracts": contracts}
        except Exception as e:
            logger.error(f"Failed to place call order for {symbol}: {e}")
            return None

    def should_sell_call(self, symbol: str, shares: int) -> bool:
        """
        Check if we should sell a covered call for this symbol.

        Args:
            symbol: Stock symbol
            shares: Number of shares owned

        Returns:
            True if a call should be sold
        """
        if shares < 100:
            logger.debug(f"Skipping {symbol}: not enough shares ({shares}) for covered call")
            return False

        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        open_calls = pm.get_open_calls(symbol)

        if open_calls:
            logger.debug(f"Skipping {symbol}: already have {len(open_calls)} open calls")
            return False

        return True

    def _get_options_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch options chain for a symbol."""
        logger.debug(f"Fetching options chain for {symbol}")
        return []

    def _find_best_call(
        self,
        chain: List[Dict[str, Any]],
        current_price: float,
        min_strike: float,
    ) -> Optional[Dict[str, Any]]:
        """Find the best call option above minimum strike."""
        min_exp = datetime.now() + timedelta(days=self.dte_min)
        max_exp = datetime.now() + timedelta(days=self.dte_max)

        candidates = []
        for opt in chain:
            if opt.get('type') != 'call':
                continue

            if opt.get('strike', 0) < min_strike:
                continue

            exp_date = opt.get('expiration_date')
            if not exp_date:
                continue

            try:
                exp_dt = datetime.fromisoformat(exp_date)
            except (ValueError, TypeError):
                continue

            if not (min_exp <= exp_dt <= max_exp):
                continue

            delta_dist = abs(opt.get('delta', 0) - self.target_delta)
            candidates.append({
                'strike': opt['strike'],
                'expiration': exp_date,
                'delta': opt.get('delta', 0),
                'premium': opt.get('bid', 0),
                'option_symbol': opt.get('symbol', ''),
                'delta_distance': delta_dist,
            })

        if not candidates:
            return None

        candidates.sort(key=lambda x: x['delta_distance'])
        return candidates[0]
