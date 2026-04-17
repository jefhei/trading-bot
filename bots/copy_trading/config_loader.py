"""
Configuration loader for copy trading.
"""
import yaml
from pathlib import Path
from typing import Dict, Any


def load_config(config_path: str) -> Dict[str, Any]:
    """
    Load copy trading configuration from YAML file.

    Args:
        config_path: Path to YAML config file

    Returns:
        Dict with configuration
    """
    path = Path(config_path)
    
    if not path.exists():
        # Return default config if file doesn't exist
        return get_default_config()
    
    with open(path, 'r') as f:
        return yaml.safe_load(f)


def get_default_config() -> Dict[str, Any]:
    """
    Get default configuration for copy trading.

    Returns:
        Dict with default config
    """
    return {
        "copy_trading": {
            "enabled": True,
            "masters": [],
            "risk_controls": {
                "max_allocation_per_master_pct": 30.0,
                "max_total_allocation_pct": 80.0,
                "daily_loss_limit_per_master_pct": 5.0,
                "max_drawdown_pct": 15.0,
                "min_cash_reserve_pct": 10.0
            },
            "position_sizing": {
                "method": "proportional",  # proportional, fixed, multiplier
                "fixed_amount": 1000.0,    # For fixed method
                "multiplier": 1.0          # For multiplier method
            },
            "filters": {
                "min_position_size": 100.0,
                "max_position_size": 5000.0,
                "symbols_blacklist": [],
                "asset_classes": ["us_equity"],
                "allow_short": False
            },
            "execution": {
                "max_retries": 3,
                "retry_delay_seconds": 1.0,
                "queue_on_failure": True
            }
        }
    }


def create_default_config(config_path: str):
    """
    Create a default configuration file.

    Args:
        config_path: Path where config should be created
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    config = get_default_config()
    
    with open(path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def validate_config(config: Dict[str, Any]) -> bool:
    """
    Validate copy trading configuration.

    Args:
        config: Configuration dictionary

    Returns:
        bool: True if valid

    Raises:
        ValueError: If configuration is invalid
    """
    if "copy_trading" not in config:
        raise ValueError("Missing 'copy_trading' section in config")
    
    copy_config = config["copy_trading"]
    
    # Validate risk controls
    risk = copy_config.get("risk_controls", {})
    
    if risk.get("max_allocation_per_master_pct", 0) > 100:
        raise ValueError("max_allocation_per_master_pct cannot exceed 100")
    
    if risk.get("max_total_allocation_pct", 0) > 100:
        raise ValueError("max_total_allocation_pct cannot exceed 100")
    
    # Validate position sizing method
    sizing_method = copy_config.get("position_sizing", {}).get("method")
    valid_methods = ["proportional", "fixed", "multiplier"]
    if sizing_method not in valid_methods:
        raise ValueError(f"Invalid sizing method: {sizing_method}. Must be one of {valid_methods}")
    
    # Validate masters (if defined)
    masters = copy_config.get("masters", [])
    total_allocation = sum(m.get("allocation_pct", 0) for m in masters)
    if total_allocation > 100:
        raise ValueError(f"Total master allocation exceeds 100%: {total_allocation}")
    
    return True
