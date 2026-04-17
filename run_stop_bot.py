#!/usr/bin/env python3
"""
Stop Strategy Bot - Main Runner
Executes bracket orders with stop-loss and take-profit legs.

Usage:
    python run_stop_bot.py --symbol AAPL --entry 150.00 --stop-pct 5.0 --tp-pct 10.0
    python run_stop_bot.py --symbol TSLA --entry 250.00 --risk-reward 2.0
    python run_stop_bot.py --symbol NVDA --entry 120.00 --qty 10 --trailing 3.0
"""
import argparse
import sys
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent))

from core.alpaca_client import get_trading_client, is_market_open
from bots.stop_strategy.order_placer import place_bracket_order, place_trailing_stop_order
from bots.stop_strategy.config_loader import load_config
from bots.stop_strategy.risk_manager import RiskManager


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Execute stop strategy trades with bracket orders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic bracket order (5% stop, 10% take-profit)
    python run_stop_bot.py --symbol AAPL --entry 150.00

    # Custom percentages
    python run_stop_bot.py --symbol TSLA --entry 250.00 --stop-pct 3.0 --tp-pct 9.0

    # Risk/reward ratio (overrides take-profit)
    python run_stop_bot.py --symbol NVDA --entry 120.00 --risk-reward 2.5

    # Trailing stop instead of fixed stop
    python run_stop_bot.py --symbol AMD --entry 150.00 --trailing 3.0

    # Specific quantity (otherwise calculated from risk)
    python run_stop_bot.py --symbol MSFT --entry 400.00 --qty 5
        """
    )

    parser.add_argument("--symbol", "-s", required=True, help="Stock symbol (e.g., AAPL)")
    parser.add_argument("--entry", "-e", type=float, required=True, help="Entry price")
    parser.add_argument("--qty", "-q", type=int, default=None, help="Quantity (auto-calculated if not provided)")
    parser.add_argument("--stop-pct", type=float, default=None, help="Stop-loss percentage below entry")
    parser.add_argument("--tp-pct", type=float, default=None, help="Take-profit percentage above entry")
    parser.add_argument("--trailing", "-t", type=float, default=None, help="Use trailing stop with this percentage")
    parser.add_argument("--risk-reward", "-rr", type=float, default=None, help="Risk/reward ratio (e.g., 2.0 for 2:1)")
    parser.add_argument("--config", "-c", default="config/settings.yaml", help="Path to config file")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be done without executing")
    parser.add_argument("--yes", "-y", action="store_true", help="Auto-confirm order (skip prompts)")

    return parser.parse_args()


def validate_config(config: dict) -> None:
    """
    Validate stop strategy configuration values.
    
    Args:
        config: The loaded configuration dictionary
        
    Raises:
        SystemExit: If configuration is invalid with error message
    """
    required_keys = ["default_stop_loss_pct", "daily_loss_limit_pct", "risk_per_trade_pct", "max_position_size_pct"]
    
    for key in required_keys:
        if key not in config:
            print(f"❌ Config error: Missing required key 'stop_strategy.{key}'")
            sys.exit(1)
    
    stop_pct = config.get("default_stop_loss_pct", 5.0)
    if not 0.1 <= stop_pct <= 50.0:
        print(f"❌ Config error: default_stop_loss_pct must be between 0.1% and 50% (got {stop_pct}%)")
        sys.exit(1)
    
    risk_pct = config.get("risk_per_trade_pct", 2.0)
    if not 0.1 <= risk_pct <= 10.0:
        print(f"❌ Config error: risk_per_trade_pct must be between 0.1% and 10% (got {risk_pct}%)")
        sys.exit(1)
    
    loss_limit = config.get("daily_loss_limit_pct", 5.0)
    if not 1.0 <= loss_limit <= 50.0:
        print(f"❌ Config error: daily_loss_limit_pct must be between 1% and 50% (got {loss_limit}%)")
        sys.exit(1)
    
    if stop_pct > loss_limit:
        print(f"⚠️  Warning: default_stop_loss_pct ({stop_pct}%) exceeds daily_loss_limit_pct ({loss_limit}%)")
        print("    This means a single loss could halt trading for the day.")


def main():
    args = parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
        stop_config = config["stop_strategy"]
    except Exception as e:
        print(f"❌ Failed to load config: {e}")
        sys.exit(1)
    
    # Validate configuration
    validate_config(stop_config)

    # Get trading client
    try:
        client = get_trading_client()
    except Exception as e:
        print(f"❌ Failed to connect to Alpaca: {e}")
        sys.exit(1)

    # Check market hours
    try:
        clock = client.get_clock()
    except Exception as e:
        print(f"❌ Failed to get market clock: {e}")
        sys.exit(1)

    print(f"Market status: {'OPEN' if clock.is_open else 'CLOSED'}")
    print(f"Next open: {clock.next_open}")
    print(f"Next close: {clock.next_close}")

    if not clock.is_open:
        print("\n⚠️  Market is closed. Orders can only be placed during market hours.")
        if args.yes:
            print("   (--yes flag set: proceeding anyway)")
        else:
            response = input("Submit order anyway? It will be queued for market open. (y/N): ")
            if response.lower() != 'y':
                print("Order cancelled.")
                return

    # Check risk limits
    try:
        risk_manager = RiskManager(client, stop_config)
        if risk_manager.is_daily_loss_limit_breached():
            print("\n❌ Trading halted: Daily loss limit reached.")
            return
    except Exception as e:
        print(f"\n❌ Risk check failed: {e}")
        print("   Trading halted as precaution.")
        return

    # Set default values from config if not provided
    stop_pct = args.stop_pct or stop_config["default_stop_loss_pct"]
    tp_pct = args.tp_pct or stop_config["default_take_profit_pct"]
    risk_reward = args.risk_reward or stop_config.get("risk_reward_ratio")

    # Determine order parameters
    symbol = args.symbol.upper()
    entry_price = args.entry
    qty = args.qty

    if qty is None:
        # Calculate position size from account risk
        account = client.get_account()
        account_value = float(account.equity)
        risk_pct = stop_config.get("risk_per_trade_pct", 2.0) / 100

        stop_price = entry_price * (1 - stop_pct / 100)
        stop_distance = entry_price - stop_price

        from bots.stop_strategy.position_sizer import calculate_position_size
        qty = calculate_position_size(
            account_value=account_value,
            risk_pct=risk_pct,
            entry_price=entry_price,
            stop_price=stop_price
        )

        print(f"\n📊 Position sizing:")
        print(f"   Account value: ${account_value:,.2f}")
        print(f"   Risk per trade: {risk_pct*100:.1f}%")
        print(f"   Calculated quantity: {qty} shares")

    print(f"\n🎯 Order Summary:")
    print(f"   Symbol: {symbol}")
    print(f"   Entry: ${entry_price:.2f}")
    print(f"   Quantity: {qty} shares")
    print(f"   Stop-loss: {stop_pct:.1f}% (${entry_price * (1 - stop_pct/100):.2f})")

    if args.trailing:
        print(f"   Stop type: Trailing ({args.trailing:.1f}%)")
    else:
        if risk_reward:
            stop_distance = entry_price * stop_pct / 100
            tp_price = entry_price + (stop_distance * risk_reward)
            print(f"   Take-profit: {risk_reward:.1f}:1 R:R (${tp_price:.2f})")
        else:
            print(f"   Take-profit: {tp_pct:.1f}% (${entry_price * (1 + tp_pct/100):.2f})")

    if args.dry_run:
        print("\n📝 DRY RUN - No order submitted")
        return

    # Confirm execution
    print(f"\nExecuting trade with paper account...")
    if args.yes:
        print("   (--yes flag set: auto-confirming)")
    else:
        response = input("Proceed? (y/N): ")
        if response.lower() != 'y':
            print("Order cancelled.")
            return

    # Place the order
    try:
        if args.trailing:
            result = place_trailing_stop_order(
                client=client,
                symbol=symbol,
                qty=qty,
                trail_percent=args.trailing
            )
        else:
            result = place_bracket_order(
                client=client,
                symbol=symbol,
                qty=qty,
                entry_price=entry_price,
                stop_loss_pct=stop_pct,
                take_profit_pct=tp_pct,
                stop_type="fixed",
                risk_reward_ratio=risk_reward
            )

        print(f"\n✅ Order submitted successfully!")
        print(f"   Order ID: {result['id']}")
        print(f"   Status: {result['status']}")

    except Exception as e:
        print(f"\n❌ Order failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
