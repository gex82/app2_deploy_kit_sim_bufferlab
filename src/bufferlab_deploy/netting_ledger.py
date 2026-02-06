"""
Time-Phased Netting Ledger.

Maintains inventory ledger by site/item/week with no double-counting.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.transfer_model import TransferModel
from bufferlab_deploy.square_set_engine import SquareSetEngine
from bufferlab_deploy.config import get_config
from bufferlab_deploy.sql_utils import get_supply_week_expr, get_plan_table


class NettingLedger:
    """
    Time-phased inventory ledger that tracks availability week by week.
    
    Key rule: NO DOUBLE COUNTING across weeks.
    available[t] = available[t-1] + arrivals[t] - consumption[t]
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
        self.transfer_model = TransferModel(loader)
        self.square_set_engine = SquareSetEngine(loader)
    
    def build_ledger(
        self,
        scenario_id: str | None = None,
        num_weeks: int | None = None,
        demand_tier: str | None = None,
    ) -> pl.DataFrame:
        """
        Build the complete netting ledger.
        
        Args:
            scenario_id: Scenario to use
            num_weeks: Number of weeks to project
        
        Returns:
            DataFrame with [week, site_id, item_id, opening_balance, arrivals, available, 
                           required, allocated, closing_balance, backlog]
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        if num_weeks is None:
            num_weeks = self.config.analysis.horizon_weeks

        plan_table = get_plan_table(self.loader)
        if not self.loader.loaded_tables.get(plan_table):
            return pl.DataFrame()

        week_expr = get_supply_week_expr(self.loader)
        if week_expr == "NULL":
            weeks_df = self.loader.query(f"SELECT DISTINCT week FROM {plan_table} ORDER BY week")
        else:
            weeks_df = self.loader.query(f"""
                SELECT DISTINCT week FROM {plan_table}
                UNION
                SELECT DISTINCT {week_expr} as week FROM supply
                WHERE {week_expr} IS NOT NULL
                ORDER BY week
            """)

        if len(weeks_df) == 0:
            return pl.DataFrame()

        weeks_df = weeks_df.with_columns(
            pl.col("week").cast(pl.Date, strict=False)
        ).drop_nulls(["week"])

        if len(weeks_df) == 0:
            return pl.DataFrame()

        weeks = weeks_df["week"].to_list()
        weeks = weeks[:num_weeks]
        weeks_df = pl.DataFrame({"week": weeks}).with_columns(
            pl.col("week").cast(pl.Date, strict=False)
        )

        requirements = self.square_set_engine.get_aggregated_requirements(
            scenario_id,
            demand_tier=demand_tier,
        )
        arrivals = self.transfer_model.calculate_arrivals_to_site(weeks)
        starting_inv = self.transfer_model.get_starting_inventory_at_site()

        if len(requirements) > 0 and "week" in requirements.columns:
            requirements = requirements.with_columns(
                pl.col("week").cast(pl.Date, strict=False)
            )
        if len(arrivals) > 0 and "week" in arrivals.columns:
            arrivals = arrivals.with_columns(
                pl.col("week").cast(pl.Date, strict=False)
            )

        if len(requirements) == 0:
            requirements = pl.DataFrame(
                schema={
                    "week": pl.Date,
                    "site_id": pl.Utf8,
                    "item_id": pl.Utf8,
                    "total_required": pl.Float64,
                }
            )
        if len(arrivals) == 0:
            arrivals = pl.DataFrame(
                schema={
                    "week": pl.Date,
                    "site_id": pl.Utf8,
                    "item_id": pl.Utf8,
                    "total_arrivals": pl.Float64,
                }
            )
        if len(starting_inv) == 0:
            starting_inv = pl.DataFrame(
                schema={
                    "site_id": pl.Utf8,
                    "item_id": pl.Utf8,
                    "site_inventory": pl.Float64,
                }
            )

        site_items = []
        if len(requirements) > 0:
            site_items.append(requirements.select(["site_id", "item_id"]))
        if len(arrivals) > 0:
            site_items.append(arrivals.select(["site_id", "item_id"]))
        if len(starting_inv) > 0:
            site_items.append(starting_inv.select(["site_id", "item_id"]))

        if not site_items:
            return pl.DataFrame()

        site_items_df = pl.concat(site_items).unique()

        base = site_items_df.join(weeks_df, how="cross")
        base = base.join(arrivals, on=["week", "site_id", "item_id"], how="left")
        base = base.join(requirements, on=["week", "site_id", "item_id"], how="left")
        base = base.join(starting_inv, on=["site_id", "item_id"], how="left")

        min_week = weeks[0]
        base = base.with_columns([
            pl.when(pl.col("week") == min_week)
            .then(pl.col("site_inventory"))
            .otherwise(0)
            .alias("opening_from_inv")
        ])

        base = base.with_columns([
            pl.col("total_arrivals").fill_null(0).alias("arrivals"),
            pl.col("total_required").fill_null(0).alias("required"),
            pl.col("opening_from_inv").fill_null(0).alias("opening_from_inv"),
        ])

        base = base.select([
            "week", "site_id", "item_id",
            "opening_from_inv", "arrivals", "required"
        ]).with_columns([
            pl.col("opening_from_inv").cast(pl.Float64, strict=False),
            pl.col("arrivals").cast(pl.Float64, strict=False),
            pl.col("required").cast(pl.Float64, strict=False),
        ])

        return self._compute_running_balances(base)

    def build_tiered_ledger(
        self,
        scenario_id: str | None = None,
        num_weeks: int | None = None,
    ) -> dict[str, pl.DataFrame]:
        """
        Build tiered netting ledgers with demand tier priority.
        
        Committed consumes first, residual flows to likely, then exploratory.
        Returns a dict with keys: committed, likely, exploratory.
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        if num_weeks is None:
            num_weeks = self.config.analysis.horizon_weeks

        plan_table = get_plan_table(self.loader)
        if not self.loader.loaded_tables.get(plan_table):
            return {
                "committed": pl.DataFrame(),
                "likely": pl.DataFrame(),
                "exploratory": pl.DataFrame(),
            }

        week_expr = get_supply_week_expr(self.loader)
        if week_expr == "NULL":
            weeks_df = self.loader.query(f"SELECT DISTINCT week FROM {plan_table} ORDER BY week")
        else:
            weeks_df = self.loader.query(f"""
                SELECT DISTINCT week FROM {plan_table}
                UNION
                SELECT DISTINCT {week_expr} as week FROM supply
                WHERE {week_expr} IS NOT NULL
                ORDER BY week
            """)

        if len(weeks_df) == 0:
            return {
                "committed": pl.DataFrame(),
                "likely": pl.DataFrame(),
                "exploratory": pl.DataFrame(),
            }

        weeks_df = weeks_df.with_columns(
            pl.col("week").cast(pl.Date, strict=False)
        ).drop_nulls(["week"])

        if len(weeks_df) == 0:
            return {
                "committed": pl.DataFrame(),
                "likely": pl.DataFrame(),
                "exploratory": pl.DataFrame(),
            }

        weeks = weeks_df["week"].to_list()
        weeks = weeks[:num_weeks]
        weeks_df = pl.DataFrame({"week": weeks}).with_columns(
            pl.col("week").cast(pl.Date, strict=False)
        )

        arrivals = self.transfer_model.calculate_arrivals_to_site(weeks)
        starting_inv = self.transfer_model.get_starting_inventory_at_site()

        if len(arrivals) > 0 and "week" in arrivals.columns:
            arrivals = arrivals.with_columns(
                pl.col("week").cast(pl.Date, strict=False)
            )

        if len(arrivals) == 0:
            arrivals = pl.DataFrame(
                schema={
                    "week": pl.Date,
                    "site_id": pl.Utf8,
                    "item_id": pl.Utf8,
                    "total_arrivals": pl.Float64,
                }
            )
        if len(starting_inv) == 0:
            starting_inv = pl.DataFrame(
                schema={
                    "site_id": pl.Utf8,
                    "item_id": pl.Utf8,
                    "site_inventory": pl.Float64,
                }
            )

        requirements_by_tier: dict[str, pl.DataFrame] = {}
        for tier in ["committed", "likely", "exploratory"]:
            req = self.square_set_engine.get_aggregated_requirements(
                scenario_id,
                demand_tier=tier,
            )
            if len(req) > 0 and "week" in req.columns:
                req = req.with_columns(
                    pl.col("week").cast(pl.Date, strict=False)
                )
            if len(req) == 0:
                req = pl.DataFrame(
                    schema={
                        "week": pl.Date,
                        "site_id": pl.Utf8,
                        "item_id": pl.Utf8,
                        "total_required": pl.Float64,
                    }
                )
            requirements_by_tier[tier] = req.rename({"total_required": f"required_{tier}"})

        site_items = []
        for req in requirements_by_tier.values():
            if len(req) > 0:
                site_items.append(req.select(["site_id", "item_id"]))
        if len(arrivals) > 0:
            site_items.append(arrivals.select(["site_id", "item_id"]))
        if len(starting_inv) > 0:
            site_items.append(starting_inv.select(["site_id", "item_id"]))

        if not site_items:
            return {
                "committed": pl.DataFrame(),
                "likely": pl.DataFrame(),
                "exploratory": pl.DataFrame(),
            }

        site_items_df = pl.concat(site_items).unique()

        base = site_items_df.join(weeks_df, how="cross")
        base = base.join(arrivals, on=["week", "site_id", "item_id"], how="left")
        for tier, req in requirements_by_tier.items():
            base = base.join(req, on=["week", "site_id", "item_id"], how="left")
        base = base.join(starting_inv, on=["site_id", "item_id"], how="left")

        min_week = weeks[0]
        base = base.with_columns([
            pl.when(pl.col("week") == min_week)
            .then(pl.col("site_inventory"))
            .otherwise(0)
            .alias("opening_from_inv")
        ])

        base = base.with_columns([
            pl.col("total_arrivals").fill_null(0).alias("arrivals"),
            pl.col("required_committed").fill_null(0).alias("required_committed"),
            pl.col("required_likely").fill_null(0).alias("required_likely"),
            pl.col("required_exploratory").fill_null(0).alias("required_exploratory"),
            pl.col("opening_from_inv").fill_null(0).alias("opening_from_inv"),
        ])

        base = base.select([
            "week", "site_id", "item_id",
            "opening_from_inv", "arrivals",
            "required_committed", "required_likely", "required_exploratory",
        ]).with_columns([
            pl.col("opening_from_inv").cast(pl.Float64, strict=False),
            pl.col("arrivals").cast(pl.Float64, strict=False),
            pl.col("required_committed").cast(pl.Float64, strict=False),
            pl.col("required_likely").cast(pl.Float64, strict=False),
            pl.col("required_exploratory").cast(pl.Float64, strict=False),
        ])

        return self._compute_tiered_running_balances(base)

    def _compute_tiered_running_balances(
        self,
        ledger_df: pl.DataFrame,
    ) -> dict[str, pl.DataFrame]:
        """
        Compute running balances across tiers with strict consumption order.
        """
        if len(ledger_df) == 0:
            return {
                "committed": ledger_df,
                "likely": ledger_df,
                "exploratory": ledger_df,
            }

        df = ledger_df.sort(["site_id", "item_id", "week"]).to_pandas()
        results: dict[str, list[dict[str, Any]]] = {
            "committed": [],
            "likely": [],
            "exploratory": [],
        }

        for (site_id, item_id), group in df.groupby(["site_id", "item_id"]):
            opening_balance = 0.0
            for _, row in group.iterrows():
                if row["opening_from_inv"] > 0:
                    opening_balance = float(row["opening_from_inv"])

                arrivals = float(row["arrivals"])
                available = opening_balance + arrivals

                committed_required = float(row["required_committed"])
                committed_allocated = min(committed_required, max(available, 0))
                committed_closing = available - committed_allocated
                committed_shortfall = committed_required - committed_allocated

                results["committed"].append({
                    "week": row["week"],
                    "site_id": site_id,
                    "item_id": item_id,
                    "demand_tier": "committed",
                    "opening_balance": opening_balance,
                    "arrivals": arrivals,
                    "available": available,
                    "required": committed_required,
                    "allocated": committed_allocated,
                    "closing_balance": committed_closing,
                    "shortfall": committed_shortfall,
                })

                likely_available = committed_closing
                likely_required = float(row["required_likely"])
                likely_allocated = min(likely_required, max(likely_available, 0))
                likely_closing = likely_available - likely_allocated
                likely_shortfall = likely_required - likely_allocated

                results["likely"].append({
                    "week": row["week"],
                    "site_id": site_id,
                    "item_id": item_id,
                    "demand_tier": "likely",
                    "opening_balance": likely_available,
                    "arrivals": 0.0,
                    "available": likely_available,
                    "required": likely_required,
                    "allocated": likely_allocated,
                    "closing_balance": likely_closing,
                    "shortfall": likely_shortfall,
                })

                exploratory_available = likely_closing
                exploratory_required = float(row["required_exploratory"])
                exploratory_allocated = min(exploratory_required, max(exploratory_available, 0))
                exploratory_closing = exploratory_available - exploratory_allocated
                exploratory_shortfall = exploratory_required - exploratory_allocated

                results["exploratory"].append({
                    "week": row["week"],
                    "site_id": site_id,
                    "item_id": item_id,
                    "demand_tier": "exploratory",
                    "opening_balance": exploratory_available,
                    "arrivals": 0.0,
                    "available": exploratory_available,
                    "required": exploratory_required,
                    "allocated": exploratory_allocated,
                    "closing_balance": exploratory_closing,
                    "shortfall": exploratory_shortfall,
                })

                opening_balance = exploratory_closing

        return {
            "committed": pl.DataFrame(results["committed"]),
            "likely": pl.DataFrame(results["likely"]),
            "exploratory": pl.DataFrame(results["exploratory"]),
        }
    
    def _compute_running_balances(self, ledger_df: pl.DataFrame) -> pl.DataFrame:
        """
        Compute running balances with proper week-over-week carryforward.
        
        Uses cumulative sum to avoid double counting.
        """
        if len(ledger_df) == 0:
            return ledger_df
        
        df = ledger_df.sort(["site_id", "item_id", "week"]).to_pandas()
        results: list[dict[str, Any]] = []

        for (site_id, item_id), group in df.groupby(["site_id", "item_id"]):
            opening_balance = 0.0
            for _, row in group.iterrows():
                if row["opening_from_inv"] > 0:
                    opening_balance = float(row["opening_from_inv"])

                arrivals = float(row["arrivals"])
                required = float(row["required"])
                available = opening_balance + arrivals
                allocated = min(required, max(available, 0))
                closing_balance = available - allocated
                shortfall = required - allocated

                results.append({
                    "week": row["week"],
                    "site_id": site_id,
                    "item_id": item_id,
                    "opening_balance": opening_balance,
                    "arrivals": arrivals,
                    "available": available,
                    "required": required,
                    "allocated": allocated,
                    "closing_balance": closing_balance,
                    "shortfall": shortfall,
                })

                opening_balance = closing_balance

        return pl.DataFrame(results)
    
    def get_availability_summary(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get summarized availability by site/week.
        
        Returns aggregated view for dashboard.
        """
        ledger = self.build_ledger(scenario_id)
        
        if len(ledger) == 0:
            return pl.DataFrame()
        
        summary = (
            ledger
            .group_by(["week", "site_id"])
            .agg([
                pl.col("required").sum().alias("total_required"),
                pl.col("allocated").sum().alias("total_allocated"),
                pl.col("shortfall").sum().alias("total_shortfall"),
                pl.col("item_id").n_unique().alias("num_items"),
                (pl.col("shortfall") > 0).sum().alias("items_short"),
            ])
            .sort(["site_id", "week"])
        )
        
        return summary
    
    def get_constrained_items(
        self,
        scenario_id: str | None = None,
        top_n: int = 20
    ) -> pl.DataFrame:
        """
        Get most constrained items (highest shortfall).
        
        Returns:
            Top N items by total shortfall
        """
        ledger = self.build_ledger(scenario_id)
        
        if len(ledger) == 0:
            return pl.DataFrame()
        
        constrained = (
            ledger
            .filter(pl.col("shortfall") > 0)
            .group_by(["site_id", "item_id"])
            .agg([
                pl.col("shortfall").sum().alias("total_shortfall"),
                pl.col("required").sum().alias("total_required"),
                (pl.col("shortfall") > 0).sum().alias("weeks_short"),
            ])
            .sort("total_shortfall", descending=True)
            .head(top_n)
        )
        
        # Join with item master for details
        if len(constrained) > 0:
            item_master = self.loader.get_table("item_master")
            if item_master is not None:
                constrained = constrained.join(
                    item_master.select(["item_id", "category", "subcategory", "description"]),
                    on="item_id",
                    how="left"
                )
        
        return constrained
