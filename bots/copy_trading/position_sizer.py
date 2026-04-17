"""
Position sizing calculations for copy trading.
Supports proportional, fixed dollar, and multiplier methods.
"""
from enum import Enum
from dataclasses import dataclass


class PositionSizingMethod(Enum):
    """Enumeration of position sizing methods."""
    PROPORTIONAL = "proportional"
    FIXED = "fixed"
    MULTIPLIER = "multiplier"


@dataclass
class SizingConfig:
    """Configuration for position sizing."""
    method: PositionSizingMethod
    fixed_amount: float = None  # For fixed method
    multiplier: float = 1.0     # For multiplier method


def calculate_proportional_size(
    master_account_value: float,
    follower_account_value: float,
    master_qty: int,
    min_qty: int = 1
) -> int:
    """
    Calculate follower position size based on account value ratio.

    Formula: follower_qty = master_qty * (follower_value / master_value)

    Args:
        master_account_value: Master's total account value
        follower_account_value: Follower's total account value
        master_qty: Master's position quantity
        min_qty: Minimum quantity (default 1)

    Returns:
        int: Calculated quantity (rounded down, minimum min_qty or 0)

    Raises:
        ValueError: If account values are not positive
    """
    if master_account_value <= 0:
        raise ValueError(f"Master account value must be positive, got {master_account_value}")

    if follower_account_value <= 0:
        raise ValueError(f"Follower account value must be positive, got {follower_account_value}")

    if master_qty <= 0:
        return 0

    # Calculate ratio
    ratio = follower_account_value / master_account_value

    # Calculate follower quantity
    follower_qty = master_qty * ratio

    # Round down to whole shares
    follower_qty_int = int(follower_qty)

    # Return 0 if below minimum, otherwise return calculated quantity
    if follower_qty_int < min_qty:
        return 0

    return follower_qty_int


def calculate_fixed_dollar_size(
    dollar_amount: float,
    price: float,
    min_qty: int = 1
) -> int:
    """
    Calculate position size based on fixed dollar amount.

    Formula: qty = dollar_amount / price

    Args:
        dollar_amount: Dollar amount to invest
        price: Current price per share
        min_qty: Minimum quantity (default 1)

    Returns:
        int: Calculated quantity (rounded down)

    Raises:
        ValueError: If price is not positive or dollar_amount is negative
    """
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")

    if dollar_amount < 0:
        raise ValueError(f"Dollar amount must be non-negative, got {dollar_amount}")

    if dollar_amount == 0:
        return 0

    qty = dollar_amount / price
    qty_int = int(qty)

    if qty_int < min_qty:
        return 0

    return qty_int


def calculate_multiplier_size(
    master_qty: int,
    multiplier: float,
    min_qty: int = 1
) -> int:
    """
    Calculate position size based on multiplier of master quantity.

    Formula: qty = master_qty * multiplier

    Args:
        master_qty: Master's position quantity
        multiplier: Multiplier to apply (e.g., 0.5 for half size, 2.0 for double)
        min_qty: Minimum quantity (default 1)

    Returns:
        int: Calculated quantity (rounded down)

    Raises:
        ValueError: If multiplier is not positive
    """
    if multiplier <= 0:
        raise ValueError(f"Multiplier must be positive, got {multiplier}")

    if master_qty <= 0:
        return 0

    qty = master_qty * multiplier
    qty_int = int(qty)

    if qty_int < min_qty:
        return 0

    return qty_int


def calculate_position_size(
    sizing_config: SizingConfig,
    master_account_value: float,
    follower_account_value: float,
    master_qty: int,
    price: float
) -> int:
    """
    Calculate position size based on sizing configuration.

    Args:
        sizing_config: Sizing configuration
        master_account_value: Master's account value
        follower_account_value: Follower's account value
        master_qty: Master's trade quantity
        price: Current price per share

    Returns:
        int: Calculated quantity for follower
    """
    if sizing_config.method == PositionSizingMethod.PROPORTIONAL:
        return calculate_proportional_size(
            master_account_value, follower_account_value, master_qty
        )
    elif sizing_config.method == PositionSizingMethod.FIXED:
        return calculate_fixed_dollar_size(sizing_config.fixed_amount, price)
    elif sizing_config.method == PositionSizingMethod.MULTIPLIER:
        return calculate_multiplier_size(master_qty, sizing_config.multiplier)
    else:
        raise ValueError(f"Unknown sizing method: {sizing_config.method}")
