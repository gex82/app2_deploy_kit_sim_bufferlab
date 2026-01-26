# BufferLab Analytics Workflow & Logic Explanation

## Executive Summary

**BufferLab (App 2)** transforms raw supply chain data into decision-grade insights for GPU deployment readiness. Unlike traditional reporting tools that simply sum up inventory, BufferLab uses a simulation approach: it models the "Square Set" (the converged unit of IT, Power, and Network), applies intelligent risk segmentation to every component, and simulates allocation based on demand confidence tiers.

This document outlines the end-to-end data and analytics workflow, demonstrating how the application turns static data into actionable deployment signals.

---

## Analytics Workflow Diagram

The following sequence diagram illustrates the "Lifecycle of a Decision" within BufferLab, from data ingestion to the final readiness signal.

```mermaid
sequenceDiagram
    autonumber
    
    box "User & Interface" #1e1e24
        participant User as Business User
        participant UI as BufferLab Dashboard
    end
    
    box "Data Foundation" #2a2a35
        participant DataLake as Gold Data Lake
        participant Contract as Data Contract
    end
    
    box "Transformation Engine" #22c55e
        participant SqEngine as Square-Set Engine
        participant SegEngine as Segmentation Engine
    end
    
    box "Analytics Core" #a100ff
        participant BufferEng as Buffer Engine v2
        participant ScenarioEng as Scenario Engine
        participant Pegging as Pegging/Netting
    end

    %% Step 1: Ingestion
    Note over User, Contract: 1. System Initialization & Data Load
    User->>UI: Launch Application
    UI->>DataLake: Request "Gold" Datasets
    DataLake-->>Contract: Stream Tables (Supply, Demand, BOMs, etc.)
    Contract->>Contract: Validate Schema & Rules
    Contract-->>UI: Data Verified (or Alerts Raised)

    %% Step 2: Transformation
    Note over SqEngine, SegEngine: 2. Intelligent Transformation
    UI->>SqEngine: Generate "Square Sets"
    SqEngine->>SqEngine: Converge Domains (IT + Power + Network)
    Note right of SqEngine: Transforms disparate BOMs<br/>into single "Buildable Units"
    
    UI->>SegEngine: Classify Item Portfolio
    SegEngine->>SegEngine: Compute MECE Segments (B1-B4, N1-N4)
    SegEngine->>SegEngine: Apply Overlay Tags (Transition, Shared, Build-Ahead)
    Note right of SegEngine: Risk-profiles every single part

    %% Step 3: Strategy
    Note over BufferEng, ScenarioEng: 3. Strategy Application
    UI->>BufferEng: Calculate Buffer Targets
    BufferEng->>BufferEng: Apply Policy vs. Segment Risk
    BufferEng->>BufferEng: Deduct E&O Penalties
    Note right of BufferEng: "Target weeks of stock"<br/>adjusted for risk & value

    %% Step 4: Simulation
    Note over ScenarioEng, Pegging: 4. Verification Simulation
    User->>UI: Select Scenario (e.g., baseline vs stressed)
    UI->>ScenarioEng: Run Simulation
    ScenarioEng->>Pegging: Run Tiered Netting
    Pegging->>Pegging: Commit Supply to Committed Demand first
    Pegging->>Pegging: Allocate Remaining to Likely/Exploratory
    
    ScenarioEng->>SqEngine: Check Convergence
    SqEngine->>SqEngine: Can we build the full Square Set?
    Note right of SqEngine: Gating Logic:<br/>No build unless ALL domains ready

    %% Step 5: Insights
    Note over User, UI: 5. Decision & Reporting
    ScenarioEng-->>UI: Simulation Results
    UI-->>User: Visual Insights
    Note right of User: • Completion Rate %<br/>• Top Blockers<br/>• Stranded Capital<br/>• E&O Risks
    
    User->>UI: Export Leadership Update
    UI-->>User: Download JSON report
```

---

## Detailed Logic Explanation

### 1. The Planning Object: "Square Sets"
Traditional planning tracks individual components (cables, servers, shelving). BufferLab elevates this to the **Square Set**—the minimum deployable unit of compute capacity.
*   **The Logic**: A "Square Set" is only considered feasible if its **IT Rack** (Compute), **Callan/HXU** (Cooling/Power), and **MOR/Network** (Connectivity) are *all* present.
*   **Business Value**: Prevents "partial builds" where you have expensive servers sitting idle because a low-cost network cable is missing.

### 2. The Intelligence: MECE Segmentation & Tagging
Instead of treating all parts equally, the **Segmentation Engine** classifies every item into distinct risk buckets (e.g., **B1** to **N4**) using a Mutually Exclusive, Collectively Exhaustive (MECE) framework.
*   **Base Segments**: Determined by three factors:
    1.  **Criticality**: Is it a Blocker? (Stops the build)
    2.  **Constraint**: Is supply scarce or unreliable?
    3.  **E&O Risk**: Is it high-cost and nearing end-of-life?
*   **Overlay Tags**: Nuanced flags like `transition_active` (item is being replaced by a new generation) or `long_lead_foundation` (takes months to arrive).
*   **Business Value**: Focuses human attention on the "Critical Few" rather than the "Trivial Many."

### 3. The Strategy: Dynamic Buffer Targets
The **Buffer Engine v2** moves away from static "one size fits all" safety stock.
*   **Tiered Posture**:
    *   **Committed Demand**: System authorizes full buffer coverage to guarantee delivery.
    *   **Likely/Exploratory**: System strictures inventory, capping it at minimum levels or zero (commitments only, no stock).
*   **E&O Penalty**: If an item is flagged as High Risk (High Value + Aging), the engine *automatically reduces* its buffer target.
*   **Business Value**: Maximizes readiness for committed plans while aggressively minimizing cash tied up in risky or speculative inventory.

### 4. The Simulation: Tiered Netting & Convergence
The **Scenario Engine** doesn't just subtract Demand from Supply; it runs a prioritized simulation.
*   **Tiered Netting**: It "fills" orders for Committed demand first. Only if excess supply exists does it allocate to Likely or Exploratory demand.
*   **Convergence Gate**: Even if you have the servers, the system will not mark a deployment as "Ready" (Green) unless all domains are ready based on on-hand availability (and optional power readiness).
*   **Business Value**: Provides a realistic, "clear to build" signal that accounts for real-world constraints, preventing false optimism.

### 5. Governance & Reporting
The final layer converts these complex calculations into simple business metrics.
*   **Completion Rate**: What % of our committed plan can we actually turn on?
*   **Stranded Capital**: What is the dollar value of inventory sitting idle due to missing cheap parts (blockers)?
*   **Top Blockers**: Which specific items are holding up the most square sets (largest gap quantities)?

This workflow ensures that every number on the dashboard is backed by rigorous, constraint-aware logic, making BufferLab a true **Decision Support System** rather than just a reporting tool.
