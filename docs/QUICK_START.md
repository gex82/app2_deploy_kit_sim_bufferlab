# BufferLab Quick Start

Get up and running in 5 minutes.

---

## 1. Install & Run

```bash
cd app2_deployment_kit_sim
python -m pip install -r requirements.txt
python app.py
```

Open: **http://127.0.0.1:5001**

---

## 2. Generate Sample Data (Optional)

```bash
python -m src.bufferlab_deploy.synthetic_data_generator
```

---

## 3. Data Schema Note

Canonical schema uses `kit_id` and `kits_planned` in `deployment_plan`. Uploads accept `square_set_id` and `square_sets_planned` (and other aliases) via `configs/column_mapping.yml`.

---

## 4. Key Pages

| Page | What It Shows | When to Use |
|------|---------------|-------------|
| **Overview** | KPIs & trends | Daily check-in |
| **Readiness** | Planned vs deployable | Identify shortfalls |
| **Blockers** | Top blocking items | Focus procurement |
| **Convergence** | Domain-level status | Cross-team coordination |
| **Stranded** | Capital at risk | Finance reporting |

---

## 5. Key Concepts

- **Square Set** = Complete deployable unit (IT + Power + Network)
- **Convergence** = All domains ready → deployable
- **Segments** = Risk classification (B1 = Critical, N4 = Low risk)
- **Tiers** = Committed > Likely > Exploratory

---

## 6. Common Tasks

| Task | How |
|------|-----|
| Upload data | `/upload` → drag & drop file |
| Export blockers | Go to `/export/csv/blockers` |
| Change scenario | Overview → dropdown → select |
| Adjust thresholds | `/settings` → modify → save |

---

## 7. Troubleshooting

| Issue | Solution |
|-------|----------|
| Empty pages | Check `/diagnostics` for data issues |
| Contract error | Verify parquet files in `data/gold/` |
| Old data showing | Click "Reload Data" button |

---

*See `docs/USER_GUIDE.md` for full documentation.*
