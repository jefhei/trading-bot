"""
SQLite database utilities for trading bot data persistence.
"""
import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "trading.db"


def init_db(db_path: Optional[Path] = None) -> str:
    """
    Initialize the SQLite database with required tables.
    
    Args:
        db_path: Path to database file. If None, uses default.
    
    Returns:
        str: Path to the initialized database.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    
    # Ensure data directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Orders table
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
    
    # Trades table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Positions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            qty INTEGER NOT NULL,
            cost_basis REAL,
            stop_order_id TEXT,
            state TEXT DEFAULT 'OPEN',
            opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME
        )
    """)
    
    conn.commit()
    conn.close()
    
    return str(db_path)


def get_db_connection(db_path: Optional[str] = None):
    """Get a database connection with row factory."""
    if db_path is None:
        db_path = str(DEFAULT_DB_PATH)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn
