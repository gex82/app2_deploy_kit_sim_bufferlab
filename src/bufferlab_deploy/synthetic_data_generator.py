"""
Synthetic Data Generator for BufferLab testing.

Generates realistic synthetic data for square-set convergence testing
with all extended schema fields.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl


class SyntheticDataGenerator:
    """
    Generate synthetic data for BufferLab testing.
    
    Creates complete datasets with:
    - Square sets across multiple sites
    - Tiered demand (committed/likely/exploratory)
    - Extended item_master fields (value_density, shared_flag, etc.)
    - Lifecycle with GPU generation tracking
    - Substitution maps with types
    """
    
    # Domain categories for square sets
    DOMAINS = {
        "it_rack": ["GPU_Server", "Compute_Node", "AI_Accelerator"],
        "callan": ["HXU_Unit", "Sidecar_Cooler", "Power_Distribution"],
        "mor": ["Network_Switch", "Fiber_Optics", "Cabling_Kit"],
    }
    
    # GPU generations
    GPU_GENERATIONS = ["H100", "H200", "B100", "B200", "GB200"]
    
    def __init__(
        self,
        num_sites: int = 3,
        num_square_sets_per_site: int = 5,
        num_items_per_domain: int = 10,
        num_weeks: int = 12,
        seed: int = 42,
    ):
        self.num_sites = num_sites
        self.num_square_sets_per_site = num_square_sets_per_site
        self.num_items_per_domain = num_items_per_domain
        self.num_weeks = num_weeks
        random.seed(seed)
        
        # Generated data storage
        self.sites: list[str] = []
        self.items: list[dict[str, Any]] = []
        self.kits: list[dict[str, Any]] = []
        self.square_sets: list[dict[str, Any]] = []
    
    def generate_all(self, output_dir: str | Path) -> dict[str, Path]:
        """
        Generate all synthetic data tables and save to parquet.
        
        Args:
            output_dir: Directory to save parquet files
        
        Returns:
            Dict of table names to file paths
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate in dependency order
        self._generate_sites()
        self._generate_items()
        self._generate_kits()
        self._generate_square_sets()
        
        files = {}
        
        # Save all tables
        files["item_master"] = self._save_item_master(output_dir)
        files["lifecycle"] = self._save_lifecycle(output_dir)
        files["node_master"] = self._save_node_master(output_dir)
        files["bom_kit"] = self._save_bom_kit(output_dir)
        files["deployment_plan"] = self._save_deployment_plan(output_dir)
        files["demand_plan"] = self._save_demand_plan(output_dir)
        files["supply"] = self._save_supply(output_dir)
        files["inventory_position"] = self._save_inventory(output_dir)
        files["site_readiness"] = self._save_site_readiness(output_dir)
        files["lane_master"] = self._save_lane_master(output_dir)
        files["square_set_master"] = self._save_square_set_master(output_dir)
        files["substitution_map"] = self._save_substitution_map(output_dir)
        files["lead_time_history"] = self._save_lead_time_history(output_dir)
        
        return files
    
    def _generate_sites(self) -> None:
        """Generate site IDs."""
        self.sites = [f"SITE-{chr(65 + i)}" for i in range(self.num_sites)]
    
    def _generate_items(self) -> None:
        """Generate items with extended fields."""
        item_id = 0
        
        for domain, categories in self.DOMAINS.items():
            for _ in range(self.num_items_per_domain):
                category = random.choice(categories)
                is_blocking = random.random() < 0.6  # 60% are blockers
                
                self.items.append({
                    "item_id": f"ITEM-{item_id:04d}",
                    "category": category,
                    "subcategory": f"{domain.upper()}_SUB",
                    "description": f"{category} component {item_id}",
                    "uom": "EA",
                    "unit_cost": round(random.uniform(100, 50000), 2),
                    # Extended fields for segmentation
                    "value_density": round(random.uniform(0.1, 10.0), 2),
                    "shared_flag": random.random() < 0.3,  # 30% shared
                    "build_ahead_flag": random.random() < 0.2,  # 20% build-ahead
                    # Additional metadata
                    "domain": domain,
                    "is_blocking": is_blocking,
                    "lead_time_p95": random.randint(7, 90),
                })
                item_id += 1
    
    def _generate_kits(self) -> None:
        """Generate kits with BOM relationships."""
        kit_id = 0
        
        for domain in self.DOMAINS.keys():
            # Items for this domain
            domain_items = [i for i in self.items if i["domain"] == domain]
            
            # Create kits for this domain
            for site in self.sites:
                for _ in range(self.num_square_sets_per_site):
                    kit = {
                        "kit_id": f"KIT-{domain.upper()}-{kit_id:04d}",
                        "site_id": site,
                        "domain": domain,
                        "description": f"{domain.title()} kit {kit_id}",
                        "items": random.sample(
                            domain_items, 
                            min(5, len(domain_items))
                        ),
                    }
                    self.kits.append(kit)
                    kit_id += 1
    
    def _generate_square_sets(self) -> None:
        """Generate square sets linking 3 domains."""
        for site in self.sites:
            site_kits = [k for k in self.kits if k["site_id"] == site]
            
            it_rack_kits = [k for k in site_kits if k["domain"] == "it_rack"]
            callan_kits = [k for k in site_kits if k["domain"] == "callan"]
            mor_kits = [k for k in site_kits if k["domain"] == "mor"]
            
            for i in range(min(len(it_rack_kits), len(callan_kits), len(mor_kits))):
                self.square_sets.append({
                    "square_set_id": f"SS-{site}-{i:04d}",
                    "site_id": site,
                    "it_rack_kit_id": it_rack_kits[i]["kit_id"],
                    "callan_kit_id": callan_kits[i]["kit_id"],
                    "mor_kit_id": mor_kits[i]["kit_id"],
                    "power_mw_required": round(random.uniform(0.3, 1.5), 2),
                })
    
    def _save_item_master(self, output_dir: Path) -> Path:
        """Save item_master table."""
        df = pl.DataFrame([
            {
                "item_id": i["item_id"],
                "category": i["category"],
                "subcategory": i["subcategory"],
                "description": i["description"],
                "uom": i["uom"],
                "unit_cost": i["unit_cost"],
                "value_density": i["value_density"],
                "shared_flag": i["shared_flag"],
                "build_ahead_flag": i["build_ahead_flag"],
            }
            for i in self.items
        ])
        path = output_dir / "item_master.parquet"
        df.write_parquet(path)
        return path
    
    def _save_lifecycle(self, output_dir: Path) -> Path:
        """Save lifecycle table with generation tracking."""
        today = date.today()
        
        records = []
        for i, item in enumerate(self.items):
            gen = self.GPU_GENERATIONS[i % len(self.GPU_GENERATIONS)]
            
            # Random lifecycle dates
            ltb_offset = random.randint(30, 365)
            eol_offset = ltb_offset + random.randint(60, 180)
            transition_start = random.randint(-30, 60)
            transition_end = transition_start + random.randint(30, 90)
            
            records.append({
                "item_id": item["item_id"],
                "generation": gen,
                "compatibility_group": f"CG-{gen}",
                "ltb_date": today + timedelta(days=ltb_offset),
                "eol_date": today + timedelta(days=eol_offset),
                "transition_start_date": today + timedelta(days=transition_start),
                "transition_end_date": today + timedelta(days=transition_end),
            })
        
        df = pl.DataFrame(records)
        path = output_dir / "lifecycle.parquet"
        df.write_parquet(path)
        return path
    
    def _save_node_master(self, output_dir: Path) -> Path:
        """Save node_master table."""
        records = []
        
        for site in self.sites:
            # Site node
            records.append({
                "node_id": f"{site}-SITE",
                "site_id": site,
                "node_type": "site",
                "region": f"REGION-{site[-1]}",
            })
            # Integration node
            records.append({
                "node_id": f"{site}-INT",
                "site_id": site,
                "node_type": "integration",
                "region": f"REGION-{site[-1]}",
            })
        
        # Regional hubs
        for region in ["REGION-A", "REGION-B", "REGION-C"]:
            records.append({
                "node_id": f"{region}-HUB",
                "site_id": "",
                "node_type": "regional",
                "region": region,
            })
        
        df = pl.DataFrame(records)
        path = output_dir / "node_master.parquet"
        df.write_parquet(path)
        return path
    
    def _save_bom_kit(self, output_dir: Path) -> Path:
        """Save BOM kit table."""
        today = date.today()
        records = []
        
        for kit in self.kits:
            for item in kit["items"]:
                records.append({
                    "kit_id": kit["kit_id"],
                    "child_item_id": item["item_id"],
                    "qty_per": random.randint(1, 10),
                    "effective_start_week": today - timedelta(weeks=4),
                    "effective_end_week": today + timedelta(weeks=52),
                    "revision": "A",
                    "kit_criticality": "blocking" if item["is_blocking"] else "non-blocking",
                })
        
        df = pl.DataFrame(records)
        path = output_dir / "bom_kit.parquet"
        df.write_parquet(path)
        return path
    
    def _save_deployment_plan(self, output_dir: Path) -> Path:
        """Save deployment plan with demand tiers."""
        today = date.today()
        records = []
        
        demand_tiers = ["committed", "likely", "exploratory"]
        tier_weights = [0.5, 0.35, 0.15]  # Distribution
        
        for week_offset in range(self.num_weeks):
            week = today + timedelta(weeks=week_offset)
            
            for kit in self.kits:
                tier = random.choices(demand_tiers, tier_weights)[0]
                priority = random.randint(1, 100)
                
                records.append({
                    "week": week,
                    "site_id": kit["site_id"],
                    "kit_id": kit["kit_id"],
                    "kits_planned": random.randint(1, 20),
                    "priority": priority,
                    "program_id": f"PROG-{random.randint(1, 5)}",
                    "demand_tier": tier,
                })
        
        df = pl.DataFrame(records)
        path = output_dir / "deployment_plan.parquet"
        df.write_parquet(path)
        return path
    
    def _save_demand_plan(self, output_dir: Path) -> Path:
        """Save demand plan (copy of deployment plan for compatibility)."""
        today = date.today()
        records = []
        
        demand_tiers = ["committed", "likely", "exploratory"]
        tier_weights = [0.5, 0.35, 0.15]
        
        for week_offset in range(self.num_weeks):
            week = today + timedelta(weeks=week_offset)
            
            for item in self.items:
                tier = random.choices(demand_tiers, tier_weights)[0]
                
                records.append({
                    "week": week,
                    "site_id": random.choice(self.sites),
                    "kit_id": random.choice(self.kits)["kit_id"],
                    "item_id": item["item_id"],
                    "qty": random.randint(10, 500),
                    "kits_planned": random.randint(1, 20),
                    "demand_tier": tier,
                })
        
        df = pl.DataFrame(records)
        path = output_dir / "demand_plan.parquet"
        df.write_parquet(path)
        return path
    
    def _save_supply(self, output_dir: Path) -> Path:
        """Save supply table."""
        today = date.today()
        records = []
        
        for item in self.items:
            for site in self.sites:
                # Multiple supply records per item
                for _ in range(random.randint(1, 3)):
                    records.append({
                        "item_id": item["item_id"],
                        "node_id": f"{site}-INT",
                        "qty": random.randint(50, 1000),
                        "status": random.choice(["confirmed", "planned", "at_risk"]),
                        "promise_date": today + timedelta(days=random.randint(7, 60)),
                        "allocation_flag": random.random() < 0.2,
                        "confidence_weight": round(random.uniform(0.5, 1.0), 2),
                    })
        
        df = pl.DataFrame(records)
        path = output_dir / "supply.parquet"
        df.write_parquet(path)
        return path
    
    def _save_inventory(self, output_dir: Path) -> Path:
        """Save inventory position."""
        today = date.today()
        records = []
        
        for item in self.items:
            for site in self.sites:
                on_hand = random.randint(0, 500)
                reserved = random.randint(0, min(on_hand, 100))
                
                records.append({
                    "as_of_date": today,
                    "item_id": item["item_id"],
                    "node_id": f"{site}-INT",
                    "on_hand": on_hand,
                    "usable_on_hand": max(0, on_hand - reserved),
                    "reserved": reserved,
                    "aging_days": random.randint(0, 120),
                    "unit_cost": item["unit_cost"],
                })
        
        df = pl.DataFrame(records)
        path = output_dir / "inventory_position.parquet"
        df.write_parquet(path)
        return path
    
    def _save_site_readiness(self, output_dir: Path) -> Path:
        """Save site readiness."""
        today = date.today()
        records = []
        
        for week_offset in range(self.num_weeks):
            week = today + timedelta(weeks=week_offset)
            
            for site in self.sites:
                records.append({
                    "scenario_id": "baseline",
                    "site_id": site,
                    "week": week,
                    "readiness_capacity_kits": random.randint(10, 50),
                    "power_ready_mw": round(random.uniform(1.0, 10.0), 2),
                    "readiness_state": random.choice(["ready", "pending", "blocked"]),
                })
        
        df = pl.DataFrame(records)
        path = output_dir / "site_readiness.parquet"
        df.write_parquet(path)
        return path
    
    def _save_lane_master(self, output_dir: Path) -> Path:
        """Save lane master for transfers."""
        records = []
        
        for site in self.sites:
            region = f"REGION-{site[-1]}"
            
            # Regional hub to integration
            records.append({
                "from_node_id": f"{region}-HUB",
                "to_node_id": f"{site}-INT",
                "transfer_lead_time_days": random.randint(3, 10),
                "transfer_capacity_units_per_week": random.randint(100, 500),
                "allowed_categories": "ALL",
            })
            
            # Integration to site
            records.append({
                "from_node_id": f"{site}-INT",
                "to_node_id": f"{site}-SITE",
                "transfer_lead_time_days": random.randint(1, 3),
                "transfer_capacity_units_per_week": random.randint(200, 800),
                "allowed_categories": "ALL",
            })
        
        df = pl.DataFrame(records)
        path = output_dir / "lane_master.parquet"
        df.write_parquet(path)
        return path
    
    def _save_square_set_master(self, output_dir: Path) -> Path:
        """Save square set master table."""
        df = pl.DataFrame(self.square_sets)
        path = output_dir / "square_set_master.parquet"
        df.write_parquet(path)
        return path
    
    def _save_substitution_map(self, output_dir: Path) -> Path:
        """Save substitution map with types."""
        records = []
        
        # Create some substitution relationships between items
        for i in range(0, len(self.items) - 1, 2):
            from_item = self.items[i]
            to_item = self.items[i + 1]
            
            sub_type = random.choice(["minor_gen", "major_gen", "equivalent"])
            
            records.append({
                "from_item_id": from_item["item_id"],
                "to_item_id": to_item["item_id"],
                "substitution_type": sub_type,
                "approval_required": sub_type == "major_gen",
                "effective_date": date.today(),
                "ratio": 1.0,
            })
        
        df = pl.DataFrame(records)
        path = output_dir / "substitution_map.parquet"
        df.write_parquet(path)
        return path
    
    def _save_lead_time_history(self, output_dir: Path) -> Path:
        """Save lead time history for items."""
        records = []
        
        for item in self.items:
            records.append({
                "item_id": item["item_id"],
                "lead_time_p50": item["lead_time_p95"] * 0.7,
                "lead_time_p95": item["lead_time_p95"],
                "lead_time_avg": item["lead_time_p95"] * 0.8,
                "sample_size": random.randint(10, 100),
            })
        
        df = pl.DataFrame(records)
        path = output_dir / "lead_time_history.parquet"
        df.write_parquet(path)
        return path


def generate_synthetic_data(
    output_dir: str = "./data/gold",
    num_sites: int = 3,
    num_square_sets_per_site: int = 5,
    seed: int = 42,
) -> dict[str, Path]:
    """
    Convenience function to generate synthetic data.
    
    Args:
        output_dir: Where to save parquet files
        num_sites: Number of sites to generate
        num_square_sets_per_site: Square sets per site
        seed: Random seed for reproducibility
    
    Returns:
        Dict of table names to file paths
    """
    generator = SyntheticDataGenerator(
        num_sites=num_sites,
        num_square_sets_per_site=num_square_sets_per_site,
        seed=seed,
    )
    return generator.generate_all(output_dir)


if __name__ == "__main__":
    import sys
    
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "./data/gold_synthetic"
    
    print(f"Generating synthetic data to: {output_dir}")
    files = generate_synthetic_data(output_dir)
    
    print("\nGenerated files:")
    for name, path in files.items():
        print(f"  {name}: {path}")
    
    print("\n✓ Synthetic data generation complete!")
