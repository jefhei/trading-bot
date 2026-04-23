"""
Signal processing for copy trading.
Handles incoming trade signals from master traders.
"""
import sqlite3
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
import json

from alpaca.trading.client import TradingClient

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Signal:
    """Trade signal from a master trader."""
    master_id: str
    symbol: str
    side: str
    qty: int
    price: float
    timestamp: datetime
    order_id: str
    asset_class: str = "us_equity"


class SignalProcessor:
    """
    Processes trade signals from master traders.
    Supports multiple signal sources (streaming, webhooks, manual).
    """

    def __init__(self, db_path: str, client: Optional[TradingClient] = None):
        """
        Initialize signal processor.

        Args:
            db_path: Path to SQLite database
            client: Optional Alpaca TradingClient
        """
        self.db_path = db_path
        self.client = client
        self._signal_handlers: List[Callable[[Signal], None]] = []
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Table for master configurations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_masters (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                account_id TEXT,
                allocation_pct REAL DEFAULT 0.0,
                max_position_pct REAL DEFAULT 10.0,
                enabled INTEGER DEFAULT 1,
                sizing_method TEXT DEFAULT 'proportional',
                filters_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table for signal log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                order_id TEXT,
                asset_class TEXT,
                processed INTEGER DEFAULT 0,
                processed_at TIMESTAMP,
                latency_ms INTEGER
            )
        """)

        # Table for queued signals during downtime
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_signal_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT NOT NULL,
                signal_data TEXT NOT NULL,
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retry_count INTEGER DEFAULT 0
            )
        """)

        conn.commit()
        conn.close()

    def register_master(self, config: Dict[str, Any]) -> bool:
        """
        Register a new master trader.

        Args:
            config: Master configuration dictionary

        Returns:
            bool: True if registration successful

        Raises:
            ValueError: If required fields are missing
        """
        if "id" not in config:
            raise ValueError("Master ID is required")

        master_id = config["id"]

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO copy_masters
            (id, name, account_id, allocation_pct, max_position_pct, enabled, sizing_method, filters_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            master_id,
            config.get("name", master_id),
            config.get("account_id"),
            config.get("allocation_pct", 0.0),
            config.get("max_position_pct", 10.0),
            1 if config.get("enabled", True) else 0,
            config.get("sizing_method", "proportional"),
            json.dumps(config.get("filters", {})),
            datetime.now()
        ))

        conn.commit()
        conn.close()

        return True

    def disable_master(self, master_id: str) -> bool:
        """
        Disable a master trader.

        Args:
            master_id: Master trader ID

        Returns:
            bool: True if disabled successfully
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE copy_masters SET enabled = 0, updated_at = ?
            WHERE id = ?
        """, (datetime.now(), master_id))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def remove_master(self, master_id: str) -> bool:
        """
        Remove a master trader.

        Args:
            master_id: Master trader ID

        Returns:
            bool: True if removed successfully
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM copy_masters WHERE id = ?", (master_id,))

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def get_master(self, master_id: str) -> Optional[Dict[str, Any]]:
        """
        Get master trader configuration.

        Args:
            master_id: Master trader ID

        Returns:
            Dict with master config or None if not found
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, name, account_id, allocation_pct, max_position_pct, enabled, sizing_method, filters_json
            FROM copy_masters
            WHERE id = ?
        """, (master_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            "id": row[0],
            "name": row[1],
            "account_id": row[2],
            "allocation_pct": row[3],
            "max_position_pct": row[4],
            "enabled": bool(row[5]),
            "sizing_method": row[6],
            "filters": json.loads(row[7]) if row[7] else {}
        }

    def get_registered_masters(self) -> List[Dict[str, Any]]:
        """
        Get all registered master traders.

        Returns:
            List of master configurations
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, name, account_id, allocation_pct, max_position_pct, enabled, sizing_method, filters_json
            FROM copy_masters
            ORDER BY name
        """)

        masters = []
        for row in cursor.fetchall():
            masters.append({
                "id": row[0],
                "name": row[1],
                "account_id": row[2],
                "allocation_pct": row[3],
                "max_position_pct": row[4],
                "enabled": bool(row[5]),
                "sizing_method": row[6],
                "filters": json.loads(row[7]) if row[7] else {}
            })

        conn.close()
        return masters

    def process_signal(self, signal: Signal) -> bool:
        """
        Process an incoming trade signal.

        Args:
            signal: Trade signal to process

        Returns:
            bool: True if signal was processed
        """
        logger.info(f"Processing signal: master={signal.master_id} symbol={signal.symbol} "
                   f"side={signal.side} qty={signal.qty} price={signal.price}")

        received_at = datetime.now()

        # Log the signal
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO copy_signals
            (master_id, symbol, side, qty, price, timestamp, order_id, asset_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.master_id,
            signal.symbol,
            signal.side,
            signal.qty,
            signal.price,
            signal.timestamp,
            signal.order_id,
            signal.asset_class
        ))

        signal_db_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Calculate and record latency
        latency_ms = int((received_at - signal.timestamp).total_seconds() * 1000)
        self._record_latency(signal_db_id, latency_ms)

        # Check if master is enabled
        master = self.get_master(signal.master_id)
        if not master or not master.get("enabled"):
            logger.warning(f"Signal ignored — master {signal.master_id} is disabled or not found")
            return False

        # Notify all registered handlers
        handler_count = len(self._signal_handlers)
        handler_errors = 0
        for handler in self._signal_handlers:
            try:
                handler(signal)
            except Exception as e:
                handler_errors += 1
                logger.error(f"Error in signal handler for master={signal.master_id} "
                           f"symbol={signal.symbol}: {e}\n{traceback.format_exc()}")

        if handler_errors > 0:
            logger.warning(f"Signal processed with {handler_errors}/{handler_count} handler errors")

        # Mark as processed
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE copy_signals SET processed = 1, processed_at = ?
            WHERE id = ?
        """, (datetime.now(), signal_db_id))

        conn.commit()
        conn.close()

        logger.info(f"Signal processed: db_id={signal_db_id} latency={latency_ms}ms "
                   f"handlers={handler_count - handler_errors}/{handler_count}")
        return True

    def _record_latency(self, signal_id: int, latency_ms: int):
        """Record signal processing latency."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE copy_signals SET latency_ms = ? WHERE id = ?
        """, (latency_ms, signal_id))

        conn.commit()
        conn.close()

    def add_signal_handler(self, handler: Callable[[Signal], None]):
        """
        Add a handler to be called when signals are processed.

        Args:
            handler: Callable that accepts a Signal
        """
        self._signal_handlers.append(handler)

    def queue_signal_for_retry(self, signal: Signal):
        """
        Queue a signal for retry when processing fails.

        Args:
            signal: Signal to queue
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO copy_signal_queue (master_id, signal_data)
            VALUES (?, ?)
        """, (signal.master_id, json.dumps({
            "symbol": signal.symbol,
            "side": signal.side,
            "qty": signal.qty,
            "price": signal.price,
            "timestamp": signal.timestamp.isoformat(),
            "order_id": signal.order_id,
            "asset_class": signal.asset_class
        })))

        conn.commit()
        conn.close()

    def get_queued_signals(self) -> List[Dict[str, Any]]:
        """
        Get signals waiting to be retried.

        Returns:
            List of queued signal records
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, master_id, signal_data, received_at, retry_count
            FROM copy_signal_queue
            ORDER BY received_at
        """)

        signals = []
        for row in cursor.fetchall():
            data = json.loads(row[2])
            signals.append({
                "id": row[0],
                "master_id": row[1],
                "symbol": data.get("symbol"),
                "side": data.get("side"),
                "qty": data.get("qty"),
                "price": data.get("price"),
                "received_at": row[3],
                "retry_count": row[4]
            })

        conn.close()
        return signals

    def get_signal_latency_stats(self, master_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get latency statistics for signal processing.

        Args:
            master_id: Optional master ID to filter by

        Returns:
            Dict with latency statistics
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if master_id:
            cursor.execute("""
                SELECT AVG(latency_ms), MIN(latency_ms), MAX(latency_ms), COUNT(*)
                FROM copy_signals
                WHERE master_id = ? AND latency_ms IS NOT NULL
            """, (master_id,))
        else:
            cursor.execute("""
                SELECT AVG(latency_ms), MIN(latency_ms), MAX(latency_ms), COUNT(*)
                FROM copy_signals
                WHERE latency_ms IS NOT NULL
            """)

        row = cursor.fetchone()
        conn.close()

        return {
            "avg_latency_ms": row[0] or 0,
            "min_latency_ms": row[1] or 0,
            "max_latency_ms": row[2] or 0,
            "sample_count": row[3] or 0
        }
