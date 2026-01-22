"""
Scenario comparison engine.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.pegging_engine import PeggingEngine
from bufferlab_deploy.blocker_engine import BlockerEngine
from bufferlab_deploy.stranded_engine import StrandedEngine
from bufferlab_deploy.config import get_config


class ScenarioEngine:
    """
    Summarize scenario outcomes for comparison.
    """

    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
        self.pegging_engine = PeggingEngine(loader)
        self.blocker_engine = BlockerEngine(loader)
        self.stranded_engine = StrandedEngine(loader)

    def get_scenario_summary(
        self,
        scenario_id: str,
        site_id: str | None = None,
        week_start: date | None = None,
        week_end: date | None = None,
    ) -> dict[str, object]:
        """
        Summarize completion, blockers, and stranded value.
        """
        pegging = self.pegging_engine.run_pegging(scenario_id)
        if len(pegging) == 0:
            return {
                "scenario_id": scenario_id,
                "total_deployable": 0,
                "total_buildable": 0,
                "total_blocked": 0,
                "completion_rate": 0.0,
                "top_blocker": "N/A",
                "stranded_value": 0.0,
            }

        pegging = self._filter(pegging, site_id, week_start, week_end)

        totals = pegging.select([
            pl.col("deployable_kits").sum().alias("total_deployable"),
            pl.col("buildable_kits").sum().alias("total_buildable"),
            pl.col("blocked_kits").sum().alias("total_blocked"),
        ]).to_dicts()[0]

        completion_rate = 0.0
        if totals["total_deployable"] > 0:
            completion_rate = round(
                totals["total_buildable"] / totals["total_deployable"] * 100, 1
            )

        top_blocker = "N/A"
        blockers = self.blocker_engine.get_blocker_attribution(scenario_id)
        if len(blockers) > 0:
            blockers = self._filter(blockers, site_id, week_start, week_end)
            if len(blockers) > 0:
                blockers = blockers.sort("gap_qty", descending=True)
                top_blocker = blockers["item_id"][0]

        stranded_value = 0.0
        stranded = self.stranded_engine.get_stranded_inventory(scenario_id)
        if len(stranded) > 0:
            stranded = self._filter(stranded, site_id, week_start, week_end)
            stranded_value = float(stranded["stranded_value"].sum())

        return {
            "scenario_id": scenario_id,
            "total_deployable": int(totals["total_deployable"]),
            "total_buildable": int(totals["total_buildable"]),
            "total_blocked": int(totals["total_blocked"]),
            "completion_rate": completion_rate,
            "top_blocker": top_blocker,
            "stranded_value": round(stranded_value, 2),
        }

    def _filter(
        self,
        df: pl.DataFrame,
        site_id: str | None,
        week_start: date | None,
        week_end: date | None,
    ) -> pl.DataFrame:
        if site_id and "site_id" in df.columns:
            df = df.filter(pl.col("site_id") == site_id)
        if "week" in df.columns:
            if week_start is not None:
                df = df.filter(pl.col("week") >= week_start)
            if week_end is not None:
                df = df.filter(pl.col("week") <= week_end)
        return df
