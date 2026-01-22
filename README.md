# BufferLab - Deployment & Kit Readiness (App 2)

Local-first analytics app for GPU buffer strategy: kit readiness, priority pegging, netting ledger, long-pole blockers, stranded inventory, and buffer targets. Built on Flask + DuckDB with an Accenture-inspired dark UI.

## Requirements
- Python 3.11+
- Windows/macOS/Linux

## Setup
```bash
python -m pip install -r requirements.txt
```

## Run
```bash
python app.py
```

Open `http://127.0.0.1:5001`.

## Data Inputs (App 1 Contract)
App 2 reads App 1 gold outputs from `data/gold/*.parquet`. App 2 does no cleaning beyond filtering and joins.

Required tables:
- `deployment_plan` (preferred) or `demand_plan` (fallback)
- `bom_kit`
- `inventory_position`
- `supply`
- `site_readiness`
- `item_master`
- `node_master`
- `lane_master`

Optional tables (used if present):
- `lead_time_distribution` or `lead_time_history`
- `lifecycle`
- `substitution_map`

If any contract check fails, App 2 shows a Data Contract Error page with remediation guidance pointing back to App 1.

## Configuration
Config file: `configs/default_config.yml`

Key settings:
- `data.gold_path`: location of App 1 gold tables.
- `analysis.default_scenario`: scenario to load by default.
- `analysis.horizon_weeks`: planning horizon.
- `analysis.transfers`: transfer model toggles.
- `buffer_policy`: heuristic buffer targets and risk thresholds.

## UI Pages
- Overview: KPIs + completion and blocked trends
- Kit Readiness: planned vs deployable vs buildable + week drilldown
- Priority & Pegging: allocation by priority buckets and kit outcomes
- Long Poles: blocker Pareto, root causes, fix recommendations
- Stranded Inventory: units and $ at risk, blocker context
- Scenario Compare: side-by-side scenario deltas
- Buffer Targets: v1 policy-based recommendations
- Diagnostics: data contract checks and assumptions

## Notes on Logic
- Netting ledger prevents double counting across weeks.
- Pegging allocates scarce components by priority (1 = highest).
- Transfer modeling shifts upstream inventory/supply by lead time.
- Buffer targets are policy-based heuristic (v1), not an optimizer.

## Tests
```bash
pytest -q
```

## Troubleshooting
- `date_trunc` errors: ensure supply date columns are valid and parseable; App 2 uses `promised_date`, `promise_date`, or `promise_week`.
- Join type mismatch on `week`: confirm `week` values are DATE in App 1 outputs.
- Missing tables/columns: fix in App 1 export and reload data.
