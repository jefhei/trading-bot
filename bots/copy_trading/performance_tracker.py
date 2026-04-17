"""
Performance tracking for copy trading.
Tracks win rate, returns, drawdowns, and other metrics per master trader.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any


@dataclass
class TradeRecord:
    """Record of a copied trade."""
    id: Optional[int]
    master_id: str
    symbol: str
    entry_price: float
    exit_price: float
    qty: int
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_pct: float


class PerformanceTracker:
    """
    Tracks performance metrics for master traders.
    """

    def __init__(self, db_path: str):
        """
        Initialize performance tracker.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Table for completed trades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                entry_time TIMESTAMP NOT NULL,
                exit_time TIMESTAMP NOT NULL,
                pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL
            )
        """)

        # Table for equity curve tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS copy_equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_id TEXT,
                timestamp TIMESTAMP NOT NULL,
                equity_value REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_master 
            ON copy_trades(master_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_equity_master 
            ON copy_equity(master_id, timestamp)
        """)

        conn.commit()
        conn.close()

    def record_trade(
        self,
        master_id: str,
        symbol: str,
        entry_price: float,
        exit_price: float,
        qty: int,
        entry_time: datetime,
        exit_time: datetime
    ) -> int:
        """
        Record a completed trade.

        Args:
            master_id: Master trader ID
            symbol: Trade symbol
            entry_price: Entry price per share
            exit_price: Exit price per share
            qty: Number of shares
            entry_time: Entry timestamp
            exit_time: Exit timestamp

        Returns:
            int: Database ID of the trade record
        """
        # Calculate P&L
        pnl = (exit_price - entry_price) * qty
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO copy_trades
            (master_id, symbol, entry_price, exit_price, qty, entry_time, exit_time, pnl, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (master_id, symbol, entry_price, exit_price, qty, entry_time, exit_time, pnl, pnl_pct))

        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return trade_id

    def record_equity(self, master_id: str, timestamp: datetime, equity_value: float):
        """
        Record equity value for a master trader.

        Args:
            master_id: Master trader ID (or None for global)
            timestamp: Timestamp of the equity snapshot
            equity_value: Equity value
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO copy_equity (master_id, timestamp, equity_value)
            VALUES (?, ?, ?)
        """, (master_id, timestamp, equity_value))

        conn.commit()
        conn.close()

    def get_master_metrics(self, master_id: str) -> Dict[str, Any]:
        """
        Get performance metrics for a master trader.

        Args:
            master_id: Master trader ID

        Returns:
            Dict with performance metrics
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get all trades for this master
        cursor.execute("""
            SELECT pnl, pnl_pct FROM copy_trades
            WHERE master_id = ?
            ORDER BY exit_time
        """, (master_id,))

        trades = cursor.fetchall()

        if not trades:
            conn.close()
            return {
                "master_id": master_id,
                "total_trades": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "avg_win_loss_ratio": 0.0,
                "total_pnl": 0.0,
                "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0
            }

        # Calculate basic metrics
        total_trades = len(trades)
        wins = [t for t in trades if t[0] > 0]
        losses = [t for t in trades if t[0] <= 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0

        # Calculate average win/loss percentages
        avg_win_pct = sum(t[1] for t in wins) / len(wins) if wins else 0
        avg_loss_pct = abs(sum(t[1] for t in losses) / len(losses)) if losses else 0

        # Win/loss ratio
        avg_win_loss_ratio = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else float('inf') if avg_win_pct > 0 else 0

        # Total P&L
        total_pnl = sum(t[0] for t in trades)

        # Get equity curve for return calculation
        cursor.execute("""
            SELECT equity_value FROM copy_equity
            WHERE master_id = ?
            ORDER BY timestamp
        """, (master_id,))

        equity_points = cursor.fetchall()

        total_return_pct = 0.0
        if len(equity_points) >= 2:
            starting_equity = equity_points[0][0]
            ending_equity = equity_points[-1][0]
            if starting_equity > 0:
                total_return_pct = ((ending_equity - starting_equity) / starting_equity) * 100

        # Calculate max drawdown from equity curve
        max_drawdown_pct = self._calculate_max_drawdown(master_id)

        conn.close()

        return {
            "master_id": master_id,
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": win_rate,
            "avg_win_pct": avg_win_pct,
            "avg_loss_pct": avg_loss_pct,
            "avg_win_loss_ratio": avg_win_loss_ratio,
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "max_drawdown_pct": max_drawdown_pct
        }

    def _calculate_max_drawdown(self, master_id: str) -> float:
        """
        Calculate maximum drawdown from equity curve.

        Args:
            master_id: Master trader ID

        Returns:
            float: Maximum drawdown percentage
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT equity_value FROM copy_equity
            WHERE master_id = ?
            ORDER BY timestamp
        """, (master_id,))

        equity_values = [row[0] for row in cursor.fetchall()]
        conn.close()

        if not equity_values:
            return 0.0

        max_drawdown = 0.0
        peak = equity_values[0]

        for value in equity_values:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return max_drawdown

    def get_all_masters_metrics(self) -> List[Dict[str, Any]]:
        """
        Get metrics for all master traders.

        Returns:
            List of metrics dictionaries
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT master_id FROM copy_trades
        """)

        master_ids = [row[0] for row in cursor.fetchall()]
        conn.close()

        return [self.get_master_metrics(mid) for mid in master_ids]

    def get_trade_history(
        self,
        master_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get trade history with optional filtering.

        Args:
            master_id: Optional master ID to filter by
            symbol: Optional symbol to filter by
            limit: Maximum number of trades to return

        Returns:
            List of trade records
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = "SELECT * FROM copy_trades WHERE 1=1"
        params = []

        if master_id:
            query += " AND master_id = ?"
            params.append(master_id)

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)

        query += " ORDER BY exit_time DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        trades = []
        for row in rows:
            trades.append({
                "id": row[0],
                "master_id": row[1],
                "symbol": row[2],
                "entry_price": row[3],
                "exit_price": row[4],
                "qty": row[5],
                "entry_time": row[6],
                "exit_time": row[7],
                "pnl": row[8],
                "pnl_pct": row[9]
            })

        return trades
