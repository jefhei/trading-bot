"""
Risk management for copy trading.
Handles allocation limits, loss limits, and drawdown controls.
"""
import sqlite3
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from decimal import Decimal

from alpaca.trading.client import TradingClient

logger = logging.getLogger(__name__)


class CopyTradingRiskManager:
    """
    Manages risk controls for copy trading including allocation limits,
    daily loss limits, and drawdown controls.
    """

    def __init__(self, db_path: str, client: TradingClient, config: Dict[str, Any]):
        """
        Initialize risk manager.

        Args:
            db_path: Path to SQLite database
            client: Authenticated Alpaca TradingClient
            config: Risk configuration dictionary
        """
        self.db_path = db_path
        self.client = client
        self.config = config

        # Extract config values with defaults
        self.max_allocation_per_master_pct = config.get("max_allocation_per_master_pct", 30.0)
        self.max_total_allocation_pct = config.get("max_total_allocation_pct", 80.0)
        self.daily_loss_limit_per_master_pct = config.get("daily_loss_limit_per_master_pct", 5.0)
        self.max_drawdown_pct = config.get("max_drawdown_pct", 15.0)
        self.min_cash_reserve_pct = config.get("min_cash_reserve_pct", 10.0)

        self._init_db()
        self._load_high_water_mark()

    def _init_db(self):
        """Initialize database tables for risk tracking."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Table for tracking daily P&L per master
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_risk_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT NOT NULL,
                date TEXT NOT NULL,
                daily_pnl REAL DEFAULT 0.0,
                starting_equity REAL,
                current_equity REAL,
                UNIQUE(master_id, date)
            )
        """)

        # Table for high water mark
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_high_water_mark (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                value REAL NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add emergency_pause column to copy_masters (signal_processor creates the table)
        try:
            cursor.execute("""
                ALTER TABLE copy_masters ADD COLUMN emergency_pause INTEGER DEFAULT 0
            """)
        except sqlite3.OperationalError:
            pass  # Column already exists, which is fine

        conn.commit()
        conn.close()

    def _load_high_water_mark(self):
        """Load high water mark from database. If API fails, use cached or default."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT value FROM copy_high_water_mark WHERE id = 1")
        row = cursor.fetchone()

        if row:
            self._high_water_mark = row[0]
        else:
            try:
                # Try to get current equity from Alpaca
                account = self.client.get_account()
                self._high_water_mark = float(account.equity)
                self._save_high_water_mark()
                self._api_failure = False
            except Exception as e:
                logger.error(f"CRITICAL: Failed to get account for high water mark initialization: {e}. Defaulting to 0 - risk checks will HALT trading.")
                # Default to 0 which will cause HALT (drawdown will be 100%)
                self._high_water_mark = 0
                self._api_failure = True

        conn.close()

    def _save_high_water_mark(self):
        """Save high water mark to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO copy_high_water_mark (id, value, timestamp)
            VALUES (1, ?, ?)
        """, (self._high_water_mark, datetime.now()))

        conn.commit()
        conn.close()

    def set_high_water_mark(self, value: float):
        """Set high water mark manually."""
        self._high_water_mark = value
        self._save_high_water_mark()

    def emergency_pause_all(self) -> bool:
        """
        Set emergency pause flag for all masters.
        When set, no signals are processed and no orders are placed.

        Returns:
            bool: True if successfully set for at least one master
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE copy_masters
            SET emergency_pause = 1, updated_at = CURRENT_TIMESTAMP
            WHERE enabled = 1
        """)
        affected = cursor.rowcount
        conn.commit()
        conn.close()

        logger.error(f"EMERGENCY PAUSE ACTIVATED for {affected} masters. All trading halted.")
        return affected > 0

    def unpause_all(self) -> bool:
        """
        Clear emergency pause flag for all masters.

        Returns:
            bool: True if successfully cleared for at least one master
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE copy_masters
            SET emergency_pause = 0, updated_at = CURRENT_TIMESTAMP
            WHERE emergency_pause = 1
        """)
        affected = cursor.rowcount
        conn.commit()
        conn.close()

        logger.info(f"Emergency pause CLEARED for {affected} masters. Trading allowed.")
        return affected > 0

    def is_master_paused(self, master_id: str) -> bool:
        """
        Check if a specific master has emergency pause set.

        Args:
            master_id: Master trader ID

        Returns:
            bool: True if master is paused
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT emergency_pause FROM copy_masters WHERE id = ?
        """, (master_id,))
        row = cursor.fetchone()
        conn.close()

        if row is None:
            # Master not found in table, assume not paused
            return False

        return bool(row[0])

    def _check_any_emergency_pause(self) -> bool:
        """
        Check if ANY enabled master has emergency pause set.

        Returns:
            bool: True if any master is paused
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM copy_masters
            WHERE enabled = 1 AND emergency_pause = 1
        """)
        paused_count = cursor.fetchone()[0]
        conn.close()

        return paused_count > 0

    def _get_account_safe(self) -> Optional[Any]:
        """
        Safe wrapper for Alpaca get_account() API call.
        
        Returns:
            Account object if API call succeeds, None if it fails.
        """
        try:
            return self.client.get_account()
        except Exception as e:
            logger.error(f"Risk manager API failure: {e}. HALTING all trading operations.")
            self._api_failure = True
            return None

    def get_allocated_value(self, master_id: str) -> float:
        """
        Get current allocated value for a master trader.

        Args:
            master_id: Master trader ID

        Returns:
            float: Total dollar value allocated to this master
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COALESCE(SUM(qty * entry_price), 0)
            FROM copy_positions
            WHERE master_id = ? AND status = 'open'
        """, (master_id,))

        result = cursor.fetchone()
        conn.close()

        return result[0] if result else 0.0

    def is_copying_allowed(self, master_id: str) -> bool:
        """
        Check if copying is allowed for a specific master.

        Args:
            master_id: Master trader ID

        Returns:
            bool: True if copying is allowed
        """
        # Check if daily loss limit reached
        if self.is_daily_loss_limit_reached(master_id):
            return False

        # Check if this master has emergency pause set
        if self.is_master_paused(master_id):
            logger.warning(f"Master {master_id} has emergency pause set. Copying denied.")
            return False

        # Check if max allocation reached
        try:
            account = self.client.get_account()
            account_value = float(account.equity)
            self._api_failure = False
        except Exception as e:
            logger.error(f"CRITICAL: get_account() failed in is_copying_allowed: {e}. HALTING trading.")
            self._api_failure = True
            return False

        allocated = self.get_allocated_value(master_id)
        allocation_pct = (allocated / account_value) * 100 if account_value > 0 else 0

        if allocation_pct >= self.max_allocation_per_master_pct:
            return False

        return True

    def is_any_copying_allowed(self) -> bool:
        """
        Check if any copying is allowed (global limits).

        Returns:
            bool: True if copying is allowed
        """
        # Check if ANY master has emergency pause set — this halts all copying immediately
        if self._check_any_emergency_pause():
            logger.error("EMERGENCY PAUSE active. All copying halted.")
            return False

        # Check drawdown
        try:
            account = self.client.get_account()
            current_equity = float(account.equity)
            self._api_failure = False
        except Exception as e:
            logger.error(f"CRITICAL: get_account() failed in is_any_copying_allowed: {e}. HALTING all trading.")
            self._api_failure = True
            return False

        if self._high_water_mark > 0:
            drawdown_pct = ((self._high_water_mark - current_equity) / self._high_water_mark) * 100
            if drawdown_pct >= self.max_drawdown_pct:
                return False

        return True

    def has_sufficient_cash(self, required_amount: float) -> bool:
        """
        Check if there's sufficient cash for a trade while maintaining reserve.

        Args:
            required_amount: Cash required for the trade

        Returns:
            bool: True if sufficient cash available
        """
        try:
            account = self.client.get_account()
            account_value = float(account.equity)
            cash = float(account.cash)
            self._api_failure = False
        except Exception as e:
            logger.error(f"CRITICAL: get_account() failed in has_sufficient_cash: {e}. HALTING trading.")
            self._api_failure = True
            return False

        min_cash = account_value * (self.min_cash_reserve_pct / 100)
        available_cash = cash - min_cash

        return available_cash >= required_amount

    def is_daily_loss_limit_reached(self, master_id: str) -> bool:
        """
        Check if daily loss limit is reached for a master.

        Args:
            master_id: Master trader ID

        Returns:
            bool: True if daily loss limit reached
        """
        today = datetime.now().strftime("%Y-%m-%d")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT daily_pnl, starting_equity FROM copy_risk_tracking
            WHERE master_id = ? AND date = ?
        """, (master_id, today))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return False

        daily_pnl, starting_equity = row

        if starting_equity and starting_equity > 0:
            loss_pct = abs(daily_pnl / starting_equity) * 100
            return loss_pct >= self.daily_loss_limit_per_master_pct

        return False

    def record_pnl(self, master_id: str, pnl: float):
        """
        Record profit/loss for a master trader.

        Args:
            master_id: Master trader ID
            pnl: Profit/loss amount (positive for profit, negative for loss)
        """
        today = datetime.now().strftime("%Y-%m-%d")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Check if record exists for today
        cursor.execute("""
            SELECT id, daily_pnl FROM copy_risk_tracking
            WHERE master_id = ? AND date = ?
        """, (master_id, today))

        row = cursor.fetchone()

        if row:
            # Update existing record
            new_pnl = row[1] + pnl
            cursor.execute("""
                UPDATE copy_risk_tracking
                SET daily_pnl = ?, current_equity = current_equity + ?
                WHERE master_id = ? AND date = ?
            """, (new_pnl, pnl, master_id, today))
        else:
            # Create new record with starting equity
            try:
                account = self.client.get_account()
                starting_equity = float(account.equity)
                self._api_failure = False
            except Exception as e:
                logger.error(f"CRITICAL: get_account() failed in record_pnl (new record): {e}. Recording P&L skipped.")
                self._api_failure = True
                conn.close()
                return

            cursor.execute("""
                INSERT INTO copy_risk_tracking
                (master_id, date, daily_pnl, starting_equity, current_equity)
                VALUES (?, ?, ?, ?, ?)
            """, (master_id, today, pnl, starting_equity, starting_equity + pnl))

        conn.commit()
        conn.close()

        # Update high water mark if needed
        if pnl > 0:
            try:
                account = self.client.get_account()
                current_equity = float(account.equity)
                self._api_failure = False
            except Exception as e:
                logger.error(f"CRITICAL: get_account() failed in record_pnl (high water mark): {e}. High water mark not updated.")
                self._api_failure = True
                return
            if current_equity > self._high_water_mark:
                self._high_water_mark = current_equity
                self._save_high_water_mark()

    def get_risk_summary(self) -> Dict[str, Any]:
        """
        Get summary of current risk metrics.

        Returns:
            Dict with risk metrics
        """
        try:
            account = self.client.get_account()
            account_value = float(account.equity)
            cash = float(account.cash)
            self._api_failure = False
        except Exception as e:
            logger.error(f"CRITICAL: get_account() failed in get_risk_summary: {e}. Returning partial summary.")
            self._api_failure = True
            return {
                "account_value": 0,
                "cash": 0,
                "total_allocated": 0,
                "allocation_pct": 0,
                "high_water_mark": self._high_water_mark,
                "current_drawdown_pct": 100,
                "max_drawdown_limit_pct": self.max_drawdown_pct,
                "min_cash_required": 0,
                "copying_allowed": False,
                "api_failure": True
            }
        # Get total allocated
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COALESCE(SUM(qty * entry_price), 0)
            FROM copy_positions
            WHERE status = 'open'
        """)

        total_allocated = cursor.fetchone()[0] or 0.0
        conn.close()

        # Calculate drawdown
        drawdown_pct = 0.0
        if self._high_water_mark > 0:
            drawdown_pct = ((self._high_water_mark - account_value) / self._high_water_mark) * 100

        return {
            "account_value": account_value,
            "cash": cash,
            "total_allocated": total_allocated,
            "allocation_pct": (total_allocated / account_value * 100) if account_value > 0 else 0,
            "high_water_mark": self._high_water_mark,
            "current_drawdown_pct": max(0, drawdown_pct),
            "max_drawdown_limit_pct": self.max_drawdown_pct,
            "min_cash_required": account_value * (self.min_cash_reserve_pct / 100),
            "copying_allowed": self.is_any_copying_allowed()
        }
