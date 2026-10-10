# Data schemas — NFL Big Data Bowl 2027 (as provided)

**Purpose:** Phase 0 record of the provided schemas, sizes and units.
**Source:** files on the mounted drive `data/raw/` → `/mnt/project_data/conor_downloads/bdb27/` (verified 2026-10-09). Column dtypes are as inferred by DuckDB `read_csv_auto` after mapping `NA`→NULL (see §6).

---

## 1. Files present

| File | Bytes | Rows (excl. header) | Cols | Role |
|---|---:|---:|---:|---|
| `combine_tracking.csv` | **83,788,174 (83.8 MB / 79.9 MiB)** | 463,189 | 14 | **Key asset** — 10 Hz optical tracking of combine drill attempts |
| `combine_results.csv` | 41,020 | 510 | 18 | Combine anthropometrics + electronic splits / NGS scores |
| `players.csv` | 47,301 | 510 | 10 | Prospect identity, position, college, draft slot |
| `games.csv` | 43,174 | 1,002 | 7 | Game index (season, week, teams) |
| `player_play.csv` | 146,879,042 | 327,019 | 64 | In-game player-play NGS records (2023–2025) |
| `player_career_successes.csv` | 13,801 | 510 | 9 | Career snaps/games/honours (label side) |
| `game_tracking_2023_sample.csv` | 93,739,461 | 1,048,575 | 12 | **SAMPLE** frame-level in-game tracking (see §5) |

> The 2027 set covers **510 prospects**, combine classes **2023/2024/2025**. `nfl_id` is the join key across all tables.

## 2. `combine_tracking.csv` (14 cols) — the key asset

| Column | Type (DuckDB) | Units / meaning |
|---|---|---|
| `draft_year` | BIGINT | combine class (2023/2024/2025) |
| `event_id` | VARCHAR | one drill attempt; links frames. 2023 = opaque hash; 2024/25 = `YYYYMMDDhhmmss_<epochms>_<seq>` |
| `nfl_id` | BIGINT | prospect id |
| `entity_type` | VARCHAR | `PLAYER` or `BALL` (filter to `PLAYER` for kinematics) |
| `time` | TIMESTAMP | ISO-8601, **naive (no TZ offset)** — assumed UTC (unverified) |
| `drill_type` | VARCHAR | `FORTY_YARD_DASH`, `THREE_CONE_DRILL`, `SHORT_SHUTTLE`, `SKILL_DRILLS_{WR,DB,DL,OL,TE,LB}` |
| `drill_name` | VARCHAR | specific sub-drill (e.g. `GAUNTLET_DRILL`) |
| `attempt` | BIGINT | attempt index — numbering unit is **class-dependent** (per-`drill_name` restart for 2023/2024; per-`(player, drill_type)` block counter for 2025) — **see D19 note below** |
| `x` | DOUBLE | yards — local Combine frame (x ∈ [1.3, 103.9]) |
| `y` | DOUBLE | yards — local Combine frame (y ∈ [−7.2, 66.3]); **not** the NFL 0–53.3 width |
| `s` | DOUBLE | speed, **yards/second** |
| `a` | DOUBLE | acceleration, **yards/second²** |
| `dis` | DOUBLE | distance since prior frame, yards — **unreliable, see audit (d)** |
| `dir` | DOUBLE | motion direction, degrees 0–360 |

- **Sampling rate:** median inter-frame Δt = **0.1 s → 10 Hz** (p95 = 0.1 s).
- **Rows/attempt:** mean 68.3, median 63, max 209.
- **Nulls:** none in any column (PLAYER+BALL) in this file.
- **Consistency across the table:** 6,310 distinct `event_id`; each maps 1:1 to `(nfl_id, drill_name, attempt)`.

> **Note (2026-10-10, Phase 2, D19).** The `attempt` numbering unit is **class-dependent and detected
> empirically** per `(nfl_id, drill_type)` block (decision **D19**, `notes/decisions.md`). A block is a
> **per-`drill_name` restart** unit (`attempt_unit='drill_name'`) iff it has **≥2 distinct `drill_name`s AND
> ≥ `features.attempt_restart_min_drill_names` (2) of them have `min(attempt)==1`**; otherwise it is a
> **per-`(player, drill_type)` block counter** (`attempt_unit='drill_type'`). In practice: `attempt` restarts per
> `(player, drill_name)` for **2023/2024** (e.g. 160/160 and 177/177 multi-`drill_name` blocks), but is a
> per-`(player, drill_type)` block counter for **2025** (a player ran `SKILL_DRILLS_WR` once through with attempts
> `1..17` across 17 different `drill_name`s; 0/173 multi-`drill_name` blocks restart). The detected unit is emitted
> per row as the `attempt_unit` provenance column and drives gap-imputation + `first_attempt`. See `notes/blockers.md`
> **B5 (RESOLVED)** and `notes/decisions.md` **D19**.

## 3. `combine_results.csv` (18 cols)

`draft_year, nfl_id, combine_position, combine_height, combine_weight, hand_size, arm_length, wing_span, ten_yd_split, forty, vertical, broad_jump, three_cone, short_shuttle, bench_reps, ngs_athleticism_score, ngs_college_production_score, ngs_final_score`

- `combine_position` ∈ {DB, DL, OL, TE, WR} with counts **DB 122, DL 118, OL 121, TE 42, WR 107**.
- Height inches; weight lbs; hand/arm/wing inches; 40/splits/cone/shuttle seconds; vertical inches; broad_jump inches; bench reps.
- **Missingness (`NA`, ~64% of rows have ≥1 NA):** `three_cone` 64.7%, `bench_reps` 62.4%, `short_shuttle` 60.6%, `broad_jump` 17.1%, `forty` 16.9%, `ten_yd_split` 16.7%, `vertical` 14.1% (opted-out values).

## 4. Other tables

- **`players.csv`** (10): `nfl_id, display_name, draft_year, nfl_position, birth_date, college_name, college_conference, draft_round, draft_pick_within_round, draft_overall_pick`. `nfl_position` is granular (C, CB, DB, DE, DT, …) — use `combine_results.combine_position` for the 5 competition groups.
- **`games.csv`** (7): `game_id, game_key, season, season_type, week, home_team_abbr, visitor_team_abbr`.
- **`player_play.csv`** (64): per player-play NGS context (down/distance, formation, coverage, pressure, EPA, expected points, win probability, …). Strings use `NA` for unavailable values.
- **`player_career_successes.csv`** (9): `career_offensive_snaps, career_defensive_snaps, career_special_teams_snaps, career_games_active, career_games_started, ap_all_pro_1st_team, ap_all_pro_2nd_team, pro_bowl_original_ballot`.

## 5. The game-tracking SAMPLE (how it was drawn)

- `game_tracking_2023_sample.csv` = **exactly 1,048,575 data rows = 2²⁰ − 1** (1,048,576 lines incl. header).
  The round power-of-two count strongly indicates the sample is a **first-N-rows prefix cap** of the
  2023 full file, not a random or whole-game sample. Treat it as **not representative**.
- Schema: `game_id, play_id, nfl_id, time, x, y, s, a, dis, o, dir, event` (NFL game frame: x 0–120, y 0–53.3).
- **What the sample cannot exercise:** (i) full-season game-side matching (audit **e**); (ii) the full
  set of games/players needed for `00_thresholds.py` "FULL" provenance; (iii) scale of Phase 4.
  → These are marked **PENDING FULL RUN**.
- Full-mode sources expected at `data/raw/`: `game_tracking_2023.csv`, `game_tracking_2024.csv`,
  `game_tracking_2025.csv` (**not present now**).

## 6. Reading rule (important)

These CSVs encode missing values as the literal token **`"NA"`**. DuckDB `read_csv_auto` keeps it as a
string unless told otherwise, which silently hides missingness. All conversions in `src/common.py`
pass **`nullstr=['NA','']`** so `NA` → SQL NULL. This was a discovered-and-fixed audit bug (see `decisions.md`).
