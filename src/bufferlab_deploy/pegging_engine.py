"""
Priority-Aware Pegging Engine.

Allocates constrained components to square-sets based on priority order.
Higher priority (lower number) square-sets get allocation first.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.square_set_engine import SquareSetEngine
from bufferlab_deploy.netting_ledger import NettingLedger
from bufferlab_deploy.config import get_config


class PeggingEngine:
    """
    Priority-aware allocation engine.
    
    When multiple square-sets compete for the same item in the same site/week,
    allocates to higher priority square-sets first.
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
        self.square_set_engine = SquareSetEngine(loader)
        self.netting_ledger = NettingLedger(loader)
    
    def run_pegging(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Run priority-aware pegging algorithm.
        
        For each site/week:
        1. Get square-set requirements sorted by priority
        2. Get available inventory from ledger
        3. Allocate items in priority order
        4. Track buildable square-sets and blockers
        
        Returns:
            DataFrame with [week, site_id, square_set_id, priority, deployable_sets, 
                           buildable_sets, blocked_sets, blocking_items]
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        # Get square-set requirements with priority
        square_set_reqs = self.square_set_engine.get_square_set_requirements_detail(scenario_id)
        
        if len(square_set_reqs) == 0:
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
        
        if "demand_tier" not in square_set_reqs.columns:
            square_set_reqs = square_set_reqs.with_columns([pl.lit("committed").alias("demand_tier")])

        allocatable_reqs, blocked_results = self._apply_convergence_gating(
            square_set_reqs,
            scenario_id,
        )

        # Run greedy allocation by demand tier priority
        results = []
        remaining_inv: dict[tuple[str, str, str], float] | None = None
        for tier in ["committed", "likely", "exploratory"]:
            tier_reqs = allocatable_reqs.filter(pl.col("demand_tier") == tier)
            if len(tier_reqs) == 0:
                continue

            tier_results, remaining_inv = self._greedy_allocate(
                tier_reqs,
                availability,
                remaining_inv=remaining_inv,
            )
            results.append(self._normalize_result_types(tier_results))

        if len(blocked_results) > 0:
            results.append(self._normalize_result_types(blocked_results))

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
        
        # Get square-set requirements with priority and demand tier
        square_set_reqs = self.square_set_engine.get_square_set_requirements_detail(scenario_id)
        
        if len(square_set_reqs) == 0:
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
        has_demand_tier = "demand_tier" in square_set_reqs.columns
        
        if not has_demand_tier:
            # Treat all as committed if no tier column
            square_set_reqs = square_set_reqs.with_columns([
                pl.lit("committed").alias("demand_tier")
            ])
        
        blocked_results = pl.DataFrame()
        allocatable_reqs = square_set_reqs
        if apply_convergence_gating:
            allocatable_reqs, blocked_results = self._apply_convergence_gating(
                square_set_reqs,
                scenario_id,
            )

        # Track remaining inventory across tiers
        tier_results = {}
        remaining_availability = availability
        stranded_items = []
        
        for tier in ["committed", "likely", "exploratory"]:
            # Filter requirements for this tier
            tier_reqs = allocatable_reqs.filter(pl.col("demand_tier") == tier)
            
            if len(tier_reqs) == 0:
                tier_results[tier] = blocked_results.filter(pl.col("demand_tier") == tier)
                continue
            
            # Run allocation for this tier with current remaining inventory
            tier_result, _ = self._greedy_allocate(tier_reqs, remaining_availability)
            tier_result = self._normalize_result_types(tier_result)
            blocked_tier = blocked_results.filter(pl.col("demand_tier") == tier)
            blocked_tier = self._normalize_result_types(blocked_tier)
            if len(blocked_tier) > 0:
                tier_results[tier] = pl.concat([tier_result, blocked_tier])
            else:
                tier_results[tier] = tier_result
            
            # Update remaining inventory (subtract what was consumed)
            if len(tier_result) > 0:
                remaining_availability = self._update_remaining_inventory(
                    remaining_availability, tier_result, tier_reqs
                )
        
        # Apply convergence gating if enabled
        if apply_convergence_gating:
            stranded_items = self._check_convergence_gating(tier_results, square_set_reqs)
        
        # Build tier summary
        tier_summary = {}
        for tier, results in tier_results.items():
            if len(results) > 0:
                tier_summary[tier] = {
                    "total_deployable": int(results["deployable_sets"].sum()),
                    "total_buildable": int(results["buildable_sets"].sum()),
                    "total_blocked": int(results["blocked_sets"].sum()),
                    "completion_rate_pct": round(
                        results["buildable_sets"].sum() / 
                        max(results["deployable_sets"].sum(), 1) * 100, 1
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

    def _apply_convergence_gating(
        self,
        square_set_reqs: pl.DataFrame,
        scenario_id: str,
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """
        Apply convergence gating before allocation.
        
        Returns:
            allocatable_reqs: requirements for converged square-sets
            blocked_results: pegging-style rows for non-converged square-sets
        """
        convergence = self.square_set_engine.get_convergence_summary(scenario_id)
        if len(convergence) == 0:
            return square_set_reqs, pl.DataFrame()

        convergence = convergence.select([
            "week",
            "site_id",
            "square_set_id",
            "all_domains_ready",
            "missing_domains",
        ])

        joined = square_set_reqs.join(
            convergence,
            on=["week", "site_id", "square_set_id"],
            how="left",
        )

        not_ready = joined.filter(pl.col("all_domains_ready") == False)
        if len(not_ready) == 0:
            return joined, pl.DataFrame()

        blocked_summary = (
            not_ready
            .group_by(["week", "site_id", "square_set_id", "priority", "demand_tier"])
            .agg([
                pl.col("deployable_sets").max().alias("deployable_sets"),
                pl.col("missing_domains").first().alias("missing_domains"),
            ])
            .with_columns([
                pl.col("missing_domains").list.len().fill_null(0).alias("num_blocking_items"),
                pl.col("missing_domains").list.join(",").fill_null("").alias("blocking_items"),
            ])
            .with_columns([
                pl.lit(0).alias("buildable_sets"),
                pl.col("deployable_sets").alias("blocked_sets"),
            ])
            .select([
                "week",
                "site_id",
                "square_set_id",
                "priority",
                "demand_tier",
                "deployable_sets",
                "buildable_sets",
                "blocked_sets",
                "num_blocking_items",
                "blocking_items",
            ])
        )

        allocatable = joined.filter(
            (pl.col("all_domains_ready") == True) | (pl.col("all_domains_ready").is_null())
        )

        return allocatable, blocked_summary
    
    def _update_remaining_inventory(
        self,
        availability: pl.DataFrame,
        pegging_result: pl.DataFrame,
        square_set_reqs: pl.DataFrame
    ) -> pl.DataFrame:
        """
        Update remaining inventory after a pegging pass.
        
        Subtracts consumed inventory from available quantities.
        """
        if len(pegging_result) == 0:
            return availability
        
        # Calculate consumed quantities from buildable square-sets
        consumed = (
            square_set_reqs
            .join(
                pegging_result.select(["week", "site_id", "square_set_id", "buildable_sets"]),
                on=["week", "site_id", "square_set_id"],
                how="inner"
            )
            .with_columns([
                (pl.col("qty_per") * pl.col("buildable_sets")).alias("consumed_qty")
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
        square_set_reqs: pl.DataFrame
    ) -> list[dict[str, Any]]:
        """
        Check for stranded inventory due to partial domain readiness.
        
        Flags inventory where some domains are ready but others are blocking.
        """
        stranded = []
        
        try:
            domain_readiness = self.square_set_engine.get_domain_readiness()
            convergence = self.square_set_engine.get_convergence_summary()
            
            if len(domain_readiness) == 0 or len(convergence) == 0:
                return stranded
            
            partial_ready = (
                domain_readiness
                .join(
                    convergence.select([
                        "week", "site_id", "square_set_id", "all_domains_ready", "missing_domains"
                    ]),
                    on=["week", "site_id", "square_set_id"],
                    how="left"
                )
                .filter(
                    (pl.col("all_domains_ready") == False) &
                    (pl.col("is_ready") == True)
                )
            )
            
            if len(partial_ready) > 0:
                for row in partial_ready.iter_rows(named=True):
                    missing = row.get("missing_domains", [])
                    if isinstance(missing, list):
                        missing_str = ", ".join(missing)
                    else:
                        missing_str = str(missing or "")
                    stranded.append({
                        "site_id": row.get("site_id", ""),
                        "week": str(row.get("week", "")),
                        "square_set_id": row.get("square_set_id", ""),
                        "domain": row.get("domain", ""),
                        "status": "partial_ready",
                        "missing_domains": missing_str,
                        "reason": "Domain ready but other domains blocking square-set completion",
                    })
        except Exception:
            pass  # Convergence data not available
        
        return stranded

    def _normalize_result_types(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Ensure consistent dtypes before concatenation.
        """
        if df is None or len(df) == 0:
            return df

        exprs = []
        columns = set(df.columns)
        if "week" in columns:
            exprs.append(pl.col("week").cast(pl.Date, strict=False))
        for col in [
            "priority",
            "deployable_sets",
            "buildable_sets",
            "blocked_sets",
            "num_blocking_items",
        ]:
            if col in columns:
                exprs.append(pl.col(col).cast(pl.Int64, strict=False))
        if exprs:
            df = df.with_columns(exprs)
        return df
    
    def _greedy_allocate(
        self,
        square_set_reqs: pl.DataFrame,
        availability: pl.DataFrame,
        remaining_inv: dict[tuple[str, str, str], float] | None = None,
    ) -> tuple[pl.DataFrame, dict[tuple[str, str, str], float]]:
        """
        Greedy allocation by priority.
        
        Processes square-sets in priority order within each site/week.
        """
        # Convert to pandas for row-by-row processing
        kit_df = square_set_reqs.to_pandas()
        avail_df = availability.to_pandas()
        
        # Create availability lookup
        if remaining_inv is None:
            avail_lookup: dict[tuple[str, str, str], float] = {}
            for _, row in avail_df.iterrows():
                key = (str(row['week']), row['site_id'], row['item_id'])
                avail_lookup[key] = row['available']
            remaining_inv = avail_lookup.copy()
        
        # Group by week/site/square_set and aggregate requirements
        kit_groups = kit_df.groupby([
            'week', 'site_id', 'square_set_id', 'priority', 'deployable_sets', 'demand_tier'
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
            square_set_id = kit['square_set_id']
            priority = kit['priority']
            deployable = kit['deployable_sets']
            demand_tier = kit['demand_tier']
            items = kit['item_id']
            required_qtys = kit['required_qty']
            qtys_per = kit['qty_per']
            criticalities = kit['kit_criticality']
            
            # Check how many square-sets can be built
            max_buildable = float(deployable)
            blocking_items = []
            
            for i, item_id in enumerate(items):
                key = (week, site_id, item_id)
                available = remaining_inv.get(key, 0)
                qty_per = qtys_per[i] if qtys_per[i] > 0 else 1
                criticality = criticalities[i]
                
                # How many square-sets can this item support?
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
            
            buildable_sets = int(max_buildable)
            blocked_sets = int(deployable) - buildable_sets
            
            # Consume inventory for built square-sets
            for i, item_id in enumerate(items):
                if criticalities[i] != 'blocking':
                    continue
                key = (week, site_id, item_id)
                consumed = buildable_sets * qtys_per[i]
                if key in remaining_inv:
                    remaining_inv[key] = max(0, remaining_inv[key] - consumed)
            
            results.append({
                'week': kit['week'],
                'site_id': site_id,
                'square_set_id': square_set_id,
                'priority': priority,
                'demand_tier': demand_tier,
                'deployable_sets': int(deployable),
                'buildable_sets': buildable_sets,
                'blocked_sets': blocked_sets,
                'num_blocking_items': len(blocking_items),
                'blocking_items': str(blocking_items[:3]) if blocking_items else '',
            })

        result_df = pl.DataFrame(results)
        if len(result_df) > 0 and "week" in result_df.columns:
            result_df = result_df.with_columns(
                pl.col("week").cast(pl.Date, strict=False)
            )

        return result_df, remaining_inv
    
    def get_buildability_summary(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get square-set buildability summary by week.
        
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
                pl.col("deployable_sets").sum().alias("total_deployable"),
                pl.col("buildable_sets").sum().alias("total_buildable"),
                pl.col("blocked_sets").sum().alias("total_blocked"),
                pl.col("square_set_id").n_unique().alias("num_square_sets"),
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
        
        Shows which square-sets got allocation and which didn't due to priority.
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
                pl.col("deployable_sets").sum().alias("total_deployable"),
                pl.col("buildable_sets").sum().alias("total_buildable"),
                pl.col("blocked_sets").sum().alias("total_blocked"),
                pl.col("square_set_id").n_unique().alias("num_square_sets"),
            ])
            .with_columns([
                (pl.col("total_buildable") / pl.col("total_deployable") * 100)
                .round(1)
                .alias("completion_rate_pct")
            ])
            .sort("priority_bucket")
        )
        
        return priority_analysis
