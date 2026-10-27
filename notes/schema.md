# Data schema — NFL Big Data Bowl 2027 (leverage × ceiling task)

**Captured:** 2026-10-26 on the Raspberry Pi. Source: `data/raw` → USB
(`/mnt/project_data/conor_downloads/bdb27`). Every number below was produced by
DuckDB over the actual files; the query or `DESCRIBE` that produced it is named.
Binding spec: `TASK.md`.

## 1. File inventory

| file | bytes | parsed rows | `wc -l` | line−row delta | comment |
|---|---:|---:|---:|---:|---|
| `combine_tracking.csv` | 83,788,174 | 463,189 | 463,190 | 1 (header) | 510 players, 56 drill names, 6,301 attempts |
| `combine_results.csv` | 41,020 | 510 | 511 | 1 (header) | table-stakes drill results |
| `players.csv` | 47,301 | 510 | 511 | 1 (header) | one row per invited player |
| `games.csv` | 43,174 | 1,002 | 1,003 | 1 (header) | 3 seasons × (272 REG + 49 PRE + 13 POST) |
| `player_play.csv` | 146,879,042 | 314,197 | 327,020 | **12,823** | quoted newlines in `play_description` |
| `game_tracking_2023_sample.csv` | 93,739,461 | **1,048,575** | 1,048,576 | 1 (header) | exactly 2²⁰−1 rows: row-capped sample |
| `player_career_successes.csv` | 13,801 | 510 | 511 | 1 (header) | not used by this task |

**`wc -l` vs parsed rows.** `player_play.csv` has 327,020 physical lines but
314,197 CSV records: 12,823 records contain an embedded newline inside a quoted
`play_description`. The DuckDB CSV parser handles this correctly; `wc -l` and
any line-based tool do not. Never use `wc -l` for row counts.

**Null marker: `NA`.** Every reader must pass `nullstr=['NA']`. Without it,
DuckDB tries to parse `NA` as a number and aborts (e.g. `Could not convert string
"NA" to 'DOUBLE'` on `pre_snap_home_team_win_probability`), and a small-sample
sniff instead infers `VARCHAR` for numeric columns. Empty string is **not** a
null marker here: `pass_result == ""` is a real category (103,636 rows), so
`lc_common` only nulls `NA` and preserves `""`.

## 2. Column-level schema (actual inferred types)

Types are DuckDB types from `DESCRIBE SELECT * FROM read_csv_auto(path, nullstr=['NA'])`.
Non-null and distinct counts come from a single aggregate scan per file.

### `combine_tracking.csv` — 463,189 rows, 83,788,174 bytes

| column | dtype | non-null | distinct | unit |
|---|---|---:|---:|---|
| `draft_year` | BIGINT | 463,189 | 3 | — |
| `event_id` | VARCHAR | 463,189 | 6,310 | — |
| `nfl_id` | BIGINT | 463,189 | 510 | — |
| `entity_type` | VARCHAR | 463,189 | 2 | — |
| `time` | TIMESTAMP | 463,189 | 429,596 | timestamp (absolute wall clock) |
| `drill_type` | VARCHAR | 463,189 | 9 | — |
| `drill_name` | VARCHAR | 463,189 | 56 | — |
| `attempt` | BIGINT | 463,189 | 17 | — |
| `x` | DOUBLE | 463,189 | 178,985 | yd |
| `y` | DOUBLE | 463,189 | 179,841 | yd |
| `s` | DOUBLE | 463,189 | 182,182 | yd/s |
| `a` | DOUBLE | 463,189 | 371,251 | yd/s^2 |
| `dis` | DOUBLE | 463,189 | 179,472 | UNRESOLVED (NOT yards; do not use) |
| `dir` | DOUBLE | 463,189 | 429,809 | degrees |

### `combine_results.csv` — 510 rows, 41,020 bytes

| column | dtype | non-null | distinct | unit |
|---|---|---:|---:|---|
| `draft_year` | BIGINT | 510 | 3 | — |
| `nfl_id` | BIGINT | 510 | 510 | — |
| `combine_position` | VARCHAR | 510 | 5 | — |
| `combine_height` | DOUBLE | 510 | 95 | in |
| `combine_weight` | BIGINT | 510 | 149 | lb |
| `hand_size` | DOUBLE | 510 | 27 | in |
| `arm_length` | DOUBLE | 510 | 60 | in |
| `wing_span` | DOUBLE | 510 | 114 | in |
| `ten_yd_split` | DOUBLE | 425 | 46 | — |
| `forty` | DOUBLE | 424 | 106 | — |
| `vertical` | DOUBLE | 438 | 41 | — |
| `broad_jump` | BIGINT | 423 | 42 | — |
| `three_cone` | DOUBLE | 180 | 95 | — |
| `short_shuttle` | DOUBLE | 201 | 88 | — |
| `bench_reps` | BIGINT | 192 | 25 | — |
| `ngs_athleticism_score` | BIGINT | 510 | 48 | — |
| `ngs_college_production_score` | BIGINT | 509 | 44 | — |
| `ngs_final_score` | BIGINT | 509 | 42 | — |

### `players.csv` — 510 rows, 47,301 bytes

| column | dtype | non-null | distinct | unit |
|---|---|---:|---:|---|
| `nfl_id` | BIGINT | 510 | 510 | — |
| `display_name` | VARCHAR | 510 | 508 | — |
| `draft_year` | BIGINT | 510 | 3 | — |
| `nfl_position` | VARCHAR | 510 | 17 | — |
| `birth_date` | DATE | 510 | 441 | — |
| `college_name` | VARCHAR | 510 | 112 | — |
| `college_conference` | VARCHAR | 510 | 21 | — |
| `draft_round` | BIGINT | 383 | 7 | — |
| `draft_pick_within_round` | BIGINT | 383 | 42 | — |
| `draft_overall_pick` | BIGINT | 383 | 222 | — |

### `games.csv` — 1,002 rows, 43,174 bytes

| column | dtype | non-null | distinct | unit |
|---|---|---:|---:|---|
| `game_id` | BIGINT | 1,002 | 1,002 | — |
| `game_key` | BIGINT | 1,002 | 1,002 | — |
| `season` | BIGINT | 1,002 | 3 | — |
| `season_type` | VARCHAR | 1,002 | 3 | — |
| `week` | BIGINT | 1,002 | 23 | — |
| `home_team_abbr` | VARCHAR | 1,002 | 33 | — |
| `visitor_team_abbr` | VARCHAR | 1,002 | 33 | — |

### `player_play.csv` — 314,197 rows, 146,879,042 bytes

| column | dtype | non-null | distinct | unit |
|---|---|---:|---:|---|
| `game_id` | BIGINT | 314,197 | 1,001 | — |
| `play_id` | BIGINT | 314,197 | 4,931 | — |
| `team_abbr` | VARCHAR | 314,197 | 33 | — |
| `nfl_id` | BIGINT | 314,197 | 497 | — |
| `lined_up_position` | VARCHAR | 312,814 | 17 | — |
| `play_description` | VARCHAR | 314,197 | 114,899 | — |
| `quarter` | BIGINT | 314,197 | 5 | — |
| `down` | BIGINT | 314,197 | 5 | — |
| `yards_to_go` | BIGINT | 314,197 | 40 | — |
| `possession_team` | VARCHAR | 314,197 | 33 | — |
| `yardline_side` | VARCHAR | 310,095 | 33 | — |
| `yardline_number` | BIGINT | 314,197 | 50 | yd from own goal line |
| `game_clock` | TIME | 314,197 | 901 | time of day (mm:ss remaining) |
| `pre_snap_home_score` | BIGINT | 314,197 | 52 | — |
| `pre_snap_visitor_score` | BIGINT | 314,197 | 46 | — |
| `play_direction` | VARCHAR | 314,147 | 2 | — |
| `pre_snap_home_team_win_probability` | DOUBLE | 314,194 | 113,328 | probability 0-1 |
| `pre_snap_visitor_team_win_probability` | DOUBLE | 314,194 | 113,328 | — |
| `home_team_win_probability_added` | DOUBLE | 314,191 | 114,047 | — |
| `visitor_team_win_probility_added` | DOUBLE | 314,191 | 114,047 | — |
| `expected_points_added` | DOUBLE | 314,197 | 112,620 | — |
| `expected_points` | DOUBLE | 314,197 | 104,329 | points |
| `offense_formation` | VARCHAR | 308,862 | 7 | — |
| `receiver_alignment` | VARCHAR | 309,600 | 15 | — |
| `pass_result` | VARCHAR | 314,197 | 6 | — |
| `pass_length` | BIGINT | 184,739 | 82 | — |
| `play_nullified_by_penalty` | VARCHAR | 314,197 | 2 | — |
| `penalty_yards` | BIGINT | 20,750 | 72 | — |
| `pre_penalty_yards_gained` | BIGINT | 314,197 | 120 | — |
| `yards_gained` | BIGINT | 314,197 | 146 | — |
| `team_coverage_man_zone` | VARCHAR | 275,278 | 2 | — |
| `team_coverage_type` | VARCHAR | 275,278 | 8 | — |
| `in_motion_at_ball_snap` | BOOLEAN | 155,966 | 2 | — |
| `target` | BOOLEAN | 314,197 | 2 | — |
| `rec_yards` | BIGINT | 6,260 | 88 | — |
| `rush_yards` | BIGINT | 22 | 12 | — |
| `yards_after_catch` | BIGINT | 6,260 | 62 | — |
| `fumble` | BIGINT | 73 | 1 | — |
| `fumble_lost` | BIGINT | 73 | 2 | — |
| `route_ran` | VARCHAR | 53,308 | 12 | — |
| `separation_at_pass_forward` | DOUBLE | 46,737 | 30,351 | — |
| `cushion` | DOUBLE | 103,752 | 1,461 | — |
| `expected_yards_after_catch` | DOUBLE | 8,976 | 8,947 | — |
| `dropback_duration` | DOUBLE | 60,803 | 5,852 | — |
| `extended_sack_allowed` | DOUBLE | 60,803 | 3 | — |
| `pass_rushers_encountered` | BIGINT | 60,803 | 4 | — |
| `peak_pressure_probability_allowed` | DOUBLE | 58,028 | 1,566 | — |
| `pressure_allowed` | BOOLEAN | 60,803 | 2 | — |
| `sack_allowed` | DOUBLE | 60,803 | 3 | — |
| `time_to_pressure_allowed` | DOUBLE | 4,970 | 80 | — |
| `quick_pressure` | BOOLEAN | 3,763 | 2 | — |
| `unblocked_pressure` | BOOLEAN | 86,838 | 2 | — |
| `blitzing` | BOOLEAN | 200,727 | 2 | — |
| `player_get_off` | DOUBLE | 44,002 | 1,662 | — |
| `sack` | DOUBLE | 86,835 | 3 | — |
| `tackle` | BIGINT | 11,896 | 3 | — |
| `assist` | BIGINT | 11,896 | 2 | — |
| `caused_forced_fumble` | BIGINT | 11,896 | 2 | — |
| `recovered_fumble` | BOOLEAN | 2,214 | 2 | — |
| `tackle_for_loss` | BOOLEAN | 11,896 | 2 | — |
| `time_to_pressure` | DOUBLE | 3,804 | 78 | — |
| `time_to_qb_hurry` | DOUBLE | 3,879 | 79 | — |
| `coverage_assignment` | VARCHAR | 72,652 | 20 | — |
| `coverage_assignment_at_snap` | VARCHAR | 72,125 | 20 | — |

### `game_tracking_2023_sample.csv` — 1,048,575 rows, 93,739,461 bytes

| column | dtype | non-null | distinct | unit |
|---|---|---:|---:|---|
| `game_id` | BIGINT | 1,048,575 | 333 | — |
| `play_id` | BIGINT | 1,048,575 | 4,228 | — |
| `nfl_id` | BIGINT | 1,048,575 | 154 | — |
| `time` | TIMESTAMP | 1,048,575 | 865,631 | timestamp |
| `x` | DOUBLE | 1,048,575 | 12,098 | yd |
| `y` | DOUBLE | 1,048,575 | 6,196 | yd |
| `s` | DOUBLE | 1,048,575 | 1,058 | yd/s |
| `a` | DOUBLE | 1,048,575 | 880 | yd/s^2 |
| `dis` | DOUBLE | 1,048,575 | 144 | UNRESOLVED (do not use) |
| `o` | DOUBLE | 1,048,575 | 36,001 | yd |
| `dir` | DOUBLE | 1,048,575 | 36,001 | degrees |
| `event` | VARCHAR | 69,905 | 44 | — |

### `player_career_successes.csv` — 510 rows, 13,801 bytes

| column | dtype | non-null | distinct | unit |
|---|---|---:|---:|---|
| `nfl_id` | BIGINT | 510 | 510 | — |
| `career_offensive_snaps` | BIGINT | 510 | 195 | — |
| `career_defensive_snaps` | BIGINT | 510 | 183 | — |
| `career_special_teams_snaps` | BIGINT | 510 | 224 | — |
| `career_games_active` | BIGINT | 510 | 51 | — |
| `career_games_started` | BIGINT | 510 | 46 | — |
| `ap_all_pro_1st_team` | BIGINT | 510 | 2 | — |
| `ap_all_pro_2nd_team` | BIGINT | 510 | 2 | — |
| `pro_bowl_original_ballot` | BIGINT | 510 | 3 | — |

Not read by this task; recorded for completeness.

## 3. Units — verified vs assumed

| fact | status | evidence |
|---|---|---|
| Combine `s` is yd/s | **verified** | PLAYER max 11.854 (= elite 40-yd top speed); BALL max 25.794 |
| Combine `a` is yd/s² | **verified by scale** | PLAYER max 10.718; BALL max 44.607; consistent with v/tau |
| Combine `x`,`y` in yards | **verified** | 40-yard dash median per-attempt x-span **40.433 yd** (n=793) |
| Combine `time` absolute wall clock | **verified** | distinct per attempt; elapsed = `time - min(time)` |
| Game tracking `s` yd/s | **verified** | max 12.63, only 6 rows > 12 over 1,048,575 |
| Game tracking `x`,`y` yd | **assumed** | consistent with `s` in yd/s and ~11.9 yd median per-track displacement; not independently anchored |
| `dis` | **UNRESOLVED — not yards; do not use** | see §4 |
| `game_clock` time-of-day | **verified in-file** | values parse as `TIME` and behave as mm:ss remaining |
| `pre_snap_*_win_probability` probability | **verified** | range 0.0007595–0.99995; 3 nulls; constant within 114,953/114,954 plays |
| `expected_points` points | **verified by scale** | range −2.90 to 6.66; 0 nulls |
| combine drill results in seconds/inches/lb | **assumed (standard Combine units)** | ranges are consistent; no official data dictionary on the competition page |

## 4. The `dis` anomaly (do not use the column)

`dis` is **not** a yard-scale cumulative distance, and it is not monotone.

* Across the 793 FORTY_YARD_DASH attempts, per-attempt `max(dis)−min(dis)` has
  min 0.699, median 1.136, max 1.436, while `max(x)−min(x)` has median **40.433 yd**.
* Single attempt `nfl_id=58896, attempt=1`: `x` rises 9.72 → 51.04 yd, while
  `dis` rises 0.002 → 0.835 and then **falls** to 0.813 by the final frame.
  A cumulative odometer cannot decrease.
* In game tracking (tracks with >20 frames), median `dis` span is 0.46 while
  median x/y displacement is 11.91 yd; `corr(dis_span, xy_span) = 0.834`.

Conclusion: `dis` is some vendor-specific/smoothed quantity with an unknown
unit. The pipeline derives distance from `x`,`y` and speed/acceleration from
`s`,`a`. This is recorded in `notes/assumptions.md`; `dis` is declared in
`lc_common.EXPECTED_SCHEMAS` as `UNRESOLVED (NOT yards; do not use)`.

## 5. Combine facts that matter for the ceiling model

* `entity_type` ∈ {`PLAYER` 431,094 rows, `BALL` 32,095 rows}. **Filter
  `entity_type='PLAYER'`.** The often-quoted "max speed 25.79 / max accel 44.6"
  are BALL values; PLAYER maxima are **s = 11.854 yd/s, a = 10.718 yd/s²**.
  (This corrects the PM brief, which attributed BALL extremes to players.)
* 510 players, draft years 2023/2024/2025 (160/177/173).
* 56 distinct `drill_name` values in 9 `drill_type` groups (the brief said 62).
* FORTY_YARD_DASH: **793 attempts, 418 players**. 43 players have 1 attempt,
  375 have 2. **Attempt labels are not limited to {1,2}:** observed labels are
  1 (19,495 rows), 2 (18,144), 3 (3,154), 4 (707) — the label is a trial id,
  not a count. Keep original labels.
* Sampling is exactly 100 ms: 424,784 of 431,094 inter-frame gaps are 0.100 s;
  the remaining 6,310 are the first frame of each of the 6,301 attempts
  (partition boundaries) plus one gap. Elapsed time must be
  `time − min(time)` per `(nfl_id, drill_name, attempt)`.
* Positions (players.csv): WR 106, TE 42, CB 60, plus linemen/defenders. This
  task uses WR primary and TE replication.

## 6. Game data facts

* `player_play.csv`: 314,197 rows, 114,954 plays, 1,001 games. Rows per play:
  1 → 29,845; 2 → 29,057; 3 → 23,826; … max 11 → 1. Mean 2.73. It does **not**
  enumerate all 22 players.
* `pre_snap_home_team_win_probability`: 3 nulls, range 0.0007595–0.99995,
  constant within 114,953 of 114,954 plays. `expected_points`: 0 nulls,
  −2.90…6.66. ⇒ leverage is definable from provided fields, no external model.
  `*_added` columns are post-snap and must not enter the leverage definition.
* `route_ran` is a **string** route name (`GO`, `HITCH`, `CROSS`, `IN`, `OUT`,
  `POST`, `CORNER`, `SLANT`, `FLAT`, `SCREEN`, `WHEEL`, …), null for 260,889
  rows (non-receivers/defenders). It is not a boolean.
* `target` is boolean: 9,842 true / 304,355 false.
* `down = 0` on 961 rows; `quarter = 5` (OT) on 1,437 rows. Do not divide by
  `yards_to_go` or assume four quarters without handling these.
* **No timeouts column exists in any of the seven files** (header check:
  `head -1 data/raw/*.csv | grep -ic timeout` → 0 for all). `play_description`
  contains the word "timeout" in prose, which is not a field. This is an
  accepted gap because pre-snap WP already embeds timeout state.

## 7. How the provided sample was drawn (evidence + queries)

| fact | value | query |
|---|---|---|
| rows | 1,048,575 = 2²⁰−1 | `SELECT count(*) FROM read_csv_auto(sample)` |
| games | 333 | `count(distinct game_id)` |
| plays | 12,638 | `count(distinct (game_id, play_id))` |
| players | 154 | `count(distinct nfl_id)` |
| rows per play | 1.156 (14,612 player-play rows / 12,638 plays) | distribution below |
| tracked players per play | 1 → 10,830; 2 → 1,650; 3 → 150; 4 → 8 | `GROUP BY n` |

The exact queries behind the table (DuckDB, `S = data/raw/game_tracking_2023_sample.csv`):

```sql
SELECT count(*), count(DISTINCT game_id),
       count(DISTINCT (game_id, play_id)), count(DISTINCT nfl_id)
FROM read_csv_auto('data/raw/game_tracking_2023_sample.csv');
-- 1048575 | 333 | 12638 | 154

SELECT n, count(*) FROM (
  SELECT game_id, play_id, count(DISTINCT nfl_id) AS n
  FROM read_csv_auto('data/raw/game_tracking_2023_sample.csv')
  GROUP BY 1, 2) GROUP BY 1 ORDER BY 1;
-- 1|10830, 2|1650, 3|150, 4|8   (mean 14612/12638 = 1.156 players/play)

SELECT min(game_id), max(game_id)
FROM read_csv_auto('data/raw/game_tracking_2023_sample.csv');
-- 2023080351 | 2024021100

SELECT game_id, count(*) AS n_rows, count(DISTINCT play_id) AS n_plays,
       count(DISTINCT nfl_id) AS n_players
FROM read_csv_auto('data/raw/game_tracking_2023_sample.csv')
GROUP BY 1 ORDER BY 1 DESC LIMIT 3;
-- 2024021100|1651|20|3 ; 2024012801|622|8|1 ; 2024012800|1396|18|2

SELECT count(*), count(DISTINCT play_id), count(DISTINCT nfl_id)
FROM read_csv_auto('data/raw/game_tracking_2023_sample.csv')
WHERE game_id = 2023122410;
-- 53 | 1 | 1
```

The cap is applied after ordering by `game_id` ascending, truncating the tail:
`min(game_id)=2023080351`, `max(game_id)=2024021100`, and the last games are
partial (e.g. `2023122410` = 53 rows / 1 play / 1 player). There is also a
player cap in effect: only 154 distinct players appear across 333 games, i.e.
**1.0–1.2 tracked players per play**, nowhere near all 22.

Consequences (also in `notes/scale.md` and the audit plan):

* The sample **cannot** exercise all-22 completeness, multi-player joins,
  defender distance, per-receiver effort counts, leverage bins, matched pairs,
  or any per-receiver aggregate.
* It **can** exercise schema/parse contract, per-frame kinematics for a single
  player, event alignment, WP joins, and memory behaviour on a 94 MB file.

## 8. Verified in-file vs assumed

**Verified by query (this file):** row counts; column names and inferred dtypes;
`NA` null semantics; `entity_type` split; 100 ms sampling; PLAYER vs BALL speed
and acceleration maxima; 40-yd x-span; FORTY attempt/player counts and label
values; positions; WP/EP availability and ranges; `route_ran`/`target` types;
`down=0`/OT counts; absence of a timeouts column; sample draw facts above;
`dis` non-yard, non-monotone behaviour.

**Assumed, not verified:** game `x`,`y` units (yd); Combine drill-result units
(seconds/inches/lb); the exact meaning of `event` beyond its string values; the
full-season row counts (files absent on this host); whether full-season tracking
also has a per-play player cap (PENDING FULL RUN).
