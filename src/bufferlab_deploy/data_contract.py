"""
Data contract validation for App 2.

Validates that gold tables from App 1 meet the required schema and constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader, REQUIRED_TABLES
from bufferlab_deploy.sql_utils import get_plan_table


@dataclass
class ContractCheck:
    """Result of a single contract check."""
    name: str
    passed: bool
    message: str
    severity: str = "error"  # error, warning, info
    fix_recommendation: str | None = None


@dataclass
class ContractResult:
    """Overall contract validation result."""
    passed: bool
    checks: list[ContractCheck] = field(default_factory=list)
    warnings: list[ContractCheck] = field(default_factory=list)
    
    @property
    def errors(self) -> list[ContractCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]
    
    @property
    def all_warnings(self) -> list[ContractCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"] + self.warnings


# Required columns for each table
REQUIRED_COLUMNS = {
    "deployment_plan": ["week", "site_id", "kit_id", "kits_planned"],
    "demand_plan": ["week", "site_id", "kit_id", "kits_planned"],
    "bom_kit": ["kit_id", "child_item_id", "qty_per", "effective_start_week"],
    "inventory_position": ["as_of_date", "item_id", "node_id", "on_hand", "usable_on_hand"],
    "supply": ["item_id", "node_id", "qty", "status"],
    "site_readiness": ["scenario_id", "site_id", "week"],
    "item_master": ["item_id", "category", "subcategory"],
    "node_master": ["node_id", "site_id", "node_type"],
    "lane_master": ["from_node_id", "to_node_id", "transfer_lead_time_days"],
    # New table for square-set convergence
    "square_set_master": ["square_set_id", "site_id", "it_rack_kit_id", "callan_kit_id", "mor_kit_id", "power_mw_required"],
}

# Optional columns (used if present)
OPTIONAL_COLUMNS = {
    # deployment_plan: demand_tier for tiered demand support
    "deployment_plan": ["priority", "program_id", "demand_tier"],
    "demand_plan": ["priority", "program_id", "demand_tier"],
    "bom_kit": ["effective_end_week", "revision", "kit_criticality"],
    "inventory_position": ["reserved", "aging_days", "unit_cost"],
    "supply": ["allocation_flag", "confidence_weight"],
    "site_readiness": ["readiness_capacity_kits", "power_ready_mw", "readiness_state"],
    # item_master: extended fields for segmentation
    "item_master": ["uom", "unit_cost", "description", "value_density", "shared_flag", "build_ahead_flag"],
    "node_master": ["region"],
    "lane_master": ["transfer_capacity_units_per_week", "allowed_categories"],
    # lifecycle: extended fields for GPU generation tracking
    "lifecycle": ["generation", "compatibility_group", "transition_start_date", "transition_end_date"],
    # substitution_map: extended fields for substitution types
    "substitution_map": ["substitution_type", "approval_required"],
    # square_set_master is in REQUIRED_COLUMNS but can have optional extensions
    "square_set_master": ["description", "active"],
}

# Valid demand tier values
VALID_DEMAND_TIERS = ["committed", "likely", "exploratory"]

# Valid substitution types
VALID_SUBSTITUTION_TYPES = ["minor_gen", "major_gen", "equivalent"]



class DataContractValidator:
    """
    Validates data contract between App 1 and App 2.
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.result = ContractResult(passed=True)
    
    def validate_all(self) -> ContractResult:
        """
        Run all contract validations.
        
        Returns:
            ContractResult with all check results
        """
        self.result = ContractResult(passed=True)
        
        # Check required tables exist
        self._check_required_tables()
        
        # If required tables missing, stop here
        if not self.result.passed:
            return self.result
        
        # Check required columns
        self._check_required_columns()
        
        # Check referential integrity
        self._check_referential_integrity()
        
        # Check data quality constraints
        self._check_data_constraints()
        
        return self.result
    
    def _add_check(self, check: ContractCheck) -> None:
        """Add a check result."""
        self.result.checks.append(check)
        if not check.passed and check.severity == "error":
            self.result.passed = False
    
    def _check_required_tables(self) -> None:
        """Check that all required tables are present."""
        missing = self.loader.get_missing_required_tables()

        for table in missing:
            self._add_check(ContractCheck(
                name=f"Table exists: {table}",
                passed=False,
                message=f"Required table '{table}' not found in gold path",
                severity="error",
                fix_recommendation=f"Ensure App 1 exports '{table}.parquet' to the gold folder",
            ))

        if (not self.loader.loaded_tables.get("deployment_plan")
                and self.loader.loaded_tables.get("demand_plan")):
            self._add_check(ContractCheck(
                name="Table exists: deployment_plan (fallback)",
                passed=True,
                message="Using demand_plan as fallback for deployment_plan",
                severity="warning",
            ))
        
        # Check for tables that exist
        for table in REQUIRED_TABLES:
            if self.loader.loaded_tables.get(table):
                self._add_check(ContractCheck(
                    name=f"Table exists: {table}",
                    passed=True,
                    message=f"Table '{table}' loaded successfully",
                    severity="info",
                ))
    
    def _check_required_columns(self) -> None:
        """Check that required columns exist in each table."""
        for table_name, required_cols in REQUIRED_COLUMNS.items():
            if not self.loader.loaded_tables.get(table_name):
                continue
            if table_name == "demand_plan" and self.loader.loaded_tables.get("deployment_plan"):
                continue
            
            stats = self.loader.table_stats.get(table_name, {})
            actual_cols = set(stats.get("columns", []))
            
            for col in required_cols:
                if col not in actual_cols:
                    self._add_check(ContractCheck(
                        name=f"Column exists: {table_name}.{col}",
                        passed=False,
                        message=f"Required column '{col}' missing from table '{table_name}'",
                        severity="error",
                        fix_recommendation=f"Update App 1 schema to include '{col}' in {table_name}",
                    ))
                else:
                    self._add_check(ContractCheck(
                        name=f"Column exists: {table_name}.{col}",
                        passed=True,
                        message=f"Column '{col}' present in '{table_name}'",
                        severity="info",
                    ))

            if table_name == "supply":
                date_cols = {"promised_date", "promise_date", "promise_week"}
                if not date_cols.intersection(actual_cols):
                    self._add_check(ContractCheck(
                        name="Column exists: supply.promise_date",
                        passed=False,
                        message="Supply table must include promised_date, promise_date, or promise_week",
                        severity="error",
                        fix_recommendation="Export promise_date (or promise_week) from App 1 supply table",
                    ))
                else:
                    self._add_check(ContractCheck(
                        name="Column exists: supply.promise_date",
                        passed=True,
                        message="Supply date column present",
                        severity="info",
                    ))
    
    def _check_referential_integrity(self) -> None:
        """Check foreign key relationships."""
        # kit_id in deployment_plan must exist in bom_kit
        plan_table = get_plan_table(self.loader)
        if self.loader.loaded_tables.get(plan_table) and self.loader.loaded_tables.get("bom_kit"):
            orphans = self.loader.query(f"""
                SELECT DISTINCT dp.kit_id
                FROM {plan_table} dp
                LEFT JOIN bom_kit bk ON dp.kit_id = bk.kit_id
                WHERE bk.kit_id IS NULL
            """)
            
            if len(orphans) > 0:
                orphan_ids = orphans["kit_id"].to_list()[:5]
                self._add_check(ContractCheck(
                    name="FK: plan.kit_id -> bom_kit",
                    passed=False,
                    message=f"Found {len(orphans)} kit_ids in {plan_table} not in bom_kit: {orphan_ids}",
                    severity="error",
                    fix_recommendation="Ensure all kit_ids in the plan table have BOM definitions in bom_kit",
                ))
            else:
                self._add_check(ContractCheck(
                    name="FK: plan.kit_id -> bom_kit",
                    passed=True,
                    message=f"All {plan_table} kit_ids exist in bom_kit",
                    severity="info",
                ))
        
        # child_item_id in bom_kit must exist in item_master
        if self.loader.loaded_tables.get("bom_kit") and self.loader.loaded_tables.get("item_master"):
            orphans = self.loader.query("""
                SELECT DISTINCT bk.child_item_id
                FROM bom_kit bk
                LEFT JOIN item_master im ON bk.child_item_id = im.item_id
                WHERE im.item_id IS NULL
            """)
            
            if len(orphans) > 0:
                orphan_ids = orphans["child_item_id"].to_list()[:5]
                self._add_check(ContractCheck(
                    name="FK: bom_kit.child_item_id -> item_master",
                    passed=False,
                    message=f"Found {len(orphans)} child_item_ids in bom_kit not in item_master: {orphan_ids}",
                    severity="error",
                    fix_recommendation="Ensure all BOM child items exist in item_master",
                ))
            else:
                self._add_check(ContractCheck(
                    name="FK: bom_kit.child_item_id -> item_master",
                    passed=True,
                    message="All bom_kit child_item_ids exist in item_master",
                    severity="info",
                ))
        
        # node_id in inventory_position must exist in node_master
        if self.loader.loaded_tables.get("inventory_position") and self.loader.loaded_tables.get("node_master"):
            orphans = self.loader.query("""
                SELECT DISTINCT ip.node_id
                FROM inventory_position ip
                LEFT JOIN node_master nm ON ip.node_id = nm.node_id
                WHERE nm.node_id IS NULL
            """)
            
            if len(orphans) > 0:
                orphan_ids = orphans["node_id"].to_list()[:5]
                self._add_check(ContractCheck(
                    name="FK: inventory_position.node_id -> node_master",
                    passed=False,
                    message=f"Found {len(orphans)} node_ids in inventory_position not in node_master: {orphan_ids}",
                    severity="error",
                    fix_recommendation="Ensure all inventory nodes exist in node_master",
                ))
            else:
                self._add_check(ContractCheck(
                    name="FK: inventory_position.node_id -> node_master",
                    passed=True,
                    message="All inventory_position node_ids exist in node_master",
                    severity="info",
                ))
        
        # lane_master nodes must exist in node_master
        if self.loader.loaded_tables.get("lane_master") and self.loader.loaded_tables.get("node_master"):
            orphans = self.loader.query("""
                SELECT DISTINCT lm.from_node_id as node_id
                FROM lane_master lm
                LEFT JOIN node_master nm ON lm.from_node_id = nm.node_id
                WHERE nm.node_id IS NULL
                UNION
                SELECT DISTINCT lm.to_node_id as node_id
                FROM lane_master lm
                LEFT JOIN node_master nm ON lm.to_node_id = nm.node_id
                WHERE nm.node_id IS NULL
            """)
            
            if len(orphans) > 0:
                orphan_ids = orphans["node_id"].to_list()[:5]
                self._add_check(ContractCheck(
                    name="FK: lane_master nodes -> node_master",
                    passed=False,
                    message=f"Found {len(orphans)} nodes in lane_master not in node_master: {orphan_ids}",
                    severity="error",
                    fix_recommendation="Ensure all lane_master from/to nodes exist in node_master",
                ))
            else:
                self._add_check(ContractCheck(
                    name="FK: lane_master nodes -> node_master",
                    passed=True,
                    message="All lane_master nodes exist in node_master",
                    severity="info",
                ))
    
    def _check_data_constraints(self) -> None:
        """Check data quality constraints."""
        # usable_on_hand must be non-negative
        if self.loader.loaded_tables.get("inventory_position"):
            negatives = self.loader.query("""
                SELECT COUNT(*) as cnt FROM inventory_position
                WHERE usable_on_hand < 0
            """)
            
            neg_count = negatives["cnt"].to_list()[0]
            if neg_count > 0:
                self._add_check(ContractCheck(
                    name="usable_on_hand >= 0",
                    passed=False,
                    message=f"Found {neg_count} rows with negative usable_on_hand",
                    severity="error",
                    fix_recommendation="Ensure usable_on_hand = max(on_hand - reserved, 0) in App 1",
                ))
            else:
                self._add_check(ContractCheck(
                    name="usable_on_hand >= 0",
                    passed=True,
                    message="All usable_on_hand values are non-negative",
                    severity="info",
                ))
        
        # Check site_readiness has capacity data
        if self.loader.loaded_tables.get("site_readiness"):
            no_capacity = self.loader.query("""
                SELECT COUNT(*) as cnt FROM site_readiness
                WHERE readiness_capacity_kits IS NULL AND power_ready_mw IS NULL
            """)
            
            no_cap_count = no_capacity["cnt"].to_list()[0]
            if no_cap_count > 0:
                self._add_check(ContractCheck(
                    name="site_readiness has capacity",
                    passed=False,
                    message=f"Found {no_cap_count} site_readiness rows with no capacity data",
                    severity="warning",
                    fix_recommendation="Provide either readiness_capacity_kits or power_ready_mw",
                ))
        
        # Check demand_tier values are valid (if column exists)
        self._check_demand_tier_values()
        
        # Check substitution_type values are valid (if column exists)
        self._check_substitution_type_values()
    
    def _check_demand_tier_values(self) -> None:
        """Check that demand_tier values are valid enums."""
        plan_table = get_plan_table(self.loader)
        if not self.loader.loaded_tables.get(plan_table):
            return
        
        stats = self.loader.table_stats.get(plan_table, {})
        actual_cols = set(stats.get("columns", []))
        
        if "demand_tier" not in actual_cols:
            # Optional column not present, skip check
            return
        
        try:
            invalid_tiers = self.loader.query(f"""
                SELECT DISTINCT demand_tier
                FROM {plan_table}
                WHERE demand_tier IS NOT NULL
                AND demand_tier NOT IN ('committed', 'likely', 'exploratory')
            """)
            
            if len(invalid_tiers) > 0:
                invalid_vals = invalid_tiers["demand_tier"].to_list()[:5]
                self._add_check(ContractCheck(
                    name="demand_tier valid values",
                    passed=False,
                    message=f"Found invalid demand_tier values: {invalid_vals}",
                    severity="warning",
                    fix_recommendation="demand_tier must be one of: committed, likely, exploratory",
                ))
            else:
                self._add_check(ContractCheck(
                    name="demand_tier valid values",
                    passed=True,
                    message="All demand_tier values are valid",
                    severity="info",
                ))
        except Exception:
            pass  # Column may not exist or have different type
    
    def _check_substitution_type_values(self) -> None:
        """Check that substitution_type values are valid enums."""
        if not self.loader.loaded_tables.get("substitution_map"):
            return
        
        stats = self.loader.table_stats.get("substitution_map", {})
        actual_cols = set(stats.get("columns", []))
        
        if "substitution_type" not in actual_cols:
            return
        
        try:
            invalid_types = self.loader.query("""
                SELECT DISTINCT substitution_type
                FROM substitution_map
                WHERE substitution_type IS NOT NULL
                AND substitution_type NOT IN ('minor_gen', 'major_gen', 'equivalent')
            """)
            
            if len(invalid_types) > 0:
                invalid_vals = invalid_types["substitution_type"].to_list()[:5]
                self._add_check(ContractCheck(
                    name="substitution_type valid values",
                    passed=False,
                    message=f"Found invalid substitution_type values: {invalid_vals}",
                    severity="warning",
                    fix_recommendation="substitution_type must be one of: minor_gen, major_gen, equivalent",
                ))
            else:
                self._add_check(ContractCheck(
                    name="substitution_type valid values",
                    passed=True,
                    message="All substitution_type values are valid",
                    severity="info",
                ))
        except Exception:
            pass  # Table may not have this column


def validate_data_contract(loader: DuckDBLoader) -> ContractResult:
    """
    Validate the data contract.
    
    Args:
        loader: DuckDB loader with tables loaded
    
    Returns:
        ContractResult with validation results
    """
    validator = DataContractValidator(loader)
    return validator.validate_all()
