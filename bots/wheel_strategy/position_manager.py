"""
Position manager for wheel strategy bot.
Tracks open options, stock positions, and calculates returns.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Tracks and manages all wheel strategy positions including
    open options, assigned stock, and premium collection.
    """

    def __init__(self, db_path: str):
        """
        Initialize position manager.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path

    def get_open_puts(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open put positions."""
        from bots.wheel_strategy.db import get_open_options
        return [p for p in get_open_options(self.db_path, symbol) if p['contract_type'] == 'PUT']

    def get_open_calls(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open call positions."""
        from bots.wheel_strategy.db import get_open_options
        return [p for p in get_open_options(self.db_path, symbol) if p['contract_type'] == 'CALL']

    def get_stock_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get held stock positions."""
        from bots.wheel_strategy.db import get_open_stock_positions
        return get_open_stock_positions(self.db_path, symbol)

    def add_put(self, symbol: str, option_symbol: str, strike: float, expiration: str,
                contracts: int, premium: float, notes: str = "") -> int:
        """Record a new short put position."""
        from bots.wheel_strategy.db import add_options_position, record_trade
        pos_id = add_options_position(self.db_path, symbol=symbol, option_symbol=option_symbol,
                                       contract_type='PUT', strike=strike, expiration=expiration,
                                       contracts=contracts, premium=premium, notes=notes)
        record_trade(self.db_path, symbol, 'put_sold', contracts=contracts,
                     strike=strike, expiration=expiration, premium=premium)
        logger.info(f"Recorded new PUT: {symbol} strike={strike} exp={expiration} "
                    f"contracts={contracts} premium={premium:.2f}")
        return pos_id

    def add_call(self, symbol: str, option_symbol: str, strike: float, expiration: str,
                 contracts: int, premium: float, cost_basis: float, notes: str = "") -> int:
        """Record a new short call position."""
        from bots.wheel_strategy.db import add_options_position, record_trade
        pos_id = add_options_position(self.db_path, symbol=symbol, option_symbol=option_symbol,
                                       contract_type='CALL', strike=strike, expiration=expiration,
                                       contracts=contracts, premium=premium,
                                       cost_basis=cost_basis, notes=notes)
        record_trade(self.db_path, symbol, 'call_sold', contracts=contracts,
                     strike=strike, expiration=expiration, premium=premium, cost_basis=cost_basis)
        logger.info(f"Recorded new CALL: {symbol} strike={strike} exp={expiration} "
                    f"contracts={contracts} premium={premium:.2f} cost_basis={cost_basis:.2f}")
        return pos_id

    def record_assignment(self, symbol: str, strike: float, contracts: int,
                          premium_collected: float, cost_basis: float) -> None:
        """Record a put assignment (stock acquired)."""
        import sqlite3
        shares = contracts * 100
        from bots.wheel_strategy.db import add_stock_position, record_trade, update_option_status

        # Close the put position
        puts = self.get_open_puts(symbol)
        for put in puts:
            if put['strike'] == strike and put['contracts'] == contracts:
                update_option_status(self.db_path, put['id'], 'assigned')
                break

        add_stock_position(self.db_path, symbol, shares, cost_basis,
                           premium_collected=premium_collected)
        record_trade(self.db_path, symbol, 'put_assigned', shares=shares,
                     strike=strike, premium=premium_collected, cost_basis=cost_basis)
        logger.info(f"Recorded assignment: {symbol} {shares} shares at ${cost_basis:.2f}")

    def record_call_exercise(self, symbol: str, strike: float, contracts: int,
                             premium_collected: float, cost_basis: float) -> float:
        """
        Record a covered call exercise (shares sold).

        Returns:
            Realized P&L from the exercise
        """
        import sqlite3
        shares = contracts * 100
        from bots.wheel_strategy.db import record_trade, update_option_status

        # Close the call position
        calls = self.get_open_calls(symbol)
        for call in calls:
            if call['strike'] == strike and call['contracts'] == contracts:
                update_option_status(self.db_path, call['id'], 'exercised')
                break

        # Close the stock position
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE wheel_stock_positions
            SET status = 'called_away'
            WHERE id = (
                SELECT id FROM wheel_stock_positions
                WHERE symbol = ? AND status = 'held' AND shares = ?
                LIMIT 1
            )
        """, (symbol, shares))
        conn.commit()
        conn.close()

        realized_pnl = (strike - cost_basis) * shares + premium_collected
        record_trade(self.db_path, symbol, 'call_exercised', shares=shares,
                     strike=strike, premium=premium_collected, cost_basis=cost_basis,
                     realized_pnl=realized_pnl)
        logger.info(f"Recorded call exercise: {symbol} {shares} shares at ${strike:.2f} "
                    f"P&L=${realized_pnl:.2f}")
        return realized_pnl

    def record_roll(self, symbol: str, old_position_id: int, new_premium: float,
                    new_strike: float, new_expiration: str, notes: str = "") -> None:
        """Record a roll (close old, open new position)."""
        import sqlite3
        from bots.wheel_strategy.db import update_option_status

        # Get old position details
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM wheel_options_positions WHERE id = ?", (old_position_id,))
        old = cursor.fetchone()
        conn.close()

        if old:
            update_option_status(self.db_path, old_position_id, 'rolled', notes=notes)
            event_type = 'put_rolled' if old['contract_type'] == 'PUT' else 'call_rolled'
            from bots.wheel_strategy.db import record_trade
            record_trade(self.db_path, symbol, event_type, contracts=old['contracts'],
                         strike=new_strike, expiration=new_expiration,
                         premium=new_premium, notes=f"Rolled: {notes}")

    def get_premium_summary(self) -> Dict[str, float]:
        """Get total premium collected and realized P&L."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SUM(premium) FROM wheel_trade_history WHERE premium IS NOT NULL
        """)
        total_premium = cursor.fetchone()[0] or 0.0
        cursor.execute("""
            SELECT SUM(realized_pnl) FROM wheel_trade_history WHERE realized_pnl IS NOT NULL
        """)
        total_pnl = cursor.fetchone()[0] or 0.0
        conn.close()
        return {
            "total_premium_collected": total_premium,
            "total_realized_pnl": total_pnl,
            "total_return": total_premium + total_pnl
        }
