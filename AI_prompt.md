We are expanding the scope. here some notes where App2 falls short under the new scope:

Object‑level mismatch. The code and UI treat each “kit” as the planning unit. Microsoft does not use “kits”; Callans, IT racks and network/MOR modules ship separately and must converge at the DC. The planning object should be “square sets” or “buildable deployment sets” (all required domains for a deployment converged).

No demand tiers. App 2 does not differentiate between committed demand and upside tiers (likely vs exploratory) even though buffer/commitment posture should vary by tier.

Limited segmentation factors. Current segmentation only considers category, kit criticality, supply risk and lifecycle risk. It does not account for cost/value (E&O exposure), fungibility, constraint status, nor whether the item is part of a square‑set or a build‑ahead stock.

No overlay tags. There is no tagging for transition‑active parts (LTB/EOL), shared components across domains/programs, long‑lead foundation parts or “square‑set coupling/build‑ahead sensitivity.”

No engineering context. App 2 lacks any view of GPU generation changes, compatibility/fungibility rules or substitution logic (beyond a simple substitution_map join).

Heuristic buffers only. The buffer recommendation engine uses fixed ranges from the config and ignores E&O exposure or multi-tier demand confidence.

INSTRUCTIONS: 

To extend App 2 and cover the missing gaps, use the following enhancements, grouped by area. These can be translated into detailed user stories or developer tasks.

Data contract & inputs

Add a demand_tier field to the deployment_plan table (values: committed, likely, exploratory) to classify demand by confidence/power‑tier.

Ensure item_master includes unit_cost and value_density (value per cubic/weight unit) to compute E&O exposure.

Extend lifecycle data with rev and generation identifiers, compatibility flags, and transition windows (start/end of LTB/EOL).

Include a shared_flag or compute a cross‑program usage count (items used in multiple kits/categories).

Include a build_ahead_flag indicating if an item is commonly procured ahead of power readiness, or derive it from inventory ageing patterns.

Maintain or expand substitution_map to capture fungibility rules (major vs minor generation).

Planning object update

Treat each deployment as a “square set” comprised of required components from IT rack, Callan/HXU and MOR/network domains. Update kit_engine to support a square_set_engine that groups BOM explosion across domains and computes required components per square set.

Replace kit‑centric terminology in routes/templates with “square-set readiness” or “buildable deployments.”

For build‑ahead analysis, introduce a separate planning level that aggregates multiple square sets intended to be positioned ahead of readiness.

Segmentation & classification

Implement a segmentation engine that assigns each item to exactly one base segment (B1–B4, N1–N4) based on:

Blocker vs Non‑blocker (kit_criticality field from BOM).

Supply regime: compute supply_constrained boolean from allocation flags or low confidence/long lead times.

E&O exposure: derive high_eo vs not_high_eo using unit cost, value density and days‑to‑risk (e.g., cost > X and days_to_risk < Y → high E&O).

Attach optional overlay tags:

Transition_active when lifecycle shows current date within LTB/EOL window.

Shared_component when usage count > 1 across programs/categories.

Long_lead_foundation when lead-time p95 > threshold and days_to_risk is low (low obsolescence).

Build_ahead_sensitivity when historical inventory shows repeated stranding due to non‑convergence of domains.

Break_glass_exception for temporary override (manual input).

Expose segmentation results in the UI, with filters and counts per segment/tag.

Scenario & demand‑tier handling

Extend the scenario_engine to support tiered demand scenarios: for each scenario (baseline, upside) and each demand tier, compute separate plans and run the netting ledger.

Buffer posture logic should vary by tier: allow forward coverage for committed demand; require commitments/options (no physical build‑ahead) for likely or exploratory tiers.

Buffer target engine

Replace the v1 policy heuristic with a segmentation‑based buffer engine:

For each base segment and overlay tag combination, define buffer ranges (min/max weeks of coverage) and preferred inventory location (integration, regional, site).

Use value‑at‑risk (unit_cost × inventory ageing × days_to_risk) as an E&O penalty to shrink buffers for high‑E&O segments.

Incorporate demand tier: for committed demand, allow buffer up to max; for likely, cap at min; for exploratory, zero buffer (commitments only).

Make buffer targets data‑driven but overrideable via YAML config.

Engineering & compatibility insights

Build a small engineering summary page that uses lifecycle and substitution_map to show current GPU generations, upcoming transitions, and which parts are fungible vs non‑fungible.

Display minor vs major generation changes and highlight substitution paths to reduce stranded inventory (retrofit, re‑kit options).

Convergence / build‑ahead controls

Add a convergence dashboard that shows, by site/week, the number of planned square sets vs deployable square sets, and the “missing domains” that block readiness.

Implement a square-set convergence gate in the pegging engine: only allocate arrivals to a square set when the paired domains and power readiness are feasible; otherwise flag as potential stranding.

Provide a build‑ahead exception log with approver, justification and end date; display in the UI.

Governance & reporting

Include weekly status reports and 4‑week leadership update exports, aligned with SOW.

Add a data‑privacy section in the README/config noting that App 2 handles only non‑PII, uses local DuckDB, and respects retention policies.

These enhancements keep App 2 MECE by separating planning objects, policy segments, overlay tags, and scenarios. They align with the SOW and internal meeting guidance while explicitly removing any reference to service‑level spares (covered in App 3, but not needed here).
