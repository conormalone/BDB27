# Notebooks

`bdb27_full_run.ipynb` **is the interface for this task.** Conor never uses a terminal:
open the notebook, set `MODE` in §1, press **Run All**.

Phases 0–2 are implemented (stage `02_turns` writes the real turn table); stages `03`–`06`
are still loud stubs, so the notebook reports them as `NOT IMPLEMENTED (stub, exit 2)`.

## What it does

1. **§1 Configuration** — the only cell to edit: `MODE`, `DATA_DIR`, `FORCE_STAGES`,
   `CLEAN`/`CLEAN_CONFIRM`, `ONLY_STAGES`. Nothing below it needs editing.
2. **§2 Bootstrap** — finds the repo, resolves the data directory, and prints a
   present/absent row for every input the current mode reads. A missing file is always
   named.
3. **§3–§4 The chain** — runs the seven stages in the order `TASK.md` fixes
   (`00_thresholds → 01_audit → 02_turns → 03_envelope → 04_demand_coverage →
   05_validate → 06_figures`), each through its own `main()`, reloading the stage module
   and every `bdb27/` helper from disk first. One row per stage: exit code, wall time,
   status.
4. **§5 Headline** — reads **only** `outputs/audit/gate.json` and
   `outputs/audit/audit_items.csv`: the provenance banner taken from the artefact, the
   verdict and why, the primary group, the recommended smoother, conditions G1–G9
   (status/value/threshold/detail) and items a–m (status + key value). No number is typed
   into the notebook; anything absent prints `NOT RUN — <field> absent`.
5. **§6 Figures** — displays what exists in `outputs/figures/`, and when nothing does,
   says why using the `06_figures` row from §4.

## Status vocabulary (the notebook prints these)

| status | meaning |
|---|---|
| `OK — ran, artefacts written` | pass |
| `OK — checkpoint reused, nothing rebuilt` | pass; existing artefacts reused |
| `NOT IMPLEMENTED (stub, exit 2)` | loud stub: wrote nothing, **never counted as a pass** |
| `REFUSED (exit 2)` | declined honestly (missing input / wrong provenance); reason printed |
| `FAILED (exit n)` | real failure; captured output printed |
| `PENDING FULL RUN` | `MODE="full"` with the full game tracking absent; nothing is run and **no sample file is substituted** |

## Provenance

* `MODE="sample"`: every artefact is `PLACEHOLDER_SAMPLE` / `UNVALIDATED SAMPLE OUTPUT`,
  and the headline prints `UNVALIDATED SAMPLE OUTPUT — NOT A RESULT`.
* `MODE="full"`: artefacts are `FULL`; only then is a number a result.
* The notebook contains **zero baked outputs** — it is committed unexecuted, and
  `tests/test_notebook_contract.py` enforces that.

## The contract is tested

`tests/test_notebook_contract.py` holds the notebook to four promises: stage order is the
spec's order (and matches `src/run.py`), the committed notebook carries no outputs, a
missing `gate.json` renders `NOT RUN` rather than a blank, and a stub is never a pass;
plus the full-mode preflight names every missing tracking file and blocks the run.

## Shell fallback

The same chain runs from the shell — see `README_RUN.md`. The notebook adds nothing to the
pipeline: it calls the same stage `main()` functions, in the same order, with the same
config.

`archive/legacy-*/notebooks/` holds the superseded pipeline's notebooks. They ran a
different analysis and must not be reused.
