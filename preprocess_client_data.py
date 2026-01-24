"""
Client flat-file preprocessing utility for App 2.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import yaml


@dataclass
class ValidationResult:
    """Validation result for a single table."""
    table_name: str
    input_path: str
    output_path: str | None
    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int = 0


class ClientDataPreprocessor:
    """
    Validates and converts client flat files to App 2 format.
    """

    def __init__(self, mapping_file: str = "configs/column_mapping.yml", output_dir: str = "data/gold"):
        self.mapping_file = Path(mapping_file)
        self.output_dir = Path(output_dir)
        self.mapping = self._load_mapping()
        self.results: list[ValidationResult] = []

    def _load_mapping(self) -> dict:
        if not self.mapping_file.exists():
            raise FileNotFoundError(f"Mapping file not found: {self.mapping_file}")
        data = yaml.safe_load(self.mapping_file.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError("Mapping file must contain a top-level dictionary.")
        return data

    def _normalize(self, name: str) -> str:
        return (
            name.strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
        )

    def _read_input(self, input_path: Path) -> pl.DataFrame:
        ext = input_path.suffix.lower()
        if ext in {".parquet", ".pq"}:
            return pl.read_parquet(input_path)
        if ext in {".csv", ".tsv", ".txt"}:
            separator = "\t" if ext == ".tsv" else ","
            return pl.read_csv(input_path, separator=separator, try_parse_dates=True)
        if ext in {".xlsx", ".xls"}:
            try:
                import pandas as pd
            except ImportError as exc:
                raise ImportError("pandas is required to read Excel files.") from exc
            df = pd.read_excel(input_path)
            return pl.from_pandas(df)
        raise ValueError(f"Unsupported input format: {input_path.suffix}")

    def _apply_column_mapping(self, df: pl.DataFrame, mapping: dict) -> tuple[pl.DataFrame, list[str]]:
        aliases = mapping.get("aliases", {})
        warnings: list[str] = []
        normalized_cols = {self._normalize(col): col for col in df.columns}

        for target, alias_list in aliases.items():
            if target in df.columns:
                continue
            candidates = []
            for alias in [target] + list(alias_list or []):
                key = self._normalize(str(alias))
                if key in normalized_cols:
                    candidates.append(normalized_cols[key])
            if len(candidates) > 1:
                warnings.append(
                    f"Multiple columns match {target}: {candidates}. Using {candidates[0]}."
                )
            if candidates:
                source = candidates[0]
                if source != target:
                    df = df.rename({source: target})
        return df, warnings

    def _coerce_types(self, df: pl.DataFrame) -> pl.DataFrame:
        date_cols = [
            "week",
            "promise_week",
            "promised_date",
            "promise_date",
            "as_of_date",
            "effective_start_week",
            "effective_end_week",
        ]
        numeric_cols = [
            "qty",
            "kits_planned",
            "square_sets_planned",
            "qty_per",
            "usable_on_hand",
            "on_hand",
            "power_ready_mw",
            "readiness_capacity_kits",
            "transfer_lead_time_days",
            "transfer_capacity_units_per_week",
        ]

        for col in date_cols:
            if col in df.columns:
                df = df.with_columns([pl.col(col).cast(pl.Date, strict=False)])
        for col in numeric_cols:
            if col in df.columns:
                df = df.with_columns([pl.col(col).cast(pl.Float64, strict=False)])
        return df

    def process_file(self, input_path: str, table_name: str) -> ValidationResult:
        """
        Process a single file and write parquet to the gold folder.
        """
        path = Path(input_path)
        errors: list[str] = []
        warnings: list[str] = []

        if table_name not in self.mapping:
            return ValidationResult(
                table_name=table_name,
                input_path=str(path),
                output_path=None,
                success=False,
                errors=[f"Table mapping not found for {table_name}."],
            )

        try:
            df = self._read_input(path)
        except Exception as exc:
            return ValidationResult(
                table_name=table_name,
                input_path=str(path),
                output_path=None,
                success=False,
                errors=[str(exc)],
            )

        df, warnings = self._apply_column_mapping(df, self.mapping[table_name])
        df = self._coerce_types(df)

        required = list(self.mapping[table_name].get("required", []))
        missing = [col for col in required if col not in df.columns]
        if missing:
            errors.append(f"Missing required columns: {', '.join(missing)}.")

        output_path = None
        success = not errors
        if success:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(self.output_dir / f"{table_name}.parquet")
            df.write_parquet(output_path)

        result = ValidationResult(
            table_name=table_name,
            input_path=str(path),
            output_path=output_path,
            success=success,
            errors=errors,
            warnings=warnings,
            row_count=len(df),
        )
        self.results.append(result)
        return result

    def process_directory(self, input_dir: str) -> dict[str, ValidationResult]:
        """
        Process all files in a directory based on filename stem.
        """
        results: dict[str, ValidationResult] = {}
        path = Path(input_dir)
        for file_path in path.iterdir():
            if not file_path.is_file():
                continue
            table_name = file_path.stem
            if table_name not in self.mapping:
                continue
            results[table_name] = self.process_file(str(file_path), table_name)
        return results

    def generate_report(self) -> str:
        """
        Generate a text report of processing outcomes.
        """
        lines = []
        for result in self.results:
            status = "OK" if result.success else "FAILED"
            lines.append(f"{result.table_name}: {status} ({result.row_count} rows)")
            for warning in result.warnings:
                lines.append(f"  Warning: {warning}")
            for error in result.errors:
                lines.append(f"  Error: {error}")
        return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preprocess client flat files for App 2.")
    parser.add_argument("--input", required=True, help="Input file or directory path.")
    parser.add_argument("--table", help="Target table name when input is a file.")
    parser.add_argument("--mapping", default="configs/column_mapping.yml", help="Mapping YAML file.")
    parser.add_argument("--output-dir", default="data/gold", help="Output directory for parquet files.")

    args = parser.parse_args()
    preprocessor = ClientDataPreprocessor(args.mapping, args.output_dir)

    input_path = Path(args.input)
    if input_path.is_dir():
        preprocessor.process_directory(str(input_path))
    else:
        if not args.table:
            raise SystemExit("--table is required when input is a file.")
        preprocessor.process_file(str(input_path), args.table)

    report = preprocessor.generate_report()
    if report:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
