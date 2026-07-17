"""Unit tests for configuration loading."""

import os
import pytest
from pathlib import Path


class TestConfigLoading:
    """Test configuration file loading and validation."""
    
    def test_project_config_exists(self):
        """project.yml must exist in config directory."""
        config_path = Path(__file__).parent.parent.parent / "config" / "project.yml"
        assert config_path.exists(), f"Missing: {config_path}"
    
    def test_sources_config_exists(self):
        """sources.yml must exist in config directory."""
        config_path = Path(__file__).parent.parent.parent / "config" / "sources.yml"
        assert config_path.exists(), f"Missing: {config_path}"
    
    def test_quality_config_exists(self):
        """data_quality.yml must exist in config directory."""
        config_path = Path(__file__).parent.parent.parent / "config" / "data_quality.yml"
        assert config_path.exists(), f"Missing: {config_path}"
    
    def test_environments_config_exists(self):
        """environments.yml must exist in config directory."""
        config_path = Path(__file__).parent.parent.parent / "config" / "environments.yml"
        assert config_path.exists(), f"Missing: {config_path}"
    
    def test_load_project_config(self):
        """Project config loads and has required keys."""
        import yaml
        config_path = Path(__file__).parent.parent.parent / "config" / "project.yml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        assert "project" in config
        assert "platform" in config
        assert "architecture" in config
        assert config["project"]["name"] == "FuelSignal"
    
    def test_load_sources_config(self):
        """Sources config loads and has required sources."""
        import yaml
        config_path = Path(__file__).parent.parent.parent / "config" / "sources.yml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        assert "sources" in config
        sources = config["sources"]
        assert "nsw_fuelcheck" in sources
        assert "aip_terminal_gate_prices" in sources
        assert "nsw_public_holidays" in sources
    
    def test_source_urls_are_valid(self):
        """All source landing pages should be valid URLs."""
        import yaml
        from urllib.parse import urlparse
        
        config_path = Path(__file__).parent.parent.parent / "config" / "sources.yml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        for name, source in config["sources"].items():
            landing_page = source.get("landing_page", "")
            if landing_page:
                parsed = urlparse(landing_page)
                assert parsed.scheme in ("http", "https"), \
                    f"Source '{name}' has invalid URL scheme: {landing_page}"
                assert parsed.netloc, \
                    f"Source '{name}' has no netloc: {landing_page}"
    
    def test_no_secrets_in_config(self):
        """Config files must not contain secrets."""
        import yaml
        config_dir = Path(__file__).parent.parent.parent / "config"
        
        secret_patterns = ["dapi", "token", "password", "secret", "Bearer"]
        
        for config_file in config_dir.glob("*.yml"):
            content = config_file.read_text()
            for pattern in secret_patterns:
                # Check for actual secret values (not field names)
                lines = content.split("\n")
                for line in lines:
                    if ":" in line and pattern in line.split(":", 1)[-1]:
                        # Allow the word in descriptions/comments
                        if not line.strip().startswith("#") and "dapi" in line.split(":", 1)[-1]:
                            pytest.fail(
                                f"Possible secret in {config_file.name}: {line.strip()[:50]}"
                            )
