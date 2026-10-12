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
| A6 | Game-side fields the task needs are present in `player_play.csv`. | required column set ⊆ header: `route_ran, pass_length, target, separation_at_pass_forward, coverage_assignment, coverage_assignment_at_snap, team_coverage_man_zone, team_coverage_type` (hard `assert`). | `01_audit` (d) | **PASS** (header + runtime assert 2026-10-11). NOTE: `route_ran` is **categorical** (route type; NULL = no route), not a flag. |
| A7 | `nfl_id` matches Combine prospects to in-game tracking. | match rate > 0 in the study positions **in full mode** (hard `assert`); matched counts reported per position. | `01_audit` (c) | **PENDING** frame-tracking; **preview** via complete `player_play.csv`: WR 106/107, TE 38/42. |
| A8 | Enough **matched WR** (and TE) — go/no-go gate. | matched WR ≥ `audit.min_matched_wr` (assert in full mode; blocker otherwise). | `01_audit` (c) | **PENDING FULL RUN** (threshold **frozen 2026-10-12**: `min_matched_wr=60`; preview WR 106 / TE 38 clear it). |
| A9 | Enough **route snaps/player** for a stable per-player tail estimate. | median route snaps/player ≥ `audit.min_route_snaps_per_player` (assert in full mode). | `01_audit` (c) | **PENDING FULL RUN** (threshold **frozen 2026-10-12**: `min_route_snaps_per_player=100`; preview median route snaps WR 261 / TE 184 clear it). |
| A10 | Full game-tracking files match the sample's 12-column schema. | column-set equality vs config (hard `assert`) — not yet implemented. | `03_game_demand` | **PENDING** |
| A11 | `NA` is the only missing-value token (plus empty). | no residual literal `"NA"` in numeric columns after convert (hard `assert`). | `01_audit` | **PASS** (0 cells) |
| A12 | Per-feature **ICC** (reliability of the per-player Combine estimate) is high enough to keep a feature. | each retained feature's ICC ≥ `envelope.icc_min` (assert; drop-and-record otherwise). | `01_audit`/`02_combine_envelope` | **PASS for the retained set** (threshold **frozen 2026-10-12**: `icc_min=0.5`): 40-yd `peak_speed` .975, `speed_at_20yd` .969, `speed_at_10yd` .883, `env_vmax` .643 retained; `env_a0` .337, `env_tau` .097 dropped. Cutting-drill features ≈ 0.1–0.3 (unreliable); `SHORT_SHUTTLE speed_at_20yd/time_to_20yd` = **UNESTIMABLE** (no repeats). Phase 2 (D29) re-derives the same ICC. |
| A13 | `draft_overall_pick`/`draft_round` NULLs are the **structural "undrafted"** category (own level), not missing data. | the pick-NULL and round-NULL sets coincide (hard `assert`). | `01_audit` | **PASS** (127/510, sets coincide) |

**Provenance of A1–A4, A11, A13:** verified on the provided data during Phase 0 of the prior task (unchanged
data). **A5** is an audit finding, retained. **A6** re-verified against the live `player_play.csv` and asserted
at runtime by `01_audit` on 2026-10-11. **A7/A8/A9** were re-touched by the Phase-1 audit (2026-10-11) with a
preview from the complete `player_play.csv`; the authoritative frame-level values remain `PENDING FULL RUN` and
the gates can only pass with the full game files **and** frozen `prereg.md` thresholds. **A12** now carries
audit ICC evidence (`01_audit` (g)).
