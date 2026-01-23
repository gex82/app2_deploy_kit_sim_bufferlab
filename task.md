# BufferLab - Deployment & Kit Readiness (App 2) - Scope Expansion

## Status: All Phases Complete ✓

### Phase A: Data Contract & Square-Set Generator
- [x] Extend data_contract.py for new fields
- [x] Create square_set_engine.py with synthetic data generation
- [x] Fixed SQL for missing deployment_plan handling

### Phase I: Settings & Configuration Page
- [x] Create settings route and template
- [x] Add all segmentation threshold inputs (12 fields)
- [x] Add buffer policy settings  
- [x] Implement session persistence
- [x] Add API endpoints for save/reset/export

### Phase B: Square-Set Engine
- [x] Create square_set_engine.py
- [x] Implement domain aggregation (IT Rack, Callan, MOR)
- [x] Add get_domain_readiness(), get_convergence_summary()
- [x] Fixed table availability checks and error handling

### Phase C: Segmentation Engine
- [x] Create segmentation_engine.py
- [x] Implement B1-B4, N1-N4 assignment
- [x] Add overlay tag computation (5 tags)
- [x] Create segmentation UI page (/segmentation)
- [x] Fixed SQL type conversion for allocation_flag

### Phase D: Demand Tier Handling
- [x] Update scenario_engine for tiered demand (committed/likely/exploratory)
- [x] Separate netting ledger runs per tier
- [x] Add get_tiered_summary() and run_tiered_netting_ledger()

### Phase E: Buffer Engine v2
- [x] Create buffer_engine_v2.py
- [x] Segment-based buffer ranges (B1-B4, N1-N4)
- [x] E&O penalty calculation
- [x] Location-based buffer targets (integration/regional/site)

### Phase F: Convergence Dashboard
- [x] Create /convergence route
- [x] Domain readiness visualization (table & chart)
- [x] Fixed template variable shadowing issue

### Phase G: Engineering Insights
- [x] Create /engineering route
- [x] GPU generation timeline view
- [x] Substitution paths view

### Phase H: Governance & Reporting
- [x] Weekly status report export (/export/weekly-status)
- [x] 4-week leadership update (/export/leadership-update)
- [x] Buffer analysis export (/export/buffer-analysis)

## Verification Status ✓
All routes verified working (HTTP 200):
- Home, Convergence, Settings, Segmentation, Engineering
- Export routes: weekly-status, leadership-update, buffer-analysis
