# Notebooks

`bdb27_full_run.ipynb` **is the interface for this task.** Conor never uses a terminal:
open the notebook, set `MODE` in §1, press **Run All**.

Six of the seven stages are implemented; `05_validate` is still a loud stub and is reported
as `NOT IMPLEMENTED (stub, exit 2)`. In `MODE="sample"` every artefact is
`PLACEHOLDER_SAMPLE` / `UNVALIDATED SAMPLE OUTPUT`, and stage `06_figures` writes twenty-two
figures plus `outputs/figures/figures_manifest.json`, which is what the gallery (§5) reads.

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
4. **§5 Figure gallery** — reads `outputs/figures/figures_manifest.json` and shows every
   figure the stage wrote, in manifest order: title, claim, caveats, sources, the figure's
   own stamp, then the PNG displayed **from its path** (nothing is embedded in the
   notebook). No figure is named in the notebook — add one to `06_figures` and it appears
   here. A listed PNG that is absent prints `MISSING` against its manifest name (no
   placeholder image, no silent skip); a missing manifest prints `NOT RUN` with the
   `06_figures` reason from §4. Sample provenance is a single **banner at the top**
   (`PLACEHOLDER_SAMPLE`), and a sample figure is never presented as a result. The
   schematic drawing shows its own `SCHEMATIC` status instead — the gallery does
   not force one stamp onto every figure.
5. **§6 Headline** — reads **only** `outputs/audit/gate.json` and
   `outputs/audit/audit_items.csv`: the provenance banner taken from the artefact, the
   verdict and why, the primary group, the recommended smoother, conditions G1–G9
   (status/value/threshold/detail) and items a–m (status + key value). No number is typed
   into the notebook; anything absent prints `NOT RUN — <field> absent`.
6. **§7–§8 Phase 3 / Phase 4 headline blocks** — the envelope and demand/coverage
   artefacts, rendered from what the stages wrote, with `NOT RUN` for anything absent.

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
plus the full-mode preflight names every missing tracking file and blocks the run. The
gallery adds five more: every manifest entry appears in the rendered text, the cell reads
the manifest rather than a literal figure list, a missing PNG prints `MISSING` instead of
raising, the sample banner is printed once, and the schematic renders as `SCHEMATIC`
without a sample stamp.

## Shell fallback

The same chain runs from the shell — see `README_RUN.md`. The notebook adds nothing to the
pipeline: it calls the same stage `main()` functions, in the same order, with the same
config.

The superseded pipeline's notebooks ran a different analysis and must not be reused;
they live only in git history.

## The outcome-link pre-flight

* `bdb27_outcome_link_preflight.ipynb` — the stop-check that runs **before** any geometric
  matching: how many band turns have an opposing route-runner on the play at all, measured on
  `player_play.csv` alone (P1–P5), with the verdict printed for each population. It reads no
  tracking file, writes nothing, and stops only on a real condition. The geometric match and the
  tie rate are described in its §6 and remain unmeasured.

## The geometric-attribution run

* `bdb27_geometric_attribution.ipynb` — **the run Conor does on the laptop**, because the full game
  tracking lives there and not on this host. Run All. It measures the **misattribution rate** of
  the D199 elimination rule, the **coverage of the band** by the frozen 3-yd rule at 2/3/5 yd, and
  re-runs the D199 estimator unchanged on the geometric unique-match set, side by side with the
  elimination coefficient. It stops, with counts, if the tracking file is the 1,048,575-row
  truncation or does not observe opponents on most plays. Spec: `notes/geometric-attribution-spec.md`.
  The committed notebook carries no outputs; it was executed here only on the sample
  (`ALLOW_SAMPLE_TRACKING`), and the artefacts are `outputs/geometric_attribution_sample/`.
