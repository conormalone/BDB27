# Notebooks

Runnable, self-contained `.ipynb` files. The **competition deliverable is a public Kaggle notebook +
writeup** (≤2,000 words, ≤5 figures); this folder holds that notebook.

**Minimum supported Python 3.12** (Kaggle currently ships 3.13); pinned deps and the `../src/` engine
require it.

## Deliverable notebook — `bdb27_full_run.ipynb`

**The primary interface; no terminal needed.** Open it, set `MODE` (`sample` | `full`) and — only if the
data is not in the usual place — `DATA_DIR`, then **Run All**. It:

- runs the `../src/` engine end-to-end, **current chain first** (D63):
  `01_audit → 02_combine_ceiling → 00_thresholds → 03_route_depth`, then the preserved ceiling-v1
  chain `02_combine_envelope → 06_security → 03_game_demand → 07_security_models → 10_dose_response
  → 00b_external_proxies → 04_models → 05_validate`, invoking each stage through **its own entry
  point** (so `--mode` / `--force` / checkpoint behaviour and the checkout guard all apply exactly as
  on the command line). `02_combine_ceiling`/`03_route_depth` are the LC stages;
  `02_combine_envelope`/`03_game_demand` are different, retired stages — not renames;
- is **cross-platform** (ARM64 Pi, Windows, macOS) and **Kaggle-ready** (auto-detects the attached dataset
  under `/kaggle/input/…`, otherwise uses `../data/raw`) — `pathlib` only, no bash-only calls, and the
  peak-RAM probe (`../src/common.py`) is portable too: POSIX `resource.getrusage` where present, a stdlib
  `ctypes`/`psapi` fallback on Windows, and a documented `-1.0` sentinel if neither is available;
- renders the **≤5 writeup figures** into `../outputs/figures/` and shows them inline;
- **degrades gracefully**: with only partial data it runs everything it can and reports the game-side
  stages as `PENDING FULL RUN`, demonstrating the game-side path on the committed synthetic fixture
  (planted-ceiling `binding` **and** `null`);
- **prints the current task's evidence in §13** — break coverage, coverage by route family and the
  D63 keep-or-fall-back rule for the continuous `break_depth_yd` control, all read from
  `../outputs/game/lc/route_depth/` with no hand-typed numbers. In `sample` mode §13 names the missing
  full-data inputs and states that the decision is **not taken** on sample counts.

**Labels.** `sample` outputs → `UNVALIDATED SAMPLE OUTPUT`; `full` outputs → `FULL`; fixture outputs →
`SYNTHETIC FIXTURE (not a finding)`. §13 takes the D63 decision only when `MODE="full"` **and** the
route-depth and thresholds provenance are both `FULL`. §8–§12 are the **retired** ceiling-v1 results:
§8 prints its pre-declared verdict only on a full run with `FULL` security outputs, otherwise a
`PREVIEW ONLY — not a result` banner.

**Checkpoints.** `CLEAN=False` (default) keeps existing stage outputs; `FORCE_STAGES=["…"]` rebuilds
only the named stages. `CLEAN=True` is inert unless `CLEAN_CONFIRM="DELETE"`; when armed it deletes
every stage output under `outputs/` (including the Combine envelope) **and the LC stage checkpoints
under `../data/interim/checkpoints/`**, so the next run is a genuine rebuild rather than a skip against
a checkpoint whose outputs no longer exist. It prints the directories and file counts before deleting.

## Conventions
- **Self-contained**: each notebook opens with its imports + data paths and runs top-to-bottom, no hidden state.
- **Data path** points at `../data/raw` (symlink to the mounted drive) — never commit data.
- Figures go to `../outputs/figures/` (the ≤5 writeup figures).
- Keep heavy logic in `../src/` and import it — the notebook stays thin and the engine stays testable.
- Memory discipline (ARM64 Pi): the notebook never loads the large tracking CSVs into pandas; the `src/`
  stages stream them (Parquet + DuckDB).
- A separate "local variant" is unnecessary: the single deliverable notebook already runs on the Pi /
  laptop with its defaults and on Kaggle via auto-detection.
