# README_RUN — how to run the BDB27 ceiling-binding pipeline

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

```
01_audit → 02_combine_envelope → 00_thresholds → 03_game_demand → 00b_external_proxies → 04_models → 05_validate
```

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

# Phase 3/4/5 tests (no pytest needed)
python src/tests/test_thresholds.py
python src/tests/test_game_demand.py
python src/tests/test_models.py
python src/tests/test_validate.py
# Phase 0.5 tests
python src/tests/test_external_proxies.py
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

## 6. Expected cost

To be measured once the stages exist (see `notes/scale.md`). Known: the whole Combine side is a **compute**
problem on a small (14.2 MB Parquet) file and runs on the Pi; the **game side** (~2.9 GB over three
seasons) is the scale driver and must stream per game/player.

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
