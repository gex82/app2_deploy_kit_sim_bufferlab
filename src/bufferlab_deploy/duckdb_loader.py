"""
DuckDB loader for Parquet gold tables.

Loads all gold tables from App 1 into an in-memory DuckDB database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from bufferlab_deploy.config import get_config
from bufferlab_deploy.sql_utils import get_plan_table


# Required tables from App 1
REQUIRED_TABLES = [
    "deployment_plan",
    "bom_kit",
    "inventory_position",
    "supply",
    "site_readiness",
    "item_master",
    "node_master",
    "lane_master",
]

# Optional tables (use if present)
OPTIONAL_TABLES = [
    "demand_plan",
    "lead_time_history",
    "lead_time_distribution",
    "lifecycle",
    "substitution_map",
    "square_set_master",
]


class DuckDBLoader:
    """
    Manages DuckDB connection and table loading.
    """
    
    def __init__(self, gold_path: str | Path | None = None):
        """
        Initialize the loader.
        
        Args:
            gold_path: Path to gold parquet files. Uses config if None.
        """
        config = get_config()
        self.gold_path = Path(gold_path) if gold_path else config.gold_path
        self.conn: duckdb.DuckDBPyConnection | None = None
        self.loaded_tables: dict[str, bool] = {}
        self.table_stats: dict[str, dict[str, Any]] = {}
    
    def connect(self) -> duckdb.DuckDBPyConnection:
        """Create or return existing DuckDB connection."""
        if self.conn is None:
            self.conn = duckdb.connect(":memory:")
        return self.conn
    
    def close(self) -> None:
        """Close the DuckDB connection."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None
    
    def load_all_tables(self) -> dict[str, bool]:
        """
        Load all parquet files from gold_path into DuckDB.
        
        Returns:
            Dict mapping table name to whether it was loaded successfully
        """
        conn = self.connect()
        self.loaded_tables = {}
        self.table_stats = {}
        
        if not self.gold_path.exists():
            return self.loaded_tables
        
        # Load all tables
        for table_name in REQUIRED_TABLES + OPTIONAL_TABLES:
            parquet_path = self.gold_path / f"{table_name}.parquet"
            
            if parquet_path.exists():
                try:
                    # Create table from parquet
                    conn.execute(f"""
                        CREATE OR REPLACE TABLE {table_name} AS
                        SELECT * FROM read_parquet('{parquet_path.as_posix()}')
                    """)
                    
                    # Get stats
                    result = conn.execute(f"SELECT COUNT(*) as cnt FROM {table_name}").fetchone()
                    row_count = result[0] if result else 0
                    
                    columns = conn.execute(f"DESCRIBE {table_name}").fetchall()
                    column_names = [col[0] for col in columns]
                    column_types = {col[0]: col[1] for col in columns}
                    
                    self.loaded_tables[table_name] = True
                    self.table_stats[table_name] = {
                        "row_count": row_count,
                        "columns": column_names,
                        "column_types": column_types,
                        "path": str(parquet_path),
                    }
                except Exception as e:
                    self.loaded_tables[table_name] = False
                    self.table_stats[table_name] = {"error": str(e)}
            else:
                self.loaded_tables[table_name] = False
                self.table_stats[table_name] = {"error": "File not found"}
        
        return self.loaded_tables
    
    def get_table(self, table_name: str) -> pl.DataFrame | None:
        """
        Get a table as a Polars DataFrame.
        
        Args:
            table_name: Name of the table
        
        Returns:
            Polars DataFrame or None if table not loaded
        """
        if not self.loaded_tables.get(table_name):
            return None
        
        conn = self.connect()
        result = conn.execute(f"SELECT * FROM {table_name}").pl()
        return result
    
    def query(self, sql: str) -> pl.DataFrame:
        """
        Execute a SQL query and return results as Polars DataFrame.
        
        Args:
            sql: SQL query string
        
        Returns:
            Polars DataFrame with results
        """
        conn = self.connect()
        return conn.execute(sql).pl()
    
    def execute(self, sql: str) -> Any:
        """
        Execute a SQL statement.
        
        Args:
            sql: SQL statement
        
        Returns:
            Query result
        """
        conn = self.connect()
        return conn.execute(sql)
    
    def get_missing_required_tables(self) -> list[str]:
        """Get list of required tables that are not loaded."""
        missing: list[str] = []
        for table in REQUIRED_TABLES:
            if table == "deployment_plan" and self.loaded_tables.get("demand_plan"):
                continue
            if not self.loaded_tables.get(table):
                missing.append(table)
        return missing

    def get_loader_errors(self) -> "LoaderErrors":
        """
        Collect loader errors for UI display.
        """
        missing_tables = self.get_missing_required_tables()
        missing_columns: dict[str, list[str]] = {}
        type_mismatches: list[str] = []

        try:
            from bufferlab_deploy.data_contract import REQUIRED_COLUMNS
        except Exception:
            REQUIRED_COLUMNS = {}

        plan_table = get_plan_table(self)
        for table_name, required_cols in REQUIRED_COLUMNS.items():
            if table_name == "demand_plan" and self.loaded_tables.get("deployment_plan"):
                continue
            if table_name == "deployment_plan" and plan_table != "deployment_plan":
                continue
            if not self.loaded_tables.get(table_name):
                continue
            stats = self.table_stats.get(table_name, {})
            actual_cols = set(stats.get("columns", []))
            missing = [col for col in required_cols if col not in actual_cols]
            if missing:
                missing_columns[table_name] = missing

        date_columns = {
            "week",
            "effective_start_week",
            "effective_end_week",
            "promised_date",
            "promise_date",
            "promise_week",
            "as_of_date",
        }
        for table_name, stats in self.table_stats.items():
            column_types = stats.get("column_types", {})
            for col in date_columns:
                if col not in column_types:
                    continue
                dtype = str(column_types.get(col, "")).upper()
                if "DATE" not in dtype and "TIMESTAMP" not in dtype:
                    type_mismatches.append(
                        f"{table_name}.{col} is {column_types.get(col)}, expected DATE"
                    )

        return LoaderErrors(
            missing_tables=missing_tables,
            missing_columns=missing_columns,
            type_mismatches=type_mismatches,
        )
    
    def get_loaded_optional_tables(self) -> list[str]:
        """Get list of optional tables that are loaded."""
        return [
            table for table in OPTIONAL_TABLES
            if self.loaded_tables.get(table)
        ]


@dataclass
class LoaderErrors:
    """User-facing loader error details."""
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)
    type_mismatches: list[str] = field(default_factory=list)

    def get_user_messages(self) -> list[str]:
        """Return actionable messages for the UI."""
        messages: list[str] = []
        if self.missing_tables:
            messages.append(
                f"Missing required tables: {', '.join(self.missing_tables)}."
            )
        for table, cols in self.missing_columns.items():
            messages.append(
                f"Missing columns in {table}: {', '.join(cols)}."
            )
        for mismatch in self.type_mismatches:
            messages.append(f"Type mismatch: {mismatch}.")
        return messages

    def get_user_message(self) -> str:
        """Return a single combined message string."""
        return "\n".join(self.get_user_messages())


# Global loader instance
_loader: DuckDBLoader | None = None


def get_loader() -> DuckDBLoader:
    """Get the global DuckDB loader instance."""
    global _loader
    if _loader is None:
        _loader = DuckDBLoader()
        _loader.load_all_tables()
    return _loader


def reset_loader() -> None:
    """Reset the global loader (useful for testing)."""
    global _loader
    if _loader is not None:
        _loader.close()
        _loader = None
