# Schema — verified against the files on 2026-11-06

**Binding spec:** `TASK.md` @ `52015cd` (Turn Coverage and the Cut Card).
Every number below was **read from the file** in the audit run that produced
`outputs/audit/schema_verified.json` and `outputs/audit/schema_compare.csv`; nothing
is carried over from the spec's description of the data. Where the spec and the
files disagree, the disagreement is stated in §6.

Old-task schema notes were archived to
`archive/legacy-2026-11-06/notes/` on 2026-11-06.

---

## 1. Files (paths relative to `data/raw/`, which is a symlink to the USB)

| key | file | bytes | rows | cols |
|---|---|---:|---:|---:|
| combine_tracking | `combine_tracking.csv` | 83,788,174 | 463,189 | 14 |
| combine_results | `combine_results.csv` | 41,020 | 510 | 18 |
| players | `players.csv` | 47,301 | 510 | 10 |
| games | `games.csv` | 43,174 | 1,002 | 7 |
| player_play | `player_play.csv` | 146,879,042 | 314,197 | 64 |
| player_career_successes | `player_career_successes.csv` | 13,801 | 510 | 9 |
| game_tracking (**sample**) | `game_tracking_2023_sample.csv` | 93,739,461 | 1,048,575 | 12 |
| game_tracking (**full**) | `game_tracking.csv` | — | — | **absent on this host** |

`games.csv` covers **three seasons, 2023–2025, 334 games each**. The tracking sample
is **2023 only**.

## 2. Combine tracking — columns, dtypes, ranges

`draft_year BIGINT, event_id VARCHAR, nfl_id BIGINT, entity_type VARCHAR,
time TIMESTAMP, drill_type VARCHAR, drill_name VARCHAR, attempt BIGINT,
x DOUBLE, y DOUBLE, s DOUBLE, a DOUBLE, dis DOUBLE, dir DOUBLE`

* `entity_type` ∈ {`PLAYER` (431,094 rows, 510 players), `BALL` (32,095 rows)}.
  **BALL rows reuse the player's `nfl_id` and duplicate the frame timeline**; they
  are excluded everywhere (`entity_type='PLAYER'`) and were the source of an
  apparent 32,095 duplicate-frame count in the first audit run.
* `x`, `y` in **yards**; the 40-yard dash runs from x≈10.7 to x≈50.0, i.e. ~40 yd.
* `s` in **yd/s** (max 25.79 — a deceleration-heavy return leg, not a sprint), `a` in
  **yd/s²** (max 44.6).
* `dir` in **degrees, 0–360, 0 = +y (up-field), increasing clockwise**. Verified:
  on the 40-yard dash running along +x the provided `dir` is ~90°, and
  `atan2(vx, vy)` is ~90°. Measured offset over 3,445 moving frames:
  **0.00° (circular SD 0.17°, 100% of frames within 5°)**.
* `dis`: see §5 — **not** a distance in yards.
* `time`: 10 Hz, **424,784 player frame steps, every one exactly 0.1 s**, 0 off-grid,
  0 duplicate player frames.
* Attempt key is `(nfl_id, event_id, attempt)` — **`event_id` is part of the key**.
  `attempt` restarts per event; grouping without `event_id` silently merges
  different sessions of the same player and corrupts every derivative.
* Frames per attempt: median 51, p05/p95 24/145 (across all drills).
* `draft_year` 2023–2025; 510 players, positions DB 122, OL 121, DL 118, WR 107, TE 42.

## 3. Game tracking (sample) — columns, dtypes, ranges

`game_id BIGINT, play_id BIGINT, nfl_id BIGINT, time TIMESTAMP, x DOUBLE, y DOUBLE,
s DOUBLE, a DOUBLE, dis DOUBLE, o DOUBLE, dir DOUBLE, event VARCHAR`

* **`o` (orientation) exists in game data and not in Combine data** — as the spec says.
* `dir`, `o` in degrees 0–360. `x` 0–120 yd (length), `y` 0–53.3 yd (width);
  `play_direction` in `player_play` is `left`/`right`, so the coordinate system does
  not rotate with the offence — `x` increases toward one end line regardless of who
  is on offence.
* 10 Hz: **1,033,963 frame steps, 1 off-grid (float rounding), 0 duplicates,
  0 gaps > 0.2 s**.
* **How the sample was drawn:** 1,048,575 rows = 333 games, 4,228 plays, 14,612
  distinct `(game, play, player)` pairs, 154 players. That is ~1/40th of a season's
  tracking rows: the median play has **2 players** tracked (p05 1, p95 6), and a
  play-player pair has a median of 69 frames. It is a **row/player sample within
  play**, not a set of complete games.
* What it therefore **cannot** exercise: full participation per play, opponent
  interactions, per-player turn counts at season scale, reliability, or any
  Combine-to-game correlation. Those are `PENDING FULL RUN`.

## 4. Supporting files

* `combine_results.csv`: 510 rows, 18 cols, `combine_position` ∈ {DB, OL, DL, WR, TE},
  height in inches, weight in lb, timing drills in seconds (`NA` when not run).
* `players.csv`: 510 rows; `nfl_position` uses game position labels
  (DE/DT/NT/EDGE/CB/SS/FS/T/G/C/WR/TE/OLB/ILB/MLB/RB/FB/DB).
* `player_play.csv`: 314,197 rows × 64 cols. **Median 2 players per play**
  (p05 1, p95 6) — it is not a participation table, only players with a recorded
  role/event in that play. Fields used: `route_ran` (**VARCHAR route name**, not a
  boolean: GO/HITCH/CROSS/…), `player_get_off` (double, seconds),
  `coverage_assignment` (VARCHAR, sentinel `NO_ASSIGNMENT`), `quick_pressure`,
  `unblocked_pressure`, `time_to_pressure`, `pressure_allowed`,
  `time_to_pressure_allowed`, `dropback_duration`, `blitzing`, `target`.
  `sack_allowed` is **DOUBLE**, not a boolean.
* `lined_up_position` gives `EDGE` / `INTERIOR_LINE` for DL — the edge-vs-interior
  split the coverage map needs exists.

## 5. `dis` is not a distance in yards — a blocker

Tested, not assumed (`outputs/audit/dis_reconstruction.csv`):

* For a 40-yard dash attempt, `dis` ends at **1.0000**; for the shuttle ~0.468;
  for the 3-cone ~0.639. These look like fractions of a fixed normaliser.
* `30*sqrt(dis)` is an **exact integer in 47.5% of all attempts** (68.9% for the
  40-yard dash), which is what a stored square of a **whole-yard accumulator**
  divided by 30 would produce: `dis = (whole_yards / 30)²`.
* But the implied distance is **not** the path length: the median ratio
  path/implied ranges **0.61–3.10 across drills** (1.36 overall), because tracking
  includes movement before and after the drill and the accumulator does not.
* Consequence: `dis` **cannot** be used as a yardage cross-check, and the "Combine
  `dis` under-counts" claim (a participant repo, unverified) **can be neither
  confirmed nor refuted** until the vendor documents the field. Distance is derived
  from x/y. Logged as blocker **B2** in `notes/blockers.md`.

## 6. Mismatches between the spec's description and the files

| # | Spec claim | What the files show | Consequence |
|---|---|---|---|
| 1 | "Combine files contain only x, y, dir and dis … **no speed, acceleration or orientation**" | `s` and `a` **exist and are populated**; `o` is absent | **Refuted for `s`/`a`.** They are used as *cross-checks only* (`dir`, `dis`, `s`, `a` are never inputs to the primary definitions). `o` absence is confirmed. |
| 2 | "`dir` angle convention to be verified" | 0 = +y, clockwise, 0–360, offset vs derived heading 0.00° | Verified. |
| 3 | "attempt" as a key | `attempt` restarts per `event_id` | Key is `(nfl_id, event_id, attempt)`. |
| 4 | "player_play" as a snap table | Only players with a recorded role/event (median 2 per play) | Snap counts per player are **not** participation counts; stated as a limitation. |
| 5 | drill geometry "provided in documentation" | No file in `data/raw` documents cone positions | Cone positions are **estimated** from detection density (item d); only the cone *count* per drill is a designed input. |

## 7. Units and conventions used downstream

| quantity | unit | source |
|---|---|---|
| x, y | yards | file |
| dir, o | degrees, 0 = +y, clockwise, mod 360 | verified |
| speed (derived) | yd/s | smoothed x/y |
| acceleration (derived) | yd/s² | derivative of derived speed |
| angular rate | deg/s | derivative of unwrapped heading |
| lateral acceleration | yd/s² | v · ω |
| turn angle | degrees, signed, cumulative over the turn | derived |
| entry/exit speed | yd/s at the approach / exit speed maximum | derived |

## 8. Runtime assertions

`src/bdb27/schema.py` exposes `assert_columns`, `assert_angle_range` and
`SchemaError`; `tests/test_schema_contract.py` pins the column sets above, the
`s`/`a` finding, the `o` asymmetry, `route_ran` being a route name, and the 0–360
angle range on real slices of every file.
