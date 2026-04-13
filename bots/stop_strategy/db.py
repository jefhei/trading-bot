"""
Database utilities specific to Stop Strategy Bot.
"""
import sqlite3
import json
from typing import Dict, List, Any, Optional
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "stop_strategy.db"


def init_db(db_path: Optional[str] = None) -> str:
    """
    Initialize the SQLite database with required tables for stop strategy.

    Args:
        db_path: Path to database file. If None, uses default.

    Returns:
        str: Path to the initialized database.
    """
    if db_path is None:
        db_path = str(DEFAULT_DB_PATH)

    # Ensure data directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Orders table for tracking all orders
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            state TEXT NOT NULL,
            entry_price REAL,
            stop_order_id TEXT,
            take_profit_price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            details TEXT
        )
    """)

    # Order events log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    return db_path


def log_order_event(
    db_path: str,
    order_id: str,
    symbol: str,
    event_type: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log an order event to the database.

    Args:
        db_path: Path to database file
        order_id: Order identifier
        symbol: Stock symbol
        event_type: Type of event (e.g., "submitted", "filled", "canceled")
        details: Optional dict of additional details
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Insert or update main orders table
    details_json = json.dumps(details) if details else None

    cursor.execute("""
        INSERT OR REPLACE INTO orders
        (order_id, symbol, state, timestamp, details)
        VALUES (?, ?, ?, datetime('now'), ?)
    """, (order_id, symbol, event_type, details_json))

    # Also log to events table for audit trail
    cursor.execute("""
        INSERT INTO order_events (order_id, event_type, details, timestamp)
        VALUES (?, ?, ?, datetime('now'))
    """, (order_id, event_type, details_json))

    conn.commit()
    conn.close()


def get_open_positions(db_path: str) -> List[Dict[str, Any]]:
    """
    Get all open positions from the database.

    Args:
        db_path: Path to database file

    Returns:
        List of dicts with position information
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT order_id, symbol, state, entry_price, stop_order_id, take_profit_price, timestamp
        FROM orders
        WHERE state IN ('PENDING', 'FILLED', 'WATCHING')
    """)

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "order_id": row["order_id"],
            "symbol": row["symbol"],
            "state": row["state"],
            "entry_price": row["entry_price"],
            "stop_order_id": row["stop_order_id"],
            "take_profit_price": row["take_profit_price"],
            "timestamp": row["timestamp"],
        }
        for row in rows
    ]


def update_order_state(
    db_path: str,
    order_id: str,
    state: str,
) -> None:
    """
    Update the state of an order.

    Args:
        db_path: Path to database file
        order_id: Order identifier
        state: New state value
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE orders SET state = ?, timestamp = datetime('now')
        WHERE order_id = ?
    """, (state, order_id))

    conn.commit()
    conn.close()
