"""
Authenticated Alpaca client wrapper.
Centralizes API authentication and client creation.
"""
import os
import logging
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class AlpacaClientError(Exception):
    """Custom exception for Alpaca client errors."""
    pass


def get_trading_client(paper: bool = None) -> TradingClient:
    """
    Get an authenticated Alpaca TradingClient.

    Args:
        paper: If True, use paper trading. If None, use .env setting.

    Returns:
        TradingClient: Authenticated client ready for API calls.

    Raises:
        AlpacaClientError: If credentials are missing or authentication fails.
    """
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        raise AlpacaClientError(
            "Missing Alpaca credentials. Please set ALPACA_API_KEY and "
            "ALPACA_SECRET_KEY in your .env file."
        )

    if paper is None:
        paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    try:
        client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
        # Test the connection by fetching account
        client.get_account()
        logger.info(f"Successfully authenticated to Alpaca ({'paper' if paper else 'live'})")
        return client
    except APIError as e:
        logger.error(f"Alpaca API error during authentication: {e}")
        raise AlpacaClientError(f"Failed to authenticate with Alpaca: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during Alpaca authentication: {e}")
        raise AlpacaClientError(f"Unexpected error connecting to Alpaca: {e}")


def is_market_open(client: TradingClient = None) -> bool:
    """
    Check if the market is currently open.

    Args:
        client: Optional existing TradingClient. If None, creates a new one.

    Returns:
        bool: True if market is open, False otherwise.

    Raises:
        AlpacaClientError: If unable to check market status.
    """
    if client is None:
        client = get_trading_client()

    try:
        clock = client.get_clock()
        return clock.is_open
    except APIError as e:
        logger.error(f"Alpaca API error checking market hours: {e}")
        raise AlpacaClientError(f"Failed to check market hours: {e}")
    except Exception as e:
        logger.error(f"Unexpected error checking market hours: {e}")
        raise AlpacaClientError(f"Unexpected error checking market status: {e}")
