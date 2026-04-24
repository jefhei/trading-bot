"""
Database schema for wheel strategy bot.
Stores watchlist, positions, trades, and performance metrics.
"""
import sqlite3
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def init_db(db_path: str) -> None:
    """
    Initialize wheel strategy database tables.

    Args:
        db_path: Path to SQLite database file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Watchlist configuration
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            max_contracts INTEGER DEFAULT 5,
            max_capital REAL DEFAULT 10000,
            min_premium_pct REAL DEFAULT 1.0,
            target_delta REAL DEFAULT 0.30,
            enabled INTEGER DEFAULT 1,
            sector TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Open options positions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_options_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            contract_type TEXT NOT NULL CHECK(contract_type IN ('PUT', 'CALL')),
            strike REAL NOT NULL,
            expiration DATE NOT NULL,
            contracts INTEGER NOT NULL,
            premium REAL NOT NULL,
            sold_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'rolled', 'assigned', 'exercised', 'expired', 'closed')),
            cost_basis REAL,
            notes TEXT
        )
    """)

    # Stock positions (assigned from puts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_stock_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            shares INTEGER NOT NULL,
            cost_basis REAL NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'held' CHECK(status IN ('held', 'called_away', 'sold')),
            premium_collected REAL DEFAULT 0.0,
            notes TEXT
        )
    """)

    # Trade history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'put_sold', 'put_assigned', 'call_sold', 'call_exercised',
                'put_rolled', 'call_rolled', 'put_expired', 'put_closed',
                'call_closed', 'stock_sold'
            )),
            contracts INTEGER,
            shares INTEGER,
            strike REAL,
            expiration DATE,
            premium REAL,
            cost_basis REAL,
            realized_pnl REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    """)

    # Performance metrics (daily snapshots)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL UNIQUE,
            total_premium_collected REAL DEFAULT 0.0,
            total_realized_pnl REAL DEFAULT 0.0,
            open_puts INTEGER DEFAULT 0,
            open_calls INTEGER DEFAULT 0,
            stock_positions_count INTEGER DEFAULT 0,
            total_capital_deployed REAL DEFAULT 0.0,
            cash_balance REAL DEFAULT 0.0
        )
    """)

    # Earnings calendar cache
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wheel_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            type TEXT DEFAULT 'quarterly',
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, earnings_date)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_options_symbol ON wheel_options_positions(symbol, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_options_expiration ON wheel_options_positions(expiration)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stock_symbol ON wheel_stock_positions(symbol, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_symbol ON wheel_trade_history(symbol, event_type)")

    conn.commit()
    conn.close()
    logger.info(f"Initialized wheel strategy database at {db_path}")


# --- Watchlist queries ---

def get_watchlist(db_path: str, enabled_only: bool = True) -> List[Dict[str, Any]]:
    """Get watchlist entries."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = "SELECT * FROM wheel_watchlist"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY symbol"
    cursor.execute(query)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def add_watchlist_entry(db_path: str, symbol: str, **kwargs) -> None:
    """Add or update a watchlist entry."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    defaults = {
        'max_contracts': 5, 'max_capital': 10000,
        'min_premium_pct': 1.0, 'target_delta': 0.30,
        'enabled': 1, 'sector': None
    }
    defaults.update(kwargs)
    cursor.execute("""
        INSERT INTO wheel_watchlist
        (symbol, max_contracts, max_capital, min_premium_pct, target_delta, enabled, sector, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            max_contracts = excluded.max_contracts,
            max_capital = excluded.max_capital,
            min_premium_pct = excluded.min_premium_pct,
            target_delta = excluded.target_delta,
            enabled = excluded.enabled,
            sector = excluded.sector,
            updated_at = CURRENT_TIMESTAMP
    """, (symbol, defaults['max_contracts'], defaults['max_capital'],
          defaults['min_premium_pct'], defaults['target_delta'],
          defaults['enabled'], defaults['sector'], datetime.now()))
    conn.commit()
    conn.close()


# --- Options position queries ---

def get_open_options(db_path: str, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get open options positions."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = "SELECT * FROM wheel_options_positions WHERE status = 'open'"
    params: list = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    query += " ORDER BY expiration"
    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def add_options_position(db_path: str, **kwargs) -> int:
    """Record a new options position."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO wheel_options_positions
        (symbol, option_symbol, contract_type, strike, expiration, contracts, premium, cost_basis, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (kwargs['symbol'], kwargs['option_symbol'], kwargs['contract_type'],
          kwargs['strike'], kwargs['expiration'], kwargs['contracts'],
          kwargs['premium'], kwargs.get('cost_basis'), kwargs.get('notes')))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def update_option_status(db_path: str, position_id: int, status: str, notes: Optional[str] = None) -> None:
    """Update an options position status."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE wheel_options_positions
        SET status = ?, notes = ?
        WHERE id = ?
    """, (status, notes, position_id))
    conn.commit()
    conn.close()


# --- Stock position queries ---

def get_open_stock_positions(db_path: str, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get held stock positions."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = "SELECT * FROM wheel_stock_positions WHERE status = 'held'"
    params: list = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


def add_stock_position(db_path: str, symbol: str, shares: int, cost_basis: float, **kwargs) -> int:
    """Record a new stock position from assignment."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO wheel_stock_positions (symbol, shares, cost_basis, premium_collected, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (symbol, shares, cost_basis, kwargs.get('premium_collected', 0), kwargs.get('notes')))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


# --- Trade history ---

def record_trade(db_path: str, symbol: str, event_type: str, **kwargs) -> int:
    """Record a trade event in history."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO wheel_trade_history
        (symbol, event_type, contracts, shares, strike, expiration, premium, cost_basis, realized_pnl, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, event_type, kwargs.get('contracts'), kwargs.get('shares'),
          kwargs.get('strike'), kwargs.get('expiration'), kwargs.get('premium'),
          kwargs.get('cost_basis'), kwargs.get('realized_pnl'), kwargs.get('notes')))
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


# --- Earnings ---

def cache_earnings(db_path: str, symbol: str, earnings_date: str, **kwargs) -> None:
    """Cache an earnings date."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO wheel_earnings (symbol, earnings_date, type, fetched_at)
        VALUES (?, ?, ?, ?)
    """, (symbol, earnings_date, kwargs.get('type', 'quarterly'), datetime.now()))
    conn.commit()
    conn.close()


def get_upcoming_earnings(db_path: str, symbol: str, before_date: str) -> Optional[str]:
    """Check if symbol has earnings before a given date."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT earnings_date FROM wheel_earnings
        WHERE symbol = ? AND earnings_date <= ?
        ORDER BY earnings_date ASC
        LIMIT 1
    """, (symbol, before_date))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


# --- Performance ---

def record_daily_performance(db_path: str, date: str, **metrics) -> None:
    """Record daily performance snapshot."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO wheel_performance
        (date, total_premium_collected, total_realized_pnl, open_puts, open_calls,
         stock_positions_count, total_capital_deployed, cash_balance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, metrics.get('total_premium_collected', 0), metrics.get('total_realized_pnl', 0),
          metrics.get('open_puts', 0), metrics.get('open_calls', 0),
          metrics.get('stock_positions_count', 0), metrics.get('total_capital_deployed', 0),
          metrics.get('cash_balance', 0)))
    conn.commit()
    conn.close()
