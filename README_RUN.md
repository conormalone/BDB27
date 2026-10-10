# README_RUN — how to run the BDB27 pipeline

Stage scripts live in `src/` and take `--mode sample|full` and `--force`. All paths/thresholds come from
`config.yaml`. Heavy files are converted to Parquet once (checkpointed) and queried with DuckDB; no full
CSV is loaded into pandas.

## 1. Environment (ARM64 Pi / x86 Linux)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt          # pinned; verified on ARM64, Python 3.13
```

_On this Pi a pre-built venv already exists at `~/.venvs/bdb27` (source it instead of rebuilding)._

## 2. Data

Raw data is symlinked at `data/raw` → the mounted drive (`/mnt/project_data/conor_downloads/bdb27/`).
Game-tracking full files (`game_tracking_2023/24/25.csv`) are **not** in the repo — mount the drive first.

## 3. Run order (full pipeline)

```
01_audit → 02_features → 03_combine_model → 00_thresholds → 04_game_features → 05_link
```

Currently implemented: **`01_audit.py`** (Phase 0/1), **`02_features.py`** (Phase 2, Combine features)
and **`03_combine_model.py`** (Phase 3, Combine fatigue model). Later stages follow in subsequent cycles.

_Dtype downcasting and stage checkpointing (D15) are now **implemented in `02_features.py`** (float64→float32,
int64→int32 on write; the whole feature stage is skipped unless `--force`, and its per-stage wall/RAM rows are
logged). The Phase-1 audit keeps the earlier behaviour (only the CSV→Parquet conversion is checkpointed)._

```bash
# Phase 1 audit (sample)
python src/01_audit.py --mode sample
# rebuild Parquet caches
python src/01_audit.py --mode sample --force

# Phase 1 audit (full; needs the full game files) — audit (e) runs only here
python src/01_audit.py --mode full

# Phase 2 Combine features (sample). Combine data is COMPLETE, so features are
# computed for REAL in both modes; the mode only changes the provenance label.
python src/02_features.py --mode sample
# force a rebuild (ignores the checkpoint)
python src/02_features.py --mode sample --force

# Phase 3 Combine fatigue model (sample). Combine data is COMPLETE, so the model is
# fit for REAL in both modes; the mode only changes the provenance label.
python src/03_combine_model.py --mode sample
# force a rebuild (ignores the checkpoint)
python src/03_combine_model.py --mode sample --force
```

## 4. Outputs

- `outputs/audit/*.csv` + `audit_summary.json` + `SUMMARY.md` (all stamped with a `provenance` column,
  which is always the **first** column).
  - audit (a–g): timing, attempts, order, distance, missingness, gaps.
  - **(h) position scope:** `h_attempts_by_drill.csv` (per position × drill_name, players reaching
    ≥1/≥2/≥3 observed attempts) and `h_family_scope.csv` (per population × family, incl. candidate
    populations DB-only / DB+WR / DB+WR+DL+OL; matched-N columns = **PENDING FULL RUN**).
  - **`assumptions_check.csv`** — runtime results for assumptions A1–A12 (`notes/assumptions.md`).
- `outputs/run_log.csv` — wall time + peak RAM per stage (appended each run).
- `outputs/features/` — Phase 2 (all stamped with a `provenance` column; CSVs have it **first**):
  - `combine_features.parquet` — per attempt (observed + imputed), **all positions** (Phase-2 deliverable).
  - `combine_features_study.parquet` — rows with `in_study_population = true` (**DB**; Phase-3 handoff).
  - `combine_features_player.parquet` — per-player summary.
  - `feature_diagnostics.csv` (long key/value), `standardization_params.csv`, `vif_report.csv`,
    `PROVENANCE.txt`, `SUMMARY.md`.
- `outputs/model/` — Phase 3 (all stamped with a `provenance` column; CSVs have it **first**):
  - `combine_player_slopes.parquet` + `.csv` — **Phase-5 handoff**: shrunken (empirical-Bayes)
    per-player fatigue slopes with CIs (`slope_raw, slope_shrunk, slope_se, ci_lo, ci_hi,
    shrinkage_weight, method, reliability, meets_link_min_attempts`).
  - `combine_model_fixed_effects.csv` — final-estimator fixed effects.
  - `combine_model_random_effects.parquet` — per-player BLUP / EB random effects.
  - `combine_model_baseline.csv` — within-player late−early differencing detail.
  - `combine_model_permutation.csv` — per-permutation `meta_slope` statistic.
  - `combine_model_xdrill.csv` — cross-drill-group per-player slope correlations.
  - `combine_model_diagnostics.csv` (long key/value), `combine_model_summary.json`,
    `SUMMARY.md`, `PROVENANCE.txt`.
- Sample-mode outputs are labelled **`UNVALIDATED SAMPLE OUTPUT`** and must never be interpreted.

## 7. Study scope (see `notes/decisions.md`)

- **Drill family = ALL drills** (D12, human decision 2026-10-09; supersedes D3's 40-yd-only pick). Configured
  in `config.yaml` under `audit.families`; the tighter `timed_battery` (40 + 3-cone + shuttle) is available
  for sensitivity.
- **Position scope = DB only, LOCKED** (D13; human decision 2026-10-09; `config.yaml` `audit.study_population`).
  A **single** position group, which satisfies TASK.md Phase 1 ("select ONE position group"). WR is excluded
  because WR in-game intensity depends on offensive scheme (between-system noise). No Phase-3 position terms
  (the LMM is the spec formula as written). Evidence in `outputs/audit/h_family_scope.csv`.

## 5. Expected cost (Pi 5, 4 GB)

| Stage | Wall | Peak RAM |
|---|---:|---:|
| `01_audit --mode sample` | ≈6 s | ≈380 MB |
| `02_features --mode sample` | ≈12 s | ≈650 MB |
| `03_combine_model --mode sample` | ≈47 s | ≈255 MB |

Full-mode costs depend on the big machine; see `notes/scale.md` and `notes/pending_full_run.md`.

## 6. Tests

```bash
python -m pytest src/tests -q       # schema assertions + invariants + determinism (if pytest available)
python src/tests/test_audit.py      # Phase 1; or run standalone
python src/tests/test_features.py   # Phase 2; or run standalone
python src/tests/test_combine_model.py   # Phase 3; or run standalone
```

> **Phase-2 note (blocker B5 — RESOLVED, D19, 2026-10-10).** The attempt-numbering unit is **detected
> empirically per `(nfl_id, drill_type)`** (`features.attempt_level: empirical`): `attempt` restarts per
> `(player, drill_name)` for 2023/2024 but is a per-`(player, drill_type)` block counter for 2025. The phantom
> 2025 imputation is gone — **2025 imputed rows now 148** (DB study population **28**; total imputed 410 of 6,310
> observed). See `notes/decisions.md` **D19** and `notes/blockers.md` **B5**.

> **Phase-3 note (D20, 2026-10-10).** On the real Combine data (DB-only study population, 122 players, 1,432
> model observations) the pre-registered **stop rule fires**: the primary random-slope LMM
> `(1 + load | player)` and the uncorrelated-RE fallback both hit the parameter-space boundary under **every**
> optimiser (only `powell` reports `converged=True`, but each emits a `ConvergenceWarning`, so neither is
> *clean*), the chain falls to **`per_player_eb`**, and the between-player slope variance is estimated at
> **0 → reliability 0.0 < 0.2**. Per TASK.md Phase 3 the finding is therefore reported as a **NULL**
> (see `outputs/model/SUMMARY.md`). Per-player slopes are still emitted (flagged) for completeness.
> The pooled population load coefficient is −0.000444 yd⁻¹ (p = 0.0055) with permutation p = 0.029, but with
> **zero cross-player slope variance** those slopes cannot support the Phase-5 link. Decisions in
> `notes/decisions.md` **D20**; threats in `notes/limitations.md`.
