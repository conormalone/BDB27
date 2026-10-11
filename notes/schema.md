# Data schemas — NFL Big Data Bowl 2027 (as provided)

**Purpose:** Phase 0 record of the provided schemas, sizes and units.
**Source:** files on the mounted drive `data/raw/` → `/mnt/project_data/conor_downloads/bdb27/`
(**verified 2026-10-09**; **re-verified 2026-10-11** for the ceiling-binding task). Dtypes are as inferred by
DuckDB `read_csv_auto` after mapping `NA`→NULL (see §6).

> Fields listed in **§7** (game side) and **§2.2** (Combine drill families) are the ones the **ceiling-binding
> task** consumes; each was **re-checked against the file header on 2026-10-11** (present ✔). No field is
> assumed without a header check.

---

## 1. Files present

| File | Bytes | Rows (excl. header) | Cols | Role |
|---|---:|---:|---:|---|
| `combine_tracking.csv` | **83,788,174 (83.8 MB / 79.9 MiB)** | 463,189 | 14 | **Key asset** — 10 Hz optical tracking of combine drill attempts |
| `combine_results.csv` | 41,020 | 510 | 18 | Combine anthropometrics + electronic splits / NGS scores |
| `players.csv` | 47,301 | 510 | 10 | Prospect identity, position, college, draft slot |
| `games.csv` | 43,174 | 1,002 | 7 | Game index (season, week, teams) |
| `player_play.csv` | 146,879,042 (140.1 MiB) | 327,019 | 64 | In-game player-play NGS records (2023–2025) |
| `player_career_successes.csv` | 13,801 | 510 | 9 | Career snaps/games/honours (label side) |
| `game_tracking_2023_sample.csv` | 93,739,461 | 1,048,575 | 12 | **SAMPLE** frame-level in-game tracking (see §5) |

> The 2027 set covers **510 prospects**, combine classes **2023/2024/2025**. `nfl_id` is the join key across
> all tables.

## 2. `combine_tracking.csv` (14 cols) — the key asset

### 2.1 Columns

| Column | Type (DuckDB) | Units / meaning |
|---|---|---|
| `draft_year` | BIGINT | combine class (2023/2024/2025) |
| `event_id` | VARCHAR | one drill attempt; links frames. 2023 = opaque hash; 2024/25 = `YYYYMMDDhhmmss_<epochms>_<seq>` |
| `nfl_id` | BIGINT | prospect id |
| `entity_type` | VARCHAR | `PLAYER` or `BALL` (filter to `PLAYER` for kinematics) |
| `time` | TIMESTAMP | ISO-8601, **naive (no TZ offset)** — assumed UTC (unverified) |
| `drill_type` | VARCHAR | see §2.2 |
| `drill_name` | VARCHAR | specific sub-drill (e.g. `GAUNTLET_DRILL`) |
| `attempt` | BIGINT | attempt index — numbering unit is **class-dependent** (see §2.3) |
| `x` | DOUBLE | yards — local Combine frame (x ∈ [1.3, 103.9]) |
| `y` | DOUBLE | yards — local Combine frame (y ∈ [−7.2, 66.3]); **not** the NFL 0–53.3 width |
| `s` | DOUBLE | speed, **yards/second** |
| `a` | DOUBLE | acceleration, **yards/second²** |
| `dis` | DOUBLE | distance since prior frame, yards — **unreliable, see §4 / audit (d)** |
| `dir` | DOUBLE | motion direction, degrees 0–360 |

- **Sampling rate:** median inter-frame Δt = **0.1 s → 10 Hz** (p95 = 0.1 s).
- **Rows/attempt:** mean 68.3, median 63, max 209.
- **Nulls:** none in any column (PLAYER+BALL) in this file.
- **Frame:** the **local Combine frame**, *not* the NFL field frame (game tracking is 120 × 53.3 yd). Never
  reuse NFL-field assumptions for combine `x`/`y` (`notes/limitations.md`).

### 2.2 `drill_type` families (ceiling-binding task)

The 9 observed `drill_type` values (header-verified 2026-10-11):

| `drill_type` | Family for this task |
|---|---|
| `FORTY_YARD_DASH` | **straight-line** → **envelope fit** `v(t)=vmax·(1−exp(−t/tau))` (`envelope.fit_drills`) |
| `THREE_CONE_DRILL` | **cutting** → cut metrics (entry/exit speed, COD deficit) |
| `SHORT_SHUTTLE` | **cutting** → cut metrics |
| `SKILL_DRILLS_{WR,DB,DL,OL,TE,LB}` | position-specific skill blocks (heterogeneous; route/technique work) |

- **Envelope** (question: player physical ceiling) is fit on the **straight-line** drill only.
  **Cutting** drills give the change-of-direction metrics.
- The skill blocks are position-specific and not simple straight-line/cutting drills — treat separately.

### 2.3 `attempt` numbering (unit is class-dependent)

The `attempt` numbering unit is **class-dependent and detected empirically** per `(nfl_id, drill_type)`
block: it restarts per `(player, drill_name)` for **2023/2024**, but is a per-`(player, drill_type)` block
counter for **2025** (e.g. one player ran `SKILL_DRILLS_WR` once through with attempts `1..17` across 17
different `drill_name`s). Any combine work that counts or segments attempts must detect the unit rather
than assume one. (Full record: archived `notes/decisions.md` at tag `archive/combine-fatigue-v1`.)

## 3. `combine_results.csv` (18 cols)

`draft_year, nfl_id, combine_position, combine_height, combine_weight, hand_size, arm_length, wing_span, ten_yd_split, forty, vertical, broad_jump, three_cone, short_shuttle, bench_reps, ngs_athleticism_score, ngs_college_production_score, ngs_final_score`

- `combine_position` ∈ {DB, DL, OL, TE, WR} with counts **DB 122, DL 118, OL 121, TE 42, WR 107**.
- Height inches; weight lbs; hand/arm/wing inches; 40/splits/cone/shuttle seconds; vertical inches;
  broad_jump inches; bench reps.
- **Missingness (`NA`, ~64% of rows have ≥1 NA):** `three_cone` 64.7%, `bench_reps` 62.4%,
  `short_shuttle` 60.6%, `broad_jump` 17.1%, `forty` 16.9%, `ten_yd_split` 16.7%, `vertical` 14.1%
  (opted-out values). **Opt-outs are non-random** — a selection threat (`notes/limitations.md`).

## 4. The provided `dis` column is unreliable

Summed `dis` under-counts the `x`/`y` path (median 9% on the 40-yd dash, 40–65% on skill drills).
**Recompute distance from `x`/`y`**; keep `dis` only as an advisory cross-check. (Audit finding d.)

## 5. Other tables

- **`players.csv`** (10): `nfl_id, display_name, draft_year, nfl_position, birth_date, college_name,
  college_conference, draft_round, draft_pick_within_round, draft_overall_pick`. `nfl_position` is granular
  (C, CB, DB, DE, DT, …) — use `combine_results.combine_position` for the 5 competition groups.
- **`games.csv`** (7): `game_id, game_key, season, season_type, week, home_team_abbr, visitor_team_abbr`.
- **`player_career_successes.csv`** (9): career offensive/defensive/special-teams snaps, games
  active/started, All-Pro/Pro-Bowl counts.

## 6. Reading rule (important)

These CSVs encode missing values as the literal token **`"NA"`**. DuckDB `read_csv_auto` keeps it as a
string unless told otherwise, which silently hides missingness. All conversions in `src/common.py` pass
**`nullstr=['NA','']`** so `NA` → SQL NULL.

## 7. Game-side fields needed by this task (header-verified ✔ 2026-10-11)

`player_play.csv` (327,019 rows, 64 cols) supplies the per-play context. **All of the following columns are
present** (verified against the header):

| Column | Used for |
|---|---|
| `route_ran` | **route snap** flag → the route-snap denominator / top-k effort sampling (Phase 3) |
| `pass_length` | deep vs. short routes (question 3: deep vs. breaking) |
| `target` | whether the route was targeted |
| `separation_at_pass_forward` | separation at the pass-forward moment |
| `coverage_assignment` | defender/coverage context per play |
| `coverage_assignment_at_snap` | coverage context at the snap |
| `team_coverage_man_zone` | man vs. zone |
| `team_coverage_type` | coverage family |

Plus the join/context keys: `game_id, play_id, nfl_id, down, yards_to_go, play_direction` (and others).

**Frame-level game tracking** (`game_tracking_{2023,24,25}.csv`) is **not present** on this Pi except the
2023 **sample** (§8). Its schema (12 cols): `game_id, play_id, nfl_id, time, x, y, s, a, dis, o, dir, event`
(NFL game frame: x 0–120, y 0–53.3 yd). Game-side audit items **c/d/e/h/i are `PENDING FULL RUN`**.

## 8. The game-tracking SAMPLE (how it was drawn)

- `game_tracking_2023_sample.csv` = **exactly 1,048,575 data rows = 2²⁰ − 1** (1,048,576 lines incl. header).
  The round power-of-two count strongly indicates the sample is a **first-N-rows prefix cap** of the
  2023 full file, not a random or whole-game sample. Treat it as **not representative**.
- **What the sample cannot exercise:** (i) full-season game-side matching (audit c/d/e/h/i); (ii) any
  full-season route-snap counts; (iii) the scale of Phase 3 (`03_game_demand`). → **`PENDING FULL RUN`**.
- Full-mode sources expected at `data/raw/`: `game_tracking_2023.csv`, `game_tracking_2024.csv`,
  `game_tracking_2025.csv` (**not present now**).
