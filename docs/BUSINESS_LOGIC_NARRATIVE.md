# BufferLab Logic & Narrative Guide

> **Purpose**: This document explains "how it works" in plain English for business users, with an accompanying "Under the Hood" section for technical detail.

---

## 1. The Core Concept: "The Square Set"

### The Business Narrative
Imagine you are building a Lego castle. To build one "unit" of the castle, you need three specific bags of parts: the **Walls** (IT Rack), the **Roof** (Power/Cooling), and the **Drawbridge** (Network).
If you have 100 bags of Walls and 100 bags of Roofs, but *zero* Drawbridges, how many castles can you build? **Zero.**
Everything else you bought is currently useless until that Drawbridge arrives.

BufferLab shifts the focus from counting individual bricks (items) to counting complete Castles (**Square Sets**).

### Under the Hood (Algorithm)
*   **Logic**: The `SquareSetEngine` groups individual "Kits" (BOMs) into a single deployable unit called a `SquareSet`.
*   **Mapping**: It assumes 1 Square Set = 1 IT Rack Kit + 1 Callan (Power) Kit + 1 MOR (Network) Kit.
*   **Convergence Check**:
    ```python
    SquareSet_Status = (IT_Rack_Ready AND Callan_Ready AND MOR_Ready)
    ```
    If *any* domain is not ready based on on-hand availability, the entire Square Set is marked **"Not Ready"**. If `power_ready_mw` is provided, it adds an additional power gate.

---

## 2. The Deployment Plan & Explosion

### The Business Narrative
We start with a high-level plan: "Deploy 50 Square Sets in Phoenix in Week 12."
The system "explodes" this plan, meaning it breaks down that high-level goal into a shopping list of thousands of cables, servers, and switches required to make it happen.

### Under the Hood (Algorithm)
*   **Input**: `deployment_plan` table (Site, Week, Kit ID, Quantity).
*   **Process**:
    1.  Join Plan with `bom_kit` (Bill of Materials).
    2.  Multiply `Planned_Qty` * `Qty_Per_Kit` for every component.
    3.  Key Output: A massive list of `Required_Qty` for every `Item_ID` by `Site` and `Week`.

---

## 3. Segmentation (The "Risk Score")

### The Business Narrative
Not all parts are equal. A generic screw is easy to replace; a custom high-voltage transformer is not.
BufferLab tags every single part with a "Risk Profile" so we know how to treat it.
*   **Is it a Blocker?** If missing, does it stop the build?
*   **Is it Constrained?** Is it hard to get more?
*   **Is it High Risk?** Is it expensive and likely to become obsolete soon?

We assign each part a score from **B1 (Critical)** to **N4 (Low Priority)**.

### Under the Hood (Algorithm)
*   **Engine**: `SegmentationEngine`
*   **Classification Logic (MECE)**:
    1.  **Blocker?** `kit_criticality == 'blocking'` (NULL defaults to blocking).
    2.  **Constrained?** `allocation_flag` is truthy OR avg(confidence_score/confidence_weight) < 70% OR `lead_time_p95 > 45 days`.
    3.  **High E&O?** `unit_cost > $5k` (or high relative cost when enabled) AND `days_to_risk < 90`.
*   **Result**: 8 mutually exclusive segments (B1-B4, N1-N4).

---

## 4. Buffer Sizing (How much safety stock?)

### The Business Narrative
How much extra inventory should we keep "just in case"?
*   For **Critical (B1)** items that are hard to get: We need a **thick safety buffer** (e.g., 4 weeks).
*   For **Expensive/Obsolete (High E&O)** items: We want a **thin buffer** to avoid wasting money.
*   For **Sketchy Demand**: If the demand is just a "guess" (Exploratory), we buy **zero** buffer.

### Under the Hood (Algorithm)
*   **Engine**: `BufferEngineV2`
*   **Formula**:
    ```python
    Target_Buffer = (Weekly_Demand) * (Target_Weeks)
    ```
*   **Target Weeks Logic**:
    1.  **Base**: Determined by Segment (e.g., B1 = 3 weeks, N4 = 1 week).
    2.  **Tier Adjustment**:
        *   `Committed` Demand → Use Max Buffer.
        *   `Likely` Demand → Use Min Buffer.
        *   `Exploratory` Demand → 0 Buffer.
    3.  **Penalties (Reductions)**:
        *   If `Transition_Active` (old gen being replaced) → Reduce by ~33%.
        *   If `High_EO` (risk of waste) → Reduce by 25%.

---

## 5. Pegging (Who gets the parts?)

### The Business Narrative
Imagine we have 100 power cables.
*   Project A (Contract Signed) needs 80.
*   Project B (Likely to happen) needs 40.

We don't just split them 50/50.
BufferLab follows a strict **Ethical Priority (Pegging)**:
1.  **Feed the "Committed" projects first.** Project A gets all 80 cables.
2.  **Feed "Likely" projects with leftovers.** Project B gets the remaining 20 (and is 20 short).
3.  **"Exploratory" projects get nothing** until everyone else is full.

**Crucially, this happens over time.** If Project A needs them in Week 1 and Project B in Week 2, Project B might get lucky if a new shipment arrives in Week 2.

### Under the Hood (Algorithm)
*   **Engine**: `PeggingEngine` & `NettingLedger`
*   **Time-Phased Ledger**: Inventory "carries forward" week-over-week.
    ```python
    Available[Week_X] = Available[Week_X-1] + Arrivals[Week_X] - Consumed[Week_X]
    ```
*   **Algorithm**: Greedy Allocation with Convergence Gating.
    1.  **Sort Demand**: By Tier (Committed > Likely > Exploratory) then by Priority Score.
    2.  **Convergence Gate**: Before allocating *anything*, check if the Square Set is even buildable based on on-hand readiness (and optional power readiness). If any domain is not ready, don't waste *other* scarce parts on a "dead" set.
    3.  **Allocate**: Iterate through sorted demand. Deduct from ledger.
    4.  **Result**: `Buildable_Sets` vs `Blocked_Sets`.

---

## 6. Diagnosis: Blockers & Stranded Capital

### The Business Narrative
Finally, the system tells us the bad news.
*   **Blockers**: "You are missing 5 Switches. This is blocking 20 Square Sets."
*   **Stranded Capital**: "Because those switches are missing, the blocking items tied to those sets remain stranded (capital at risk)."

This is the "Money Shot." It tells executives exactly where to focus to unlock value.

### Under the Hood (Algorithm)
*   **Stranded Calculation**:
    ```python
    Stranded_Value = Sum(Closing_Balance * Unit_Cost) for blocking items in blocked/partial-ready sets
    ```
    *Logic*: Uses netting-ledger closing balances and focuses on blocking items tied to blocked or partial-ready square sets.
*   **Root Cause**: Identifies the specific `item_id` that drove the Square Set status to `False`.

---

## Summary Flowchart

1.  **Load Data** (Plans, BOMs, Inventory)
2.  **Explode** → Create item-level shopping list.
3.  **Segment** → Tag every item with importance (B1-N4).
4.  **Buffer** → Calculate safety stock targets based on risk.
5.  **Allocated (Pegging)** → Distribute inventory to highest priority first.
6.  **Converge** → Check if IT + Power + Network are all present.
7.  **Report** → Show what's ready, what's blocked, and the cost of the delay.
