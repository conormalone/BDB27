# TASK: Does the Combine ceiling bind on max-effort routes? (WR primary, TE replication)

> **Core project task spec — binding.** Captured from Conor 2026-10-11. **This supersedes the old
> combine-fatigue task** (archived at git tag `archive/combine-fatigue-v1`, HEAD `fdc794c`). Every role and
> sub-agent working the BDB27 project follows this spec; when the spec and convenience disagree, the spec
> wins.

NFL Big Data Bowl 2027 (Kaggle), deadline **2027-01-06 23:59 UTC**.

## Goal

For **receivers who reached the NFL**, test whether each player's **physical ceiling** (estimated from
Combine tracking) predicts the **upper tail** of their in-game speed/acceleration on **max-effort routes**,
and **how close they get to it**.

Questions:

1. **Is the Combine ceiling a real bound in games** — what is the **exceedance rate** (share of in-game
   efforts that beat the ceiling)?
2. **Does the Combine→game relationship strengthen toward the upper tail** (quantile-dependent link)?
3. **Which Combine features predict closeness** on **deep vs. breaking routes**?

**Claim limits (hard).** Claims are limited to players who **reached the NFL**. **Never** claim that Combine
athleticism predicts NFL success — that angle is saturated and explicitly out of scope (see
`research/prior-work-scan.md` §1).

## Environment / engineering

- One code path for `--mode sample|full` plus `--force`. Config-driven — **no magic numbers**.
- **Never load full CSVs into pandas.** Parquet + DuckDB / lazy Polars with explicit column selection;
  process game data **per game/player**, write small summary tables.
- Downcast dtypes.
- Log **wall time + peak RAM per stage** to `outputs/run_log.csv`.
- **Checkpoint + skip finished work** unless `--force`.
- Note what the **sample can't exercise** and add a **synthetic schema-compliant set** with a **planted
  binding ceiling** if matched players are insufficient.
- Label sample outputs **`UNVALIDATED SAMPLE OUTPUT`**.

## Phases

**0. Setup.** Rules/limits → `notes/rules.md`; schemas/sizes → `notes/schema.md`; pre-registration skeleton →
`notes/prereg.md`; config skeleton → `config.yaml`.

**1. Audit (a–i).** Game-side items **c/d/e/h/i marked `PENDING FULL RUN`**. Go/no-go gate on: matched WR
count, route snaps/player, missing Combine fields, ICC.

**2. Combine envelope.** Fit `v(t) = vmax·(1 − exp(−t/tau))` on **straight-line drills** → `a0 = vmax/tau`;
model-free split / speed-at-time features; cut metrics for **cutting drills**; hierarchical shrinkage;
**≤3 features** chosen by **ICC + VIF**, fixed in the pre-registration; per-player table with SE.

**3. Game demand.** `00_thresholds.json` provenance `FULL`/`PLACEHOLDER`; route snaps via `route_ran`;
**top-k efforts per fixed route-snap count**; per-effort features; **envelope fraction**.

**4. Models.** Quantile regression at pre-registered taus, **effort-level**, **player-clustered bootstrap**;
draft position with/without; exceedance; utilization `u`; pooled vs WR/TE; fallbacks.

**5. Validation.** Player-grouped CV vs **controls-only baseline**; permutation; measurement-error
correction; sensitivity; power note; limitations.

## Deliverables / tests / handoff

- Public **Kaggle notebook + writeup** (≤5 figures; `UNVALIDATED` placeholders pre-full-run).
- Schema assertions.
- **Synthetic recovery**: planted ceiling **+ null case**.
- Edge cases.
- **Scale test** (sample concatenated ×N).
- Determinism.
- `README_RUN.md`.
- Pinned **ARM+x86** `requirements.txt`.
- `notes/{pending_full_run,assumptions,limitations}.md`.
- `.gitignore` data — commit only **code/configs/notes/synthetic fixtures**.

## Blockers — write to `notes/blockers.md`

A blocker **stops work** and must be written there. Trigger on:

- A **missing required Combine/game field**.
- **Matched receivers or route snaps below config minima**.
- **Key Combine features unreliable**.
- **Thresholds provenance not `FULL` in a full run**.
- **`notes/prereg.md` missing before a full run**.
- Any instruction **conflicting with competition rules**.

Never invent data or fields. If something is unclear/missing, stop and write it to `notes/blockers.md`.

## Layout and run order

```
/data/raw  /data/interim  /src  /notebooks  /notes  /outputs/figures

01_audit → 02_combine_envelope → 00_thresholds → 03_game_demand → 04_models → 05_validate
```
