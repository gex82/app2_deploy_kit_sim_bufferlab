# App 2 Scope Expansion: Square Sets & Advanced Segmentation

## Overview

This plan extends App 2 from kit-centric planning to **square-set convergence** with **demand tiers** and **MECE segmentation**.

---

## Gap Review (Omissions/Commissions)

### Potential Omissions Identified & Addressed

| Gap | Resolution |
|-----|------------|
| No Settings UI for thresholds | Added Phase I: Settings page with all segmentation inputs |
| Missing `days_to_risk` computation | Will derive from lifecycle EOL/LTB dates or aging days |
| No synthetic square-set data generator | Added to Phase A |
| Build-ahead sensitivity computation unclear | Define as items with >30% historical stranding when one domain missing |
| No domain-level visibility | Convergence dashboard shows per-domain readiness |

### Potential Commissions (Avoided)
- **Not replacing** all "kit" references - kits still exist as domain components within square-sets
- Keeping substitution_map simple for v1 - only essential fields

---

## Key Concept Changes

| Current (Kit-Centric) | New (Square-Set Centric) |
|-----------------------|--------------------------|
| Kit = single BOM | Square Set = convergence of 3 domains (IT Rack, Callan/HXU, MOR/Network) |
| Single demand plan | Tiered demand (committed, likely, exploratory) |
| Simple category segmentation | B1-B4, N1-N4 segments + overlay tags |
| Fixed buffer ranges | Value-at-risk adjusted buffers by segment + tier |

---

## Implementation Phases

### Phase A: Data Contract Extensions

#### A1. Extend `deployment_plan` schema
Add `demand_tier` field (committed, likely, exploratory)

#### A2. Extend `item_master` schema
- `value_density` (value per unit weight/volume)
- `shared_flag` or compute `cross_program_count`
- `build_ahead_flag`

#### A3. Extend `lifecycle` table
- `generation` (GPU generation identifier)
- `compatibility_group` (fungibility rules)
- `transition_start_date`, `transition_end_date`

#### A4. Create `square_set_master` table
```
square_set_id | site_id | it_rack_kit_id | callan_kit_id | mor_kit_id | power_mw_required
```

#### A5. Extend `substitution_map`
- `substitution_type` (minor_gen, major_gen, equivalent)
- `approval_required` (boolean)

---

### Phase B: Square-Set Engine

#### B1. Create `square_set_engine.py`
- Replace kit explosion with square-set explosion
- Aggregate requirements across 3 domains per deployment
- Compute required components per square set

#### B2. Update terminology
Replace "kit" → "square set" or "buildable deployment" in:
- Routes, templates, UI labels
- API responses

#### B3. Implement domain convergence check
- For each site/week, check if all 3 domains have sufficient inventory
- Flag partial readiness (e.g., "IT Rack ready, Callan missing")

---

### Phase C: Advanced Segmentation Engine

#### C1. Create `segmentation_engine.py`
Assign each item to exactly one base segment:

**Blockers (B segments):**
| Segment | Criteria |
|---------|----------|
| B1 | Blocker + Constrained + High E&O |
| B2 | Blocker + Constrained + Not High E&O |
| B3 | Blocker + Not Constrained + High E&O |
| B4 | Blocker + Not Constrained + Not High E&O |

**Non-blockers (N segments):**
| Segment | Criteria |
|---------|----------|
| N1 | Non-blocker + Constrained + High E&O |
| N2 | Non-blocker + Constrained + Not High E&O |
| N3 | Non-blocker + Not Constrained + High E&O |
| N4 | Non-blocker + Not Constrained + Not High E&O |

#### C2. Compute segment dimensions
- `is_blocker`: from `kit_criticality` in BOM
- `is_constrained`: allocation_flag OR confidence < 0.7 OR lead_time_p95 > threshold
- `is_high_eo`: unit_cost > X AND days_to_risk < Y

#### C3. Implement overlay tags
| Tag | Logic |
|-----|-------|
| `transition_active` | Current date within [transition_start, transition_end] |
| `shared_component` | Usage count > 1 across programs/categories |
| `long_lead_foundation` | lead_time_p95 > 60 days AND days_to_risk > 180 |
| `build_ahead_sensitivity` | Historical stranding from non-convergence |
| `break_glass_exception` | Manual override flag |

---

### Phase D: Demand Tier Handling

#### D1. Extend `scenario_engine.py`
- Support tiered demand scenarios
- Separate netting ledger runs per tier

#### D2. Buffer posture by tier
| Tier | Buffer Posture |
|------|----------------|
| Committed | Allow full buffer (up to max coverage) |
| Likely | Cap at min coverage |
| Exploratory | Zero buffer (commitments/options only) |

---

### Phase E: Buffer Target Engine v2

#### E1. Create `buffer_engine_v2.py`
- Segment-based buffer ranges (not config-only)
- E&O penalty: `value_at_risk = unit_cost × inventory_days × days_to_risk_factor`
- Shrink buffers for high E&O segments

#### E2. Buffer range by segment + tag
Example matrix (configurable):
| Segment | Base Range | With `transition_active` | Location |
|---------|------------|--------------------------|----------|
| B1 | 2-3 weeks | 1-2 weeks (reduced) | Integration |
| B2 | 3-4 weeks | 2-3 weeks | Integration |
| B3 | 2-3 weeks | 1-2 weeks | Regional |
| B4 | 4-6 weeks | 3-4 weeks | Regional |
| N1-N4 | 1-2 weeks | 0-1 weeks | Site |

---

### Phase F: Convergence Dashboard

#### F1. Create `/convergence` route
- Show planned vs deployable square sets by site/week
- Highlight "missing domains" blocking readiness

#### F2. Square-set convergence gate
- Only allocate when all domains + power ready
- Otherwise flag as potential stranding

#### F3. Build-ahead exception log
- Approver, justification, end date
- Display in UI with expiry warnings

---

### Phase G: Engineering Insights

#### G1. Create `/engineering` route
- GPU generation timeline
- Transition windows (LTB/EOL)
- Fungibility view (substitution paths)

#### G2. Substitution recommendations
- Show retrofit/re-kit options to reduce stranded inventory
- Minor vs major generation changes

---

### Phase H: Governance & Reporting

#### H1. Weekly status report export
- Excel with summary + details tabs
- Aligned with SOW metrics

#### H2. 4-week leadership update
- Executive summary view
- Key risk callouts

#### H3. Data privacy section in README
- Non-PII only
- Local DuckDB processing
- Retention policies

---

## File Changes Summary

### New Files
| File | Purpose |
|------|---------|
| `square_set_engine.py` | Square-set explosion and convergence |
| `segmentation_engine.py` | B1-B4, N1-N4 assignment + overlay tags |
| `buffer_engine_v2.py` | Segment-aware buffer targets with E&O |
| `routes/convergence.py` | Convergence dashboard |
| `routes/engineering.py` | GPU generation and substitution view |
| `templates/convergence.html` | Missing domain visualization |
| `templates/engineering.html` | Transition and fungibility view |
| `templates/segmentation.html` | Segment counts and filters |

### Modified Files
| File | Changes |
|------|---------|
| `data_contract.py` | Add demand_tier, value_density, generation checks |
| `kit_engine.py` → `square_set_engine.py` | Replace kit with square set |
| `pegging_engine.py` | Add convergence gate |
| `netting_ledger.py` | Support tiered demand runs |
| `scenario_engine.py` | Multi-tier scenario handling |
| `app.py` | New routes, updated nav |
| `base.html` | Updated nav labels |
| All templates | "kit" → "square set" terminology |

---

## Schema Extensions Required (App 1)

> [!IMPORTANT]  
> These changes need to be made in App 1's `canonical.py` and sample data generator before App 2 can use them.

```python
# deployment_plan additions
demand_tier: Literal["committed", "likely", "exploratory"] = "committed"

# item_master additions
value_density: float | None = None
shared_flag: bool = False
build_ahead_flag: bool = False

# lifecycle additions
generation: str | None = None
compatibility_group: str | None = None
transition_start_date: date | None = None
transition_end_date: date | None = None

# New table: square_set_master
class SquareSetMaster(BaseModel):
    square_set_id: str
    site_id: str
    it_rack_kit_id: str
    callan_kit_id: str
    mor_kit_id: str
    power_mw_required: float

# substitution_map extensions
substitution_type: Literal["minor_gen", "major_gen", "equivalent"] = "equivalent"
approval_required: bool = False
```

---

### Phase I: Settings & Configuration Page

#### I1. Create `/settings` route and page
UI for all configurable thresholds (persisted to session/config):

**Segmentation Thresholds:**
| Setting | Description | Default |
|---------|-------------|---------|
| High E&O Unit Cost Threshold | Items above this cost considered high E&O risk | $5,000 |
| High E&O Days-to-Risk Threshold | Items with < this days to EOL/LTB are high E&O | 90 days |
| Constrained Lead Time Threshold | Items with lead_time_p95 > this are constrained | 45 days |
| Constrained Confidence Threshold | Items with confidence < this are constrained | 0.70 |
| Long Lead Foundation Threshold | lead_time_p95 > this = long lead | 60 days |
| Long Lead Days-to-Risk Min | days_to_risk > this = foundation (low obsolescence) | 180 days |
| Build-Ahead Stranding Threshold | Historical stranding % above this = sensitive | 30% |
| Shared Component Usage Threshold | Usage count > this = shared | 1 |

**Buffer Policy Settings:**
| Setting | Description | Default |
|---------|-------------|---------|
| Committed Tier Max Coverage | Max buffer weeks for committed demand | 6 weeks |
| Likely Tier Max Coverage | Max buffer weeks for likely demand | 2 weeks |
| Exploratory Tier Coverage | Buffer for exploratory (commitments only) | 0 weeks |
| Transition Active Buffer Reduction | % reduction when transition_active tag | 33% |

**Analysis Settings:**
| Setting | Description | Default |
|---------|-------------|---------|
| Default Scenario | Scenario to use if not selected | baseline |
| Horizon Weeks | Number of weeks to project | 12 |
| Week Start | Day of week for week start | Monday |
| MW per Square-Set | If readiness in MW, conversion factor | 0.5 |

#### I2. Settings persistence
- Store in Flask session for current run
- Allow save/load to YAML config file

---

## Execution Order

1. **Phase A** - Data contract + synthetic square-set generator
2. **Phase I** - Settings page (enables threshold tuning early)
3. **Phase B** - Square-set engine
4. **Phase C** - Segmentation engine with UI-configurable thresholds
5. **Phase D** - Demand tier handling
6. **Phase E** - Buffer engine v2
7. **Phase F** - Convergence dashboard
8. **Phase G** - Engineering insights
9. **Phase H** - Governance & reporting

---

## Ready to Execute

User confirmed:
- ✅ Assume new fields present (no App 1 updates first)
- ✅ Generate synthetic square-set mapping
- ✅ UI-configurable thresholds (not hardcoded)
- ✅ Use "square-set" terminology
