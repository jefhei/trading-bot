"""
Tests for order executor retry logic and error paths (TB-038)
===============================================================
Test suite for bots/copy_trading/order_executor.py:
(1) Successful retry after transient failure
(2) Fails after max retries
(3) Non-retryable errors fail immediately
Also covers: retryable error classification, rate limiter, queue operations.

Run with:
    pytest tests/test_copy_trading.py -v

All tests use mocked Alpaca clients — no live API calls.
"""

import pytest
import sqlite3
import tempfile
import os
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, call
from collections import deque

from bots.copy_trading.order_executor import (
    OrderExecutor,
    RateLimiter,
    is_retryable_error,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_temp_db(suffix: str = "executor"):
    fd, path = tempfile.mkstemp(suffix=f"_{suffix}.db")
    os.close(fd)
    return path


def _make_executor(db_path=None, mock_client=None, rate_limiter=None):
    if db_path is None:
        db_path = _make_temp_db()
    if mock_client is None:
        mock_client = MagicMock()
    return OrderExecutor(client=mock_client, db_path=db_path, rate_limiter=rate_limiter)


def _make_api_error(msg, status_code=None):
    """Create a mock APIError-like exception."""
    err = Exception(msg)
    err.code = status_code
    err.status_code = status_code
    return err


class FakeAPIError(Exception):
    """Fake Alpaca APIError for testing is_retryable_error."""
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code
        self.code = status_code


# ============================================================================
# (1) Successful retry after transient failure
# ============================================================================

class TestRetrySuccessAfterTransientFailure:
    """place_order_with_retry should retry on transient errors and eventually succeed."""

    def setup_method(self):
        self.db_path = _make_temp_db("retry1")
        self.client = MagicMock()
        # Use a permissive rate limiter so it never blocks
        self.rl = RateLimiter(max_requests_per_second=1000, max_requests_per_minute=10000)
        self.executor = _make_executor(
            db_path=self.db_path, mock_client=self.client, rate_limiter=self.rl,
        )

    def test_succeeds_on_second_attempt_after_503(self):
        """Server error (503) on first attempt, success on retry."""
        self.client.submit_order.side_effect = [
            FakeAPIError("Internal Server Error 503", 503),
            MagicMock(id="order-abc", status="filled", filled_avg_price=150.00),
        ]
        result = self.executor.place_order_with_retry("AAPL", 50, "BUY")
        assert result is not None
        assert result["order_id"] == "order-abc"
        assert result["status"] == "filled"
        assert self.client.submit_order.call_count == 2

    def test_succeeds_on_third_attempt_after_429(self):
        """Rate limited (429) twice, then succeeds on third try."""
        self.client.submit_order.side_effect = [
            FakeAPIError("Rate limit exceeded 429", 429),
            FakeAPIError("Rate limit exceeded 429", 429),
            MagicMock(id="order-xyz", status="accepted", filled_avg_price=None),
        ]
        result = self.executor.place_order_with_retry("MSFT", 10, "SELL")
        assert result is not None
        assert result["order_id"] == "order-xyz"
        assert self.client.submit_order.call_count == 3

    def test_succeeds_after_timeout(self):
        """Timeout on first two attempts, success on third."""
        self.client.submit_order.side_effect = [
            TimeoutError("Connection timed out"),
            TimeoutError("Connection timed out"),
            MagicMock(id="order-timeout", status="accepted", filled_avg_price=200.0),
        ]
        result = self.executor.place_order_with_retry("TSLA", 100, "BUY", max_retries=3)
        assert result is not None
        assert result["order_id"] == "order-timeout"

    def test_succeeds_immediately_when_no_errors(self):
        """No errors at all — should succeed on first attempt."""
        self.client.submit_order.return_value = MagicMock(
            id="order-ok", status="filled", filled_avg_price=180.0,
        )
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY")
        assert result is not None
        assert result["order_id"] == "order-ok"
        assert self.client.submit_order.call_count == 1

    def test_succeeds_after_connection_error(self):
        """ConnectionError is retryable; succeeds on retry."""
        self.client.submit_order.side_effect = [
            ConnectionError("Connection refused"),
            MagicMock(id="order-conn", status="accepted", filled_avg_price=50.0),
        ]
        result = self.executor.place_order_with_retry("F", 100, "BUY")
        assert result is not None
        assert result["order_id"] == "order-conn"


# ============================================================================
# (2) Fails after max retries
# ============================================================================

class TestFailsAfterMaxRetries:
    """All retries exhausted — should return None."""

    def setup_method(self):
        self.db_path = _make_temp_db("retry2")
        self.client = MagicMock()
        self.rl = RateLimiter(max_requests_per_second=1000, max_requests_per_minute=10000)
        self.executor = _make_executor(
            db_path=self.db_path, mock_client=self.client, rate_limiter=self.rl,
        )

    def test_returns_none_after_max_retries(self):
        """3 attempts all fail with 500 errors → returns None."""
        self.client.submit_order.side_effect = FakeAPIError("Internal Server Error 500", 500)
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY", max_retries=3)
        assert result is None
        assert self.client.submit_order.call_count == 3

    def test_returns_none_with_single_retry(self):
        """max_retries=1 means only 1 attempt."""
        self.client.submit_order.side_effect = FakeAPIError("Server Error 503", 503)
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY", max_retries=1)
        assert result is None
        assert self.client.submit_order.call_count == 1

    def test_fails_after_all_retries_with_timeout(self):
        """3 timeouts in a row → None."""
        self.client.submit_order.side_effect = TimeoutError("Request timed out")
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY", max_retries=3, retry_delay=0.01)
        assert result is None
        assert self.client.submit_order.call_count == 3

    def test_fails_after_oserror(self):
        """OSError (e.g., connection refused) is retryable but eventually fails."""
        self.client.submit_order.side_effect = OSError("Network unreachable")
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY", max_retries=2, retry_delay=0.01)
        assert result is None
        assert self.client.submit_order.call_count == 2


# ============================================================================
# (3) Non-retryable errors fail immediately
# ============================================================================

class TestNonRetryableErrorsFailImmediately:
    """Non-retryable errors should NOT trigger any retry — fail on first attempt."""

    def setup_method(self):
        self.db_path = _make_temp_db("retry3")
        self.client = MagicMock()
        self.rl = RateLimiter(max_requests_per_second=1000, max_requests_per_minute=10000)
        self.executor = _make_executor(
            db_path=self.db_path, mock_client=self.client, rate_limiter=self.rl,
        )

    def test_insufficient_funds_fails_immediately(self):
        err = FakeAPIError("Insufficient buying power", 403)
        self.client.submit_order.side_effect = err
        result = self.executor.place_order_with_retry("AAPL", 1000, "BUY", max_retries=3)
        assert result is None
        assert self.client.submit_order.call_count == 1  # No retries

    def test_unauthorized_fails_immediately(self):
        err = FakeAPIError("Unauthorized 401", 401)
        self.client.submit_order.side_effect = err
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY", max_retries=3)
        assert result is None
        assert self.client.submit_order.call_count == 1

    def test_invalid_symbol_fails_immediately(self):
        err = FakeAPIError("Asset is not active: INVALID_SYMBOL", 404)
        self.client.submit_order.side_effect = err
        result = self.executor.place_order_with_retry("INVALID", 10, "BUY", max_retries=3)
        assert result is None
        assert self.client.submit_order.call_count == 1

    def test_forbidden_fails_immediately(self):
        err = FakeAPIError("Forbidden 403", 403)
        self.client.submit_order.side_effect = err
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY", max_retries=3)
        assert result is None
        assert self.client.submit_order.call_count == 1

    def test_bad_request_fails_immediately(self):
        err = FakeAPIError("Bad request 400", 400)
        self.client.submit_order.side_effect = err
        result = self.executor.place_order_with_retry("AAPL", -5, "BUY", max_retries=3)
        assert result is None
        assert self.client.submit_order.call_count == 1

    def test_unprocessable_fails_immediately(self):
        err = FakeAPIError("Unprocessable 422", 422)
        self.client.submit_order.side_effect = err
        result = self.executor.place_order_with_retry("AAPL", 10, "BUY", max_retries=3)
        assert result is None
        assert self.client.submit_order.call_count == 1


# ============================================================================
# Retryable Error Classification
# ============================================================================

class TestRetryableErrorClassification:
    """is_retryable_error should correctly categorize errors."""

    def test_429_is_retryable(self):
        assert is_retryable_error(Exception("Rate limit 429")) == (True, "rate_limited")

    def test_500_is_retryable(self):
        assert is_retryable_error(Exception("Internal Server Error 500")) == (True, "server_error")

    def test_503_is_retryable(self):
        assert is_retryable_error(Exception("Service Unavailable 503")) == (True, "server_error")

    def test_504_is_retryable(self):
        assert is_retryable_error(Exception("Gateway Timeout 504")) == (True, "server_error")

    def test_timeout_is_retryable(self):
        assert is_retryable_error(TimeoutError("Connection timed out")) == (True, "timeout")

    def test_connection_error_is_retryable(self):
        assert is_retryable_error(ConnectionError("Refused")) == (True, "connection_error")

    def test_oserror_is_retryable(self):
        assert is_retryable_error(OSError("Network unreachable")) == (True, "connection_error")

    def test_401_is_not_retryable(self):
        assert is_retryable_error(Exception("Unauthorized 401")) == (False, "unauthorized")

    def test_403_is_not_retryable(self):
        assert is_retryable_error(Exception("Forbidden 403")) == (False, "forbidden")

    def test_insufficient_funds_not_retryable(self):
        assert is_retryable_error(Exception("Insufficient buying power")) == (False, "insufficient_funds")

    def test_invalid_symbol_not_retryable(self):
        assert is_retryable_error(Exception("Asset is not active")) == (False, "invalid_symbol")

    def test_400_bad_request_not_retryable(self):
        assert is_retryable_error(Exception("Bad request 400")) == (False, "bad_request")

    def test_422_unprocessable_not_retryable(self):
        assert is_retryable_error(Exception("Unprocessable 422")) == (False, "unprocessable")

    def test_unknown_error_is_retryable(self):
        """Unknown errors should be treated as retryable to be safe."""
        result = is_retryable_error(Exception("Some weird error"))
        assert result[0] is True
        assert result[1] == "unknown"

    def test_api_error_500_retryable(self):
        """FakeAPIError doesn't match real alpaca APIError class, but unknown is still retryable."""
        result = is_retryable_error(FakeAPIError("Error", 500))
        assert result[0] is True  # Still retryable (treated as unknown)

    def test_api_error_429_retryable(self):
        result = is_retryable_error(FakeAPIError("Rate limit", 429))
        assert result[0] is True


# ============================================================================
# Rate Limiter
# ============================================================================

class TestRateLimiter:
    """Test token bucket rate limiter behavior."""

    def test_allows_requests_under_limit(self):
        rl = RateLimiter(max_requests_per_second=5, max_requests_per_minute=100)
        for _ in range(5):
            assert rl.acquire() is True

    def test_blocks_when_per_second_exceeded(self):
        rl = RateLimiter(max_requests_per_second=1, max_requests_per_minute=100)
        assert rl.acquire() is True
        # Second request should block (timeout quickly)
        result = rl.acquire(timeout=0.2)
        assert result is False

    def test_stats_return_correct_format(self):
        rl = RateLimiter(max_requests_per_second=5, max_requests_per_minute=100)
        stats = rl.get_stats()
        assert "requests_last_second" in stats
        assert "requests_last_minute" in stats
        assert "max_per_second" in stats
        assert "max_per_minute" in stats
        assert "utilization_pct" in stats

    def test_stats_reflect_requests(self):
        rl = RateLimiter(max_requests_per_second=100, max_requests_per_minute=1000)
        for _ in range(3):
            rl.acquire()
        stats = rl.get_stats()
        assert stats["requests_last_second"] == 3
        assert stats["requests_last_minute"] == 3
        assert stats["max_per_second"] == 100
        assert stats["max_per_minute"] == 1000


# ============================================================================
# Failed Order Tracking
# ============================================================================

class TestFailedOrderTracking:

    def setup_method(self):
        self.db_path = _make_temp_db("tracking")
        self.client = MagicMock()
        self.rl = RateLimiter(max_requests_per_second=1000, max_requests_per_minute=10000)
        self.executor = _make_executor(
            db_path=self.db_path, mock_client=self.client, rate_limiter=self.rl,
        )

    def test_failed_order_recorded_on_max_retries(self):
        self.client.submit_order.side_effect = FakeAPIError("Server Error 503", 503)
        self.executor.place_follower_order("master1", "AAPL", 10, "BUY")

        conn = sqlite3.connect(self.db_path)
        cur = conn.execute("SELECT master_id, symbol, qty, side, error_message FROM copy_failed_orders")
        rows = cur.fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "master1"
        assert rows[0][1] == "AAPL"
        assert rows[0][2] == 10
        assert rows[0][3] == "BUY"
        assert rows[0][4] == "Max retries exceeded"

    def test_successful_order_not_recorded_as_failed(self):
        self.client.submit_order.return_value = MagicMock(id="order-ok", status="filled", filled_avg_price=100.0)
        self.executor.place_follower_order("master1", "AAPL", 10, "BUY")

        conn = sqlite3.connect(self.db_path)
        cur = conn.execute("SELECT COUNT(*) FROM copy_failed_orders")
        count = cur.fetchone()[0]
        conn.close()

        assert count == 0

    def test_get_failed_orders_returns_all(self):
        self.client.submit_order.side_effect = [
            FakeAPIError("Error 503", 503),
            FakeAPIError("Error 503", 503),
        ]
        self.executor.place_follower_order("master1", "AAPL", 10, "BUY")
        self.executor.place_follower_order("master2", "MSFT", 5, "SELL")

        failed = self.executor.get_failed_orders()
        assert len(failed) == 2

    def test_get_failed_orders_filters_by_master(self):
        self.client.submit_order.side_effect = [
            FakeAPIError("Error 503", 503),
            FakeAPIError("Error 503", 503),
        ]
        self.executor.place_follower_order("master1", "AAPL", 10, "BUY")
        self.executor.place_follower_order("master2", "MSFT", 5, "SELL")

        failed = self.executor.get_failed_orders(master_id="master1")
        assert len(failed) == 1
        assert failed[0]["master_id"] == "master1"


# ============================================================================
# Queued Trade Operations
# ============================================================================

class TestQueuedTradeOperations:

    def setup_method(self):
        self.db_path = _make_temp_db("queue")
        self.client = MagicMock()
        self.rl = RateLimiter(max_requests_per_second=1000, max_requests_per_minute=10000)
        self.executor = _make_executor(
            db_path=self.db_path, mock_client=self.client, rate_limiter=self.rl,
        )

    def test_get_queued_trades_empty(self):
        assert self.executor.get_queued_trades() == []

    def test_queue_trade_and_retrieve(self):
        self.executor.queue_trade_for_retry("master1", "AAPL", 10, "BUY")
        trades = self.executor.get_queued_trades()
        assert len(trades) == 1
        assert trades[0]["master_id"] == "master1"
        assert trades[0]["symbol"] == "AAPL"
        assert trades[0]["qty"] == 10
        assert trades[0]["side"] == "BUY"

    def test_process_queued_trades_success(self):
        self.executor.queue_trade_for_retry("master1", "AAPL", 10, "BUY")
        self.client.submit_order.return_value = MagicMock(id="order-1", status="filled", filled_avg_price=100.0)

        result = self.executor.process_queued_trades()
        assert result["successful"] == 1
        assert result["failed"] == 0
        assert self.executor.get_queued_trades() == []

    def test_process_queued_trades_failure(self):
        self.executor.queue_trade_for_retry("master1", "AAPL", 10, "BUY")
        self.client.submit_order.side_effect = FakeAPIError("Error 503", 503)

        result = self.executor.process_queued_trades()
        assert result["failed"] == 1
        # Trade should still be in queue (retry_count incremented)
        remaining = self.executor.get_queued_trades()
        assert len(remaining) == 1
        assert remaining[0]["retry_count"] == 1

    def test_process_multiple_queued_trades(self):
        self.executor.queue_trade_for_retry("master1", "AAPL", 10, "BUY")
        self.executor.queue_trade_for_retry("master1", "MSFT", 5, "SELL")
        self.client.submit_order.side_effect = [
            MagicMock(id="o1", status="filled", filled_avg_price=100.0),
            MagicMock(id="o2", status="filled", filled_avg_price=380.0),
        ]

        result = self.executor.process_queued_trades()
        assert result["successful"] == 2
        assert result["failed"] == 0
        assert self.executor.get_queued_trades() == []


# ============================================================================
# DB Cleanup
# ============================================================================

class TestDBCleanup:
    def test_temp_db_is_created(self):
        path = _make_temp_db("cleanup")
        assert os.path.exists(path)
        os.unlink(path)
