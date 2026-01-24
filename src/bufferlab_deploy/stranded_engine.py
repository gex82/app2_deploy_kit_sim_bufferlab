"""
Stranded Inventory Engine.

Identifies inventory that is available but cannot be consumed due to blockers.
"""

from __future__ import annotations

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.square_set_engine import SquareSetEngine
from bufferlab_deploy.netting_ledger import NettingLedger
from bufferlab_deploy.pegging_engine import PeggingEngine
from bufferlab_deploy.blocker_engine import BlockerEngine
from bufferlab_deploy.config import get_config


class StrandedEngine:
    """
    Compute stranded inventory risk.
    """

    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
        self.square_set_engine = SquareSetEngine(loader)
        self.pegging_engine = PeggingEngine(loader)
        self.netting_ledger = NettingLedger(loader)
        self.blocker_engine = BlockerEngine(loader)

    def get_stranded_inventory(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Identify stranded inventory by site/week/item.
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario

        pegging = self.pegging_engine.run_pegging(scenario_id)
        if len(pegging) == 0:
            return pl.DataFrame()

        blocked_sets = pegging.filter(pl.col("blocked_sets") > 0).select(
            ["week", "site_id", "square_set_id"]
        ).unique()

        requirements = self.square_set_engine.get_square_set_requirements_detail(scenario_id)
        if len(requirements) == 0:
            return pl.DataFrame()

        blocked_items = []
        if len(blocked_sets) > 0:
            blocked_items.append(
                requirements
                .join(blocked_sets, on=["week", "site_id", "square_set_id"], how="inner")
                .filter(pl.col("kit_criticality") == "blocking")
                .select(["week", "site_id", "item_id"])
                .unique()
                .with_columns([pl.lit("blocked_set").alias("stranding_reason")])
            )

        domain_readiness = self.square_set_engine.get_domain_readiness(scenario_id)
        convergence = self.square_set_engine.get_convergence_summary(scenario_id)
        if len(domain_readiness) > 0 and len(convergence) > 0:
            partial_ready = (
                domain_readiness
                .join(
                    convergence.select(["week", "site_id", "square_set_id", "all_domains_ready"]),
                    on=["week", "site_id", "square_set_id"],
                    how="left",
                )
                .filter(
                    (pl.col("all_domains_ready") == False) &
                    (pl.col("is_ready") == True)
                )
            )
            if len(partial_ready) > 0:
                blocked_items.append(
                    requirements
                    .join(
                        partial_ready.select(["week", "site_id", "square_set_id", "domain"]),
                        on=["week", "site_id", "square_set_id", "domain"],
                        how="inner",
                    )
                    .filter(pl.col("kit_criticality") == "blocking")
                    .select(["week", "site_id", "item_id"])
                    .unique()
                    .with_columns([pl.lit("partial_domain_ready").alias("stranding_reason")])
                )

        if not blocked_items:
            return pl.DataFrame()

        blocked_items_df = pl.concat(blocked_items)
        blocked_items_df = (
            blocked_items_df
            .group_by(["week", "site_id", "item_id"])
            .agg([
                pl.col("stranding_reason").unique().alias("stranding_reasons"),
            ])
        )

        ledger = self.netting_ledger.build_ledger(scenario_id)
        if len(ledger) == 0:
            return pl.DataFrame()

        stranded = (
            ledger
            .join(blocked_items_df, on=["week", "site_id", "item_id"], how="inner")
            .filter(pl.col("closing_balance") > 0)
            .with_columns([
                pl.col("closing_balance").alias("stranded_units"),
            ])
        )

        if len(stranded) == 0:
            return pl.DataFrame()

        item_master = self.loader.get_table("item_master")
        if item_master is not None:
            columns = set(item_master.columns)
            select_cols = ["item_id", "category", "subcategory"]
            if "description" in columns:
                select_cols.append("description")
            if "unit_cost" in columns:
                select_cols.append("unit_cost")
            stranded = stranded.join(
                item_master.select(select_cols),
                on="item_id",
                how="left"
            )

        aging = self._get_aging_by_site_item()
        if len(aging) > 0:
            stranded = stranded.join(
                aging,
                on=["site_id", "item_id"],
                how="left"
            )

        if "unit_cost" not in stranded.columns:
            stranded = stranded.with_columns([pl.lit(0).alias("unit_cost")])

        stranded = stranded.with_columns([
            (pl.col("stranded_units") * pl.col("unit_cost").fill_null(0)).alias("stranded_value")
        ])

        stranded = stranded.with_columns([
            pl.col("stranding_reasons").list.join(",").fill_null("").alias("stranding_reasons")
        ])

        blocked_by = self._get_blocked_by_lookup(scenario_id)
        if len(blocked_by) > 0:
            stranded = stranded.join(
                blocked_by,
                on=["week", "site_id"],
                how="left"
            )

        return stranded

    def get_stranded_summary(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Summarize stranded inventory by site and item.
        """
        stranded = self.get_stranded_inventory(scenario_id)
        if len(stranded) == 0:
            return pl.DataFrame()

        summary = (
            stranded
            .group_by(["site_id", "item_id", "category", "subcategory"])
            .agg([
                pl.col("stranded_units").sum().alias("stranded_units"),
                pl.col("stranded_value").sum().alias("stranded_value"),
                pl.col("week").n_unique().alias("weeks_stranded"),
                pl.col("aging_days").max().alias("max_aging_days"),
            ])
            .sort("stranded_value", descending=True)
        )

        return summary

    def _get_aging_by_site_item(self) -> pl.DataFrame:
        """
        Get aging days by site/item if present.
        """
        if not self.loader.loaded_tables.get("inventory_position"):
            return pl.DataFrame()
        columns = set(self.loader.table_stats.get("inventory_position", {}).get("columns", []))
        if "aging_days" not in columns:
            return pl.DataFrame()

        return self.loader.query("""
            SELECT 
                nm.site_id,
                ip.item_id,
                MAX(ip.aging_days) as aging_days
            FROM inventory_position ip
            JOIN node_master nm ON ip.node_id = nm.node_id
            WHERE ip.aging_days IS NOT NULL
            GROUP BY nm.site_id, ip.item_id
        """)

    def _get_blocked_by_lookup(self, scenario_id: str) -> pl.DataFrame:
        """
        Map site/week to the top blocking item.
        """
        blockers = self.blocker_engine.get_blocker_attribution(scenario_id)
        if len(blockers) == 0:
            return pl.DataFrame()

        top_blocker = (
            blockers
            .sort("gap_qty", descending=True)
            .group_by(["week", "site_id"])
            .agg([
                pl.col("item_id").first().alias("blocked_by_item"),
                pl.col("root_cause").first().alias("blocked_by_cause"),
            ])
        )

        return top_blocker
