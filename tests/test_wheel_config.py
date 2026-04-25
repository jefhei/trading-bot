"""
Tests for wheel strategy config loader (TB-041 / TR-4)
=======================================================
Test YAML parsing, validation, defaults, and config manipulation:
- Default config has all required keys and valid defaults
- validate_config accepts valid configs
- validate_config rejects invalid values (delta, DTE, percentages)
- load_config loads YAML, merges with defaults, handles missing files
- save_config writes valid YAML that can be reloaded
- _deep_merge correctly handles nested merging
- Watchlist validation checks for required fields

Run with:
    pytest tests/test_wheel_config.py -v

All tests are fully isolated — no live API calls are made.
"""

import pytest
import tempfile
import os
import yaml
from pathlib import Path

from bots.wheel_strategy.config_loader import (
    DEFAULT_CONFIG,
    validate_config,
    load_config,
    save_config,
    _deep_merge,
)


# ============================================================================
# Default Config Structure
# ============================================================================

class TestDefaultConfig:
    """Test DEFAULT_CONFIG structure and values."""

    def test_default_has_wheel_strategy_key(self):
        assert "wheel_strategy" in DEFAULT_CONFIG

    def test_default_has_all_subsections(self):
        wheel = DEFAULT_CONFIG["wheel_strategy"]
        assert "watchlist" in wheel
        assert "put_selling" in wheel
        assert "call_selling" in wheel
        assert "risk_controls" in wheel
        assert "roll_management" in wheel

    def test_default_watchlist_is_empty_list(self):
        assert DEFAULT_CONFIG["wheel_strategy"]["watchlist"] == []

    def test_default_put_selling_values(self):
        ps = DEFAULT_CONFIG["wheel_strategy"]["put_selling"]
        assert ps["days_to_expiration_min"] == 30
        assert ps["days_to_expiration_max"] == 45
        assert ps["target_delta"] == 0.30
        assert ps["min_premium_pct"] == 1.0
        assert ps["max_contracts_per_stock"] == 5
        assert ps["avoid_earnings"] is True

    def test_default_call_selling_values(self):
        cs = DEFAULT_CONFIG["wheel_strategy"]["call_selling"]
        assert cs["days_to_expiration_min"] == 30
        assert cs["days_to_expiration_max"] == 45
        assert cs["target_delta"] == 0.30
        assert cs["min_premium_pct"] == 1.0
        assert cs["strike_min_above_cost_basis"] == 0.0

    def test_default_risk_controls_values(self):
        rc = DEFAULT_CONFIG["wheel_strategy"]["risk_controls"]
        assert rc["max_capital_per_stock_pct"] == 20.0
        assert rc["max_total_puts"] == 10
        assert rc["max_sector_concentration_pct"] == 30.0
        assert rc["min_cash_reserve_pct"] == 20.0
        assert rc["stock_stop_loss_pct"] == 15.0

    def test_default_roll_management_values(self):
        rm = DEFAULT_CONFIG["wheel_strategy"]["roll_management"]
        assert rm["auto_roll_put_delta"] == 0.70
        assert rm["auto_roll_call_delta"] == 0.70
        assert rm["roll_days_to_expiration"] == 7

    def test_default_config_validates(self):
        """DEFAULT_CONFIG should pass validation."""
        # Should not raise
        validate_config(DEFAULT_CONFIG)


# ============================================================================
# Validation: Put Selling
# ============================================================================

class TestValidatePutSelling:
    """Test validation of put_selling configuration."""

    def _make_config(self, **put_overrides):
        """Helper to create config with put_selling overrides."""
        config = {
            "wheel_strategy": {
                "watchlist": [],
                "put_selling": {
                    "days_to_expiration_min": 30,
                    "days_to_expiration_max": 45,
                    "target_delta": 0.30,
                    "min_premium_pct": 1.0,
                    "max_contracts_per_stock": 5,
                },
                "call_selling": {
                    "target_delta": 0.30,
                },
                "risk_controls": {
                    "max_capital_per_stock_pct": 20.0,
                    "max_sector_concentration_pct": 30.0,
                    "min_cash_reserve_pct": 20.0,
                },
            }
        }
        config["wheel_strategy"]["put_selling"].update(put_overrides)
        return config

    def test_valid_put_selling_passes(self):
        config = self._make_config()
        validate_config(config)  # Should not raise

    def test_target_delta_zero_raises(self):
        config = self._make_config(target_delta=0.0)
        with pytest.raises(ValueError, match="target_delta"):
            validate_config(config)

    def test_target_delta_negative_raises(self):
        config = self._make_config(target_delta=-0.10)
        with pytest.raises(ValueError, match="target_delta"):
            validate_config(config)

    def test_target_delta_one_passes(self):
        config = self._make_config(target_delta=1.0)
        validate_config(config)  # Should not raise

    def test_target_delta_above_one_raises(self):
        config = self._make_config(target_delta=1.10)
        with pytest.raises(ValueError, match="target_delta"):
            validate_config(config)

    def test_very_small_delta_passes(self):
        config = self._make_config(target_delta=0.01)
        validate_config(config)

    def test_dte_min_zero_raises(self):
        config = self._make_config(days_to_expiration_min=0)
        with pytest.raises(ValueError, match="days_to_expiration_min"):
            validate_config(config)

    def test_dte_min_negative_raises(self):
        config = self._make_config(days_to_expiration_min=-5)
        with pytest.raises(ValueError, match="days_to_expiration_min"):
            validate_config(config)

    def test_dte_max_equals_min_raises(self):
        config = self._make_config(
            days_to_expiration_min=30,
            days_to_expiration_max=30
        )
        with pytest.raises(ValueError, match="days_to_expiration_max"):
            validate_config(config)

    def test_dte_max_below_min_raises(self):
        config = self._make_config(
            days_to_expiration_min=45,
            days_to_expiration_max=30
        )
        with pytest.raises(ValueError, match="days_to_expiration_max"):
            validate_config(config)

    def test_dte_range_valid(self):
        config = self._make_config(
            days_to_expiration_min=14,
            days_to_expiration_max=60
        )
        validate_config(config)

    def test_min_premium_pct_zero_passes(self):
        config = self._make_config(min_premium_pct=0.0)
        validate_config(config)

    def test_min_premium_pct_negative_raises(self):
        config = self._make_config(min_premium_pct=-1.0)
        with pytest.raises(ValueError, match="min_premium_pct"):
            validate_config(config)


# ============================================================================
# Validation: Call Selling
# ============================================================================

class TestValidateCallSelling:
    """Test validation of call_selling configuration."""

    def _make_config(self, **call_overrides):
        config = {
            "wheel_strategy": {
                "watchlist": [],
                "put_selling": {
                    "days_to_expiration_min": 30,
                    "days_to_expiration_max": 45,
                    "target_delta": 0.30,
                    "min_premium_pct": 1.0,
                },
                "call_selling": {
                    "target_delta": 0.30,
                    "days_to_expiration_min": 30,
                    "days_to_expiration_max": 45,
                    "min_premium_pct": 1.0,
                },
                "risk_controls": {
                    "max_capital_per_stock_pct": 20.0,
                    "max_sector_concentration_pct": 30.0,
                    "min_cash_reserve_pct": 20.0,
                },
            }
        }
        config["wheel_strategy"]["call_selling"].update(call_overrides)
        return config

    def test_valid_call_selling_passes(self):
        config = self._make_config()
        validate_config(config)

    def test_call_delta_zero_raises(self):
        config = self._make_config(target_delta=0.0)
        with pytest.raises(ValueError, match="Invalid target_delta for call_selling"):
            validate_config(config)

    def test_call_delta_above_one_raises(self):
        config = self._make_config(target_delta=1.50)
        with pytest.raises(ValueError, match="Invalid target_delta for call_selling"):
            validate_config(config)

    def test_call_delta_at_one_passes(self):
        config = self._make_config(target_delta=1.0)
        validate_config(config)


# ============================================================================
# Validation: Risk Controls
# ============================================================================

class TestValidateRiskControls:
    """Test validation of risk_controls configuration."""

    def _make_config(self, **risk_overrides):
        config = {
            "wheel_strategy": {
                "watchlist": [],
                "put_selling": {
                    "days_to_expiration_min": 30,
                    "days_to_expiration_max": 45,
                    "target_delta": 0.30,
                    "min_premium_pct": 1.0,
                },
                "call_selling": {"target_delta": 0.30},
                "risk_controls": {
                    "max_capital_per_stock_pct": 20.0,
                    "max_sector_concentration_pct": 30.0,
                    "min_cash_reserve_pct": 20.0,
                },
            }
        }
        config["wheel_strategy"]["risk_controls"].update(risk_overrides)
        return config

    def test_valid_risk_controls_pass(self):
        config = self._make_config()
        validate_config(config)

    def test_max_capital_zero_raises(self):
        config = self._make_config(max_capital_per_stock_pct=0.0)
        with pytest.raises(ValueError, match="max_capital_per_stock_pct"):
            validate_config(config)

    def test_max_capital_above_100_raises(self):
        config = self._make_config(max_capital_per_stock_pct=101.0)
        with pytest.raises(ValueError, match="max_capital_per_stock_pct"):
            validate_config(config)

    def test_max_capital_at_100_passes(self):
        config = self._make_config(max_capital_per_stock_pct=100.0)
        validate_config(config)

    def test_max_sector_zero_raises(self):
        config = self._make_config(max_sector_concentration_pct=0.0)
        with pytest.raises(ValueError, match="max_sector_concentration_pct"):
            validate_config(config)

    def test_max_sector_above_100_raises(self):
        config = self._make_config(max_sector_concentration_pct=150.0)
        with pytest.raises(ValueError, match="max_sector_concentration_pct"):
            validate_config(config)

    def test_min_cash_zero_passes(self):
        config = self._make_config(min_cash_reserve_pct=0.0)
        validate_config(config)

    def test_min_cash_negative_raises(self):
        config = self._make_config(min_cash_reserve_pct=-5.0)
        with pytest.raises(ValueError, match="min_cash_reserve_pct"):
            validate_config(config)

    def test_min_cash_at_100_passes(self):
        config = self._make_config(min_cash_reserve_pct=100.0)
        validate_config(config)

    def test_min_cash_above_100_raises(self):
        config = self._make_config(min_cash_reserve_pct=100.1)
        with pytest.raises(ValueError, match="min_cash_reserve_pct"):
            validate_config(config)


# ============================================================================
# Validation: Watchlist
# ============================================================================

class TestValidateWatchlist:
    """Test validation of watchlist entries."""

    def _make_config(self, watchlist=None):
        if watchlist is None:
            watchlist = []
        return {
            "wheel_strategy": {
                "watchlist": watchlist,
                "put_selling": {
                    "days_to_expiration_min": 30,
                    "days_to_expiration_max": 45,
                    "target_delta": 0.30,
                    "min_premium_pct": 1.0,
                },
                "call_selling": {"target_delta": 0.30},
                "risk_controls": {
                    "max_capital_per_stock_pct": 20.0,
                    "max_sector_concentration_pct": 30.0,
                    "min_cash_reserve_pct": 20.0,
                },
            }
        }

    def test_empty_watchlist_validates(self):
        config = self._make_config()
        validate_config(config)

    def test_watchlist_with_valid_entry(self):
        config = self._make_config(watchlist=[{"symbol": "AAPL"}])
        validate_config(config)

    def test_watchlist_multiple_entries(self):
        watchlist = [
            {"symbol": "AAPL", "max_contracts": 5, "sector": "Technology"},
            {"symbol": "MSFT", "max_contracts": 3, "sector": "Technology"},
        ]
        config = self._make_config(watchlist=watchlist)
        validate_config(config)

    def test_watchlist_missing_symbol_raises(self):
        config = self._make_config(watchlist=[{"max_contracts": 5}])
        with pytest.raises(ValueError, match="symbol"):
            validate_config(config)

    def test_watchlist_empty_symbol_raises(self):
        config = self._make_config(watchlist=[{"symbol": ""}])
        with pytest.raises(ValueError, match="symbol"):
            validate_config(config)

    def test_watchlist_non_string_symbol_raises(self):
        config = self._make_config(watchlist=[{"symbol": 123}])
        with pytest.raises(ValueError, match="symbol"):
            validate_config(config)


# ============================================================================
# Deep Merge Function
# ============================================================================

class TestDeepMerge:
    """Test the _deep_merge utility function."""

    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"a": 10}
        result = _deep_merge(base, override)
        assert result == {"a": 10, "b": 2}

    def test_nested_merge(self):
        base = {"x": {"y": 1, "z": 2}}
        override = {"x": {"y": 99}}
        result = _deep_merge(base, override)
        assert result == {"x": {"y": 99, "z": 2}}

    def test_deep_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 100}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 100, "d": 2}}}

    def test_new_keys_added(self):
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_nested_new_keys(self):
        base = {"x": {"y": 1}}
        override = {"x": {"z": 99}}
        result = _deep_merge(base, override)
        assert result == {"x": {"y": 1, "z": 99}}

    def test_list_override_replaces(self):
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = _deep_merge(base, override)
        assert result == {"items": [4, 5]}

    def test_empty_override_returns_base(self):
        base = {"a": 1, "b": {"c": 2}}
        result = _deep_merge(base, {})
        assert result == {"a": 1, "b": {"c": 2}}

    def test_empty_base_returns_override(self):
        result = _deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_base_not_mutated(self):
        base = {"a": 1, "nested": {"x": 10}}
        original = {"a": 1, "nested": {"x": 10}}
        _deep_merge(base, {"a": 2, "nested": {"x": 99}})
        assert base == original

    def test_full_yaml_merge_simulation(self):
        """Simulate merging user YAML with DEFAULT_CONFIG."""
        base = dict(DEFAULT_CONFIG)
        user = {
            "wheel_strategy": {
                "put_selling": {"target_delta": 0.20},
                "watchlist": [{"symbol": "AAPL"}],
            }
        }
        result = _deep_merge(base, user)
        # User overrides applied
        assert result["wheel_strategy"]["put_selling"]["target_delta"] == 0.20
        assert result["wheel_strategy"]["watchlist"] == [{"symbol": "AAPL"}]
        # Defaults preserved
        assert result["wheel_strategy"]["put_selling"]["days_to_expiration_min"] == 30
        assert result["wheel_strategy"]["risk_controls"]["max_capital_per_stock_pct"] == 20.0


# ============================================================================
# Load Config
# ============================================================================

class TestLoadConfig:
    """Test load_config with real files and edge cases."""

    def test_missing_file_returns_defaults(self):
        """When config file doesn't exist, should return DEFAULT_CONFIG copy."""
        config = load_config("/tmp/nonexistent_wheel_config_12345.yaml")
        assert config == DEFAULT_CONFIG

    def test_load_full_yaml_file(self):
        """Should load and validate a valid YAML config."""
        yaml_content = """
wheel_strategy:
  watchlist:
    - symbol: AAPL
      max_contracts: 5
  put_selling:
    days_to_expiration_min: 30
    days_to_expiration_max: 45
    target_delta: 0.30
    min_premium_pct: 1.0
    max_contracts_per_stock: 5
    avoid_earnings: true
  call_selling:
    days_to_expiration_min: 30
    days_to_expiration_max: 45
    target_delta: 0.30
    min_premium_pct: 1.0
    strike_min_above_cost_basis: 0.0
  risk_controls:
    max_capital_per_stock_pct: 20.0
    max_sector_concentration_pct: 30.0
    min_cash_reserve_pct: 20.0
    stock_stop_loss_pct: 15.0
  roll_management:
    auto_roll_put_delta: 0.70
    auto_roll_call_delta: 0.70
    roll_days_to_expiration: 7
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = load_config(f.name)
            os.unlink(f.name)

        assert config["wheel_strategy"]["watchlist"] == [{"symbol": "AAPL", "max_contracts": 5}]
        assert config["wheel_strategy"]["put_selling"]["target_delta"] == 0.30

    def test_load_partial_yaml_merges_defaults(self):
        """Should merge partial YAML with defaults."""
        yaml_content = """
wheel_strategy:
  put_selling:
    target_delta: 0.25
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = load_config(f.name)
            os.unlink(f.name)

        # User value applied
        assert config["wheel_strategy"]["put_selling"]["target_delta"] == 0.25
        # Defaults preserved
        assert config["wheel_strategy"]["put_selling"]["days_to_expiration_min"] == 30
        assert config["wheel_strategy"]["risk_controls"]["max_capital_per_stock_pct"] == 20.0

    def test_load_invalid_yaml_raises_validation(self):
        """Should raise ValueError for invalid config values."""
        yaml_content = """
wheel_strategy:
  put_selling:
    target_delta: 2.0
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            with pytest.raises(ValueError, match="target_delta"):
                load_config(f.name)
            os.unlink(f.name)

    def test_load_empty_yaml_returns_defaults(self):
        """Empty YAML file should return defaults."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            config = load_config(f.name)
            os.unlink(f.name)

        assert config == DEFAULT_CONFIG


# ============================================================================
# Save Config
# ============================================================================

class TestSaveConfig:
    """Test save_config functionality."""

    def test_save_and_reload_config(self):
        """Should save config to YAML and reload it identically."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_path = f.name

        try:
            # Save default config
            save_config(DEFAULT_CONFIG, config_path)
            assert os.path.exists(config_path)

            # Reload
            reloaded = load_config(config_path)
            assert reloaded == DEFAULT_CONFIG
        finally:
            if os.path.exists(config_path):
                os.unlink(config_path)

    def test_save_creates_parent_directories(self):
        """Should create parent directories if they don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "nested", "dir", "config.yaml")
            save_config(DEFAULT_CONFIG, config_path)
            assert os.path.exists(config_path)

    def test_save_custom_config_survives_roundtrip(self):
        """Custom config should survive save/load cycle."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_path = f.name

        try:
            custom = {
                "wheel_strategy": {
                    "watchlist": [{"symbol": "MSFT"}],
                    "put_selling": {
                        "days_to_expiration_min": 20,
                        "days_to_expiration_max": 35,
                        "target_delta": 0.25,
                        "min_premium_pct": 1.5,
                        "max_contracts_per_stock": 3,
                    },
                    "call_selling": {
                        "days_to_expiration_min": 20,
                        "days_to_expiration_max": 35,
                        "target_delta": 0.25,
                        "min_premium_pct": 1.5,
                        "strike_min_above_cost_basis": 0.0,
                    },
                    "risk_controls": {
                        "max_capital_per_stock_pct": 15.0,
                        "max_total_puts": 8,
                        "max_sector_concentration_pct": 25.0,
                        "min_cash_reserve_pct": 25.0,
                        "stock_stop_loss_pct": 10.0,
                    },
                    "roll_management": {
                        "auto_roll_put_delta": 0.60,
                        "auto_roll_call_delta": 0.60,
                        "roll_days_to_expiration": 14,
                    },
                }
            }

            save_config(custom, config_path)
            reloaded = load_config(config_path)

            assert reloaded["wheel_strategy"]["put_selling"]["target_delta"] == 0.25
            assert reloaded["wheel_strategy"]["risk_controls"]["max_capital_per_stock_pct"] == 15.0
            assert reloaded["wheel_strategy"]["watchlist"] == [{"symbol": "MSFT"}]
        finally:
            if os.path.exists(config_path):
                os.unlink(config_path)

    def test_saved_yaml_is_valid_yaml(self):
        """Saved file should be parseable as valid YAML."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_path = f.name

        try:
            save_config(DEFAULT_CONFIG, config_path)
            with open(config_path) as f:
                parsed = yaml.safe_load(f)
            assert "wheel_strategy" in parsed
        finally:
            if os.path.exists(config_path):
                os.unlink(config_path)


# ============================================================================
# End-to-End Integration Tests
# ============================================================================

class TestConfigEndToEnd:
    """Integration tests simulating real config workflows."""

    def test_save_tweak_reload(self):
        """User saves tweaked config, reloads, values preserved."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_path = f.name

        try:
            # Start with defaults
            config = dict(DEFAULT_CONFIG)
            config["wheel_strategy"]["put_selling"]["target_delta"] = 0.20
            config["wheel_strategy"]["risk_controls"]["min_cash_reserve_pct"] = 30.0

            save_config(config, config_path)
            reloaded = load_config(config_path)

            assert reloaded["wheel_strategy"]["put_selling"]["target_delta"] == 0.20
            assert reloaded["wheel_strategy"]["risk_controls"]["min_cash_reserve_pct"] == 30.0
            # Other defaults unchanged
            assert reloaded["wheel_strategy"]["put_selling"]["days_to_expiration_min"] == 30
        finally:
            if os.path.exists(config_path):
                os.unlink(config_path)

    def test_multiple_watchlist_entries_roundtrip(self):
        """Multiple watchlist entries should survive config roundtrip."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_path = f.name

        try:
            config = dict(DEFAULT_CONFIG)
            config["wheel_strategy"]["watchlist"] = [
                {"symbol": "AAPL", "max_contracts": 5, "max_capital": 10000, "sector": "Technology", "enabled": True},
                {"symbol": "MSFT", "max_contracts": 3, "max_capital": 15000, "sector": "Technology", "enabled": True},
                {"symbol": "JNJ", "max_contracts": 4, "max_capital": 8000, "sector": "Healthcare", "enabled": False},
            ]

            save_config(config, config_path)
            reloaded = load_config(config_path)

            assert len(reloaded["wheel_strategy"]["watchlist"]) == 3
            assert reloaded["wheel_strategy"]["watchlist"][0]["symbol"] == "AAPL"
            assert reloaded["wheel_strategy"]["watchlist"][2]["symbol"] == "JNJ"
            assert reloaded["wheel_strategy"]["watchlist"][2]["enabled"] is False
        finally:
            if os.path.exists(config_path):
                os.unlink(config_path)
