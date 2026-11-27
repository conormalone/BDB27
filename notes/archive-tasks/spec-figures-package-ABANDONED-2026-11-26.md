# Spec — split `bdb27/figures.py` into one module per figure

**Status:** binding for this refactor cycle (D224).
**Author:** PM, 2026-11-26.
**Baseline:** `9554769`, clean tree.
**Conor's instruction (verbatim):** "Ok, id like a file for each plot if that's ok, whats a
good way of organising the data engineering stuff?"

The second half of that sentence (data-engineering layout) is **out of scope** for this cycle
and is answered elsewhere. This cycle delivers **one file per plot** only.

**Hard constraint: the figures must not change.** Same pixels, same CSV bytes, same drew-text
JSON, same manifest fields. This is a structural refactor, not a redraw. Anyone reviewing this
must be able to show that no line of drawing, labelling or aggregation logic changed.

---

## 1. Facts established before the spec

- `src/bdb27/figures.py` at `9554769`: **12,704 lines**, **402 top-level names**
  (functions, classes and module-level constants).
- 21 figures are drawn; **20** have a `plot_*` builder. Figure 6 ("Measurement limits") has
  no `plot_*` builder — its drawing entry point is `build_measurement_limits`. It still gets
  its own module.
- `src/06_figures.py` calls `figures.*` in 21 labelled blocks (`# ---- figure N:`).
- 11 test files import `bdb27.figures`; none import a submodule directly. Three tests read the
  **source path** `src/bdb27/figures.py` (see §6).
- `src/bdb27/figure_text_scan.py` resolves render entry points by name from `bdb27.figures`
  (`RENDER_FUNCTIONS`); it needs no change if the package keeps the same import surface.
- `src/bdb27/full_run.py` does **not** include `figures` in `DETECTION_FILES`; its only
  reference is a comment. Do not edit it (data-engineering module, separate cycle).

## 2. Target layout

Replace the single module with a package:

```
src/bdb27/figures/
    __init__.py                 # the public surface (see §4)
    shared.py                   # cross-figure machinery (see §3)
    fig01_the_hole.py
    fig02_drill_coverage.py
    fig03_before_after.py
    fig04_drill_schematic.py
    fig05_all_positions.py
    fig06_measurement_limits.py
    fig07_gap_honest.py
    fig08_turn_cost.py
    fig09_stopwatch.py
    fig10_both_hips.py
    fig11_shallow_sources.py
    fig12_ledger.py
    fig13_bars_of_doubt.py
    fig14_supply_composition.py
    fig15_band_only.py
    fig16_entry_exit_speed.py
    fig17_movement_family.py
    fig18_drill_angle.py
    fig19_population_shrink.py
    fig20_backpedal_route.py
    fig21_backpedal_polar.py
```

**Naming scheme.** `figNN_<slug>.py`, zero-padded, figure number as printed in the report
(1–21), slug from the figure's established name in `06_figures.py`. The module owns every
top-level name for that figure: its `plot_*`/`build_*` builder, its `render_*`, its
`load_*`, `*_table`, `*_csv_frame`, `*_manifest_payload`, drawn-text dump, private helpers
and constants.

`shared.py` owns every name reachable from **two or more** figure roots, plus module-wide
provenance/rendering/band machinery. No figure module may import another figure module
(`fig18` does **not** import `fig14`; shared names live in `shared.py`). `shared.py` may
**not** import any figure module. This forbids cycles by construction.

Nothing is renamed. Every help­er keeps its exact name and body, including leading
underscores. Only the file it lives in changes.

## 3. Ownership rule (deterministic)

Build the assignment mechanically from `9554769`; do not eyeball it.

1. Parse `src/bdb27/figures.py`; collect every top-level def/class/assign as a name with its
   AST node.
2. Build a graph: name → module-level names it references (`ast.Name` in Load context and
   `ast.Attribute` whose value is a `Name`).
3. For each figure 1–21, roots = every `figures.<name>` referenced in that figure's block of
   `src/06_figures.py`, where blocks are delimited by `# ---- figure N:`. Figure 1's block
   starts at `png_path = outdir` and ends before `drill_turns = figures.load_drill_turns`;
   figure 2 starts there and ends at the figure-3 marker. **The trailing `timer.rows/timer.detail`
   summary belongs to no figure** and must not be used as roots.
4. Take the transitive closure of each root set.
5. Partition:
   - reachable from exactly one figure → that figure's module;
   - reachable from two or more figures → `shared.py`;
   - reachable from none → the fixed orphan table in §3.1 (these are dead/test-only names
     that must still exist).
6. Every one of the 402 names must get exactly one home. The migration must print the
   partition and assert `len(partition) == 402` and disjointness. **No name may be dropped,
   duplicated, or silently left in `_impl`.**

The expected partition at `9554769` produced **35 shared names** and **10 orphans**; use this
as a cross-check, not as input:

**Shared (35):** `ALERT`, `ARTEFACT_VERSION`, `BACKPEDAL_AXIS_GREY`, `BACKPEDAL_DRILL`,
`BACKPEDAL_INK`, `BACKPEDAL_RULE_GREY`, `BARS_GREY`, `COMBINE_LABEL`, `DETECTOR_EVIDENCE`,
`DETECTOR_SOURCE_GATE`, `DETECTOR_SOURCE_NOTES`, `DRILL_DISPLAY`, `FOCUS_BAND_KEY`,
`GAME_LABEL`, `PALETTE`, `PROJECT_EDGE`, `PROJECT_FILL`, `PROVENANCE_STAMP_KEY`, `REPO_ROOT`,
`SETTING_LABELS`, `STAGE`, `STAMP`, `SUPPLY_COMPOSITION_DISPLAY`, `_deg`, `_ee_json`,
`_wrap_text`, `band_labels`, `band_table`, `focus_band`, `focus_band_index`, `on_image_stamp`,
`render`, `shallow_drill_label`, `stamp_lines`, `stamp_text`.

### 3.1 Orphan assignments (names no `06_figures` block reaches)

| Name | Module |
|---|---|
| `_caption_lines` | fig01 |
| `EXPLORATORY_LABEL` | fig05 |
| `all_positions_badges` | fig05 |
| `all_positions_dl_speed_count` | fig05 |
| `_all_positions_caption` | fig05 |
| `LATERAL_COLUMNS` | fig06 |
| `_ml_boundary_shape` | fig06 |
| `STOPWATCH_COLUMNS` | fig09 |
| `_da_caption` | fig18 |
| `BACKPEDAL_DIR_TOL_DEG` | fig20 |

If the mechanical run disagrees with this table, **stop and report the disagreement**; do not
invent a home.

## 4. `__init__.py` contract

`bdb27.figures` must expose **all 402 names** exactly as before, including private
(underscore) names, because tests and `figure_text_scan.py` use them.

- Set the backend before anything can import pyplot:
  ```python
  import matplotlib
  matplotlib.use("Agg")
  ```
- Import `shared` and all 21 figure modules.
- Merge their exported names into the package namespace and into `__all__`.
  Each module declares `__all__` listing every top-level name it defines (private names
  included). The merge must **raise on a duplicate name** — a duplicate is proof of a split
  bug.
- Do not use `from x import *`. A short explicit merge loop over a `_MODULES` tuple is
  acceptable and preferred over a 402-line import block, provided the duplicate check runs.
- `figures.__file__` will now be `.../figures/__init__.py`; §6 handles the tests that used it.

## 5. Module template

Each generated module must:

1. Start with `from __future__ import annotations`, then
   `import matplotlib` / `matplotlib.use("Agg")` / `import matplotlib.pyplot as plt`
   (same pattern as the original file) so a direct submodule import is still safe.
2. Import only what it uses: stdlib modules, `numpy as np`, `pandas as pd`, the needed
   `plotnine` names, `from bdb27 import common, kinematics, stats, turns` as required, and
   `from .shared import (...)` for shared names (private shared names listed explicitly).
3. Keep the moved source text **verbatim**: same order, same comments, same blank lines.
   Do not reformat, re-wrap, rename or "tidy" anything. The only edits inside a moved block
   are none.
4. Declare `__all__` at the end listing its names.

## 6. Tests

Tests stay where they are. They import `bdb27.figures`, which is unchanged by §4. Only the
three source-path readers change:

1. Add `tests/figure_sources.py` (a helper, not a test module):
   ```python
   FIGURES_DIR = Path(__file__).resolve().parents[1] / "src" / "bdb27" / "figures"
   def figure_source_files() -> list[Path]: return sorted(FIGURES_DIR.glob("*.py"))
   def figure_source_text() -> str:
       return "\n".join(p.read_text(encoding="utf-8") for p in figure_source_files())
   ```
2. `tests/test_focus_band.py` — replace both
   `(ROOT / "src/bdb27/figures.py").read_text(...)` calls with `figure_source_text()`.
3. `tests/test_figures_stage.py` — replace the single same-path read (the
   "too small" scan) with `figure_source_text()`.
4. `tests/test_population_shrink.py` — replace `Path(figures.__file__)` reads: parse every
   file from `figure_source_files()` with `ast` for the integer-constant guard, and search
   the concatenated `figure_source_text()` for the pin constant.
5. `tests/test_population_shrink.py::test_a_re_pinned_paste_must_still_be_the_right_population`
   patches the **package attribute** `figures.POP_SHRINK_RECEIVED_SHA256`. After the split
   `population_shrink_ladder` reads its own module's global, so patching the package no
   longer reaches it. Patch the owning module instead:
   `from bdb27.figures import fig19_population_shrink as fig19` then
   `monkeypatch.setattr(fig19, "POP_SHRINK_RECEIVED_SHA256", digest)`. This is the only
   known package-attribute monkeypatch that must follow the code; verify by running that
   file. (`figure_text_scan.py` patches render entry points on the package and keeps
   working, because `06_figures.py` looks those up through `figures.` at call time — do not
   change it.)

Add **one** new test, `tests/test_figures_layout.py`, that freezes this structure:

- the package directory contains exactly `__init__.py`, `shared.py` and
  `fig01_…` … `fig21_…` (21 files);
- the set of names exported by `bdb27.figures` equals a frozen list of the 402 names from
  `9554769`, stored as `tests/fixtures/figures_surface.json` (generate it from HEAD's AST,
  sorted);
- no `fig*_*.py` imports another `fig*_*.py`; `shared.py` imports no `fig*_*.py`.

This test is the durable guard that nothing was lost in the split.

## 7. Sequencing — five commits, green at each

Use `_impl.py` as the temporary holding module. Every commit must end with:
`python -c "import bdb27.figures"` succeeding, the surface test passing, and the focused tests
for any figure touched in that commit passing.

1. **Skeleton.** `git mv src/bdb27/figures.py src/bdb27/figures/_impl.py`; add
   `figures/__init__.py` that merges `_impl.__all__` (generate `__all__` from the 402 names);
   add `tests/figure_sources.py`, the three test edits, `tests/fixtures/figures_surface.json`
   and `tests/test_figures_layout.py` (the layout test may initially allow `_impl.py`; tighten
   it in commit 5); add this spec file. No code moves.
2. **Shared.** Move the 35 shared names to `figures/shared.py`; `_impl.py` imports them from
   `.shared`; `__init__` merges `shared` + `_impl`.
3. **Figures 1–10.** Move each figure's names to `figNN_*.py`; `_impl.py` keeps the rest.
   `__init__` merges the new modules.
4. **Figures 11–21.** Same; `_impl.py` becomes empty and is deleted. All names now live in
   `shared.py` or a figure module.
5. **Close-out.** Tighten `test_figures_layout.py` to the final directory shape; add the D224
   entry to `notes/decisions.md`. No code changes.

Commit messages: `refactor(figures): …` for 1–4, `docs: D224 …` for 5. Push once at the end.

## 8. Mechanical validation (required, in this order)

1. **Partition proof.** Script output: 402/402 names assigned, no duplicates, no orphan left
   unassigned.
2. **AST-equivalence proof.** For every top-level node in HEAD's `figures.py`, find the same
   name in the new package and assert `ast.dump()` of the node is identical. Imports and the
   generated `__all__` lists are exempt. Report `N/N identical`.
3. **Render proof.** Render sample mode at `9554769` (already done by PM: baseline hashes in
   `/tmp/baseline_hashes.txt`) and again from the refactored tree in a **scratch worktree**
   with a copied `outputs/` and the same `data/` symlinks — never overwrite committed
   artefacts. Compare SHA-256 of every `fig_*.png`, `fig_*.csv`, `fig_*.drawn_text.json`, and
   the manifest with `generated_utc` removed. **All must match.**
4. **Focused tests** after each commit; **full suite** once at the end:
   `.venv/bin/python -m pytest -q` (≈20 min on this host; do not truncate it). Report the
   exact pass/skip/fail counts.
5. `git status` clean; `git log --oneline` shows the five commits; report the pushed SHA.

## 9. Forbidden changes

- No change to any figure's PNG, CSV, drawn-text JSON or manifest fields.
- No change to `TASK.md`, `notes/report-draft.md`, `config.yaml`, `archive/`, or any
  data-engineering module (`turns.py`, `envelope.py`, `demand_coverage.py`, `full_run.py`, …).
- No rename of any helper, constant or function.
- No "cleanup" of dead code, no reordering of statements inside a moved block, no formatting.
- No new threshold that can refuse a run. (Tests may assert structure; figure code may not
  gain gates.)

## 10. Coder report (what comes back to PM)

- The partition table: module → number of names, and the 35 shared names.
- AST equivalence: N/N.
- Hash comparison: N/N identical artefacts, with the two directories named.
- Commit list + hashes; pushed SHA.
- Full-suite counts.
- Anything that could not be made to work, stated plainly.
