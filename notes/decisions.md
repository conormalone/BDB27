# Decisions log — BDB27

Append-only. Each entry: what was decided, why, and the evidence/constraint behind it. Binding spec =
`TASK.md`. **Prior (combine-fatigue) decisions D1–D25 are archived at git tag
`archive/combine-fatigue-v1`** (HEAD `fdc794c`); this log restarts fresh for the ceiling-binding task.

---

## D26 — Cycle 0: pivot to the ceiling-binding task; reset analysis, keep infra · 2026-10-11
**Decision.** Pivot the project to **"Does the Combine ceiling bind on max-effort routes? (WR primary, TE
replication)"** (`TASK.md`, superseding the combine-fatigue spec). This cycle = **repo restructure + Phase 0
setup only**; the 01–05 pipeline is **not** implemented yet.
**Kept (infra):** `src/common.py` (config loader + DuckDB/Parquet helpers), `src/README.md`,
`requirements.txt`, `.gitignore`, `research/` (prior-work scan), `data/` (symlinks + README), `notes/`,
`README.md`, `README_RUN.md`, plus the git history and the `archive/combine-fatigue-v1` tag.
**Removed (`git rm`):** the combine-fatigue analysis — `src/{00_thresholds,01_audit,02_features,
03_combine_model,03b_metric_panel,03d_warmup_phenotype,04_game_features,05_link}.py`, `src/tests/`,
`notebooks/*` (old run/build notebooks + prototype), `tools/`, `specs/`. Analysis artefacts under
`outputs/` deleted on disk (gitignored).
**Why.** The new task shares the data and the engineering discipline but not the analysis; keeping the old
scripts would confuse the run order and imply unimplemented stages.
**Evidence.** `TASK.md` (new binding spec); the archive tag preserves the prior work verbatim.
