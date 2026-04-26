"""
Configuration loader for Stop Strategy Bot.
Loads and validates YAML configuration files.
"""
import logging
from typing import Dict, Any
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)


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
        logger.warning(f"Configuration file not found: {config_path}. Using default configuration.")
        return _apply_defaults(DEFAULT_CONFIG.copy())

    try:
        with open(path, "r") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse configuration file {config_path}: {e}. Using defaults.")
        return _apply_defaults(DEFAULT_CONFIG.copy())
    except IOError as e:
        logger.error(f"Failed to read configuration file {config_path}: {e}. Using defaults.")
        return _apply_defaults(DEFAULT_CONFIG.copy())

    if config is None:
        logger.warning(f"Configuration file is empty: {config_path}. Using defaults.")
        return _apply_defaults(DEFAULT_CONFIG.copy())

    if "stop_strategy" not in config:
        logger.warning(f"Missing 'stop_strategy' section in {config_path}. Using defaults.")
        return _apply_defaults(DEFAULT_CONFIG.copy())

    ss_config = config["stop_strategy"]

    # Validate required fields — fill missing ones from defaults
    for field in REQUIRED_FIELDS:
        if field not in ss_config:
            logger.warning(f"Missing required field '{field}' in config, using default {_get_default(field)}")
            ss_config[field] = _get_default(field)

    # Validate value ranges — log and clamp dangerous values
    _validate_config_values_with_defaults(ss_config)

    # Cross-field validation — warn on dangerous config combinations
    _validate_config_relationships(ss_config)

    return config


def _get_default(field: str) -> Any:
    """Get the default value for a config field."""
    defaults = {
        "default_stop_loss_pct": 5.0,
        "default_take_profit_pct": 10.0,
        "trailing_stop_pct": 3.0,
        "max_position_size_pct": 10.0,
        "daily_loss_limit_pct": 5.0,
        "use_trailing_stop": False,
        "risk_reward_ratio": 2.0,
    }
    return defaults.get(field)


def _validate_config_relationships(config: Dict[str, Any]) -> None:
    """
    Cross-field validation — warn on dangerous config combinations.

    Checks that related config values form a safe set:
    - stop_loss_pct should be less than daily_loss_limit_pct
    - trailing_stop_pct should be less than default_stop_loss_pct
    - risk_reward_ratio should be consistent with take_profit / stop_loss
    """
    stop_loss = config.get("default_stop_loss_pct", 0)
    take_profit = config.get("default_take_profit_pct", 0)
    trailing = config.get("trailing_stop_pct", 0)
    daily_limit = config.get("daily_loss_limit_pct", 0)
    risk_reward = config.get("risk_reward_ratio", 0)

    # Stop loss should not exceed daily loss limit
    if stop_loss > daily_limit:
        logger.warning(
            f"STOP LOSS EXCEEDS DAILY LIMIT: stop_loss={stop_loss}% > "
            f"daily_loss_limit={daily_limit}%. A single stop-out hits the daily cap."
        )

    # Trailing stop should be tighter than the initial stop loss
    if trailing >= stop_loss:
        logger.warning(
            f"TRAILING STOP NOT TIGHTER THAN INITIAL: trailing_stop={trailing}% >= "
            f"stop_loss={stop_loss}%. Trailing stop should be narrower to protect "
            f"unrealized gains."
        )

    # Implied R:R from take_profit / stop_loss should be reasonable
    if stop_loss > 0:
        implied_rr = round(take_profit / stop_loss, 2)
        if risk_reward > 0 and abs(implied_rr - risk_reward) > 0.5:
            logger.warning(
                f"R:R MISMATCH: implied risk/reward={implied_rr} (take_profit/stop_loss) "
                f"but configured risk_reward_ratio={risk_reward}. Difference > 0.5 — "
                f"verify your expectations."
            )

    # Take profit should generally exceed stop loss (positive expectancy setup)
    if take_profit <= stop_loss and risk_reward > 1.0:
        logger.warning(
            f"TAKE PROFIT NOT GREATER THAN STOP LOSS: take_profit={take_profit}% <= "
            f"stop_loss={stop_loss}% with risk_reward_ratio={risk_reward} > 1.0. "
            f"The configured percentages create a negative expectancy setup."
        )

    logger.info(
        f"Config cross-validation complete. "
        f"stop_loss={stop_loss}%, take_profit={take_profit}%, "
        f"trailing={trailing}%, daily_limit={daily_limit}%, R:R={risk_reward}"
    )


def _apply_defaults(config: dict) -> Dict[str, Any]:
    """Apply default values for any missing required fields."""
    ss_config = config.get("stop_strategy", config)
    for field in REQUIRED_FIELDS:
        if field not in ss_config:
            ss_config[field] = _get_default(field)
    return config


def _validate_config_values_with_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate configuration value ranges — fills in defaults for missing fields
    and logs warnings for out-of-range values instead of raising.
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
        if value is None:
            config[field] = _get_default(field)
            logger.warning(f"Config field '{field}' is missing, using default {config[field]}")
            continue
        if not isinstance(value, (int, float)):
            logger.error(f"Invalid type for {field}: {type(value)}, using default {config[field]}")
            config[field] = _get_default(field)
            continue
        if not (min_val <= value <= max_val):
            clamped = max(min_val, min(max_val, value))
            logger.warning(
                f"{field} out of range ({value}). Must be {min_val}-{max_val}. "
                f"Clamping to {clamped}."
            )
            config[field] = clamped

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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Created default configuration at {config_path}")
    except IOError as e:
        raise RuntimeError(f"Failed to create configuration file at {config_path}: {e}")
