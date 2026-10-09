# Assumptions about the FULL data (each with a runtime assertion)

Unverified assumptions we are making about the full (non-sample) data. Each pair is asserted at runtime so
a contradiction fails loudly rather than silently corrupting results. Assertions live in the stage scripts
(`src/01_audit.py` today; `src/02_features.py` etc. as they are built).

| # | Assumption | Runtime assertion (where) |
|---|---|---|
| A1 | `combine_tracking` covers all 510 prospects' drill attempts identically in full mode (file is complete, not a sample). | `count(distinct nfl_id) == count(distinct nfl_id in combine_results)` (01_audit) |
| A2 | `time` is naive UTC and strictly non-decreasing within a session for each player. | ordering check: attempt~time agreement ≥ 0.99 (01_audit a) |
| A3 | 10 Hz sampling holds across all drills/years. | median Δt == 0.1 s (01_audit f) |
| A4 | `x,y` are yards in the local Combine frame; `s` yd/s; `a` yd/s². | 40-yd straight-line span ∈ [38, 42] yd (01_audit d/f) |
| A5 | `event_id` is 1:1 with `(nfl_id, drill_name, attempt)`. | no `event_id` with >1 `(nfl_id,attempt)` (01_audit) |
| A6 | Every prospect who ran a drill's first attempt has contiguous `attempt` numbering (gaps = lost data only). | reported; if a drill's max attempt > attempts for >X%, flag (01_audit g) |
| A7 | Combine position groups are exactly {DB, DL, OL, TE, WR}. | set equality (01_audit b) |
| A8 | Game tracking ids overlap the combine `nfl_id` set (matching is by `nfl_id`). | match_rate > 0 per group in full mode (01_audit e) |
| A9 | Full game-tracking files are `game_tracking_{2023,2024,2025}.csv` with the sample's 12-column schema. | column-set equality vs config (04_game_features) |
| A10 | `NA` is the only missing-value token (plus empty). | no residual literal `"NA"` strings in numeric columns after convert (common.to_parquet) |
| A11 | Full-mode `combine_tracking` fits in RAM (<~1 GB) on the bigger machine. | size pre-check vs `config.audit.max_combine_bytes` (Phase 2 guard) |
| A12 | Draft position (`players.draft_overall_pick`) is populated where needed as a control. | null rate reported; excluded as control if >20% null (Phase 5) |
