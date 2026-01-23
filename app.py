"""
BufferLab - Deployment & Kit Readiness

Flask application entry point.
"""

import os
import sys
from datetime import datetime, date
from pathlib import Path

from flask import Flask, render_template, jsonify, request, session
import polars as pl

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from bufferlab_deploy.config import load_config, get_config, set_config
from bufferlab_deploy.duckdb_loader import DuckDBLoader, get_loader, reset_loader
from bufferlab_deploy.data_contract import validate_data_contract
from bufferlab_deploy.kit_engine import KitEngine
from bufferlab_deploy.pegging_engine import PeggingEngine
from bufferlab_deploy.blocker_engine import BlockerEngine
from bufferlab_deploy.stranded_engine import StrandedEngine
from bufferlab_deploy.buffer_engine import BufferEngine
from bufferlab_deploy.scenario_engine import ScenarioEngine
from bufferlab_deploy.sql_utils import get_plan_table
from bufferlab_deploy.square_set_engine import SquareSetEngine
from bufferlab_deploy.segmentation_engine import SegmentationEngine, SegmentationThresholds


app = Flask(__name__)
app.secret_key = 'bufferlab-deploy-secret-key-2024'


# =============================================================================
# Global State
# =============================================================================

class AppState:
    """Global application state."""
    loader: DuckDBLoader | None = None
    contract_result = None
    last_analysis_run = None
    current_scenario = None
    
    @classmethod
    def initialize(cls):
        """Initialize the application state."""
        config = get_config()
        
        # Create loader and load tables
        cls.loader = DuckDBLoader(config.gold_path)
        cls.loader.load_all_tables()
        
        # Validate data contract
        cls.contract_result = validate_data_contract(cls.loader)
        
        # Set default scenario
        cls.current_scenario = config.analysis.default_scenario
    
    @classmethod
    def get_stats(cls) -> dict:
        """Get current statistics."""
        if cls.loader is None:
            return {
                "tables_loaded": 0,
                "total_rows": 0,
                "contract_passed": False,
                "contract_errors": 0,
            }
        
        total_rows = sum(
            cls.loader.table_stats.get(t, {}).get("row_count", 0)
            for t in cls.loader.loaded_tables
            if cls.loader.loaded_tables.get(t)
        )
        
        return {
            "tables_loaded": sum(1 for v in cls.loader.loaded_tables.values() if v),
            "total_rows": total_rows,
            "contract_passed": cls.contract_result.passed if cls.contract_result else False,
            "contract_errors": len(cls.contract_result.errors) if cls.contract_result else 0,
            "current_scenario": cls.current_scenario,
        }


def _format_week(value: object) -> str:
    """Format a week value as ISO date string."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_week(value: str | None) -> date | None:
    """Parse an ISO date string into a date."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _filter_df(df, site_id: str | None, week_start: date | None, week_end: date | None):
    """Filter a Polars DataFrame by site and week range."""
    if df is None or len(df) == 0:
        return df
    if site_id:
        if "site_id" in df.columns:
            df = df.filter(pl.col("site_id") == site_id)
    if "week" in df.columns:
        if week_start is not None:
            df = df.filter(pl.col("week") >= week_start)
        if week_end is not None:
            df = df.filter(pl.col("week") <= week_end)
    return df


def _get_scenarios(loader: DuckDBLoader) -> list[str]:
    if loader is None or not loader.loaded_tables.get("site_readiness"):
        return []
    try:
        result = loader.query(
            "SELECT DISTINCT scenario_id FROM site_readiness ORDER BY scenario_id"
        )
        return result["scenario_id"].to_list()
    except Exception:
        return []


def _get_sites(loader: DuckDBLoader) -> list[str]:
    if loader is None:
        return []
    plan_table = get_plan_table(loader)
    if loader.loaded_tables.get(plan_table):
        try:
            result = loader.query(f"SELECT DISTINCT site_id FROM {plan_table} ORDER BY site_id")
            return result["site_id"].to_list()
        except Exception:
            pass
    if loader.loaded_tables.get("node_master"):
        try:
            result = loader.query("SELECT DISTINCT site_id FROM node_master ORDER BY site_id")
            return result["site_id"].to_list()
        except Exception:
            return []
    return []


def _get_kits(loader: DuckDBLoader) -> list[str]:
    if loader is None:
        return []
    plan_table = get_plan_table(loader)
    if not loader.loaded_tables.get(plan_table):
        return []
    try:
        result = loader.query(f"SELECT DISTINCT kit_id FROM {plan_table} ORDER BY kit_id")
        return result["kit_id"].to_list()
    except Exception:
        return []


def _get_weeks(loader: DuckDBLoader) -> list[str]:
    if loader is None:
        return []
    plan_table = get_plan_table(loader)
    if not loader.loaded_tables.get(plan_table):
        return []
    try:
        result = loader.query(f"SELECT DISTINCT week FROM {plan_table} ORDER BY week")
        return [_format_week(v) for v in result["week"].to_list()]
    except Exception:
        return []


@app.context_processor
def inject_stats():
    """Inject stats into all templates."""
    return {
        "stats": AppState.get_stats(),
        "now": datetime.now(),
    }


# =============================================================================
# Routes
# =============================================================================

@app.route("/")
def index():
    """Home page with overview."""
    stats = AppState.get_stats()
    loader = AppState.loader
    scenarios = _get_scenarios(loader)
    sites = _get_sites(loader)
    weeks = _get_weeks(loader)

    site_id = request.args.get("site_id") or None
    if site_id == "all":
        site_id = None
    week_start = _parse_week(request.args.get("week_start"))
    week_end = _parse_week(request.args.get("week_end"))

    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario

    kpis = {
        "completion_rate": 0.0,
        "blocked_kits": 0,
        "top_blocker": "N/A",
        "stranded_value": 0.0,
    }
    completion_trend = []
    blocked_trend = []

    if loader and stats.get("contract_passed"):
        try:
            pegging_engine = PeggingEngine(loader)
            pegging = pegging_engine.run_pegging(scenario_id)
            pegging = _filter_df(pegging, site_id, week_start, week_end)

            if len(pegging) > 0:
                totals = pegging.select([
                    pl.col("deployable_kits").sum().alias("total_deployable"),
                    pl.col("buildable_kits").sum().alias("total_buildable"),
                    pl.col("blocked_kits").sum().alias("total_blocked"),
                ]).to_dicts()[0]

                if totals["total_deployable"] > 0:
                    kpis["completion_rate"] = round(
                        totals["total_buildable"] / totals["total_deployable"] * 100, 1
                    )
                kpis["blocked_kits"] = int(totals["total_blocked"])

                weekly = (
                    pegging
                    .group_by("week")
                    .agg([
                        pl.col("deployable_kits").sum().alias("total_deployable"),
                        pl.col("buildable_kits").sum().alias("total_buildable"),
                        pl.col("blocked_kits").sum().alias("total_blocked"),
                    ])
                    .sort("week")
                )
                completion_trend = [
                    {
                        "week": _format_week(row["week"]),
                        "completion_rate": round(
                            row["total_buildable"] / row["total_deployable"] * 100, 1
                        ) if row["total_deployable"] > 0 else 0.0,
                    }
                    for row in weekly.to_dicts()
                ]
                blocked_trend = [
                    {"week": _format_week(row["week"]), "blocked": int(row["total_blocked"])}
                    for row in weekly.to_dicts()
                ]

            blocker_engine = BlockerEngine(loader)
            blockers = blocker_engine.get_blocker_attribution(scenario_id)
            blockers = _filter_df(blockers, site_id, week_start, week_end)
            if len(blockers) > 0:
                blockers = blockers.sort("gap_qty", descending=True)
                kpis["top_blocker"] = blockers["item_id"][0]

            stranded_engine = StrandedEngine(loader)
            stranded = stranded_engine.get_stranded_inventory(scenario_id)
            stranded = _filter_df(stranded, site_id, week_start, week_end)
            if len(stranded) > 0:
                kpis["stranded_value"] = round(float(stranded["stranded_value"].sum()), 2)
        except Exception:
            pass

    return render_template(
        "index.html",
        stats=stats,
        scenarios=scenarios,
        contract_result=AppState.contract_result,
        sites=sites,
        weeks=weeks,
        filters={
            "site_id": site_id or "all",
            "week_start": _format_week(week_start) if week_start else "",
            "week_end": _format_week(week_end) if week_end else "",
        },
        kpis=kpis,
        completion_trend=completion_trend,
        blocked_trend=blocked_trend,
    )


@app.route("/readiness")
def readiness():
    """Kit readiness page."""
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    sites = _get_sites(loader)
    kits = _get_kits(loader)
    weeks = _get_weeks(loader)

    selected_site = request.args.get("site_id") or (sites[0] if sites else None)
    selected_kit = request.args.get("kit_id") or (kits[0] if kits else None)
    week_start = _parse_week(request.args.get("week_start"))
    week_end = _parse_week(request.args.get("week_end"))
    detail_week = _parse_week(request.args.get("detail_week"))

    summary_rows = []
    chart_data = {"labels": [], "planned": [], "deployable": [], "buildable": []}
    detail_rows = []

    if loader and AppState.get_stats().get("contract_passed"):
        try:
            kit_engine = KitEngine(loader)
            deployable = kit_engine.get_deployable_kits(scenario_id)
            pegging = PeggingEngine(loader).run_pegging(scenario_id)

            combined = deployable.join(
                pegging.select(["week", "site_id", "kit_id", "buildable_kits", "blocked_kits"]),
                on=["week", "site_id", "kit_id"],
                how="left"
            ).with_columns([
                pl.col("buildable_kits").fill_null(0),
                pl.col("blocked_kits").fill_null(0),
            ])

            if selected_site:
                combined = combined.filter(pl.col("site_id") == selected_site)
            if selected_kit:
                combined = combined.filter(pl.col("kit_id") == selected_kit)
            combined = _filter_df(combined, None, week_start, week_end)

            if len(combined) > 0:
                summary = (
                    combined
                    .group_by("week")
                    .agg([
                        pl.col("kits_planned").sum().alias("planned"),
                        pl.col("deployable_kits").sum().alias("deployable"),
                        pl.col("buildable_kits").sum().alias("buildable"),
                    ])
                    .with_columns([
                        (pl.col("deployable") - pl.col("buildable")).alias("blocked")
                    ])
                    .sort("week")
                )

                summary_rows = [
                    {
                        "week": _format_week(row["week"]),
                        "planned": int(row["planned"]),
                        "deployable": int(row["deployable"]),
                        "buildable": int(row["buildable"]),
                        "blocked": int(row["blocked"]),
                    }
                    for row in summary.to_dicts()
                ]

                chart_data = {
                    "labels": [_format_week(row["week"]) for row in summary.to_dicts()],
                    "planned": [int(row["planned"]) for row in summary.to_dicts()],
                    "deployable": [int(row["deployable"]) for row in summary.to_dicts()],
                    "buildable": [int(row["buildable"]) for row in summary.to_dicts()],
                }

            if detail_week:
                detail = pegging.filter(pl.col("week") == detail_week)
                if selected_site:
                    detail = detail.filter(pl.col("site_id") == selected_site)
                detail_rows = [
                    {**row, "week": _format_week(row["week"])}
                    for row in detail.sort(["priority", "kit_id"]).to_dicts()
                ]
        except Exception:
            pass

    return render_template(
        "readiness.html",
        scenario_id=scenario_id,
        sites=sites,
        kits=kits,
        weeks=weeks,
        filters={
            "site_id": selected_site or "",
            "kit_id": selected_kit or "",
            "week_start": _format_week(week_start) if week_start else "",
            "week_end": _format_week(week_end) if week_end else "",
            "detail_week": _format_week(detail_week) if detail_week else "",
        },
        summary_rows=summary_rows,
        chart_data=chart_data,
        detail_rows=detail_rows,
    )


@app.route("/pegging")
def pegging():
    """Priority and pegging page."""
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    sites = _get_sites(loader)
    weeks = _get_weeks(loader)

    selected_site = request.args.get("site_id") or None
    week_start = _parse_week(request.args.get("week_start"))
    week_end = _parse_week(request.args.get("week_end"))

    priority_summary = []
    pegging_rows = []

    if loader and AppState.get_stats().get("contract_passed"):
        try:
            pegging_engine = PeggingEngine(loader)
            pegging_df = pegging_engine.run_pegging(scenario_id)
            pegging_df = _filter_df(pegging_df, selected_site, week_start, week_end)

            if len(pegging_df) > 0:
                pegging_df = pegging_df.with_columns([
                    pl.when(pl.col("priority") <= 20).then(pl.lit("P1 (1-20)"))
                    .when(pl.col("priority") <= 50).then(pl.lit("P2 (21-50)"))
                    .when(pl.col("priority") <= 80).then(pl.lit("P3 (51-80)"))
                    .otherwise(pl.lit("P4 (81+)"))
                    .alias("priority_bucket")
                ])

                priority_summary_df = (
                    pegging_df
                    .group_by("priority_bucket")
                    .agg([
                        pl.col("deployable_kits").sum().alias("total_deployable"),
                        pl.col("buildable_kits").sum().alias("total_buildable"),
                        pl.col("blocked_kits").sum().alias("total_blocked"),
                    ])
                    .with_columns([
                        (pl.col("total_buildable") / pl.col("total_deployable") * 100)
                        .round(1)
                        .alias("completion_rate_pct")
                    ])
                    .sort("priority_bucket")
                )
                priority_summary = priority_summary_df.to_dicts()

                pegging_rows = pegging_df.with_columns([
                    pl.when((pl.col("blocked_kits") > 0) & (pl.col("priority") > 20))
                    .then(pl.lit(True))
                    .otherwise(pl.lit(False))
                    .alias("priority_shift")
                ]).sort(["week", "site_id", "priority", "kit_id"]).to_dicts()
                pegging_rows = [
                    {**row, "week": _format_week(row["week"])}
                    for row in pegging_rows
                ]
        except Exception:
            pass

    return render_template(
        "pegging.html",
        scenario_id=scenario_id,
        sites=sites,
        weeks=weeks,
        filters={
            "site_id": selected_site or "",
            "week_start": _format_week(week_start) if week_start else "",
            "week_end": _format_week(week_end) if week_end else "",
        },
        priority_summary=priority_summary,
        pegging_rows=pegging_rows,
    )


@app.route("/blockers")
def blockers():
    """Long pole blockers page."""
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    sites = _get_sites(loader)
    weeks = _get_weeks(loader)

    selected_site = request.args.get("site_id") or None
    week_start = _parse_week(request.args.get("week_start"))
    week_end = _parse_week(request.args.get("week_end"))

    pareto_rows = []
    blocker_rows = []
    fix_rows = []
    pareto_chart = {"labels": [], "values": [], "cumulative": []}

    if loader and AppState.get_stats().get("contract_passed"):
        try:
            blocker_engine = BlockerEngine(loader)
            blockers_df = blocker_engine.get_blocker_attribution(scenario_id)
            blockers_df = _filter_df(blockers_df, selected_site, week_start, week_end)

            if len(blockers_df) > 0:
                pareto = (
                    blockers_df
                    .filter(pl.col("root_cause") != "no_gap")
                    .group_by(["item_id", "category", "subcategory", "root_cause"])
                    .agg([
                        pl.col("gap_qty").sum().alias("total_gap_qty"),
                        pl.col("gap_value").sum().alias("total_gap_value"),
                        pl.col("kit_id").n_unique().alias("kits_affected"),
                        pl.col("site_id").n_unique().alias("sites_affected"),
                        pl.col("week").n_unique().alias("weeks_affected"),
                    ])
                    .sort("total_gap_qty", descending=True)
                    .head(15)
                    .with_row_count("rank", offset=1)
                )
                if len(pareto) > 0:
                    total_gap = pareto["total_gap_qty"].sum()
                    pareto = pareto.with_columns([
                        (pl.col("total_gap_qty").cum_sum() / total_gap * 100)
                        .round(1)
                        .alias("cumulative_pct")
                    ])
                    pareto_rows = pareto.to_dicts()
                    pareto_chart = {
                        "labels": [row["item_id"] for row in pareto_rows],
                        "values": [float(row["total_gap_qty"]) for row in pareto_rows],
                        "cumulative": [float(row.get("cumulative_pct", 0)) for row in pareto_rows],
                    }

                blocker_rows = [
                    {**row, "week": _format_week(row["week"])}
                    for row in blockers_df.head(50).to_dicts()
                ]

            fix_rows = blocker_engine.get_fix_recommendations(scenario_id, top_n=10)
        except Exception:
            pass

    return render_template(
        "blockers.html",
        scenario_id=scenario_id,
        sites=sites,
        weeks=weeks,
        filters={
            "site_id": selected_site or "",
            "week_start": _format_week(week_start) if week_start else "",
            "week_end": _format_week(week_end) if week_end else "",
        },
        pareto_rows=pareto_rows,
        pareto_chart=pareto_chart,
        blocker_rows=blocker_rows,
        fix_rows=fix_rows,
    )


@app.route("/stranded")
def stranded():
    """Stranded inventory page."""
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    sites = _get_sites(loader)
    weeks = _get_weeks(loader)

    selected_site = request.args.get("site_id") or None
    week_start = _parse_week(request.args.get("week_start"))
    week_end = _parse_week(request.args.get("week_end"))

    stranded_rows = []
    chart_data = {"labels": [], "values": []}
    kpis = {"total_units": 0, "total_value": 0.0, "top_item": "N/A"}

    if loader and AppState.get_stats().get("contract_passed"):
        try:
            stranded_engine = StrandedEngine(loader)
            stranded_df = stranded_engine.get_stranded_inventory(scenario_id)
            stranded_df = _filter_df(stranded_df, selected_site, week_start, week_end)

            if len(stranded_df) > 0:
                kpis["total_units"] = int(stranded_df["stranded_units"].sum())
                kpis["total_value"] = round(float(stranded_df["stranded_value"].sum()), 2)

                top = (
                    stranded_df
                    .group_by("item_id")
                    .agg(pl.col("stranded_value").sum().alias("value"))
                    .sort("value", descending=True)
                )
                if len(top) > 0:
                    kpis["top_item"] = top["item_id"][0]

                table = (
                    stranded_df
                    .group_by(["item_id", "category", "subcategory", "blocked_by_item", "blocked_by_cause"])
                    .agg([
                        pl.col("stranded_units").sum().alias("stranded_units"),
                        pl.col("stranded_value").sum().alias("stranded_value"),
                        pl.col("aging_days").max().alias("aging_days"),
                    ])
                    .sort("stranded_value", descending=True)
                )
                stranded_rows = table.head(50).to_dicts()

                chart_top = table.head(10).to_dicts()
                chart_data = {
                    "labels": [row["item_id"] for row in chart_top],
                    "values": [float(row["stranded_value"]) for row in chart_top],
                }
        except Exception:
            pass

    return render_template(
        "stranded.html",
        scenario_id=scenario_id,
        sites=sites,
        weeks=weeks,
        filters={
            "site_id": selected_site or "",
            "week_start": _format_week(week_start) if week_start else "",
            "week_end": _format_week(week_end) if week_end else "",
        },
        stranded_rows=stranded_rows,
        chart_data=chart_data,
        kpis=kpis,
    )


@app.route("/scenarios")
def scenarios():
    """Scenario comparison page."""
    loader = AppState.loader
    scenario_options = _get_scenarios(loader)
    sites = _get_sites(loader)
    weeks = _get_weeks(loader)

    scenario_a = request.args.get("scenario_a") or (scenario_options[0] if scenario_options else "")
    scenario_b = request.args.get("scenario_b") or (scenario_options[1] if len(scenario_options) > 1 else "")
    scenario_c = request.args.get("scenario_c") or (scenario_options[2] if len(scenario_options) > 2 else "")
    selected_site = request.args.get("site_id") or None
    week_start = _parse_week(request.args.get("week_start"))
    week_end = _parse_week(request.args.get("week_end"))

    summaries = []
    if loader and AppState.get_stats().get("contract_passed"):
        try:
            scenario_engine = ScenarioEngine(loader)
            for scenario_id in [scenario_a, scenario_b, scenario_c]:
                if scenario_id:
                    summaries.append(
                        scenario_engine.get_scenario_summary(
                            scenario_id,
                            site_id=selected_site,
                            week_start=week_start,
                            week_end=week_end,
                        )
                    )
        except Exception:
            pass

    if summaries:
        baseline = summaries[0]
        for row in summaries:
            row["delta_completion"] = round(row["completion_rate"] - baseline["completion_rate"], 1)
            row["delta_blocked"] = row["total_blocked"] - baseline["total_blocked"]
            row["delta_stranded"] = round(row["stranded_value"] - baseline["stranded_value"], 2)

    return render_template(
        "scenarios.html",
        scenario_options=scenario_options,
        sites=sites,
        weeks=weeks,
        filters={
            "scenario_a": scenario_a,
            "scenario_b": scenario_b,
            "scenario_c": scenario_c,
            "site_id": selected_site or "",
            "week_start": _format_week(week_start) if week_start else "",
            "week_end": _format_week(week_end) if week_end else "",
        },
        summaries=summaries,
    )


@app.route("/buffers")
def buffers():
    """Buffer targets page."""
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    rows = []
    policy = get_config().buffer_policy

    if loader and AppState.get_stats().get("contract_passed"):
        try:
            buffer_engine = BufferEngine(loader)
            targets = buffer_engine.get_buffer_targets()
            rows = targets.to_dicts() if len(targets) > 0 else []
        except Exception:
            pass

    return render_template(
        "buffers.html",
        scenario_id=scenario_id,
        policy=policy,
        rows=rows,
    )


@app.route("/diagnostics")
def diagnostics():
    """Data contract and diagnostics page."""
    return render_template(
        "diagnostics.html",
        contract_result=AppState.contract_result,
        loader=AppState.loader,
        config=get_config(),
    )


@app.route("/error")
def error_page():
    """Data contract error page."""
    return render_template(
        "error.html",
        contract_result=AppState.contract_result,
    )


@app.route("/settings")
def settings():
    """Settings page for configuring thresholds."""
    # Get thresholds from session or defaults
    if 'thresholds' in session:
        thresholds = SegmentationThresholds(**session['thresholds'])
    else:
        thresholds = SegmentationThresholds()
    
    return render_template("settings.html", thresholds=thresholds)


@app.route("/convergence")
def convergence():
    """Square-set convergence dashboard."""
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    weeks = _get_weeks(loader)
    
    conv_stats = {"fully_ready": 0, "blocked": 0, "convergence_rate": 0, "weeks": len(weeks)}
    convergence_data = []
    weekly_labels = []
    weekly_ready = []
    weekly_blocked = []
    exceptions = []
    
    if loader and AppState.get_stats().get("contract_passed"):
        try:
            square_set_engine = SquareSetEngine(loader)
            
            # Get convergence summary
            summary = square_set_engine.get_convergence_summary(scenario_id)
            
            if len(summary) > 0:
                conv_stats["fully_ready"] = int(summary["all_domains_ready"].sum())
                conv_stats["blocked"] = int((~summary["all_domains_ready"]).sum())
                total = len(summary)
                if total > 0:
                    conv_stats["convergence_rate"] = round(conv_stats["fully_ready"] / total * 100, 1)
                
                # Get domain readiness for detail view
                domain_readiness = square_set_engine.get_domain_readiness(scenario_id)
                
                # Build convergence data for table
                for row in summary.to_dicts():
                    # Determine domain readiness
                    ss_id = row["square_set_id"]
                    site_id = row["site_id"]
                    week = row["week"]
                    
                    dr = domain_readiness.filter(
                        (pl.col("square_set_id") == ss_id) &
                        (pl.col("site_id") == site_id) &
                        (pl.col("week") == week)
                    )
                    
                    it_rack_ready = True
                    callan_ready = True
                    mor_ready = True
                    
                    if len(dr) > 0:
                        for d in dr.to_dicts():
                            if d["domain"] == "it_rack":
                                it_rack_ready = d["is_ready"]
                            elif d["domain"] == "callan":
                                callan_ready = d["is_ready"]
                            elif d["domain"] == "mor":
                                mor_ready = d["is_ready"]
                    
                    convergence_data.append({
                        "week": _format_week(row["week"]),
                        "site_id": row["site_id"],
                        "square_set_id": row["square_set_id"],
                        "all_domains_ready": row["all_domains_ready"],
                        "it_rack_ready": it_rack_ready,
                        "callan_ready": callan_ready,
                        "mor_ready": mor_ready,
                        "missing_domains": row.get("missing_domains", []),
                    })
                
                # Weekly chart data
                weekly_stats = square_set_engine.get_weekly_convergence_stats(scenario_id)
                if len(weekly_stats) > 0:
                    weekly_labels = [_format_week(r["week"]) for r in weekly_stats.to_dicts()]
                    weekly_ready = [int(r["fully_ready_sets"]) for r in weekly_stats.to_dicts()]
                    weekly_blocked = [int(r["blocked_sets"]) for r in weekly_stats.to_dicts()]
        except Exception as e:
            print(f"Convergence error: {e}")
    
    return render_template(
        "convergence.html",
        conv_stats=conv_stats,
        convergence_data=convergence_data,
        weeks=weeks,
        weekly_labels=weekly_labels,
        weekly_ready=weekly_ready,
        weekly_blocked=weekly_blocked,
        exceptions=exceptions,
    )


@app.route("/segmentation")
def segmentation():
    """Segmentation page showing B1-B4, N1-N4 classification."""
    loader = AppState.loader
    
    segment_counts = {s: 0 for s in ["B1", "B2", "B3", "B4", "N1", "N2", "N3", "N4"]}
    tag_counts = {
        "transition_active": 0,
        "shared_component": 0,
        "long_lead_foundation": 0,
        "build_ahead_sensitivity": 0,
        "break_glass_exception": 0,
    }
    items = []
    
    if loader and AppState.get_stats().get("contract_passed"):
        try:
            # Get thresholds from session
            if 'thresholds' in session:
                thresholds = SegmentationThresholds(**session['thresholds'])
            else:
                thresholds = SegmentationThresholds()
            
            seg_engine = SegmentationEngine(loader, thresholds)
            segmentation_df = seg_engine.get_full_segmentation()
            
            if len(segmentation_df) > 0:
                # Get segment counts
                seg_summary = seg_engine.get_segment_summary()
                for row in seg_summary.to_dicts():
                    segment_counts[row["segment"]] = int(row["item_count"])
                
                # Get tag counts
                tag_summary = seg_engine.get_overlay_tag_summary()
                for row in tag_summary.to_dicts():
                    tag_counts[row["tag"]] = int(row["count"])
                
                # Get items for table
                items = segmentation_df.head(100).to_dicts()
        except Exception as e:
            print(f"Segmentation error: {e}")
    
    return render_template(
        "segmentation.html",
        segment_counts=segment_counts,
        tag_counts=tag_counts,
        items=items,
    )


@app.route("/engineering")
def engineering():
    """Engineering insights - GPU generations and substitution paths."""
    loader = AppState.loader
    
    generations = []
    transitions = []
    substitutions = []
    
    if loader and AppState.get_stats().get("contract_passed"):
        try:
            # Get lifecycle data for transitions
            if loader.loaded_tables.get("lifecycle"):
                lifecycle = loader.query("""
                    SELECT 
                        item_id,
                        generation,
                        status,
                        ltb_date,
                        eol_date,
                        transition_start_date,
                        transition_end_date
                    FROM lifecycle
                    ORDER BY generation, item_id
                """)
                transitions = lifecycle.to_dicts() if len(lifecycle) > 0 else []
            
            # Get substitution paths if available
            if loader.loaded_tables.get("substitution_map"):
                subs = loader.query("""
                    SELECT * FROM substitution_map
                    ORDER BY from_item_id
                """)
                substitutions = subs.to_dicts() if len(subs) > 0 else []
        except Exception:
            pass
    
    return render_template(
        "engineering.html",
        generations=generations,
        transitions=transitions,
        substitutions=substitutions,
    )


# =============================================================================
# API Endpoints
# =============================================================================

@app.route("/api/reload-data", methods=["POST"])
def api_reload_data():
    """Reload data from gold tables."""
    try:
        reset_loader()
        AppState.initialize()
        
        return jsonify({
            "success": True,
            "stats": AppState.get_stats(),
            "message": "Data reloaded successfully",
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500


@app.route("/api/run-analysis", methods=["POST"])
def api_run_analysis():
    """Run analytics computations."""
    if AppState.loader is None:
        return jsonify({"success": False, "error": "No data loaded"}), 400

    try:
        scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
        pegging = PeggingEngine(AppState.loader).run_pegging(scenario_id)
        blocked = int(pegging["blocked_kits"].sum()) if len(pegging) > 0 else 0
        deployable = int(pegging["deployable_kits"].sum()) if len(pegging) > 0 else 0
        buildable = int(pegging["buildable_kits"].sum()) if len(pegging) > 0 else 0

        AppState.last_analysis_run = datetime.now()
        return jsonify({
            "success": True,
            "scenario_id": scenario_id,
            "blocked_kits": blocked,
            "deployable_kits": deployable,
            "buildable_kits": buildable,
            "run_time": AppState.last_analysis_run.isoformat(),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/set-scenario", methods=["POST"])
def api_set_scenario():
    """Set the current scenario."""
    data = request.json or {}
    scenario_id = data.get("scenario_id")
    
    if scenario_id:
        AppState.current_scenario = scenario_id
        return jsonify({
            "success": True,
            "current_scenario": scenario_id,
        })
    
    return jsonify({
        "success": False,
        "error": "No scenario_id provided",
    }), 400


@app.route("/api/contract-status")
def api_contract_status():
    """Get data contract validation status."""
    if AppState.contract_result is None:
        return jsonify({
            "passed": False,
            "checks": [],
            "errors": [],
        })
    
    return jsonify({
        "passed": AppState.contract_result.passed,
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "message": c.message,
                "severity": c.severity,
                "fix": c.fix_recommendation,
            }
            for c in AppState.contract_result.checks
        ],
        "errors": [
            {
                "name": c.name,
                "message": c.message,
                "fix": c.fix_recommendation,
            }
            for c in AppState.contract_result.errors
        ],
    })


@app.route("/api/table-info/<table_name>")
def api_table_info(table_name: str):
    """Get info about a specific table."""
    if AppState.loader is None:
        return jsonify({"error": "No data loaded"}), 404
    
    if not AppState.loader.loaded_tables.get(table_name):
        return jsonify({"error": f"Table '{table_name}' not loaded"}), 404
    
    stats = AppState.loader.table_stats.get(table_name, {})
    
    # Get sample data
    try:
        sample = AppState.loader.query(f"SELECT * FROM {table_name} LIMIT 5")
        sample_data = sample.to_dicts()
    except:
        sample_data = []
    
    return jsonify({
        "name": table_name,
        "row_count": stats.get("row_count", 0),
        "columns": stats.get("columns", []),
        "sample": sample_data,
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    """Save segmentation settings."""
    try:
        data = request.json or request.form.to_dict()
        
        thresholds_dict = {
            'high_eo_unit_cost': float(data.get('high_eo_unit_cost', 5000)),
            'high_eo_days_to_risk': int(data.get('high_eo_days_to_risk', 90)),
            'constrained_lead_time': int(data.get('constrained_lead_time', 45)),
            'constrained_confidence': float(data.get('constrained_confidence', 0.70)),
            'long_lead_threshold': int(data.get('long_lead_threshold', 60)),
            'long_lead_days_to_risk_min': int(data.get('long_lead_days_to_risk_min', 180)),
            'build_ahead_stranding_pct': float(data.get('build_ahead_stranding_pct', 0.30)),
            'shared_usage_threshold': int(data.get('shared_usage_threshold', 1)),
            'committed_max_coverage_weeks': int(data.get('committed_max_coverage_weeks', 6)),
            'likely_max_coverage_weeks': int(data.get('likely_max_coverage_weeks', 2)),
            'exploratory_coverage_weeks': int(data.get('exploratory_coverage_weeks', 0)),
            'transition_buffer_reduction_pct': float(data.get('transition_buffer_reduction_pct', 0.33)),
        }
        
        session['thresholds'] = thresholds_dict
        session.modified = True
        
        return jsonify({'success': True, 'message': 'Settings saved successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route("/api/settings/reset", methods=["POST"])
def api_reset_settings():
    """Reset settings to defaults."""
    session.pop('thresholds', None)
    session.modified = True
    return jsonify({'success': True, 'message': 'Settings reset to defaults'})


@app.route("/api/settings/export", methods=["GET"])
def api_export_settings():
    """Export current settings as YAML."""
    import yaml
    
    if 'thresholds' in session:
        thresholds = session['thresholds']
    else:
        thresholds = SegmentationThresholds().__dict__
    
    settings_dict = {
        'segmentation_thresholds': {
            'high_eo_unit_cost': thresholds.get('high_eo_unit_cost', 5000),
            'high_eo_days_to_risk': thresholds.get('high_eo_days_to_risk', 90),
            'constrained_lead_time': thresholds.get('constrained_lead_time', 45),
            'constrained_confidence': thresholds.get('constrained_confidence', 0.70),
            'long_lead_threshold': thresholds.get('long_lead_threshold', 60),
            'long_lead_days_to_risk_min': thresholds.get('long_lead_days_to_risk_min', 180),
            'build_ahead_stranding_pct': thresholds.get('build_ahead_stranding_pct', 0.30),
            'shared_usage_threshold': thresholds.get('shared_usage_threshold', 1),
        },
        'buffer_policy': {
            'committed_max_coverage_weeks': thresholds.get('committed_max_coverage_weeks', 6),
            'likely_max_coverage_weeks': thresholds.get('likely_max_coverage_weeks', 2),
            'exploratory_coverage_weeks': thresholds.get('exploratory_coverage_weeks', 0),
            'transition_buffer_reduction_pct': thresholds.get('transition_buffer_reduction_pct', 0.33),
        }
    }
    
    yaml_content = yaml.dump(settings_dict, default_flow_style=False)
    
    from flask import Response
    return Response(
        yaml_content,
        mimetype='application/x-yaml',
        headers={'Content-Disposition': 'attachment; filename=settings.yml'}
    )


# =============================================================================
# Governance & Reporting Export Routes
# =============================================================================

@app.route("/export/weekly-status")
def export_weekly_status():
    """Export weekly status report as JSON."""
    from flask import Response
    import json
    
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    
    report = {
        "report_type": "weekly_status",
        "generated_at": datetime.now().isoformat(),
        "scenario_id": scenario_id,
        "summary": {},
        "convergence": {},
        "blockers": [],
        "buffer_status": {},
    }
    
    if loader and AppState.get_stats().get("contract_passed"):
        try:
            # Get scenario summary
            scenario_engine = ScenarioEngine(loader)
            report["summary"] = scenario_engine.get_scenario_summary(scenario_id)
            
            # Get convergence data
            square_set_engine = SquareSetEngine(loader)
            summary = square_set_engine.get_convergence_summary(scenario_id)
            if len(summary) > 0:
                report["convergence"] = {
                    "fully_ready": int(summary["all_domains_ready"].sum()),
                    "blocked": int((~summary["all_domains_ready"]).sum()),
                    "total_sets": len(summary),
                }
            
            # Get top blockers
            blocker_engine = BlockerEngine(loader)
            blockers = blocker_engine.get_blocker_attribution(scenario_id)
            if len(blockers) > 0:
                top_blockers = blockers.sort("gap_qty", descending=True).head(10)
                report["blockers"] = top_blockers.to_dicts()
            
            # Get buffer status from segmentation
            seg_engine = SegmentationEngine(loader)
            seg_summary = seg_engine.get_segment_summary()
            if len(seg_summary) > 0:
                report["buffer_status"] = {
                    "segment_counts": seg_summary.to_dicts(),
                }
        except Exception as e:
            report["error"] = str(e)
    
    return Response(
        json.dumps(report, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=weekly_status_{datetime.now().strftime("%Y%m%d")}.json'}
    )


@app.route("/export/leadership-update")
def export_leadership_update():
    """Export 4-week leadership update report as JSON."""
    from flask import Response
    import json
    from datetime import timedelta
    
    loader = AppState.loader
    scenario_id = AppState.current_scenario or get_config().analysis.default_scenario
    
    today = date.today()
    four_weeks_ago = today - timedelta(weeks=4)
    
    report = {
        "report_type": "leadership_update",
        "generated_at": datetime.now().isoformat(),
        "period": {
            "start": four_weeks_ago.isoformat(),
            "end": today.isoformat(),
        },
        "scenario_id": scenario_id,
        "executive_summary": {},
        "key_metrics": {},
        "risk_items": [],
        "recommendations": [],
    }
    
    if loader and AppState.get_stats().get("contract_passed"):
        try:
            scenario_engine = ScenarioEngine(loader)
            
            # Get tiered summary for executive view
            tiered = scenario_engine.get_tiered_summary(scenario_id)
            report["executive_summary"] = {
                "committed": tiered["committed"],
                "likely": tiered["likely"],
                "total": tiered["total"],
            }
            
            # Key metrics
            total_summary = tiered["total"]
            report["key_metrics"] = {
                "completion_rate": total_summary.get("completion_rate", 0),
                "total_deployable": total_summary.get("total_deployable", 0),
                "total_blocked": total_summary.get("total_blocked", 0),
                "stranded_value": total_summary.get("stranded_value", 0),
            }
            
            # Get high-risk items (blockers with high gap)
            blocker_engine = BlockerEngine(loader)
            blockers = blocker_engine.get_blocker_attribution(scenario_id)
            if len(blockers) > 0:
                high_risk = blockers.filter(pl.col("gap_qty") > 10).sort("gap_qty", descending=True).head(5)
                report["risk_items"] = high_risk.to_dicts()
            
            # Auto-generate recommendations based on data
            recommendations = []
            if total_summary.get("completion_rate", 0) < 80:
                recommendations.append({
                    "priority": "high",
                    "area": "completion_rate",
                    "recommendation": "Focus on resolving top blockers to improve completion rate.",
                })
            if total_summary.get("stranded_value", 0) > 100000:
                recommendations.append({
                    "priority": "medium",
                    "area": "stranded_inventory",
                    "recommendation": "Review stranded inventory for potential reallocation or disposal.",
                })
            report["recommendations"] = recommendations
            
        except Exception as e:
            report["error"] = str(e)
    
    return Response(
        json.dumps(report, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=leadership_update_{datetime.now().strftime("%Y%m%d")}.json'}
    )


@app.route("/export/buffer-analysis")
def export_buffer_analysis():
    """Export buffer analysis with E&O impact as JSON."""
    from flask import Response
    import json
    
    loader = AppState.loader
    
    report = {
        "report_type": "buffer_analysis",
        "generated_at": datetime.now().isoformat(),
        "by_segment": [],
        "by_location": [],
        "eo_impact": {},
        "items": [],
    }
    
    if loader and AppState.get_stats().get("contract_passed"):
        try:
            from bufferlab_deploy.buffer_engine_v2 import BufferEngineV2
            
            buffer_engine = BufferEngineV2(loader)
            
            # Get buffer summary by segment
            seg_summary = buffer_engine.get_buffer_summary_by_segment("committed")
            if len(seg_summary) > 0:
                report["by_segment"] = seg_summary.to_dicts()
            
            # Get buffer summary by location
            loc_summary = buffer_engine.get_buffer_summary_by_location("committed")
            if len(loc_summary) > 0:
                report["by_location"] = loc_summary.to_dicts()
            
            # Get E&O impact
            report["eo_impact"] = buffer_engine.calculate_eo_penalty_impact("committed")
            
            # Get item-level buffers (limited to top 100)
            item_buffers = buffer_engine.calculate_item_buffers("committed")
            if len(item_buffers) > 0:
                report["items"] = item_buffers.head(100).to_dicts()
                
        except Exception as e:
            report["error"] = str(e)
    
    return Response(
        json.dumps(report, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=buffer_analysis_{datetime.now().strftime("%Y%m%d")}.json'}
    )


# =============================================================================
# Startup
# =============================================================================

def initialize_app():
    """Initialize the application."""
    # Load config
    config_path = Path(__file__).parent / "configs" / "default_config.yml"
    config = load_config(config_path)
    set_config(config)
    
    # Ensure data directories exist
    config.gold_path.mkdir(parents=True, exist_ok=True)
    config.runs_path.mkdir(parents=True, exist_ok=True)
    
    # Initialize state
    AppState.initialize()


# Initialize on import
initialize_app()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  BufferLab - Deployment & Kit Readiness")
    print("  Open in browser: http://127.0.0.1:5001")
    print("=" * 60 + "\n")
    
    app.run(debug=True, port=5001)
