## BufferLab Deploy Agent Notes

Purpose: local-first analytics app for kit readiness, pegging, blockers, stranded inventory, and buffer targets.

Quick start:
- Install deps: `python -m pip install -r requirements.txt`
- Run app: `python app.py`
- Open: `http://127.0.0.1:5001`

Data expectations:
- App reads App 1 gold parquet tables from `data/gold`.
- Required tables: `deployment_plan` (or `demand_plan`), `bom_kit`, `inventory_position`, `supply`, `site_readiness`, `item_master`, `node_master`, `lane_master`.
- If data contract fails, open Diagnostics page for remediation guidance.

Core workflows:
- Kit readiness: uses `KitEngine` and pegging outputs.
- Pegging: greedy allocation by priority in `PeggingEngine`.
- Ledger: `NettingLedger` enforces no double counting by week.
- Blockers: `BlockerEngine` for root cause attribution.
- Stranded: `StrandedEngine` for blocked-but-available inventory.
- Buffers: `BufferEngine` generates v1 policy-based targets.
- Scenarios: `ScenarioEngine` compares scenario summaries.

Key files:
- `app.py`: Flask routes + API endpoints.
- `src/bufferlab_deploy/sql_utils.py`: schema helpers (week, supply date, readiness expr).
- `src/bufferlab_deploy/netting_ledger.py`: time-phased ledger.
- `templates/`: Jinja pages for all views.

Common pitfalls:
- Supply date columns can be `promised_date`, `promise_date`, or `promise_week`.
- `week` joins require consistent DATE types; use `sql_utils.get_supply_week_expr`.
- Data contract errors block analysis; fix in App 1 export first.

Testing:
- `pytest -q` for contract/ledger/pegging regressions.
