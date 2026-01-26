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
    SquareSet_Status = (IT_Rack_Ready AND Callan_Ready AND MOR_Ready AND Power_Ready)
    ```
    *   **The Hardware Gate**: A square set is only "hardware ready" if all three domains (IT, Callan, MOR) have zero shortages for blocking components.
    *   **The Power Gate**: Even if all hardware is on-site, the set is blocked if the `site_readiness` table shows insufficient `power_ready_mw` (Megawatts) to support the power requirement defined in `square_set_master`.

---

## 2. The Deployment Plan & Explosion

### The Business Narrative
We start with a high-level plan: "Deploy 50 Square Sets in Phoenix in Week 12."
The system "explodes" this plan, breaking it down into a list of required hardware (BOMs) AND a list of required infrastructure (Power/MW) from the **Site Readiness** schedule.

### Under the Hood (Algorithm)
*   **Input**: `deployment_plan` table (Hardware needs) and `site_readiness` table (Infrastructure availability).
*   **Process**:
    1.  Join Plan with `bom_kit` to get hardware needs.
    2.  Check `site_readiness` for `power_ready_mw`.
    3.  Key Output: A total view of both "What we need to build" and "Does the site have the power to turn it on?"

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
    2.  **Constrained?** `allocation_flag` is truthy OR `avg(confidence_score) < 70%` OR `lead_time_p95 > 45 days`.
    3.  **High E&O?** `unit_cost > $5k` (or high relative cost) AND `days_to_risk < 90`.
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

## 4.5 Fungibility: The "Swap" Logic

### The Business Narrative
If you run out of 2-meter power cables, but you have thousands of 3-meter cables that work just as well, do you really have a shortage? In the real world, no. 
BufferLab identifies these "Fungible" items (parts that can be substituted for one another) to prevent unnecessary panic and over-ordering.

### Under the Hood (Algorithm)
*   **Modeling**: `SegmentationEngine` uses a `substitution_map` and "Compatibility Groups" to tag items as `minor_gen` (easy swap) or `major_gen` (harder swap).
*   **Strategic Buffer Reduction**: If an item is easily substituted (`minor_gen`), the `BufferEngineV2` **automatically reduces its safety stock target by 15%**. 
    *   *Logic*: Why store extra "Insurance" for Item A if Item B can cover its shifts?
*   **Current Limit**: While the system *calculates* the risk based on fungibility, the current `PeggingEngine` requires a manual decision to perform the swap in the physical warehouse (it does not yet "auto-allocate" an alternative part).

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
    2.  **Convergence Gate**: Before allocating *anything*, check if the Square Set is even buildable based on hardware readiness AND power readiness. If any domain or power is missing, don't waste *other* scarce parts on a "dead" set.
    3.  **Allocate**: Iterate through sorted demand. Deduct from ledger.
    4.  **Result**: `Buildable_Sets` vs `Blocked_Sets`.

---

## 6. Diagnosis: Blockers & Stranded Capital

### The Business Narrative
Finally, the system tells us the bad news.
*   **Blockers**: "You are missing 5 Switches. This is blocking 20 Square Sets."
*   **Stranded Capital**: "Because those switches are missing, the other components tied to those sets (like servers) remain **stranded** (paid for but unusable)."

This is the "Money Shot." It tells executives exactly where to focus to unlock value.

### Under the Hood (Algorithm)
*   **Stranded Calculation**:
    ```python
    Stranded_Value = Sum(Closing_Balance * Unit_Cost) for items tied to blocked sets
    ```
    *Logic*: Uses netting-ledger closing balances for items assigned to square sets that failed the Convergence Gate.
*   **Root Cause**: Identifies the specific `item_id` that drove the Square Set status to `False`.

---

## Summary Flowchart

1.  **Load Data**: Hardware Plans (Deployment) + Infrastructure Availability (Site Readiness).
2.  **Explode**: Create item-level shopping list + MW power targets.
3.  **Segment**: Tag every item with importance and risk (B1-N4).
4.  **Buffer**: Calculate safety stock targets based on segment and demand tier.
5.  **Allocated (Pegging)**: Distribute inventory to highest priority first.
6.  **Converge**: Check if IT + Power + Network hardware AND site power (MW) are all present.
7.  **Report**: Show what's ready, what's blocked, and the cost of the delay (Stranded Capital).
