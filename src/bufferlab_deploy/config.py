"""
Configuration management for BufferLab Deploy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class TransferSettings(BaseModel):
    """Transfer modeling settings."""
    enabled: bool = True
    assume_unlimited_capacity: bool = False


class PeggingSettings(BaseModel):
    """Pegging algorithm settings."""
    enabled: bool = True
    default_priority: int = 50


class AnalysisSettings(BaseModel):
    """Analysis configuration."""
    week_start: int = Field(default=0, ge=0, le=6)
    default_scenario: str = "baseline"
    horizon_weeks: int = 12
    transfers: TransferSettings = Field(default_factory=TransferSettings)
    pegging: PeggingSettings = Field(default_factory=PeggingSettings)


class BufferTarget(BaseModel):
    """Buffer target for a segment."""
    min: int = 2
    max: int = 4
    location: str = "regional"


class BufferPolicy(BaseModel):
    """Buffer policy settings."""
    lead_time_high_risk: int = 60
    lead_time_medium_risk: int = 30
    low_confidence_threshold: float = 0.7
    aging_warning: int = 60
    aging_critical: int = 90
    targets: dict[str, BufferTarget] = Field(default_factory=dict)


class DataPaths(BaseModel):
    """Data path configuration."""
    gold_path: str = "./data/gold"
    runs_path: str = "./runs"


class ExportSettings(BaseModel):
    """Export configuration."""
    excel: bool = True
    html: bool = True
    pdf: bool = False


class LoggingSettings(BaseModel):
    """Logging configuration."""
    level: str = "INFO"
    format: str = "json"
    console: bool = True


class AppConfig(BaseModel):
    """Main application configuration."""
    data: DataPaths = Field(default_factory=DataPaths)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    mw_per_kit: dict[str, Any] = Field(default_factory=lambda: {"default": 0.5})
    buffer_policy: BufferPolicy = Field(default_factory=BufferPolicy)
    exports: ExportSettings = Field(default_factory=ExportSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    
    @property
    def gold_path(self) -> Path:
        return Path(self.data.gold_path)
    
    @property
    def runs_path(self) -> Path:
        return Path(self.data.runs_path)


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to config file. If None, uses default.
    
    Returns:
        AppConfig instance
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent.parent / "configs" / "default_config.yml"
    
    config_path = Path(config_path)
    
    if not config_path.exists():
        # Return default config if file doesn't exist
        return AppConfig()
    
    with open(config_path, "r") as f:
        data = yaml.safe_load(f) or {}
    
    # Parse buffer targets
    if "buffer_policy" in data and "targets" in data["buffer_policy"]:
        targets = {}
        for key, value in data["buffer_policy"]["targets"].items():
            targets[key] = BufferTarget(**value)
        data["buffer_policy"]["targets"] = targets
    
    return AppConfig(**data)


# Global config instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the current configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: AppConfig) -> None:
    """Set the global configuration."""
    global _config
    _config = config
