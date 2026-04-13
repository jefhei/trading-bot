"""
Configuration loader for Stop Strategy Bot.
Loads and validates YAML configuration files.
"""
from typing import Dict, Any
from pathlib import Path
import yaml


DEFAULT_CONFIG = {
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

REQUIRED_FIELDS = [
    "default_stop_loss_pct",
    "default_take_profit_pct",
    "trailing_stop_pct",
    "max_position_size_pct",
    "daily_loss_limit_pct",
    "use_trailing_stop",
    "risk_reward_ratio",
]


def load_config(config_path: str) -> Dict[str, Any]:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to YAML configuration file

    Returns:
        Dict containing configuration values

    Raises:
        FileNotFoundError: If config file doesn't exist
        KeyError: If required fields are missing
        ValueError: If configuration is invalid
    """
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    if config is None:
        raise ValueError(f"Configuration file is empty: {config_path}")

    if "stop_strategy" not in config:
        raise KeyError("Missing 'stop_strategy' section in configuration")

    ss_config = config["stop_strategy"]

    # Validate required fields
    for field in REQUIRED_FIELDS:
        if field not in ss_config:
            raise KeyError(f"Missing required field in stop_strategy config: {field}")

    # Validate value ranges
    _validate_config_values(ss_config)

    return config


def _validate_config_values(config: Dict[str, Any]) -> None:
    """
    Validate configuration value ranges.

    Args:
        config: Stop strategy configuration dict

    Raises:
        ValueError: If any value is out of valid range
    """
    validations = [
        ("default_stop_loss_pct", 0.1, 50.0),
        ("default_take_profit_pct", 0.1, 100.0),
        ("trailing_stop_pct", 0.1, 20.0),
        ("max_position_size_pct", 1.0, 100.0),
        ("daily_loss_limit_pct", 0.1, 50.0),
        ("risk_reward_ratio", 0.1, 10.0),
    ]

    for field, min_val, max_val in validations:
        value = config.get(field)
        if not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a number, got {type(value)}")

        if not (min_val <= value <= max_val):
            raise ValueError(
                f"{field} must be between {min_val} and {max_val}, got {value}"
            )


def create_default_config(config_path: str) -> None:
    """
    Create a default configuration file.

    Args:
        config_path: Path to create configuration file
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
