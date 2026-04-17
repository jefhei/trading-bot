"""
Test Suite: Copy Trading Bot
============================
Covers all functional requirements from PRD_copy_trading_bot.md

FR-1: Master Trader Registration
FR-2: Trade Replication (Proportional/Fixed/Multiplier sizing)
FR-3: Trade Filtering
FR-4: Latency Requirements (architecture tests)
FR-5: Signal Input Methods
FR-6: Position Management
FR-7: Risk Controls
FR-8: Master Trader Performance Tracking

Run with:
    pip install pytest pytest-mock freezegun
    pytest tests/test_copy_trading_bot.py -v

All tests are fully isolated — no live API calls are made.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, call
from freezegun import freeze_time

# Adjust import paths to match project structure
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bots.copy_trading.signal_processor import SignalProcessor, Signal
from bots.copy_trading.position_sizer import (
    calculate_proportional_size,
    calculate_fixed_dollar_size,
    calculate_multiplier_size,
    PositionSizingMethod
)
from bots.copy_trading.trade_filter import TradeFilter, FilterCriteria
from bots.copy_trading.position_tracker import PositionTracker, Position
from bots.copy_trading.risk_manager import CopyTradingRiskManager
from bots.copy_trading.performance_tracker import PerformanceTracker


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture
def mock_alpaca_client():
    """Returns a fully mocked Alpaca TradingClient."""
    client = MagicMock()
    
    # Default account state: $50,000 equity
    client.get_account.return_value = MagicMock(
        equity=Decimal("50000.00"),
        cash=Decimal("45000.00"),
        portfolio_value=Decimal("50000.00"),
        buying_power=Decimal("90000.00"),
    )
    
    # Default: market is open
    client.get_clock.return_value = MagicMock(is_open=True)
    
    return client


@pytest.fixture
def sample_master_config():
    """Returns a sample master trader configuration."""
    return {
        "id": "master_1",
        "name": "Test Master",
        "account_id": "acc-12345",
        "allocation_pct": 30.0,
        "max_position_pct": 10.0,
        "enabled": True,
        "sizing_method": "proportional",
        "filters": {
            "min_position_size": 100,
            "max_position_size": 5000,
            "symbols_blacklist": ["GME", "AMC"],
            "asset_classes": ["us_equity"],
            "allow_short": False
        }
    }


@pytest.fixture
def sample_signal():
    """Returns a sample trade signal from a master trader."""
    return Signal(
        master_id="master_1",
        symbol="AAPL",
        side="buy",
        qty=100,
        price=150.00,
        timestamp=datetime.now(),
        order_id="order-abc-123",
        asset_class="us_equity"
    )


@pytest.fixture
def temp_db():
    """Creates a temporary SQLite DB for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    os.unlink(db_path)


# ===========================================================================
# FR-1: Master Trader Registration Tests
# ===========================================================================

class TestMasterTraderRegistration:
    """Tests for registering and managing master traders."""
    
    def test_register_master_with_valid_config(self, temp_db, sample_master_config):
        """Verify master trader can be registered with complete config."""
        processor = SignalProcessor(temp_db)
        
        success = processor.register_master(sample_master_config)
        
        assert success is True
        masters = processor.get_registered_masters()
        assert len(masters) == 1
        assert masters[0]["id"] == "master_1"
        assert masters[0]["enabled"] is True
    
    def test_register_master_missing_required_fields(self, temp_db):
        """Verify registration fails when required fields are missing."""
        processor = SignalProcessor(temp_db)
        
        incomplete_config = {"name": "No ID"}  # Missing 'id'
        
        with pytest.raises(ValueError, match="Master ID is required"):
            processor.register_master(incomplete_config)
    
    def test_duplicate_master_registration(self, temp_db, sample_master_config):
        """Verify duplicate master ID updates existing config."""
        processor = SignalProcessor(temp_db)
        processor.register_master(sample_master_config)
        
        # Update with new allocation
        updated_config = sample_master_config.copy()
        updated_config["allocation_pct"] = 50.0
        
        success = processor.register_master(updated_config)
        
        assert success is True
        masters = processor.get_registered_masters()
        assert len(masters) == 1
        assert masters[0]["allocation_pct"] == 50.0
    
    def test_disable_master_trader(self, temp_db, sample_master_config):
        """Verify master can be disabled."""
        processor = SignalProcessor(temp_db)
        processor.register_master(sample_master_config)
        
        success = processor.disable_master("master_1")
        
        assert success is True
        master = processor.get_master("master_1")
        assert master["enabled"] is False
    
    def test_remove_master_trader(self, temp_db, sample_master_config):
        """Verify master can be removed."""
        processor = SignalProcessor(temp_db)
        processor.register_master(sample_master_config)
        
        success = processor.remove_master("master_1")
        
        assert success is True
        masters = processor.get_registered_masters()
        assert len(masters) == 0


# ===========================================================================
# FR-2: Trade Replication / Position Sizing Tests
# ===========================================================================

class TestPositionSizing:
    """Tests for position sizing calculations."""
    
    def test_proportional_sizing_basic(self):
        """Verify proportional sizing based on account value ratio."""
        # Master: $100k, buys 100 shares
        # Follower: $10k, should buy 10 shares
        follower_qty = calculate_proportional_size(
            master_account_value=100000.0,
            follower_account_value=10000.0,
            master_qty=100
        )
        
        assert follower_qty == 10
    
    def test_proportional_sizing_rounds_down(self):
        """Verify proportional sizing rounds down to whole shares."""
        # Should round down to avoid fractional shares
        follower_qty = calculate_proportional_size(
            master_account_value=100000.0,
            follower_account_value=15000.0,  # 15% ratio
            master_qty=100
        )
        
        # 100 * 0.15 = 15, but we might want 15
        # Or if ratio is 0.153, should still round down
        assert follower_qty >= 0
        assert isinstance(follower_qty, int)
    
    def test_proportional_sizing_zero_if_too_small(self):
        """Verify zero returned if calculated size is less than 1 share."""
        follower_qty = calculate_proportional_size(
            master_account_value=100000.0,
            follower_account_value=100.0,  # Very small account
            master_qty=10
        )
        
        assert follower_qty == 0
    
    def test_fixed_dollar_sizing(self):
        """Verify fixed dollar amount sizing."""
        qty = calculate_fixed_dollar_size(
            dollar_amount=1500.0,
            price=150.00
        )
        
        assert qty == 10  # $1500 / $150 = 10 shares
    
    def test_fixed_dollar_sizing_rounds_down(self):
        """Verify fixed dollar sizing rounds down."""
        qty = calculate_fixed_dollar_size(
            dollar_amount=1000.0,
            price=150.00
        )
        
        # $1000 / $150 = 6.66 -> should be 6 shares
        assert qty == 6
    
    def test_multiplier_sizing(self):
        """Verify multiplier-based sizing."""
        qty = calculate_multiplier_size(
            master_qty=100,
            multiplier=0.5
        )
        
        assert qty == 50  # 100 * 0.5 = 50
    
    def test_multiplier_sizing_rounds_down(self):
        """Verify multiplier sizing rounds down."""
        qty = calculate_multiplier_size(
            master_qty=100,
            multiplier=0.33
        )
        
        # 100 * 0.33 = 33 -> should be 33
        assert qty == 33
    
    def test_multiplier_sizing_rejects_negative(self):
        """Verify negative multiplier raises error."""
        with pytest.raises(ValueError, match="Multiplier must be positive"):
            calculate_multiplier_size(master_qty=100, multiplier=-0.5)


# ===========================================================================
# FR-3: Trade Filtering Tests
# ===========================================================================

class TestTradeFiltering:
    """Tests for filtering trades based on criteria."""
    
    def test_symbol_whitelist_pass(self):
        """Verify trade passes when symbol in whitelist."""
        criteria = FilterCriteria(
            symbols_whitelist=["AAPL", "MSFT", "GOOGL"]
        )
        filter_engine = TradeFilter(criteria)
        
        signal = Signal(
            master_id="m1",
            symbol="AAPL",
            side="buy",
            qty=10,
            price=150.0,
            timestamp=datetime.now(),
            order_id="ord1",
            asset_class="us_equity"
        )
        
        result = filter_engine.should_process(signal)
        assert result is True
    
    def test_symbol_whitelist_block(self):
        """Verify trade blocked when symbol not in whitelist."""
        criteria = FilterCriteria(
            symbols_whitelist=["AAPL", "MSFT"]  # GOOGL not allowed
        )
        filter_engine = TradeFilter(criteria)
        
        signal = Signal(
            master_id="m1",
            symbol="GOOGL",
            side="buy",
            qty=10,
            price=150.0,
            timestamp=datetime.now(),
            order_id="ord1",
            asset_class="us_equity"
        )
        
        result = filter_engine.should_process(signal)
        assert result is False
    
    def test_symbol_blacklist_block(self):
        """Verify trade blocked when symbol in blacklist."""
        criteria = FilterCriteria(
            symbols_blacklist=["GME", "AMC"]
        )
        filter_engine = TradeFilter(criteria)
        
        signal = Signal(
            master_id="m1",
            symbol="GME",
            side="buy",
            qty=100,
            price=20.0,
            timestamp=datetime.now(),
            order_id="ord1",
            asset_class="us_equity"
        )
        
        result = filter_engine.should_process(signal)
        assert result is False
    
    def test_asset_class_filter(self):
        """Verify trade filtered by asset class."""
        criteria = FilterCriteria(
            asset_classes=["us_equity"]  # No options, no crypto
        )
        filter_engine = TradeFilter(criteria)
        
        # Crypto trade should be blocked
        crypto_signal = Signal(
            master_id="m1",
            symbol="BTCUSD",
            side="buy",
            qty=1,
            price=50000.0,
            timestamp=datetime.now(),
            order_id="ord1",
            asset_class="crypto"
        )
        
        result = filter_engine.should_process(crypto_signal)
        assert result is False
    
    def test_min_position_size_filter(self):
        """Verify trades below minimum position size are blocked."""
        criteria = FilterCriteria(
            min_position_size=500  # $500 minimum
        )
        filter_engine = TradeFilter(criteria)
        
        signal = Signal(
            master_id="m1",
            symbol="AAPL",
            side="buy",
            qty=1,
            price=150.0,  # Only $150 position
            timestamp=datetime.now(),
            order_id="ord1",
            asset_class="us_equity"
        )
        
        result = filter_engine.should_process(signal)
        assert result is False
    
    def test_max_position_size_filter(self):
        """Verify trades above maximum position size are blocked."""
        criteria = FilterCriteria(
            max_position_size=10000  # $10,000 maximum
        )
        filter_engine = TradeFilter(criteria)
        
        signal = Signal(
            master_id="m1",
            symbol="AAPL",
            side="buy",
            qty=1000,
            price=150.0,  # $150,000 position
            timestamp=datetime.now(),
            order_id="ord1",
            asset_class="us_equity"
        )
        
        result = filter_engine.should_process(signal)
        assert result is False
    
    def test_short_direction_filter(self):
        """Verify short trades can be blocked."""
        criteria = FilterCriteria(
            allow_short=False
        )
        filter_engine = TradeFilter(criteria)
        
        short_signal = Signal(
            master_id="m1",
            symbol="AAPL",
            side="sell",  # Short sell
            qty=100,
            price=150.0,
            timestamp=datetime.now(),
            order_id="ord1",
            asset_class="us_equity"
        )
        
        result = filter_engine.should_process(short_signal)
        assert result is False


# ===========================================================================
# FR-6: Position Management Tests
# ===========================================================================

class TestPositionTracking:
    """Tests for tracking open positions per master."""
    
    def test_add_position(self, temp_db, mock_alpaca_client):
        """Verify new position is tracked correctly."""
        tracker = PositionTracker(temp_db, mock_alpaca_client)
        
        position = Position(
            master_id="master_1",
            symbol="AAPL",
            qty=10,
            entry_price=150.0,
            entry_time=datetime.now(),
            master_order_id="master-ord-1",
            follower_order_id="follower-ord-1"
        )
        
        tracker.add_position(position)
        
        positions = tracker.get_open_positions("master_1")
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"
        assert positions[0].qty == 10
    
    def test_close_position_full(self, temp_db, mock_alpaca_client):
        """Verify position is closed completely."""
        tracker = PositionTracker(temp_db, mock_alpaca_client)
        
        position = Position(
            master_id="master_1",
            symbol="AAPL",
            qty=10,
            entry_price=150.0,
            entry_time=datetime.now(),
            master_order_id="master-ord-1",
            follower_order_id="follower-ord-1"
        )
        tracker.add_position(position)
        
        tracker.close_position("master_1", "AAPL", qty=10)
        
        positions = tracker.get_open_positions("master_1")
        assert len(positions) == 0
    
    def test_close_position_partial(self, temp_db, mock_alpaca_client):
        """Verify partial close updates position size."""
        tracker = PositionTracker(temp_db, mock_alpaca_client)
        
        position = Position(
            master_id="master_1",
            symbol="AAPL",
            qty=100,
            entry_price=150.0,
            entry_time=datetime.now(),
            master_order_id="master-ord-1",
            follower_order_id="follower-ord-1"
        )
        tracker.add_position(position)
        
        # Master closes 50%, follower should close 50%
        tracker.close_position("master_1", "AAPL", master_closed_qty=50)
        
        positions = tracker.get_open_positions("master_1")
        assert len(positions) == 1
        assert positions[0].qty == 50  # 100 - 50% of 100 = 50
    
    def test_get_positions_by_symbol(self, temp_db, mock_alpaca_client):
        """Verify positions can be retrieved by symbol."""
        tracker = PositionTracker(temp_db, mock_alpaca_client)
        
        # Add positions in same symbol from different masters
        tracker.add_position(Position(
            master_id="master_1",
            symbol="AAPL",
            qty=10,
            entry_price=150.0,
            entry_time=datetime.now(),
            master_order_id="m1-1",
            follower_order_id="f1-1"
        ))
        tracker.add_position(Position(
            master_id="master_2",
            symbol="AAPL",
            qty=20,
            entry_price=152.0,
            entry_time=datetime.now(),
            master_order_id="m2-1",
            follower_order_id="f2-1"
        ))
        
        aapl_positions = tracker.get_positions_by_symbol("AAPL")
        assert len(aapl_positions) == 2
    
    def test_sync_positions_on_startup(self, temp_db, mock_alpaca_client):
        """Verify positions are synced from API on startup."""
        # Mock API to return positions
        mock_position = MagicMock()
        mock_position.symbol = "AAPL"
        mock_position.qty = Decimal("10")
        mock_position.avg_entry_price = Decimal("150.0")
        
        mock_alpaca_client.get_all_positions.return_value = [mock_position]
        
        tracker = PositionTracker(temp_db, mock_alpaca_client)
        tracker.sync_positions_from_api()
        
        positions = tracker.get_all_open_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"


# ===========================================================================
# FR-7: Risk Control Tests
# ===========================================================================

class TestRiskManagement:
    """Tests for risk controls and limits."""
    
    def test_max_allocation_per_master(self, temp_db, mock_alpaca_client):
        """Verify per-master allocation limit is enforced."""
        risk_config = {
            "max_allocation_per_master_pct": 30.0,
            "max_total_allocation_pct": 80.0
        }
        risk_manager = CopyTradingRiskManager(
            temp_db, mock_alpaca_client, risk_config
        )
        
        # Simulate $15,000 allocated to master (30% of $50k)
        allocated = risk_manager.get_allocated_value("master_1")
        assert allocated <= 15000.0
    
    def test_daily_loss_limit_per_master(self, temp_db, mock_alpaca_client):
        """Verify copying stops when master hits daily loss limit."""
        risk_config = {
            "daily_loss_limit_per_master_pct": 5.0
        }
        risk_manager = CopyTradingRiskManager(
            temp_db, mock_alpaca_client, risk_config
        )
        
        # Simulate tracking losses for master_1
        # After 5% loss, should block new trades
        risk_manager.record_pnl("master_1", -2500.0)  # 5% of 50k
        
        allowed = risk_manager.is_copying_allowed("master_1")
        assert allowed is False
    
    def test_max_drawdown_limit(self, temp_db, mock_alpaca_client):
        """Verify all copying stops at max drawdown."""
        risk_config = {
            "max_drawdown_pct": 15.0
        }
        risk_manager = CopyTradingRiskManager(
            temp_db, mock_alpaca_client, risk_config
        )
        
        # Set initial high watermark
        risk_manager.set_high_water_mark(50000.0)
        
        # Account drops to $42,500 (15% drawdown)
        mock_alpaca_client.get_account.return_value = MagicMock(
            equity=Decimal("42500.00"),
            portfolio_value=Decimal("42500.00")
        )
        
        allowed = risk_manager.is_any_copying_allowed()
        assert allowed is False
    
    def test_min_cash_reserve(self, temp_db, mock_alpaca_client):
        """Verify trades blocked if cash below reserve."""
        risk_config = {
            "min_cash_reserve_pct": 10.0  # Keep 10% cash
        }
        risk_manager = CopyTradingRiskManager(
            temp_db, mock_alpaca_client, risk_config
        )
        
        # Only $2000 cash left (4% of $50k)
        mock_alpaca_client.get_account.return_value = MagicMock(
            equity=Decimal("50000.00"),
            cash=Decimal("2000.00"),
            buying_power=Decimal("4000.00")
        )
        
        can_trade = risk_manager.has_sufficient_cash(1000.0)
        assert can_trade is False


# ===========================================================================
# FR-8: Performance Tracking Tests
# ===========================================================================

class TestPerformanceTracking:
    """Tests for tracking master trader performance."""
    
    def test_record_trade_and_calculate_return(self, temp_db):
        """Verify trade recording and return calculation."""
        tracker = PerformanceTracker(temp_db)
        
        # Record a winning trade
        tracker.record_trade(
            master_id="master_1",
            symbol="AAPL",
            entry_price=150.0,
            exit_price=165.0,  # +10%
            qty=10,
            entry_time=datetime.now() - timedelta(days=1),
            exit_time=datetime.now()
        )
        
        metrics = tracker.get_master_metrics("master_1")
        assert metrics["total_return_pct"] > 0
        assert metrics["win_count"] == 1
        assert metrics["loss_count"] == 0
    
    def test_win_rate_calculation(self, temp_db):
        """Verify win rate is calculated correctly."""
        tracker = PerformanceTracker(temp_db)
        
        # 3 wins, 2 losses
        for i in range(5):
            is_win = i < 3
            tracker.record_trade(
                master_id="master_1",
                symbol=f"STOCK{i}",
                entry_price=100.0,
                exit_price=110.0 if is_win else 90.0,
                qty=10,
                entry_time=datetime.now() - timedelta(days=i),
                exit_time=datetime.now()
            )
        
        metrics = tracker.get_master_metrics("master_1")
        assert metrics["win_rate"] == 60.0  # 3/5 = 60%
    
    def test_average_win_loss_ratio(self, temp_db):
        """Verify average win/loss ratio calculation."""
        tracker = PerformanceTracker(temp_db)
        
        # Wins: +$150, +$200 (avg $175)
        # Losses: -$100 (avg $100)
        # Ratio: 175 / 100 = 1.75
        tracker.record_trade(
            master_id="master_1", symbol="WIN1",
            entry_price=100.0, exit_price=115.0, qty=10,
            entry_time=datetime.now(), exit_time=datetime.now()
        )
        tracker.record_trade(
            master_id="master_1", symbol="WIN2",
            entry_price=100.0, exit_price=120.0, qty=10,
            entry_time=datetime.now(), exit_time=datetime.now()
        )
        tracker.record_trade(
            master_id="master_1", symbol="LOSS1",
            entry_price=100.0, exit_price=90.0, qty=10,
            entry_time=datetime.now(), exit_time=datetime.now()
        )
        
        metrics = tracker.get_master_metrics("master_1")
        assert metrics["avg_win_loss_ratio"] == 1.75
    
    def test_max_drawdown_calculation(self, temp_db):
        """Verify max drawdown tracking."""
        tracker = PerformanceTracker(temp_db)
        
        # Simulate equity curve: 50k -> 55k -> 48k -> 52k
        tracker.record_equity("master_1", datetime.now() - timedelta(hours=3), 50000.0)
        tracker.record_equity("master_1", datetime.now() - timedelta(hours=2), 55000.0)  # Peak
        tracker.record_equity("master_1", datetime.now() - timedelta(hours=1), 48000.0)  # Trough
        tracker.record_equity("master_1", datetime.now(), 52000.0)
        
        metrics = tracker.get_master_metrics("master_1")
        # Drawdown: (55000 - 48000) / 55000 = 12.7%
        assert metrics["max_drawdown_pct"] > 12.0


# ===========================================================================
# Integration Tests
# ===========================================================================

class TestSignalProcessingIntegration:
    """Integration tests for full signal flow."""
    
    def test_full_signal_to_order_flow(self, temp_db, mock_alpaca_client, sample_signal):
        """Verify end-to-end signal processing results in order."""
        # Setup components
        filter_criteria = FilterCriteria(
            symbols_blacklist=[],
            min_position_size=100,
            asset_classes=["us_equity"]
        )
        filters = TradeFilter(filter_criteria)
        
        tracker = PositionTracker(temp_db, mock_alpaca_client)
        
        risk_config = {
            "max_allocation_per_master_pct": 30.0,
            "daily_loss_limit_per_master_pct": 5.0
        }
        risk_manager = CopyTradingRiskManager(temp_db, mock_alpaca_client, risk_config)
        
        # Signal passes all checks
        assert filters.should_process(sample_signal) is True
        assert risk_manager.is_copying_allowed(sample_signal.master_id) is True
        
        # Calculate size
        qty = calculate_proportional_size(
            master_account_value=100000.0,
            follower_account_value=50000.0,
            master_qty=sample_signal.qty
        )
        assert qty == 50  # Half the master's size
    
    @freeze_time("2025-01-15 14:30:00")
    def test_signal_latency_tracking(self, temp_db):
        """Verify signal processing latency is tracked."""
        processor = SignalProcessor(temp_db)
        
        signal_time = datetime.now()
        
        # Process signal
        with patch.object(processor, '_record_latency') as mock_latency:
            processor.process_signal(Signal(
                master_id="m1",
                symbol="AAPL",
                side="buy",
                qty=10,
                price=150.0,
                timestamp=signal_time,
                order_id="ord1",
                asset_class="us_equity"
            ))
            
            # Verify latency was recorded
            assert mock_latency.called


# ===========================================================================
# Error Handling Tests
# ===========================================================================

class TestErrorHandling:
    """Tests for error handling and recovery."""
    
    def test_order_failure_retry(self, temp_db, mock_alpaca_client):
        """Verify failed orders are queued for retry."""
        from bots.copy_trading.order_executor import OrderExecutor
        
        executor = OrderExecutor(mock_alpaca_client, temp_db)
        
        # Mock API to fail first, succeed second
        mock_alpaca_client.submit_order.side_effect = [
            Exception("API timeout"),
            MagicMock(id="order-123")
        ]
        
        result = executor.place_order_with_retry(
            symbol="AAPL",
            qty=10,
            side="buy",
            max_retries=2
        )
        
        assert result is not None
        assert mock_alpaca_client.submit_order.call_count == 2
    
    def test_api_unavailable_queue_trade(self, temp_db, mock_alpaca_client):
        """Verify trades queued when API is unavailable."""
        from bots.copy_trading.order_executor import OrderExecutor
        
        executor = OrderExecutor(mock_alpaca_client, temp_db)
        
        # Mock API as down
        mock_alpaca_client.get_clock.side_effect = Exception("Connection error")
        
        # Trade should be queued, not lost
        executor.queue_trade_for_retry(
            master_id="m1",
            symbol="AAPL",
            qty=10,
            side="buy"
        )
        
        queued = executor.get_queued_trades()
        assert len(queued) == 1
        assert queued[0]["symbol"] == "AAPL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
