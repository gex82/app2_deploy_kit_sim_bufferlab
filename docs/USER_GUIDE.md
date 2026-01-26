# BufferLab User Guide

> **BufferLab – Deployment & Square-Set Readiness** is a decision-grade analytics application for GPU deployment planning. It answers the critical question: *"Can we deploy on time, and if not, what's blocking us?"*

---

## Table of Contents

1. [What Is BufferLab?](#1-what-is-bufferlab)
2. [Core Concepts](#2-core-concepts)
3. [Getting Started](#3-getting-started)
4. [Dashboard Overview](#4-dashboard-overview)
5. [Detailed Page Guide](#5-detailed-page-guide)
6. [Data Exports](#6-data-exports)
7. [Uploading Your Data](#7-uploading-your-data)
8. [Configuration & Settings](#8-configuration--settings)
9. [Troubleshooting](#9-troubleshooting)
10. [Glossary](#10-glossary)

---

## 1. What Is BufferLab?

BufferLab is a **local-first Flask + DuckDB analytics application** that transforms raw supply chain data into actionable deployment insights. Unlike traditional reporting tools that simply aggregate numbers, BufferLab:

| Traditional Reporting | BufferLab Approach |
|-----------------------|--------------------|
| Tracks individual SKUs | Tracks **Square Sets** (complete deployable units) |
| Reports inventory totals | Simulates **tiered allocation** based on demand confidence |
| Shows generic shortages | Identifies **convergence blockers** across domains |
| One-size-fits-all buffers | **Risk-segmented** buffer targets (B1–N4) |

### Key Value Propositions

- **Prevents "Partial Builds"** – Ensures all three domains (IT Rack, Power, Network) converge before signaling readiness
- **Prioritizes Scarce Supply** – Committed demand gets allocated first, protecting firm delivery dates
- **Minimizes Stranded Capital** – Quantifies the dollar value of inventory blocked by missing components
- **Dynamic Buffer Sizing** – Adjusts inventory targets based on risk profile and demand tier

---

## 2. Core Concepts

### 2.1 The Square Set

A **Square Set** is the atomic unit of deployment - representing one complete, deployable unit of compute capacity (default ~0.5 MW per kit, configurable).

```
┌─────────────────────────────────────────────────────────────┐
│                       SQUARE SET                            │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  IT Rack    │  │   Callan    │  │    MOR      │         │
│  │  Domain     │  │   Domain    │  │   Domain    │         │
│  │             │  │             │  │             │         │
│  │ • GPU Servers│  │ • HXU Cooling│  │ • Network   │         │
│  │ • Racks     │  │ • Power Dist │  │ • Switches  │         │
│  │ • Compute   │  │ • Sidecars  │  │ • Optics    │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│                                                             │
│  ✓ Ready = ALL domains ready    ✗ Not Ready = ANY missing │
└─────────────────────────────────────────────────────────────┘
```

**Key Principle**: A Square Set is only "deployable" when **all three domains** have their required blocking components available.

---

### 2.2 MECE Segmentation (B1–N4)

Every component is classified into exactly **one** of 8 segments using three dimensions:

| Dimension | Question | Impact |
|-----------|----------|--------|
| **Blocker** | Does this stop the build? | B1–B4 (Blockers) vs N1–N4 (Non-blockers) |
| **Constrained** | Is supply scarce or unreliable? | Constrained = Higher buffer priority |
| **High E&O** | Is it expensive + near end-of-life? | High E&O = Reduced buffer to minimize risk |

**The 8 Segments:**

| Segment | Profile | Buffer Priority |
|---------|---------|-----------------|
| **B1** | Blocker + Constrained + High E&O | 🔴 Critical – Must watch closely |
| **B2** | Blocker + Constrained + Low E&O | 🟠 High – Safe to buffer more |
| **B3** | Blocker + Unconstrained + High E&O | 🟡 Medium – Watch obsolescence |
| **B4** | Blocker + Unconstrained + Low E&O | 🟢 Low – Generally safe |
| **N1–N4** | Non-blocking variants | Lower priority than B-segments |

---

### 2.3 Demand Tiers

BufferLab prioritizes supply allocation based on demand confidence:

| Tier | Description | Buffer Policy |
|------|-------------|---------------|
| **Committed** | Firm orders, contractual obligations | Full buffer coverage (max weeks) |
| **Likely** | High-probability demand, expected to materialize | Capped buffer (min weeks) |
| **Exploratory** | Speculative, planning purposes only | Zero buffer (don't stock) |

---

### 2.4 Convergence Gating

The system won't mark a deployment "Ready" unless ALL domains converge:

```
Convergence Check:
  IT Rack Ready?  ✓
  Callan Ready?   ✓
  MOR Ready?      ✗ ← Missing network cables

  Final Status: ❌ NOT DEPLOYABLE
  
  >> All other components = STRANDED CAPITAL
```

---

## 3. Getting Started

### 3.1 System Requirements

- **Python 3.11+**
- **Windows, macOS, or Linux**
- No external database required (uses embedded DuckDB)

### 3.2 Installation

```bash
# Clone or download the repository
cd app2_deployment_kit_sim

# Create virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # macOS/Linux

# Install dependencies
python -m pip install -r requirements.txt
```

### 3.3 Running the Application

```bash
python app.py
```

Open your browser to **http://127.0.0.1:5001**

### 3.4 Generate Sample Data (Optional)

To explore BufferLab with synthetic data:

```bash
python -m src.bufferlab_deploy.synthetic_data_generator
```

This creates:
- 10 square sets
- 45 items
- 3 scenarios (baseline, optimistic, constrained)
- Multiple demand tiers

---

## 4. Dashboard Overview

After launching, the **Overview** page provides:

| Metric | Description |
|--------|-------------|
| **Completion Rate** | % of planned square sets that are fully deployable |
| **Stranded Capital** | $ value of inventory blocked by missing components |
| **Top Blockers** | Components causing the most deployment failures |
| **Week-over-Week Trend** | How readiness is changing over time |

### Navigation Menu

| Page | Purpose |
|------|---------|
| **Overview** | Executive dashboard with KPIs |
| **Readiness** | Square-set readiness by site/week |
| **Pegging** | Supply allocation priority waterfall |
| **Blockers** | Root cause analysis of blockers |
| **Stranded** | Inventory risk quantification |
| **Scenarios** | What-if comparisons |
| **Convergence** | Domain-level readiness tracking |
| **Segments** | Item classification (B1–N4) |
| **Buffers** | Buffer target recommendations |
| **Engineering** | GPU generations & transitions |
| **Settings** | Configure thresholds |
| **Upload** | Import your data files |
| **Diagnostics** | Data validation & troubleshooting |

---

## 5. Detailed Page Guide

### 5.1 Overview Page
**URL:** `/`

The home page showing:
- **KPI Cards**: Completion rate, stranded $, square sets planned vs ready
- **Trend Charts**: Week-by-week deployment readiness
- **Quick Actions**: Reload data, change scenario, export reports

---

### 5.2 Square-Set Readiness Page
**URL:** `/readiness`

Shows the deployment readiness status:

| Column | Meaning |
|--------|---------|
| **Week** | Planning week |
| **Site** | Deployment location |
| **Planned** | Square sets scheduled |
| **Deployable** | Fully ready to deploy |
| **Buildable** | Have components, awaiting site readiness |
| **Gap** | Shortfall to plan |

**Use This Page To:**
- Identify which sites are falling behind
- Drill into specific weeks to see blockers
- Compare sites' performance

---

### 5.3 Priority & Pegging Page
**URL:** `/pegging`

Visualizes how scarce supply is allocated:

```
┌─────────────────────────────────────────────────────────────┐
│                    ALLOCATION WATERFALL                     │
│                                                             │
│  Available Supply: 100 units                                │
│                                                             │
│  Tier 1 (Committed):  80 allocated │ 0 short               │
│  Tier 2 (Likely):     15 allocated │ 5 short               │
│  Tier 3 (Exploratory): 5 allocated │ 25 short              │
│                                                             │
│  Residual: 0 units                                          │
└─────────────────────────────────────────────────────────────┘
```

**Use This Page To:**
- Verify committed demand is protected
- See which tiers are experiencing shortages
- Understand allocation fairness across priority levels

---

### 5.4 Blockers Page
**URL:** `/blockers`

Pareto analysis of blocking components:

| Analysis | Description |
|----------|-------------|
| **Blocker Pareto** | Top components by total gap qty/$ (with square_sets_affected) |
| **Root Cause** | Why is this item blocking? (transfer delay, supply timing, pure shortage) |
| **Fix Recommendations** | Actionable steps to resolve |
| **Impact Quantification** | Gap qty/$ and affected square sets |

**Use This Page To:**
- Focus engineering on high-impact items
- Generate expedite lists for procurement
- Prioritize supplier escalations

---

### 5.5 Stranded Inventory Page
**URL:** `/stranded`

Quantifies capital at risk (blocking-item view based on ledger balances):

| Metric | Formula |
|--------|---------|
| **Stranded Units** | Closing balance of blocking items tied to blocked or partial-ready sets |
| **Stranded Value** | Sum(stranded_units * unit_cost) |
| **Blocking Context** | Which missing items are causing stranding |

**Example:**
> "You have $2.4M worth of blocking components sitting idle because a dependent domain is not ready."

---

### 5.6 Scenarios Page

**URL:** `/scenarios`

Compare "what-if" analyses:

| Template | Description |
|----------|-------------|
| **Baseline** | Current plan assumptions |
| **Favorable** | +20% power, +10% supply availability |
| **Stressed** | -20% power, -20% supply, +20% demand |

**Use This Page To:**
- Test resilience of the plan
- Quantify upside/downside risks
- Support investment decisions

---

### 5.7 Convergence Page
**URL:** `/convergence`

Domain-level breakdown with missing domains:

| Domain | Status |
|--------|--------|
| IT Rack | Ready |
| Callan | Ready |
| MOR | Blocked |
| Power | Blocked (if power gate active) |

**Use This Page To:**
- Pinpoint which domain is causing the delay
- Coordinate cross-functional resolution
- Track convergence over time

---

### 5.8 Segments Page
**URL:** `/segmentation`

Full MECE classification with overlay tags:

| Column | Description |
|--------|-------------|
| **Segment** | B1–B4 or N1–N4 |
| **Is Blocker** | Critical for build? |
| **Is Constrained** | Supply reliability issues? |
| **High E&O** | Financial risk if stranded? |
| **Overlay Tags** | transition_active, shared_component, etc. |

---

### 5.9 Buffers Page
**URL:** `/buffers`

Policy-based buffer targets (v1):

| Column | Description |
|--------|-------------|
| **Segment** | B1-B4 or N1-N4 bucket |
| **Items** | Count of items in the segment |
| **Min Weeks** | Minimum recommended coverage |
| **Max Weeks** | Maximum recommended coverage |
| **Location** | Where to position (integration, regional, site) |
| **Rationale** | Why the policy was selected |

Note: Tiered buffer targets are produced by BufferEngineV2 and used in CSV exports.

---

### 5.10 Engineering Insights Page

**URL:** `/engineering`

GPU generation and lifecycle management:

| Section | Content |
|---------|---------|
| **Active Generations** | H100, H200, B100, etc. |
| **Lifecycle Status** | Active, LTB (Last Time Buy), EOL |
| **Transition Windows** | Items in active transition |
| **Substitution Paths** | Which items can substitute for others |

---

### 5.11 Settings Page
**URL:** `/settings`

Configure analysis thresholds:

| Setting | Default | Description |
|---------|---------|-------------|
| High E&O Unit Cost | $5,000 | Threshold for "expensive" items |
| High E&O Days to Risk | 90 days | Threshold for "near obsolescence" |
| Constrained Lead Time | 45 days | Threshold for "long lead" |
| Constrained Confidence | 0.70 | Threshold for "unreliable supply" |
| Shared Usage Threshold | 2 | Min usages to be "shared component" |

Settings can be persisted to file for consistency across sessions.

---

### 5.12 Upload Page
**URL:** `/upload`

Import your data files:

**Supported Formats:**
- CSV
- Excel (.xlsx)
- Parquet

**Process:**
1. Select target table from dropdown
2. Drag and drop your file
3. System auto-maps columns using `configs/column_mapping.yml`
4. Review validation results
5. Data loads into `data/gold/`

---

### 5.13 Diagnostics Page
**URL:** `/diagnostics`

Data validation and troubleshooting:

| Check | Description |
|-------|-------------|
| **Contract Validation** | Are all required tables and columns present? |
| **Schema Consistency** | Do data types match expectations? |
| **Row Counts** | Record counts per table |
| **Missing Data** | Any critical fields with NULL values? |

---

## 6. Data Exports

### JSON Exports (API)

| Endpoint | Description |
|----------|-------------|
| `/export/weekly-status` | Weekly status report |
| `/export/leadership-update` | 4-week leadership summary |
| `/export/buffer-analysis` | Buffer targets with E&O impact |

### CSV Downloads

| Endpoint | Content |
|----------|---------|
| `/export/csv/buffers` | Item-level buffer targets |
| `/export/csv/blockers` | Blocker attribution |
| `/export/csv/stranded` | Stranded inventory |
| `/export/csv/convergence` | Convergence summary |
| `/export/csv/segments` | Item segmentation |
| `/export/csv/pegging` | Pegging results |

---

## 7. Uploading Your Data

### Required Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `deployment_plan` | What needs to be deployed | week, site_id, kit_id, kits_planned, demand_tier |
| `bom_kit` | Bill of materials | kit_id, child_item_id, qty_per, effective_start_week |
| `inventory_position` | Current inventory | as_of_date, item_id, node_id, on_hand, usable_on_hand |
| `supply` | Incoming supply | item_id, node_id, qty, status, promised_date/promise_date/promise_week (one required) |
| `site_readiness` | Site power/infra status | scenario_id, site_id, week |
| `item_master` | Item attributes | item_id, category, subcategory, value_density, shared_flag, build_ahead_flag |
| `node_master` | Network nodes for transfer model | node_id, site_id, node_type |
| `lane_master` | Transfer lanes between nodes | from_node_id, to_node_id, transfer_lead_time_days |

### Optional Tables

| Table | Purpose |
|-------|---------|
| `demand_plan` | Fallback if deployment_plan is missing |
| `square_set_master` | Explicit square set to kit mapping |
| `lifecycle` | GPU generation and LTB/EOL dates |
| `substitution_map` | Item substitution rules |
| `lead_time_history` | Historical lead times |
| `lead_time_distribution` | Lead time variability |

### Column Mapping


The system uses `configs/column_mapping.yml` to handle various column naming conventions. Canonical schema uses `kit_id` and `kits_planned`, but the mapper accepts `square_set_id` and `square_sets_planned` along with other aliases. For example:

```yaml
deployment_plan:
  aliases:
    square_sets_planned: ["quantity", "qty", "planned_qty"]
    square_set_id: ["deployment_id", "build_id", "kit_id"]
    site_id: ["location", "site", "dc_id"]
```

---

## 8. Configuration & Settings

### Configuration File

`configs/default_config.yml` controls:

```yaml
data:
  gold_path: "./data/gold"     # Location of parquet files

analysis:
  default_scenario: "baseline"  # Starting scenario
  horizon_weeks: 12             # Planning horizon

buffer_policy:
  lead_time_high_risk: 60       # Days threshold
  targets:
    GPU_blocking_high_risk: {min: 4, max: 6, location: "integration"}
    default: {min: 2, max: 3, location: "regional"}
```

### User Settings

Personal settings saved to `configs/user_settings.yml` (gitignored):
- Segmentation thresholds
- Buffer policy overrides
- UI preferences

---

## 9. Troubleshooting

### Common Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| "Data Contract Error" | Missing required tables | Check `data/gold/` for required parquet files |
| Empty readiness table | No valid square sets found | Verify `deployment_plan` has all three domains |
| "date_trunc" errors | Date columns not valid | Ensure week/date columns are proper DATE format |
| Join mismatches | Type inconsistencies | Check that `week` is DATE in all tables |

### Running Diagnostics

1. Navigate to `/diagnostics`
2. Review contract validation results
3. Check row counts and missing data reports
4. Click "View Details" for specific table issues

### Regenerating Data

If data is corrupted or you want fresh synthetic data:

```bash
python -m src.bufferlab_deploy.synthetic_data_generator
```

---

## 10. Glossary

| Term | Definition |
|------|------------|
| **Square Set** | Complete deployable unit combining IT Rack, Callan, and MOR domains |
| **Domain** | One of three equipment categories: IT Rack, Callan (Power/Cooling), MOR (Network) |
| **Convergence** | All domains ready simultaneously for a square set |
| **MECE** | Mutually Exclusive, Collectively Exhaustive – every item in exactly one segment |
| **Blocker** | Component that stops the build if missing |
| **E&O** | Excess & Obsolescence – financial risk of unsold/unused inventory |
| **Pegging** | Allocating supply to demand in priority order |
| **Netting** | Subtracting allocations from available supply |
| **Stranded Capital** | Inventory value stuck in incomplete builds |
| **LTB** | Last Time Buy – final opportunity to purchase before discontinuation |
| **EOL** | End of Life – item no longer available |
| **Demand Tier** | Confidence level: Committed > Likely > Exploratory |
| **Buffer Target** | Recommended inventory level (weeks of coverage) |
| **Value at Risk** | Dollar exposure if inventory becomes obsolete |

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────────────────┐
│                  BUFFERLAB QUICK REFERENCE                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  START: python app.py → http://127.0.0.1:5001              │
│                                                             │
│  DAILY WORKFLOW:                                            │
│  1. Check Overview for KPIs                                 │
│  2. Review Blockers for action items                        │
│  3. Validate Convergence by domain                          │
│  4. Export data for stakeholder updates                     │
│                                                             │
│  KEY METRICS:                                               │
│  • Completion Rate: Target > 85%                            │
│  • Stranded Capital: Target < 5% of deployed value          │
│  • Top Blockers: Focus on reducing top 5                    │
│                                                             │
│  SEGMENTS TO WATCH:                                         │
│  • B1: Critical – Blocking + Constrained + High E&O         │
│  • B2: High priority – Safe to buffer                       │
│                                                             │
│  EXPORTS:                                                   │
│  • /export/csv/blockers – For procurement action            │
│  • /export/csv/stranded – For finance reporting             │
│  • /export/leadership-update – For exec summary             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

*Last updated: January 2026*
*BufferLab v2.0 – Deployment & Square-Set Readiness*
