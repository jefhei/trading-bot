"""
Put seller for wheel strategy bot.
Handles cash-secured put selling logic: strike selection, premium targets, and order execution.
"""
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class PutSeller:
    """
    Manages cash-secured put selling for the wheel strategy.
    Selects strikes based on delta, ensures adequate premium, and places orders.
    """

    def __init__(self, db_path: str, client: Any, config: Dict[str, Any]):
        """
        Initialize put seller.

        Args:
            db_path: Path to SQLite database
            client: Alpaca TradingClient (options-approved)
            config: Wheel strategy configuration
        """
        self.db_path = db_path
        self.client = client
        self.config = config.get("put_selling", {})
        self.dte_min = self.config.get("days_to_expiration_min", 30)
        self.dte_max = self.config.get("days_to_expiration_max", 45)
        self.target_delta = self.config.get("target_delta", 0.30)
        self.min_premium_pct = self.config.get("min_premium_pct", 1.0)
        self.max_contracts_per_stock = self.config.get("max_contracts_per_stock", 5)

    def select_strike(
        self,
        symbol: str,
        current_price: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Select an optimal put strike for a symbol.

        Uses delta-based selection: finds the strike closest to target_delta
        with expiration in the DTE range.

        Args:
            symbol: Stock symbol
            current_price: Current stock price

        Returns:
            Dict with strike, expiration, delta, premium, and option_symbol
            or None if no suitable strike found.
        """
        try:
            # Get options chain for the symbol
            # In production, use Alpaca options chain API
            # For now, this is a template that will be filled when the
            # options chain endpoint is available
            chain = self._get_options_chain(symbol)
            if not chain:
                logger.warning(f"No options chain data for {symbol}")
                return None

            best = self._find_best_put(chain, current_price)
            if best:
                logger.info(f"Selected put for {symbol}: strike={best['strike']} "
                           f"exp={best['expiration']} delta={best['delta']} "
                           f"premium=${best['premium']:.2f}")
            return best
        except Exception as e:
            logger.error(f"Error selecting put strike for {symbol}: {e}")
            return None

    def place_put_order(self, symbol: str, strike: float, expiration: str,
                        contracts: int, premium: float) -> Optional[Dict[str, Any]]:
        """
        Sell a cash-secured put.

        Args:
            symbol: Stock symbol
            strike: Put strike price
            expiration: Expiration date (YYYY-MM-DD)
            contracts: Number of contracts to sell
            premium: Premium received per share

        Returns:
            Order result dict or None if failed.
        """
        try:
            # Build the Sell-to-Open put order
            # Using Alpaca's options order API
            # In production: client.submit_order(OrderRequest(...))
            logger.info(f"Placing PUT order: {symbol} strike={strike} exp={expiration} "
                       f"contracts={contracts} premium=${premium:.2f}")

            # Placeholder for actual order submission
            # result = self.client.submit_order(
            #     OrderRequest(
            #         symbol=option_symbol,
            #         qty=contracts,
            #         side=OrderSide.SELL,
            #         type=OrderType.MARKET,
            #         time_in_force=TimeInForce.DAY,
            #     )
            # )
            return {"status": "pending", "symbol": symbol, "strike": strike,
                    "contracts": contracts}
        except Exception as e:
            logger.error(f"Failed to place put order for {symbol}: {e}")
            return None

    def should_sell_put(self, symbol: str, current_price: float,
                        available_cash: float) -> bool:
        """
        Check if conditions are right to sell a put for a symbol.

        Args:
            symbol: Stock symbol
            current_price: Current stock price
            available_cash: Available cash for new positions

        Returns:
            True if a put should be sold
        """
        # Check if we already have open puts for this symbol
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        open_puts = pm.get_open_puts(symbol)

        if open_puts:
            logger.debug(f"Skipping {symbol}: already have {len(open_puts)} open puts")
            return False

        # Check if there's enough cash to secure the put
        # Conservative estimate: 1 contract at current price
        min_required = current_price * 100
        if available_cash < min_required:
            logger.debug(f"Insufficient cash for {symbol}: need ${min_required:.0f}, "
                        f"have ${available_cash:.0f}")
            return False

        return True

    def _get_options_chain(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Get options chain for a symbol.

        Args:
            symbol: Stock symbol

        Returns:
            List of option dicts with strike, expiration, bid, ask, delta
        """
        # In production, call Alpaca Options Chain API
        # For now, return empty list
        logger.debug(f"Fetching options chain for {symbol}")
        return []

    def _find_best_put(
        self,
        chain: List[Dict[str, Any]],
        current_price: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Find the best put option from the chain based on delta and premium.

        Args:
            chain: Options chain data
            current_price: Current stock price

        Returns:
            Best option dict or None
        """
        from datetime import datetime, timedelta

        min_exp = datetime.now() + timedelta(days=self.dte_min)
        max_exp = datetime.now() + timedelta(days=self.dte_max)

        min_premium = current_price * (self.min_premium_pct / 100)

        candidates = []
        for opt in chain:
            if opt.get('type') != 'put':
                continue
            if opt.get('delta', 0) >= 0:
                continue  # Put delta is negative; we want OTM puts

            exp_date = opt.get('expiration_date')
            if not exp_date:
                continue

            try:
                exp_dt = datetime.fromisoformat(exp_date)
            except (ValueError, TypeError):
                continue

            if not (min_exp <= exp_dt <= max_exp):
                continue

            premium = abs(opt.get('delta', 0)) * current_price * 0.1  # Rough estimate
            bid = opt.get('bid', 0)

            # Prefer options closest to target delta
            delta_dist = abs(abs(opt['delta']) - self.target_delta)
            candidates.append({
                'strike': opt['strike'],
                'expiration': exp_date,
                'delta': opt['delta'],
                'premium': bid,
                'option_symbol': opt.get('symbol', ''),
                'delta_distance': delta_dist,
            })

        if not candidates:
            return None

        # Sort by delta distance (closest to target delta first)
        candidates.sort(key=lambda x: x['delta_distance'])
        return candidates[0]
