"""
Risk management module for Stop Strategy Bot.
Handles daily loss limits, position caps, and trading halts.
"""
import logging
from decimal import Decimal
from typing import Dict, Any
from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError

logger = logging.getLogger(__name__)


class RiskError(Exception):
    """Custom exception for risk management errors."""
    pass


class RiskManager:
    """
    Manages risk parameters and trading halts for the stop strategy bot.
    """

    def __init__(self, client: TradingClient, config: Dict[str, Any]):
        """
        Initialize risk manager with client and config.

        Args:
            client: Authenticated Alpaca TradingClient
            config: Risk configuration dictionary
        """
        self.client = client
        self.config = config
        self.daily_loss_limit_pct = config.get("daily_loss_limit_pct", 5.0)
        self.max_position_size_pct = config.get("max_position_size_pct", 10.0)

    def _get_account_with_retry(self, max_retries=3):
        """Fetch account with retry logic for transient errors."""
        for attempt in range(max_retries):
            try:
                return self.client.get_account()
            except APIError as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to fetch account after {max_retries} attempts: {e}")
                    raise RiskError(f"Cannot fetch account data: {e}")
                logger.warning(f"Account fetch failed (attempt {attempt + 1}), retrying...")
                import time
                time.sleep(0.5 * (2 ** attempt))  # Exponential backoff

    def is_daily_loss_limit_breached(self) -> bool:
        """
        Check if daily loss limit has been breached.

        Returns:
            bool: True if daily loss >= limit, False otherwise
            
        Raises:
            RiskError: If unable to fetch account data
        """
        account = self._get_account_with_retry()

        # Get current and previous equity
        current_equity = Decimal(str(account.equity))
        last_equity = Decimal(str(account.last_equity))

        if last_equity == 0:
            return False

        # Calculate percentage change
        pct_change = (current_equity - last_equity) / last_equity * 100

        # Loss limit breached if pct_change <= -daily_loss_limit_pct
        return pct_change <= -Decimal(str(self.daily_loss_limit_pct))

    def assert_trading_allowed(self) -> None:
        """
        Assert that trading is allowed (daily loss limit not breached).

        Raises:
            Exception: If trading should be halted due to daily loss limit
            RiskError: If unable to verify trading status
        """
        try:
            if self.is_daily_loss_limit_breached():
                account = self._get_account_with_retry()
                current_equity = float(account.equity)
                last_equity = float(account.last_equity)
                loss_pct = (current_equity - last_equity) / last_equity * 100

                raise Exception(
                    f"Trading halted: Daily loss limit breached. "
                    f"Current loss: {loss_pct:.2f}% (limit: {self.daily_loss_limit_pct}%)"
                )
        except RiskError:
            # If we can't verify loss status, halt trading to be safe
            logger.error("Cannot verify loss status - halting trading as precaution")
            raise Exception("Trading halted: Unable to verify risk status")

    def apply_position_cap(
        self,
        raw_shares: int,
        entry_price: float,
        account_value: float,
    ) -> int:
        """
        Cap position size to maximum percentage of account.

        Args:
            raw_shares: Shares calculated from risk formula
            entry_price: Entry price per share
            account_value: Total account equity

        Returns:
            int: Capped number of shares
        """
        max_position_value = account_value * (self.max_position_size_pct / 100)
        max_shares = int(max_position_value / entry_price)

        return min(raw_shares, max_shares)

    def get_account_summary(self) -> Dict[str, Any]:
        """
        Get account summary for risk assessment.

        Returns:
            Dict with equity, cash, buying_power, and daily P&L
            
        Raises:
            RiskError: If unable to fetch account data
        """
        account = self._get_account_with_retry()

        current_equity = float(account.equity)
        last_equity = float(account.last_equity)
        daily_pnl_pct = (current_equity - last_equity) / last_equity * 100

        return {
            "equity": current_equity,
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "daily_pnl_pct": daily_pnl_pct,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "trading_allowed": not self.is_daily_loss_limit_breached(),
        }
