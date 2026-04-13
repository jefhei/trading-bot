"""
Authenticated Alpaca client wrapper.
Centralizes API authentication and client creation.
"""
import os
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi

# Load environment variables
load_dotenv()


def get_api(paper: bool = None):
    """
    Get an authenticated Alpaca API client.
    
    Args:
        paper: If True, use paper trading. If None, use .env setting.
    
    Returns:
        tradeapi.REST: Authenticated API client ready for API calls.
    """
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    
    if not api_key or not secret_key:
        raise ValueError(
            "Missing Alpaca credentials. Please set ALPACA_API_KEY and "
            "ALPACA_SECRET_KEY in your .env file."
        )
    
    if paper is None:
        paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"
    
    base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    
    return tradeapi.REST(api_key, secret_key, base_url, api_version="v2")


def is_market_open(api=None) -> bool:
    """
    Check if the market is currently open.
    
    Args:
        api: Optional existing API client. If None, creates a new one.
    
    Returns:
        bool: True if market is open, False otherwise.
    """
    if api is None:
        api = get_api()
    
    clock = api.get_clock()
    return clock.is_open
