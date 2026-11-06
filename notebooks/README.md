# Notebooks

The **deliverable interface** is `bdb27_full_run.ipynb`, the notebook for the **current
burst-vs-jump chain** (`TASK.md`, 2026-11-05). Open it, set `MODE` in §1, and **Run All**. No
terminal, no command to type.

**Minimum supported Python 3.12** (Kaggle currently ships 3.13); the pinned deps and the `../src/`
engine require it. Running the notebook needs Jupyter; every other dependency is already in
`requirements.txt`.

## `bdb27_full_run.ipynb` — the primary interface

**One editable cell.** The top code cell holds exactly five parameters and nothing below it needs
editing:

| parameter | default | meaning |
|---|---|---|
| `MODE` | `"sample"` | `"sample"` (provided truncated tracking) or `"full"` (the final run) |
| `DATA_DIR` | `None` | `None` = repo default `data/raw`; a relative path resolves against the repo root |
| `FORCE_STAGES` | `[]` | rebuild only the named stages; `[]` reuses checkpoints |
| `CLEAN` | `False` | destructive, but inert unless `CLEAN_CONFIRM == "DELETE"` |
| `CLEAN_CONFIRM` | `""` | safety interlock for `CLEAN` |

**What it runs.** Exactly the `TASK.md` order:

```
01_audit -> 02_combine_physical_time -> 03_game_snaps -> 03b_reliability -> 04_models
```

Each stage is invoked through **its own `main()`**, with the stage module and its `src/` helpers
(`common`, `schema`, `physical`, `stats`, `models`, `reliability`, `power`) reloaded from disk
before every call — including purging the cached bytecode. A `git pull` cannot leave stale code in
a long-lived kernel. The notebook holds no analysis logic; the engine stays in `src/`.

`05_validate` is a **stub that exits 3**. The notebook runs it after the chain and reports
`NOT BUILT (stub, exits 3)`. It never counts the stub as a pass and never claims Phase 5 ran.

**Provenance.**

* `MODE="sample"` — every stage artefact is stamped `PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE
  OUTPUT`. The headline prints the numbers as **sample diagnostics** under a loud
  `UNVALIDATED SAMPLE OUTPUT — NOT A RESULT` banner, with the qualifier repeated on every group.
  Figures are skipped with a reason.
* `MODE="full"` — artefacts are stamped `FULL`; only then is a number a result and only then are
  the four figures rendered.
* A missing file or field prints **`NOT RUN — <field> absent`**. Nothing is guessed, proxied or
  zero-filled. A zero variance share is printed as zero and is never replaced by the naive rung.

**Full-mode degradation.** If `game_tracking_{2023,2024,2025}.csv` are absent, `01_audit` and
`03_game_snaps` are reported `PENDING FULL RUN` with the missing filenames; `03b_reliability` and
`04_models` are `PENDING FULL RUN` because their upstream table cannot exist. The shared full-mode
gate (`common.require_full_tracking`) is applied to every stage, so `02_combine_physical_time` is
attempted and its `REFUSED (gate)` exit code is reported. The run continues throughout and **never
falls back to the sample**.

**Headline.** Reads only `../outputs/phase4/decomposition.json`. In order: the provenance banner
from the artefact itself; the estimator and measurement-error treatment; pooled, edge and interior
separately (between-player phys/jump split, total-variance split, 95% intervals, `b` and its
interval, jump SD in ms, reliability, latency agreement, rung used, boundary flags); the
**resolution limit** (smallest detectable jump SD ≈ 52–54 ms) stated as a resolution limit, not a
pass/fail threshold; and the physical-interpolation coverage counter.

**Figures (max 4 — `TASK.md` line 115).** Rendered only from `FULL` outputs, saved to
`../outputs/figures/` and shown inline:

1. variance split with intervals, edge vs interior;
2. Combine-predicted physical time vs observed get-off, with named players off the diagonal;
3. reliability and latency agreement of jump;
4. one rusher's rep-level story against that player's own physical curve.

**No baked outputs.** The committed `.ipynb` has zero `execution_count`s and zero outputs. It
imports and sets paths at the top and runs top-to-bottom with no hidden state.

The rendering and contract helpers live in `../src/notebook_report.py`; the notebook cells only
call them.

## Archived notebooks — historical, do not follow

On 2026-11-05 the two previous notebooks were archived with the tasks they served:

| file | task | status |
|---|---|---|
| `../archive/legacy-2026-11-05/notebooks/bdb27_full_run.ipynb` | retired leverage × ceiling (WR/TE) + preserved ceiling-v1 chain | archived; its stage names do not exist in the current `src/` |
| `../archive/legacy-2026-11-05/notebooks/bdb27_dl_full_run.ipynb` | retired DL acceleration-ceiling task | archived; superseded by `TASK.md` 2026-11-05 |

Their README text described those notebooks as live. That was a real defect in the repo's
description of its own interface; this file now describes only what exists. Historical notebooks
are preserved with git history and are not run.

## Conventions

- **Self-contained**: imports and data paths at the top, top-to-bottom run, no hidden state.
- **Data path** points at `../data/raw` (a symlink to the mounted drive) — never commit data.
- Figures go to `../outputs/figures/` (the four writeup figures, seeded and deterministic).
- **Thin interface**: heavy logic lives in `../src/`; the notebook configures, runs stages, and
  renders. `src/notebook_report.py` holds presentation only — no analysis.
- **Memory discipline (ARM64 Pi)**: the notebook never loads the large tracking CSVs into pandas;
  the `src/` stages stream them (Parquet + DuckDB).
- A separate "local variant" is unnecessary: the single deliverable notebook runs on the Pi /
  laptop with its defaults and on Kaggle by pointing `DATA_DIR` at the attached dataset.

## Maintenance check

The notebook is exercised end-to-end by `nbconvert` in sample mode (zero error outputs) and the
contract is covered by `tests/test_notebook_contract.py`: stage order, zero baked outputs, the
reload-from-disk path, `05_validate` reported as not built, `NOT RUN` on a missing
`decomposition.json`, and the sample banner. `python -m pytest -q` is the suite.
