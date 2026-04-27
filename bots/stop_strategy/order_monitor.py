"""
Order monitoring and state management for Stop Strategy Bot.
Tracks order lifecycle and handles breakeven stop adjustments.
"""
import sqlite3
from enum import Enum
from typing import Dict, Optional, Any
from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError
import logging

logger = logging.getLogger(__name__)

class OrderState(Enum):
    """Enumeration of possible order states."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    WATCHING = "WATCHING"
    CLOSED = "CLOSED"


class OrderMonitor:
    """
    Monitors order status, handles state transitions, and manages
    breakeven stop-loss adjustments.
    """

    def __init__(self, client: TradingClient, db_path: str):
        """
        Initialize order monitor.

        Args:
            client: Authenticated Alpaca TradingClient
            db_path: Path to SQLite database
        """
        self.client = client
        self.db_path = db_path
        self._state: Dict[str, Dict[str, Any]] = {}

    def register_order(
        self,
        order_id: str,
        symbol: str,
        entry_price: float,
        stop_order_id: Optional[str] = None,
        take_profit_price: Optional[float] = None,
    ) -> None:
        """
        Register a new order for monitoring.

        Args:
            order_id: Main order ID
            symbol: Stock symbol
            entry_price: Entry price
            stop_order_id: Stop-loss order ID
            take_profit_price: Take profit target price
        """
        self._state[order_id] = {
            "order_id": order_id,
            "symbol": symbol,
            "state": OrderState.PENDING,
            "entry_price": entry_price,
            "stop_order_id": stop_order_id,
            "take_profit_price": take_profit_price,
            "breakeven_triggered": False,
        }

        # Persist to database
        self._persist_order(order_id, symbol, "PENDING", entry_price, stop_order_id)

    def get_state(self, order_id: str) -> OrderState:
        """
        Get current state of an order.

        Args:
            order_id: Order ID to check

        Returns:
            OrderState: Current state
        """
        if order_id not in self._state:
            return OrderState.CLOSED  # Unknown orders considered closed
        return self._state[order_id]["state"]

    def _find_order_by_id(self, order_id: str) -> Optional[str]:
        """
        Find the parent order ID for a given order ID (handles entry or stop order IDs).
        
        Returns:
            Parent order ID or None if not found
        """
        # Direct match
        if order_id in self._state:
            return order_id
        
        # Check if this is a stop order ID
        for parent_id, order_info in self._state.items():
            if order_info.get("stop_order_id") == order_id:
                return parent_id
        
        return None

    def handle_event(self, event: Dict[str, Any]) -> None:
        """
        Handle a trade update event.

        Args:
            event: Trade update event dict
        """
        event_type = event.get("event")
        order_data = event.get("order", {})
        order_id = order_data.get("id")

        if not order_id:
            return

        # Find the parent order ID (handles entry or stop order IDs)
        parent_order_id = self._find_order_by_id(order_id)
        if not parent_order_id:
            # Unknown order - log silently and ignore
            return

        order_info = self._state[parent_order_id]

        if event_type == "fill":
            if order_id == order_info.get("stop_order_id"):
                # Stop-loss filled - position closed
                order_info["state"] = OrderState.CLOSED
                self._update_db_state(parent_order_id, "CLOSED")
                logger.info(f"Stop order {order_id} filled for {order_info['symbol']} — position closed")
            elif order_info["state"] == OrderState.PENDING:
                order_info["filled_price"] = float(order_data.get("filled_avg_price", 0))
                order_info["state"] = OrderState.WATCHING  # Now we watch for breakeven
                self._update_db_state(parent_order_id, "WATCHING")
                logger.info(f"Entry order {order_id} filled for {order_info['symbol']} at ${order_info['filled_price']:.2f}")

        elif event_type == "canceled":
            if order_info["state"] != OrderState.CLOSED:
                order_info["state"] = OrderState.CLOSED
                self._update_db_state(parent_order_id, "CLOSED")

        else:
            # Log unknown events (partial_fill, rejected, expired, etc.)
            logger.warning(
                f"Unknown event type '{event_type}' for order {order_id} "
                f"({parent_order_id}). Ignoring."
            )

    def check_breakeven_adjustment(
        self,
        order_id: str,
        current_price: float,
    ) -> bool:
        """
        Check if breakeven stop adjustment should trigger (50% of TP distance).

        Args:
            order_id: Order ID to check
            current_price: Current market price

        Returns:
            bool: True if adjustment was triggered
        """
        if order_id not in self._state:
            return False

        order_info = self._state[order_id]

        # Only adjust once
        if order_info.get("breakeven_triggered"):
            return False

        # Must be actively monitored to adjust
        if order_info["state"] not in (OrderState.FILLED, OrderState.WATCHING):
            return False

        entry_price = order_info["entry_price"]
        take_profit_price = order_info.get("take_profit_price")

        if not take_profit_price:
            return False

        # Calculate 50% of take-profit distance
        tp_distance = take_profit_price - entry_price
        trigger_price = entry_price + (tp_distance * 0.5)

        # Check if current price has reached 50% of TP distance
        if current_price >= trigger_price:
            # Cancel existing stop and place new one at breakeven
            stop_order_id = order_info.get("stop_order_id")
            if stop_order_id:
                try:
                    self.client.cancel_order_by_id(stop_order_id)
                except APIError as e:
                    logger.error(f"Failed to cancel stop order {stop_order_id}: {e}")
                    return False
                except Exception as e:
                    logger.error(f"Unexpected error canceling stop order {stop_order_id}: {e}")
                    return False

                # Mark as triggered (new stop placement would be handled separately)
                order_info["breakeven_triggered"] = True
                logger.info(
                    f"Breakeven triggered for {order_info['symbol']} @ ${current_price:.2f} "
                    f"(entry ${entry_price:.2f}, TP ${take_profit_price:.2f})"
                )
                return True

        return False

    def load_state_from_db(self) -> None:
        """
        Load order states from database to recover after restart.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT order_id, symbol, state, entry_price, stop_order_id "
                "FROM orders WHERE state IN ('PENDING', 'FILLED', 'WATCHING')"
            )

            for row in cursor.fetchall():
                order_id, symbol, state, entry_price, stop_order_id = row
                self._state[order_id] = {
                    "order_id": order_id,
                    "symbol": symbol,
                    "state": OrderState(state),
                    "entry_price": entry_price,
                    "stop_order_id": stop_order_id,
                    "breakeven_triggered": False,
                }

            conn.close()
        except Exception as e:
            logger.error(f"Failed to load order state from database: {e}")
            # Don't raise — better to start with empty state than crash

    def _persist_order(
        self,
        order_id: str,
        symbol: str,
        state: str,
        entry_price: float,
        stop_order_id: Optional[str] = None,
    ) -> None:
        """Persist order to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO orders
            (order_id, symbol, state, entry_price, stop_order_id, timestamp)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (order_id, symbol, state, entry_price, stop_order_id),
        )

        conn.commit()
        conn.close()

    def _update_db_state(self, order_id: str, state: str) -> None:
        """Update order state in database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE orders SET state = ? WHERE order_id = ?",
            (state, order_id),
        )

        conn.commit()
        conn.close()
