"""
Calibration script for segmentation thresholds.

Computes percentile-based unit cost and lead-time thresholds to help tune
segmentation settings.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.config import load_config, set_config


def main() -> None:
    config_path = Path(__file__).parent / "configs" / "default_config.yml"
    config = load_config(config_path)
    set_config(config)

    loader = DuckDBLoader(config.gold_path)
    loader.load_all_tables()

    item_master = loader.get_table("item_master")
    lead_time = loader.get_table("lead_time_history")

    if item_master is None or len(item_master) == 0:
        raise SystemExit("item_master missing; export gold data first.")

    unit_cost = item_master.select(pl.col("unit_cost").cast(pl.Float64, strict=False)).drop_nulls()
    cost_threshold = unit_cost.quantile(0.8, "nearest")[0, 0] if len(unit_cost) > 0 else 5000

    lead_time_threshold = 45
    if lead_time is not None and len(lead_time) > 0:
        lead_time_threshold = (
            lead_time.select(pl.col("lead_time_p95").cast(pl.Float64, strict=False))
            .drop_nulls()
            .quantile(0.8, "nearest")[0, 0]
        )

    print("Suggested thresholds:")
    print(f"- high_eo_unit_cost: {cost_threshold}")
    print(f"- constrained_lead_time: {lead_time_threshold}")


if __name__ == "__main__":
    main()
