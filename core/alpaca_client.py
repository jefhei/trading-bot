"""
Authenticated Alpaca client wrapper.
Centralizes API authentication and client creation.
"""
import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

# Load environment variables
load_dotenv()


def get_trading_client(paper: bool = None) -> TradingClient:
    """
    Get an authenticated Alpaca TradingClient.

    Args:
        paper: If True, use paper trading. If None, use .env setting.

    Returns:
        TradingClient: Authenticated client ready for API calls.
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

    return TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)


def is_market_open(client: TradingClient = None) -> bool:
    """
    Check if the market is currently open.

    Args:
        client: Optional existing TradingClient. If None, creates a new one.

    Returns:
        bool: True if market is open, False otherwise.
    """
    if client is None:
        client = get_trading_client()

    clock = client.get_clock()
    return clock.is_open
