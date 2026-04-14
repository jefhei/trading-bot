#!/usr/bin/python3
"""
Position Monitor Script
Checks open positions and triggers breakeven adjustments when appropriate.
Run via cron every 5 minutes during market hours.

Usage:
    python scripts/monitor_positions.py

Cron:
    */5 9-16 * * 1-5 cd /home/jeff/Projects/alpaca && python with_market_check.py scripts/monitor_positions.py >> logs/cron_monitor.log 2>&1
"""
import sys
import fcntl
import os
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.alpaca_client import get_trading_client
from bots.stop_strategy.order_monitor import OrderMonitor, OrderState
from bots.stop_strategy.db import get_open_positions, init_db


def get_lock():
    """Prevent overlapping runs with a lock file."""
    lock_path = Path("/tmp/trading_bot_monitor.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except IOError:
        print(f"[{datetime.now()}] Another monitor instance running. Exiting.")
        return None


def get_current_price(client, symbol):
    """Get current price for a symbol."""
    try:
        # Get latest trade
        trades = client.get_trades(symbol, limit=1)
        if trades:
            return float(trades[0].price)
        return None
    except Exception as e:
        print(f"Error getting price for {symbol}: {e}")
        return None


def main():
    # Prevent overlapping runs
    lock = get_lock()
    if lock is None:
        return 0
    
    try:
        # Initialize
        db_path = "data/orders.db"
        init_db(db_path)
        
        client = get_trading_client()
        monitor = OrderMonitor(client, db_path=db_path)
        
        # Load state from DB (critical for cron safety)
        monitor.load_state_from_db()
        
        # Get open positions
        positions = get_open_positions(db_path)
        
        if not positions:
            print(f"[{datetime.now()}] No open positions to monitor.")
            return 0
        
        print(f"[{datetime.now()}] Monitoring {len(positions)} position(s)...")
        
        actions_taken = 0
        
        for position in positions:
            order_id = position['order_id']
            symbol = position['symbol']
            state = monitor.get_state(order_id)
            
            print(f"  {symbol} ({order_id}): {state.value}")
            
            # Only check breakeven for filled positions
            if state != OrderState.FILLED:
                continue
            
            # Get current price
            current_price = get_current_price(client, symbol)
            if current_price is None:
                continue
            
            # Check breakeven adjustment
            triggered = monitor.check_breakeven_adjustment(order_id, current_price)
            
            if triggered:
                print(f"    ✅ Breakeven triggered at ${current_price:.2f}")
                print(f"    Stop-loss moved to entry price")
                actions_taken += 1
            else:
                entry = position.get('entry_price', 'N/A')
                tp = position.get('take_profit_price', 'N/A')
                print(f"    Current: ${current_price:.2f} | Entry: ${entry} | TP: ${tp}")
        
        if actions_taken > 0:
            print(f"[{datetime.now()}] Actions taken: {actions_taken}")
        
        return 0
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
