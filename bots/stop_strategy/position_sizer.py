"""
Position sizing calculator for Stop Strategy Bot.
Determines share quantity based on risk parameters and stop distance.
"""
from typing import List
import statistics


def calculate_position_size(
    account_value: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
) -> int:
    """
    Calculate the number of shares to buy based on risk parameters.

    Formula: shares = (account_value * risk_pct) / (entry_price - stop_price)

    Args:
        account_value: Total account equity
        risk_pct: Percentage of account to risk (e.g., 0.02 for 2%)
        entry_price: Planned entry price
        stop_price: Stop-loss price (must be below entry_price)

    Returns:
        int: Number of shares to buy (floored to whole shares, 0 if too small)

    Raises:
        ValueError: If stop_price >= entry_price, risk_pct <= 0, or invalid inputs
    """
    if risk_pct <= 0:
        raise ValueError(f"Risk percentage must be positive, got {risk_pct}")

    if entry_price <= 0:
        raise ValueError(f"Entry price must be positive, got {entry_price}")

    if stop_price <= 0:
        raise ValueError(f"Stop price must be positive, got {stop_price}")

    if stop_price >= entry_price:
        raise ValueError(
            f"Stop price {stop_price} must be below entry price {entry_price}"
        )

    stop_distance = entry_price - stop_price
    if stop_distance <= 0:
        raise ValueError(f"Stop distance must be positive, got {stop_distance}")

    # Calculate dollar amount to risk
    risk_dollars = account_value * risk_pct

    # Calculate shares: risk_dollars / stop_distance
    shares = risk_dollars / stop_distance

    # Floor to whole shares (never round up - that increases risk)
    shares_int = int(shares)

    # Return 0 for very small positions rather than attempting fractional
    return max(0, shares_int)


def calculate_atr_stop(
    entry_price: float,
    highs: List[float],
    lows: List[float],
    closes: List[float],
    atr_multiplier: float = 1.5,
    period: int = 14,
) -> float:
    """
    Calculate stop-loss price based on Average True Range (ATR).

    Args:
        entry_price: Current entry price
        highs: List of high prices (oldest to newest)
        lows: List of low prices (oldest to newest)
        closes: List of close prices (oldest to newest)
        atr_multiplier: Multiplier for ATR (default: 1.5)
        period: Period for ATR calculation (default: 14)

    Returns:
        float: Calculated stop price based on ATR

    Raises:
        ValueError: If insufficient data provided
    """
    if len(highs) < period or len(lows) < period or len(closes) < period:
        raise ValueError(
            f"Insufficient data for ATR calculation. "
            f"Need {period} periods, got {len(highs)} highs, {len(lows)} lows, {len(closes)} closes"
        )

    # Calculate True Range for each period
    true_ranges = []
    for i in range(1, len(closes)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]

        tr1 = high - low  # Current high - current low
        tr2 = abs(high - prev_close)  # Current high - previous close
        tr3 = abs(low - prev_close)  # Current low - previous close

        true_range = max(tr1, tr2, tr3)
        true_ranges.append(true_range)

    # Need at least 'period' number of true ranges
    if len(true_ranges) < period:
        raise ValueError(
            f"Insufficient data for ATR calculation. "
            f"Need {period} true ranges, got {len(true_ranges)}"
        )

    # Use only the most recent 'period' true ranges
    recent_tr = true_ranges[-period:]

    # Calculate ATR (simple average)
    atr = statistics.mean(recent_tr)

    # Calculate stop price: entry - (ATR * multiplier)
    stop_price = entry_price - (atr * atr_multiplier)

    # Ensure stop price is positive
    return max(0.01, stop_price)


def apply_position_cap(
    shares: int,
    entry_price: float,
    account_value: float,
    max_position_pct: float,
) -> int:
    """
    Cap position size to maximum percentage of account.

    Args:
        shares: Calculated shares from risk formula
        entry_price: Entry price per share
        account_value: Total account equity
        max_position_pct: Maximum position size as % of account (e.g., 10.0 for 10%)

    Returns:
        int: Capped number of shares
    """
    max_position_value = account_value * (max_position_pct / 100)
    max_shares = int(max_position_value / entry_price)

    return min(shares, max_shares)
