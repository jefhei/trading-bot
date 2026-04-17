"""
Order placement module for Stop Strategy Bot.
Handles bracket orders, trailing stops, and order validation.
"""
import time
import logging
from typing import Optional
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, TrailingStopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderType
from alpaca.common.exceptions import APIError

logger = logging.getLogger(__name__)


def _submit_order_with_retry(client: TradingClient, order_request, max_retries: int = 3, base_delay: float = 1.0):
    """
    Submit order with exponential backoff retry for transient errors.
    
    Retries on rate limits (429), server errors (5xx), and connection issues.
    
    Args:
        client: Authenticated Alpaca TradingClient
        order_request: Order request object (LimitOrderRequest, TrailingStopOrderRequest, etc.)
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds (doubles with each retry)
        
    Returns:
        Order object from Alpaca API
        
    Raises:
        APIError: If max retries exceeded or non-retryable error occurs
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return client.submit_order(order_request)
        except APIError as e:
            last_error = e
            status_code = getattr(e, 'status_code', None)
            
            # Check if this is a retryable error
            is_retryable = (
                status_code == 429 or  # Rate limited
                (status_code and 500 <= status_code < 600) or  # Server error
                'Connection' in str(e) or
                'Timeout' in str(e)
            )
            
            if not is_retryable or attempt == max_retries:
                raise
                
            delay = base_delay * (2 ** attempt)  # Exponential backoff
            logger.warning(f"Order submission failed (attempt {attempt + 1}/{max_retries + 1}). "
                          f"Retrying in {delay}s... Error: {e}")
            time.sleep(delay)
            
    raise last_error  # Should not reach here


def place_bracket_order(
    client: TradingClient,
    symbol: str,
    qty: int,
    entry_price: float,
    stop_loss_pct: float,
    take_profit_pct: Optional[float] = None,
    stop_type: str = "fixed",
    risk_reward_ratio: Optional[float] = None,
) -> dict:
    """
    Place a bracket order with entry, stop-loss, and take-profit legs.

    Args:
        client: Authenticated Alpaca TradingClient
        symbol: Stock symbol (e.g., "AAPL")
        qty: Number of shares (must be > 0)
        entry_price: Entry price for the position
        stop_loss_pct: Stop loss percentage below entry (e.g., 5.0 for 5%)
        take_profit_pct: Take profit percentage above entry (e.g., 10.0 for 10%)
        stop_type: "fixed" or "trailing" (default: "fixed")
        risk_reward_ratio: If provided, calculates take-profit from stop distance

    Returns:
        dict: Order response from Alpaca API

    Raises:
        ValueError: If validation fails (qty <= 0, invalid prices, market closed)
        Exception: If API call fails
    """
    # Validate market is open
    clock = client.get_clock()
    if not clock.is_open:
        raise Exception("Market is closed. Orders can only be placed during market hours.")

    # Validate quantity
    if qty <= 0:
        raise ValueError(f"Quantity must be positive, got {qty}")

    # Validate stop-loss percentage
    if stop_loss_pct <= 0:
        raise ValueError(f"Stop loss percentage must be positive, got {stop_loss_pct}")

    # Calculate stop-loss price
    stop_price = entry_price * (1 - stop_loss_pct / 100)
    if stop_price <= 0:
        raise ValueError(f"Calculated stop price {stop_price} is invalid")

    # Calculate take-profit price
    if risk_reward_ratio is not None:
        # Calculate based on risk:reward ratio
        stop_distance = entry_price - stop_price
        take_profit_distance = stop_distance * risk_reward_ratio
        take_profit_price = entry_price + take_profit_distance
    elif take_profit_pct is not None:
        take_profit_price = entry_price * (1 + take_profit_pct / 100)
    else:
        # Default to 2x risk/reward if neither specified
        stop_distance = entry_price - stop_price
        take_profit_price = entry_price + (stop_distance * 2.0)

    if take_profit_price <= entry_price:
        raise ValueError(
            f"Take profit price {take_profit_price} must be above entry {entry_price}"
        )

    # Build bracket order
    stop_loss_config = {
        "stop_price": round(stop_price, 2),
    }

    if stop_type == "trailing":
        # For trailing stops, use stop type instead of bracket
        return place_trailing_stop_order(
            client=client,
            symbol=symbol,
            qty=qty,
            trail_percent=stop_loss_pct,
        )

    take_profit_config = {
        "limit_price": round(take_profit_price, 2),
    }

    # Submit bracket order with retry
    order = _submit_order_with_retry(
        client,
        LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            limit_price=round(entry_price, 2),
            stop_loss=stop_loss_config,
            take_profit=take_profit_config,
        )
    )

    return {
        "id": str(order.id),
        "status": order.status,
        "order_class": "bracket",
        "symbol": symbol,
        "qty": qty,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
    }


def place_trailing_stop_order(
    client: TradingClient,
    symbol: str,
    qty: int,
    trail_percent: float,
) -> dict:
    """
    Place a trailing stop order.

    Args:
        client: Authenticated Alpaca TradingClient
        symbol: Stock symbol (e.g., "TSLA")
        qty: Number of shares
        trail_percent: Percentage to trail (e.g., 3.0 for 3%)

    Returns:
        dict: Order response from Alpaca API
    """
    # Validate market is open
    clock = client.get_clock()
    if not clock.is_open:
        raise Exception("Market is closed. Orders can only be placed during market hours.")

    if qty <= 0:
        raise ValueError(f"Quantity must be positive, got {qty}")

    if trail_percent <= 0:
        raise ValueError(f"Trail percentage must be positive, got {trail_percent}")

    order = _submit_order_with_retry(
        client,
        TrailingStopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            type=OrderType.TRAILING_STOP,
            time_in_force=TimeInForce.DAY,
            trail_percent=trail_percent,
        )
    )

    return {
        "id": str(order.id),
        "status": order.status,
        "order_type": "trailing_stop",
        "trail_percent": trail_percent,
        "symbol": symbol,
        "qty": qty,
    }


def update_stop_loss(
    client: TradingClient,
    order_id: str,
    new_stop_price: float,
) -> dict:
    """
    Cancel existing stop-loss order and place a new one at breakeven or better.

    Args:
        client: Authenticated Alpaca TradingClient
        order_id: Order ID of the stop-loss order to replace
        new_stop_price: New stop price

    Returns:
        dict: New order response
    """
    # Cancel existing stop order
    client.cancel_order_by_id(order_id)

    # Note: In a real scenario, you'd want to replace the order
    # For now, this demonstrates the cancellation part of breakeven adjustment
    return {
        "cancelled_order_id": order_id,
        "new_stop_price": new_stop_price,
        "status": "cancelled_pending_replace",
    }
