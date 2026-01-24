"""
Segmentation Engine for MECE item classification.

Assigns each item to exactly one base segment (B1-B4, N1-N4) and
applies overlay tags based on configurable thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.config import get_config
from bufferlab_deploy.square_set_engine import SquareSetEngine
from bufferlab_deploy.stranded_engine import StrandedEngine


@dataclass
class SegmentationThresholds:
    """UI-configurable segmentation thresholds."""
    # High E&O thresholds
    high_eo_unit_cost: float = 5000.0
    high_eo_days_to_risk: int = 90
    
    # Constrained thresholds
    constrained_lead_time: int = 45
    constrained_confidence: float = 0.70
    
    # Overlay tag thresholds
    long_lead_threshold: int = 60
    long_lead_days_to_risk_min: int = 180
    build_ahead_stranding_pct: float = 0.30
    shared_usage_threshold: int = 2
    use_category_relative_cost: bool = False
    
    # Buffer policy by tier
    committed_max_coverage_weeks: int = 6
    likely_max_coverage_weeks: int = 2
    exploratory_coverage_weeks: int = 0
    transition_buffer_reduction_pct: float = 0.33


# Segment definitions
SEGMENTS = {
    "B1": "Blocker + Constrained + High E&O",
    "B2": "Blocker + Constrained + Not High E&O",
    "B3": "Blocker + Not Constrained + High E&O",
    "B4": "Blocker + Not Constrained + Not High E&O",
    "N1": "Non-blocker + Constrained + High E&O",
    "N2": "Non-blocker + Constrained + Not High E&O",
    "N3": "Non-blocker + Not Constrained + High E&O",
    "N4": "Non-blocker + Not Constrained + Not High E&O",
}

OVERLAY_TAGS = [
    "transition_active",
    "shared_component",
    "long_lead_foundation",
    "build_ahead_sensitivity",
    "break_glass_exception",
]


class SegmentationEngine:
    """
    Engine for MECE item segmentation.
    
    Assigns each item to exactly one base segment and applies overlay tags.
    """
    
    def __init__(self, loader: DuckDBLoader, thresholds: SegmentationThresholds | None = None):
        self.loader = loader
        self.config = get_config()
        self.thresholds = thresholds or SegmentationThresholds()
    
    def set_thresholds(self, thresholds: SegmentationThresholds) -> None:
        """Update segmentation thresholds from UI."""
        self.thresholds = thresholds
    
    def compute_item_dimensions(self) -> pl.DataFrame:
        """
        Compute the three segmentation dimensions for each item.
        
        Returns:
            DataFrame with [item_id, is_blocker, is_constrained, is_high_eo]
        """
        today = date.today()
        t = self.thresholds
        
        # Get blocker status from BOM
        blocker_status = self.loader.query("""
            SELECT DISTINCT
                child_item_id as item_id,
                CASE WHEN kit_criticality = 'blocking' OR kit_criticality IS NULL 
                     THEN TRUE ELSE FALSE END as is_blocker
            FROM bom_kit
        """)
        
        # Get constraint status from supply data
        try:
            constrained_status = self.loader.query("""
                SELECT 
                    item_id,
                    AVG(COALESCE(confidence_score, confidence_weight, 1.0)) as avg_confidence,
                    SUM(CASE 
                        WHEN CAST(allocation_flag AS VARCHAR) IN ('true', '1', 'allocated', 'True', 'TRUE') THEN 1 
                        ELSE 0 
                    END) > 0 as has_allocation
                FROM supply
                GROUP BY item_id
            """)
        except Exception:
            constrained_status = pl.DataFrame()
        
        # Get E&O risk from item_master + lifecycle
        eo_status = self.loader.query("""
            SELECT 
                im.item_id,
                COALESCE(im.unit_cost, 0) as unit_cost,
                COALESCE(
                    DATEDIFF('day', CURRENT_DATE, lc.eol_date),
                    DATEDIFF('day', CURRENT_DATE, lc.ltb_date),
                    365
                ) as days_to_risk,
                lc.transition_start_date,
                lc.transition_end_date,
                lc.generation,
                lc.compatibility_group
            FROM item_master im
            LEFT JOIN lifecycle lc ON im.item_id = lc.item_id
        """)
        
        # Get lead time if available
        lead_times = pl.DataFrame()
        if self.loader.loaded_tables.get("lead_time_history"):
            lead_times = self.loader.query("""
                SELECT 
                    item_id,
                    MAX(lead_time_p95) as lead_time_p95
                FROM lead_time_history
                GROUP BY item_id
            """)
        
        # Combine dimensions
        items = eo_status

        if t.use_category_relative_cost:
            category_costs = self._get_category_cost_percentile()
            if len(category_costs) > 0:
                items = items.join(category_costs, on="item_id", how="left")
                items = items.with_columns([
                    pl.col("category_cost_percentile").fill_null(0.0)
                ])
            else:
                items = items.with_columns([pl.lit(0.0).alias("category_cost_percentile")])
        else:
            items = items.with_columns([pl.lit(0.0).alias("category_cost_percentile")])
        
        # Join blocker status
        if len(blocker_status) > 0:
            items = items.join(blocker_status, on="item_id", how="left")
            items = items.with_columns([
                pl.col("is_blocker").fill_null(True)  # Default blocking
            ])
        else:
            items = items.with_columns([pl.lit(True).alias("is_blocker")])
        
        # Join constrained status
        if len(constrained_status) > 0:
            items = items.join(constrained_status, on="item_id", how="left")
        else:
            items = items.with_columns([
                pl.lit(1.0).alias("avg_confidence"),
                pl.lit(False).alias("has_allocation"),
            ])
        
        # Join lead times
        if len(lead_times) > 0:
            items = items.join(lead_times, on="item_id", how="left")
            items = items.with_columns([
                pl.col("lead_time_p95").fill_null(0)
            ])
        else:
            items = items.with_columns([pl.lit(0).alias("lead_time_p95")])
        
        # Compute boolean dimensions
        items = items.with_columns([
            # is_constrained: allocation flag OR low confidence OR long lead time
            (
                pl.col("has_allocation") |
                (pl.col("avg_confidence") < t.constrained_confidence) |
                (pl.col("lead_time_p95") > t.constrained_lead_time)
            ).alias("is_constrained"),
            
            # is_high_eo: high cost (absolute or category-relative) AND short days to risk
            (
                (
                    pl.when(pl.lit(t.use_category_relative_cost))
                    .then(pl.col("category_cost_percentile") >= 0.5)
                    .otherwise(pl.col("unit_cost") > t.high_eo_unit_cost)
                ) &
                (pl.col("days_to_risk") < t.high_eo_days_to_risk)
            ).alias("is_high_eo"),
        ])
        
        return items
    
    def assign_base_segments(self) -> pl.DataFrame:
        """
        Assign each item to exactly one base segment (B1-B4 or N1-N4).
        
        Returns:
            DataFrame with item_id, segment, and dimension flags
        """
        items = self.compute_item_dimensions()
        
        # Assign segment based on blocker/constrained/high_eo
        items = items.with_columns([
            pl.when(pl.col("is_blocker") & pl.col("is_constrained") & pl.col("is_high_eo"))
            .then(pl.lit("B1"))
            .when(pl.col("is_blocker") & pl.col("is_constrained") & ~pl.col("is_high_eo"))
            .then(pl.lit("B2"))
            .when(pl.col("is_blocker") & ~pl.col("is_constrained") & pl.col("is_high_eo"))
            .then(pl.lit("B3"))
            .when(pl.col("is_blocker") & ~pl.col("is_constrained") & ~pl.col("is_high_eo"))
            .then(pl.lit("B4"))
            .when(~pl.col("is_blocker") & pl.col("is_constrained") & pl.col("is_high_eo"))
            .then(pl.lit("N1"))
            .when(~pl.col("is_blocker") & pl.col("is_constrained") & ~pl.col("is_high_eo"))
            .then(pl.lit("N2"))
            .when(~pl.col("is_blocker") & ~pl.col("is_constrained") & pl.col("is_high_eo"))
            .then(pl.lit("N3"))
            .otherwise(pl.lit("N4"))
            .alias("segment")
        ])
        
        return items
    
    def compute_overlay_tags(self, items: pl.DataFrame) -> pl.DataFrame:
        """
        Compute overlay tags for each item.
        
        Returns:
            DataFrame with overlay tag boolean columns added
        """
        t = self.thresholds
        today = date.today()
        
        # Get usage count across programs/categories
        usage_counts = self.loader.query("""
            SELECT 
                b.child_item_id as item_id,
                COUNT(DISTINCT b.kit_id) as kit_count,
                COUNT(DISTINCT im.category) as category_count
            FROM bom_kit b
            LEFT JOIN (
                SELECT DISTINCT kit_id, 
                    (SELECT category FROM item_master WHERE item_id = b2.child_item_id LIMIT 1) as category
                FROM bom_kit b2
            ) im ON b.kit_id = im.kit_id
            GROUP BY b.child_item_id
        """)
        
        # Join usage counts
        if len(usage_counts) > 0:
            items = items.join(usage_counts, on="item_id", how="left")
            items = items.with_columns([
                pl.col("kit_count").fill_null(0),
                pl.col("category_count").fill_null(0),
            ])
        else:
            items = items.with_columns([
                pl.lit(0).alias("kit_count"),
                pl.lit(0).alias("category_count"),
            ])
        
        # Compute overlay tags
        items = items.with_columns([
            # transition_active: within transition window
            (
                (pl.col("transition_start_date").is_not_null()) &
                (pl.col("transition_end_date").is_not_null()) &
                (pl.col("transition_start_date") <= pl.lit(today)) &
                (pl.col("transition_end_date") >= pl.lit(today))
            ).alias("transition_active"),
            
            # shared_component: used in multiple kits/categories
            (pl.col("kit_count") >= t.shared_usage_threshold).alias("shared_component"),
            
            # long_lead_foundation: long lead time but low obsolescence risk
            (
                (pl.col("lead_time_p95") > t.long_lead_threshold) &
                (pl.col("days_to_risk") > t.long_lead_days_to_risk_min)
            ).alias("long_lead_foundation"),
            
            # build_ahead_sensitivity: set below via stranding ratios + overrides
            pl.lit(False).alias("build_ahead_sensitivity"),
            
            # break_glass_exception: set below via manual overrides
            pl.lit(False).alias("break_glass_exception"),
        ])

        item_master = self.loader.get_table("item_master")
        if item_master is not None and "shared_flag" in item_master.columns:
            shared_flags = item_master.select([
                "item_id",
                pl.col("shared_flag").cast(pl.Boolean, strict=False).alias("shared_flag"),
            ])
            items = items.join(shared_flags, on="item_id", how="left").with_columns([
                pl.col("shared_flag").fill_null(False)
            ]).with_columns([
                (pl.col("shared_component") | pl.col("shared_flag")).alias("shared_component")
            ]).drop("shared_flag")

        build_ahead = self._get_build_ahead_sensitivity()
        if len(build_ahead) > 0:
            items = items.join(build_ahead, on="item_id", how="left")
            items = items.with_columns([
                pl.coalesce(["build_ahead_sensitivity_right", "build_ahead_sensitivity"])
                .alias("build_ahead_sensitivity")
            ]).drop("build_ahead_sensitivity_right")

        break_glass = self._get_break_glass_exceptions()
        if len(break_glass) > 0:
            items = items.join(break_glass, on="item_id", how="left")
            items = items.with_columns([
                pl.coalesce(["break_glass_exception_right", "break_glass_exception"])
                .alias("break_glass_exception")
            ]).drop("break_glass_exception_right")
        
        return items

    def _get_category_cost_percentile(self) -> pl.DataFrame:
        """
        Compute category-relative unit cost percentile per item.
        """
        item_master = self.loader.get_table("item_master")
        if item_master is None:
            return pl.DataFrame()
        if "category" not in item_master.columns or "unit_cost" not in item_master.columns:
            return pl.DataFrame()

        return (
            item_master
            .select(["item_id", "category", "unit_cost"])
            .with_columns([
                pl.col("unit_cost").fill_null(0).cast(pl.Float64, strict=False)
            ])
            .with_columns([
                pl.col("unit_cost").rank("dense").over("category").alias("cost_rank"),
                pl.count().over("category").alias("category_count"),
            ])
            .with_columns([
                (pl.col("cost_rank") / pl.col("category_count")).alias("category_cost_percentile")
            ])
            .select(["item_id", "category_cost_percentile"])
        )

    def _get_build_ahead_sensitivity(self) -> pl.DataFrame:
        """
        Calculate build-ahead sensitivity using stranded vs required ratio.
        """
        t = self.thresholds
        square_set_engine = SquareSetEngine(self.loader)
        stranded_engine = StrandedEngine(self.loader)

        required = square_set_engine.get_aggregated_requirements()
        stranded = pl.DataFrame()
        for table in ["stranded_inventory_history", "stranded_history", "stranded_inventory"]:
            if not self.loader.loaded_tables.get(table):
                continue
            history = self.loader.get_table(table)
            if history is None or len(history) == 0 or "item_id" not in history.columns:
                continue
            if "stranded_units" in history.columns:
                units_col = "stranded_units"
            elif "stranded_qty" in history.columns:
                units_col = "stranded_qty"
            elif "qty" in history.columns:
                units_col = "qty"
            else:
                continue
            stranded = history.group_by("item_id").agg(
                pl.col(units_col).sum().alias("stranded_units")
            )
            break
        if len(stranded) == 0:
            stranded = stranded_engine.get_stranded_summary()
        item_master = self.loader.get_table("item_master")

        if len(required) == 0:
            return pl.DataFrame()

        required_totals = required.group_by("item_id").agg(
            pl.col("total_required").sum().alias("total_required")
        )
        stranded_totals = stranded.group_by("item_id").agg(
            pl.col("stranded_units").sum().alias("stranded_units")
        )

        combined = required_totals.join(stranded_totals, on="item_id", how="left").with_columns([
            pl.col("stranded_units").fill_null(0),
        ])

        combined = combined.with_columns([
            (
                (pl.col("total_required") > 0) &
                (pl.col("stranded_units") / pl.col("total_required") > t.build_ahead_stranding_pct)
            ).alias("build_ahead_sensitivity")
        ])

        if item_master is not None and "build_ahead_flag" in item_master.columns:
            overrides = item_master.select([
                "item_id",
                pl.col("build_ahead_flag").cast(pl.Boolean, strict=False).alias("build_ahead_flag"),
            ])
            combined = combined.join(overrides, on="item_id", how="left").with_columns([
                pl.col("build_ahead_flag").fill_null(False)
            ]).with_columns([
                (pl.col("build_ahead_sensitivity") | pl.col("build_ahead_flag"))
                .alias("build_ahead_sensitivity")
            ])

        return combined.select(["item_id", "build_ahead_sensitivity"])

    def _get_break_glass_exceptions(self) -> pl.DataFrame:
        """
        Read break-glass overrides from item_master if present.
        """
        item_master = self.loader.get_table("item_master")
        if item_master is None or "break_glass_exception" not in item_master.columns:
            return pl.DataFrame()

        return item_master.select([
            "item_id",
            pl.col("break_glass_exception").cast(pl.Boolean, strict=False).alias("break_glass_exception")
        ])
    
    def get_full_segmentation(self) -> pl.DataFrame:
        """
        Get complete segmentation with base segment and all overlay tags.
        
        Returns:
            Full segmentation DataFrame
        """
        items = self.assign_base_segments()
        items = self.compute_overlay_tags(items)
        
        # Join with item_master for additional info
        item_master = self.loader.get_table("item_master")
        if item_master is not None:
            items = items.join(
                item_master.select(["item_id", "category", "subcategory", "description"]),
                on="item_id",
                how="left"
            )
        
        return items
    
    def get_segment_summary(self) -> pl.DataFrame:
        """
        Get count of items per segment.
        """
        segmentation = self.get_full_segmentation()
        
        summary = (
            segmentation
            .group_by("segment")
            .agg([
                pl.count().alias("item_count"),
                pl.col("unit_cost").sum().alias("total_value"),
                pl.col("transition_active").sum().alias("transition_active_count"),
                pl.col("shared_component").sum().alias("shared_count"),
                pl.col("long_lead_foundation").sum().alias("long_lead_count"),
            ])
            .sort("segment")
        )
        
        return summary
    
    def get_overlay_tag_summary(self) -> pl.DataFrame:
        """
        Get count of items per overlay tag.
        """
        segmentation = self.get_full_segmentation()
        
        tag_counts = [
            {"tag": "transition_active", "count": segmentation["transition_active"].sum()},
            {"tag": "shared_component", "count": segmentation["shared_component"].sum()},
            {"tag": "long_lead_foundation", "count": segmentation["long_lead_foundation"].sum()},
            {"tag": "build_ahead_sensitivity", "count": segmentation["build_ahead_sensitivity"].sum()},
            {"tag": "break_glass_exception", "count": segmentation["break_glass_exception"].sum()},
        ]
        
        return pl.DataFrame(tag_counts)
    
    def get_buffer_policy_for_segment(
        self, 
        segment: str, 
        demand_tier: str = "committed",
        has_transition_tag: bool = False
    ) -> dict[str, Any]:
        """
        Get buffer policy for a specific segment and demand tier.
        
        Returns:
            Dict with min_weeks, max_weeks, location
        """
        t = self.thresholds
        
        # Base buffer ranges by segment
        base_ranges = {
            "B1": {"min": 2, "max": 3, "location": "integration"},
            "B2": {"min": 3, "max": 4, "location": "integration"},
            "B3": {"min": 2, "max": 3, "location": "regional"},
            "B4": {"min": 4, "max": 6, "location": "regional"},
            "N1": {"min": 1, "max": 2, "location": "site"},
            "N2": {"min": 1, "max": 2, "location": "site"},
            "N3": {"min": 1, "max": 2, "location": "site"},
            "N4": {"min": 1, "max": 2, "location": "site"},
        }
        
        policy = base_ranges.get(segment, {"min": 2, "max": 3, "location": "regional"}).copy()
        
        # Apply tier adjustment
        if demand_tier == "committed":
            # Full buffer allowed
            policy["max"] = min(policy["max"], t.committed_max_coverage_weeks)
        elif demand_tier == "likely":
            # Cap at likely max
            policy["max"] = min(policy["min"], t.likely_max_coverage_weeks)
            policy["min"] = min(policy["min"], t.likely_max_coverage_weeks)
        else:  # exploratory
            # Zero buffer
            policy["min"] = 0
            policy["max"] = t.exploratory_coverage_weeks
        
        # Apply transition reduction
        if has_transition_tag:
            reduction = 1 - t.transition_buffer_reduction_pct
            policy["min"] = max(0, int(policy["min"] * reduction))
            policy["max"] = max(0, int(policy["max"] * reduction))
        
        return policy
