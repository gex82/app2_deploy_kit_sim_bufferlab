# App 2 Implementation Plan: Scope Alignment & Real Data Readiness

## Overview

This plan addresses the remaining gaps identified in the SOW review to make App 2 fully aligned with the square-set deployment planning scope and robust enough for **real client data**.

---

## Current State Summary

### ✅ Already Implemented
- Netting ledger with time-phased inventory (no double counting)
- Tiered pegging (`run_tiered_pegging()`) - committed → likely → exploratory
- MECE segmentation engine (B1-B4, N1-N4) with configurable thresholds
- Buffer engine v2 with value-at-risk calculations
- Square-set engine with convergence checks and power gating
- Data contract validation for new fields (demand_tier, lifecycle, etc.)
- Settings page with session-based threshold storage
- Convergence dashboard, Engineering insights, Governance exports
- Build-ahead exception log with API endpoints
- README data privacy statement
- Synthetic data generator and calibration script

### ❌ Remaining Gaps (This Plan)
1. Core logic still uses `KitEngine` instead of `SquareSetEngine`
2. Demand tiers not integrated into buffer sizing/requirements
3. Segmentation thresholds need refinement (shared ≥2, category-relative cost)
4. No engineering logic for GPU generation fungibility
5. No preprocessing script for client flat files
6. Settings not persisted to file (session-only)
7. Limited error messaging for missing data

---

## Phase 1: Core Logic Completion (High Impact)

### 1.1 Replace KitEngine with SquareSetEngine End-to-End

**Goal:** Make square-set the atomic planning unit throughout the analytics.

#### [MODIFY] [netting_ledger.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/netting_ledger.py)

Replace:
```python
self.kit_engine = KitEngine(loader)
requirements = self.kit_engine.get_aggregated_requirements(scenario_id)
```

With:
```python
self.square_set_engine = SquareSetEngine(loader)
requirements = self.square_set_engine.get_aggregated_requirements(scenario_id)
```

Changes needed:
- Import `SquareSetEngine` instead of/in addition to `KitEngine`
- Update `build_ledger()` to use square-set requirements
- Ensure square-set domain components are properly exploded

#### [MODIFY] [pegging_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/pegging_engine.py)

- Replace `KitEngine` with `SquareSetEngine` in constructor
- Update `run_pegging()` to work with square-set requirements
- Add domain-level convergence check before allowing allocation

#### [MODIFY] [blocker_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/blocker_engine.py)

- Update blocker attribution to use square-set context
- Show which domain(s) are blocking each square-set

#### [MODIFY] [stranded_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/stranded_engine.py)

- Detect stranded inventory when domains don't converge
- Flag partial-domain readiness as stranding risk

---

### 1.2 Integrate Demand Tiers Throughout

**Goal:** Buffer sizing, requirements, and netting should all respect demand tier.

#### [MODIFY] [buffer_engine_v2.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/buffer_engine_v2.py)

- Add `get_tiered_buffer_targets()` that returns different targets by tier
- Committed: full buffer (up to max weeks)
- Likely: capped at min weeks
- Exploratory: zero buffer

#### [MODIFY] [square_set_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/square_set_engine.py)

- `get_aggregated_requirements()` should accept `demand_tier` parameter
- Return requirements filtered/grouped by tier

#### [MODIFY] [netting_ledger.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/netting_ledger.py)

- Add `build_tiered_ledger()` that runs netting per tier in sequence
- Committed consumes first, residual flows to likely, then exploratory

---

### 1.3 Fix Segmentation Thresholds

#### [MODIFY] [segmentation_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/segmentation_engine.py)

**Shared component threshold:**
```python
# Change from:
shared_usage_threshold: int = 1
# To:
shared_usage_threshold: int = 2  # ≥2 means shared
```

**Category-relative cost thresholds:**
- Add `use_category_relative_cost: bool = False`
- When enabled, compare unit_cost to category median instead of absolute $5K
- Implement `_get_category_cost_percentile()` method

**Build-ahead sensitivity:**
- Replace `pl.lit(False)` placeholder with actual computation
- Query historical stranding data if available
- Default to False if no historical data

---

## Phase 2: Robustness for Real Data (Critical for Client Use)

### 2.1 Create Preprocessing Script for Client Flat Files

#### [NEW] [preprocess_client_data.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/preprocess_client_data.py)

A standalone script/utility that:

```python
class ClientDataPreprocessor:
    """
    Validates and converts client flat files to App 2 format.
    
    Features:
    - Auto-detect CSV/Excel/Parquet input
    - Column name mapping (configurable via YAML)
    - Data type validation and conversion
    - Missing field detection with recommendations
    - Parquet output to data/gold/
    - Detailed validation report
    """
    
    def __init__(self, mapping_file: str = "column_mapping.yml"):
        ...
    
    def process_file(self, input_path: str, table_name: str) -> ValidationResult:
        ...
    
    def process_directory(self, input_dir: str) -> dict[str, ValidationResult]:
        ...
    
    def generate_report(self) -> str:
        ...
```

#### [NEW] [column_mapping.yml](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/configs/column_mapping.yml)

```yaml
# Maps client column names to App 2 expected names
# NOTE: Uses square-set as atomic planning unit (not kit)

deployment_plan:
  aliases:
    square_sets_planned: ["quantity", "qty", "planned_qty", "units", "kits_planned"]
    square_set_id: ["deployment_id", "build_id", "kit_id"]
    site_id: ["location", "site", "dc_id"]
    week: ["plan_week", "target_week", "date"]
    demand_tier: ["tier", "priority_tier", "demand_type"]
  required: ["week", "site_id", "square_set_id", "square_sets_planned"]
  optional: ["demand_tier", "priority"]

square_set_master:
  aliases:
    square_set_id: ["deployment_id", "build_id"]
    it_rack_kit_id: ["it_rack_id", "compute_kit_id"]
    callan_kit_id: ["hxu_kit_id", "cooling_kit_id"]
    mor_kit_id: ["network_kit_id", "fabric_kit_id"]
  required: ["square_set_id", "site_id", "it_rack_kit_id", "callan_kit_id", "mor_kit_id"]

item_master:
  aliases:
    item_id: ["part_number", "sku", "component_id"]
    category: ["item_category", "type"]
    unit_cost: ["cost", "price", "unit_price"]
  ...
```

---

### 2.2 Persistent Settings Storage

#### [MODIFY] [app.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/app.py)

Add file-based settings persistence:

```python
# Local-only settings file (gitignored)
SETTINGS_FILE = Path("configs/user_settings.yml")

def load_settings_from_file() -> dict:
    """Load settings from YAML file if exists."""
    if SETTINGS_FILE.exists():
        return yaml.safe_load(SETTINGS_FILE.read_text())
    return {}

def save_settings_to_file(settings: dict) -> None:
    """Save settings to YAML file (local only, not committed)."""
    SETTINGS_FILE.parent.mkdir(exist_ok=True)
    SETTINGS_FILE.write_text(yaml.dump(settings))
```

Update `/api/settings` endpoint:
- Accept `persist: bool` parameter
- If True, save to file in addition to session

> [!IMPORTANT]
> Add `configs/user_settings.yml` to `.gitignore` - this file is local-only and should not be committed.

---

### 2.3 Improve Error Messaging

#### [MODIFY] [duckdb_loader.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/duckdb_loader.py)

Add user-friendly error collection:

```python
class LoaderErrors:
    missing_tables: list[str]
    missing_columns: dict[str, list[str]]
    type_mismatches: list[str]
    
    def get_user_message(self) -> str:
        """Generate actionable error message for UI."""
```

#### [MODIFY] Templates

Add error banners when data is incomplete:
```html
{% if loader_errors %}
<div class="error-banner">
    <h3>Data Issues Detected</h3>
    <ul>
    {% for error in loader_errors %}
        <li>{{ error }}</li>
    {% endfor %}
    </ul>
    <a href="/diagnostics">View Details</a>
</div>
{% endif %}
```

---

### 2.8 Scenario Templates (Definition)

> **Moved from Phase 3** - Templates are needed early for client discussions.

#### [MODIFY] [scenario_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/scenario_engine.py)

Add structured scenario variants with clear naming:

```python
SCENARIO_TEMPLATES = {
    "baseline": {
        "description": "Current plan assumptions",
        "power_adjustment": 1.0,
        "supply_adjustment": 1.0,
        "demand_adjustment": 1.0,
    },
    "favorable": {
        "description": "Eased constraints: more power/supply, same demand",
        "power_adjustment": 1.2,   # 20% more power available
        "supply_adjustment": 1.1,  # 10% more supply
        "demand_adjustment": 1.0,  # demand unchanged
    },
    "stressed": {
        "description": "Tighter constraints: less power/supply, increased demand",
        "power_adjustment": 0.8,   # 20% less power
        "supply_adjustment": 0.8,  # 20% supply cut
        "demand_adjustment": 1.2,  # 20% more demand pressure
    },
}

def run_scenario_variant(self, template: str, base_scenario: str) -> dict:
    """Apply scenario template adjustments to base scenario."""
```

> [!NOTE]
> "Favorable" = eased constraints (more capacity/supply, demand unchanged).  
> "Stressed" = tighter constraints (less capacity/supply, higher demand pressure).

---

## Phase 3: Engineering & Governance

### 3.1 Implement Fungibility Logic for GPU Generations

#### [MODIFY] [segmentation_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/segmentation_engine.py)

Add generation-aware logic:

```python
def compute_fungibility_factor(self, item_id: str) -> dict:
    """
    Determine item fungibility based on GPU generation.
    
    Returns:
        {
            "generation": "H100",
            "compatibility_group": "CG-H100",
            "can_substitute_to": ["H200"],  # minor gen
            "can_substitute_from": [],
            "substitution_type": "minor_gen" | "major_gen" | None
        }
    """
```

#### [MODIFY] [buffer_engine_v2.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/buffer_engine_v2.py)

Adjust buffer policy when item is fungible:
- Fungible items can have slightly lower buffers
- Items in transition get reduced buffers

---

### 3.2 Build-Ahead Sensitivity Implementation

#### [MODIFY] [segmentation_engine.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/src/bufferlab_deploy/segmentation_engine.py)

Replace placeholder with actual computation:

```python
def _compute_build_ahead_sensitivity(self, item_id: str) -> bool:
    """
    Determine if item has historical build-ahead stranding.
    
    Logic:
    1. Look for historical cases where item was built ahead
    2. Check if subsequent non-convergence caused stranding
    3. If stranding rate > threshold (30%), mark as sensitive
    
    If no historical data available, return False.
    """
```

---

### 3.3 Scenario Template UI (Quick-Select Buttons)

> **Note:** Scenario templates are defined in Phase 2.8 below. This phase adds the UI quick-select buttons.

#### [MODIFY] [scenarios.html](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/templates/scenarios.html)

Add quick-select buttons for baseline/favorable/stressed scenarios that use the templates from scenario_engine.py.

---

## Verification Plan

### Automated Tests

#### [NEW] [tests/test_square_set_integration.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/tests/test_square_set_integration.py)

- Test that netting ledger uses square-set requirements
- Test tier-based allocation priority
- Test convergence gating blocks partial-domain allocation

#### [NEW] [tests/test_client_preprocessing.py](file:///c:/Users/ely.x.colon/OneDrive%20-%20Accenture/Desktop/app2_deployment_kit_sim/tests/test_client_preprocessing.py)

- Test CSV with different column names
- Test missing required columns
- Test type conversion

### Manual Verification

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Generate synthetic data | Parquet files in data/gold with square-set master |
| 2 | Run app, check /convergence | Square-set convergence table, not kit readiness |
| 3 | Change settings, restart app | Settings should persist |
| 4 | Use preprocessing script on sample CSV | Clean Parquet output with mapped columns |
| 5 | Load data with missing table | Clear error message in UI |

---

## Execution Order

### Week 1: Phase 1 (Core Logic)
1. Replace KitEngine with SquareSetEngine in netting_ledger.py
2. Update pegging_engine.py for square-set context
3. Integrate demand_tier into buffer_engine_v2.py
4. Fix segmentation thresholds (shared ≥2, category-relative)

### Week 2: Phase 2 (Data Robustness)
5. Create preprocess_client_data.py and column_mapping.yml
6. Add persistent settings storage
7. Improve error messaging in loader and UI

### Week 3: Phase 3 (Engineering & Governance)
8. Implement fungibility logic
9. Build-ahead sensitivity computation
10. Scenario templates (baseline/upside/downside)

---

## Files Summary

### New Files
| File | Purpose |
|------|---------|
| `preprocess_client_data.py` | Client flat file → Parquet converter |
| `configs/column_mapping.yml` | Column name aliases for preprocessing |
| `configs/user_settings.yml` | Persistent settings storage |
| `tests/test_square_set_integration.py` | Integration tests |
| `tests/test_client_preprocessing.py` | Preprocessing tests |

### Modified Files
| File | Changes |
|------|---------|
| `netting_ledger.py` | Use SquareSetEngine, tiered ledger |
| `pegging_engine.py` | Square-set context, domain convergence |
| `blocker_engine.py` | Square-set blocker attribution |
| `stranded_engine.py` | Domain non-convergence stranding |
| `buffer_engine_v2.py` | Tiered buffer targets, fungibility adjustment |
| `segmentation_engine.py` | Fixed thresholds, category-relative, fungibility |
| `scenario_engine.py` | Scenario templates |
| `duckdb_loader.py` | Better error collection |
| `app.py` | Persistent settings, error banners |

---

## Success Criteria

### Core Logic (Phase 1)
- [ ] All pages use "square-set" terminology, not "kit"
- [ ] Netting ledger requirements come from `SquareSetEngine.get_aggregated_requirements()`
- [ ] `SquareSetEngine.get_aggregated_requirements()` accepts and respects `demand_tier` parameter
- [ ] Buffer targets vary by demand tier (max for committed, min for likely, zero for exploratory)
- [ ] Buffer outputs include tier breakdown (committed/likely/exploratory columns)
- [ ] Shared component threshold ≥2
- [ ] Category-relative cost thresholds available as option

### Data Robustness (Phase 2)
- [ ] Client can drop CSV files and preprocess to Parquet
- [ ] Preprocessing uses square-set terminology (`square_set_id`, `square_sets_planned`)
- [ ] Settings persist across app restarts (via `configs/user_settings.yml`)
- [ ] `configs/user_settings.yml` is in `.gitignore`
- [ ] Missing data shows actionable error messages in UI
- [ ] Scenario templates (baseline/favorable/stressed) are available in `scenario_engine.py`

### Engineering & Governance (Phase 3)
- [ ] Scenario page offers baseline/favorable/stressed quick-select buttons
- [ ] Fungibility factor computed for items with generation data
- [ ] Build-ahead sensitivity computed (or defaults to False if no historical data)
