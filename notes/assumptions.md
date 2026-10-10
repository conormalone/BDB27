# Assumptions about the FULL data (each with a runtime assertion)

Unverified assumptions we make about the data. Each is checked **at runtime** so a contradiction fails
loudly rather than silently corrupting results.

**Where the assertions live (B3 fix).** All checks are implemented in
`src/01_audit.py::verify_assumptions()` and emitted to **`outputs/audit/assumptions_check.csv`**
(`assumption_id, assumption, status, detail`; status ∈ {PASS, FAIL, INFO, SKIP, PENDING}). Hard invariants
raise `AssertionError` (fail loudly); the one *known* violation (A5 reverse, below) and purely
informational checks are recorded without aborting. **Exception: A9 is NOT yet implemented** — it is
recorded as `PENDING` (the game-tracking schema check lands in `04_game_features`, Phase 4); every other
check is implemented. *(Earlier revisions of this file claimed assertions
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
| A8 | Game tracking ids overlap the combine `nfl_id` set (matching key). | match rate > 0 **in every position group** (hard `assert` in full mode; reuses the audit (e) match table). | **SKIP** (sample) / asserted in full mode |
| A9 | Full game-tracking files are `game_tracking_{2023,2024,2025}.csv` with the sample's 12-column schema. | column-set equality vs config — **not yet implemented**; lands in `04_game_features`. | **PENDING — not yet implemented (Phase 4)** |
| A10 | `NA` is the only missing-value token (plus empty). | no residual literal `"NA"` in numeric columns after convert (hard `assert`). | **PASS** (0 cells) |
| A11 | Full-mode `combine_tracking` fits in RAM (< ~1 GB). | file size ≤ `config.audit.max_combine_bytes` (hard `assert`). | **PASS** (83.8 MB ≤ 1 GB) |
| A12 | Draft position is available as a Phase-5 control; the `draft_overall_pick`/`draft_round` NULLs are the **structural "undrafted" category**, not missing data. | hard `assert` the pick-NULL and round-NULL sets coincide (no pick-NULL/round-present rows); apply the `audit.null_exclusion_pct` rule to **non-structural** nulls only. | **INFO — structural undrafted (127/510); non-structural null = 0 → KEEP draft position as a Phase-5 control (undrafted as its own level)** |

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

`players.draft_overall_pick` is null for **127 / 510 (24.9%)** prospects and `players.draft_round` is
null for the **same 127** — the two NULL sets coincide exactly (**0** rows with a pick NULL but a round
present, or vice versa). That coincidence identifies the nulls as the **structural "undrafted"
category**, not missing data: an undrafted prospect has no draft pick by construction.

The `>20%`-null exclusion rule (`audit.null_exclusion_pct`) is therefore **reserved for non-structural
missingness only** and does **not** fire here. Draft position is **KEPT as a Phase-5 control**, with
**"undrafted" encoded as its own level** (a dummy `undrafted` flag, or a capped/ranked pick with an
explicit "undrafted" category). The runtime assertion (A12) hard-asserts that the two NULL sets coincide
and records `structural_undrafted=127, nonstructural_null=0 -> KEEP`.

*(Earlier revisions wrongly applied the raw 24.9% null rate and **excluded** draft position as a control;
that conclusion is reverted — see `notes/decisions.md` and `notes/limitations.md`.)*

## Phase-2 additions (2026-10-09)

- **A8 hardened (audit, full mode).** `01_audit.py::verify_assumptions` A8 now **hard-asserts** that the audit-(e)
  match table contains **exactly** the expected position-group set `audit.positions_of_interest`
  (`{DB,DL,OL,TE,WR}`), not merely `match_rate > 0` in every observed group. Sample-mode behaviour is unchanged
  (A8 = SKIP), so `test_audit.py` still passes.
- **Phase-2 attempt-numbering check (warning, not assertion).** `02_features.py` assumes the locked
  `features.attempt_level` unit is the level at which `attempt` numbering restarts. It **detects** a violation of
  this at runtime by comparing imputed-row volume to observed rows and emits a **WARNING** when
  `imputed / observed > features.impute_warn_share` (0.5), recording `imputed_rows_{2023,2024,2025}`,
  `imputed_share_of_observed` and `attempt_numbering_restart_warning` in `outputs/features/feature_diagnostics.csv`.
  This is a *reported* condition (the design is locked, so the rule is not silently changed) — see blocker **B5**
  and `notes/decisions.md` D18. It is **not** a hard `assert` because the correct level legitimately differs by
  draft class and the fix is a methodology decision.
- **Phase-2 hard invariants** are asserted in `src/tests/test_features.py` (schema/dtypes; imputed rows carry no
  performance/clock; `first_attempt == (attempt==1)`; `prior_load_observed_yd ≤ prior_load_yd`; no negative
  effort; group `mean(perf_z)≈0`/`sd(perf_z)≈1`; monotone `prior_load_yd` in the session order).
