# Notebooks

**There is no notebook for this task yet.** The click-through interface described in
`TASK.md` (a public Kaggle notebook that runs the pipeline end to end and renders the
five figures and the writeup) **does not exist**. Nothing here should be read as one.

The previous task's notebook (`bdb27_full_run.ipynb`) was moved with the rest of that
pipeline to `archive/legacy-2026-11-06/notebooks/`. It ran a different analysis and
must not be reused.

## What will be built (next cycle, after Phases 2–6 exist)

A single notebook, `bdb27_turn_coverage.ipynb`, that:

1. runs `python src/run.py --mode full` (with `sample` as a smoke option), showing each
   stage's exit status, wall time and peak RAM from `outputs/run_log.csv`;
2. refuses to render results unless every stage's provenance is `FULL`;
3. draws the five deliverable figures from committed stage outputs — Cut Card, demand
   map with the feasible region, transfer scatter with the noise ceiling, feasibility
   by speed range, held-out outcome comparison;
4. renders the writeup (≤2,000 words working limit, **UNVERIFIED** — see
   `notes/rules.md`) from the artefacts, printing every unresolved token.

Every figure and claim in it must come from a stage output, never from a cell that
computes its own numbers.

## Until then

Run the pipeline from the shell: see `README_RUN.md`. In sample mode every artefact is
stamped `UNVALIDATED SAMPLE OUTPUT` and is for exercising code paths only.
