#!/usr/bin/python3
"""
Market Hours Safety Wrapper
Ensures scripts only run when market is open.

Usage:
    python scripts/with_market_check.py scripts/monitor_positions.py

Cron:
    */5 9-16 * * 1-5 cd /home/jeff/Projects/alpaca && python scripts/with_market_check.py scripts/monitor_positions.py >> logs/cron.log 2>&1
"""
import sys
import subprocess
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.alpaca_client import is_market_open


def main():
    if len(sys.argv) < 2:
        print("Usage: with_market_check.py <script_to_run> [args...]")
        print("Example: python with_market_check.py scripts/monitor_positions.py")
        sys.exit(1)
    
    # Check market hours
    try:
        if not is_market_open():
            print("Market closed. Skipping script execution.")
            sys.exit(0)
    except Exception as e:
        print(f"Error checking market status: {e}")
        # Fail safe - don't run if we can't verify market status
        print("Unable to verify market status. Skipping for safety.")
        sys.exit(0)
    
    # Run the actual script
    script_path = sys.argv[1]
    script_args = sys.argv[2:]
    
    try:
        result = subprocess.run(
            [sys.executable, script_path] + script_args,
            capture_output=False,  # Let output flow through
            text=True
        )
        sys.exit(result.returncode)
    except Exception as e:
        print(f"Error running script {script_path}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
