"""
Blocker Attribution Engine.

Identifies long-pole blockers and root causes for blocked square-sets.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.square_set_engine import SquareSetEngine
from bufferlab_deploy.netting_ledger import NettingLedger
from bufferlab_deploy.pegging_engine import PeggingEngine
from bufferlab_deploy.config import get_config


class BlockerEngine:
    """
    Identifies and attributes blocking causes for square-set completion.
    
    Root cause categories:
    - Transfer delay: inventory exists upstream but hasn't arrived
    - Supply timing: receipts are scheduled but for later weeks
    - Pure shortage: not enough supply anywhere in the network
    - Readiness gating: site not ready (informational)
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
        self.square_set_engine = SquareSetEngine(loader)
        self.netting_ledger = NettingLedger(loader)
        self.pegging_engine = PeggingEngine(loader)
    
    def get_blocker_attribution(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get detailed blocker attribution.
        
        For each blocked square-set, identifies:
        - Which items are blocking
        - Root cause category
        - Gap quantity
        - Potential fix (expedite, transfer, etc.)
        
        Returns:
            DataFrame with blocker details
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario

        requirements = self.square_set_engine.get_square_set_requirements_detail(scenario_id)
        if len(requirements) == 0:
            return pl.DataFrame()

        requirements = (
            requirements
            .filter(pl.col("kit_criticality") == "blocking")
            .group_by([
                "week",
                "site_id",
                "square_set_id",
                "domain",
                "priority",
                "demand_tier",
                "item_id",
            ])
            .agg([
                pl.col("required_qty").sum().alias("required_qty"),
                pl.col("category").first().alias("category"),
                pl.col("subcategory").first().alias("subcategory"),
            ])
        )

        if len(requirements) == 0:
            return pl.DataFrame()

        site_inventory = pl.DataFrame()
        if self.loader.loaded_tables.get("inventory_position") and self.loader.loaded_tables.get("node_master"):
            site_inventory = self.loader.query("""
                SELECT 
                    nm.site_id,
                    ip.item_id,
                    SUM(ip.usable_on_hand) as available_now
                FROM inventory_position ip
                JOIN node_master nm ON ip.node_id = nm.node_id
                WHERE nm.node_type = 'site'
                GROUP BY nm.site_id, ip.item_id
            """)

        future_supply_site = pl.DataFrame()
        if self.loader.loaded_tables.get("supply") and self.loader.loaded_tables.get("node_master"):
            future_supply_site = self.loader.query("""
                SELECT 
                    nm.site_id,
                    s.item_id,
                    SUM(s.qty) as future_arriving
                FROM supply s
                JOIN node_master nm ON s.node_id = nm.node_id
                WHERE s.status NOT IN ('cancelled', 'received')
                  AND nm.node_type = 'site'
                GROUP BY nm.site_id, s.item_id
            """).rename({"future_arriving": "future_arriving_site"})

        upstream_inventory = pl.DataFrame()
        future_supply_upstream = pl.DataFrame()
        if (
            self.loader.loaded_tables.get("lane_master")
            and self.loader.loaded_tables.get("node_master")
            and self.loader.loaded_tables.get("inventory_position")
        ):
            upstream_inventory = self.loader.query("""
                SELECT 
                    l.site_id,
                    ip.item_id,
                    SUM(ip.usable_on_hand) as upstream_qty
                FROM inventory_position ip
                JOIN node_master nm ON ip.node_id = nm.node_id
                JOIN (
                    SELECT DISTINCT 
                        lm.from_node_id,
                        nm_to.site_id
                    FROM lane_master lm
                    JOIN node_master nm_to ON lm.to_node_id = nm_to.node_id
                    WHERE nm_to.node_type = 'site'
                ) l ON ip.node_id = l.from_node_id
                WHERE nm.node_type IN ('integration', 'regional')
                GROUP BY l.site_id, ip.item_id
            """)
        if (
            self.loader.loaded_tables.get("lane_master")
            and self.loader.loaded_tables.get("node_master")
            and self.loader.loaded_tables.get("supply")
        ):
            future_supply_upstream = self.loader.query("""
                SELECT 
                    l.site_id,
                    s.item_id,
                    SUM(s.qty) as future_arriving
                FROM supply s
                JOIN node_master nm ON s.node_id = nm.node_id
                JOIN (
                    SELECT DISTINCT 
                        lm.from_node_id,
                        nm_to.site_id
                    FROM lane_master lm
                    JOIN node_master nm_to ON lm.to_node_id = nm_to.node_id
                    WHERE nm_to.node_type = 'site'
                ) l ON s.node_id = l.from_node_id
                WHERE s.status NOT IN ('cancelled', 'received')
                  AND nm.node_type IN ('integration', 'regional')
                GROUP BY l.site_id, s.item_id
            """)

        blockers = requirements
        if len(site_inventory) > 0:
            blockers = blockers.join(site_inventory, on=["site_id", "item_id"], how="left")
        else:
            blockers = blockers.with_columns([pl.lit(0).alias("available_now")])

        if len(upstream_inventory) > 0:
            blockers = blockers.join(upstream_inventory, on=["site_id", "item_id"], how="left")
        else:
            blockers = blockers.with_columns([pl.lit(0).alias("upstream_qty")])

        if len(future_supply_site) > 0:
            blockers = blockers.join(future_supply_site, on=["site_id", "item_id"], how="left")
        else:
            blockers = blockers.with_columns([pl.lit(0).alias("future_arriving_site")])

        if len(future_supply_upstream) > 0:
            blockers = blockers.join(
                future_supply_upstream.rename({"future_arriving": "future_arriving_upstream"}),
                on=["site_id", "item_id"],
                how="left",
            )
        else:
            blockers = blockers.with_columns([pl.lit(0).alias("future_arriving_upstream")])

        blockers = blockers.with_columns([
            pl.col("available_now").fill_null(0),
            pl.col("upstream_qty").fill_null(0),
            pl.col("future_arriving_site").fill_null(0),
            pl.col("future_arriving_upstream").fill_null(0),
        ]).with_columns([
            (pl.col("future_arriving_site") + pl.col("future_arriving_upstream"))
            .alias("future_arriving"),
            (pl.col("required_qty") - pl.col("available_now")).alias("gap_qty"),
        ])

        blockers = blockers.with_columns([
            pl.when(pl.col("available_now") >= pl.col("required_qty"))
            .then(pl.lit("no_gap"))
            .when(pl.col("available_now") + pl.col("upstream_qty") >= pl.col("required_qty"))
            .then(pl.lit("transfer_delay"))
            .when(
                pl.col("available_now") + pl.col("upstream_qty") + pl.col("future_arriving")
                >= pl.col("required_qty")
            )
            .then(pl.lit("supply_timing"))
            .otherwise(pl.lit("pure_shortage"))
            .alias("root_cause")
        ])

        blockers = blockers.filter(pl.col("required_qty") > pl.col("available_now"))

        item_master = self.loader.get_table("item_master")
        if item_master is not None and len(item_master) > 0:
            select_cols = ["item_id"]
            if "description" in item_master.columns:
                select_cols.append("description")
            if "unit_cost" in item_master.columns:
                select_cols.append("unit_cost")
            blockers = blockers.join(
                item_master.select(select_cols),
                on="item_id",
                how="left",
            )
        else:
            blockers = blockers.with_columns([
                pl.lit(None).alias("description"),
                pl.lit(0).alias("unit_cost"),
            ])

        if "unit_cost" not in blockers.columns:
            blockers = blockers.with_columns([pl.lit(0).alias("unit_cost")])

        blockers = blockers.with_columns([
            (pl.col("gap_qty") * pl.col("unit_cost").fill_null(0)).alias("gap_value")
        ])

        return blockers.sort("gap_qty", descending=True)
    
    def get_blocker_pareto(
        self,
        scenario_id: str | None = None,
        top_n: int = 15
    ) -> pl.DataFrame:
        """
        Get Pareto ranking of blockers.
        
        Ranks items by:
        - Number of blocked square-sets contributed
        - Total shortage gap
        """
        blockers = self.get_blocker_attribution(scenario_id)
        
        if len(blockers) == 0:
            return pl.DataFrame()
        
        pareto = (
            blockers
            .filter(pl.col("root_cause") != "no_gap")
            .group_by(["item_id", "category", "subcategory", "root_cause"])
            .agg([
                pl.col("gap_qty").sum().alias("total_gap_qty"),
                pl.col("gap_value").sum().alias("total_gap_value"),
                pl.col("square_set_id").n_unique().alias("square_sets_affected"),
                pl.col("site_id").n_unique().alias("sites_affected"),
                pl.col("week").n_unique().alias("weeks_affected"),
            ])
            .sort("total_gap_qty", descending=True)
            .head(top_n)
            .with_row_count("rank", offset=1)
        )
        
        # Add cumulative percentage
        if len(pareto) > 0:
            total_gap = pareto["total_gap_qty"].sum()
            pareto = pareto.with_columns([
                (pl.col("total_gap_qty").cum_sum() / total_gap * 100).round(1).alias("cumulative_pct")
            ])
        
        return pareto
    
    def get_weekly_blocked_kits(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get blocked square-sets trend by week.
        """
        pegging = self.pegging_engine.run_pegging(scenario_id)
        
        if len(pegging) == 0:
            return pl.DataFrame()
        
        weekly = (
            pegging
            .group_by("week")
            .agg([
                pl.col("deployable_sets").sum().alias("total_deployable"),
                pl.col("buildable_sets").sum().alias("total_buildable"),
                pl.col("blocked_sets").sum().alias("total_blocked"),
            ])
            .with_columns([
                (pl.col("total_buildable") / pl.col("total_deployable") * 100)
                .round(1)
                .alias("completion_rate")
            ])
            .sort("week")
        )
        
        return weekly
    
    def get_fix_recommendations(
        self,
        scenario_id: str | None = None,
        top_n: int = 10
    ) -> list[dict[str, Any]]:
        """
        Generate fix recommendations for top blockers.
        """
        pareto = self.get_blocker_pareto(scenario_id, top_n)
        
        if len(pareto) == 0:
            return []
        
        recommendations = []
        
        for row in pareto.iter_rows(named=True):
            recommendation = {
                "rank": row["rank"],
                "item_id": row["item_id"],
                "category": row.get("category", "Unknown"),
                "root_cause": row["root_cause"],
                "gap_qty": row["total_gap_qty"],
                "gap_value": row.get("total_gap_value", 0),
                "square_sets_affected": row["square_sets_affected"],
            }
            
            # Generate fix text based on root cause
            if row["root_cause"] == "supply_timing":
                recommendation["fix"] = f"Expedite {int(row['total_gap_qty'])} units to arrive earlier"
                recommendation["fix_type"] = "expedite"
            elif row["root_cause"] == "pure_shortage":
                recommendation["fix"] = f"Source additional {int(row['total_gap_qty'])} units"
                recommendation["fix_type"] = "source"
            elif row["root_cause"] == "transfer_delay":
                recommendation["fix"] = f"Initiate transfer of {int(row['total_gap_qty'])} units from upstream"
                recommendation["fix_type"] = "transfer"
            else:
                recommendation["fix"] = "Review allocation logic"
                recommendation["fix_type"] = "review"
            
            recommendations.append(recommendation)
        
        return recommendations
