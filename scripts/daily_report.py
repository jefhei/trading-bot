#!/usr/bin/python3
"""
Daily Report Generator
Creates end-of-day trading summary with P&L and order statistics.

Usage:
    python scripts/daily_report.py

Cron:
    5 16 * * 1-5 cd /home/jeff/Projects/alpaca && python scripts/daily_report.py >> logs/reports.log 2>&1
"""
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.alpaca_client import get_trading_client


def ensure_reports_dir():
    """Create reports directory if it doesn't exist."""
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    return reports_dir


def get_orders_today(db_path="data/orders.db"):
    """Get all orders placed today from database."""
    if not Path(db_path).exists():
        return []
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    cursor.execute(
        "SELECT * FROM orders WHERE DATE(timestamp) = ?",
        (today,)
    )
    
    orders = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return orders


def main():
    try:
        # Initialize
        reports_dir = ensure_reports_dir()
        client = get_trading_client()
        account = client.get_account()
        
        # Get account values
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        buying_power = float(account.buying_power)
        cash = float(account.cash)
        
        # Calculate metrics
        daily_pnl = equity - last_equity
        daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity > 0 else 0
        
        # Get today's orders
        orders = get_orders_today()
        filled_orders = [o for o in orders if o.get('state') == 'FILLED']
        closed_orders = [o for o in orders if o.get('state') == 'CLOSED']
        pending_orders = [o for o in orders if o.get('state') == 'PENDING']
        
        # Build report
        date_str = datetime.now().strftime("%Y-%m-%d")
        report = {
            "date": date_str,
            "generated_at": datetime.now().isoformat(),
            "account": {
                "equity": round(equity, 2),
                "last_equity": round(last_equity, 2),
                "cash": round(cash, 2),
                "buying_power": round(buying_power, 2),
                "daily_pnl": round(daily_pnl, 2),
                "daily_pnl_pct": round(daily_pnl_pct, 2),
            },
            "orders": {
                "total_placed": len(orders),
                "filled": len(filled_orders),
                "closed": len(closed_orders),
                "pending": len(pending_orders),
            },
            "market": {
                "was_open": datetime.now().weekday() < 5,  # Mon-Fri
            }
        }
        
        # Save JSON report
        json_path = reports_dir / f"{date_str}.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2)
        
        # Print summary
        print(f"[{datetime.now()}]")
        print(f"=" * 50)
        print(f"Daily Report: {date_str}")
        print(f"=" * 50)
        print(f"Account Equity: ${equity:,.2f}")
        print(f"Daily P&L: ${daily_pnl:+,.2f} ({daily_pnl_pct:+.2f}%)")
        print(f"Cash: ${cash:,.2f}")
        print(f"Buying Power: ${buying_power:,.2f}")
        print(f"-" * 50)
        print(f"Orders Placed Today: {len(orders)}")
        print(f"  Filled: {len(filled_orders)}")
        print(f"  Closed: {len(closed_orders)}")
        print(f"  Pending: {len(pending_orders)}")
        print(f"=" * 50)
        print(f"Report saved: {json_path}")
        
        # Alert on significant losses
        if daily_pnl_pct < -5:
            print(f"\n⚠️  ALERT: Daily loss exceeded 5%!")
            return 1
        
        return 0
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
