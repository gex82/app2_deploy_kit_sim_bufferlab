"""
Kit Explosion Engine - BOM effective dating and requirements calculation.

Computes component requirements by exploding kit BOMs for each site/week.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.config import get_config
from bufferlab_deploy.sql_utils import (
    get_plan_table,
    get_bom_effective_clause,
    get_readiness_capacity_expr,
)


class KitEngine:
    """
    Engine for kit explosion and requirements calculation.
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
    
    def get_deployable_kits(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Calculate deployable kits per site/week based on site readiness.
        
        deployable_kits = min(kits_planned, readiness_capacity_kits)
        
        Args:
            scenario_id: Scenario to use (defaults to config default)
        
        Returns:
            DataFrame with [week, site_id, kit_id, kits_planned, readiness_capacity, deployable_kits, priority]
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        plan_table = get_plan_table(self.loader)
        default_mw_per_kit = float(self.config.mw_per_kit.get("default", 0.5))
        readiness_expr = get_readiness_capacity_expr(self.loader, default_mw_per_kit)

        result = self.loader.query(f"""
            WITH deployment AS (
                SELECT 
                    week,
                    site_id,
                    kit_id,
                    kits_planned,
                    COALESCE(priority, {self.config.analysis.pegging.default_priority}) as priority,
                    program_id
                FROM {plan_table}
            ),
            readiness AS (
                SELECT 
                    site_id,
                    week,
                    {readiness_expr} as readiness_capacity_kits
                FROM site_readiness
                WHERE scenario_id = '{scenario_id}'
            )
            SELECT 
                d.week,
                d.site_id,
                d.kit_id,
                d.kits_planned,
                d.priority,
                d.program_id,
                COALESCE(r.readiness_capacity_kits, d.kits_planned) as readiness_capacity,
                LEAST(d.kits_planned, COALESCE(r.readiness_capacity_kits, d.kits_planned)) as deployable_kits
            FROM deployment d
            LEFT JOIN readiness r ON d.site_id = r.site_id AND d.week = r.week
            ORDER BY d.week, d.site_id, d.priority, d.kit_id
        """)
        
        return result
    
    def explode_kits(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Explode kits into component requirements.
        
        For each site/week/kit, calculates:
        required_qty = deployable_kits * qty_per
        
        Filters BOM by effective dates.
        
        Returns:
            DataFrame with [week, site_id, kit_id, item_id, required_qty, priority, kit_criticality]
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        plan_table = get_plan_table(self.loader)
        bom_clause = get_bom_effective_clause(self.loader, "d.week", alias="b")

        default_mw_per_kit = float(self.config.mw_per_kit.get("default", 0.5))
        readiness_expr = get_readiness_capacity_expr(self.loader, default_mw_per_kit)
        result = self.loader.query(f"""
            WITH readiness AS (
                SELECT 
                    site_id,
                    week,
                    {readiness_expr} as readiness_capacity_kits
                FROM site_readiness
                WHERE scenario_id = '{scenario_id}'
            ),
            deployable AS (
                SELECT 
                    dp.week,
                    dp.site_id,
                    dp.kit_id,
                    LEAST(
                        dp.kits_planned,
                        COALESCE(r.readiness_capacity_kits, dp.kits_planned)
                    ) as deployable_kits,
                    COALESCE(dp.priority, {self.config.analysis.pegging.default_priority}) as priority
                FROM {plan_table} dp
                LEFT JOIN readiness r 
                    ON dp.site_id = r.site_id 
                    AND dp.week = r.week
            )
            SELECT 
                d.week,
                d.site_id,
                d.kit_id,
                b.child_item_id as item_id,
                CAST(d.deployable_kits * b.qty_per AS DOUBLE) as required_qty,
                d.priority,
                COALESCE(b.kit_criticality, 'blocking') as kit_criticality
            FROM deployable d
            JOIN bom_kit b 
                ON d.kit_id = b.kit_id
                AND {bom_clause}
            ORDER BY d.week, d.site_id, d.priority, d.kit_id, b.child_item_id
        """)
        
        return result
    
    def get_aggregated_requirements(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get total item requirements aggregated by site/week/item.
        
        Returns:
            DataFrame with [week, site_id, item_id, total_required]
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        plan_table = get_plan_table(self.loader)
        bom_clause = get_bom_effective_clause(self.loader, "d.week", alias="b")

        default_mw_per_kit = float(self.config.mw_per_kit.get("default", 0.5))
        readiness_expr = get_readiness_capacity_expr(self.loader, default_mw_per_kit)
        result = self.loader.query(f"""
            WITH readiness AS (
                SELECT 
                    site_id,
                    week,
                    {readiness_expr} as readiness_capacity_kits
                FROM site_readiness
                WHERE scenario_id = '{scenario_id}'
            ),
            deployable AS (
                SELECT 
                    dp.week,
                    dp.site_id,
                    dp.kit_id,
                    LEAST(
                        dp.kits_planned,
                        COALESCE(r.readiness_capacity_kits, dp.kits_planned)
                    ) as deployable_kits,
                    COALESCE(dp.priority, {self.config.analysis.pegging.default_priority}) as priority
                FROM {plan_table} dp
                LEFT JOIN readiness r 
                    ON dp.site_id = r.site_id 
                    AND dp.week = r.week
            ),
            requirements AS (
                SELECT 
                    d.week,
                    d.site_id,
                    d.kit_id,
                    d.priority,
                    b.child_item_id as item_id,
                    CAST(d.deployable_kits * b.qty_per AS DOUBLE) as required_qty,
                    COALESCE(b.kit_criticality, 'blocking') as kit_criticality
                FROM deployable d
                JOIN bom_kit b 
                    ON d.kit_id = b.kit_id
                    AND {bom_clause}
            )
            SELECT 
                week,
                site_id,
                item_id,
                SUM(required_qty) as total_required,
                COUNT(DISTINCT kit_id) as num_kits_requiring
            FROM requirements
            GROUP BY week, site_id, item_id
            ORDER BY week, site_id, total_required DESC
        """)
        
        return result
    
    def get_kit_requirements_detail(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get detailed kit-level requirements for pegging.
        
        Returns:
            DataFrame with full details for priority-aware allocation.
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        plan_table = get_plan_table(self.loader)
        bom_clause = get_bom_effective_clause(self.loader, "d.week", alias="b")

        default_mw_per_kit = float(self.config.mw_per_kit.get("default", 0.5))
        readiness_expr = get_readiness_capacity_expr(self.loader, default_mw_per_kit)
        result = self.loader.query(f"""
            WITH readiness AS (
                SELECT 
                    site_id,
                    week,
                    {readiness_expr} as readiness_capacity_kits
                FROM site_readiness
                WHERE scenario_id = '{scenario_id}'
            ),
            deployable AS (
                SELECT 
                    dp.week,
                    dp.site_id,
                    dp.kit_id,
                    dp.kits_planned,
                    LEAST(
                        dp.kits_planned,
                        COALESCE(r.readiness_capacity_kits, dp.kits_planned)
                    ) as deployable_kits,
                    COALESCE(dp.priority, {self.config.analysis.pegging.default_priority}) as priority,
                    dp.program_id
                FROM {plan_table} dp
                LEFT JOIN readiness r 
                    ON dp.site_id = r.site_id 
                    AND dp.week = r.week
            )
            SELECT 
                d.week,
                d.site_id,
                d.kit_id,
                d.kits_planned,
                d.deployable_kits,
                d.priority,
                d.program_id,
                b.child_item_id as item_id,
                b.qty_per,
                CAST(d.deployable_kits * b.qty_per AS DOUBLE) as required_qty,
                COALESCE(b.kit_criticality, 'blocking') as kit_criticality,
                im.category,
                im.subcategory
            FROM deployable d
            JOIN bom_kit b 
                ON d.kit_id = b.kit_id
                AND {bom_clause}
            LEFT JOIN item_master im ON b.child_item_id = im.item_id
            ORDER BY d.week, d.site_id, d.priority, d.kit_id
        """)
        
        return result
