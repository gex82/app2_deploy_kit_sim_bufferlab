"""
Buffer target recommendation engine (v1 policy-based heuristic).
"""

from __future__ import annotations

from datetime import date

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.config import get_config, BufferTarget


class BufferEngine:
    """
    Policy-based buffer recommendation engine.
    """

    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()

    def get_buffer_targets(self) -> pl.DataFrame:
        """
        Generate buffer targets by segment.
        """
        items = self._get_item_segments()
        if len(items) == 0:
            return pl.DataFrame()

        supply_risk = self._get_supply_risk()
        lead_times = self._get_lead_time_p95()
        lifecycle = self._get_lifecycle_risk()

        if len(supply_risk) > 0:
            items = items.join(supply_risk, on="item_id", how="left")
        if len(lead_times) > 0:
            items = items.join(lead_times, on="item_id", how="left")
        if len(lifecycle) > 0:
            items = items.join(lifecycle, on="item_id", how="left")

        items = self._assign_risk_flags(items)
        segments = self._summarize_segments(items)

        rows: list[dict[str, object]] = []
        for row in segments.iter_rows(named=True):
            target = self._select_target(row)
            rationale = self._build_rationale(row, target)
            rows.append({
                "segment": self._segment_name(row),
                "category": row.get("category", "Unknown"),
                "kit_criticality": row.get("kit_criticality", "blocking"),
                "supply_risk": row.get("supply_risk", "unknown"),
                "lifecycle_risk": row.get("lifecycle_risk", "unknown"),
                "items_count": row.get("items_count", 0),
                "min_weeks": target.min,
                "max_weeks": target.max,
                "location": target.location,
                "rationale": rationale,
            })

        return pl.DataFrame(rows)

    def _get_item_segments(self) -> pl.DataFrame:
        """
        Get distinct BOM items with category and criticality.
        """
        columns = set(self.loader.table_stats.get("item_master", {}).get("columns", []))
        unit_cost_expr = "im.unit_cost" if "unit_cost" in columns else "NULL as unit_cost"

        return self.loader.query(f"""
            SELECT DISTINCT
                b.child_item_id as item_id,
                COALESCE(b.kit_criticality, 'blocking') as kit_criticality,
                im.category,
                im.subcategory,
                {unit_cost_expr}
            FROM bom_kit b
            LEFT JOIN item_master im ON b.child_item_id = im.item_id
        """)

    def _get_supply_risk(self) -> pl.DataFrame:
        """
        Compute supply risk indicators from supply table.
        """
        if not self.loader.loaded_tables.get("supply"):
            return pl.DataFrame()

        columns = set(self.loader.table_stats.get("supply", {}).get("columns", []))
        has_alloc = "allocation_flag" in columns
        has_conf = "confidence_weight" in columns

        alloc_expr = "MAX(CASE WHEN allocation_flag THEN 1 ELSE 0 END) as has_allocation" if has_alloc else "0 as has_allocation"
        conf_expr = "AVG(confidence_weight) as avg_confidence" if has_conf else "NULL as avg_confidence"

        return self.loader.query(f"""
            SELECT 
                item_id,
                {alloc_expr},
                {conf_expr}
            FROM supply
            WHERE status NOT IN ('cancelled', 'received')
            GROUP BY item_id
        """)

    def _get_lead_time_p95(self) -> pl.DataFrame:
        """
        Compute lead time p95 by item if table exists.
        """
        table = None
        if self.loader.loaded_tables.get("lead_time_distribution"):
            table = "lead_time_distribution"
        elif self.loader.loaded_tables.get("lead_time_history"):
            table = "lead_time_history"

        if table is None:
            return pl.DataFrame()

        columns = set(self.loader.table_stats.get(table, {}).get("columns", []))
        if "p95" in columns:
            expr = "AVG(p95) as lead_time_p95"
        elif "lead_time_p95" in columns:
            expr = "AVG(lead_time_p95) as lead_time_p95"
        elif "lead_time_days" in columns:
            expr = "quantile_cont(lead_time_days, 0.95) as lead_time_p95"
        else:
            return pl.DataFrame()

        return self.loader.query(f"""
            SELECT 
                item_id,
                {expr}
            FROM {table}
            GROUP BY item_id
        """)

    def _get_lifecycle_risk(self) -> pl.DataFrame:
        """
        Compute lifecycle risk (days to EOL/LTB).
        """
        if not self.loader.loaded_tables.get("lifecycle"):
            return pl.DataFrame()

        columns = set(self.loader.table_stats.get("lifecycle", {}).get("columns", []))
        has_eol = "eol_date" in columns
        has_ltb = "ltb_date" in columns
        if not has_eol and not has_ltb:
            return pl.DataFrame()

        eol_expr = "eol_date" if has_eol else "NULL"
        ltb_expr = "ltb_date" if has_ltb else "NULL"

        return self.loader.query(f"""
            SELECT
                item_id,
                CASE
                    WHEN {eol_expr} IS NOT NULL AND {ltb_expr} IS NOT NULL THEN
                        DATEDIFF('day', CURRENT_DATE, LEAST({eol_expr}, {ltb_expr}))
                    WHEN {eol_expr} IS NOT NULL THEN
                        DATEDIFF('day', CURRENT_DATE, {eol_expr})
                    WHEN {ltb_expr} IS NOT NULL THEN
                        DATEDIFF('day', CURRENT_DATE, {ltb_expr})
                    ELSE NULL
                END as days_to_risk
            FROM lifecycle
        """)

    def _assign_risk_flags(self, items: pl.DataFrame) -> pl.DataFrame:
        """
        Add supply and lifecycle risk labels.
        """
        policy = self.config.buffer_policy

        for col in ["avg_confidence", "has_allocation", "lead_time_p95", "days_to_risk"]:
            if col not in items.columns:
                items = items.with_columns([pl.lit(None).alias(col)])

        items = items.with_columns([
            pl.col("avg_confidence").fill_null(1.0).alias("avg_confidence"),
            pl.col("has_allocation").fill_null(0).alias("has_allocation"),
            pl.col("lead_time_p95").fill_null(0).alias("lead_time_p95"),
        ])

        items = items.with_columns([
            pl.when(
                (pl.col("has_allocation") > 0)
                | (pl.col("avg_confidence") < policy.low_confidence_threshold)
                | (pl.col("lead_time_p95") >= policy.lead_time_high_risk)
            )
            .then(pl.lit("high"))
            .when(pl.col("lead_time_p95") >= policy.lead_time_medium_risk)
            .then(pl.lit("medium"))
            .otherwise(pl.lit("low"))
            .alias("supply_risk")
        ])

        items = items.with_columns([
            pl.when(pl.col("days_to_risk").is_null())
            .then(pl.lit("unknown"))
            .when(pl.col("days_to_risk") <= policy.aging_critical)
            .then(pl.lit("high"))
            .when(pl.col("days_to_risk") <= policy.aging_warning)
            .then(pl.lit("medium"))
            .otherwise(pl.lit("low"))
            .alias("lifecycle_risk")
        ])

        return items

    def _summarize_segments(self, items: pl.DataFrame) -> pl.DataFrame:
        """
        Summarize items into segments.
        """
        return (
            items
            .group_by(["category", "kit_criticality", "supply_risk", "lifecycle_risk"])
            .agg([
                pl.col("item_id").n_unique().alias("items_count"),
                pl.col("lead_time_p95").mean().alias("lead_time_p95_avg"),
                pl.col("avg_confidence").mean().alias("avg_confidence"),
                pl.col("has_allocation").max().alias("has_allocation"),
            ])
            .sort(["category", "kit_criticality", "supply_risk"])
        )

    def _segment_name(self, row: dict[str, object]) -> str:
        category = str(row.get("category", "Unknown"))
        criticality = str(row.get("kit_criticality", "blocking"))
        supply_risk = str(row.get("supply_risk", "unknown"))
        lifecycle_risk = str(row.get("lifecycle_risk", "unknown"))
        return f"{category} | {criticality} | supply:{supply_risk} | life:{lifecycle_risk}"

    def _select_target(self, row: dict[str, object]) -> BufferTarget:
        """
        Map a segment to a buffer target based on config.
        """
        raw_category = str(row.get("category", "default")).lower()
        if "gpu" in raw_category:
            category = "GPU"
        elif "callan" in raw_category:
            category = "Callan"
        elif "front_end" in raw_category or "fen" in raw_category:
            category = "FEN"
        else:
            category = str(row.get("category", "default")).replace(" ", "_")
        criticality = str(row.get("kit_criticality", "blocking")).lower()
        supply_risk = str(row.get("supply_risk", "unknown")).lower()

        if criticality != "blocking":
            key = f"{category}_non_blocking"
        else:
            if supply_risk == "high":
                key = f"{category}_blocking_high_risk"
            elif supply_risk == "low":
                key = f"{category}_blocking_low_risk"
            else:
                key = f"{category}_blocking_high_risk"

        targets = self.config.buffer_policy.targets
        if key in targets:
            return targets[key]
        if "default" in targets:
            return targets["default"]
        return BufferTarget()

    def _build_rationale(self, row: dict[str, object], target: BufferTarget) -> str:
        """
        Build a short rationale for the target.
        """
        lead_time = row.get("lead_time_p95_avg", 0)
        confidence = row.get("avg_confidence", 1.0)
        allocation = row.get("has_allocation", 0)

        pieces = [
            "Policy-based heuristic (v1).",
            f"Lead time p95 ~{int(lead_time)}d.",
            f"Avg confidence {confidence:.2f}.",
        ]
        if allocation:
            pieces.append("Allocation flags present.")

        pieces.append(f"Buffer {target.min}-{target.max} weeks at {target.location}.")
        return " ".join(pieces)
