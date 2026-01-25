# BufferLab Analytics Logic Deep Dive

This document provides a comprehensive, technical explanation of the analytical logic, algorithms, and heuristics driving the BufferLab application. 
---

## 1. Unit Definition: The "Square Set" Logic

**Objective**: Define the atomic unit of deployment to prevent "partial builds."

### Logic & Heuristics
Unlike traditional planning that tracks individual SKUs (e.g., "Cable A," "Server B"), BufferLab aggregates components into a **Square Set**.
*   **Definition**: A Square Set is a logical container representing one deployable unit of compute capacity (typically 0.5 - 1.2 MW).
*   **Composition**: It is the mandatory convergence of three distinct physical domains:
    1.  **IT Rack Domain**: The compute servers and racks.
    2.  **Callan/HXU Domain**: The specific cooling and power distribution units.
    3.  **MOR/Network Domain**: The network fabric and connectivity modules.
*   **Aggregation Algorithm**:
    1.  Ingest `deployment_plan` (contains Site, Week, and Quantities for each Domain Kit).
    2.  Perform an "Inner Join" logic across the three domains for each (Site, Week) tuple.
    3.  If a Site/Week has a demand for IT Racks but zero demand for Power/Network, it is flagged as a "Broken Signal" but excluded from the valid Square Set count.

---

## 2. Risk Intelligence: MECE Segmentation & Tagging

**Objective**: Classify every component into a distinct risk profile to drive differentiated inventory policies.

### A. Base Segmentation Algorithm (MECE)
The system assigns every `item_id` to exactly **one** of 8 base segments (**B1–B4, N1–N4**) using a boolean decision tree:

1.  **Is Blocker?** (Primary Dimension)
    *   *Logic*: `True` if `kit_criticality == 'blocking'`, else `False`.
    *   *Implication*: Blockers stop the production line; Non-blockers do not.

2.  **Is Constrained?** (Supply Dimension)
    *   *Logic*: `True` if (`allocation_flag == True`) OR (`confidence_score < 0.70`) OR (`lead_time_p95 > 45 days`).
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
*   `transition_active`: `True` if current date is within the item's [LTB Start, EOL Date] window.
    *   *Analytical Use*: Triggers buffer reduction to prevent obsolescence.
*   `shared_component`: `True` if item appears in >1 distinct Kit Types.
    *   *Analytical Use*: qualifies item for inventory pooling aggregation.
*   `long_lead_foundation`: `True` if (`lead_time > 60d`) AND (`days_to_risk > 180d`).
    *   *Analytical Use*: Safe candidate for "Build Ahead" (long lead, low obsolescence risk).

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
1.  **Check Components**: Are 100% of required *Blocker* components available for the IT Rack Kit?
2.  **Check Components**: Are 100% of required *Blocker* components available for the Callan Kit?
3.  **Check Components**: Are 100% of required *Blocker* components available for the MOR Kit?
4.  **Convergence Gate**:
    $$ \text{Is Ready} = (\text{IT Ready}) \land (\text{Callan Ready}) \land (\text{MOR Ready}) $$

**Crucial Logic**: If IT Rack is ready but Power is missing, the IT Rack allocation is treated as "Stranded" (it physically exists but cannot turn on). It does *not* count toward the Completion Rate.

---

## 5. Final Output Metrics

**Objective**: Quantify the results of the simulation for executive decision-making.

### A. Completion Rate (%)
$$ \frac{\text{Sum(Fully Converged Square Sets)}}{\text{Sum(Total Planned Square Sets)}} \times 100 $$
*   *Note*: Partial builds count as 0.

### B. Stranded Capital ($)
Sum of the unit cost of all components allocated to Square Sets that **failed** the Convergence Gate.
$$ \sum (\text{Qty Allocated} \times \text{Unit Cost}) \text{ where } \text{SquareSet.IsReady} == \text{False} $$
*   *Insight*: This represents effective capital waste.

### C. Top Blockers
Ranked list of `item_id`s sorted by:
$$ \text{Gap Impact} = \text{Sum(Missing Qty)} \times \text{Count(Impacted Square Sets)} $$
*   Identifies the "long poles" in the tent preventing deployment.
