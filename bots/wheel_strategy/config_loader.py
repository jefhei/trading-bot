"""
Configuration loader for wheel strategy bot.
Loads and validates YAML configuration.
"""
import logging
from typing import Dict, Any, Optional
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "wheel_strategy": {
        "watchlist": [],
        "put_selling": {
            "days_to_expiration_min": 30,
            "days_to_expiration_max": 45,
            "target_delta": 0.30,
            "min_premium_pct": 1.0,
            "max_contracts_per_stock": 5,
            "avoid_earnings": True,
        },
        "call_selling": {
            "days_to_expiration_min": 30,
            "days_to_expiration_max": 45,
            "target_delta": 0.30,
            "min_premium_pct": 1.0,
            "strike_min_above_cost_basis": 0.0,
        },
        "risk_controls": {
            "max_capital_per_stock_pct": 20.0,
            "max_total_puts": 10,
            "max_sector_concentration_pct": 30.0,
            "min_cash_reserve_pct": 20.0,
            "stock_stop_loss_pct": 15.0,
        },
        "roll_management": {
            "auto_roll_put_delta": 0.70,
            "auto_roll_call_delta": 0.70,
            "roll_days_to_expiration": 7,
        },
    }
}


def validate_config(config: Dict[str, Any]) -> None:
    """
    Validate wheel strategy configuration values.

    Raises:
        ValueError: If any configuration values are invalid
    """
    wheel = config.get("wheel_strategy", {})

    put_selling = wheel.get("put_selling", {})
    if put_selling.get("target_delta", 0) <= 0 or put_selling.get("target_delta", 1) > 1:
        raise ValueError(f"Invalid target_delta for put_selling: {put_selling.get('target_delta')}")
    if put_selling.get("days_to_expiration_min", 0) <= 0:
        raise ValueError("days_to_expiration_min must be positive")
    if put_selling.get("days_to_expiration_max", 0) <= put_selling.get("days_to_expiration_min", 0):
        raise ValueError("days_to_expiration_max must be > days_to_expiration_min")
    if put_selling.get("min_premium_pct", 0) < 0:
        raise ValueError("min_premium_pct must be non-negative")

    call_selling = wheel.get("call_selling", {})
    if call_selling.get("target_delta", 0) <= 0 or call_selling.get("target_delta", 1) > 1:
        raise ValueError(f"Invalid target_delta for call_selling: {call_selling.get('target_delta')}")

    risk = wheel.get("risk_controls", {})
    if risk.get("max_capital_per_stock_pct", 0) <= 0 or risk.get("max_capital_per_stock_pct", 100) > 100:
        raise ValueError(f"Invalid max_capital_per_stock_pct: {risk.get('max_capital_per_stock_pct')}")
    if risk.get("max_sector_concentration_pct", 0) <= 0 or risk.get("max_sector_concentration_pct", 100) > 100:
        raise ValueError(f"Invalid max_sector_concentration_pct: {risk.get('max_sector_concentration_pct')}")
    if risk.get("min_cash_reserve_pct", 0) < 0 or risk.get("min_cash_reserve_pct", 100) > 100:
        raise ValueError(f"Invalid min_cash_reserve_pct: {risk.get('min_cash_reserve_pct')}")

    watchlist = wheel.get("watchlist", [])
    for item in watchlist:
        if "symbol" not in item:
            raise ValueError(f"Watchlist entry missing 'symbol': {item}")
        symbol = item["symbol"]
        if not isinstance(symbol, str) or len(symbol) == 0:
            raise ValueError(f"Invalid symbol in watchlist: {symbol}")

    # Validate watchlist entries
    for entry in watchlist:
        if "symbol" not in entry:
            raise ValueError("Each watchlist entry must have a 'symbol' field")
        if "delta" not in entry.get("filters", {}) and "target_delta" not in entry:
            logger.warning(f"Watchlist entry {entry['symbol']} has no delta filter set")


def load_config(config_path: str) -> Dict[str, Any]:
    """
    Load wheel strategy configuration from YAML file.
    Merges with defaults for any missing values.

    Args:
        config_path: Path to YAML configuration file

    Returns:
        Merged configuration dictionary

    Raises:
        FileNotFoundError: If config file does not exist
        ValueError: If configuration values are invalid
    """
    path = Path(config_path)

    if not path.exists():
        logger.warning(f"Config file not found at {config_path}, using defaults")
        return DEFAULT_CONFIG

    with open(path) as f:
        user_config = yaml.safe_load(f) or {}

    # Deep merge user config with defaults
    config = _deep_merge(DEFAULT_CONFIG, user_config)

    validate_config(config)

    logger.info(f"Loaded wheel strategy config from {config_path}")
    return config


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Deep merge two dictionaries. Override values take precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def save_config(config: Dict[str, Any], config_path: str):
    """
    Save configuration to YAML file.

    Args:
        config: Configuration dictionary
        config_path: Path to save YAML file
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Saved wheel strategy config to {config_path}")
