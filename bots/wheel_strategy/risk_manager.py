"""
Risk manager for wheel strategy bot.
Enforces capital limits, position limits, and sector concentration.
"""
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Manages risk controls for the wheel strategy including:
    - Maximum capital per stock
    - Maximum total open puts
    - Sector concentration limits
    - Minimum cash reserves
    """

    def __init__(self, db_path: str, client: Any, config: Dict[str, Any]):
        """
        Initialize risk manager.

        Args:
            db_path: Path to SQLite database
            client: Alpaca TradingClient (for account queries)
            config: Risk configuration from wheel_strategy.risk_controls
        """
        self.db_path = db_path
        self.client = client
        risk_config = config.get("risk_controls", {})
        self.max_capital_per_stock_pct = risk_config.get("max_capital_per_stock_pct", 20.0)
        self.max_total_puts = risk_config.get("max_total_puts", 10)
        self.max_sector_concentration_pct = risk_config.get("max_sector_concentration_pct", 30.0)
        self.min_cash_reserve_pct = risk_config.get("min_cash_reserve_pct", 20.0)
        self.stock_stop_loss_pct = risk_config.get("stock_stop_loss_pct", 15.0)

    def _get_account_value(self) -> float:
        """Get current account equity value."""
        try:
            account = self.client.get_account()
            return float(account.equity)
        except Exception as e:
            logger.error(f"Failed to get account value: {e}")
            return 0.0

    def _get_cash_balance(self) -> float:
        """Get available cash balance."""
        try:
            account = self.client.get_account()
            return float(account.cash)
        except Exception as e:
            logger.error(f"Failed to get cash balance: {e}")
            return 0.0

    def can_open_put(self, symbol: str, strike: float, contracts: int) -> bool:
        """
        Check if opening a new put position passes all risk checks.

        Args:
            symbol: Stock symbol
            strike: Put strike price
            contracts: Number of contracts

        Returns:
            True if all risk checks pass
        """
        account_value = self._get_account_value()
        if account_value == 0:
            logger.error("Cannot open put: account value is 0 or API failure")
            return False

        # Check max capital per stock
        required_capital = strike * contracts * 100
        max_capital = account_value * (self.max_capital_per_stock_pct / 100)
        if required_capital > max_capital:
            logger.warning(f"Risk check FAILED for {symbol}: "
                         f"required ${required_capital:.0f} exceeds max "
                         f"per-stock ${max_capital:.0f} ({self.max_capital_per_stock_pct}% of ${account_value:.0f})")
            return False

        # Check max total puts
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)
        open_puts = pm.get_open_puts()
        if len(open_puts) >= self.max_total_puts:
            logger.warning(f"Risk check FAILED: already have {len(open_puts)} open puts "
                         f"(max {self.max_total_puts})")
            return False

        # Check sector concentration
        sector_exposure = self._get_sector_exposure()
        # This is a simplified check
        sector_count = sector_exposure.get(self._get_symbol_sector(symbol), 0)
        total_symbols = sum(sector_exposure.values()) if sector_exposure else 1
        if sector_count > 0:
            sector_pct = (sector_count / total_symbols) * 100
            if sector_pct >= self.max_sector_concentration_pct:
                logger.warning(f"Risk check FAILED: sector concentration {sector_pct:.0f}% "
                             f"exceeds limit {self.max_sector_concentration_pct}%")
                return False

        # Check minimum cash reserve
        cash = self._get_cash_balance()
        min_cash = account_value * (self.min_cash_reserve_pct / 100)
        remaining_cash = cash - required_capital
        if remaining_cash < min_cash:
            logger.warning(f"Risk check FAILED: remaining cash ${remaining_cash:.0f} "
                         f"would fall below reserve ${min_cash:.0f}")
            return False

        logger.info(f"Risk check PASSED for {symbol}: {contracts} contracts at ${strike:.2f}")
        return True

    def can_open_call(self, symbol: str, strike: float, contracts: int,
                      cost_basis: float) -> bool:
        """
        Check if opening a covered call passes risk checks.

        For covered calls, the main check is that strike is above cost basis
        (unless intentional loss for roll management).
        """
        if strike < cost_basis:
            logger.warning(f"Risk check: CALL strike ${strike:.2f} below cost basis "
                         f"${cost_basis:.2f} for {symbol}")
            # Allow below cost basis with warning (for roll management)

        logger.info(f"Risk check PASSED for covered call on {symbol}: "
                   f"{contracts} contracts at ${strike:.2f}")
        return True

    def _get_symbol_sector(self, symbol: str) -> str:
        """Get sector for a symbol from watchlist (simplified lookup)."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT sector FROM wheel_watchlist WHERE symbol = ?", (symbol,))
        row = cursor.fetchone()
        conn.close()
        return row['sector'] or 'unknown' if row else 'unknown'

    def _get_sector_exposure(self) -> Dict[str, int]:
        """Get current sector counts from open positions."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT w.sector, COUNT(*) as cnt
            FROM wheel_options_positions o
            JOIN wheel_watchlist w ON o.symbol = w.symbol
            WHERE o.status = 'open' AND w.sector IS NOT NULL
            GROUP BY w.sector
        """)
        return {row['sector']: row['cnt'] for row in cursor.fetchall() if row['sector']}

    def get_risk_status(self) -> Dict[str, Any]:
        """Get a summary of current risk metrics."""
        account_value = self._get_account_value()
        cash = self._get_cash_balance()
        from bots.wheel_strategy.position_manager import PositionManager
        pm = PositionManager(self.db_path)

        open_puts = pm.get_open_puts()
        open_calls = pm.get_open_calls()
        stock_positions = pm.get_stock_positions()

        total_put_exposure = sum(p['strike'] * p['contracts'] * 100 for p in open_puts)

        return {
            "account_value": account_value,
            "cash": cash,
            "cash_reserve_pct": round((cash / account_value * 100), 1) if account_value > 0 else 0,
            "min_cash_required": account_value * (self.min_cash_reserve_pct / 100),
            "open_puts": len(open_puts),
            "max_puts": self.max_total_puts,
            "open_calls": len(open_calls),
            "stock_positions": len(stock_positions),
            "total_put_exposure": total_put_exposure,
            "exposure_pct_of_account": round((total_put_exposure / account_value * 100), 1) if account_value > 0 else 0,
            "sector_concentration": self._get_sector_exposure(),
        }
