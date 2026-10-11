# Assumptions about the FULL data (each with a runtime assertion)

Unverified assumptions we make about the data. Each is to be checked **at runtime** so a contradiction fails
loudly rather than silently corrupting results. **Status: PROPOSED** — the pipeline for this task is not
implemented yet (Phase 0 only); the "Where" column names the stage that must implement each check. Until
then, treat these as the contract the later phases must satisfy.

Legend: **PASS** = holds now · **FAIL** = violated (recorded) · **INFO** = reported, not pass/fail ·
**SKIP** = not evaluable in sample mode · **PENDING** = full-mode only.

| # | Assumption | Runtime check (assertion) | Where | Current status |
|---|---|---|---|---|
| A1 | `combine_tracking` is complete (not a sample) — every `combine_results` prospect appears. | `count(DISTINCT nfl_id)` equal across both tables (hard `assert`). | `01_audit` | **PASS** (510 = 510) |
| A2 | `time` is naive UTC and non-decreasing per session. | `time` carries no TZ offset (hard `assert`; agreement vs attempt order INFO). | `01_audit` | **PASS** (naive) |
| A3 | 10 Hz sampling holds across drills/years. | median inter-frame Δt == 0.1 s (hard `assert`). | `01_audit` | **PASS** (0.1 s) |
| A4 | `x,y` are yards in the **local Combine frame** (not the NFL field frame). | combine `x∈[1.3,103.9]`, `y∈[−7.2,66.3]` bounds (hard `assert`); never treated as 120×53.3. | `01_audit` | **PASS** |
| A5 | The provided `dis` column is unreliable → recompute from `x,y`. | `dis`-sum vs `x,y`-path exceeds tolerance on a majority of attempts (INFO; drives the design). | `01_audit` (d) | **INFO** — under-counts 9–65% |
| A6 | Game-side fields the task needs are present in `player_play.csv`. | required column set ⊆ header: `route_ran, pass_length, target, separation_at_pass_forward, coverage_assignment, coverage_assignment_at_snap, team_coverage_man_zone, team_coverage_type` (hard `assert`). | `01_audit` (g) | **PASS** (header-verified 2026-10-11) |
| A7 | `nfl_id` matches Combine prospects to in-game tracking. | match rate > 0 in the study positions **in full mode** (hard `assert`); matched counts reported per position. | `01_audit` (e) | **SKIP** (sample) / full-mode only |
| A8 | Enough **matched WR** (and TE) — go/no-go gate. | matched WR ≥ `audit.min_matched_wr` (assert in full mode; blocker otherwise). | `01_audit` (e) | **PENDING FULL RUN** |
| A9 | Enough **route snaps/player** for a stable per-player tail estimate. | median route snaps/player ≥ `audit.min_route_snaps_per_player` (assert in full mode). | `01_audit` (h) | **PENDING FULL RUN** |
| A10 | Full game-tracking files match the sample's 12-column schema. | column-set equality vs config (hard `assert`) — not yet implemented. | `03_game_demand` | **PENDING** |
| A11 | `NA` is the only missing-value token (plus empty). | no residual literal `"NA"` in numeric columns after convert (hard `assert`). | `01_audit` | **PASS** (0 cells) |
| A12 | Per-feature **ICC** (reliability of the per-player Combine estimate) is high enough to keep a feature. | each retained feature's ICC ≥ `envelope.icc_min` (assert; drop-and-record otherwise). | `01_audit`/`02_combine_envelope` | **PENDING** |
| A13 | `draft_overall_pick`/`draft_round` NULLs are the **structural "undrafted"** category (own level), not missing data. | the pick-NULL and round-NULL sets coincide (hard `assert`). | `01_audit` | **PASS** (127/510, sets coincide) |

**Provenance of A1–A4, A11, A13:** verified on the provided data during Phase 0 of the prior task (unchanged
data). **A5** is an audit finding, retained. **A6** was re-verified against the live `player_play.csv` header
on 2026-10-11. **A8/A9** are the go/no-go gates and can only be answered with the full game files.
