"""
Order execution for copy trading.
Handles order placement with retry logic and failure queuing.
"""
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce


class OrderExecutor:
    """
    Executes orders with retry logic and failure handling.
    """

    def __init__(self, client: TradingClient, db_path: str):
        """
        Initialize order executor.

        Args:
            client: Authenticated Alpaca TradingClient
            db_path: Path to SQLite database
        """
        self.client = client
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_failed_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                qty INTEGER NOT NULL,
                side TEXT NOT NULL,
                error_message TEXT,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retry_count INTEGER DEFAULT 0
            )
        """)

        # Table for queued trades during API downtime
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_trade_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                qty INTEGER NOT NULL,
                side TEXT NOT NULL,
                queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retry_count INTEGER DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

    def place_order_with_retry(
        self,
        symbol: str,
        qty: int,
        side: str,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> Optional[Dict[str, Any]]:
        """
        Place an order with exponential backoff retry.

        Args:
            symbol: Stock symbol
            qty: Number of shares
            side: 'buy' or 'sell'
            max_retries: Maximum number of retry attempts
            retry_delay: Initial delay between retries (seconds)

        Returns:
            Dict with order info, or None if all retries failed
        """
        # Convert side string to enum
        order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL

        last_error = None

        for attempt in range(max_retries):
            try:
                order = self.client.submit_order(
                    MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=order_side,
                        time_in_force=TimeInForce.DAY
                    )
                )

                return {
                    "order_id": str(order.id),
                    "symbol": symbol,
                    "qty": qty,
                    "side": side,
                    "status": order.status,
                    "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None
                }

            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    sleep_time = retry_delay * (2 ** attempt)
                    time.sleep(sleep_time)
                continue

        # All retries failed
        return None

    def place_follower_order(
        self,
        master_id: str,
        symbol: str,
        qty: int,
        side: str
    ) -> Optional[Dict[str, Any]]:
        """
        Place a follower order and track it.

        Args:
            master_id: Master trader ID
            symbol: Stock symbol
            qty: Number of shares
            side: 'buy' or 'sell'

        Returns:
            Dict with order info, or None if failed
        """
        result = self.place_order_with_retry(symbol, qty, side)

        if result:
            return result

        # Order failed - record for later retry
        self._record_failed_order(master_id, symbol, qty, side, "Max retries exceeded")
        return None

    def _record_failed_order(
        self,
        master_id: str,
        symbol: str,
        qty: int,
        side: str,
        error_message: str
    ):
        """Record a failed order for retry tracking."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO copy_failed_orders
            (master_id, symbol, qty, side, error_message)
            VALUES (?, ?, ?, ?, ?)
        """, (master_id, symbol, qty, side, error_message))

        conn.commit()
        conn.close()

    def queue_trade_for_retry(
        self,
        master_id: str,
        symbol: str,
        qty: int,
        side: str
    ):
        """
        Queue a trade for retry when API is unavailable.

        Args:
            master_id: Master trader ID
            symbol: Stock symbol
            qty: Number of shares
            side: 'buy' or 'sell'
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO copy_trade_queue
            (master_id, symbol, qty, side)
            VALUES (?, ?, ?, ?)
        """, (master_id, symbol, qty, side))

        conn.commit()
        conn.close()

    def get_queued_trades(self) -> List[Dict[str, Any]]:
        """
        Get all trades waiting to be executed.

        Returns:
            List of queued trade records
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, master_id, symbol, qty, side, queued_at, retry_count
            FROM copy_trade_queue
            ORDER BY queued_at
        """)

        trades = []
        for row in cursor.fetchall():
            trades.append({
                "id": row[0],
                "master_id": row[1],
                "symbol": row[2],
                "qty": row[3],
                "side": row[4],
                "queued_at": row[5],
                "retry_count": row[6]
            })

        conn.close()
        return trades

    def process_queued_trades(self) -> Dict[str, int]:
        """
        Attempt to process all queued trades.

        Returns:
            Dict with counts of successful and failed trades
        """
        trades = self.get_queued_trades()

        successful = 0
        failed = 0

        for trade in trades:
            result = self.place_order_with_retry(
                trade["symbol"],
                trade["qty"],
                trade["side"]
            )

            if result:
                # Success - remove from queue
                self._remove_queued_trade(trade["id"])
                successful += 1
            else:
                # Failed - increment retry count
                self._increment_retry_count(trade["id"])
                failed += 1

        return {"successful": successful, "failed": failed}

    def _remove_queued_trade(self, trade_id: int):
        """Remove a trade from the queue."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM copy_trade_queue WHERE id = ?", (trade_id,))

        conn.commit()
        conn.close()

    def _increment_retry_count(self, trade_id: int):
        """Increment retry count for a queued trade."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE copy_trade_queue SET retry_count = retry_count + 1
            WHERE id = ?
        """, (trade_id,))

        conn.commit()
        conn.close()

    def get_failed_orders(self, master_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get failed orders with optional filtering.

        Args:
            master_id: Optional master ID to filter by

        Returns:
            List of failed order records
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if master_id:
            cursor.execute("""
                SELECT id, master_id, symbol, qty, side, error_message, failed_at, retry_count
                FROM copy_failed_orders
                WHERE master_id = ?
                ORDER BY failed_at DESC
            """, (master_id,))
        else:
            cursor.execute("""
                SELECT id, master_id, symbol, qty, side, error_message, failed_at, retry_count
                FROM copy_failed_orders
                ORDER BY failed_at DESC
            """)

        orders = []
        for row in cursor.fetchall():
            orders.append({
                "id": row[0],
                "master_id": row[1],
                "symbol": row[2],
                "qty": row[3],
                "side": row[4],
                "error_message": row[5],
                "failed_at": row[6],
                "retry_count": row[7]
            })

        conn.close()
        return orders

    def cancel_open_orders(self, symbol: Optional[str] = None) -> int:
        """
        Cancel open orders.

        Args:
            symbol: Optional symbol to filter by

        Returns:
            int: Number of orders cancelled
        """
        try:
            if symbol:
                # Get orders for symbol and cancel them
                orders = self.client.get_orders(symbol=symbol, status='open')
            else:
                orders = self.client.get_orders(status='open')

            cancelled = 0
            for order in orders:
                try:
                    self.client.cancel_order_by_id(order.id)
                    cancelled += 1
                except Exception:
                    continue

            return cancelled

        except Exception as e:
            print(f"Error cancelling orders: {e}")
            return 0
