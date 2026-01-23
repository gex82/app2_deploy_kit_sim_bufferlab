"""
Priority-Aware Pegging Engine.

Allocates constrained components to kits based on priority order.
Higher priority (lower number) kits get allocation first.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.kit_engine import KitEngine
from bufferlab_deploy.netting_ledger import NettingLedger
from bufferlab_deploy.config import get_config


class PeggingEngine:
    """
    Priority-aware allocation engine.
    
    When multiple kits compete for the same item in the same site/week,
    allocates to higher priority kits first.
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
        self.kit_engine = KitEngine(loader)
        self.netting_ledger = NettingLedger(loader)
    
    def run_pegging(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Run priority-aware pegging algorithm.
        
        For each site/week:
        1. Get kit requirements sorted by priority
        2. Get available inventory from ledger
        3. Allocate items in priority order
        4. Track buildable kits and blockers
        
        Returns:
            DataFrame with [week, site_id, kit_id, priority, deployable_kits, 
                           buildable_kits, blocked_kits, blocking_items]
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        # Get kit requirements with priority
        kit_reqs = self.kit_engine.get_kit_requirements_detail(scenario_id)
        
        if len(kit_reqs) == 0:
            return pl.DataFrame()
        
        # Get availability from ledger
        ledger = self.netting_ledger.build_ledger(scenario_id)
        
        if len(ledger) == 0:
            return pl.DataFrame()
        
        # Build available inventory lookup
        availability = (
            ledger
            .select(["week", "site_id", "item_id", "available"])
            .unique(["week", "site_id", "item_id"])
        )
        
        # Run greedy allocation
        results = self._greedy_allocate(kit_reqs, availability)
        
        return results
    
    def _greedy_allocate(
        self,
        kit_reqs: pl.DataFrame,
        availability: pl.DataFrame
    ) -> pl.DataFrame:
        """
        Greedy allocation by priority.
        
        Processes kits in priority order within each site/week.
        """
        # Convert to pandas for row-by-row processing
        kit_df = kit_reqs.to_pandas()
        avail_df = availability.to_pandas()
        
        # Create availability lookup
        avail_lookup = {}
        for _, row in avail_df.iterrows():
            key = (str(row['week']), row['site_id'], row['item_id'])
            avail_lookup[key] = row['available']
        
        # Track remaining inventory
        remaining_inv = avail_lookup.copy()
        
        # Group by week/site/kit and aggregate requirements
        kit_groups = kit_df.groupby(['week', 'site_id', 'kit_id', 'priority', 'deployable_kits']).agg({
            'item_id': list,
            'required_qty': list,
            'qty_per': list,
            'kit_criticality': lambda x: list(x)
        }).reset_index()
        
        # Sort by week, site, priority
        kit_groups = kit_groups.sort_values(['week', 'site_id', 'priority'])
        
        results = []
        
        for _, kit in kit_groups.iterrows():
            week = str(kit['week'])
            site_id = kit['site_id']
            kit_id = kit['kit_id']
            priority = kit['priority']
            deployable = kit['deployable_kits']
            items = kit['item_id']
            required_qtys = kit['required_qty']
            qtys_per = kit['qty_per']
            criticalities = kit['kit_criticality']
            
            # Check how many kits can be built
            max_buildable = float(deployable)
            blocking_items = []
            
            for i, item_id in enumerate(items):
                key = (week, site_id, item_id)
                available = remaining_inv.get(key, 0)
                qty_per = qtys_per[i] if qtys_per[i] > 0 else 1
                criticality = criticalities[i]
                
                # How many kits can this item support?
                item_can_build = available / qty_per if qty_per > 0 else float('inf')
                
                if item_can_build < max_buildable:
                    if criticality == 'blocking':
                        max_buildable = max(0, item_can_build)
                        blocking_items.append({
                            'item_id': item_id,
                            'available': available,
                            'required': required_qtys[i],
                            'shortfall': required_qtys[i] - available
                        })
            
            buildable_kits = int(max_buildable)
            blocked_kits = int(deployable) - buildable_kits
            
            # Consume inventory for built kits
            for i, item_id in enumerate(items):
                if criticalities[i] != 'blocking':
                    continue
                key = (week, site_id, item_id)
                consumed = buildable_kits * qtys_per[i]
                if key in remaining_inv:
                    remaining_inv[key] = max(0, remaining_inv[key] - consumed)
            
            results.append({
                'week': kit['week'],
                'site_id': site_id,
                'kit_id': kit_id,
                'priority': priority,
                'deployable_kits': int(deployable),
                'buildable_kits': buildable_kits,
                'blocked_kits': blocked_kits,
                'num_blocking_items': len(blocking_items),
                'blocking_items': str(blocking_items[:3]) if blocking_items else '',
            })
        
        return pl.DataFrame(results)
    
    def get_buildability_summary(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get kit buildability summary by week.
        
        Returns:
            DataFrame with weekly totals
        """
        pegging = self.run_pegging(scenario_id)
        
        if len(pegging) == 0:
            return pl.DataFrame()
        
        summary = (
            pegging
            .group_by(["week", "site_id"])
            .agg([
                pl.col("deployable_kits").sum().alias("total_deployable"),
                pl.col("buildable_kits").sum().alias("total_buildable"),
                pl.col("blocked_kits").sum().alias("total_blocked"),
                pl.col("kit_id").n_unique().alias("num_kit_types"),
            ])
            .with_columns([
                (pl.col("total_buildable") / pl.col("total_deployable") * 100)
                .round(1)
                .alias("completion_rate_pct")
            ])
            .sort(["site_id", "week"])
        )
        
        return summary
    
    def get_priority_impact(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Analyze how priority affected allocation outcomes.
        
        Shows which kits got allocation and which didn't due to priority.
        """
        pegging = self.run_pegging(scenario_id)
        
        if len(pegging) == 0:
            return pl.DataFrame()
        
        # Group by priority bucket
        priority_analysis = (
            pegging
            .with_columns([
                pl.when(pl.col("priority") <= 20).then(pl.lit("P1 (1-20)"))
                .when(pl.col("priority") <= 50).then(pl.lit("P2 (21-50)"))
                .when(pl.col("priority") <= 80).then(pl.lit("P3 (51-80)"))
                .otherwise(pl.lit("P4 (81+)"))
                .alias("priority_bucket")
            ])
            .group_by("priority_bucket")
            .agg([
                pl.col("deployable_kits").sum().alias("total_deployable"),
                pl.col("buildable_kits").sum().alias("total_buildable"),
                pl.col("blocked_kits").sum().alias("total_blocked"),
                pl.col("kit_id").n_unique().alias("num_kit_types"),
            ])
            .with_columns([
                (pl.col("total_buildable") / pl.col("total_deployable") * 100)
                .round(1)
                .alias("completion_rate_pct")
            ])
            .sort("priority_bucket")
        )
        
        return priority_analysis
