# Notebooks

Runnable, self-contained `.ipynb` files. The **competition deliverable is a public Kaggle notebook +
writeup** (≤2,000 words, ≤5 figures); this folder holds that notebook.

**Minimum supported Python 3.12** (Kaggle currently ships 3.13); pinned deps and the `../src/` engine
require it.

## Deliverable notebook — `bdb27_full_run.ipynb`

**The primary interface; no terminal needed.** Open it, set `MODE` (`sample` | `full`) and — only if the
data is not in the usual place — `DATA_DIR`, then **Run All**. It:

- runs the `../src/` engine end-to-end, **current chain first** (D63–D67):
  `01_audit → 02_combine_ceiling → 00_thresholds → 03_route_depth → 04_efforts → 05_models →
  06_validation → 07_phase4`, then the preserved ceiling-v1
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

  §14 renders the effort table and the primary leverage model (D65), §15 the Phase 5 validation
  (D66), and **§16 the Phase 4 remainder (D67)** — matched vs regression, headroom moderation
  (naive and corrected), ceiling-bound exceedance by bin, dose-response monotonicity, the per-player
  reliability gate and the labelled TE replication, all read from `../outputs/game/lc/phase4/`.
  A check whose field is absent is printed as **NOT RUN with the field named**, never proxied.

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

## Current task — `bdb27_dl_full_run.ipynb`

**Use this notebook for the DL acceleration-ceiling task** (`TASK.md`, 2026-10-30). It is the
runnable interface for the current chain, not a variant of the retired leverage pipeline. Open it,
set `MODE` (`sample` | `full`) and — only if needed — `DATA_DIR`, then **Run All**. No terminal.

- Runs exactly `00_thresholds → 01_audit → 02_combine_ceiling → 03_game_snaps → 04_models`, each
  through its own `main()` with `stage_runner.run_stage`, so the stage reloads its code (and
  helpers) from disk on every call. A `git pull` cannot leave stale code in the kernel.
- **Sample mode** stamps every artefact `PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT` and the
  headline cell prints **`WITHHELD`** — no sample number can be mistaken for a finding.
- **Full mode** requires `game_tracking_{2023,24,25}.csv`. If they are absent, the stages that
  need them (`01_audit`, `03_game_snaps`, `04_models`) print **`PENDING FULL RUN`** with the missing
  filenames, and the run continues; they are never executed on the sample just to look green.
- The **headline cell** reads only `outputs/dl/`, prints the pre-declared audit gate, `a0` ICC,
  matched-DL count, median primary snaps, Part-1 HR with CI, the Part-2 out-of-sample comparison,
  S1 load and load×`a0`, event rate, MDE, reliability rows and every gate item. A missing file or
  column prints `NOT RUN — <field> absent`; nothing is guessed or zero-filled.
- `05_validate` is **not built yet**, so it is deliberately absent from `DL_STAGE_ORDER`; the
  headline reports Phase 5 as `NOT RUN` and `notes/blockers.md` carries it as the next build item.
  The notebook does not claim validation ran.
- `CLEAN=True` is inert unless `CLEAN_CONFIRM="DELETE"`; when armed it deletes only `outputs/dl/`
  and `data/interim/dl_checkpoints/`, never the preserved LC/ceiling-v1 outputs.
- Diagnostics: at most two figures, rendered only from `FULL`-provenance outputs; skipped with a
  reason in sample mode or when the inputs are absent.

**When to use which**

| notebook | task | use it when |
|---|---|---|
| `bdb27_dl_full_run.ipynb` | **current** DL acceleration ceiling → pass-rush pressure | any current DL run; this is the interface Conor should click |
| `bdb27_full_run.ipynb` | retired leverage × ceiling (WR/TE) + preserved ceiling-v1 chain | reproducing the parked LC/legacy outputs; not the current task |

## Conventions
- **Self-contained**: each notebook opens with its imports + data paths and runs top-to-bottom, no hidden state.
- **Data path** points at `../data/raw` (symlink to the mounted drive) — never commit data.
- Figures go to `../outputs/figures/` (the ≤5 writeup figures).
- Keep heavy logic in `../src/` and import it — the notebook stays thin and the engine stays testable.
- Memory discipline (ARM64 Pi): the notebook never loads the large tracking CSVs into pandas; the `src/`
  stages stream them (Parquet + DuckDB).
- A separate "local variant" is unnecessary: the single deliverable notebook already runs on the Pi /
  laptop with its defaults and on Kaggle via auto-detection.
