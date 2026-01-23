"""
Scenario comparison engine with tiered demand support.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.pegging_engine import PeggingEngine
from bufferlab_deploy.blocker_engine import BlockerEngine
from bufferlab_deploy.stranded_engine import StrandedEngine
from bufferlab_deploy.config import get_config


DemandTier = Literal["committed", "likely", "exploratory", "all"]


class ScenarioEngine:
    """
    Summarize scenario outcomes for comparison with tiered demand support.
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
        demand_tier: DemandTier = "all",
    ) -> dict[str, object]:
        """
        Summarize completion, blockers, and stranded value.
        
        Args:
            demand_tier: Filter by demand tier (committed, likely, exploratory, or all)
        """
        pegging = self.pegging_engine.run_pegging(scenario_id)
        if len(pegging) == 0:
            return {
                "scenario_id": scenario_id,
                "demand_tier": demand_tier,
                "total_deployable": 0,
                "total_buildable": 0,
                "total_blocked": 0,
                "completion_rate": 0.0,
                "top_blocker": "N/A",
                "stranded_value": 0.0,
            }

        pegging = self._filter(pegging, site_id, week_start, week_end, demand_tier)

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
            blockers = self._filter(blockers, site_id, week_start, week_end, demand_tier)
            if len(blockers) > 0:
                blockers = blockers.sort("gap_qty", descending=True)
                top_blocker = blockers["item_id"][0]

        stranded_value = 0.0
        stranded = self.stranded_engine.get_stranded_inventory(scenario_id)
        if len(stranded) > 0:
            stranded = self._filter(stranded, site_id, week_start, week_end, demand_tier)
            stranded_value = float(stranded["stranded_value"].sum())

        return {
            "scenario_id": scenario_id,
            "demand_tier": demand_tier,
            "total_deployable": int(totals["total_deployable"]),
            "total_buildable": int(totals["total_buildable"]),
            "total_blocked": int(totals["total_blocked"]),
            "completion_rate": completion_rate,
            "top_blocker": top_blocker,
            "stranded_value": round(stranded_value, 2),
        }

    def get_tiered_summary(
        self,
        scenario_id: str,
        site_id: str | None = None,
        week_start: date | None = None,
        week_end: date | None = None,
    ) -> dict[str, dict]:
        """
        Get scenario summary for each demand tier separately.
        
        Returns:
            Dict with keys 'committed', 'likely', 'exploratory', 'total'
        """
        return {
            "committed": self.get_scenario_summary(
                scenario_id, site_id, week_start, week_end, "committed"
            ),
            "likely": self.get_scenario_summary(
                scenario_id, site_id, week_start, week_end, "likely"
            ),
            "exploratory": self.get_scenario_summary(
                scenario_id, site_id, week_start, week_end, "exploratory"
            ),
            "total": self.get_scenario_summary(
                scenario_id, site_id, week_start, week_end, "all"
            ),
        }

    def run_tiered_netting_ledger(
        self,
        scenario_id: str,
    ) -> dict[str, pl.DataFrame]:
        """
        Run separate netting ledger calculations for each demand tier.
        
        Each tier has different supply allocation rules:
        - committed: Full supply access
        - likely: Only excess after committed
        - exploratory: Only excess after likely
        """
        # Get demand by tier from demand_plan
        demand = self.loader.get_table("demand_plan")
        if demand is None or len(demand) == 0:
            return {"committed": pl.DataFrame(), "likely": pl.DataFrame(), "exploratory": pl.DataFrame()}
        
        # Add demand_tier if not present
        if "demand_type" not in demand.columns:
            demand = demand.with_columns([pl.lit("committed").alias("demand_type")])
        
        # Categorize demand types into tiers
        demand = demand.with_columns([
            pl.when(pl.col("demand_type").is_in(["committed", "firm", "booked"]))
            .then(pl.lit("committed"))
            .when(pl.col("demand_type").is_in(["likely", "probable", "forecast"]))
            .then(pl.lit("likely"))
            .otherwise(pl.lit("exploratory"))
            .alias("tier")
        ])
        
        # Get supply
        supply = self.loader.get_table("supply")
        if supply is None or len(supply) == 0:
            supply = pl.DataFrame()
        
        results = {}
        remaining_supply = supply.clone() if len(supply) > 0 else pl.DataFrame()
        
        for tier in ["committed", "likely", "exploratory"]:
            tier_demand = demand.filter(pl.col("tier") == tier)
            
            if len(tier_demand) == 0 or len(remaining_supply) == 0:
                results[tier] = pl.DataFrame()
                continue
            
            # Simple netting calculation per tier
            netting = self._run_netting_for_tier(tier_demand, remaining_supply)
            results[tier] = netting
            
            # Update remaining supply for next tier (reduce by allocated qty)
            if "allocated_qty" in netting.columns:
                allocated = netting.group_by("item_id").agg([
                    pl.col("allocated_qty").sum().alias("total_allocated")
                ])
                if len(allocated) > 0 and len(remaining_supply) > 0:
                    remaining_supply = remaining_supply.join(
                        allocated, on="item_id", how="left"
                    ).with_columns([
                        (pl.col("qty") - pl.col("total_allocated").fill_null(0))
                        .clip(lower_bound=0)
                        .alias("qty")
                    ]).drop("total_allocated")
        
        return results

    def _run_netting_for_tier(
        self,
        demand: pl.DataFrame,
        supply: pl.DataFrame,
    ) -> pl.DataFrame:
        """Run simple netting for a single demand tier."""
        if len(demand) == 0:
            return pl.DataFrame()
        
        # Aggregate demand by item
        demand_agg = demand.group_by("item_id").agg([
            pl.col("qty").sum().alias("total_demand")
        ])
        
        # Aggregate supply by item
        supply_agg = pl.DataFrame()
        if len(supply) > 0 and "item_id" in supply.columns and "qty" in supply.columns:
            supply_agg = supply.group_by("item_id").agg([
                pl.col("qty").sum().alias("total_supply")
            ])
        
        # Join and calculate netting
        if len(supply_agg) > 0:
            netting = demand_agg.join(supply_agg, on="item_id", how="left")
        else:
            netting = demand_agg.with_columns([pl.lit(0).alias("total_supply")])
        
        netting = netting.with_columns([
            pl.col("total_supply").fill_null(0),
            pl.min_horizontal("total_demand", "total_supply").alias("allocated_qty"),
            (pl.col("total_demand") - pl.col("total_supply").fill_null(0))
            .clip(lower_bound=0)
            .alias("shortage_qty")
        ])
        
        return netting

    def _filter(
        self,
        df: pl.DataFrame,
        site_id: str | None,
        week_start: date | None,
        week_end: date | None,
        demand_tier: DemandTier = "all",
    ) -> pl.DataFrame:
        if site_id and "site_id" in df.columns:
            df = df.filter(pl.col("site_id") == site_id)
        if "week" in df.columns:
            if week_start is not None:
                df = df.filter(pl.col("week") >= week_start)
            if week_end is not None:
                df = df.filter(pl.col("week") <= week_end)
        # Filter by demand tier if column exists
        if demand_tier != "all" and "demand_tier" in df.columns:
            df = df.filter(pl.col("demand_tier") == demand_tier)
        return df

