"""
Buffer Engine v2 - Segmentation-based buffer target calculation.

Uses MECE segments (B1-B4, N1-N4) and overlay tags to compute
buffer ranges with E&O penalty consideration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.segmentation_engine import SegmentationEngine, SegmentationThresholds
from bufferlab_deploy.config import get_config


@dataclass
class BufferPolicy:
    """Buffer policy for a segment."""
    min_weeks: int
    max_weeks: int
    location: str  # integration, regional, site
    eo_penalty_pct: float = 0.0  # Reduction for E&O risk
    

@dataclass
class ItemBuffer:
    """Calculated buffer target for an item."""
    item_id: str
    segment: str
    demand_tier: str
    buffer_target_qty: int
    buffer_target_weeks: float
    min_buffer_qty: int
    max_buffer_qty: int
    location: str
    eo_penalty_applied: bool
    overlay_tags: list[str] = field(default_factory=list)


class BufferEngineV2:
    """
    Segment-based buffer target calculation engine.
    
    Computes buffer ranges based on:
    - MECE segment (B1-B4, N1-N4)
    - Demand tier (committed, likely, exploratory)
    - Overlay tags (transition, shared, long-lead, etc.)
    - E&O penalty for high-risk items
    """
    
    # Base buffer ranges by segment (min weeks, max weeks)
    BASE_RANGES = {
        "B1": (2, 3),   # Blocker + Constrained + High E&O -> min buffer, integration
        "B2": (3, 4),   # Blocker + Constrained -> moderate buffer, integration
        "B3": (2, 3),   # Blocker + High E&O -> min buffer, regional
        "B4": (4, 6),   # Blocker only -> standard buffer, regional
        "N1": (1, 2),   # Non-blocker + Constrained + High E&O -> min buffer, site
        "N2": (1, 2),   # Non-blocker + Constrained -> limited, site
        "N3": (1, 2),   # Non-blocker + High E&O -> limited, site
        "N4": (1, 2),   # Non-blocker only -> minimal, site
    }
    
    # Location by segment
    LOCATIONS = {
        "B1": "integration", "B2": "integration",
        "B3": "regional", "B4": "regional",
        "N1": "site", "N2": "site", "N3": "site", "N4": "site",
    }
    
    def __init__(
        self, 
        loader: DuckDBLoader, 
        thresholds: SegmentationThresholds | None = None
    ):
        self.loader = loader
        self.config = get_config()
        self.thresholds = thresholds or SegmentationThresholds()
        self.segmentation_engine = SegmentationEngine(loader, thresholds)
    
    def get_buffer_policy(
        self, 
        segment: str, 
        demand_tier: str = "committed",
        has_transition_tag: bool = False,
        has_high_eo: bool = False,
    ) -> BufferPolicy:
        """
        Get buffer policy for a segment + demand tier combination.
        
        Args:
            segment: B1-B4 or N1-N4
            demand_tier: committed, likely, or exploratory
            has_transition_tag: If item has transition_active overlay
            has_high_eo: If item has high E&O risk
        """
        base_min, base_max = self.BASE_RANGES.get(segment, (2, 3))
        location = self.LOCATIONS.get(segment, "regional")
        
        # Apply demand tier adjustment
        if demand_tier == "committed":
            # Full buffer allowed
            max_weeks = min(base_max, self.thresholds.committed_max_coverage_weeks)
            min_weeks = base_min
        elif demand_tier == "likely":
            # Reduced buffer
            max_weeks = min(base_min, self.thresholds.likely_max_coverage_weeks)
            min_weeks = min(base_min, self.thresholds.likely_max_coverage_weeks)
        else:  # exploratory
            # Zero/minimal buffer
            max_weeks = self.thresholds.exploratory_coverage_weeks
            min_weeks = 0
        
        # Apply transition reduction
        eo_penalty = 0.0
        if has_transition_tag:
            reduction = 1 - self.thresholds.transition_buffer_reduction_pct
            min_weeks = max(0, int(min_weeks * reduction))
            max_weeks = max(0, int(max_weeks * reduction))
            eo_penalty = self.thresholds.transition_buffer_reduction_pct
        
        # Apply E&O penalty for high-risk segments
        if has_high_eo and segment in ("B1", "B3", "N1", "N3"):
            # Further reduce for high E&O items
            eo_penalty = max(eo_penalty, 0.25)
            max_weeks = max(0, int(max_weeks * 0.75))
        
        return BufferPolicy(
            min_weeks=min_weeks,
            max_weeks=max_weeks,
            location=location,
            eo_penalty_pct=eo_penalty,
        )
    
    def calculate_item_buffers(
        self,
        demand_tier: str = "committed",
    ) -> pl.DataFrame:
        """
        Calculate buffer targets for all items.
        
        Returns:
            DataFrame with item_id, segment, buffer targets, location
        """
        # Get full segmentation
        segmentation = self.segmentation_engine.get_full_segmentation()
        if len(segmentation) == 0:
            return pl.DataFrame()
        
        # Get weekly demand for sizing
        weekly_demand = self._get_weekly_demand()
        
        # Calculate buffers for each item
        buffer_records = []
        
        for row in segmentation.to_dicts():
            item_id = row["item_id"]
            segment = row["segment"]
            
            # Get overlay tags
            overlay_tags = []
            if row.get("transition_active"):
                overlay_tags.append("transition_active")
            if row.get("shared_component"):
                overlay_tags.append("shared_component")
            if row.get("long_lead_foundation"):
                overlay_tags.append("long_lead_foundation")
            if row.get("build_ahead_sensitivity"):
                overlay_tags.append("build_ahead_sensitivity")
            if row.get("break_glass_exception"):
                overlay_tags.append("break_glass_exception")
            
            # Get policy
            policy = self.get_buffer_policy(
                segment=segment,
                demand_tier=demand_tier,
                has_transition_tag="transition_active" in overlay_tags,
                has_high_eo=row.get("is_high_eo", False),
            )
            
            # Get weekly demand for this item
            item_weekly = weekly_demand.filter(pl.col("item_id") == item_id)
            avg_weekly_demand = 0.0
            if len(item_weekly) > 0:
                avg_weekly_demand = float(item_weekly["avg_weekly_demand"][0])
            
            # Calculate qty targets
            min_buffer_qty = int(avg_weekly_demand * policy.min_weeks)
            max_buffer_qty = int(avg_weekly_demand * policy.max_weeks)
            target_weeks = (policy.min_weeks + policy.max_weeks) / 2
            target_qty = int(avg_weekly_demand * target_weeks)
            
            buffer_records.append({
                "item_id": item_id,
                "segment": segment,
                "demand_tier": demand_tier,
                "is_blocker": row.get("is_blocker", True),
                "is_constrained": row.get("is_constrained", False),
                "is_high_eo": row.get("is_high_eo", False),
                "avg_weekly_demand": avg_weekly_demand,
                "buffer_target_qty": target_qty,
                "buffer_target_weeks": round(target_weeks, 1),
                "min_buffer_qty": min_buffer_qty,
                "max_buffer_qty": max_buffer_qty,
                "min_weeks": policy.min_weeks,
                "max_weeks": policy.max_weeks,
                "location": policy.location,
                "eo_penalty_pct": policy.eo_penalty_pct,
                "eo_penalty_applied": policy.eo_penalty_pct > 0,
                "overlay_tags": ",".join(overlay_tags) if overlay_tags else "",
            })
        
        return pl.DataFrame(buffer_records)
    
    def get_buffer_summary_by_segment(
        self,
        demand_tier: str = "committed",
    ) -> pl.DataFrame:
        """Get aggregated buffer summary by segment."""
        buffers = self.calculate_item_buffers(demand_tier)
        if len(buffers) == 0:
            return pl.DataFrame()
        
        return (
            buffers
            .group_by("segment")
            .agg([
                pl.count().alias("item_count"),
                pl.col("buffer_target_qty").sum().alias("total_buffer_qty"),
                pl.col("avg_weekly_demand").sum().alias("total_weekly_demand"),
                pl.col("eo_penalty_applied").sum().alias("eo_penalty_count"),
            ])
            .sort("segment")
        )
    
    def get_buffer_summary_by_location(
        self,
        demand_tier: str = "committed",
    ) -> pl.DataFrame:
        """Get aggregated buffer summary by location."""
        buffers = self.calculate_item_buffers(demand_tier)
        if len(buffers) == 0:
            return pl.DataFrame()
        
        return (
            buffers
            .group_by("location")
            .agg([
                pl.count().alias("item_count"),
                pl.col("buffer_target_qty").sum().alias("total_buffer_qty"),
                pl.col("avg_weekly_demand").sum().alias("total_weekly_demand"),
            ])
            .sort("location")
        )
    
    def calculate_eo_penalty_impact(
        self,
        demand_tier: str = "committed",
    ) -> dict[str, Any]:
        """
        Calculate the impact of E&O penalties on buffer targets.
        
        Returns dict with:
            - items_with_penalty: count
            - total_buffer_reduction: qty
            - estimated_savings: dollar value
        """
        buffers = self.calculate_item_buffers(demand_tier)
        if len(buffers) == 0:
            return {
                "items_with_penalty": 0,
                "total_buffer_reduction": 0,
                "estimated_savings": 0.0,
            }
        
        # Get items with E&O penalty
        penalized = buffers.filter(pl.col("eo_penalty_applied") == True)
        
        if len(penalized) == 0:
            return {
                "items_with_penalty": 0,
                "total_buffer_reduction": 0,
                "estimated_savings": 0.0,
            }
        
        # Calculate what buffer would have been without penalty
        reduction_qty = 0
        for row in penalized.to_dicts():
            # Estimate original max without penalty
            original_max = row["max_buffer_qty"] / (1 - row["eo_penalty_pct"]) if row["eo_penalty_pct"] < 1 else 0
            reduction_qty += int(original_max - row["max_buffer_qty"])
        
        # Estimate dollar savings (would need unit cost data)
        item_master = self.loader.get_table("item_master")
        estimated_savings = 0.0
        if item_master is not None and len(item_master) > 0:
            for row in penalized.to_dicts():
                item_cost = item_master.filter(
                    pl.col("item_id") == row["item_id"]
                ).select("unit_cost")
                if len(item_cost) > 0:
                    cost = float(item_cost[0, 0] or 0)
                    original_max = row["max_buffer_qty"] / (1 - row["eo_penalty_pct"]) if row["eo_penalty_pct"] < 1 else 0
                    estimated_savings += cost * (original_max - row["max_buffer_qty"])
        
        return {
            "items_with_penalty": len(penalized),
            "total_buffer_reduction": reduction_qty,
            "estimated_savings": round(estimated_savings, 2),
        }
    
    def _get_weekly_demand(self) -> pl.DataFrame:
        """Get average weekly demand per item."""
        demand = self.loader.get_table("demand_plan")
        if demand is None or len(demand) == 0:
            return pl.DataFrame({"item_id": [], "avg_weekly_demand": []})
        
        # Calculate average weekly demand
        return (
            demand
            .group_by("item_id")
            .agg([
                pl.col("qty").mean().alias("avg_weekly_demand")
            ])
        )
