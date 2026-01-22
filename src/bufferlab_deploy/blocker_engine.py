"""
Blocker Attribution Engine.

Identifies long-pole blockers and root causes for blocked kits.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.kit_engine import KitEngine
from bufferlab_deploy.netting_ledger import NettingLedger
from bufferlab_deploy.pegging_engine import PeggingEngine
from bufferlab_deploy.config import get_config
from bufferlab_deploy.sql_utils import (
    get_plan_table,
    get_bom_effective_clause,
    get_readiness_capacity_expr,
)


class BlockerEngine:
    """
    Identifies and attributes blocking causes for kit completion.
    
    Root cause categories:
    - Transfer delay: inventory exists upstream but hasn't arrived
    - Supply timing: receipts are scheduled but for later weeks
    - Pure shortage: not enough supply anywhere in the network
    - Readiness gating: site not ready (informational)
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
        self.kit_engine = KitEngine(loader)
        self.netting_ledger = NettingLedger(loader)
        self.pegging_engine = PeggingEngine(loader)
    
    def get_blocker_attribution(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get detailed blocker attribution.
        
        For each blocked kit, identifies:
        - Which items are blocking
        - Root cause category
        - Gap quantity
        - Potential fix (expedite, transfer, etc.)
        
        Returns:
            DataFrame with blocker details
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        # Get kit requirements
        kit_reqs = self.kit_engine.get_kit_requirements_detail(scenario_id)
        
        if len(kit_reqs) == 0:
            return pl.DataFrame()
        
        # Get ledger for availability
        ledger = self.netting_ledger.build_ledger(scenario_id)
        
        if len(ledger) == 0:
            return pl.DataFrame()
        
        # Identify shortfalls and attribute causes
        plan_table = get_plan_table(self.loader)
        bom_clause = get_bom_effective_clause(self.loader, "dp.week", alias="b")
        default_mw_per_kit = float(self.config.mw_per_kit.get("default", 0.5))
        readiness_expr = get_readiness_capacity_expr(self.loader, default_mw_per_kit)
        item_cols = set(self.loader.table_stats.get("item_master", {}).get("columns", []))
        unit_cost_expr = "im.unit_cost" if "unit_cost" in item_cols else "0"
        description_expr = "im.description" if "description" in item_cols else "NULL"

        if not self.loader.loaded_tables.get("lane_master"):
            blockers = self.loader.query(f"""
                WITH readiness AS (
                    SELECT 
                        site_id,
                        week,
                        {readiness_expr} as readiness_capacity_kits
                    FROM site_readiness
                    WHERE scenario_id = '{scenario_id}'
                ),
                requirements AS (
                    SELECT 
                        dp.week,
                        dp.site_id,
                        dp.kit_id,
                        COALESCE(dp.priority, {self.config.analysis.pegging.default_priority}) as priority,
                        b.child_item_id as item_id,
                        SUM(
                            LEAST(dp.kits_planned, COALESCE(r.readiness_capacity_kits, dp.kits_planned)) 
                            * b.qty_per
                        ) as required_qty
                    FROM {plan_table} dp
                    JOIN bom_kit b 
                        ON dp.kit_id = b.kit_id
                        AND {bom_clause}
                    LEFT JOIN readiness r 
                        ON dp.site_id = r.site_id 
                        AND dp.week = r.week
                    GROUP BY dp.week, dp.site_id, dp.kit_id, dp.priority, b.child_item_id
                ),
                site_inventory AS (
                    SELECT 
                        nm.site_id,
                        ip.item_id,
                        SUM(ip.usable_on_hand) as available_now
                    FROM inventory_position ip
                    JOIN node_master nm ON ip.node_id = nm.node_id
                    WHERE nm.node_type = 'site'
                    GROUP BY nm.site_id, ip.item_id
                ),
                future_supply_site AS (
                    SELECT 
                        nm.site_id,
                        s.item_id,
                        SUM(s.qty) as future_arriving
                    FROM supply s
                    JOIN node_master nm ON s.node_id = nm.node_id
                    WHERE s.status NOT IN ('cancelled', 'received')
                      AND nm.node_type = 'site'
                    GROUP BY nm.site_id, s.item_id
                ),
                shortfalls AS (
                    SELECT 
                        r.week,
                        r.site_id,
                        r.kit_id,
                        r.priority,
                        r.item_id,
                        r.required_qty,
                        COALESCE(si.available_now, 0) as available_now,
                        COALESCE(fs.future_arriving, 0) as future_arriving,
                        r.required_qty - COALESCE(si.available_now, 0) as gap_qty,
                        CASE 
                            WHEN COALESCE(si.available_now, 0) >= r.required_qty THEN 'no_gap'
                            WHEN COALESCE(si.available_now, 0) + COALESCE(fs.future_arriving, 0) >= r.required_qty
                                THEN 'supply_timing'
                            ELSE 'pure_shortage'
                        END as root_cause
                    FROM requirements r
                    LEFT JOIN site_inventory si ON r.site_id = si.site_id AND r.item_id = si.item_id
                    LEFT JOIN future_supply_site fs ON r.site_id = fs.site_id AND r.item_id = fs.item_id
                    WHERE r.required_qty > COALESCE(si.available_now, 0)
                )
                SELECT 
                    s.*,
                    im.category,
                    im.subcategory,
                    {description_expr} as description,
                    COALESCE({unit_cost_expr}, 0) as unit_cost,
                    s.gap_qty * COALESCE({unit_cost_expr}, 0) as gap_value
                FROM shortfalls s
                LEFT JOIN item_master im ON s.item_id = im.item_id
                ORDER BY s.gap_qty DESC
            """)
            return blockers

        blockers = self.loader.query(f"""
            WITH readiness AS (
                SELECT 
                    site_id,
                    week,
                    {readiness_expr} as readiness_capacity_kits
                FROM site_readiness
                WHERE scenario_id = '{scenario_id}'
            ),
            requirements AS (
                SELECT 
                    dp.week,
                    dp.site_id,
                    dp.kit_id,
                    COALESCE(dp.priority, {self.config.analysis.pegging.default_priority}) as priority,
                    b.child_item_id as item_id,
                    SUM(
                        LEAST(dp.kits_planned, COALESCE(r.readiness_capacity_kits, dp.kits_planned)) 
                        * b.qty_per
                    ) as required_qty
                FROM {plan_table} dp
                JOIN bom_kit b 
                    ON dp.kit_id = b.kit_id
                    AND {bom_clause}
                LEFT JOIN readiness r 
                    ON dp.site_id = r.site_id 
                    AND dp.week = r.week
                GROUP BY dp.week, dp.site_id, dp.kit_id, dp.priority, b.child_item_id
            ),
            site_inventory AS (
                SELECT 
                    nm.site_id,
                    ip.item_id,
                    SUM(ip.usable_on_hand) as available_now
                FROM inventory_position ip
                JOIN node_master nm ON ip.node_id = nm.node_id
                WHERE nm.node_type = 'site'
                GROUP BY nm.site_id, ip.item_id
            ),
            upstream_inventory AS (
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
            ),
            future_supply_site AS (
                SELECT 
                    nm.site_id,
                    s.item_id,
                    SUM(s.qty) as future_arriving
                FROM supply s
                JOIN node_master nm ON s.node_id = nm.node_id
                WHERE s.status NOT IN ('cancelled', 'received')
                  AND nm.node_type = 'site'
                GROUP BY nm.site_id, s.item_id
            ),
            future_supply_upstream AS (
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
            ),
            shortfalls AS (
                SELECT 
                    r.week,
                    r.site_id,
                    r.kit_id,
                    r.priority,
                    r.item_id,
                    r.required_qty,
                    COALESCE(si.available_now, 0) as available_now,
                    COALESCE(ui.upstream_qty, 0) as upstream_qty,
                    COALESCE(fs.future_arriving, 0) + COALESCE(fu.future_arriving, 0) as future_arriving,
                    r.required_qty - COALESCE(si.available_now, 0) as gap_qty,
                    CASE 
                        WHEN COALESCE(si.available_now, 0) >= r.required_qty THEN 'no_gap'
                        WHEN COALESCE(si.available_now, 0) + COALESCE(ui.upstream_qty, 0) >= r.required_qty THEN 'transfer_delay'
                        WHEN COALESCE(si.available_now, 0) + COALESCE(ui.upstream_qty, 0)
                             + COALESCE(fs.future_arriving, 0) + COALESCE(fu.future_arriving, 0) >= r.required_qty
                            THEN 'supply_timing'
                        ELSE 'pure_shortage'
                    END as root_cause
                FROM requirements r
                LEFT JOIN site_inventory si ON r.site_id = si.site_id AND r.item_id = si.item_id
                LEFT JOIN upstream_inventory ui ON r.site_id = ui.site_id AND r.item_id = ui.item_id
                LEFT JOIN future_supply_site fs ON r.site_id = fs.site_id AND r.item_id = fs.item_id
                LEFT JOIN future_supply_upstream fu ON r.site_id = fu.site_id AND r.item_id = fu.item_id
                WHERE r.required_qty > COALESCE(si.available_now, 0)
            )
            SELECT 
                s.*,
                im.category,
                im.subcategory,
                {description_expr} as description,
                COALESCE({unit_cost_expr}, 0) as unit_cost,
                s.gap_qty * COALESCE({unit_cost_expr}, 0) as gap_value
            FROM shortfalls s
            LEFT JOIN item_master im ON s.item_id = im.item_id
            ORDER BY s.gap_qty DESC
        """)
        
        return blockers
    
    def get_blocker_pareto(
        self,
        scenario_id: str | None = None,
        top_n: int = 15
    ) -> pl.DataFrame:
        """
        Get Pareto ranking of blockers.
        
        Ranks items by:
        - Number of blocked kits contributed
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
                pl.col("kit_id").n_unique().alias("kits_affected"),
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
        Get blocked kits trend by week.
        """
        pegging = self.pegging_engine.run_pegging(scenario_id)
        
        if len(pegging) == 0:
            return pl.DataFrame()
        
        weekly = (
            pegging
            .group_by("week")
            .agg([
                pl.col("deployable_kits").sum().alias("total_deployable"),
                pl.col("buildable_kits").sum().alias("total_buildable"),
                pl.col("blocked_kits").sum().alias("total_blocked"),
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
                "kits_affected": row["kits_affected"],
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
