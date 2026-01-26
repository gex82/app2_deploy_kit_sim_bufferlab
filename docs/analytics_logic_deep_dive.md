# BufferLab Analytics Logic Deep Dive

This document provides a comprehensive, technical explanation of the analytical logic, algorithms, and heuristics driving the BufferLab application. 
---

## 1. Unit Definition: The "Square Set" Logic

**Objective**: Define the atomic unit of deployment to prevent "partial builds."

### Logic & Heuristics
Unlike traditional planning that tracks individual SKUs (e.g., "Cable A," "Server B"), BufferLab aggregates components into a **Square Set**.
*   **Definition**: A Square Set is a logical container representing one deployable unit of compute capacity (default ~0.5 MW per kit, configurable).
*   **Composition**: It is the mandatory convergence of three distinct physical domains:
    1.  **IT Rack Domain**: The compute servers and racks.
    2.  **Callan/HXU Domain**: The specific cooling and power distribution units.
    3.  **MOR/Network Domain**: The network fabric and connectivity modules.
*   **Aggregation Algorithm**:
    1.  Load `deployment_plan` (or `demand_plan` as fallback) plus `square_set_master` (explicit mapping or auto-generated).
    2.  Map each `kit_id` to a `square_set_id` + domain via `square_set_master`.
    3.  For each (week, site_id, square_set_id), compute `square_sets_planned` / `deployable_sets` as the MIN across the domain-specific kit plans. Missing domains simply do not contribute to the join; there is no explicit "broken signal" flag.

---

## 2. Risk Intelligence: MECE Segmentation & Tagging

**Objective**: Classify every component into a distinct risk profile to drive differentiated inventory policies.

### A. Base Segmentation Algorithm (MECE)
The system assigns every `item_id` to exactly **one** of 8 base segments (**B1–B4, N1–N4**) using a boolean decision tree:

1.  **Is Blocker?** (Primary Dimension)
    *   *Logic*: `True` if `kit_criticality == 'blocking'` **or NULL** (defaults to blocking), else `False`.
    *   *Implication*: Blockers stop the production line; Non-blockers do not.

2.  **Is Constrained?** (Supply Dimension)
    *   *Logic*: `True` if (`allocation_flag == True`) OR (`avg(confidence_score/confidence_weight) < 0.70`) OR (`lead_time_p95 > 45 days`).
    *   *Implication*: Constrained items rely on scarce supply; unconstrained items are readily valid.

3.  **Is High E&O?** (Value Dimension)
    *   *Logic*: `True` if (`unit_cost > $5,000`) AND (`days_to_risk < 90 days`).
    *   *Implication*: High E&O items have high "Value at Risk" if stranded; others have low financial impact.

**The Matrix:**
*   **B1**: Blocker + Constrained + High E&O (The "Critical Few")
*   **B2**: Blocker + Constrained + Low E&O
*   ...
*   **N4**: Non-blocker + Unconstrained + Low E&O (The "Trivial Many")

### B. Overlay Tagging Heuristics
After base segmentation, items are tagged with non-exclusive attributes:
*   `transition_active`: `True` if current date is within the item's `transition_start_date` to `transition_end_date` window.
    *   *Analytical Use*: Triggers buffer reduction to prevent obsolescence.
*   `shared_component`: `True` if item appears in >=2 distinct kit types **or** `item_master.shared_flag == True`.
    *   *Analytical Use*: qualifies item for inventory pooling aggregation.
*   `long_lead_foundation`: `True` if (`lead_time > 60d`) AND (`days_to_risk > 180d`).
    *   *Analytical Use*: Safe candidate for "Build Ahead" (long lead, low obsolescence risk).
*   `build_ahead_sensitivity`: `True` if historical stranding ratio > 30% or `item_master.build_ahead_flag == True`.
    *   *Analytical Use*: Discourages building ahead on items with high stranding risk.
*   `break_glass_exception`: `True` if `item_master.break_glass_exception == True`.
    *   *Analytical Use*: Manual override for exceptional cases.

---

## 3. Dynamic Inventory Strategy: Buffer Engine v2

**Objective**: Calculate the optimal "Target Stock Level" (TSL) for each item, balancing service level vs. financial risk.

### Calculation Logic
$$ \text{Target Inventory (Qty)} = \text{Avg Weekly Demand} \times \text{Target Weeks Coverage} $$

The **Target Weeks Coverage** is dynamic, calculated as follows:

1.  **Base Range Selection**: Look up (Min, Max) weeks based on Segment.
    *   *Example*: Segment **B1** (High Risk) → 2–3 weeks. Segment **N4** (Low Risk) → 1–2 weeks.

2.  **Tiered Demand Adjustment** (Confidence Factor):
    *   **Committed Demand**: Use **Max** of range (Prioritize Service Level).
    *   **Likely Demand**: Use **Min** of range (Prioritize Cash Preservation).
    *   **Exploratory Demand**: Set Target to **0** (Prioritize Risk Avoidance - do not build to stock).

3.  **Risk Penalties (E&O Factor)**:
    *   If `transition_active` is True: Reduce Target Weeks by **33%** (Hardcoded Policy).
    *   If Segment is High E&O (**B1/B3/N1/N3**): Reduce Target Weeks by further **25%**.
    *   *Heuristic*: "Better to stock out of an obsolete part than to own it forever."

---

## 4. Simulation: Allocation & Readiness Engine

**Objective**: Simulate whether the current supply plan is sufficient to meet the demand plan, respecting constraints.

### A. Pegging Algorithm (The "Waterfalls")
The simulation uses a strict priority waterfall to allocate supply ("Pegging"):

1.  **Tier 1 Allocation**:
    *   Demand = `Committed` only.
    *   Supply = Total Available On-Hand + Inbound.
    *   *Result*: `Shortage_Committed`.

2.  **Tier 2 Allocation**:
    *   Demand = `Likely`.
    *   Supply = Remaining Supply (Total - Allocated_to_Tier1).
    *   *Result*: `Shortage_Likely`.

3.  **Tier 3 Allocation**:
    *   Demand = `Exploratory`.
    *   Supply = Remaining Supply (Total - Allocated_to_Tier1 - Allocated_to_Tier2).
    *   *Result*: `Shortage_Exploratory`.

### B. Convergence Gating Logic
For a Square Set Deployment at (Site S, Week W) to be marked "Deployable":
1.  **Check Components**: For each domain, compare total required (all BOM items) vs available on-hand at the site.
2.  **Domain Ready**: A domain is ready if `total_available >= total_required`.
3.  **Power Gate (Optional)**: If `site_readiness.power_ready_mw` exists, require `power_ready_mw >= power_mw_required`.
4.  **Convergence Gate**:
    $$ \text{Is Ready} = (\text{IT Ready}) \land (\text{Callan Ready}) \land (\text{MOR Ready}) $$
    If `power_ready_mw` is provided, it is treated as an additional AND gate.

**Crucial Logic**: If a domain is not ready, the square set is not deployable and its blocking items can become stranded in the ledger. It does *not* count toward the Completion Rate.

---

## 5. Final Output Metrics

**Objective**: Quantify the results of the simulation for executive decision-making.

### A. Completion Rate (%)
$$ \frac{\text{Sum(Fully Converged Square Sets)}}{\text{Sum(Total Planned Square Sets)}} \times 100 $$
*   *Note*: Partial builds count as 0.

### B. Stranded Capital ($)
Sum of stranded units (from the netting ledger) for **blocking items** tied to blocked or partial-ready square sets.
$$ \sum (\text{Closing Balance} \times \text{Unit Cost}) \text{ for blocking items in blocked/partial-ready sets} $$
*   *Insight*: This is a conservative, blocking-item view of stranded value.

### C. Top Blockers
Ranked list of `item_id`s sorted by total gap quantity (and gap value), with counts of square sets affected.
*   Identifies the "long poles" in the tent preventing deployment.
