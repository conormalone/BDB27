# Assumptions about the FULL data (each with a runtime assertion)

Unverified assumptions we make about the data. Each is checked **at runtime** so a contradiction fails
loudly rather than silently corrupting results.

**Where the assertions live (B3 fix).** All checks are implemented in
`src/01_audit.py::verify_assumptions()` and emitted to **`outputs/audit/assumptions_check.csv`**
(`assumption_id, assumption, status, detail`; status ∈ {PASS, FAIL, INFO, SKIP, PENDING}). Hard invariants
raise `AssertionError` (fail loudly); the one *known* violation (A5 reverse, below) and purely
informational checks are recorded without aborting. *(Earlier revisions of this file claimed assertions
existed while `grep assert src/` was empty — that is now fixed: `verify_assumptions` contains real
`assert` statements.)*

Legend: **PASS** = holds now · **FAIL** = violated (recorded) · **INFO** = reported, not pass/fail ·
**SKIP** = not evaluable in sample mode · **PENDING** = full-mode only.

*A non-A guard is also emitted: **S1** — sample discipline (game-tracking sample rows ≤ `audit.sample_row_count`, the 2²⁰ cap).*

| # | Assumption | Runtime check (assertion) | Current status |
|---|---|---|---|
| A1 | `combine_tracking` covers the same prospects as `combine_results` (file is complete, not a sample). | `count(DISTINCT nfl_id)` equal across both tables (hard `assert`). | **PASS** (510 = 510) |
| A2 | `time` is naive UTC and strictly non-decreasing per session; `attempt` agrees with `time`. | `time` has no TZ offset **and** attempt~time agreement ≥ 0.99 (hard `assert`). | **PASS** (naive; 0.9992) |
| A3 | 10 Hz sampling holds across drills/years. | median inter-frame Δt == 0.1 s (hard `assert`). | **PASS** (0.1 s) |
| A4 | `x,y` are yards in the local Combine frame; a 40-yd attempt spans ≈ 40 yd. | 40-yd per-attempt straight-line **median** span ∈ [38, 42] yd (hard `assert`; median is robust to frame tails — p05/p95 also logged). | **PASS** (p50 = 40.43; p05 39.54 / p95 41.28) |
| **A5** | **`event_id` is 1:1 with `(nfl_id, drill_name, attempt)`.** | **Split:** forward `event_id → one (nfl_id, drill_name, attempt)` (hard `assert`); reverse `(nfl_id, drill_name, attempt) → one event_id` (recorded). | **A5f PASS · A5r FAIL — the assumption as written is VIOLATED** |
| A6 | A numbering gap = lost data only (no re-runs hiding as new attempts). | gap share reported (INFO); per-drill share in audit (g). | **INFO** (overall gap share 0.3209) |
| A7 | Combine position groups are exactly {DB, DL, OL, TE, WR}. | set equality (hard `assert`). | **PASS** |
| A8 | Game tracking ids overlap the combine `nfl_id` set (matching key). | match rate > 0 per group in full mode. | **SKIP** (sample) / PENDING (full) |
| A9 | Full game-tracking files are `game_tracking_{2023,2024,2025}.csv` with the sample's 12-column schema. | column-set equality vs config (04_game_features). | **SKIP** (sample) / PENDING (full) |
| A10 | `NA` is the only missing-value token (plus empty). | no residual literal `"NA"` in numeric columns after convert (hard `assert`). | **PASS** (0 cells) |
| A11 | Full-mode `combine_tracking` fits in RAM (< ~1 GB). | file size ≤ `config.audit.max_combine_bytes` (hard `assert`). | **PASS** (83.8 MB ≤ 1 GB) |
| A12 | Draft position (`players.draft_overall_pick`) is populated where needed as a control. | null rate reported; **excluded as control if > 20% null**. | **INFO — 24.9% null → EXCLUDE draft position as a Phase-5 control** |

## A5 detail (the violated assumption)

`event_id → (nfl_id, drill_name, attempt)` is 1:1 (6,310/6,310), but the **reverse is not**: **9 distinct
`(nfl_id, drill_name, attempt)` tuples carry 2 `event_id`s each** (all at `attempt = 1`, the two events
minutes apart). So `schema.md` §2's claim that "each `event_id` maps 1:1 to `(nfl_id, drill_name, attempt)`"
is only true in one direction. Impact:

- **Counting the ≥3 link threshold is unaffected:** ≥3 counts are identical under `event_id` vs distinct
  `(drill_name, attempt)` slots (verified per position for both families) — see `decisions.md` D12.
- Treated as **duplicate attempt captures** to be flagged in Phase 2 (TASK.md Phase 2 sanity check:
  "flag … duplicate attempts"), not as a stop condition. Recorded as A5r **FAIL** in
  `outputs/audit/assumptions_check.csv`.

## A12 detail (control availability)

`players.draft_overall_pick` is null for **127 / 510 (24.9%)** prospects, exceeding the pre-set 20%
threshold, so **draft position is dropped as a Phase-5 control** (the spec already says "if provided").
Phase 5 keeps baseline speed + height/weight controls. Logged to `notes/limitations.md`.
