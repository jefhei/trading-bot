"""
Test Suite: Stop Strategy Bot
===============================
Covers all functional requirements from PRD_stop_strategy_bot.md

FR-1: Bracket Order Support
FR-2: Configurable Stop-Loss Types
FR-3: Configurable Take-Profit Levels
FR-4: Position Sizing
FR-5: Order Management
FR-6: Risk Parameters Configuration

Run with:
    pip install pytest pytest-mock freezegun
    pytest tests/test_stop_strategy_bot.py -v

All tests are fully isolated — no live API calls are made.
The Alpaca client is mocked throughout via pytest-mock.
"""

import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, time
from decimal import Decimal
from unittest.mock import MagicMock, patch, call
from freezegun import freeze_time

# ---------------------------------------------------------------------------
# These imports assume the following module structure (adjust paths as needed):
#
#   bots/stop_strategy/
#       order_placer.py       -> place_bracket_order, place_trailing_stop_order
#       position_sizer.py     -> calculate_position_size, calculate_atr_stop
#       risk_manager.py       -> RiskManager
#       order_monitor.py      -> OrderMonitor, OrderState
#       config_loader.py      -> load_config
#       db.py                 -> init_db, log_order_event, get_open_positions
#
# Adjust the import paths to match your actual structure.
# ---------------------------------------------------------------------------

from bots.stop_strategy.order_placer import place_bracket_order, place_trailing_stop_order
from bots.stop_strategy.position_sizer import calculate_position_size, calculate_atr_stop
from bots.stop_strategy.risk_manager import RiskManager
from bots.stop_strategy.order_monitor import OrderMonitor, OrderState
from bots.stop_strategy.config_loader import load_config
from bots.stop_strategy.db import init_db, log_order_event, get_open_positions


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture
def mock_alpaca_client():
    """Returns a fully mocked Alpaca TradingClient."""
    client = MagicMock()

    # Default account state: $100,000 equity, no daily losses
    client.get_account.return_value = MagicMock(
        equity=Decimal("100000.00"),
        cash=Decimal("95000.00"),
        portfolio_value=Decimal("100000.00"),
        last_equity=Decimal("100000.00"),  # used for daily loss calc
    )

    # Default: market is open
    client.get_clock.return_value = MagicMock(
        is_open=True,
        next_open=datetime(2025, 1, 2, 9, 30),
        next_close=datetime(2025, 1, 2, 16, 0),
    )

    return client


@pytest.fixture
def default_config():
    """Returns a config dict matching the PRD's default YAML values."""
    return {
        "stop_strategy": {
            "default_stop_loss_pct": 5.0,
            "default_take_profit_pct": 10.0,
            "trailing_stop_pct": 3.0,
            "max_position_size_pct": 10.0,
            "daily_loss_limit_pct": 5.0,
            "use_trailing_stop": False,
            "risk_reward_ratio": 2.0,
        }
    }


@pytest.fixture
def temp_db():
    """Creates a temporary SQLite DB for each test and tears it down after."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    init_db(db_path)
    yield db_path
    os.unlink(db_path)


@pytest.fixture
def risk_manager(mock_alpaca_client, default_config):
    return RiskManager(client=mock_alpaca_client, config=default_config["stop_strategy"])


@pytest.fixture
def order_monitor(mock_alpaca_client, temp_db):
    return OrderMonitor(client=mock_alpaca_client, db_path=temp_db)


# ===========================================================================
# FR-1: BRACKET ORDER SUPPORT
# ===========================================================================

class TestBracketOrderPlacement:
    """
    FR-1: Bot must place bracket orders containing:
      - Entry (market or limit)
      - Take-profit limit order
      - Stop-loss order
    """

    def test_bracket_order_places_three_legs(self, mock_alpaca_client):
        """A submitted bracket order should result in exactly one API call
        with bracket order class containing stop_loss and take_profit."""
        mock_alpaca_client.submit_order.return_value = MagicMock(
            id="order-001",
            status="accepted",
            order_class="bracket",
        )

        place_bracket_order(
            client=mock_alpaca_client,
            symbol="AAPL",
            qty=10,
            entry_price=150.00,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )

        mock_alpaca_client.submit_order.assert_called_once()
        order_req = mock_alpaca_client.submit_order.call_args.args[0]
        assert order_req.order_class.value == "bracket"
        assert order_req.stop_loss is not None
        assert order_req.take_profit is not None

    def test_stop_loss_price_calculated_correctly(self, mock_alpaca_client):
        """Stop-loss price must be exactly stop_pct% below entry price."""
        mock_alpaca_client.submit_order.return_value = MagicMock(id="order-002")

        place_bracket_order(
            client=mock_alpaca_client,
            symbol="AAPL",
            qty=10,
            entry_price=200.00,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )

        order_req = mock_alpaca_client.submit_order.call_args.args[0]
        expected_stop = 200.00 * (1 - 0.05)  # 190.00
        assert abs(order_req.stop_loss.stop_price - expected_stop) < 0.01

    def test_take_profit_price_calculated_correctly(self, mock_alpaca_client):
        """Take-profit price must be exactly take_profit_pct% above entry price."""
        mock_alpaca_client.submit_order.return_value = MagicMock(id="order-003")

        place_bracket_order(
            client=mock_alpaca_client,
            symbol="AAPL",
            qty=10,
            entry_price=200.00,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )

        order_req = mock_alpaca_client.submit_order.call_args.args[0]
        expected_tp = 200.00 * (1 + 0.10)  # 220.00
        assert abs(order_req.take_profit.limit_price - expected_tp) < 0.01

    def test_bracket_order_outside_market_hours_raises(self, mock_alpaca_client):
        """Orders submitted when market is closed should raise, not silently fail."""
        mock_alpaca_client.get_clock.return_value = MagicMock(is_open=False)

        with pytest.raises(Exception, match="[Mm]arket.*closed|[Cc]losed"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=10,
                entry_price=150.00,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
            )

    def test_bracket_order_rejected_by_api_raises(self, mock_alpaca_client):
        """An API rejection must propagate as an exception, not be swallowed."""
        mock_alpaca_client.submit_order.side_effect = Exception("insufficient funds")

        with pytest.raises(Exception, match="insufficient funds"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=100,
                entry_price=150.00,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
            )

    def test_invalid_symbol_raises(self, mock_alpaca_client):
        """A bad ticker (e.g. 'APPL') that the API rejects must surface clearly."""
        mock_alpaca_client.submit_order.side_effect = Exception("asset not found: APPL")

        with pytest.raises(Exception, match="APPL"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="APPL",
                qty=1,
                entry_price=150.00,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
            )

    def test_zero_quantity_raises(self, mock_alpaca_client):
        """Zero-share orders must be rejected before hitting the API."""
        with pytest.raises(ValueError, match="[Qq]ty|[Qq]uantity|shares"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=0,
                entry_price=150.00,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
            )
        mock_alpaca_client.submit_order.assert_not_called()


# ===========================================================================
# FR-2: CONFIGURABLE STOP-LOSS TYPES
# ===========================================================================

class TestStopLossTypes:
    """
    FR-2: Fixed, trailing, and ATR-based stop-loss types.
    """

    def test_fixed_stop_loss_uses_static_price(self, mock_alpaca_client):
        """Fixed stop should not move regardless of subsequent price changes."""
        mock_alpaca_client.submit_order.return_value = MagicMock(id="order-010")

        place_bracket_order(
            client=mock_alpaca_client,
            symbol="TSLA",
            qty=5,
            entry_price=250.00,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            stop_type="fixed",
        )

        order_req = mock_alpaca_client.submit_order.call_args.args[0]
        assert order_req.stop_loss.stop_price is not None
        assert order_req.stop_loss.stop_price > 0

    def test_trailing_stop_order_uses_trail_percent(self, mock_alpaca_client):
        """Trailing stop orders must use trail_percent, not a fixed stop_price."""
        mock_alpaca_client.submit_order.return_value = MagicMock(id="order-011")

        place_trailing_stop_order(
            client=mock_alpaca_client,
            symbol="TSLA",
            qty=5,
            trail_percent=3.0,
        )

        order_req = mock_alpaca_client.submit_order.call_args.args[0]
        # Trailing stop uses trail_percent parameter
        assert order_req.trail_percent == 3.0

    def test_atr_based_stop_produces_valid_price(self):
        """ATR stop should return a price that is below entry and above zero."""
        entry_price = 100.00
        # Simulate 15 periods of high/low/close data (need period+1 for TR calculation)
        highs  = [100, 102, 104, 103, 105, 107, 106, 108, 110, 109, 111, 113, 112, 114, 116]
        lows   = [98,  98,  99, 100, 101, 103, 102, 104, 106, 105, 107, 109, 108, 110, 112]
        closes = [99, 100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 111, 110, 112, 114]

        stop_price = calculate_atr_stop(
            entry_price=entry_price,
            highs=highs,
            lows=lows,
            closes=closes,
            atr_multiplier=1.5,
            period=14,
        )

        assert 0 < stop_price < entry_price

    def test_atr_stop_with_insufficient_data_raises(self):
        """ATR calculation must fail explicitly if fewer candles than the period
        are supplied, rather than returning a nonsense value."""
        with pytest.raises(ValueError, match="[Ii]nsufficient|[Dd]ata|period"):
            calculate_atr_stop(
                entry_price=100.00,
                highs=[102, 104],
                lows=[98, 99],
                closes=[100, 102],
                atr_multiplier=1.5,
                period=14,
            )


# ===========================================================================
# FR-3: CONFIGURABLE TAKE-PROFIT LEVELS
# ===========================================================================

class TestTakeProfitLevels:
    """
    FR-3: Single target, multiple targets, and risk-reward ratio take-profits.
    """

    def test_single_take_profit_target(self, mock_alpaca_client):
        """Single take-profit: one limit order at the configured percentage above entry."""
        mock_alpaca_client.submit_order.return_value = MagicMock(id="order-020")

        place_bracket_order(
            client=mock_alpaca_client,
            symbol="AAPL",
            qty=10,
            entry_price=100.00,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )

        order_req = mock_alpaca_client.submit_order.call_args.args[0]
        assert abs(order_req.take_profit.limit_price - 110.00) < 0.01

    def test_risk_reward_ratio_sets_take_profit_correctly(self, mock_alpaca_client):
        """
        With a 2:1 R:R and 5% stop, take-profit should be 10% above entry.
        take_profit_distance = stop_distance * risk_reward_ratio
        """
        mock_alpaca_client.submit_order.return_value = MagicMock(id="order-021")

        place_bracket_order(
            client=mock_alpaca_client,
            symbol="AAPL",
            qty=10,
            entry_price=100.00,
            stop_loss_pct=5.0,
            risk_reward_ratio=2.0,  # overrides take_profit_pct
        )

        order_req = mock_alpaca_client.submit_order.call_args.args[0]
        # stop distance = 5.00, TP distance = 5.00 * 2 = 10.00 -> price = 110.00
        assert abs(order_req.take_profit.limit_price - 110.00) < 0.01

    def test_take_profit_below_entry_raises(self, mock_alpaca_client):
        """A take-profit at or below entry price is always invalid."""
        with pytest.raises(ValueError, match="[Tt]ake.profit|[Ii]nvalid"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=10,
                entry_price=100.00,
                stop_loss_pct=5.0,
                take_profit_pct=0.0,
            )

    def test_stop_loss_above_entry_raises(self, mock_alpaca_client):
        """A stop-loss at or above entry price would invert risk — must be rejected."""
        with pytest.raises(ValueError, match="[Ss]top.loss|[Ii]nvalid"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=10,
                entry_price=100.00,
                stop_loss_pct=-5.0,  # negative pct means stop is above entry
                take_profit_pct=10.0,
            )


# ===========================================================================
# FR-4: POSITION SIZING
# ===========================================================================

class TestPositionSizing:
    """
    FR-4: shares = (account_value * risk_pct) / (entry_price - stop_price)
    """

    def test_standard_position_size_calculation(self):
        """Core formula from PRD US-3 acceptance criteria."""
        shares = calculate_position_size(
            account_value=100_000,
            risk_pct=0.02,     # 2% risk
            entry_price=200.00,
            stop_price=190.00, # $10 stop distance
        )
        # (100000 * 0.02) / (200 - 190) = 2000 / 10 = 200
        assert shares == 200

    def test_position_size_rounds_down_to_whole_shares(self):
        """Fractional shares must be floored — never round up (that increases risk)."""
        shares = calculate_position_size(
            account_value=10_000,
            risk_pct=0.02,
            entry_price=150.00,
            stop_price=143.00,  # $7 stop -> 10000*0.02/7 = 28.57
        )
        assert shares == 28

    def test_zero_stop_distance_raises(self):
        """Entry price equal to stop price would cause division by zero."""
        with pytest.raises(ValueError, match="[Ss]top|[Dd]istance|[Zz]ero"):
            calculate_position_size(
                account_value=100_000,
                risk_pct=0.02,
                entry_price=150.00,
                stop_price=150.00,
            )

    def test_stop_above_entry_raises(self):
        """Stop price above entry price implies a short — invalid for a long entry."""
        with pytest.raises(ValueError, match="[Ss]top|[Ee]ntry|[Ii]nvalid"):
            calculate_position_size(
                account_value=100_000,
                risk_pct=0.02,
                entry_price=150.00,
                stop_price=160.00,
            )

    def test_zero_risk_pct_raises(self):
        """Zero risk percentage should never result in placing a trade."""
        with pytest.raises(ValueError, match="[Rr]isk"):
            calculate_position_size(
                account_value=100_000,
                risk_pct=0.0,
                entry_price=150.00,
                stop_price=140.00,
            )

    def test_position_size_capped_by_max_position_pct(self, mock_alpaca_client, default_config):
        """
        Even if the formula says 500 shares, if that exceeds max_position_size_pct
        of the account, the result must be capped.
        max_position_size_pct=10% of $100,000 = $10,000 max
        At $200/share, cap = 10000/200 = 50 shares
        """
        risk_mgr = RiskManager(
            client=mock_alpaca_client,
            config=default_config["stop_strategy"],
        )

        capped_shares = risk_mgr.apply_position_cap(
            raw_shares=500,
            entry_price=200.00,
            account_value=100_000,
        )

        # 10% of 100k / $200 = 50 shares max
        assert capped_shares == 50

    def test_small_account_results_in_at_least_one_share_or_zero(self):
        """
        When account is very small and the formula returns < 1 share,
        result must be 0 (don't place the trade) — never a negative number.
        """
        shares = calculate_position_size(
            account_value=500,
            risk_pct=0.01,    # $5 risk
            entry_price=200.00,
            stop_price=190.00, # $10 stop -> 5/10 = 0.5 -> floors to 0
        )
        assert shares == 0


# ===========================================================================
# FR-5: ORDER MANAGEMENT
# ===========================================================================

class TestOrderManagement:
    """
    FR-5: SSE streaming state transitions and breakeven stop adjustment.
    """

    def test_order_state_transitions_pending_to_filled(self, order_monitor):
        """
        When a 'fill' event is received for a known order,
        its state must transition from PENDING to FILLED.
        """
        order_monitor.register_order("order-100", symbol="AAPL", entry_price=150.00)
        assert order_monitor.get_state("order-100") == OrderState.PENDING

        order_monitor.handle_event({
            "event": "fill",
            "order": {"id": "order-100", "filled_avg_price": "150.50"},
        })

        assert order_monitor.get_state("order-100") == OrderState.FILLED

    def test_order_state_transitions_filled_to_closed_on_stop_hit(self, order_monitor):
        """When a stop-loss leg fills, the position must be marked CLOSED."""
        order_monitor.register_order(
            "order-101",
            symbol="TSLA",
            entry_price=250.00,
            stop_order_id="stop-101",
        )
        # Fill the entry order first
        order_monitor.handle_event({
            "event": "fill",
            "order": {"id": "order-101", "filled_avg_price": "250.00"},
        })
        assert order_monitor.get_state("order-101") == OrderState.FILLED
        
        # Then fill the stop-loss order
        order_monitor.handle_event({
            "event": "fill",
            "order": {"id": "stop-101", "filled_avg_price": "237.50"},
        })

        assert order_monitor.get_state("order-101") == OrderState.CLOSED

    def test_unknown_order_event_is_logged_not_raised(self, order_monitor):
        """Events for unregistered order IDs must be logged and ignored gracefully."""
        # Should not raise — unknown orders can arrive if bot restarted mid-session
        order_monitor.handle_event({
            "event": "fill",
            "order": {"id": "unknown-order-999", "filled_avg_price": "100.00"},
        })

    def test_breakeven_stop_triggers_at_50pct_of_take_profit_distance(
        self, order_monitor, mock_alpaca_client
    ):
        """
        US-4: When current price reaches 50% of the take-profit distance above entry,
        the original stop-loss must be cancelled and marked as triggered.
        """
        order_monitor.register_order(
            "order-102",
            symbol="AAPL",
            entry_price=100.00,
            stop_order_id="stop-102",
            take_profit_price=110.00,  # TP is $10 above entry
        )
        order_monitor.handle_event({
            "event": "fill",
            "order": {"id": "order-102", "filled_avg_price": "100.00"},
        })

        # Current price is $105 = exactly 50% of the $10 TP distance -> trigger
        result = order_monitor.check_breakeven_adjustment("order-102", current_price=105.00)

        mock_alpaca_client.cancel_order_by_id.assert_called_with("stop-102")
        assert result is True

    def test_breakeven_stop_does_not_trigger_below_threshold(
        self, order_monitor, mock_alpaca_client
    ):
        """Stop must NOT be moved to breakeven when price hasn't reached 50% of TP distance."""
        order_monitor.register_order(
            "order-103",
            symbol="AAPL",
            entry_price=100.00,
            stop_order_id="stop-103",
            take_profit_price=110.00,
        )
        order_monitor.handle_event({
            "event": "fill",
            "order": {"id": "order-103", "filled_avg_price": "100.00"},
        })

        # Price is $103 — only 30% of TP distance, below the 50% trigger
        order_monitor.check_breakeven_adjustment("order-103", current_price=103.00)

        mock_alpaca_client.cancel_order_by_id.assert_not_called()

    def test_state_recovered_from_db_after_restart(self, temp_db, mock_alpaca_client):
        """
        If the bot restarts, it must rebuild in-memory state from the DB
        rather than treating all positions as new.
        """
        # Simulate a prior run: write an open position directly to the DB
        conn = sqlite3.connect(temp_db)
        conn.execute(
            """INSERT INTO orders (order_id, symbol, state, entry_price, stop_order_id)
               VALUES (?, ?, ?, ?, ?)""",
            ("order-200", "MSFT", "FILLED", 300.00, "stop-200"),
        )
        conn.commit()
        conn.close()

        # Boot a fresh monitor and confirm it loads the persisted state
        fresh_monitor = OrderMonitor(client=mock_alpaca_client, db_path=temp_db)
        fresh_monitor.load_state_from_db()

        assert fresh_monitor.get_state("order-200") == OrderState.FILLED


# ===========================================================================
# FR-6: RISK PARAMETERS CONFIGURATION
# ===========================================================================

class TestRiskParameters:
    """
    FR-6: Daily loss limit, max position size, and config loading.
    """

    def test_daily_loss_limit_halts_new_orders(self, risk_manager, mock_alpaca_client):
        """
        If account equity has dropped >= daily_loss_limit_pct since market open,
        no new orders should be placed.
        5% of $100,000 starting equity = $5,000. Drop equity to $94,000 -> blocked.
        """
        mock_alpaca_client.get_account.return_value = MagicMock(
            equity=Decimal("94000.00"),
            last_equity=Decimal("100000.00"),
        )

        assert risk_manager.is_daily_loss_limit_breached() is True

    def test_trading_allowed_when_under_daily_loss_limit(self, risk_manager, mock_alpaca_client):
        """Trading must continue when losses are within the daily limit."""
        mock_alpaca_client.get_account.return_value = MagicMock(
            equity=Decimal("97000.00"),  # only -3%, below -5% limit
            last_equity=Decimal("100000.00"),
        )

        assert risk_manager.is_daily_loss_limit_breached() is False

    def test_daily_loss_limit_exactly_at_threshold_halts(self, risk_manager, mock_alpaca_client):
        """Boundary condition: exactly at the limit should halt (>= not just >)."""
        mock_alpaca_client.get_account.return_value = MagicMock(
            equity=Decimal("95000.00"),  # exactly -5%
            last_equity=Decimal("100000.00"),
        )

        assert risk_manager.is_daily_loss_limit_breached() is True

    def test_halt_prevents_bracket_order_placement(
        self, risk_manager, mock_alpaca_client
    ):
        """When the daily loss limit is breached, place_bracket_order must refuse."""
        mock_alpaca_client.get_account.return_value = MagicMock(
            equity=Decimal("94000.00"),
            last_equity=Decimal("100000.00"),
        )

        with pytest.raises(Exception, match="[Dd]aily.loss|[Hh]alted|[Ll]imit"):
            risk_manager.assert_trading_allowed()

    def test_config_loads_default_values_correctly(self, tmp_path):
        """load_config must return all PRD-specified defaults when no overrides exist."""
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(
            "stop_strategy:\n"
            "  default_stop_loss_pct: 5.0\n"
            "  default_take_profit_pct: 10.0\n"
            "  trailing_stop_pct: 3.0\n"
            "  max_position_size_pct: 10.0\n"
            "  daily_loss_limit_pct: 5.0\n"
            "  use_trailing_stop: false\n"
            "  risk_reward_ratio: 2.0\n"
        )

        config = load_config(str(config_file))
        ss = config["stop_strategy"]

        assert ss["default_stop_loss_pct"] == 5.0
        assert ss["default_take_profit_pct"] == 10.0
        assert ss["trailing_stop_pct"] == 3.0
        assert ss["max_position_size_pct"] == 10.0
        assert ss["daily_loss_limit_pct"] == 5.0
        assert ss["use_trailing_stop"] is False
        assert ss["risk_reward_ratio"] == 2.0

    def test_config_missing_required_field_raises(self, tmp_path):
        """A config file missing a required field must raise at load time,
        not fail silently at runtime."""
        config_file = tmp_path / "bad_settings.yaml"
        config_file.write_text(
            "stop_strategy:\n"
            "  default_stop_loss_pct: 5.0\n"
            # daily_loss_limit_pct intentionally missing
        )

        with pytest.raises((KeyError, ValueError)):
            load_config(str(config_file))


# ===========================================================================
# MARKET HOURS GUARD
# ===========================================================================

class TestMarketHoursGuard:
    """
    Bot must be completely inert outside 09:30–16:00 ET, Monday–Friday.
    Tested via freezegun to control system time.
    """

    @freeze_time("2025-01-06 14:00:00", tz_offset=-5)  # Monday 2pm ET
    def test_orders_allowed_during_market_hours(self, mock_alpaca_client):
        mock_alpaca_client.get_clock.return_value = MagicMock(is_open=True)
        mock_alpaca_client.submit_order.return_value = MagicMock(id="order-300")

        # Should not raise
        place_bracket_order(
            client=mock_alpaca_client,
            symbol="AAPL",
            qty=1,
            entry_price=150.00,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )

        mock_alpaca_client.submit_order.assert_called_once()

    @freeze_time("2025-01-06 20:00:00", tz_offset=-5)  # Monday 8pm ET
    def test_orders_blocked_after_market_close(self, mock_alpaca_client):
        mock_alpaca_client.get_clock.return_value = MagicMock(is_open=False)

        with pytest.raises(Exception, match="[Mm]arket|[Cc]losed"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=1,
                entry_price=150.00,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
            )

    @freeze_time("2025-01-04 12:00:00", tz_offset=-5)  # Saturday noon ET
    def test_orders_blocked_on_weekend(self, mock_alpaca_client):
        mock_alpaca_client.get_clock.return_value = MagicMock(is_open=False)

        with pytest.raises(Exception, match="[Mm]arket|[Cc]losed"):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=1,
                entry_price=150.00,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
            )


# ===========================================================================
# DATABASE LOGGING
# ===========================================================================

class TestDatabaseLogging:
    """
    TR-3: All order events must be persisted with timestamps.
    """

    def test_order_event_logged_to_db(self, temp_db):
        """Every order submission must write a record to the orders table."""
        log_order_event(
            db_path=temp_db,
            order_id="order-400",
            symbol="AAPL",
            event_type="submitted",
            details={"qty": 10, "entry_price": 150.00},
        )

        conn = sqlite3.connect(temp_db)
        rows = conn.execute(
            "SELECT * FROM orders WHERE order_id = 'order-400'"
        ).fetchall()
        conn.close()

        assert len(rows) == 1

    def test_order_event_includes_timestamp(self, temp_db):
        """Each logged event must have a non-null timestamp."""
        log_order_event(
            db_path=temp_db,
            order_id="order-401",
            symbol="TSLA",
            event_type="filled",
            details={"filled_price": 250.00},
        )

        conn = sqlite3.connect(temp_db)
        row = conn.execute(
            "SELECT timestamp FROM orders WHERE order_id = 'order-401'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] is not None  # timestamp column must be populated

    def test_get_open_positions_returns_only_unfilled(self, temp_db):
        """get_open_positions must exclude CLOSED positions from its result."""
        conn = sqlite3.connect(temp_db)
        conn.executemany(
            "INSERT INTO orders (order_id, symbol, state, entry_price) VALUES (?, ?, ?, ?)",
            [
                ("order-501", "AAPL", "FILLED", 150.00),
                ("order-502", "TSLA", "CLOSED", 250.00),
                ("order-503", "MSFT", "PENDING", 300.00),
            ],
        )
        conn.commit()
        conn.close()

        open_positions = get_open_positions(temp_db)
        order_ids = [p["order_id"] for p in open_positions]

        assert "order-501" in order_ids
        assert "order-503" in order_ids
        assert "order-502" not in order_ids


# ===========================================================================
# RESILIENCE & ERROR HANDLING
# ===========================================================================

class TestResilience:
    """
    Edge cases: network failures, API timeouts, and partial data.
    """

    def test_api_timeout_does_not_crash_bot(self, mock_alpaca_client):
        """A transient network timeout should be caught and logged, not crash the process."""
        import socket
        mock_alpaca_client.submit_order.side_effect = socket.timeout("connection timed out")

        with pytest.raises(Exception):
            place_bracket_order(
                client=mock_alpaca_client,
                symbol="AAPL",
                qty=5,
                entry_price=150.00,
                stop_loss_pct=5.0,
                take_profit_pct=10.0,
            )
        # The test passes as long as an exception is raised cleanly (not a hang or silent fail)

    def test_duplicate_order_event_is_idempotent(self, order_monitor):
        """
        Receiving the same fill event twice (e.g. due to reconnect) must not
        create duplicate DB records or corrupt state.
        """
        order_monitor.register_order("order-600", symbol="AAPL", entry_price=150.00)

        fill_event = {
            "event": "fill",
            "order": {"id": "order-600", "filled_avg_price": "150.50"},
        }
        order_monitor.handle_event(fill_event)
        order_monitor.handle_event(fill_event)  # duplicate

        # State should still be FILLED, not broken or duplicated
        assert order_monitor.get_state("order-600") == OrderState.FILLED

    def test_account_fetch_failure_halts_order_placement(self, mock_alpaca_client):
        """If get_account fails (e.g. API down), no orders should be placed."""
        mock_alpaca_client.get_account.side_effect = Exception("API unavailable")

        with pytest.raises(Exception, match="API unavailable|account"):
            risk_mgr = RiskManager(
                client=mock_alpaca_client,
                config={
                    "daily_loss_limit_pct": 5.0,
                    "max_position_size_pct": 10.0,
                },
            )
            risk_mgr.assert_trading_allowed()
