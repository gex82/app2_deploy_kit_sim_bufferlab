## BufferLab Deploy Agent Notes

Purpose: local-first analytics app for square-set readiness, pegging, blockers, stranded inventory, and buffer targets.

Quick start:
- Install deps: `python -m pip install -r requirements.txt`
- Run app: `python app.py`
- Open: `http://127.0.0.1:5001`
- Preprocess client files: `python preprocess_client_data.py --input <path> --table <table_name>`
- Upload via web: `/upload` supports CSV, Excel, Parquet with automatic column mapping
- Regenerate synthetic data: `python -m src.bufferlab_deploy.synthetic_data_generator`

Data expectations:
- App reads App 1 gold parquet tables from `data/gold`.
- Required tables: `deployment_plan` (or `demand_plan`), `bom_kit`, `inventory_position`, `supply`, `site_readiness`, `item_master`, `node_master`, `lane_master`.
- Optional: `square_set_master`, `lifecycle`, `substitution_map`, `lead_time_history`
- If data contract fails, open Diagnostics page for remediation guidance.

Core workflows:
- Square-set readiness: uses `SquareSetEngine` for requirements and convergence.
- Pegging: greedy allocation by priority with convergence gating in `PeggingEngine`.
- Ledger: `NettingLedger` enforces no double counting by week with square-set requirements.
- Blockers: `BlockerEngine` for root cause attribution with domain context.
- Stranded: `StrandedEngine` flags blocked and partial-domain readiness inventory.
- Segmentation: `SegmentationEngine` classifies items into B1-B4/N1-N4 segments with overlay tags.
- Buffers: `BufferEngineV2` computes tier-aware policy targets with E&O penalty.
- Scenarios: `ScenarioEngine` compares scenario summaries; templates for baseline/favorable/stressed.
- Engineering: Lifecycle transitions, GPU generations (H100/H200/B100), substitution paths.

Key routes:
- `/upload` - Web file upload with validation
- `/export/csv/<type>` - CSV export (buffers, blockers, stranded, convergence, segments, pegging)
- `/export/weekly-status` - JSON weekly status report
- `/export/buffer-analysis` - JSON buffer analysis with E&O impact

Key files:
- `app.py`: Flask routes + API endpoints.
- `src/bufferlab_deploy/synthetic_data_generator.py`: generates sample data for testing.
- `src/bufferlab_deploy/segmentation_engine.py`: B1-B4/N1-N4 segmentation with overlays.
- `src/bufferlab_deploy/buffer_engine_v2.py`: tier-aware buffer targets with E&O.
- `src/bufferlab_deploy/sql_utils.py`: schema helpers (week, supply date, readiness expr).
- `src/bufferlab_deploy/netting_ledger.py`: time-phased ledger.
- `templates/upload.html`: drag-and-drop file upload UI.
- `templates/`: Jinja pages for all views.

Common pitfalls:
- Supply date columns can be `promised_date`, `promise_date`, or `promise_week`.
- `week` joins require consistent DATETIME types; synthetic data uses datetime, not date.
- Data contract errors block analysis; fix in App 1 export first.
- Persisted settings live in `configs/user_settings.yml` (gitignored).
- Date vs Datetime mismatch: ensure all date columns are Datetime type for Polars compatibility.

Testing:
- `pytest -q` for contract/ledger/pegging regressions.

