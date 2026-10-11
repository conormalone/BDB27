# BDB27 — NFL Big Data Bowl 2027

**Question:** *Does the Combine ceiling bind on max-effort routes?* For receivers who **reached the NFL**,
test whether each player's physical **ceiling** (estimated from 10 Hz Combine tracking) predicts the
**upper tail** of their in-game speed/acceleration on **max-effort routes**, and **how close they get to it**.
**WR primary; TE replication.**

Three questions: (1) is the Combine ceiling a **real bound** in games (exceedance rate)? (2) does the
Combine→game relationship **strengthen toward the upper tail**? (3) **which Combine features** predict
closeness on **deep vs. breaking** routes?

**Claim limits (hard).** Claims apply only to players who reached the NFL. We **never** claim Combine
athleticism predicts NFL success — that angle is saturated (combine tests explain ~2.6% of year-1 game
performance; Cook et al. 2020). This is a **mechanism / measurement** paper.

**Competition:** NFL Big Data Bowl 2027, theme **"Combine to NFL Performance"** (first combine-themed
edition). Final deadline **6 Jan 2027**; judged Football 30 / Data Science 30 / Writeup 20 / Viz 20, with
pass/fail gates (an explicit Combine→NFL linkage; a public Kaggle notebook + writeup). Limits: ≤2,000 words,
≤5 figures (stricter than the official "<10"). See `notes/rules.md`.

> **Binding spec = `TASK.md`.** The prior combine-fatigue task is archived at git tag
> `archive/combine-fatigue-v1` (HEAD `fdc794c`). `research/prior-work-scan.md` is the prior-art scan and
> remains valid for context.

## Deliverable model: scripts engine + Kaggle notebook

`src/` is the **development engine** — small, tested, config-driven stage scripts (`01_audit`,
`02_combine_envelope`, `00_thresholds`, `03_game_demand`, `04_models`, `05_validate`). The **competition
deliverable** is a **public Kaggle notebook + writeup** (≤5 figures) that runs the same logic end-to-end on
a Kaggle kernel and renders the results. Keep heavy logic in `src/`; the notebook imports/calls it.

## Data (2027 BDB, Kaggle)

Raw data lives on the mounted drive (`data/raw` → `/mnt/project_data/conor_downloads/bdb27/`) — never
committed here.

- `combine_tracking.csv` — **10 Hz** optical tracking of combine drill attempts (~6,310 attempts). The key
  Combine asset (`event_id`, `attempt`, ISO-8601 `time`, `drill_type`, `x/y/s/a/dis/dir`).
- `combine_results.csv` — anthropometrics + electronic splits (10-yd split, 40, vertical, broad, 3-cone,
  shuttle, bench reps) + NGS scores.
- `player_play.csv` — in-game per-play context (`route_ran`, `pass_length`, `target`,
  `separation_at_pass_forward`, coverage fields, …).
- `game_tracking_{2023,24,25}.csv` — frame-level in-game tracking (**absent on the Pi**; full-run only).
  A non-representative 2023 **sample** is present for plumbing only.

See `notes/schema.md` for full schemas/sizes.

## Run order

```
01_audit → 02_combine_envelope → 00_thresholds → 03_game_demand → 04_models → 05_validate
```

Every stage takes `--mode sample|full` and `--force`, with **one code path** for both. All paths/thresholds
come from `config.yaml` (no magic numbers). Heavy files are converted to Parquet once (checkpointed) and
queried with DuckDB; **no full CSV is loaded into pandas**. Sample outputs are labelled
`UNVALIDATED SAMPLE OUTPUT`. **Not yet implemented** — this cycle is Phase 0 (setup) only.

## Layout

| Dir | Purpose |
|---|---|
| `research/` | Prior-work scan, literature notes, method surveys |
| `src/` | Stage scripts + reusable code (imported by notebooks) |
| `notebooks/` | The public Kaggle notebook deliverable (+ local-run variant) |
| `notes/` | Rules, schema, pre-registration, assumptions, limitations, decisions, blockers |
| `outputs/` | Generated figures, tables, metrics (gitignored) |
| `data/` | Pointer to the raw data on the mounted drive (gitignored) |

## Status (Phase 0)

- [x] Prior-work scan — `research/prior-work-scan.md`
- [x] **Cycle 0: pivot to the ceiling-binding task — repo restructured, analysis reset, infra kept**
- [x] Phase 0 setup — `notes/rules.md`, `notes/schema.md`, `notes/prereg.md` (skeleton), `config.yaml`
- [ ] Phase 1 audit (a–i) — go/no-go gate (matched WR, route snaps, missing fields, ICC)
- [ ] Phases 2–5

## Local run (Windows / laptop)

Runs on a laptop or desktop as well as the Pi: create a venv, `pip install -r requirements.txt`, point
`data/raw` at a folder holding the BDB CSVs, then run the stage scripts (or the notebook). Full Windows /
laptop instructions are in **`README_RUN.md`**.
