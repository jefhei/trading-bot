"""
Risk management module for Stop Strategy Bot.
Handles daily loss limits, position caps, and trading halts.
"""
from decimal import Decimal
from typing import Dict, Any
from alpaca.trading.client import TradingClient


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

    def is_daily_loss_limit_breached(self) -> bool:
        """
        Check if daily loss limit has been breached.

        Returns:
            bool: True if daily loss >= limit, False otherwise
        """
        account = self.client.get_account()

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
        """
        if self.is_daily_loss_limit_breached():
            account = self.client.get_account()
            current_equity = float(account.equity)
            last_equity = float(account.last_equity)
            loss_pct = (current_equity - last_equity) / last_equity * 100

            raise Exception(
                f"Trading halted: Daily loss limit breached. "
                f"Current loss: {loss_pct:.2f}% (limit: {self.daily_loss_limit_pct}%)"
            )

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
        """
        account = self.client.get_account()

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
