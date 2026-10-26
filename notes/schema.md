# Data schemas — NFL Big Data Bowl 2027 (as provided)

**Purpose:** Phase 0 record of the provided schemas, sizes and units, refreshed for the active task —
**"In-game decay of DB performance relative to the Combine ceiling"** (`TASK.md`, 2026-10-25).
**Source:** files on the mounted drive `data/raw/` (\`/mnt/project_data/conor_downloads/bdb27/\`).
**Last verified:** 2026-10-26 (this refresh) — every byte count, row count and column count below was
re-measured on the files now in `data/raw/`. Column semantics marked *audit* come from the completed DB
audit at `outputs/audit/db_decay/` (named per item); nothing is restated as fact without that path.
The prior WR/TE version of this file is archived verbatim at `notes/archive-tasks/schema-wr-security.md`.

> **Correction to the Phase-0 brief (2026-10-26).** The task brief said `combine_tracking.csv` has "12 cols";
> the file header has **14**. The 12-column file is the **game-tracking sample** (`game_id … event`). Do not
> carry the 12-column count over to the Combine file.
>
> **Correction to §8 (2026-10-11 entry).** The game-tracking sample is **not** a first-N-rows prefix. It is a
> **2²⁰−1-row cap applied to a file whose games are interleaved** — `d_sample_draw.csv` hypothesis A is
> **REFUTED** ("games=333 are interleaved in file order; a game/play prefix would cover ~1 game"). Coverage
> is **scattered** play-by-play (`d_sample_game_coverage.csv`, `coverage=scattered`), not a prefix.

---

## 1. Files present (verified 2026-10-26)

| File | Bytes | Rows (excl. header) | Cols | Role |
|---|---:|---:|---:|---|
| `combine_tracking.csv` | **83,788,174 (83.8 MB / 79.9 MiB)** | 463,189 | **14** | **Key asset** — 10 Hz optical tracking of Combine drill attempts |
| `combine_results.csv` | 41,020 | 510 | 18 | Combine anthropometrics + electronic splits / NGS scores |
| `players.csv` | 47,301 | 510 | 10 | Prospect identity, position, college, draft slot |
| `games.csv` | 43,174 | 1,002 | 7 | Game index (season, season_type, week, teams) |
| `player_play.csv` | 146,879,042 (140.1 MiB) | **314,197** | 64 | In-game player-play NGS records (2023–2025) |
| `player_career_successes.csv` | 13,801 | 510 | 9 | Career snaps/games/honours |
| `game_tracking_2023_sample.csv` | 93,739,461 (89.4 MiB) | **1,048,575** | 12 | **SAMPLE** frame-level in-game tracking (see §8) |

> The 2027 set covers **510 prospects**, combine classes **2023/2024/2025**. `nfl_id` is the join key across
> all tables. The record counts for `combine_tracking`, `player_play` and the sample were re-counted with a
> real RFC-4180 CSV parser (2026-10-26) and agree with the audit: **463,189 / 314,197 / 1,048,575**.
>
> **Row-count caveat (unchanged).** `player_play.csv` has 314,197 **records**, not 327,019 — 11,234
> `play_description` fields contain quoted embedded newlines, so `wc -l` over-counts. Never size or count
> this file by lines.

## 2. `combine_tracking.csv` (14 cols) — the key asset

### 2.1 Columns

| # | Column | Type (DuckDB) | Units / meaning |
|---:|---|---|---|
| 1 | `draft_year` | BIGINT | combine class (2023/2024/2025) |
| 2 | `event_id` | VARCHAR | one drill attempt; links frames. 2023 = opaque hash; 2024/25 = `YYYYMMDDhhmmss_<epochms>_<seq>` |
| 3 | `nfl_id` | BIGINT | prospect id |
| 4 | `entity_type` | VARCHAR | `PLAYER` or `BALL` (filter to `PLAYER` for kinematics) |
| 5 | `time` | TIMESTAMP | ISO-8601, **naive (no TZ offset)** — assumed UTC (unverified) |
| 6 | `drill_type` | VARCHAR | see §2.2 |
| 7 | `drill_name` | VARCHAR | specific sub-drill (e.g. `GAUNTLET_DRILL`) |
| 8 | `attempt` | BIGINT | attempt index — numbering unit is **class-dependent** (see §2.3) |
| 9 | `x` | DOUBLE | yards — local Combine frame; observed range **1.3 – 103.9** (`f_coordinate_frame.csv`) |
| 10 | `y` | DOUBLE | yards — local Combine frame; observed range **−7.2 – 66.28**; **not** the NFL 0–53.3 width |
| 11 | `s` | DOUBLE | speed, **yards/second**; observed 0 – 11.85 |
| 12 | `a` | DOUBLE | acceleration, **yards/second²**; observed 0 – 10.72 |
| 13 | `dis` | DOUBLE | **per-frame distance increment**, yards — a *delta since the prior frame*, never a cumulative distance. Observed 0 – 1.44. **Unreliable, see §4** |
| 14 | `dir` | DOUBLE | motion direction, degrees 0–360 |

- **Sampling rate:** median inter-frame Δt = **0.1 s → 10 Hz** (p95 = 0.1 s).
- **Rows/attempt:** mean 68.3, median 63, max 209.
- **Nulls:** none in any column (PLAYER+BALL).
- **Frame:** the **local Combine frame**, *not* the NFL field frame. Never reuse NFL-field assumptions for
  combine `x`/`y` (`notes/limitations.md`).

### 2.2 `drill_type` families (DB)

The DB audit found **12** `drill_type` values for DBs, all mapped (`a_family_summary.csv`,
`a_drill_inventory.csv`):

| Family | `drill_type` / `drill_name` values | Attempts | Rows | Use here |
|---|---|---:|---:|---|
| `straight_line` | `FORTY_YARD_DASH` (1 drill; 4 position-split inventory rows) | 189 | 9,443 | **the ceiling fit** (`v(t)=vmax(1−e^{−t/tau})`) |
| `shuffle` | `SHORT_SHUTTLE` (1 drill; 3 rows) | 35 | 1,927 | not used (cut metrics dropped, §4) |
| `backpedal` | `SKILL_DRILLS_DB`: back-pedal+90° break, back-pedal+45° transition, gauntlet (3 drills; 12 rows) | 472 | 41,001 | not used |
| `cut` | `THREE_CONE_DRILL` + `SKILL_DRILLS_DB` W / box / Teryl Austin 1 & 2 (5 drills; 19 rows) | 478 | 37,786 | not used (ICC too low) |
| `other` | `SKILL_DRILLS_DB` `LINE`, `LINE_DRILL` (2 drills; 7 rows) | 209 | 14,887 | not used |

The mapping itself lives in `config.yaml` (`db_decay.audit.drill_families`); `n_unmapped = 0`.

### 2.3 `attempt` numbering (unit is class-dependent)

The `attempt` numbering unit is **class-dependent**. For DBs the audit selected
**`H1 = (nfl_id, drill_type, drill_name, attempt)`** with **7 duplicate keys** out of 1,383 event-attempt
rows, against **595** duplicates for the coarser `(nfl_id, drill_type, attempt)` (`b_attempt_unit.csv`).
Any combine work that counts or segments attempts must use the H1 unit and report the 7 collisions
(`b_attempt_duplicates.csv`) rather than assume uniqueness.

## 3. `combine_results.csv` (18 cols)

`draft_year, nfl_id, combine_position, combine_height, combine_weight, hand_size, arm_length, wing_span, ten_yd_split, forty, vertical, broad_jump, three_cone, short_shuttle, bench_reps, ngs_athleticism_score, ngs_college_production_score, ngs_final_score`

- `combine_position` ∈ {DB, DL, OL, TE, WR} with counts **DB 122, DL 118, OL 121, TE 42, WR 107**.
- Height inches; weight lbs; hand/arm/wing inches; 40/splits/cone/shuttle seconds; vertical inches;
  broad_jump inches; bench reps.
- **Missingness (`NA`, ~64% of rows have ≥1 NA):** `three_cone` 64.7%, `bench_reps` 62.4%,
  `short_shuttle` 60.6%, `broad_jump` 17.1%, `forty` 16.9%, `ten_yd_split` 16.7%, `vertical` 14.1%
  (opted-out values). **Opt-outs are non-random** — a selection threat (`notes/limitations.md`).
- **DB-specific:** **19 of 118 DBs have no 40-yard-dash attempt** in `combine_tracking.csv`
  (`a_dbs_missing_40.csv`, listed by `nfl_id`) and therefore **cannot receive a ceiling** (the ceiling is
  fit on the dash only). Note the two counts answer different questions: `forty` in `combine_results` is
  the *result* field; `a_dbs_missing_40` is the *tracking* attempt presence, which is what the fit needs.

## 4. Units, frames and the reliability of provided `s` / `a` / `dis`  (audit item f, `f_*.csv`)

**This is the section the whole design hinges on.** Evidence table:
`outputs/audit/db_decay/f_reliability_combine.csv` (12 drill groups) and
`outputs/audit/db_decay/f_reliability_game.csv` (game tracking, n = 225,679 frames).

| Source | Quantity | r vs x/y derivative | bias | 5%-tolerance violation |
|---|---|---|---:|---:|
| Combine, 40-yd dash | `dis` vs per-frame \|Δxy\| | 0.980 | −0.048 yd | **75.6%** of frames |
| Combine, 40-yd dash | `s` vs \|Δxy\|/Δt | 0.999 | +0.083 yd/s | 15.2% |
| Combine, 40-yd dash | `a` vs d`s`/dt | 0.982 | +0.002 yd/s² | 64.0% |
| Combine, cut/shuffle/skill drills | `a` vs d`s`/dt | ≈0 (−0.10 to +0.25) | — | 69–94% |
| **Game tracking** | `dis` vs \|Δxy\| | 0.9997 | −0.0001 yd | 3.0% |
| **Game tracking** | `s` vs \|Δxy\|/Δt | 0.998 | −0.007 yd/s | 31.5% |
| **Game tracking** | **`a` vs d`s`/dt** | **0.0027** | **+1.733 yd/s²** | **88.0%** |

Conclusions recorded as facts about these files:

1. **Combine `dis` is a per-frame increment that under-counts low-speed displacement** (mean ratio |Δxy|/dis
   ≫ 1 at low speed), breaching the 5% relative tolerance on **75.6%** of 40-yd frames. Path length is
   **always** recomputed from `x`/`y`.
2. **Game `a` is not the derivative of game `s`** (r ≈ 0.003; bias +1.73 yd/s²). It is unusable as
   acceleration and is **never** used downstream (`notes/decisions.md` D55; `notes/blockers.md`,
   OPEN-WORKAROUND). Acceleration is derived from smoothed speed everywhere.
3. **Game `s` and `dis` are usable cross-checks** but are still not the primary source; the pipeline derives
   speed from `x`/`y` in both Combine and game data.
4. The **`s` violation rate** in game data (31.5%) is a *precision* difference at 10 Hz, not a definitional
   failure: `s` is the vendor's smoothed speed and disagrees with a raw finite difference on individual
   frames. Smoothing both sides is what makes them comparable; the audit reports it rather than correcting it.

**Nothing in this section is "corrected" by the audit** — it is measured and reported. Any correction happens
in the downstream stages, and is stated there.

## 5. Other tables

- **`players.csv`** (10 cols, 510 rows): `nfl_id, display_name, draft_year, nfl_position, birth_date,
  college_name, college_conference, draft_round, draft_pick_within_round, draft_overall_pick`.
  `nfl_position` counts (verified 2026-10-26): **WR 106, CB 60, G 51, T 50, DT 47, TE 42, DE 36, OLB 30,
  SS 28, FS 25, C 20, NT 5, DB 5, ILB 2, MLB 1, RB 1, FB 1**.
  → **DB population = CB 60 + SS 28 + FS 25 + DB 5 = 118**, matching audit item a
  (`a_db_coverage.csv`). Use `combine_results.combine_position` for the competition's 5 groups; use
  `nfl_position` for the DB sub-positions in this task.
  **Draft slot:** of the 118 DBs, **94 have `draft_overall_pick`, 24 are NULL** (structural "undrafted"
  level, `limitations.md` §10) — and all 24 appear in `player_play.csv`. The population is therefore
  "reached the NFL and played", not "draftees only" (`notes/rules.md` §9.1).
- **`games.csv`** (7 cols, 1,002 rows): `game_id, game_key, season, season_type, week, home_team_abbr,
  visitor_team_abbr`. Per-season composition (verified): **REG 272, POST 13, PRE 49** for each of 2023,
  2024, 2025. Season scope is **REG + POST** (D49); **preseason rows are excluded** — they are present in
  the file and in `player_play`, so the exclusion must be an explicit filter, not an accident.
- **`player_career_successes.csv`** (9 cols, 510 rows): career offensive/defensive/special-teams snaps,
  games active/started, All-Pro/Pro-Bowl counts. Not used by the DB-decay models (it is a career outcome,
  hence post-treatment for this question).

## 6. Reading rule (important)

These CSVs encode missing values as the literal token **`"NA"`**. DuckDB `read_csv_auto` keeps it as a
string unless told otherwise, which silently hides missingness. All conversions in `src/common.py` pass
**`nullstr=['NA','']`** so `NA` → SQL NULL. (A plain `pandas.read_csv` needs `na_values=['NA','']`; the
keyword is `na_values`, not `nullstr` — a silent-NaN trap when hand-checking numbers.)

## 7. Game-side fields — `player_play.csv` (64 cols, 314,197 records)

The 64-column list is the file header. Grouped by role for this task
(non-null rates for **DB rows** from `d_field_availability.csv`, n = 84,233 DB rows):

**Context / design controls**

| Column | DB non-null | Use |
|---|---:|---|
| `game_id, play_id, nfl_id` | 100% | keys |
| `team_abbr, possession_team` | 100% | side of the ball |
| `quarter` | 100% | control; garbage-time rule |
| `game_clock` | 100% | control; rest/seconds-since-rest derivation |
| `down`, `yards_to_go` | 100% | controls |
| `pre_snap_home_score`, `pre_snap_visitor_score` | 100% | score-margin band; garbage time |
| `play_direction`, `yardline_side`, `yardline_number` | 100% | field orientation for alignment |
| `play_nullified_by_penalty` | 100% | exclusion rule |
| `play_description` | 100% | human-readable cross-check (embedded newlines) |
| `lined_up_position` | ~99.5% | **alignment source**: CB / SLOT_CB / HIGH_SAFETY / BOX_SAFETY / OLB / EDGE / MLB / INTERIOR_LINE / NULL (`c_alignment_labels.csv`) |
| `team_coverage_man_zone`, `team_coverage_type` | 86.2% | man/zone control |
| `coverage_assignment`, `coverage_assignment_at_snap` | 49.6% / 49.2% | coverage context (thin; descriptive) |
| `pass_result`, `pass_length` | 62.4% / 54.6% | play type / depth |
| `dropback_duration` | **0%** | **not on DB rows** — only on the opponent (offense) row of the same play (38.4% non-null there); join by `(game_id, play_id)` |
| `target` | 100% (True/False) | not a control for DBs; post-snap |
| `tackle`, `assist`, `caused_forced_fumble`, `tackle_for_loss` | — | max-effort subset membership (pursuit/tackle) |

**Not present in `player_play.csv`:** any `pass_forward` / `pass_arrived` **timestamp** column, ball location,
player coordinates, or a frame-level event flag. The ball-in-air window must therefore come from the
tracking file's `event` column (§8). `route_ran` is **0% non-null on DB rows** (it is an offensive-route
column; 33.5% on opponent rows) and is not used here.

**Frame-level game tracking** (`game_tracking_{2023,24,25}.csv`) is **not present** on this Pi except the
2023 **sample** (§8). Its schema (12 cols): `game_id, play_id, nfl_id, time, x, y, s, a, dis, o, dir, event`
(NFL game frame). **There is no `frame_id`** — frame order is `time` within `(game_id, play_id, nfl_id)`.
Game-side audit items c/d/e/h/i are `PENDING FULL RUN` for the full seasons.

## 8. The game-tracking SAMPLE (how it was drawn, and what it cannot do)

- `game_tracking_2023_sample.csv` = **exactly 1,048,575 data rows = 2²⁰ − 1** (1,048,576 lines incl. header)
  — a hard row cap.
- **It is not a prefix** (`d_sample_draw.csv`): the 333 games in the file are **interleaved in file order**,
  and the per-game coverage table is marked `scattered` (`d_sample_game_coverage.csv`). Only 128/333 games
  have their last play present. **197 units have < 40 frames** and there is one internal gap > 150 ms
  (`d_sample_draw.csv`, hypothesis D) — i.e. units are **truncated mid-play** and the tail of a play can be
  cut, which biases peaks **downward**.
- **Class restriction is stronger than expected:** every player in the sample has `draft_year = 2023`
  (`d_sample_draw.csv`, hypothesis C) — so the sample exercises **one** class and **one** season.
- **Players per play:** mean **1.156**, max 4; 0 plays with all 22 tracked (`d_players_per_play.csv`,
  `audit_items.json` item d). `player_play` itself averages **2.733** players/play — i.e. `player_play` is
  also not an all-22 roster table; it carries the combine-class players only (D56).
- **Sampling rate:** 1,033,962 of 1,033,963 measured frame steps are 100 ms; one is 200 ms
  (`d_sample_dt_histogram.csv`) → 10 Hz.
- **`event` vocabulary observed in the sample** (needed for the max-effort window; 97.9% of rows are NULL
  because events fire on event frames only): `ball_snap` 14,583, `pass_forward` 9,024, `tackle` 8,287,
  `first_contact` 8,180, `pass_arrived` 6,803, `pass_outcome_caught` 5,372, `handoff` 4,095,
  `pass_outcome_incomplete` 3,178, plus `qb_sack`, `interception`, `fumble`, `out_of_bounds`, `touchdown`.
  → the ball-in-air window (`pass_forward → pass_arrived`/outcome) and the pursuit/tackle window
  (`first_contact`, `tackle`) **are derivable**, but the **full-data vocabulary is unverified**
  (`PENDING FULL RUN`, TBD register `notes/prereg.md` §13).
- **What the sample cannot exercise:** same-play opponent/ball rows (so no nearest-receiver, no ball
  location, no lateral alignment), within-game substitution inference (one player per play), load
  accumulation (plays are a scattered ~11% subset), multi-season replication, and any peak-tail quantity
  (truncation + gaps bias peaks down). All such paths are `PENDING FULL RUN`
  (`d_sample_blocked_paths.csv`).

## 9. External EDP board — `GTM_BDB_EDP_Snapshots_20261016.csv` — **LEGACY (security task)**

**Not read by the DB-decay pipeline.** Retained verbatim for provenance of the archived WR/TE work (D37/D38).
**Source:** `data/raw/external/gtm/GTM_BDB_EDP_Snapshots_20261016.csv` (external data, permitted —
`notes/rules.md` §6; **not committed**). **Verified 2026-10-17.**
sha256 `4664b369e56dfcbe62de678932059695c2c5b8b128989fa5a9068994857563b4` · **3,625 data rows × 13 cols**
(the `.xlsx` twin is ignored). Dates are **`DD/MM/YYYY`**, parsed with an explicit `dateformat`. The exact
column order below is asserted on read (`src/security_features.py`) and the exposure-table schema on write
(`security.schema`).

| # | Column | Type (DuckDB) | Units / meaning |
|---:|---|---|---|
| 1 | `draft_year` | BIGINT | combine class (2023/2024/2025) |
| 2 | `name` | VARCHAR | prospect name (display only; **never** a join key) |
| 3 | `position` | VARCHAR | board position (WR/TE/CB/S/EDGE/DT/LB/OT/G/C/QB/RB/ST; one `CB/WR`) |
| 4 | `school` | VARCHAR | college |
| 5 | `type` | VARCHAR | always `All` (consensus filter) |
| 6 | `nfl_id` | BIGINT | **exact join key** (blank on 142 rows → counted, never invented) |
| 7 | `n_total` | BIGINT | mock-draft pool size at the snapshot |
| 8 | `n` | BIGINT | consensus share numerator (`n/n_total` = consensus share) |
| 9 | `edp` | DOUBLE | expected draft position (**lower = more secure**) |
| 10 | `edp_rank` | BIGINT | EDP rank |
| 11 | `pos_rank` | VARCHAR | position label + rank (e.g. `WR1`) |
| 12 | `snapshot_date` | DATE | snapshot date (parsed `DD/MM/YYYY`) |
| 13 | `snapshot_note` | VARCHAR | snapshot vocabulary — see below |

**`snapshot_note` vocabulary** (the ONLY pre-Combine exposures are the two "Pre-Combine" labels):

| `snapshot_note` | Role | Exposure? |
|---|---|---|
| `2 Weeks Pre-Combine` | trend base | **yes** (pre-Combine) |
| `1 Week Pre-Combine` | **primary exposure** `log(EDP)` | **yes** (pre-Combine) |
| `Combine Week` | — | **never** (may reflect Combine results) |
| `1 Week Post-Combine` | Analysis-A **outcome** | **never** (outcome) |
| `2 Weeks Post-Combine` | — | **never** |

## 10. Fields that are ABSENT (not merely null)

Listed so no stage silently assumes them:

| Missing thing | Consequence | Status |
|---|---|---|
| `game_tracking_2023.csv`, `game_tracking_2024.csv`, `game_tracking_2025.csv` | no full-season frame data; all frame-level scoring is sample-only | `PENDING FULL RUN` |
| ball entity / ball x,y in game tracking | no ball-in-air **distance**; ball-in-air **time window** still derivable from `event` | TBD (prereg §13) |
| opponent rows on the same play (all-22) | no nearest receiver, no co-location, no matchup-demand covariate | TBD (D56) |
| man/zone on 13.8% of DB rows | control has missingness; model needs an explicit "unknown" level | TBD |
| `dropback_duration` on DB rows (0%) | must be joined from the opponent row | TBD |
| `frame_id` in tracking | frame order = `time` within `(game_id, play_id, nfl_id)`; ties are a known edge case | handled |
| lateral (x-offset) alignment reference | outside vs slot **cannot** be derived from tracking; `lined_up_position` is the only source (92.3% agreement on safety/non-safety; the 3-class lateral variant has n=947 and 80.9% agreement) | TBD / limitation |
