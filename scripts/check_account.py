#!/usr/bin/python3
"""
Morning Account Check Script
Run before market open to verify account status and catch overnight issues.

Usage:
    python scripts/check_account.py

Cron:
    15 9 * * 1-5 cd /home/jeff/Projects/alpaca && python scripts/check_account.py >> logs/cron_account.log 2>&1
"""
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.alpaca_client import get_trading_client


def main():
    try:
        client = get_trading_client()
        account = client.get_account()
        clock = client.get_clock()
        
        now = datetime.now()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        buying_power = float(account.buying_power)
        daily_pnl_pct = ((equity - last_equity) / last_equity * 100) if last_equity > 0 else 0
        
        # Build status report
        lines = [
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}]",
            f"Market opens at: {clock.next_open}",
            f"Market closes at: {clock.next_close}",
            f"Market status: {'OPEN' if clock.is_open else 'CLOSED'}",
            f"Equity: ${equity:,.2f}",
            f"Last equity: ${last_equity:,.2f}",
            f"Daily P&L: {daily_pnl_pct:+.2f}%",
            f"Buying power: ${buying_power:,.2f}",
        ]
        
        # Alert thresholds
        alerts = []
        
        if daily_pnl_pct < -5:
            alerts.append(f"⚠️ ALERT: Equity down >5% from yesterday! ({daily_pnl_pct:+.2f}%)")
        
        if buying_power < equity * 0.1:
            alerts.append(f"⚠️ ALERT: Buying power below 10% of equity!")
        
        if equity < 95000:  # Paper starts at $100k
            alerts.append(f"⚠️ ALERT: Equity below $95,000!")
        
        # Print report
        for line in lines:
            print(line)
        
        if alerts:
            print("\n" + "\n".join(alerts))
            return 1  # Exit with warning status
        else:
            print("\n✅ Account status: OK")
            return 0
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
