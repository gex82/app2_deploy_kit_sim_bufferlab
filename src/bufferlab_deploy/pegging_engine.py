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
        
        if "demand_tier" not in kit_reqs.columns:
            kit_reqs = kit_reqs.with_columns([pl.lit("committed").alias("demand_tier")])

        # Run greedy allocation by demand tier priority
        results = []
        remaining_inv: dict[tuple[str, str, str], float] | None = None
        for tier in ["committed", "likely", "exploratory"]:
            tier_reqs = kit_reqs.filter(pl.col("demand_tier") == tier)
            if len(tier_reqs) == 0:
                continue

            tier_results, remaining_inv = self._greedy_allocate(
                tier_reqs,
                availability,
                remaining_inv=remaining_inv,
            )
            results.append(tier_results)

        if not results:
            return pl.DataFrame()

        return pl.concat(results)
    
    def run_tiered_pegging(
        self, 
        scenario_id: str | None = None,
        apply_convergence_gating: bool = True
    ) -> dict[str, Any]:
        """
        Run tiered pegging with strict demand tier priority.
        
        Processes demand in tier order:
        1. Committed demand first (full supply access)
        2. Likely demand second (only residual supply)
        3. Exploratory demand last (remaining residual)
        
        Args:
            scenario_id: Scenario to use
            apply_convergence_gating: If True, apply square-set convergence checks
        
        Returns:
            Dict with:
                - committed_results: pegging results for committed tier
                - likely_results: pegging results for likely tier
                - exploratory_results: pegging results for exploratory tier
                - stranded_inventory: items stranded due to partial domain readiness
                - tier_summary: aggregated metrics by tier
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        # Get kit requirements with priority and demand tier
        kit_reqs = self.kit_engine.get_kit_requirements_detail(scenario_id)
        
        if len(kit_reqs) == 0:
            return {
                "committed_results": pl.DataFrame(),
                "likely_results": pl.DataFrame(),
                "exploratory_results": pl.DataFrame(),
                "stranded_inventory": pl.DataFrame(),
                "tier_summary": {},
            }
        
        # Get initial availability from ledger
        ledger = self.netting_ledger.build_ledger(scenario_id)
        
        if len(ledger) == 0:
            return {
                "committed_results": pl.DataFrame(),
                "likely_results": pl.DataFrame(),
                "exploratory_results": pl.DataFrame(),
                "stranded_inventory": pl.DataFrame(),
                "tier_summary": {},
            }
        
        # Build initial available inventory lookup
        availability = (
            ledger
            .select(["week", "site_id", "item_id", "available"])
            .unique(["week", "site_id", "item_id"])
        )
        
        # Check if demand_tier column exists
        has_demand_tier = "demand_tier" in kit_reqs.columns
        
        if not has_demand_tier:
            # Treat all as committed if no tier column
            kit_reqs = kit_reqs.with_columns([
                pl.lit("committed").alias("demand_tier")
            ])
        
        # Track remaining inventory across tiers
        tier_results = {}
        remaining_availability = availability
        stranded_items = []
        
        for tier in ["committed", "likely", "exploratory"]:
            # Filter requirements for this tier
            tier_reqs = kit_reqs.filter(pl.col("demand_tier") == tier)
            
            if len(tier_reqs) == 0:
                tier_results[tier] = pl.DataFrame()
                continue
            
            # Run allocation for this tier with current remaining inventory
            tier_result = self._greedy_allocate(tier_reqs, remaining_availability)
            tier_results[tier] = tier_result
            
            # Update remaining inventory (subtract what was consumed)
            if len(tier_result) > 0:
                remaining_availability = self._update_remaining_inventory(
                    remaining_availability, tier_result, tier_reqs
                )
        
        # Apply convergence gating if enabled
        if apply_convergence_gating:
            stranded_items = self._check_convergence_gating(tier_results, kit_reqs)
        
        # Build tier summary
        tier_summary = {}
        for tier, results in tier_results.items():
            if len(results) > 0:
                tier_summary[tier] = {
                    "total_deployable": int(results["deployable_kits"].sum()),
                    "total_buildable": int(results["buildable_kits"].sum()),
                    "total_blocked": int(results["blocked_kits"].sum()),
                    "completion_rate_pct": round(
                        results["buildable_kits"].sum() / 
                        max(results["deployable_kits"].sum(), 1) * 100, 1
                    ),
                }
            else:
                tier_summary[tier] = {
                    "total_deployable": 0,
                    "total_buildable": 0,
                    "total_blocked": 0,
                    "completion_rate_pct": 0.0,
                }
        
        return {
            "committed_results": tier_results.get("committed", pl.DataFrame()),
            "likely_results": tier_results.get("likely", pl.DataFrame()),
            "exploratory_results": tier_results.get("exploratory", pl.DataFrame()),
            "stranded_inventory": pl.DataFrame(stranded_items) if stranded_items else pl.DataFrame(),
            "tier_summary": tier_summary,
        }
    
    def _update_remaining_inventory(
        self,
        availability: pl.DataFrame,
        pegging_result: pl.DataFrame,
        kit_reqs: pl.DataFrame
    ) -> pl.DataFrame:
        """
        Update remaining inventory after a pegging pass.
        
        Subtracts consumed inventory from available quantities.
        """
        if len(pegging_result) == 0:
            return availability
        
        # Calculate consumed quantities from buildable kits
        consumed = (
            kit_reqs
            .join(
                pegging_result.select(["week", "site_id", "kit_id", "buildable_kits"]),
                on=["week", "site_id", "kit_id"],
                how="inner"
            )
            .with_columns([
                (pl.col("qty_per") * pl.col("buildable_kits")).alias("consumed_qty")
            ])
            .group_by(["week", "site_id", "item_id"])
            .agg([
                pl.col("consumed_qty").sum().alias("total_consumed")
            ])
        )
        
        if len(consumed) == 0:
            return availability
        
        # Cast week column to consistent type for join
        availability = availability.with_columns([
            pl.col("week").cast(pl.Utf8).alias("week_str")
        ])
        consumed = consumed.with_columns([
            pl.col("week").cast(pl.Utf8).alias("week_str")
        ])
        
        # Join and subtract
        updated = (
            availability
            .join(
                consumed.select(["week_str", "site_id", "item_id", "total_consumed"]),
                on=["week_str", "site_id", "item_id"],
                how="left"
            )
            .with_columns([
                pl.col("total_consumed").fill_null(0),
                (pl.col("available") - pl.col("total_consumed").fill_null(0))
                .clip(lower_bound=0)
                .alias("available")
            ])
            .drop(["total_consumed", "week_str"])
        )
        
        return updated
    
    def _check_convergence_gating(
        self,
        tier_results: dict[str, pl.DataFrame],
        kit_reqs: pl.DataFrame
    ) -> list[dict[str, Any]]:
        """
        Check for stranded inventory due to partial domain readiness.
        
        Flags inventory where some domains are ready but others are blocking.
        """
        stranded = []
        
        # Import square-set engine for domain checks
        try:
            from bufferlab_deploy.square_set_engine import SquareSetEngine
            ss_engine = SquareSetEngine(self.loader)
            domain_readiness = ss_engine.get_domain_readiness()
            
            if len(domain_readiness) == 0:
                return stranded
            
            # Find cases where some domains ready, others not
            partial_ready = (
                domain_readiness
                .filter(
                    (pl.col("is_ready") == True) & 
                    (pl.col("domain") != "all")
                )
            )
            
            if len(partial_ready) > 0:
                for row in partial_ready.iter_rows(named=True):
                    stranded.append({
                        "site_id": row.get("site_id", ""),
                        "week": str(row.get("week", "")),
                        "domain": row.get("domain", ""),
                        "status": "partial_ready",
                        "reason": "Domain ready but other domains blocking square-set completion",
                    })
        except Exception:
            pass  # Square-set engine not available
        
        return stranded
    
    def _greedy_allocate(
        self,
        kit_reqs: pl.DataFrame,
        availability: pl.DataFrame,
        remaining_inv: dict[tuple[str, str, str], float] | None = None,
    ) -> tuple[pl.DataFrame, dict[tuple[str, str, str], float]]:
        """
        Greedy allocation by priority.
        
        Processes kits in priority order within each site/week.
        """
        # Convert to pandas for row-by-row processing
        kit_df = kit_reqs.to_pandas()
        avail_df = availability.to_pandas()
        
        # Create availability lookup
        if remaining_inv is None:
            avail_lookup: dict[tuple[str, str, str], float] = {}
            for _, row in avail_df.iterrows():
                key = (str(row['week']), row['site_id'], row['item_id'])
                avail_lookup[key] = row['available']
            remaining_inv = avail_lookup.copy()
        
        # Group by week/site/kit and aggregate requirements
        kit_groups = kit_df.groupby([
            'week', 'site_id', 'kit_id', 'priority', 'deployable_kits', 'demand_tier'
        ]).agg({
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
            demand_tier = kit['demand_tier']
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
                'demand_tier': demand_tier,
                'deployable_kits': int(deployable),
                'buildable_kits': buildable_kits,
                'blocked_kits': blocked_kits,
                'num_blocking_items': len(blocking_items),
                'blocking_items': str(blocking_items[:3]) if blocking_items else '',
            })

        return pl.DataFrame(results), remaining_inv
    
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
