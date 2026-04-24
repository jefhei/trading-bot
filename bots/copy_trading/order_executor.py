"""
Order execution for copy trading.
Handles order placement with retry logic and failure queuing.
"""
import sqlite3
import time
import logging
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from core.logger import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """
    Token bucket rate limiter for Alpaca API calls.

    Alpaca standard tier limits:
    - 200 requests per minute
    - 3 requests per second

    This limiter enforces a rolling window per-second cap to stay well under limits.
    """

    def __init__(self, max_requests_per_second: float = 2.0, max_requests_per_minute: int = 150):
        """
        Initialize rate limiter.

        Args:
            max_requests_per_second: Max requests allowed per second (below Alpaca's 3/sec)
            max_requests_per_minute: Max requests allowed per minute (below Alpaca's 200/min)
        """
        self.max_rps = max_requests_per_second
        self.max_rpm = max_requests_per_minute
        self._requests: deque = deque()  # timestamps of recent requests
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """
        Wait for rate limit window to allow a request.

        Args:
            timeout: Max seconds to wait for rate limit clearance

        Returns:
            True if request was allowed, False if timeout expired
        """
        start = time.monotonic()

        while time.monotonic() - start < timeout:
            with self._lock:
                now = time.monotonic()
                # Prune old timestamps beyond the 1-minute window
                cutoff = now - 60.0
                while self._requests and self._requests[0] < cutoff:
                    self._requests.popleft()

                # Check per-second limit
                recent = sum(1 for t in self._requests if t > now - 1.0)
                if recent < self.max_rps and len(self._requests) < self.max_rpm:
                    self._requests.append(now)
                    return True

                # Calculate wait time until next slot is available
                if self._requests:
                    next_slot = self._requests[0] + 60.0 - now
                    wait = max(0.1, min(next_slot, 1.0))  # At least 100ms, at most 1s
                else:
                    wait = 0.1

            time.sleep(wait)

        logger.warning(f"Rate limiter timeout after {timeout}s — request queue blocked")
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get current rate limiter stats."""
        with self._lock:
            now = time.monotonic()
            recent_1s = sum(1 for t in self._requests if t > now - 1.0)
            recent_60s = len(self._requests)

        return {
            "requests_last_second": recent_1s,
            "requests_last_minute": recent_60s,
            "max_per_second": self.max_rps,
            "max_per_minute": self.max_rpm,
            "utilization_pct": round((recent_60s / self.max_rpm) * 100, 1)
        }


def is_retryable_error(error: Exception) -> tuple:
    """
    Determine if an Alpaca API error is retryable.

    Retryable: rate limits (429), server errors (5xx), timeouts, connection errors
    Non-retryable: client errors (400/401/403/404/422), insufficient funds, invalid symbols

    Returns:
        Tuple of (is_retryable: bool, error_category: str)
    """
    from alpaca.common.exceptions import APIError

    error_str = str(error).lower()

    # Retryable: rate limits
    if "429" in error_str or "rate limit" in error_str:
        return True, "rate_limited"

    # Retryable: server errors
    if any(code in error_str for code in ["500", "502", "503", "504"]):
        return True, "server_error"

    # Retryable: timeouts and connection errors
    if "timeout" in error_str or "timed out" in error_str:
        return True, "timeout"
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True, "connection_error"

    # Retryable: Alpaca APIError with 5xx status
    if isinstance(error, APIError):
        status = getattr(error, 'status_code', None) or getattr(error, 'code', None)
        if status and str(status).startswith("5"):
            return True, "server_error"
        if status and str(status) == "429":
            return True, "rate_limited"

    # Non-retryable: client errors (4xx)
    if "401" in error_str or "unauthorized" in error_str:
        return False, "unauthorized"
    if "403" in error_str or "forbidden" in error_str:
        return False, "forbidden"
    if "insufficient" in error_str or "not enough" in error_str or "buying power" in error_str:
        return False, "insufficient_funds"
    if "invalid symbol" in error_str or "asset is not active" in error_str or "not found" in error_str:
        return False, "invalid_symbol"
    if "400" in error_str or "bad request" in error_str:
        return False, "bad_request"
    if "422" in error_str or "unprocessable" in error_str:
        return False, "unprocessable"

    # Unknown errors — retry to be safe
    logger.warning(f"Unknown error type, treating as retryable: {error}")
    return True, "unknown"


class OrderExecutor:
    """
    Executes orders with retry logic and failure handling.
    """

    def __init__(self, client: TradingClient, db_path: str, rate_limiter: Optional['RateLimiter'] = None):
        """
        Initialize order executor.

        Args:
            client: Authenticated Alpaca TradingClient
            db_path: Path to SQLite database
            rate_limiter: Optional RateLimiter instance. If None, creates one with defaults.
        """
        self.client = client
        self.db_path = db_path
        self._rate_limiter = rate_limiter or RateLimiter()
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

        logger.info(f"Placing order: side={side} symbol={symbol} qty={qty} max_retries={max_retries}")

        # Respect rate limit before submitting
        if not self._rate_limiter.acquire():
            logger.error("Order submission blocked by rate limiter timeout. "
                        f"symbol={symbol} qty={qty} side={side}")
            return None

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

                logger.info(f"Order placed successfully: order_id={order.id} symbol={symbol} "
                           f"qty={qty} side={side} status={order.status} "
                           f"filled_avg_price={order.filled_avg_price}")

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
                retryable, error_category = is_retryable_error(e)

                if not retryable:
                    # Non-retryable error — fail immediately
                    logger.error(f"Order failed with non-retryable error [{error_category}]: "
                                f"symbol={symbol} qty={qty} side={side} error={e}")
                    return None

                logger.warning(f"Order attempt {attempt + 1}/{max_retries} failed "
                             f"[{error_category}]: symbol={symbol} qty={qty} side={side} error={e}")

                if attempt < max_retries - 1:
                    sleep_time = retry_delay * (2 ** attempt)
                    logger.info(f"Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                continue

        # All retries failed
        logger.error(f"Order failed after {max_retries} attempts: "
                    f"symbol={symbol} qty={qty} side={side} last_error={last_error}")
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
            logger.info(f"Follower order succeeded: master_id={master_id} symbol={symbol} "
                       f"qty={qty} side={side} order_id={result['order_id']}")
            return result

        # Order failed - record for later retry
        error_msg = "Max retries exceeded"
        logger.error(f"Follower order failed after all retries: master_id={master_id} "
                    f"symbol={symbol} qty={qty} side={side}. Queued for retry.")
        self._record_failed_order(master_id, symbol, qty, side, error_msg)
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

        logger.warning(f"Failed order recorded: master_id={master_id} symbol={symbol} "
                      f"qty={qty} side={side} error=\"{error_message}\"")

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

        logger.warning(f"Trade queued for retry: master_id={master_id} symbol={symbol} "
                      f"qty={qty} side={side}")

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
        logger.info(f"Processing {len(trades)} queued trades")

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
                logger.info(f"Queued trade succeeded: id={trade['id']} symbol={trade['symbol']} "
                           f"qty={trade['qty']} side={trade['side']}")
            else:
                # Failed - increment retry count
                self._increment_retry_count(trade["id"])
                failed += 1
                logger.warning(f"Queued trade failed: id={trade['id']} symbol={trade['symbol']} "
                             f"qty={trade['qty']} side={trade['side']} retry_count={trade['retry_count'] + 1}")

        logger.info(f"Queued trade processing complete: successful={successful} failed={failed}")
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
            # Respect rate limit before making API calls
            if not self._rate_limiter.acquire():
                logger.error("Cancel orders blocked by rate limiter timeout")
                return 0

            logger.info(f"Cancelling open orders{' for symbol=' + symbol if symbol else ''}")
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
                    logger.info(f"Cancelled order: id={order.id} symbol={order.symbol}")
                except Exception as e:
                    logger.warning(f"Failed to cancel order id={order.id}: {e}")
                    continue

            logger.info(f"Cancelled {cancelled}/{len(orders)} open orders")
            return cancelled

        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
            return 0
