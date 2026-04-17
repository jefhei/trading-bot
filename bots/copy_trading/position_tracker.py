"""
Position tracking for copy trading.
Manages open positions per master trader with persistence.
"""
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional, Dict
from decimal import Decimal

from alpaca.trading.client import TradingClient


@dataclass
class Position:
    """Represents a copied position."""
    master_id: str
    symbol: str
    qty: int
    entry_price: float
    entry_time: datetime
    master_order_id: str
    follower_order_id: str
    id: Optional[int] = None  # Database ID
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None


class PositionTracker:
    """
    Tracks open positions per master trader with database persistence.
    """

    def __init__(self, db_path: str, client: TradingClient):
        """
        Initialize position tracker.

        Args:
            db_path: Path to SQLite database
            client: Authenticated Alpaca TradingClient
        """
        self.db_path = db_path
        self.client = client
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                qty INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                entry_time TIMESTAMP NOT NULL,
                master_order_id TEXT NOT NULL,
                follower_order_id TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                closed_qty INTEGER DEFAULT 0,
                remaining_qty INTEGER GENERATED ALWAYS AS (qty - closed_qty) STORED
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_master 
            ON copy_positions(master_id, status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_symbol 
            ON copy_positions(symbol, status)
        """)

        conn.commit()
        conn.close()

    def add_position(self, position: Position) -> int:
        """
        Add a new position to tracking.

        Args:
            position: Position to track

        Returns:
            int: Database ID of the position
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO copy_positions 
            (master_id, symbol, qty, entry_price, entry_time, master_order_id, follower_order_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
        """, (
            position.master_id,
            position.symbol,
            position.qty,
            position.entry_price,
            position.entry_time,
            position.master_order_id,
            position.follower_order_id
        ))

        position_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return position_id

    def close_position(self, master_id: str, symbol: str, 
                       master_closed_qty: Optional[int] = None,
                       qty: Optional[int] = None) -> bool:
        """
        Close or partially close a position.

        Args:
            master_id: Master trader ID
            symbol: Position symbol
            master_closed_qty: Master's closed quantity (for proportional close)
            qty: Exact quantity to close (if not using proportional)

        Returns:
            bool: True if position was updated, False if not found
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get current position
        cursor.execute("""
            SELECT id, qty, closed_qty FROM copy_positions
            WHERE master_id = ? AND symbol = ? AND status = 'open'
        """, (master_id, symbol))

        row = cursor.fetchone()
        if not row:
            conn.close()
            return False

        position_id, total_qty, already_closed = row
        remaining_qty = total_qty - already_closed

        # Calculate close quantity
        if qty is not None:
            close_qty = min(qty, remaining_qty)
        elif master_closed_qty is not None:
            # Proportional close based on master's close
            # Get master position to calculate ratio
            cursor.execute("""
                SELECT master_entry_qty FROM copy_positions
                WHERE master_id = ? AND symbol = ?
            """, (master_id, symbol))
            # For now, simple proportional: close same percentage
            close_qty = int(remaining_qty * (master_closed_qty / total_qty))
        else:
            close_qty = remaining_qty  # Full close

        new_closed_qty = already_closed + close_qty

        if new_closed_qty >= total_qty:
            # Fully closed
            cursor.execute("""
                UPDATE copy_positions 
                SET closed_qty = ?, status = 'closed', closed_time = ?
                WHERE id = ?
            """, (new_closed_qty, datetime.now(), position_id))
        else:
            # Partial close
            cursor.execute("""
                UPDATE copy_positions 
                SET closed_qty = ?
                WHERE id = ?
            """, (new_closed_qty, position_id))

        conn.commit()
        conn.close()

        return True

    def get_open_positions(self, master_id: str) -> List[Position]:
        """
        Get all open positions for a master trader.

        Args:
            master_id: Master trader ID

        Returns:
            List of Position objects
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, master_id, symbol, qty, entry_price, entry_time,
                   master_order_id, follower_order_id
            FROM copy_positions
            WHERE master_id = ? AND status = 'open'
            ORDER BY entry_time DESC
        """, (master_id,))

        positions = []
        for row in cursor.fetchall():
            positions.append(Position(
                id=row[0],
                master_id=row[1],
                symbol=row[2],
                qty=row[3],
                entry_price=row[4],
                entry_time=datetime.fromisoformat(row[5]),
                master_order_id=row[6],
                follower_order_id=row[7]
            ))

        conn.close()
        return positions

    def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        """
        Get all open positions for a symbol across all masters.

        Args:
            symbol: Stock symbol

        Returns:
            List of Position objects
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, master_id, symbol, qty, entry_price, entry_time,
                   master_order_id, follower_order_id
            FROM copy_positions
            WHERE symbol = ? AND status = 'open'
            ORDER BY entry_time DESC
        """, (symbol,))

        positions = []
        for row in cursor.fetchall():
            positions.append(Position(
                id=row[0],
                master_id=row[1],
                symbol=row[2],
                qty=row[3],
                entry_price=row[4],
                entry_time=datetime.fromisoformat(row[5]),
                master_order_id=row[6],
                follower_order_id=row[7]
            ))

        conn.close()
        return positions

    def get_all_open_positions(self) -> List[Position]:
        """
        Get all open positions across all masters.

        Returns:
            List of Position objects
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, master_id, symbol, qty, entry_price, entry_time,
                   master_order_id, follower_order_id
            FROM copy_positions
            WHERE status = 'open'
            ORDER BY entry_time DESC
        """)

        positions = []
        for row in cursor.fetchall():
            positions.append(Position(
                id=row[0],
                master_id=row[1],
                symbol=row[2],
                qty=row[3],
                entry_price=row[4],
                entry_time=datetime.fromisoformat(row[5]),
                master_order_id=row[6],
                follower_order_id=row[7]
            ))

        conn.close()
        return positions

    def sync_positions_from_api(self) -> List[Position]:
        """
        Sync positions from Alpaca API on startup.

        Returns:
            List of synchronized Position objects
        """
        # Get positions from API
        api_positions = self.client.get_all_positions()

        synced_positions = []
        for api_pos in api_positions:
            # Note: We can't directly map API positions to master positions
            # This is a placeholder - in reality you'd need order history
            # to map positions back to masters
            position = Position(
                master_id="synced",  # Would need proper mapping
                symbol=api_pos.symbol,
                qty=int(float(api_pos.qty)),
                entry_price=float(api_pos.avg_entry_price),
                entry_time=datetime.now(),  # Would come from API
                master_order_id="synced",
                follower_order_id="synced"
            )
            synced_positions.append(position)

        return synced_positions

    def get_position_summary(self, master_id: Optional[str] = None) -> Dict:
        """
        Get summary of positions.

        Args:
            master_id: Optional master ID to filter by

        Returns:
            Dict with position counts and values
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if master_id:
            cursor.execute("""
                SELECT COUNT(*), COALESCE(SUM(qty - closed_qty), 0)
                FROM copy_positions
                WHERE master_id = ? AND status = 'open'
            """, (master_id,))
        else:
            cursor.execute("""
                SELECT COUNT(*), COALESCE(SUM(qty - closed_qty), 0)
                FROM copy_positions
                WHERE status = 'open'
            """)

        count, total_qty = cursor.fetchone()
        conn.close()

        return {
            "position_count": count,
            "total_shares": total_qty
        }
