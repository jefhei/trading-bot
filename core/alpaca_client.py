"""
Authenticated Alpaca client wrapper.
Centralizes API authentication and client creation.
"""
import time
import os
import logging
from typing import Optional
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class AlpacaClientError(Exception):
    """Custom exception for Alpaca client errors."""
    pass


class RetryableError(AlpacaClientError):
    """Error that may be resolved by retrying (rate limit, timeout, 5xx)."""
    pass


class NonRetryableError(AlpacaClientError):
    """Error that should not be retried (auth failure, bad request, etc)."""
    pass


def _is_retryable(error: Exception) -> bool:
    """Determine if an exception is retryable."""
    if isinstance(error, RetryableError):
        return True
    if isinstance(error, NonRetryableError):
        return False
    # Our own client errors are not retryable by default
    if isinstance(error, AlpacaClientError):
        return False
    if isinstance(error, APIError):
        status_code = getattr(error, "status_code", None)
        return status_code == 429 or (status_code and 500 <= status_code < 600)
    # Connection errors, timeouts
    err_str = str(error).lower()
    return any(kw in err_str for kw in ["connection", "timeout", "timed out", "unavailable"])


def _retry_on_exception(func, max_retries: int = 3, base_delay: float = 1.0):
    """
    Retry a function call with exponential backoff for transient errors.

    Args:
        func: Callable to retry
        max_retries: Maximum retry attempts (default 3)
        base_delay: Base delay in seconds (doubles each retry)

    Returns:
        Result from func()

    Raises:
        Last exception if max retries exhausted or error is non-retryable
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_error = e
            if not _is_retryable(e) or attempt == max_retries:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"API call failed (attempt {attempt + 1}/{max_retries + 1}): "
                f"{e}. Retrying in {delay}s..."
            )
            time.sleep(delay)
    raise last_error  # Should not reach here


def get_trading_client_retry(paper: bool = None, max_retries: int = 3) -> TradingClient:
    """
    Get authenticated trading client with retry on transient errors.

    Wraps get_trading_client() with exponential backoff for network failures
    during the initial connection test.

    Returns:
        TradingClient: Authenticated client ready for API calls.

    Raises:
        AlpacaClientError: If credentials are missing or authentication fails.
    """
    return _retry_on_exception(lambda: get_trading_client(paper), max_retries=max_retries)


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
        client: Optional existing TradingClient. If None, creates one with retry.

    Returns:
        bool: True if market is open, False otherwise.

    Raises:
        AlpacaClientError: If unable to check market status after retries.
    """
    if client is None:
        try:
            client = get_trading_client_retry()
        except AlpacaClientError:
            raise  # Already wrapped
        except Exception as e:
            raise AlpacaClientError(f"Failed to create trading client: {e}")

    def _check_clock():
        try:
            clock = client.get_clock()
            return clock.is_open
        except APIError as e:
            logger.error(f"Alpaca API error checking market hours: {e}")
            raise AlpacaClientError(f"Failed to check market hours: {e}")
        except Exception as e:
            raise AlpacaClientError(f"Unexpected error checking market status: {e}")

    try:
        return _retry_on_exception(_check_clock, max_retries=2, base_delay=0.5)
    except AlpacaClientError:
        raise
    except Exception as e:
        raise AlpacaClientError(f"Failed to check market status after retries: {e}")
