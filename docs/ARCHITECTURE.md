# BufferLab Architecture & Developer Reference

> Technical documentation for developers working with or extending BufferLab.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BufferLab Architecture                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                          PRESENTATION LAYER                         │   │
│  │                                                                     │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │   │
│  │  │   Flask     │  │  Jinja2     │  │    CSS      │  │    JS     │  │   │
│  │  │   Routes    │  │  Templates  │  │ (Dark Theme)│  │ (Charts)  │  │   │
│  │  │  (app.py)   │  │ (15 pages)  │  │             │  │           │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                          ANALYTICS LAYER                            │   │
│  │                    (src/bufferlab_deploy/*.py)                      │   │
│  │                                                                     │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │   │
│  │  │  SquareSetEngine │  │ SegmentationEngine│  │  BufferEngineV2  │  │   │
│  │  │                  │  │                  │  │                  │  │   │
│  │  │ • Domain mapping │  │ • MECE B1-N4     │  │ • Tiered buffers │  │   │
│  │  │ • Convergence    │  │ • Overlay tags   │  │ • E&O penalties  │  │   │
│  │  │ • Explosion      │  │ • Thresholds     │  │ • Value at Risk  │  │   │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘  │   │
│  │                                                                     │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │   │
│  │  │  PeggingEngine   │  │  NettingLedger   │  │  ScenarioEngine  │  │   │
│  │  │                  │  │                  │  │                  │  │   │
│  │  │ • Tier allocation│  │ • Time-phased    │  │ • What-if        │  │   │
│  │  │ • Priority rules │  │ • No double count│  │ • Templates      │  │   │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘  │   │
│  │                                                                     │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │   │
│  │  │  BlockerEngine   │  │  StrandedEngine  │  │  TransferModel   │  │   │
│  │  │                  │  │                  │  │                  │  │   │
│  │  │ • Root cause     │  │ • Capital at risk│  │ • Lead time      │  │   │
│  │  │ • Pareto         │  │ • Context        │  │ • Site shifts    │  │   │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                           DATA LAYER                                │   │
│  │                                                                     │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │   │
│  │  │   DuckDBLoader   │  │  DataContract    │  │  Parquet Files   │  │   │
│  │  │                  │  │                  │  │                  │  │   │
│  │  │ • Query engine   │  │ • Schema checks  │  │ • data/gold/     │  │   │
│  │  │ • Table registry │  │ • Validation     │  │ • Immutable      │  │   │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
app2_deployment_kit_sim/
├── app.py                          # Flask application entry point
├── requirements.txt                # Python dependencies
├── pyproject.toml                  # Project metadata
├── preprocess_client_data.py       # Client data import utility
├── calibration_script.py           # Threshold calibration tool
│
├── src/bufferlab_deploy/           # Core analytics modules
│   ├── __init__.py
│   ├── duckdb_loader.py            # Data access layer
│   ├── data_contract.py            # Schema validation
│   ├── config.py                   # Configuration management
│   │
│   ├── square_set_engine.py        # Square-set convergence logic
│   ├── segmentation_engine.py      # MECE classification (B1-N4)
│   ├── buffer_engine_v2.py         # Tier-aware buffer calculation
│   ├── buffer_engine.py            # Legacy v1 buffer engine
│   │
│   ├── netting_ledger.py           # Time-phased inventory netting
│   ├── pegging_engine.py           # Priority-based allocation
│   ├── blocker_engine.py           # Root cause analysis
│   ├── stranded_engine.py          # Capital at risk calculation
│   ├── scenario_engine.py          # What-if simulation
│   │
│   ├── kit_engine.py               # Legacy kit-based planning
│   ├── transfer_model.py           # Lead time shifting
│   ├── calendar_utils.py           # Week/date utilities
│   ├── sql_utils.py                # SQL helpers
│   └── synthetic_data_generator.py # Sample data creation
│
├── templates/                      # Jinja2 HTML templates
│   ├── base.html                   # Base layout with navigation
│   ├── index.html                  # Overview dashboard
│   ├── readiness.html              # Square-set readiness
│   ├── pegging.html                # Priority allocation
│   ├── blockers.html               # Blocker analysis
│   ├── stranded.html               # Stranded inventory
│   ├── scenarios.html              # Scenario comparison
│   ├── convergence.html            # Domain convergence
│   ├── segmentation.html           # MECE segments
│   ├── buffers.html                # Buffer recommendations
│   ├── engineering.html            # GPU generations
│   ├── settings.html               # Configuration UI
│   ├── upload.html                 # File upload
│   ├── diagnostics.html            # Data validation
│   └── error.html                  # Error display
│
├── configs/
│   ├── default_config.yml          # Application defaults
│   ├── column_mapping.yml          # Client column aliases
│   └── user_settings.yml           # User overrides (gitignored)
│
├── data/
│   └── gold/                       # Input parquet files
│       ├── deployment_plan.parquet
│       ├── bom_kit.parquet
│       ├── inventory_position.parquet
│       └── ...
│
└── docs/
    ├── USER_GUIDE.md               # End-user documentation
    └── ARCHITECTURE.md             # This file
```

---

## Core Engine Modules

### SquareSetEngine (`square_set_engine.py`)

**Purpose**: Multi-domain deployment planning with convergence checking.

**Key Methods**:

| Method | Description |
|--------|-------------|
| `get_or_create_square_set_master()` | Load or generate square-set definitions |
| `explode_square_sets()` | Expand square sets into component requirements |
| `get_aggregated_requirements()` | Total item requirements by site/week/item |
| `get_domain_readiness()` | Readiness status by domain |
| `get_convergence_summary()` | Which square sets are fully deployable |

**Data Flow**:
```
deployment_plan → explode_square_sets() → domain requirements
                                        ↓
                            get_domain_readiness()
                                        ↓
                            get_convergence_summary()
```

---

### SegmentationEngine (`segmentation_engine.py`)

**Purpose**: MECE item classification into risk segments.

**Key Classes**:


```python
@dataclass
class SegmentationThresholds:
    high_eo_unit_cost: float = 5000.0
    high_eo_days_to_risk: int = 90
    constrained_lead_time: int = 45
    constrained_confidence: float = 0.70
    shared_usage_threshold: int = 2
    use_category_relative_cost: bool = False
```

**Key Methods**:

| Method | Description |
|--------|-------------|
| `compute_item_dimensions()` | Calculate is_blocker, is_constrained, is_high_eo |
| `assign_base_segments()` | Map to B1-B4 or N1-N4 |
| `compute_overlay_tags()` | Add transition_active, shared_component, etc. |
| `get_full_segmentation()` | Complete segmentation with all attributes |
| `compute_fungibility_factor()` | GPU generation substitutability |

---

### BufferEngineV2 (`buffer_engine_v2.py`)

**Purpose**: Calculate optimal buffer targets based on segment and demand tier.

**Key Methods**:

| Method | Description |
|--------|-------------|
| `get_buffer_policy()` | Policy for segment + tier + tags |
| `calculate_item_buffers()` | Buffer targets for all items |
| `get_tiered_buffer_targets()` | Targets across all demand tiers |
| `calculate_value_at_risk()` | E&O exposure quantification |
| `calculate_eo_penalty_impact()` | Impact of risk-based reductions |

**Buffer Calculation**:
```
Target Qty = Avg Weekly Demand × Target Weeks Coverage

Where Target Weeks is adjusted by:
- Segment risk level (B1 > B2 > ... > N4)
- Demand tier (committed: max, likely: min, exploratory: 0)
- E&O penalty (high_eo or transition_active: -25% to -33%)
- Fungibility (substitutable items: reduced buffer)
```

---

### NettingLedger (`netting_ledger.py`)

**Purpose**: Time-phased inventory netting without double counting.

**Algorithm**:
```
For each (site, week) in chronological order:
    1. Carry forward residual from previous week
    2. Add new supply arriving this week
    3. Subtract demand requirements (by tier priority)
    4. Compute shortfall or surplus
    5. Carry forward residual to next week
```

---

### PeggingEngine (`pegging_engine.py`)

**Purpose**: Allocate scarce supply to demand by priority.

**Tiered Allocation**:
```
1. Tier 1 (Committed): Full allocation, track shortage
2. Tier 2 (Likely): Allocate remaining supply
3. Tier 3 (Exploratory): Allocate any residual

Each tier can only use supply not allocated to higher tiers.
```

---

## Data Contract

Required tables and their key columns (demand_plan can stand in for deployment_plan):

| Table | Required Columns | Optional Columns |
|-------|------------------|------------------|
| `deployment_plan` | week, site_id, kit_id, kits_planned, demand_tier | priority, program_id |
| `demand_plan` (fallback) | week, site_id, kit_id, kits_planned | demand_tier |
| `bom_kit` | kit_id, child_item_id, qty_per, effective_start_week | effective_end_week, revision, kit_criticality |
| `inventory_position` | as_of_date, item_id, node_id, on_hand, usable_on_hand | reserved, aging_days, unit_cost |
| `supply` | item_id, node_id, qty, status, promised_date/promise_date/promise_week (one required) | allocation_flag, confidence_weight |
| `site_readiness` | scenario_id, site_id, week | readiness_capacity_kits, power_ready_mw, readiness_state |
| `item_master` | item_id, category, subcategory, value_density, shared_flag, build_ahead_flag | unit_cost, description, uom |
| `node_master` | node_id, site_id, node_type | region |
| `lane_master` | from_node_id, to_node_id, transfer_lead_time_days | transfer_capacity_units_per_week, allowed_categories |

Optional tables (used if present):

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `square_set_master` | Square set to kit mapping | square_set_id, site_id, it_rack_kit_id, callan_kit_id, mor_kit_id, power_mw_required |
| `lifecycle` | GPU generation and LTB/EOL dates | item_id, generation, compatibility_group, transition_start_date, transition_end_date, ltb_date, eol_date |
| `substitution_map` | Item substitution rules | from_item_id, to_item_id, substitution_type, approval_required |
| `lead_time_history` | Historical lead times | item_id, lead_time_p95 or lead_time_days |
| `lead_time_distribution` | Lead time distribution | item_id, p95 or lead_time_p95 or lead_time_days |

---

## API Endpoints

### Data Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/reload-data` | POST | Reload data from gold tables |
| `/api/run-analysis` | POST | Execute analytics computations |
| `/api/set-scenario` | POST | Change active scenario |
| `/api/contract-status` | GET | Current data contract validation |
| `/api/table-info/<table_name>` | GET | Table schema and stats |

### Settings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/settings` | POST | Save segmentation thresholds |
| `/api/settings/reset` | POST | Reset to defaults |
| `/api/settings/export` | GET | Export as YAML |

### Exceptions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/exceptions` | GET | List exceptions |
| `/api/exceptions` | POST | Create/update exception |
| `/api/exceptions/<exception_id>` | DELETE | Remove exception |

### Exports

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/export/weekly-status` | GET | JSON weekly status |
| `/export/leadership-update` | GET | JSON leadership summary |
| `/export/buffer-analysis` | GET | JSON buffer analysis |
| `/export/csv/<type>` | GET | CSV download (buffers, blockers, etc.) |

---

## Configuration Reference

### `default_config.yml`

```yaml
data:
  gold_path: "./data/gold"
  runs_path: "./runs"

analysis:
  week_start: 0                    # Monday
  default_scenario: "baseline"
  horizon_weeks: 12
  transfers:
    enabled: true
    assume_unlimited_capacity: false
  pegging:
    enabled: true
    default_priority: 50

buffer_policy:
  lead_time_high_risk: 60
  lead_time_medium_risk: 30
  low_confidence_threshold: 0.7
  aging_warning: 60
  aging_critical: 90
  targets:
    GPU_blocking_high_risk: {min: 4, max: 6, location: "integration"}
    default: {min: 2, max: 3, location: "regional"}
```

---

## Testing

```bash
# Run all tests
pytest -q

# Run specific test file
pytest tests/test_square_set_integration.py -v

# Run with coverage
pytest --cov=src/bufferlab_deploy
```

---

## Extension Points

### Adding a New Engine

1. Create module in `src/bufferlab_deploy/`
2. Accept `DuckDBLoader` in constructor
3. Add route in `app.py`
4. Create template in `templates/`

### Adding a New Segment Dimension

1. Extend `SegmentationThresholds` dataclass
2. Add dimension computation in `compute_item_dimensions()`
3. Update segment assignment logic
4. Add UI control in `settings.html`

### Adding a New Data Table

1. Add schema definition in `data_contract.py`
2. Register load logic in `duckdb_loader.py`
3. Update column mapping in `column_mapping.yml`
4. Add validation checks in `diagnostics.html`

---

## Dependencies

Key Python packages:

| Package | Version | Purpose |
|---------|---------|---------|
| Flask | 2.x | Web framework |
| DuckDB | 0.9+ | Embedded analytics database |
| Polars | 0.19+ | DataFrame operations |
| PyYAML | 6.x | Configuration parsing |
| Jinja2 | 3.x | HTML templating |

---

*Last updated: January 2026*
