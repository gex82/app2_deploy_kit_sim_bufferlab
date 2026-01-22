"""
SQL helpers for dynamic schema handling.
"""

from __future__ import annotations

from bufferlab_deploy.duckdb_loader import DuckDBLoader


def get_plan_table(loader: DuckDBLoader) -> str:
    """Return the preferred plan table name."""
    if loader.loaded_tables.get("deployment_plan"):
        return "deployment_plan"
    if loader.loaded_tables.get("demand_plan"):
        return "demand_plan"
    return "deployment_plan"


def get_supply_date_column(loader: DuckDBLoader) -> str | None:
    """Return the available supply date column name."""
    columns = loader.table_stats.get("supply", {}).get("columns", [])
    for name in ("promised_date", "promise_date", "promise_week"):
        if name in columns:
            return name
    return None


def get_supply_week_expr(loader: DuckDBLoader) -> str:
    """Return a SQL expression that yields a week-start date for supply."""
    col = get_supply_date_column(loader)
    if col is None:
        return "NULL"
    return f"CAST(DATE_TRUNC('week', TRY_CAST({col} AS DATE)) AS DATE)"


def get_bom_effective_clause(loader: DuckDBLoader, week_col: str, alias: str = "b") -> str:
    """Return a SQL clause for BOM effective date filtering."""
    columns = loader.table_stats.get("bom_kit", {}).get("columns", [])
    if "effective_end_week" in columns:
        return (
            f"{alias}.effective_start_week <= {week_col} "
            f"AND ({alias}.effective_end_week IS NULL OR {alias}.effective_end_week >= {week_col})"
        )
    return f"{alias}.effective_start_week <= {week_col}"


def get_readiness_capacity_expr(loader: DuckDBLoader, default_mw_per_kit: float) -> str:
    """Return a SQL expression for readiness capacity."""
    columns = loader.table_stats.get("site_readiness", {}).get("columns", [])
    has_kits = "readiness_capacity_kits" in columns
    has_mw = "power_ready_mw" in columns
    if has_kits and has_mw:
        return (
            "CASE "
            "WHEN readiness_capacity_kits IS NOT NULL THEN readiness_capacity_kits "
            f"WHEN power_ready_mw IS NOT NULL THEN CAST(power_ready_mw / {default_mw_per_kit} AS INTEGER) "
            "ELSE NULL END"
        )
    if has_kits:
        return "readiness_capacity_kits"
    if has_mw:
        return f"CAST(power_ready_mw / {default_mw_per_kit} AS INTEGER)"
    return "NULL"
