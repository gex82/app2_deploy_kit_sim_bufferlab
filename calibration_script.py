"""
Calibration Script for BufferLab threshold tuning.

Analyzes data distributions to recommend optimal segmentation thresholds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl


class ThresholdCalibrator:
    """
    Calibrate segmentation thresholds based on data distributions.
    
    Uses percentile-based analysis to recommend:
    - High E&O unit cost threshold
    - Lead time threshold bands
    - Confidence thresholds
    """
    
    def __init__(self, gold_path: str | Path):
        self.gold_path = Path(gold_path)
        self.recommendations: dict[str, Any] = {}
    
    def load_data(self) -> bool:
        """Load required tables from gold path."""
        self.item_master = self._load_table("item_master")
        self.supply = self._load_table("supply")
        self.lead_time_history = self._load_table("lead_time_history")
        self.lifecycle = self._load_table("lifecycle")
        
        return self.item_master is not None and len(self.item_master) > 0
    
    def _load_table(self, name: str) -> pl.DataFrame | None:
        """Load a single parquet table."""
        path = self.gold_path / f"{name}.parquet"
        if not path.exists():
            print(f"  Warning: {name}.parquet not found")
            return None
        try:
            return pl.read_parquet(path)
        except Exception as e:
            print(f"  Error loading {name}: {e}")
            return None
    
    def calibrate_cost_threshold(
        self,
        target_percentile: float = 0.75,
    ) -> float:
        """
        Recommend high E&O unit cost threshold based on percentile.
        
        Args:
            target_percentile: Percentile to use (0.75 = top 25% are high cost)
        
        Returns:
            Recommended threshold value
        """
        if self.item_master is None or "unit_cost" not in self.item_master.columns:
            return 5000.0  # Default fallback
        
        costs = self.item_master["unit_cost"].drop_nulls()
        if len(costs) == 0:
            return 5000.0
        
        threshold = float(costs.quantile(target_percentile))
        
        self.recommendations["high_eo_unit_cost"] = {
            "recommended": round(threshold, 2),
            "percentile": target_percentile,
            "median": round(float(costs.median()), 2),
            "max": round(float(costs.max()), 2),
            "item_count_above": int((costs > threshold).sum()),
        }
        
        return threshold
    
    def calibrate_lead_time_thresholds(
        self,
        constrained_percentile: float = 0.70,
        long_lead_percentile: float = 0.85,
    ) -> tuple[int, int]:
        """
        Recommend two-band lead time thresholds.
        
        Args:
            constrained_percentile: Percentile for constrained threshold
            long_lead_percentile: Percentile for long-lead threshold
        
        Returns:
            Tuple of (constrained_threshold, long_lead_threshold)
        """
        if self.lead_time_history is None or "lead_time_p95" not in self.lead_time_history.columns:
            return (45, 60)  # Defaults
        
        lead_times = self.lead_time_history["lead_time_p95"].drop_nulls()
        if len(lead_times) == 0:
            return (45, 60)
        
        constrained_threshold = int(lead_times.quantile(constrained_percentile))
        long_lead_threshold = int(lead_times.quantile(long_lead_percentile))
        
        self.recommendations["lead_time_thresholds"] = {
            "constrained_recommended": constrained_threshold,
            "constrained_percentile": constrained_percentile,
            "long_lead_recommended": long_lead_threshold,
            "long_lead_percentile": long_lead_percentile,
            "median_lead_time": int(lead_times.median()),
            "max_lead_time": int(lead_times.max()),
        }
        
        return (constrained_threshold, long_lead_threshold)
    
    def calibrate_confidence_threshold(
        self,
        target_percentile: float = 0.30,
    ) -> float:
        """
        Recommend constrained confidence threshold.
        
        Args:
            target_percentile: Percentile for low confidence (0.30 = bottom 30%)
        
        Returns:
            Recommended threshold value
        """
        if self.supply is None or "confidence_weight" not in self.supply.columns:
            return 0.70  # Default
        
        confidence = self.supply["confidence_weight"].drop_nulls()
        if len(confidence) == 0:
            return 0.70
        
        threshold = float(confidence.quantile(target_percentile))
        
        self.recommendations["confidence_threshold"] = {
            "recommended": round(threshold, 2),
            "percentile": target_percentile,
            "median": round(float(confidence.median()), 2),
            "min": round(float(confidence.min()), 2),
        }
        
        return threshold
    
    def calibrate_days_to_risk(
        self,
        target_percentile: float = 0.25,
    ) -> int:
        """
        Recommend high E&O days-to-risk threshold.
        
        Args:
            target_percentile: Percentile for short timeline (0.25 = bottom 25%)
        
        Returns:
            Recommended threshold in days
        """
        if self.lifecycle is None:
            return 90  # Default
        
        # Try to compute days to risk from EOL dates
        from datetime import date
        today = date.today()
        
        if "eol_date" in self.lifecycle.columns:
            try:
                dates = self.lifecycle["eol_date"].drop_nulls()
                days = [(d - today).days for d in dates.to_list() if d is not None]
                if days:
                    threshold = int(sorted(days)[int(len(days) * target_percentile)])
                    
                    self.recommendations["days_to_risk"] = {
                        "recommended": max(30, threshold),
                        "percentile": target_percentile,
                        "median_days": int(sorted(days)[len(days) // 2]),
                        "min_days": min(days),
                    }
                    
                    return max(30, threshold)
            except Exception:
                pass
        
        return 90  # Default
    
    def run_full_calibration(self) -> dict[str, Any]:
        """
        Run all calibration analyses.
        
        Returns:
            Dict with all recommendations
        """
        if not self.load_data():
            return {"error": "Could not load required data"}
        
        print("\n🔧 Running threshold calibration...\n")
        
        cost_threshold = self.calibrate_cost_threshold()
        print(f"  High E&O Unit Cost: ${cost_threshold:,.2f}")
        
        lt_constrained, lt_long = self.calibrate_lead_time_thresholds()
        print(f"  Constrained Lead Time: {lt_constrained} days")
        print(f"  Long-Lead Threshold: {lt_long} days")
        
        conf_threshold = self.calibrate_confidence_threshold()
        print(f"  Constrained Confidence: {conf_threshold:.2f}")
        
        days_threshold = self.calibrate_days_to_risk()
        print(f"  High E&O Days-to-Risk: {days_threshold} days")
        
        # Build summary
        self.recommendations["summary"] = {
            "high_eo_unit_cost": round(cost_threshold, 2),
            "high_eo_days_to_risk": days_threshold,
            "constrained_lead_time": lt_constrained,
            "constrained_confidence": round(conf_threshold, 2),
            "long_lead_threshold": lt_long,
        }
        
        return self.recommendations
    
    def export_yaml_config(self, output_path: str | Path) -> None:
        """Export calibrated thresholds to YAML config."""
        import yaml
        
        if "summary" not in self.recommendations:
            self.run_full_calibration()
        
        config = {
            "segmentation_thresholds": self.recommendations["summary"],
            "calibration_metadata": {
                "source": str(self.gold_path),
                "analysis_details": {
                    k: v for k, v in self.recommendations.items() 
                    if k != "summary"
                },
            },
        }
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        print(f"\n✓ Exported config to: {output_path}")
    
    def print_report(self) -> None:
        """Print a formatted calibration report."""
        if "summary" not in self.recommendations:
            self.run_full_calibration()
        
        print("\n" + "=" * 60)
        print("THRESHOLD CALIBRATION REPORT")
        print("=" * 60)
        
        summary = self.recommendations["summary"]
        
        print("\nRecommended Settings:")
        print("-" * 40)
        print(f"  high_eo_unit_cost_threshold: ${summary['high_eo_unit_cost']:,.2f}")
        print(f"  high_eo_days_to_risk_threshold: {summary['high_eo_days_to_risk']} days")
        print(f"  constrained_lead_time_threshold: {summary['constrained_lead_time']} days")
        print(f"  constrained_confidence_threshold: {summary['constrained_confidence']:.2f}")
        print(f"  long_lead_foundation_threshold: {summary['long_lead_threshold']} days")
        
        if "high_eo_unit_cost" in self.recommendations:
            r = self.recommendations["high_eo_unit_cost"]
            print(f"\nCost Analysis:")
            print(f"  - Using {int(r['percentile']*100)}th percentile")
            print(f"  - {r['item_count_above']} items above threshold")
            print(f"  - Median: ${r['median']:,.2f}, Max: ${r['max']:,.2f}")
        
        if "lead_time_thresholds" in self.recommendations:
            r = self.recommendations["lead_time_thresholds"]
            print(f"\nLead Time Analysis:")
            print(f"  - Two-band approach: {r['constrained_recommended']} / {r['long_lead_recommended']} days")
            print(f"  - Median: {r['median_lead_time']} days, Max: {r['max_lead_time']} days")
        
        print("\n" + "=" * 60)


def calibrate_thresholds(
    gold_path: str = "./data/gold",
    export_config: bool = True,
    config_output: str = "./configs/calibrated_thresholds.yml",
) -> dict[str, Any]:
    """
    Convenience function to run calibration.
    
    Args:
        gold_path: Path to gold data directory
        export_config: Whether to export YAML config
        config_output: Path for exported config
    
    Returns:
        Dict with calibration recommendations
    """
    calibrator = ThresholdCalibrator(gold_path)
    results = calibrator.run_full_calibration()
    calibrator.print_report()
    
    if export_config:
        calibrator.export_yaml_config(config_output)
    
    return results


if __name__ == "__main__":
    import sys
    
    gold_path = sys.argv[1] if len(sys.argv) > 1 else "./data/gold"
    
    print(f"Calibrating thresholds from: {gold_path}")
    calibrate_thresholds(gold_path)
