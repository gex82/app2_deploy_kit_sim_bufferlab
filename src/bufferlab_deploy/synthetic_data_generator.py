"""
Synthetic data generator for square-set workflows.

Creates a minimal gold dataset with tiered demand, segmentation fields,
square_set_master, and lifecycle transitions for validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl


@dataclass
class SyntheticConfig:
    output_dir: Path
    num_sites: int = 2
    num_weeks: int = 8
    num_square_sets: int = 3


def generate_synthetic_gold(config: SyntheticConfig) -> None:
    """
    Generate synthetic App 1 gold tables for local validation.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    sites = [f"SITE-{i+1:02d}" for i in range(config.num_sites)]
    weeks = [today + timedelta(weeks=i) for i in range(config.num_weeks)]

    square_sets = []
    bom_rows = []
    deployment_rows = []
    item_master_rows = []
    lifecycle_rows = []
    lead_time_rows = []
    inventory_rows = []
    supply_rows = []
    site_readiness_rows = []

    for site_id in sites:
        for idx in range(config.num_square_sets):
            square_set_id = f"SS-{site_id}-{idx+1:03d}"
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
                        "effective_start_week": weeks[0],
                        "kit_criticality": "blocking",
                    })

                    if item_id not in {row["item_id"] for row in item_master_rows}:
                        item_master_rows.append({
                            "item_id": item_id,
                            "category": domain,
                            "subcategory": f"{domain}-Sub",
                            "value_density": 1.5,
                            "shared_flag": item_idx == 0,
                            "build_ahead_flag": False,
                            "unit_cost": 5000 + (item_idx * 250),
                            "description": f"Synthetic {domain} component",
                        })
                        lifecycle_rows.append({
                            "item_id": item_id,
                            "generation": "GenA" if item_idx < 2 else "GenB",
                            "compatibility_group": f"{domain}-Group",
                            "transition_start_date": today - timedelta(days=7),
                            "transition_end_date": today + timedelta(days=90),
                            "status": "active",
                            "ltb_date": today + timedelta(days=180),
                            "eol_date": today + timedelta(days=365),
                        })
                        lead_time_rows.append({
                            "item_id": item_id,
                            "lead_time_p95": 45 + item_idx * 10,
                        })

                    inventory_rows.append({
                        "as_of_date": today,
                        "item_id": item_id,
                        "node_id": f"{site_id}-NODE",
                        "on_hand": 50,
                        "usable_on_hand": 45,
                        "aging_days": 30 + item_idx * 5,
                        "unit_cost": 5000 + item_idx * 250,
                    })
                    supply_rows.append({
                        "item_id": item_id,
                        "node_id": f"{site_id}-NODE",
                        "qty": 20,
                        "status": "open",
                        "allocation_flag": False,
                        "confidence_weight": 0.9,
                        "promise_week": weeks[0],
                    })

            for week in weeks:
                deployment_rows.append({
                    "week": week,
                    "site_id": site_id,
                    "kit_id": it_kit,
                    "kits_planned": 4,
                    "priority": 10,
                    "program_id": "BASE",
                    "demand_tier": "committed" if idx == 0 else "likely",
                })
                deployment_rows.append({
                    "week": week,
                    "site_id": site_id,
                    "kit_id": callan_kit,
                    "kits_planned": 4,
                    "priority": 20,
                    "program_id": "BASE",
                    "demand_tier": "committed" if idx == 0 else "likely",
                })
                deployment_rows.append({
                    "week": week,
                    "site_id": site_id,
                    "kit_id": mor_kit,
                    "kits_planned": 4,
                    "priority": 30,
                    "program_id": "BASE",
                    "demand_tier": "committed" if idx == 0 else "likely",
                })

                site_readiness_rows.append({
                    "scenario_id": "BASE",
                    "site_id": site_id,
                    "week": week,
                    "readiness_capacity_kits": 6,
                    "power_ready_mw": 3.0,
                })

    pl.DataFrame(square_sets).write_parquet(config.output_dir / "square_set_master.parquet")
    pl.DataFrame(bom_rows).write_parquet(config.output_dir / "bom_kit.parquet")
    pl.DataFrame(deployment_rows).write_parquet(config.output_dir / "deployment_plan.parquet")
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

    substitution_map = pl.DataFrame([
        {
            "from_item_id": item_master_rows[0]["item_id"],
            "to_item_id": item_master_rows[1]["item_id"],
            "substitution_type": "minor_gen",
            "approval_required": False,
        }
    ])
    substitution_map.write_parquet(config.output_dir / "substitution_map.parquet")
