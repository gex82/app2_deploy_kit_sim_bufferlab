"""
Square-Set Engine for multi-domain deployment planning.

A square-set represents the convergence of 3 domains:
- IT Rack (GPU servers, compute)
- Callan/HXU (cooling, power distribution)  
- MOR/Network (fabric, switches, cabling)

All three domains must converge at a site for a deployment to be complete.
"""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.config import get_config


# Domain categories mapping
DOMAIN_CATEGORIES = {
    "it_rack": ["GPU_IT_Rack", "Rack", "Server", "Compute", "Accelerator"],
    "callan": ["Callan", "HXU", "Cooling", "Power", "Sidecar"],
    "mor": ["MOR", "Network", "Front_End_Network", "FEN", "Switch", "Optics", "Cable"],
}


@dataclass
class DomainReadiness:
    """Readiness status for a single domain."""
    domain: str
    required_qty: int
    available_qty: int
    is_ready: bool
    blocking_items: list[str] = field(default_factory=list)
    shortfall_pct: float = 0.0


@dataclass
class SquareSetReadiness:
    """Readiness status for a complete square-set."""
    square_set_id: str
    site_id: str
    week: date
    planned_sets: int
    deployable_sets: int
    buildable_sets: int
    domains: dict[str, DomainReadiness] = field(default_factory=dict)
    is_fully_ready: bool = False
    missing_domains: list[str] = field(default_factory=list)


class SquareSetEngine:
    """
    Engine for square-set explosion and convergence analysis.
    
    Replaces kit-centric planning with multi-domain convergence.
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
    
    def get_or_create_square_set_master(self) -> pl.DataFrame:
        """
        Get square_set_master table or generate synthetic mapping.
        
        If table doesn't exist, creates mapping by grouping kits by site
        and inferring domain from kit category.
        """
        if self.loader.loaded_tables.get("square_set_master"):
            return self.loader.get_table("square_set_master")
        
        # Generate synthetic square-set mapping from kit data
        return self._generate_synthetic_square_sets()
    
    def _generate_synthetic_square_sets(self) -> pl.DataFrame:
        """
        Generate synthetic square-set mapping based on kit categorization.
        
        Groups kits by site and assigns to domains based on item categories.
        """
        # Check which tables are available
        has_deployment = self.loader.loaded_tables.get("deployment_plan")
        has_demand = self.loader.loaded_tables.get("demand_plan")
        has_bom = self.loader.loaded_tables.get("bom_kit")
        
        if not has_bom:
            return pl.DataFrame()
        
        # Get kit-to-domain mapping from BOM + item_master
        try:
            kit_domains = self.loader.query("""
                WITH kit_items AS (
                    SELECT DISTINCT
                        b.kit_id,
                        im.category
                    FROM bom_kit b
                    JOIN item_master im ON b.child_item_id = im.item_id
                ),
                kit_primary_category AS (
                    SELECT 
                        kit_id,
                        category,
                        COUNT(*) as item_count,
                        ROW_NUMBER() OVER (PARTITION BY kit_id ORDER BY COUNT(*) DESC) as rn
                    FROM kit_items
                    GROUP BY kit_id, category
                )
                SELECT kit_id, category
                FROM kit_primary_category
                WHERE rn = 1
            """)
        except Exception as e:
            print(f"Error getting kit domains: {e}")
            return pl.DataFrame()
        
        if len(kit_domains) == 0:
            return pl.DataFrame()
        
        # Map categories to domains
        def map_to_domain(category: str) -> str:
            category_upper = category.upper() if category else ""
            for domain, patterns in DOMAIN_CATEGORIES.items():
                for pattern in patterns:
                    if pattern.upper() in category_upper:
                        return domain
            return "it_rack"  # Default
        
        kit_domains = kit_domains.with_columns([
            pl.col("category").map_elements(map_to_domain, return_dtype=pl.Utf8).alias("domain")
        ])
        
        # Get unique site/kit combinations - handle different table structures
        try:
            if has_deployment:
                site_kits = self.loader.query("""
                    SELECT DISTINCT site_id, kit_id
                    FROM deployment_plan
                """)
            elif has_demand:
                # demand_plan doesn't have kit_id, so pick kits from bom_kit
                # and associate with sites from demand_plan
                site_kits = self.loader.query("""
                    SELECT DISTINCT 
                        dp.site_id,
                        bk.kit_id
                    FROM demand_plan dp
                    CROSS JOIN (SELECT DISTINCT kit_id FROM bom_kit LIMIT 10) bk
                """)
            else:
                return pl.DataFrame()
        except Exception as e:
            print(f"Error getting site/kit combinations: {e}")
            return pl.DataFrame()
        
        # Join to get site/kit/domain
        site_kit_domains = site_kits.join(kit_domains, on="kit_id", how="left")
        
        # Generate square_set_master by grouping
        # Each unique combination of kits at a site forms a potential square-set
        square_sets = []
        
        for site_id in site_kit_domains["site_id"].unique().to_list():
            site_data = site_kit_domains.filter(pl.col("site_id") == site_id)
            
            it_rack_kits = site_data.filter(pl.col("domain") == "it_rack")["kit_id"].to_list()
            callan_kits = site_data.filter(pl.col("domain") == "callan")["kit_id"].to_list()
            mor_kits = site_data.filter(pl.col("domain") == "mor")["kit_id"].to_list()
            
            # Create square sets (cross product or 1:1 based on count)
            max_sets = max(len(it_rack_kits), len(callan_kits), len(mor_kits), 1)
            
            for i in range(max_sets):
                square_sets.append({
                    "square_set_id": f"SS-{site_id}-{i+1:03d}",
                    "site_id": site_id,
                    "it_rack_kit_id": it_rack_kits[i % len(it_rack_kits)] if it_rack_kits else None,
                    "callan_kit_id": callan_kits[i % len(callan_kits)] if callan_kits else None,
                    "mor_kit_id": mor_kits[i % len(mor_kits)] if mor_kits else None,
                    "power_mw_required": self.config.mw_per_kit.get("default", 0.5),
                })
        
        if not square_sets:
            return pl.DataFrame()
        
        result = pl.DataFrame(square_sets)
        
        # Register in DuckDB for queries
        try:
            self.loader.execute("""
                CREATE OR REPLACE TABLE square_set_master AS
                SELECT * FROM result
            """)
        except:
            pass
        
        return result
    
    def explode_square_sets(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Explode square-sets into component requirements by domain.
        
        Aggregates requirements across all 3 domains.
        
        Returns:
            DataFrame with [week, site_id, square_set_id, domain, item_id, required_qty]
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        # Check for required tables
        has_deployment = self.loader.loaded_tables.get("deployment_plan")
        has_bom = self.loader.loaded_tables.get("bom_kit")
        
        if not has_deployment or not has_bom:
            return pl.DataFrame()
        
        square_sets = self.get_or_create_square_set_master()
        
        if len(square_sets) == 0:
            return pl.DataFrame()
        
        # Ensure square_set_master is registered in DuckDB
        try:
            self.loader.conn.execute("SELECT 1 FROM square_set_master LIMIT 1")
        except:
            # Register it
            self.loader.conn.register("square_set_master_df", square_sets.to_arrow())
            self.loader.conn.execute("""
                CREATE OR REPLACE TABLE square_set_master AS 
                SELECT * FROM square_set_master_df
            """)
        
        plan_columns = set(self.loader.table_stats.get("deployment_plan", {}).get("columns", []))
        demand_tier_expr = "dp.demand_tier" if "demand_tier" in plan_columns else "'committed'"

        # Get deployable quantities from deployment plan + readiness
        try:
            result = self.loader.query(f"""
                WITH square_sets AS (
                    SELECT * FROM square_set_master
                ),
                deployment AS (
                    SELECT 
                        dp.week,
                        dp.site_id,
                        dp.kit_id,
                        dp.kits_planned,
                        COALESCE(dp.priority, 50) as priority,
                        {demand_tier_expr} as demand_tier,
                        LEAST(
                            dp.kits_planned,
                            COALESCE(sr.readiness_capacity_kits, dp.kits_planned)
                        ) as deployable
                    FROM deployment_plan dp
                    LEFT JOIN site_readiness sr 
                        ON dp.site_id = sr.site_id 
                        AND dp.week = sr.week 
                        AND sr.scenario_id = '{scenario_id}'
                ),
                -- IT Rack domain requirements
                it_rack_reqs AS (
                    SELECT 
                        d.week,
                        d.site_id,
                        ss.square_set_id,
                        'it_rack' as domain,
                        b.child_item_id as item_id,
                        SUM(d.deployable * b.qty_per) as required_qty,
                        d.priority,
                        d.demand_tier
                    FROM deployment d
                    JOIN square_sets ss ON d.site_id = ss.site_id AND d.kit_id = ss.it_rack_kit_id
                    JOIN bom_kit b ON d.kit_id = b.kit_id
                    WHERE COALESCE(b.kit_criticality, 'blocking') = 'blocking'
                    GROUP BY d.week, d.site_id, ss.square_set_id, b.child_item_id, d.priority, d.demand_tier
                ),
                -- Callan domain requirements
                callan_reqs AS (
                    SELECT 
                        d.week,
                        d.site_id,
                        ss.square_set_id,
                        'callan' as domain,
                        b.child_item_id as item_id,
                        SUM(d.deployable * b.qty_per) as required_qty,
                        d.priority,
                        d.demand_tier
                    FROM deployment d
                    JOIN square_sets ss ON d.site_id = ss.site_id AND d.kit_id = ss.callan_kit_id
                    JOIN bom_kit b ON d.kit_id = b.kit_id
                    WHERE ss.callan_kit_id IS NOT NULL
                        AND COALESCE(b.kit_criticality, 'blocking') = 'blocking'
                    GROUP BY d.week, d.site_id, ss.square_set_id, b.child_item_id, d.priority, d.demand_tier
                ),
                -- MOR/Network domain requirements
                mor_reqs AS (
                    SELECT 
                        d.week,
                        d.site_id,
                        ss.square_set_id,
                        'mor' as domain,
                        b.child_item_id as item_id,
                        SUM(d.deployable * b.qty_per) as required_qty,
                        d.priority,
                        d.demand_tier
                    FROM deployment d
                    JOIN square_sets ss ON d.site_id = ss.site_id AND d.kit_id = ss.mor_kit_id
                    JOIN bom_kit b ON d.kit_id = b.kit_id
                    WHERE ss.mor_kit_id IS NOT NULL
                        AND COALESCE(b.kit_criticality, 'blocking') = 'blocking'
                    GROUP BY d.week, d.site_id, ss.square_set_id, b.child_item_id, d.priority, d.demand_tier
                )
                SELECT * FROM it_rack_reqs
                UNION ALL
                SELECT * FROM callan_reqs
                UNION ALL
                SELECT * FROM mor_reqs
                ORDER BY week, site_id, square_set_id, domain, item_id
            """)
        except Exception as e:
            print(f"Error in explode_square_sets: {e}")
            return pl.DataFrame()
        
        return result
    
    def get_domain_readiness(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get readiness status by domain for each site/week.
        
        Shows which domains are ready vs blocking deployment.
        """
        if scenario_id is None:
            scenario_id = self.config.analysis.default_scenario
        
        reqs = self.explode_square_sets(scenario_id)
        
        if len(reqs) == 0:
            return pl.DataFrame()
        
        # Get availability by item/site
        availability = self.loader.query("""
            SELECT 
                nm.site_id,
                ip.item_id,
                SUM(ip.usable_on_hand) as available
            FROM inventory_position ip
            JOIN node_master nm ON ip.node_id = nm.node_id
            GROUP BY nm.site_id, ip.item_id
        """)
        
        # Join requirements with availability
        result = (
            reqs
            .join(availability, on=["site_id", "item_id"], how="left")
            .with_columns([
                pl.col("available").fill_null(0),
            ])
            .group_by(["week", "site_id", "square_set_id", "domain"])
            .agg([
                pl.col("required_qty").sum().alias("total_required"),
                pl.col("available").sum().alias("total_available"),
                (pl.col("required_qty") > pl.col("available")).sum().alias("items_short"),
            ])
            .with_columns([
                (pl.col("total_available") >= pl.col("total_required")).alias("is_ready"),
                ((pl.col("total_required") - pl.col("total_available")).clip(0, None) 
                 / pl.col("total_required") * 100).round(1).alias("shortfall_pct"),
            ])
            .sort(["week", "site_id", "square_set_id", "domain"])
        )
        
        return result
    
    def get_convergence_summary(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get convergence summary showing which square-sets are fully deployable.
        
        A square-set is only deployable when ALL domains are ready.
        """
        domain_readiness = self.get_domain_readiness(scenario_id)
        
        if len(domain_readiness) == 0:
            return pl.DataFrame()
        
        summary = (
            domain_readiness
            .group_by(["week", "site_id", "square_set_id"])
            .agg([
                pl.col("is_ready").all().alias("all_domains_ready"),
                pl.col("domain").filter(~pl.col("is_ready")).alias("missing_domains"),
                pl.col("is_ready").sum().alias("domains_ready_count"),
            ])
            .with_columns([
                pl.lit(3).alias("total_domains"),
            ])
            .sort(["week", "site_id", "square_set_id"])
        )

        # Power readiness (optional)
        if self.loader.loaded_tables.get("site_readiness"):
            sr_cols = set(self.loader.table_stats.get("site_readiness", {}).get("columns", []))
            if "power_ready_mw" in sr_cols:
                square_sets = self.get_or_create_square_set_master()
                if len(square_sets) > 0:
                    power_req = square_sets.select([
                        "square_set_id",
                        pl.col("power_mw_required").fill_null(0).alias("power_mw_required"),
                    ])
                    power_ready = self.loader.query("""
                        SELECT site_id, week, power_ready_mw
                        FROM site_readiness
                        WHERE power_ready_mw IS NOT NULL
                    """)

                    summary = summary.join(power_req, on="square_set_id", how="left").join(
                        power_ready,
                        on=["site_id", "week"],
                        how="left"
                    ).with_columns([
                        pl.col("power_mw_required").fill_null(0),
                    ]).with_columns([
                        pl.when(pl.col("power_ready_mw").is_null())
                        .then(pl.lit(True))
                        .otherwise(pl.col("power_ready_mw") >= pl.col("power_mw_required"))
                        .alias("power_ready")
                    ])

                    summary = summary.with_columns([
                        (pl.col("all_domains_ready") & pl.col("power_ready")).alias("all_domains_ready"),
                        pl.when(~pl.col("power_ready"))
                        .then(pl.col("missing_domains").list.concat(pl.lit(["power"])))
                        .otherwise(pl.col("missing_domains"))
                        .alias("missing_domains"),
                        pl.lit(4).alias("total_domains"),
                    ])

        return summary
    
    def get_weekly_convergence_stats(self, scenario_id: str | None = None) -> pl.DataFrame:
        """
        Get weekly rollup of convergence statistics.
        """
        convergence = self.get_convergence_summary(scenario_id)
        
        if len(convergence) == 0:
            return pl.DataFrame()
        
        stats = (
            convergence
            .group_by("week")
            .agg([
                pl.count().alias("total_square_sets"),
                pl.col("all_domains_ready").sum().alias("fully_ready_sets"),
                (~pl.col("all_domains_ready")).sum().alias("blocked_sets"),
            ])
            .with_columns([
                (pl.col("fully_ready_sets") / pl.col("total_square_sets") * 100)
                .round(1)
                .alias("convergence_rate_pct"),
            ])
            .sort("week")
        )
        
        return stats
