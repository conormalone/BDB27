# README_RUN — how to run the BDB27 ceiling-binding pipeline

> **CURRENT TASK (D59/D60, 2026-10-27): leverage × ceiling.** The rest of this file still describes the
> retired ceiling-v1 / db-decay / security pipeline and its commands are **not** the current run order.
> The current pipeline is:
>
> ```bash
> python src/01_audit.py --mode sample            # gate -> outputs/audit/lc/decision.json
> python src/02_combine_ceiling.py --mode sample  # ceiling -> outputs/ceiling/lc/
> python src/00_thresholds.py --mode sample       # thresholds -> outputs/thresholds.json
> python src/03_route_depth.py --mode sample      # depth at the break (D61) -> outputs/game/lc/route_depth/
> python src/04_efforts.py --mode sample          # one row per max-effort route (D65) -> outputs/game/lc/efforts/
> python src/05_models.py --mode sample           # primary within-player estimand (D65) -> outputs/game/lc/models/
> python src/06_validation.py --mode sample       # Phase 5 validation (D66) -> outputs/game/lc/validation/
> python src/07_phase4.py --mode sample           # Phase 4 remainder (D67) -> outputs/game/lc/phase4/
> python -m pytest tests                          # 159 tests
> ```
>
> Full mode uses `--mode full` on `01`/`02`/`00`; it additionally requires the three full
> `game_tracking_{2023,24,25}.csv` files, and `00_thresholds` refuses to stamp `FULL` on sample-sized
> input. Current status is `GO_PENDING_FULL_RUN`; see `notes/decisions.md` D59–D60 and
> `notes/pending_full_run.md`.
>
> **D65 (2026-10-29): the chain now ends in the estimand.** `04_efforts` and `05_models` are wired
> after `03_route_depth` and run from the notebook like every other stage. §14 renders the effort-table
> audit and the leverage coefficient (with the convergence fallback used and the achieved analytic MDE).
> Phase 4's matched design, headroom, ceiling bound, dose-response, per-player slopes, TE replication and
> all of Phase 5 remain **not yet wired** — see D65.
>
> **D66 (2026-10-29): Phase 5 is wired.** `06_validation` runs after `05_models`: leverage permutation
> within player-game and within player-route-family (declared shuffle count, full null distribution on
> disk), the time-and-load orthogonalised refit, placebos, the garbage-time exclusion with and without,
> the declared sensitivity curve and the TOST equivalence test. §15 renders it. The matched design,
> headroom moderation, ceiling bound, dose-response, per-player slopes, TE replication and the
> non-max-effort placebo remain **not wired** — see D66.
>
> **D67 (2026-10-29): Phase 4 is wired, and the chain now covers Phases 1–5.** `07_phase4` runs after
> `06_validation` on the same effort table: matched high-vs-low design (CEM within player, clustered
> bootstrap) against the primary fit on the matched rows, headroom moderation (naive and
> errors-in-variables corrected, plus `vmax` and `tau`), ceiling-bound exceedance by leverage bin,
> dose-response with an explicit monotonicity statement, the reliability-gated per-player slopes, and
> TE replication with its label. §16 renders it. `defender_distance_yd` is still absent from the effort
> table, so that one check is printed as **NOT RUN with the field named**; the non-max-effort placebo
> in Phase 5 remains not wired (it needs the gate-rejected rows written by `04_efforts`).
>
> **D63 (2026-10-28): the deliverable notebook runs this chain.** `notebooks/bdb27_full_run.ipynb`
> now executes the LC chain above first, then the preserved ceiling-v1 chain, and its **§13** prints
> break coverage, coverage by route family, and the D63 keep-or-fall-back rule for the continuous
> `break_depth_yd` control. No terminal is needed for the deciding run. The commands below remain the
> reference for a headless run and for testing (`python -m pytest tests`; 159 tests at D67).

> **Deliverable model:** a **scripts engine** (`src/`) + a **public Kaggle notebook** that runs the same
> logic end-to-end and renders the writeup figures. Stage scripts take `--mode sample|full` and `--force`
> with **one code path** for both; all paths/thresholds come from `config.yaml`. Heavy files are converted
> to Parquet once (checkpointed) and queried with DuckDB; **no full CSV is loaded into pandas**. Every
> sample output is stamped `UNVALIDATED SAMPLE OUTPUT`.
>
> **Status:** Phase 0 **done**; **Phase 1 audit** implemented (D27); **pre-registration FROZEN** + freeze gate
> implemented (D28); **Phase 2 Combine envelope implemented and run** (`src/02_combine_envelope.py`, D29);
> **Phase 3a `00_thresholds`** and **Phase 3b `03_game_demand`** implemented and exercised on the synthetic
> fixture (D30); **Phase 4 `04_models`** and **Phase 5 `05_validate`** implemented and exercised on the
> synthetic fixture (D31); **deliverable notebook** `notebooks/bdb27_full_run.ipynb` built (D32);
> **Phase 0.5 external proxies** (`src/00b_external_proxies.py`, D36) implemented and exercised on a
> synthetic GTM fixture — its real `--mode full` run is **BLOCKED (data)** (GTM export not machine-
> obtainable; see `notes/blockers.md` / `notes/external_data_manifest.md`), and it **gates Phase 4**
> (existence + schema). The whole game-side pipeline is game-side, so every real `--mode full` run still
> needs the full tracking files (`PENDING FULL RUN`). See `notes/decisions.md` for D27–D36.
>
> **Cycle 2 (D37–D39).** `06_security` (EDP-board exposures + Analysis A) and the **new**
> `07_security_models` (Phase 5 B–G: the **incentive-exposure** two-group contrast) are implemented and
> exercised on committed fixtures. **D38 retires Analysis A as a headline gate** — it is now a short
> attenuation-corrected appendix, and the paper's spine is incentive exposure (`moved_down` vs
> `high_secure`, assigned from **pre-Combine** EDP only). The real game-side runs remain
> **`PENDING FULL RUN`** (no `game_tracking_{2023,24,25}.csv` on this Pi).

## 1. Environment (ARM64 Pi / x86 Linux / Windows laptop)

**Minimum supported Python 3.12.** The pinned dependencies require it (`numpy==2.5.3` and `scipy` need >= 3.12; `pyarrow` 26 / `pandas` 3.0.6 / `scikit-learn` 1.9.1 / `matplotlib` 3.11.2 need >= 3.11), and the pipeline uses PEP 701 f-strings — so Python 3.12 is the floor. Kaggle currently ships 3.13.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt          # pinned; works on ARM64 + x86_64
```

_On the Pi a pre-built venv already exists at `~/.venvs/bdb27` (source it instead of rebuilding)._

## 2. Data

Raw data is symlinked at `data/raw` → the mounted drive (`/mnt/project_data/conor_downloads/bdb27/`).
The **full** game-tracking files (`game_tracking_{2023,24,25}.csv`) are **not** present on the Pi — mount
the drive (or copy the CSVs to a laptop and point `data/raw` there) before any game-side stage.

## 3. Run order

**Current chain (D59–D66)** — what the notebook runs first:

```
01_audit → 02_combine_ceiling → 00_thresholds → 03_route_depth → 04_efforts → 05_models → 06_validation
```

**Retired ceiling-v1 chain (D27–D50, preserved under `src/legacy_ceiling_v1/`)** — still runnable, and
what §5–§12 of the notebook read:

```
02_combine_envelope → 06_security → 03_game_demand → 07_security_models → 10_dose_response → 00b_external_proxies → 04_models → 05_validate
```

`02_combine_envelope` / `03_game_demand` are *different stages* from the LC `02_combine_ceiling` /
`03_route_depth`, not renames: different code, different outputs. Stage names resolve against `src/`
first, then the parked tree.

```bash
# Phase 1 audit (go/no-go gate); frame-level game items (c/h/i speed+accel) are full-run only
python src/01_audit.py --mode sample
python src/01_audit.py --mode full          # needs the full game files
# exercise the game-side code path on the committed synthetic fixture (planted ceiling; NOT a finding)
python src/01_audit.py --synthetic --synthetic-regime binding
# schema/behaviour tests (no pytest needed)
python src/tests/test_audit.py

# Phase 2 Combine envelope — the Combine file is COMPLETE, so this runs for real in both modes
python src/02_combine_envelope.py --mode sample
python src/02_combine_envelope.py --mode sample --force   # rebuild ignoring the checkpoint

# Phase 3 thresholds — SAMPLE writes a-priori PLACEHOLDER values; FULL derives from full game data
python src/00_thresholds.py --mode sample
python src/00_thresholds.py --mode full          # derives; needs the full game files
# exercise the derivation on the committed synthetic fixture (planted ceiling; NOT a finding)
python src/00_thresholds.py --synthetic --synthetic-regime binding

# Phase 3 game demand — SAMPLE emits a labelled PENDING placeholder; FULL consumes thresholds(FULL)+ceiling
python src/03_game_demand.py --mode sample
python src/03_game_demand.py --synthetic --synthetic-regime binding   # or: null
python src/03_game_demand.py --mode full         # needs the full game files

# Phase 4 models — consumes thresholds(FULL)+ceiling+efforts; game-side gated (freeze + audit go/no-go)
#   and now ALSO gated on the Phase 0.5 external proxy (existence + schema)
python src/04_models.py --mode full          # needs the full game files AND outputs/pre_combine_expectation.parquet
python src/04_models.py --synthetic --synthetic-regime binding   # or: null (not a finding)

# Phase 0.5 external proxies — pre-Combine Expected Draft Position (Grinding The Mocks) -> gates Phase 4
python src/synthetic_gtm_fixture.py                       # (re)build the committed synthetic GTM fixture
python src/00b_external_proxies.py --mode sample          # exhibits the pipeline on the fixture (UNVALIDATED)
python src/00b_external_proxies.py --synthetic             # identical path on the fixture (not a finding)
python src/00b_external_proxies.py --mode full            # needs a raw GTM export in data/raw/external/gtm/
#   (GTM is currently NOT machine-obtainable -> --mode full stops with a data blocker; notes/blockers.md)

# Phase 5 validation — CV vs controls-only baseline; permutation; icc_attenuation; sensitivity
python src/05_validate.py --mode full        # needs the full game files
python src/05_validate.py --synthetic --synthetic-regime binding # or: null (not a finding)

# Phase 4 SECURITY — pre-Combine EDP-board exposures + Analysis A (the early headline gate; board+Combine
#   only, so it runs for real on this Pi). Reads external.board (permitted; notes/rules.md §6) + the Phase-2
#   envelope; writes outputs/security/{security_exposure.parquet,analysis_a.json,
#   analysis_a_attenuation.csv,SUMMARY.md}. D38: Analysis A is APPENDIX-ONLY (its gate is retired).
python src/synthetic_security_fixture.py                    # (re)build the committed synthetic security fixture
python src/06_security.py --mode full --force                # real board + envelope (Analysis A gate)
python src/06_security.py --mode sample                      # labelled UNVALIDATED SAMPLE OUTPUT
python src/06_security.py --synthetic --synthetic-regime planted   # planted effect (not a finding)
python src/06_security.py --synthetic --synthetic-regime null      # null case (not a finding)

# Phase 5 B–G SECURITY MODELS — the incentive-exposure spine (D38). Needs the Phase-3 game tables, the
#   Phase-4 security exposure table, the Phase-2 envelope + FULL thresholds. Writes
#   outputs/security_models/{SUMMARY.md,SUMMARY.json,group_assignment.csv,b_primary.csv,b_group_means.csv,
#   c_end.csv,c_exceedance.csv,d_skippers.csv,e_selection.csv,e_ipw.csv,falsification.csv,g_tail.csv,
#   g_tail_summary.csv,covariate_quality.csv,_DONE.json}. covariate_quality.csv (D44) lists every
#   implausible height/weight/BMI row by nfl_id and the plausible bounds it violated.
python src/synthetic_security_game_fixture.py                 # (re)build the committed game-side fixture
python src/07_security_models.py --mode sample                # labelled PENDING FULL RUN placeholder (exit 0)
python src/07_security_models.py --mode full                  # real estimates (needs the full game files)
python src/07_security_models.py --synthetic --synthetic-regime planted    # planted effect
python src/07_security_models.py --synthetic --synthetic-regime null       # null case
python src/07_security_models.py --synthetic --synthetic-regime selection  # selection + IPW

# DOSE-RESPONSE PANEL (D50) — EXPLORATORY / POST HOC: the same block-B outcome with the exposure modelled
#   CONTINUOUSLY (edp_trend: linear, 3-df restricted cubic spline, explicit knot at 5, fixed/quantile bins,
#   segmented kink) under seven draft-capital treatments. It cannot change b_primary.csv or the verdict.
#   Writes outputs/security_models/dose/{dose_fits.csv,dose_bins.csv,dose_curve.csv,SUMMARY.md,SUMMARY.json,
#   fig_dose_response.png} (the figure is supplementary — NOT one of the 5 writeup figures).
python src/synthetic_security_game_fixture.py --regime dose_all          # (re)build the four dose fixtures
python src/10_dose_response.py --synthetic --synthetic-regime dose --force        # planted linear -0.002
python src/10_dose_response.py --synthetic --synthetic-regime dose_null --force   # null case
python src/10_dose_response.py --synthetic --synthetic-regime dose_kink --force   # 0 below 5, -0.006 above
python src/10_dose_response.py --synthetic --synthetic-regime dose_capital --force  # slope dies under pick
python src/10_dose_response.py --mode sample                             # labelled PENDING FULL RUN (exit 0)
python src/10_dose_response.py --mode full                               # needs the full game files

# Phase 3/4/5 tests (no pytest needed)
python src/tests/test_thresholds.py
python src/tests/test_game_demand.py
python src/tests/test_models.py
python src/tests/test_validate.py
# Phase 0.5 tests
python src/tests/test_external_proxies.py
# Phase 4 security tests
python src/tests/test_security.py
python src/tests/test_security_audit.py
# Phase 5 B–G security-model tests (sort key, post-Combine invariance, recovery/null/selection, CLI)
python src/tests/test_security_models.py
# D50 dose-response panel tests (spline/bins/kink, thin guards, recovery, capital, determinism, figure)
python src/tests/test_dose_response.py
```

`--force` ignores stage checkpoints (stages otherwise skip finished work). Logs of wall time + peak RAM per
stage append to `outputs/run_log.csv`.

**Data-QC note — game-side frame-level speed despike (D35).** Game-side per-frame speed is despiked at
`game.speed_cap_yds` (**12.5 yd/s** = 25.6 mph, above every real in-game route speed): **impossible
per-frame samples** (`> cap`, e.g. from a single noisy or duplicated frame) are **discarded** and the
per-effort `peak_speed` / `peak_accel` are recomputed from the **retained** frames via a greedy forward
scan. The **effort is always kept** (a degenerate effort with <2 retained frames reports 0.0) — we
discard the bad *sample*, not the whole effort. This stops the top-k (exceedance) tail being
contaminated by impossible samples. Applied at the frame level in the shared kinematics SQL (game side
only — the Combine side is untouched). After this change the game chain must be re-run with `--force`:
`00_thresholds → 03_game_demand → 04_models → 05_validate` (Combine `02` is unchanged). The notebook
does this for you via its `FORCE` / `CLEAN` switches (Set `FORCE=True` and/or `CLEAN=True`, then Run
All). See `notes/decisions.md` D35 and `notes/limitations.md` §21.

**Pre-full-run gate.** A `--mode full` run may not start until `notes/prereg.md` is **frozen** (values
filled, not the skeleton) and the audit go/no-go gate passes (matched WR count, route snaps/player, missing
Combine fields, ICC). Otherwise → `notes/blockers.md`.

## 4. Notebook deliverable (primary interface — no terminal needed)

`notebooks/bdb27_full_run.ipynb` is the runnable deliverable and runs the same `src/` engine end-to-end.
Open it, set `MODE` (`sample` | `full`) and — only if the data is elsewhere — `DATA_DIR`, then **Run All**:

- drives the stages in the frozen order `01_audit → 02_combine_envelope → 00_thresholds → 03_game_demand →
  04_models → 05_validate` (each through its own entry point, so `--mode` / `--force` / checkpoints and the
  pre-registration freeze gate all apply);
- cross-platform (ARM64 Pi / Windows / macOS) and **Kaggle-ready** (auto-detects the dataset under
  `/kaggle/input/…`, otherwise `data/raw`);
- renders the ≤5 writeup figures into `outputs/figures/` and shows them inline;
- with only partial data, runs everything it can and reports the game-side stages `PENDING FULL RUN`,
  demonstrating the game-side path on the committed synthetic fixture (`binding` + `null`).

Pre-full-run it renders `UNVALIDATED` placeholders. The `python src/…` commands in §3 are exactly what the
notebook runs for you (kept for CI / power users).

## 5. Outputs

- `outputs/run_log.csv` — wall time + peak RAM per stage (columns, in order: `timestamp, stage, mode,
  wall_s, peak_ram_mb, error, notes`). Peak RAM is measured **portably**: POSIX `resource.getrusage`
  (KiB on Linux, bytes on macOS) or, on Windows, `psapi.GetProcessMemoryInfo` via stdlib `ctypes`. If no
  measurement is available the documented sentinel **`-1.0`** is logged — a stage **never** fails because
  memory measurement is unavailable.
- `outputs/figures/` — the ≤5 writeup figures.
- Per-stage summary tables under `outputs/` (gitignored). Every table's **first** column is `provenance`
  (`UNVALIDATED SAMPLE OUTPUT` in sample mode, `FULL` in full mode).
- `outputs/thresholds.json` — route-effort cut-offs (`FULL` provenance in full mode; `PLACEHOLDER` in sample).
- `outputs/pre_combine_expectation.parquet` — Phase 0.5 deliverable: maps Kaggle `nflId` to
  `Pre_Combine_EDP` + `match_confidence_score` (EXACT schema asserted). Written by
  `src/00b_external_proxies.py --mode full`; **required** by `04_models --mode full` (Phase-4 gate). Also
  `outputs/unmatched_external_ids.csv` (sub-threshold matches) and `notes/external_data_manifest.md`
  (external-data compliance record). Sample/synthetic runs write these under `outputs/external/` with a
  `UNVALIDATED SAMPLE OUTPUT` / `SYNTHETIC FIXTURE` label.

## 6. Expected cost (measured on the Pi, ARM64 / Python 3.13)

Wall time and peak RAM are appended per stage to `outputs/run_log.csv` (`common.timed_stage`). The table is
**measured on the synthetic fixtures**; the real `--mode full` cost is `PENDING FULL RUN` (P8/P16 in
`notes/pending_full_run.md`) and is dominated by the Phase-3 streaming pass over ~2.9 GB of game tracking.

| Stage | Fixture / mode | Wall time (median) | Peak RAM | Notes |
|---|---|---:|---:|---|
| `01_audit` | synthetic (small) / sample | 0.9 s / 21 s | 144 / 387 MB | sample mode scans the 2²⁰-row game prefix |
| `02_combine_envelope` | sample (complete Combine file) | 3.0 s | 280 MB | real: Combine side is complete on this Pi |
| `06_security` | real board + envelope | 2.2 s | 281 MB | Analysis A + attenuation appendix (appendix-only, D38) |
| `00_thresholds` | synthetic | 0.17 s | 280 MB | streams the efforts once |
| `03_game_demand` | synthetic | 0.7 s | 280 MB | real: the per-game streaming pass (scale driver) |
| `07_security_models` | synthetic, 3 regimes | **6.2 s** (max 22.8 s with the repo bootstrap reps) | **267 MB** | B/C/E/F/G; the G quantile bootstrap is the cost driver |
| `00b_external_proxies` | sample (fixture) | 0.3 s | 162 MB | real `full` is data-blocked (GTM not obtainable) |
| `04_models` | synthetic (8 players) | 7.2 s | 280 MB | 1000-rep quantile bootstrap |
| `05_validate` | synthetic (8 players) | 4.9 s | 280 MB | CV + permutation + sensitivities |

`07_security_models` bootstrap knobs: `security_models.primary.attenuation_reps` (200) and
`security_models.g.bootstrap_reps` (60). Raise them on the big machine if tighter tail intervals are wanted;
the estimate is unchanged. The synthetic fixture is 198 players / ~1.7k efforts; the real full run is the
same code path with the real Phase-3 tables.

## 7. Local run on Windows / laptop

The pipeline is cross-platform (Windows, macOS, Linux): stage scripts use `pathlib`, no bash-only calls, and
accept Windows paths. Memory logging is portable too — the peak-RAM probe uses POSIX `resource.getrusage`
where present and falls back to the Windows `psapi` API via stdlib `ctypes` (logging the `-1.0` sentinel if
neither is available), so importing `src/common.py` never depends on a POSIX-only module. On Windows:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
# put the BDB CSVs in a folder, then point config.yaml paths.raw at it (or copy them to data\raw)
python src\02_combine_envelope.py --mode full      # Combine side (data complete)
```

A real (`full`) run requires `game_tracking_2023.csv`, `game_tracking_2024.csv`, `game_tracking_2025.csv`
in the raw data folder. Without them the game-side stages cannot run; the Combine-side stage still runs on
the complete Combine file.
