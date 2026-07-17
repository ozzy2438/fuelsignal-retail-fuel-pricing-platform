"""FuelSignal Configuration Management.

Loads and validates project configuration from YAML files.
Handles environment-specific settings and credential management.
Credentials are NEVER loaded from config files - only from environment variables.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def load_env() -> None:
    """Load environment variables from .env file if it exists.
    
    Only loads from .env file in the project root.
    Never logs or prints credential values.
    """
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)


def get_databricks_config() -> dict[str, str]:
    """Get Databricks connection configuration from environment variables.
    
    Returns:
        Dictionary with host and token keys.
        
    Raises:
        EnvironmentError: If required environment variables are not set.
    """
    load_env()
    
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    
    if not host:
        raise EnvironmentError(
            "DATABRICKS_HOST environment variable is not set. "
            "Please set it to your Databricks workspace URL."
        )
    if not token:
        raise EnvironmentError(
            "DATABRICKS_TOKEN environment variable is not set. "
            "Please set it to your Databricks personal access token."
        )
    
    # Normalize host URL
    if not host.startswith("https://"):
        host = f"https://{host}"
    host = host.rstrip("/")
    
    return {
        "host": host,
        "token": token,
        "catalog": os.environ.get("DATABRICKS_CATALOG", "main"),
        "schema_prefix": os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal"),
    }


def load_yaml_config(filename: str) -> dict[str, Any]:
    """Load a YAML configuration file from the config directory.
    
    Args:
        filename: Name of the YAML file (e.g., 'project.yml')
        
    Returns:
        Parsed YAML content as dictionary.
        
    Raises:
        FileNotFoundError: If the config file doesn't exist.
        yaml.YAMLError: If the file contains invalid YAML.
    """
    config_path = CONFIG_DIR / filename
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}"
        )
    
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def load_project_config() -> dict[str, Any]:
    """Load the main project configuration."""
    return load_yaml_config("project.yml")


def load_sources_config() -> dict[str, Any]:
    """Load the data sources configuration."""
    return load_yaml_config("sources.yml")


def load_quality_config() -> dict[str, Any]:
    """Load the data quality configuration."""
    return load_yaml_config("data_quality.yml")


def load_environment_config(environment: str = None) -> dict[str, Any]:
    """Load environment-specific configuration.
    
    Args:
        environment: Environment name (dev/staging/prod). 
                    Defaults to ENVIRONMENT env var or 'dev'.
    """
    if environment is None:
        environment = os.environ.get("ENVIRONMENT", "dev")
    
    config = load_yaml_config("environments.yml")
    environments = config.get("environments", {})
    
    if environment not in environments:
        raise ValueError(
            f"Unknown environment '{environment}'. "
            f"Available: {list(environments.keys())}"
        )
    
    return environments[environment]


def get_schema_names(catalog: str = None, prefix: str = None) -> dict[str, str]:
    """Get fully qualified schema names.
    
    Returns:
        Dictionary mapping layer names to full schema paths.
    """
    if catalog is None:
        db_config = get_databricks_config()
        catalog = db_config["catalog"]
    if prefix is None:
        prefix = os.environ.get("DATABRICKS_SCHEMA_PREFIX", "fuelsignal")
    
    return {
        "bronze": f"{catalog}.{prefix}_bronze",
        "silver": f"{catalog}.{prefix}_silver",
        "gold": f"{catalog}.{prefix}_gold",
        "monitoring": f"{catalog}.{prefix}_monitoring",
    }
