"""
Synthetic data generator for square-set workflows.

Creates a minimal gold dataset with tiered demand, segmentation fields,
square_set_master, and lifecycle transitions for validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import polars as pl


def _to_datetime(d: date | None) -> datetime | None:
    """Convert date to datetime for Polars compatibility."""
    if d is None:
        return None
    return datetime.combine(d, datetime.min.time())


@dataclass
class SyntheticConfig:
    output_dir: Path
    num_sites: int = 2
    num_weeks: int = 8
    num_square_sets: int = 3


def generate_synthetic_gold(config: SyntheticConfig) -> None:
    """
    Generate synthetic App 1 gold tables for local validation.
    
    Features:
    - Multiple scenarios (baseline, optimistic, constrained)
    - Current dates for all time-based fields
    - Stranding conditions (high aging, oversupply)
    - All three demand tiers (committed, likely, exploratory)
    - Active lifecycle transitions
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    # Start 2 weeks ago, extend 12 weeks ahead for realistic date range
    week_start = today - timedelta(weeks=2)
    sites = [f"SITE-{chr(65+i)}" for i in range(config.num_sites)]  # SITE-A, SITE-B
    weeks = [week_start + timedelta(weeks=i) for i in range(config.num_weeks + 4)]
    scenarios = ["baseline", "optimistic", "constrained"]
    tiers = ["committed", "likely", "exploratory"]

    square_sets = []
    bom_rows = []
    deployment_rows = []
    demand_plan_rows = []
    item_master_rows = []
    lifecycle_rows = []
    lead_time_rows = []
    inventory_rows = []
    supply_rows = []
    site_readiness_rows = []
    item_ids_seen = set()

    for site_id in sites:
        for idx in range(config.num_square_sets):
            square_set_id = f"SS-{site_id}-{idx+1:04d}"
            it_kit = f"IT-KIT-{idx+1:03d}"
            callan_kit = f"CALLAN-KIT-{idx+1:03d}"
            mor_kit = f"MOR-KIT-{idx+1:03d}"
            square_sets.append({
                "square_set_id": square_set_id,
                "site_id": site_id,
                "it_rack_kit_id": it_kit,
                "callan_kit_id": callan_kit,
                "mor_kit_id": mor_kit,
                "power_mw_required": 0.5 + (idx * 0.1),
            })

            for kit_id, domain in [
                (it_kit, "Compute"),
                (callan_kit, "Cooling"),
                (mor_kit, "Network"),
            ]:
                for item_idx in range(3):
                    item_id = f"{domain[:3].upper()}-ITEM-{idx+1:03d}-{item_idx+1:02d}"
                    bom_rows.append({
                        "kit_id": kit_id,
                        "child_item_id": item_id,
                        "qty_per": 2,
                        "effective_start_week": _to_datetime(weeks[0]),
                        "kit_criticality": "blocking",
                    })

                    if item_id not in item_ids_seen:
                        item_ids_seen.add(item_id)
                        # Vary cost to create high E&O items
                        high_eo = item_idx == 2
                        unit_cost = 15000 if high_eo else 5000 + (item_idx * 500)
                        
                        item_master_rows.append({
                            "item_id": item_id,
                            "category": domain,
                            "subcategory": f"{domain}-Sub",
                            "value_density": 2.0 if high_eo else 1.5,
                            "shared_flag": item_idx == 0,
                            "build_ahead_flag": high_eo,
                            "unit_cost": unit_cost,
                            "description": f"Synthetic {domain} component",
                        })
                        
                        # Create active lifecycle transitions for some items
                        if item_idx == 0:
                            # Active transition NOW
                            lifecycle_rows.append({
                                "item_id": item_id,
                                "generation": "H100",
                                "compatibility_group": f"CG-{domain}",
                                "transition_start_date": _to_datetime(today - timedelta(days=30)),
                                "transition_end_date": _to_datetime(today + timedelta(days=60)),
                                "status": "active",
                                "ltb_date": _to_datetime(today + timedelta(days=90)),
                                "eol_date": _to_datetime(today + timedelta(days=180)),
                            })
                        elif item_idx == 1:
                            # Future transition
                            lifecycle_rows.append({
                                "item_id": item_id,
                                "generation": "H200",
                                "compatibility_group": f"CG-{domain}",
                                "transition_start_date": _to_datetime(today + timedelta(days=30)),
                                "transition_end_date": _to_datetime(today + timedelta(days=120)),
                                "status": "planned",
                                "ltb_date": _to_datetime(today + timedelta(days=180)),
                                "eol_date": _to_datetime(today + timedelta(days=365)),
                            })
                        else:
                            # No transition, future gen
                            lifecycle_rows.append({
                                "item_id": item_id,
                                "generation": "B100",
                                "compatibility_group": f"CG-{domain}-Future",
                                "transition_start_date": None,
                                "transition_end_date": None,
                                "status": "future",
                                "ltb_date": None,
                                "eol_date": None,
                            })
                        
                        lead_time_rows.append({
                            "item_id": item_id,
                            "lead_time_p95": 45 + item_idx * 15,
                        })

                    # Create stranding conditions for some items
                    is_stranding_item = item_idx == 2 and idx == 0
                    aging = 120 if is_stranding_item else 30 + item_idx * 10
                    on_hand = 200 if is_stranding_item else 50
                    
                    inventory_rows.append({
                        "as_of_date": _to_datetime(today),
                        "item_id": item_id,
                        "node_id": f"{site_id}-NODE",
                        "on_hand": on_hand,
                        "usable_on_hand": int(on_hand * 0.9),
                        "aging_days": aging,
                        "unit_cost": 15000 if is_stranding_item else 5000 + item_idx * 500,
                    })
                    
                    # Oversupply for stranding items
                    supply_qty = 100 if is_stranding_item else 20
                    supply_rows.append({
                        "item_id": item_id,
                        "node_id": f"{site_id}-NODE",
                        "qty": supply_qty,
                        "status": "open",
                        "allocation_flag": False,
                        "confidence_weight": 0.9,
                        "promise_week": _to_datetime(weeks[2]),
                    })

            # Deployment plan with all tiers
            tier = tiers[idx % 3]
            for week in weeks:
                for kit_id in [it_kit, callan_kit, mor_kit]:
                    kits = 4 if tier == "committed" else 2 if tier == "likely" else 1
                    deployment_rows.append({
                        "week": _to_datetime(week),
                        "site_id": site_id,
                        "kit_id": kit_id,
                        "kits_planned": kits,
                        "priority": 10 if tier == "committed" else 20 if tier == "likely" else 30,
                        "program_id": "PROGRAM-A",
                        "demand_tier": tier,
                    })
                    
                    # Also add to demand_plan
                    demand_plan_rows.append({
                        "week": _to_datetime(week),
                        "site_id": site_id,
                        "kit_id": kit_id,
                        "kits_planned": kits,
                        "demand_tier": tier,
                        "demand_type": tier,
                        "priority": 10 if tier == "committed" else 20,
                    })

            # Site readiness for all scenarios
            for scenario_id in scenarios:
                capacity_mult = 1.2 if scenario_id == "optimistic" else 0.8 if scenario_id == "constrained" else 1.0
                for week in weeks:
                    site_readiness_rows.append({
                        "scenario_id": scenario_id,
                        "site_id": site_id,
                        "week": _to_datetime(week),
                        "readiness_capacity_kits": int(8 * capacity_mult),
                        "power_ready_mw": 3.0 * capacity_mult,
                        "readiness_state": "ready",
                    })

    # Write all tables
    pl.DataFrame(square_sets).write_parquet(config.output_dir / "square_set_master.parquet")
    pl.DataFrame(bom_rows).write_parquet(config.output_dir / "bom_kit.parquet")
    pl.DataFrame(deployment_rows).write_parquet(config.output_dir / "deployment_plan.parquet")
    pl.DataFrame(demand_plan_rows).write_parquet(config.output_dir / "demand_plan.parquet")
    pl.DataFrame(item_master_rows).write_parquet(config.output_dir / "item_master.parquet")
    pl.DataFrame(lifecycle_rows).write_parquet(config.output_dir / "lifecycle.parquet")
    pl.DataFrame(lead_time_rows).write_parquet(config.output_dir / "lead_time_history.parquet")
    pl.DataFrame(inventory_rows).write_parquet(config.output_dir / "inventory_position.parquet")
    pl.DataFrame(supply_rows).write_parquet(config.output_dir / "supply.parquet")
    pl.DataFrame(site_readiness_rows).write_parquet(config.output_dir / "site_readiness.parquet")

    # Node and lane master
    node_master = pl.DataFrame([
        {"node_id": f"{site_id}-NODE", "site_id": site_id, "node_type": "site", "region": "NA"}
        for site_id in sites
    ])
    node_master.write_parquet(config.output_dir / "node_master.parquet")

    lane_master = pl.DataFrame([
        {
            "from_node_id": f"{site_id}-NODE",
            "to_node_id": f"{site_id}-NODE",
            "transfer_lead_time_days": 7,
            "transfer_capacity_units_per_week": 100,
        }
        for site_id in sites
    ])
    lane_master.write_parquet(config.output_dir / "lane_master.parquet")

    # Substitution map with multiple entries
    if len(item_master_rows) >= 4:
        substitution_map = pl.DataFrame([
            {
                "from_item_id": item_master_rows[0]["item_id"],
                "to_item_id": item_master_rows[1]["item_id"],
                "substitution_type": "minor_gen",
                "approval_required": False,
            },
            {
                "from_item_id": item_master_rows[2]["item_id"],
                "to_item_id": item_master_rows[3]["item_id"],
                "substitution_type": "major_gen",
                "approval_required": True,
            },
        ])
    else:
        substitution_map = pl.DataFrame([
            {
                "from_item_id": item_master_rows[0]["item_id"],
                "to_item_id": item_master_rows[0]["item_id"],
                "substitution_type": "minor_gen",
                "approval_required": False,
            },
        ])
    substitution_map.write_parquet(config.output_dir / "substitution_map.parquet")

    print(f"Generated synthetic data in {config.output_dir}")
    print(f"  - {len(square_sets)} square sets")
    print(f"  - {len(item_master_rows)} items")
    print(f"  - {len(deployment_rows)} deployment records")
    print(f"  - {len(site_readiness_rows)} site readiness records ({len(scenarios)} scenarios)")
    print(f"  - {len(lifecycle_rows)} lifecycle records")


if __name__ == "__main__":
    from pathlib import Path
    config = SyntheticConfig(
        output_dir=Path("data/gold"),
        num_sites=2,
        num_weeks=12,
        num_square_sets=5,
    )
    generate_synthetic_gold(config)

