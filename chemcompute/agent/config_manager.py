"""
Runtime configuration manager for ChemCompute Agent.
Handles loading, persisting, and hot-patching node configuration (node.yaml / node_config.json).
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional
import yaml

from chemcompute.common.models import DynamicNodeConfig, ReleaseChannel, UpdatePolicy


class ConfigManager:
    """Manages local node configuration and dynamic hot updates."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config: DynamicNodeConfig = DynamicNodeConfig()
        self.load()

    def load(self):
        """Load configuration from YAML or JSON file."""
        if not self.config_path.exists():
            self.save()
            return

        try:
            content = self.config_path.read_text(encoding="utf-8")
            if self.config_path.suffix.lower() in [".yaml", ".yml"]:
                data = yaml.safe_load(content) or {}
            else:
                data = json.loads(content) or {}
            self.config = DynamicNodeConfig.model_validate(data)
        except Exception as e:
            print(f"[ConfigManager] Warning loading config: {e}. Using defaults.")

    def save(self):
        """Persist current configuration to disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = self.config.model_dump()
        if self.config_path.suffix.lower() in [".yaml", ".yml"]:
            self.config_path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        else:
            self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def apply_patch(self, patch: Dict[str, Any]) -> DynamicNodeConfig:
        """Apply dynamic configuration patch from Controller and persist."""
        current_data = self.config.model_dump()
        current_data.update(patch)
        self.config = DynamicNodeConfig.model_validate(current_data)
        self.save()
        print(f"[ConfigManager] Configuration hot-updated: {patch}")
        return self.config
