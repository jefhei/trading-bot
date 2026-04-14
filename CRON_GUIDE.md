# Using Cron Safely with the Trading Bot

**Purpose:** Guidelines and examples for safe cron job usage with the Alpaca Trading Bot.
**Last Updated:** April 2026

---

## ⚠️ The Golden Rule

> **Never use cron for real-time order execution or entry decisions.**
> 
> Cron jobs are for **housekeeping, reporting, and monitoring** — not active trading.
> 
> Active trading requires continuous state awareness. A cron job that runs every 5 minutes can miss critical events (order fills, stop triggers, breakeven levels) that happen between invocations.

---

## Safe vs. Unsafe Cron Usage

| ✅ Safe | ❌ Unsafe |
|--------|----------|
| Daily account status reports | Entry/exit decision making |
| Position monitoring with state persistence | Submitting new orders on a timer |
| Log rotation and cleanup | Stop-loss adjustment without state checks |
| Pre-market preparation tasks | High-frequency order placement |
| End-of-day trade summaries | Moment-to-moment risk checking |

---

## Working with State

The bot uses SQLite for state persistence. Cron jobs **must**:

1. **Load state** from the database on startup
2. **Check last known state** before acting
3. **Save state** before exiting
4. **Handle failures gracefully** — don't leave state inconsistent

### Example: Safe Position Monitor
```python
# monitor_positions.py - Safe for cron
from bots.stop_strategy.db import get_open_positions
from bots.stop_strategy.order_monitor import OrderMonitor
from core.alpaca_client import get_trading_client

def main():
    client = get_trading_client()
    monitor = OrderMonitor(client, db_path="data/orders.db")
    
    # 1. Load state from DB (critical!)
    monitor.load_state_from_db()
    
    # 2. Check current positions
    positions = get_open_positions("data/orders.db")
    
    # 3. Check breakeven conditions for each
    for position in positions:
        if monitor.get_state(position['order_id']) == 'FILLED':
            # Get current price (API call)
            # Check if 50% of TP distance reached
            # Update stop if needed
            pass
    
    # 4. State automatically saved by OrderMonitor

if __name__ == "__main__":
    main()
```

---

## Recommended Cron Jobs

### 1. Morning Account Check (Pre-Market)
```cron
# Run at 9:15 AM ET, Monday-Friday
15 9 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 check_account.py >> logs/cron_account.log 2>&1
```

**Purpose:** Verify account status before market opens, catch any overnight issues.

**check_account.py:**
```python
#!/usr/bin/env python3
from core.alpaca_client import get_trading_client
from datetime import datetime

def main():
    client = get_trading_client()
    account = client.get_account()
    clock = client.get_clock()
    
    print(f"[{datetime.now()}]")
    print(f"Market opens at: {clock.next_open}")
    print(f"Equity: ${float(account.equity):,.2f}")
    print(f"Buying power: ${float(account.buying_power):,.2f}")
    print(f"Daily P&L: {((float(account.equity) - float(account.last_equity)) / float(account.last_equity) * 100):+.2f}%")
    
    # Alert if equity dropped significantly overnight
    if float(account.equity) < float(account.last_equity) * 0.95:
        print("⚠️ ALERT: Equity down >5% from yesterday!")

if __name__ == "__main__":
    main()
```

---

### 2. Position Monitoring (Market Hours Only)
```cron
# Every 5 minutes from 9:35 AM to 3:55 PM ET
35-59/5 9 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 monitor_positions.py >> logs/cron_monitor.log 2>&1
*/5 10-15 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 monitor_positions.py >> logs/cron_monitor.log 2>&1
0-55/5 9 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 monitor_positions.py >> logs/cron_monitor.log 2>&1
```

**Purpose:** Check breakeven conditions, log position status.

**Key:** Must load state from DB, never assume clean slate.

---

### 3. Market Hours Safety Wrapper
```cron
# Run trading logic only if market open
*/5 10-15 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 with_market_check.py monitor_positions.py >> logs/cron.log 2>&1
```

**with_market_check.py:**
```python
#!/usr/bin/env python3
"""Wrapper to ensure scripts only run during market hours."""
import sys
import subprocess
from core.alpaca_client import is_market_open

def main():
    if not is_market_open():
        print("Market closed. Skipping.")
        sys.exit(0)
    
    # Run the actual script
    script = sys.argv[1]
    subprocess.run([sys.executable, script])

if __name__ == "__main__":
    main()
```

---

### 4. End-of-Day Report
```cron
# Run at 4:05 PM ET (5 min after close)
5 16 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 daily_report.py >> logs/reports.log 2>&1
```

**daily_report.py:**
```python
#!/usr/bin/env python3
from core.alpaca_client import get_trading_client
from bots.stop_strategy.db import get_orders_today
from datetime import datetime
import json

def main():
    client = get_trading_client()
    account = client.get_account()
    orders = get_orders_today()
    
    report = {
        "date": datetime.now().isoformat(),
        "equity": float(account.equity),
        "daily_pnl_pct": ((float(account.equity) - float(account.last_equity)) / float(account.last_equity) * 100),
        "orders_placed": len(orders),
        "orders_filled": len([o for o in orders if o['status'] == 'filled']),
    }
    
    with open(f"reports/{datetime.now().strftime('%Y-%m-%d')}.json", "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"Report saved: {report}")

if __name__ == "__main__":
    main()
```

---

### 5. Log Rotation (Weekly)
```cron
# Sundays at midnight
0 0 * * 0 cd /home/jeff/Projects/alpaca && /usr/bin/python3 rotate_logs.py
```

**rotate_logs.py:**
```python
#!/usr/bin/env python3
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path

def main():
    log_dir = Path("logs")
    
    # Compress logs older than 7 days
    for log_file in log_dir.glob("*.log"):
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        if datetime.now() - mtime > timedelta(days=7):
            with open(log_file, "rb") as f_in:
                with gzip.open(f"{log_file}.gz", "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            log_file.unlink()
            print(f"Compressed: {log_file}")
    
    # Delete compressed logs older than 90 days
    for gz_file in log_dir.glob("*.gz"):
        mtime = datetime.fromtimestamp(gz_file.stat().st_mtime)
        if datetime.now() - mtime > timedelta(days=90):
            gz_file.unlink()
            print(f"Deleted: {gz_file}")

if __name__ == "__main__":
    main()
```

---

## Error Handling Best Practices

### 1. Always Capture Output
```cron
# Redirect stdout and stderr to log files
* * * * * cd /home/jeff/Projects/alpaca && /usr/bin/python3 script.py >> logs/script.log 2>&1
```

### 2. Use Script Exit Codes
```python
import sys

try:
    main()
    sys.exit(0)  # Success
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)  # Failure - cron will log this
```

### 3. Set Up Cron Monitoring
Check cron job status:
```bash
# See recent cron activity
grep CRON /var/log/syslog | tail -20

# Check for specific job failures
grep "exit status 1" /var/log/syslog
```

### 4. Lock Files (Prevent Overlapping Runs)
```python
#!/usr/bin/env python3
import fcntl
import sys

def main():
    # Create lock file
    lock_file = open("/tmp/trading_bot_monitor.lock", "w")
    try:
        fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("Another instance is running. Exiting.")
        sys.exit(0)
    
    # Your code here
    
    # Lock released when script exits

if __name__ == "__main__":
    main()
```

---

## Testing Cron Jobs

Before deploying any cron job:

```bash
# 1. Test the script manually first
python3 check_account.py

# 2. Test with bash (simulates cron environment)
env -i bash -c 'cd /home/jeff/Projects/alpaca && python3 check_account.py'

# 3. Check if command is in crontab
crontab -l | grep check_account

# 4. Monitor first few runs
tail -f /var/log/syslog | grep CRON
tail -f logs/cron_account.log
```

---

## Complete Crontab Example

```bash
# Edit crontab: crontab -e

# Trading Bot Cron Jobs
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
MAILTO="your-email@example.com"

# Morning account check (9:15 AM ET weekdays)
15 9 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 scripts/check_account.py >> logs/cron_account.log 2>&1

# Position monitoring during market hours (every 5 min)
# Only runs if market open (via wrapper)
*/5 9-16 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 scripts/with_market_check.py scripts/monitor_positions.py >> logs/cron_monitor.log 2>&1

# End-of-day report (4:05 PM ET weekdays)
5 16 * * 1-5 cd /home/jeff/Projects/alpaca && /usr/bin/python3 scripts/daily_report.py >> logs/reports.log 2>&1

# Weekly log rotation (Sunday midnight)
0 0 * * 0 cd /home/jeff/Projects/alpaca && /usr/bin/python3 scripts/rotate_logs.py
```

---

## Monitoring Checklist

After setting up cron jobs, verify:

- [ ] Logs are being written to `logs/` directory
- [ ] Log files don't grow unbounded (rotation works)
- [ ] Failed jobs are visible in syslog
- [ ] Scripts handle "market closed" gracefully (no errors)
- [ ] Lock files prevent overlapping runs
- [ ] Scripts exit with code 0 on success, 1 on failure
- [ ] State is always loaded from DB before acting
- [ ] No duplicate orders from overlapping cron runs

---

## Emergency Stop

If a cron job goes wrong:

```bash
# Remove all cron jobs (emergency)
crontab -r

# Or comment out specific lines
crontab -e
# Add # at start of line to disable

# Kill any running Python processes
pkill -f "python3.*trading"

# Check what was running
ps aux | grep python
```

---

## Alternatives to Cron

| Tool | Use Case | Pros | Cons |
|------|----------|------|------|
| **systemd** | Long-running daemon | Handles restarts, logging | Linux only, more complex |
| **APScheduler** | Python-specific scheduling | Integrated with Python code | Requires running process |
| **Celery** | Distributed task queue | Handles retries, state | Overkill for simple bots |
| **Prefect/Airflow** | Data pipelines | Visual monitoring, retries | Heavy dependencies |

For this trading bot: **Cron for reports + systemd for trading daemon** is recommended.

---

## Summary

**Safe cron usage:**
- ✓ Load state from DB on every run
- ✓ Handle market closed gracefully
- ✓ Log everything
- ✓ Use lock files
- ✓ Test before deploying

**Never use cron for:**
- ✗ Entry/exit decisions
- ✗ Real-time order placement
- ✗ High-frequency monitoring
- ✗ Anything requiring immediate reaction

Questions? Review UAT_CHECKLIST.md for testing procedures.
