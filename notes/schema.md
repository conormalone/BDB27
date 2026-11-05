# Data schema — NFL Big Data Bowl 2027 (burst vs. jump)

**Captured:** 2026-11-05 on the Raspberry Pi. Source: `data/raw` → USB
(`/mnt/project_data/conor_downloads/bdb27`). Every count below was produced by
DuckDB against the actual file, with the null marker set to `NA`. Binding spec:
`TASK.md` (commit `bcfd48b`).

This file replaces the leverage × ceiling schema note. That note is preserved at
`archive/legacy-2026-11-05/`.

---

## 1. File inventory

| file | bytes | parsed rows | physical lines | note |
|---|---:|---:|---:|---|
| `games.csv` | 43,174 | 1,002 | 1,003 | 3 seasons × (272 REG + 49 PRE + 13 POST) |
| `players.csv` | 47,301 | 510 | 511 | one row per player in scope |
| `combine_results.csv` | 41,020 | 510 | 511 | same 510 `nfl_id` as `players.csv`, exactly |
| `combine_tracking.csv` | 83,788,174 | 463,189 | 463,190 | 56 drill names, 6,301 (player, attempt) pairs |
| `player_play.csv` | 146,879,042 | **314,197** | 327,020 | **12,823 embedded newlines** inside quoted `play_description` |
| `game_tracking_2023_sample.csv` | 93,739,461 | **1,048,575** | 1,048,576 | exactly 2²⁰−1 data rows — see §3 |
| `player_career_successes.csv` | 13,801 | 510 | 511 | not used by this task |

`wc -l` on `player_play.csv` overcounts by 12,823 because some
`play_description` values contain newlines inside quotes. **Never use `wc -l`
for a row count.** DuckDB's CSV reader handles the quoting correctly.

**Null marker.** Every file uses the literal `NA`. Every reader must pass
`nullstr=['NA']`. Empty string is *not* a null marker: `pass_result == ""` is a
real category, so `""` is preserved.

## 2. Columns, as read by DuckDB

### `games.csv`

| # | column | DuckDB type |
|---:|---|---|
| 1 | `game_id` | `BIGINT` |
| 2 | `game_key` | `BIGINT` |
| 3 | `season` | `BIGINT` |
| 4 | `season_type` | `VARCHAR` |
| 5 | `week` | `BIGINT` |
| 6 | `home_team_abbr` | `VARCHAR` |
| 7 | `visitor_team_abbr` | `VARCHAR` |

### `players.csv`

| # | column | DuckDB type |
|---:|---|---|
| 1 | `nfl_id` | `BIGINT` |
| 2 | `display_name` | `VARCHAR` |
| 3 | `draft_year` | `BIGINT` |
| 4 | `nfl_position` | `VARCHAR` |
| 5 | `birth_date` | `DATE` |
| 6 | `college_name` | `VARCHAR` |
| 7 | `college_conference` | `VARCHAR` |
| 8 | `draft_round` | `BIGINT` |
| 9 | `draft_pick_within_round` | `BIGINT` |
| 10 | `draft_overall_pick` | `BIGINT` |

### `combine_results.csv`

| # | column | DuckDB type |
|---:|---|---|
| 1 | `draft_year` | `BIGINT` |
| 2 | `nfl_id` | `BIGINT` |
| 3 | `combine_position` | `VARCHAR` |
| 4 | `combine_height` | `DOUBLE` |
| 5 | `combine_weight` | `BIGINT` |
| 6 | `hand_size` | `DOUBLE` |
| 7 | `arm_length` | `DOUBLE` |
| 8 | `wing_span` | `DOUBLE` |
| 9 | `ten_yd_split` | `DOUBLE` |
| 10 | `forty` | `DOUBLE` |
| 11 | `vertical` | `DOUBLE` |
| 12 | `broad_jump` | `BIGINT` |
| 13 | `three_cone` | `DOUBLE` |
| 14 | `short_shuttle` | `DOUBLE` |
| 15 | `bench_reps` | `BIGINT` |
| 16 | `ngs_athleticism_score` | `BIGINT` |
| 17 | `ngs_college_production_score` | `BIGINT` |
| 18 | `ngs_final_score` | `BIGINT` |

### `combine_tracking.csv`

| # | column | DuckDB type |
|---:|---|---|
| 1 | `draft_year` | `BIGINT` |
| 2 | `event_id` | `VARCHAR` |
| 3 | `nfl_id` | `BIGINT` |
| 4 | `entity_type` | `VARCHAR` |
| 5 | `time` | `TIMESTAMP` |
| 6 | `drill_type` | `VARCHAR` |
| 7 | `drill_name` | `VARCHAR` |
| 8 | `attempt` | `BIGINT` |
| 9 | `x` | `DOUBLE` |
| 10 | `y` | `DOUBLE` |
| 11 | `s` | `DOUBLE` |
| 12 | `a` | `DOUBLE` |
| 13 | `dis` | `DOUBLE` |
| 14 | `dir` | `DOUBLE` |

### `player_play.csv`

| # | column | DuckDB type |
|---:|---|---|
| 1 | `game_id` | `BIGINT` |
| 2 | `play_id` | `BIGINT` |
| 3 | `team_abbr` | `VARCHAR` |
| 4 | `nfl_id` | `BIGINT` |
| 5 | `lined_up_position` | `VARCHAR` |
| 6 | `play_description` | `VARCHAR` |
| 7 | `quarter` | `BIGINT` |
| 8 | `down` | `BIGINT` |
| 9 | `yards_to_go` | `BIGINT` |
| 10 | `possession_team` | `VARCHAR` |
| 11 | `yardline_side` | `VARCHAR` |
| 12 | `yardline_number` | `BIGINT` |
| 13 | `game_clock` | `TIME` |
| 14 | `pre_snap_home_score` | `BIGINT` |
| 15 | `pre_snap_visitor_score` | `BIGINT` |
| 16 | `play_direction` | `VARCHAR` |
| 17 | `pre_snap_home_team_win_probability` | `DOUBLE` |
| 18 | `pre_snap_visitor_team_win_probability` | `DOUBLE` |
| 19 | `home_team_win_probability_added` | `DOUBLE` |
| 20 | `visitor_team_win_probility_added` | `DOUBLE` |
| 21 | `expected_points_added` | `DOUBLE` |
| 22 | `expected_points` | `DOUBLE` |
| 23 | `offense_formation` | `VARCHAR` |
| 24 | `receiver_alignment` | `VARCHAR` |
| 25 | `pass_result` | `VARCHAR` |
| 26 | `pass_length` | `BIGINT` |
| 27 | `play_nullified_by_penalty` | `VARCHAR` |
| 28 | `penalty_yards` | `BIGINT` |
| 29 | `pre_penalty_yards_gained` | `BIGINT` |
| 30 | `yards_gained` | `BIGINT` |
| 31 | `team_coverage_man_zone` | `VARCHAR` |
| 32 | `team_coverage_type` | `VARCHAR` |
| 33 | `in_motion_at_ball_snap` | `BOOLEAN` |
| 34 | `target` | `BOOLEAN` |
| 35 | `rec_yards` | `BIGINT` |
| 36 | `rush_yards` | `BIGINT` |
| 37 | `yards_after_catch` | `BIGINT` |
| 38 | `fumble` | `BIGINT` |
| 39 | `fumble_lost` | `BIGINT` |
| 40 | `route_ran` | `VARCHAR` |
| 41 | `separation_at_pass_forward` | `DOUBLE` |
| 42 | `cushion` | `DOUBLE` |
| 43 | `expected_yards_after_catch` | `DOUBLE` |
| 44 | `dropback_duration` | `DOUBLE` |
| 45 | `extended_sack_allowed` | `DOUBLE` |
| 46 | `pass_rushers_encountered` | `BIGINT` |
| 47 | `peak_pressure_probability_allowed` | `DOUBLE` |
| 48 | `pressure_allowed` | `BOOLEAN` |
| 49 | `sack_allowed` | `DOUBLE` |
| 50 | `time_to_pressure_allowed` | `DOUBLE` |
| 51 | `quick_pressure` | `BOOLEAN` |
| 52 | `unblocked_pressure` | `BOOLEAN` |
| 53 | `blitzing` | `BOOLEAN` |
| 54 | `player_get_off` | `DOUBLE` |
| 55 | `sack` | `DOUBLE` |
| 56 | `tackle` | `BIGINT` |
| 57 | `assist` | `BIGINT` |
| 58 | `caused_forced_fumble` | `BIGINT` |
| 59 | `recovered_fumble` | `BOOLEAN` |
| 60 | `tackle_for_loss` | `BOOLEAN` |
| 61 | `time_to_pressure` | `DOUBLE` |
| 62 | `time_to_qb_hurry` | `DOUBLE` |
| 63 | `coverage_assignment` | `VARCHAR` |
| 64 | `coverage_assignment_at_snap` | `VARCHAR` |

### `game_tracking_2023_sample.csv`

| # | column | DuckDB type |
|---:|---|---|
| 1 | `game_id` | `BIGINT` |
| 2 | `play_id` | `BIGINT` |
| 3 | `nfl_id` | `BIGINT` |
| 4 | `time` | `TIMESTAMP` |
| 5 | `x` | `DOUBLE` |
| 6 | `y` | `DOUBLE` |
| 7 | `s` | `DOUBLE` |
| 8 | `a` | `DOUBLE` |
| 9 | `dis` | `DOUBLE` |
| 10 | `o` | `DOUBLE` |
| 11 | `dir` | `DOUBLE` |
| 12 | `event` | `VARCHAR` |

## 3. Provenance of the game-tracking sample

This is the single most important fact in the audit, so it is stated precisely.

**What it is.** `game_tracking_2023_sample.csv` has **1,048,575** parsed data
rows — exactly `2**20 - 1`. It covers **333** distinct games, all of `season
2023` (271 REG, 49 PRE, 13 POST), spanning 2023-08-04 to 2024-02-12, i.e. the
whole 2023 season including the Super Bowl.

**How it was drawn.** Not a head truncation and not a random row sample; it is a
**player-in-play sample**:

* within each `(game_id, play_id)` there are **1–4 players**, not 22
  (10,830 plays have exactly 1; 1,650 have 2; 150 have 3; 8 have 4);
* each retained player has a **complete 10 Hz series** for the play
  (median 69 frames ≈ 6.9 s; min 22, max 249);
* each game contributes **1–90 plays** (median 38) out of roughly 150, so play
  coverage within a game is also partial;
* **154 distinct `nfl_id`** appear, and the row counts are dominated by a small
  set of players (top 10 carry 20k–27k rows each).

**Which code paths it cannot exercise.**

| Cannot exercise | Why | Consequence |
|---|---|---|
| Relative geometry between two tracked players | most plays carry one player | blocker/looser interaction cannot be measured from tracking; use the `player_play` labels instead |
| Blocker assignment / blocker count from positions | as above | `pass_rushers_encountered`, `unblocked_pressure` are label-derived, not position-derived |
| Per-game aggregates for all 22 players | only a subset of plays per game | a per-game "all rushers" table is not reconstructible |
| Full-season per-player snap counts | 1–90 of ~150 plays per game | sample snap counts understate true counts by roughly an order of magnitude |
| Anything requiring the 2024 and 2025 seasons | the sample is 2023 only | season-over-season questions (exploratory Q4) are untestable here |
| The 62 games missing from `player_play` | 1,001 of 1,002 games have rows | one game has no player rows at all |

What it *can* exercise: onset detection on real 10 Hz traces, LOS/depth
derivation, `player_get_off` cross-checks, and the whole schema/unit gate. **57**
of the 196 DL analysis-set players appear in the sample, with 3–282 player-plays
each (median 68; 5,635 player-plays total). That is enough to exercise code, not
enough to estimate anything.

> Every number derived from this file is stamped `UNVALIDATED SAMPLE OUTPUT` and
> must never be quoted as a finding.

## 4. Units — verified, not assumed

| column | unit | evidence |
|---|---|---|
| `x`, `y` (both trackers) | yards | game tracking spans 0–120 with end zones; player positions reach −6.1 to 126 |
| `s` | yd/s | matches `Δposition/Δt`; max 11.85 for players |
| `a` | yd/s² | consistent with the `s` differences |
| `dis` | **yards per frame, NOT cumulative** | over a 40-yard dash `dis ∈ [0.0, 1.44]` and `10 × dis ≈ s`; cumulative distance must be rebuilt from `x`/`y` |
| `dir`, `o` | degrees | `dir` wraps at 0/360, so travel direction is estimated from the coordinate path |
| `player_get_off` | seconds | range 0.004–2.0, hard cap at 2.0 (2 rows sit exactly at the cap) |
| `time_to_pressure`, `time_to_qb_hurry`, `time_to_pressure_allowed` | seconds | 0.8–9.6 |
| `dropback_duration` | seconds | 60,803 non-null |
| `sack` | 0 / 0.5 / 1.0 | split credit, not a boolean |
| `game_clock` | clock time (`TIME` in DuckDB) | not a duration |
| `combine_height` / `arm_length` / `wing_span` | inches | |
| `combine_weight` | lb | |
| `forty`, `ten_yd_split`, `three_cone`, `short_shuttle` | seconds | |
| `vertical` / `broad_jump` | inches | |

**`entity_type` in `combine_tracking` is not all players.** 431,094 rows are
`PLAYER` and **32,095 are `BALL`**. The ball rows carry speeds up to 25.8 yd/s
and `y` beyond ±11 yd. They are a projectile, not a person. Every physical-time
measurement filters to `entity_type = 'PLAYER'`; the unit gate asserts it.

**`combine_tracking.time` is a wall-clock timestamp, not seconds.** Relative time
must be computed per attempt (`time - min(time)`), never used raw. A session that
crosses midnight would break naive subtraction; the frame index at a nominal
10 Hz is the fallback.

## 5. Mismatch log — `TASK.md` "Variables to expect" vs the actual files

`TASK.md` lists expected names from a data dictionary and instructs that they be
verified against the actual files. Result:

| expected name | exists? | where | verdict |
|---|---|---|---|
| `player_get_off` | **yes** | `player_play.col 54`, `DOUBLE` | matches TASK.md's definition ("seconds from snap until the rusher crosses the line") **empirically** (§6); **no official data dictionary was available to confirm the wording** |
| `quick_pressure` | **yes** | `player_play.col 51`, `BOOLEAN` | exists, but **null on 92.2% of DL get-off rows**. `null` is not `false`. See §7. |
| `time_to_pressure` | **yes** | `player_play.col 61`, `DOUBLE` | exists — but see `time_to_pressure_allowed` below |
| `time_to_qb_hurry` | **yes** | `player_play.col 62`, `DOUBLE` | exists |
| `sack` | **yes** | `player_play.col 55`, `DOUBLE` | 0 / 0.5 / 1.0 |
| `blitzing` | **yes** | `player_play.col 53`, `BOOLEAN` | exists |
| `unblocked_pressure` | **yes** | `player_play.col 52`, `BOOLEAN` | exists, null on 24.3% of DL get-off rows |
| `dropback_duration` | **yes** | `player_play.col 44`, `DOUBLE` | exists |
| x/y/speed/time tracking | **yes** | `game_tracking_*.csv` | 10 Hz, timestamps |
| pre-snap alignment | **yes (label)** | `player_play.lined_up_position` | a **label**, not a tracked coordinate |
| snap time | **yes (event)** | `game_tracking.event == 'ball_snap'` | explicit event in 12,606 of 12,638 sample plays (99.7%) |
| line-of-scrimmage location | **no direct column** | derived from `yardline_side` + `yardline_number` + `play_direction` | **derivable**; formula in §6; validated against tracked rusher positions |
| rusher–blocker assignments | **no** | — | **do not exist as a column.** Nearest available: `pass_rushers_encountered` (count) and `unblocked_pressure` (flag). This is a genuine gap for Phase 3's "assigned blocker(s) and blocker count". |
| Combine x/y/time per DL drill attempt | **yes** | `combine_tracking.csv` | 14 columns, `entity_type ∈ {PLAYER, BALL}` |
| stance (three-point vs stand-up) | **no** | — | **no column anywhere.** Not labelled, and not tested for inferability. See §8. |

### 5a. Position vocabulary — a real inconsistency, not a naming preference

`player_play.lined_up_position` uses `EDGE` / `INTERIOR_LINE` (plus `CB`,
`SLOT_CB`, `G`, `T`, `C`, `WR`, `SLOT_WR`, `TE`, `RB`, `FB`, `MLB`, `OLB`,
`HIGH_SAFETY`, `BOX_SAFETY`, `EXTRA_OL`). It **never** uses `DE`/`DT`/`NT`.

`players.csv.nfl_position` and `combine_results.combine_position` use a different
vocabulary (`DE`, `DT`, `NT`, `DL`, `EDGE`, `OLB`, …). **The two vocabularies do
not agree**, and the disagreement is not merely nominal:

| `lined_up_position` | `combine_position` | players |
|---|---|---:|
| `EDGE` | `DL` | 100 |
| `EDGE` | **`DB`** | **53** |
| `INTERIOR_LINE` | `DL` | 105 |
| `INTERIOR_LINE` | **`DB`** | **9** |
| `CB` | **`DL`** | **7** |
| `SLOT_CB` | **`DL`** | **25** |

Also, 440 `player_get_off` reps sit on rows labelled `CB` or `SLOT_CB`. Either
`lined_up_position` is noisy, or `combine_position` is being used loosely. **No
rule for resolving this conflict is specified in `TASK.md`, and none has been
invented.** It is logged as an open question in `notes/blockers.md` (B3). The
audit's edge/interior split uses `lined_up_position` because it is the only
in-game alignment label, and says so.

## 6. Deriving the line of scrimmage (validated)

Field coordinates are 0–120 yd with goal lines at 10 and 110. For a play:

```
LOS_x = 10 + yardline_number   if (yardline_side == possession_team and play_direction == 'right')
                              or (yardline_side != possession_team and play_direction == 'left')
        110 - yardline_number  otherwise
```

Validation (sample, `UNVALIDATED SAMPLE OUTPUT`): `|x_at_snap − LOS_x|` for DL
reps has median 0.63 yd, p95 1.24 yd, and 100% inside [0, 5] yd. A wrong formula
produces depths of tens of yards, so this is a sharp test.

**Consequence for the design:** pre-snap depth is **0–2.3 yd**, i.e. the rusher is
never more than ~1 yard off the ball. `player_get_off` is therefore dominated by
the first 1–2 yards of acceleration, which is exactly the region where a
parametric speed curve is least constrained. `TASK.md` anticipates this; it is
restated here because it constrains every downstream model.

## 7. Null semantics of the pressure labels (audit item f)

On the 43,562 DL rows that carry a `player_get_off`:

| column | non-null | null | null share |
|---|---:|---:|---:|
| `quick_pressure` | 3,378 | 40,184 | **92.2%** |
| `unblocked_pressure` | 32,977 | 10,585 | 24.3% |
| `time_to_pressure` | 3,418 | 40,144 | 92.2% |
| `time_to_qb_hurry` | 3,488 | 40,074 | 92.0% |

`quick_pressure` and `time_to_pressure` are **event fields**: they are populated
together (cross-tab: 2,334 `false`, 1,044 `true`, 40 with a time but a null
flag). A null almost certainly means "no pressure event was recorded", but that
is an inference, not a documented rule.

**Therefore `quick_pressure` cannot be used as a complete binary outcome.** A
model that treats `null` as `false` would be inventing 40,184 negatives. This is
blocker **B1** in `notes/blockers.md`.

**Label provenance.** `player_play` carries `peak_pressure_probability_allowed`,
`pressure_allowed`, `time_to_pressure_allowed` and `quick_pressure` — a family of
pressure fields sitting on the *offence* row as well as the defence row. That is
the signature of a model-derived pressure label, not a naive function of the
rusher's own tracking. There is **no row-level provenance column** to confirm it.
Logged as a threat in `notes/limitations.md`.

## 8. Stance

There is no stance column in any file. `TASK.md` Phase 1(c) asks whether stance
is "labeled (three-point vs stand-up), or inferable from pre-snap speed and
position". It is **not labelled**. Whether it is inferable has **not** been
established: doing so honestly needs a labelled subset to validate against, and
none exists in the provided data. This is recorded as PENDING, not answered.

## 9. Combine drill inventory

56 distinct `drill_name` values. The straight-line sprint from a stance is
`FORTY_YARD_DASH` (41,500 PLAYER rows, 418 players, 793 (player, attempt) pairs;
194 of those pairs are 3rd or 4th attempts). DL-only: 118 players, 177 attempts,
84 with two attempts and 9 with one.

The DL-specific drills (`SKILL_DRILLS_DL`: bag agility, hoop, club, wave, etc.)
are change-of-direction drills. None is a straight-line sprint, so none can
substitute for the 40-yard dash in a physical-time model.
