# DL task — PM evidence log (read-only probes, 2026-10-30)

All numbers below were produced by the PM with DuckDB/pandas against
`data/raw/` on 2026-10-30, before any pipeline code existed. They are the
**expected** values the Phase 1 audit must reproduce or contradict. If the audit
disagrees, the audit is right and this file gets corrected with the query.

Environment: `/mnt/project_data/bdb27_venv/bin/python`
(duckdb 2.0.0, polars 2.1.0, pytest 9.2.0, pyarrow 25.0.0). Baseline suite
before this cycle: **159 passed**.

## 1. Cohort

| item | value |
|---|---|
| `players.csv` rows | 510 |
| DL (`nfl_position` DE/DT/NT) | **88** — DE 36, DT 47, NT 5 |
| DL with any `player_play` row | 87 |
| draft_year 2023 / 2024 / 2025 | 26 / 30 / 32 |
| `combine_position` for all DL | `DL` |

`player_play.csv` contains **497 distinct nfl_id**, not 22 per play: it is the
prospect cohort, not a full participation table. Mean 2.73 rows/play. This is
why per-play DL row counts are 1–4 and why team-level blitz cannot be derived
from `player_play` alone.

## 2. Combine coverage (DL)

| drill | rows | players |
|---|---:|---:|
| FORTY_YARD_DASH | 7,310 | **71** |
| SHORT_SHUTTLE | 1,724 | 28 |
| THREE_CONE_DRILL | 3,691 | 35 |
| SKILL_DRILLS_DL | 53,688 | 85 |
| SKILL_DRILLS_LB | 2,362 | 3 |

40-yd attempts per player: **65 players with 2, 6 with 1**. ICC(1) is therefore
computable but estimated from at most 2 attempts per player — wide CI expected.
Frames per attempt ≈ 54 (min 49, max 60), i.e. ~0.1 s sampling over ~5.4 s.

`combine_results` non-null for DL: `ten_yd_split` 80.7%, `forty` 80.7%,
`three_cone` 38.6%, `short_shuttle` 40.9%.

## 3. Game side

1002 games: per season REG 272, POST 13, PRE 49. 314,197 `player_play` rows over
114,954 plays; 45,056 plays have `dropback_duration` (constant within play,
mean 3.02 s, min 0.257, max 14.245).

### Column ownership (critical)

`dropback_duration`, `pressure_allowed`, `pass_rushers_encountered`,
`peak_pressure_probability_allowed`, `time_to_pressure_allowed`, `sack_allowed`,
`extended_sack_allowed` are **100% null on DL rows** and populated on OL rows
(G/T/C). They are play/blocker-level facts and must be joined at
`(game_id, play_id)`; using them as DL features would silently produce all-null
columns.

### Null semantics on DL rows

All DL rows (46,911): `player_get_off` 37.3% null, `time_to_pressure` 95.4%,
`time_to_qb_hurry` 95.3%, `quick_pressure` 95.4%, `sack` 46.2%,
`blitzing` 46.0%, `unblocked_pressure` 46.2%.

DL rows on dropback plays (17,481 rows / 14,917 plays / 87 players):

| field | null % | interpretation supported by evidence |
|---|---:|---|
| `time_to_pressure` | 91.45% | event absent (no pressure credited to this rusher) |
| `time_to_qb_hurry` | 91.30% | event absent; superset of `time_to_pressure` (DL dropback frame: 1,520 vs 1,494 rows, 0 violations — §7.4) |
| `quick_pressure` | 91.54% | event absent; **not** a deterministic function of ttp |
| `sack` | 0% | applicable; 0/0.5/1 = 17,269/50/162 |
| `unblocked_pressure` | 0% | applicable; True on 101 rows |
| `blitzing` | 0% | applicable; True 17,159, False 322 |

`sack`, `blitzing`, `unblocked_pressure` are null on **non-dropback** plays →
"not applicable", not missing. Strictly, 352 `sack` / 351 `unblocked_pressure`
nulls survive on out-of-frame live pass plays with no recorded dropback (§7.1);
on the dropback **analysis frame** all three are complete (0/17,481 nulls).
`time_to_pressure` etc. are null because no event was charged to that rusher →
"no event", not missing.

`quick_pressure` vs `ttp <= 2.5`: agreement 1,396/1,479 = 94.4% (83 rows
quick=False with `ttp <= 2.5`; 15 rows have `ttp` and a null `quick_pressure`).
Non-null quick ⇒ non-null ttp in every case (0 violations).

### `blitzing` is not a team blitz flag (semantics mismatch vs TASK.md)

Among DL rows where defined: True share INTERIOR_LINE 98.3%, EDGE 92.3%;
MLB 11.8%, OLB 9.6%, SLOT_CB 6.1%, CB/HIGH_SAFETY ≈0; OL 0%. Play-level: only
4.3% of plays all-True, 40.3% mixed.

Decisive test: on DL dropback rows, `blitzing == TRUE` → `player_get_off`
present 15,633/17,159; `blitzing == FALSE` → present 2/322. **`blitzing` on a DL
row means "this defender rushed the passer".** It is a per-player rush
assignment, not a team blitz indicator. Team blitz must be derived as
"rushers > 4" from tracking (`PENDING FULL RUN`), because `player_play` does not
enumerate all 11 defenders.

Consequence for the primary snap set: `blitzing == TRUE` on DL rows is a clean
rush-assignment filter (24,878 rows). It is **not** usable as the non-blitz /
blitz stratifier that TASK.md asks for.

### Pass-rush rates (blitzing TRUE rows)

| group | rows | pressures | rate |
|---|---:|---:|---:|
| all DL | 24,878 | 2,169 | **8.72%** |
| EDGE | 9,285 | 1,070 | 11.52% |
| INTERIOR_LINE | 15,555 | 1,091 | 7.01% |

`player_get_off` non-null: EDGE 95.3%, INTERIOR 89.0%.

### Two pressure accounting systems disagree

Play level, dropback plays only:

| | `pressure_allowed` False | True |
|---|---:|---:|
| no DL `time_to_pressure` | 39,571 | **4,030** |
| ≥1 DL `time_to_pressure` | 811 | 644 |

4,674 plays have `pressure_allowed`, but only 1,455 have a DL `time_to_pressure`
row (1,494 DL rows — §7.3). Two reasons, both material:
1. pressures by rushers outside the 510-prospect cohort have no DL row
   (~2,700 plays);
2. 811 plays have a DL pressure but `pressure_allowed = False` — pressure
   charged to a rusher without being "allowed" by an OL (unblocked / scheme).

The systems are **not interchangeable**. Any model must name which one it uses.

### Label provenance (Phase 1e)

No data dictionary is present in `data/raw/external/` (only a GTM snapshots
xlsx/csv), and no official dictionary text was retrievable this run (Kaggle page
is JS-only; no search provider). What the column names and behaviour support:

* `peak_pressure_probability_allowed` (DOUBLE, 1,566 distinct values,
  58,028 non-null) is a **probability**, almost certainly model-derived rather
  than observed. `pressure_allowed` (BOOLEAN) is its thresholded counterpart and
  matches it on the obvious cut.
* `time_to_pressure` / `time_to_qb_hurry` are event timings, plausibly derived
  from tracking (the pass-rush model that produces the pressure probability also
  produces a time).
* **Circularity risk is real and must be stated in the report:** if the pressure
  label is generated by a model whose inputs include rusher speed/acceleration
  and get-off, then "Combine acceleration predicts pressure" is partly
  mechanical. Mitigation available without the dictionary: (i) use
  `pressure_allowed` (blocker-side, OL-attributed) as the primary outcome and
  `time_to_pressure` as a secondary, reporting both; (ii) measure how much of
  the label is explained by *same-snap* in-game kinematics (sample tracking) and
  report that R² as an upper bound on mechanical coupling; (iii) never claim the
  Combine effect is independent of the label's inputs — say it is conditional on
  them.

The audit writes this as a finding, not a blocker: the task can proceed, but the
claim is downgraded to "predicts the provided pressure label".

## 4. Tracking sample limits (Phases 3–5)

`game_tracking_2023_sample.csv`: 1,048,575 rows (= 2^20 − 1, truncated),
333 games (2023 + one 2024 playoff game), 12,638 plays, **154 players**, 25 DL,
1,797 plays with DL rows. 12 columns; events include ball_snap 14,583,
pass_forward 9,024, first_contact 8,180, pass_arrived 6,803. Max game_id
2024021100; last games partial (min 53 rows/game).

So the sample can exercise the game-side code path, alignment, get-off
consistency and kinematics for a handful of rushers, but it cannot estimate
team blitz, blocker assignments, snap coverage, or any population quantity.
Every game-side population number is `PENDING FULL RUN`.

Full-season tracking files (`game_tracking_2023/2024/2025.csv`) are **absent
from this host** — `notes/blockers.md` records this as the only true blocker for
`--mode full`.

## 5. Missing fields (not inferable)

* No rusher–blocker assignment column in any file. Must be derived at full run
  from snap-time proximity (nearest OL within `blocker_radius_yds`) and flagged
  as a proxy.
* No team blitz column (see above).
* No pass-rush/pass-block snap count column; snaps must be counted from rows.
* No `dis` unit resolution (existing schema note stands: do not use).

## 6. Open questions for the audit to settle

1. Why 811 plays have a DL pressure but `pressure_allowed = False` — unblocked
   pressures, or a different attribution rule? (Cross-tab with
   `unblocked_pressure` and `pass_rushers_encountered`.)
2. Is `pressure_allowed` ever True on a play with no OL row? (Would prove the
   play-level join is incomplete.)
3. Does `quick_pressure` disagree with `ttp <= 2.5` systematically by
   `unblocked_pressure` or by position group?
4. Are the 6 one-attempt 40-yd players systematically slower/faster (selection
   check for ICC)?
5. Does `player_get_off` on run plays have the same distribution as on dropback
   plays (it should not be used as a pressure-only covariate)?

## 7. ERRATA (corrected 2026-10-31, D71)

The audit reproduced every headline count in §1–§4 and contradicted four
statements. The bodies above have now been **corrected in place**; this section
records what was wrong and the evidence that settled it. Every number below is
reproduced by `outputs/dl/audit/d_null_semantics.csv`,
`outputs/dl/audit/d2_unexplained_nulls.csv` and the item-e 2×2, all written by
`01_audit --mode sample`.

1. **The null boundary for `sack` / `blitzing` / `unblocked_pressure` is the
   dropback play, not "live pass play".** Every row of the snap table needs
   `censor_time = dropback_duration`, so the **dropback play is the analysis
   frame**. On it `d_null_semantics.csv` reports `null_rate_dropback = 0.0` for
   all three fields (0 nulls in 17,481 DL rows): null is not applicable, a
   value is observed, and nothing is zero-filled. The wider `player_play`
   universe also carries these fields on 7,739 / 7,854 / 7,740 live pass plays
   that have no recorded dropback; 352 `sack` / 351 `unblocked_pressure` nulls
   on those out-of-frame plays (109 after the participation rule) are a
   recorded **data-quality residual**, not a modelling question. Verdict on the
   analysis frame: **not applicable outside, observed inside** — `sack` and
   `unblocked_pressure` are therefore *not* `UNRESOLVED` for modelling. The
   `UNRESOLVED` string in the local item-d output is scored on the wider
   diagnostic frame; it never gated Part 1 and is superseded by D71.
2. **`quick_pressure` disagreement with `ttp <= 2.5` is 83 rows**, not 15.
   Agreement is 1,396/1,479 = 94.4%. The 83 are `quick == FALSE` with
   `ttp <= 2.5`; a separate 15 rows have `ttp` and a null `quick_pressure`,
   and 0 have `quick_pressure` with a null `ttp`. The old "15" conflated the
   two counts.
3. **1,455 plays have a DL `time_to_pressure` row** (1,494 rows), not 2,116.
   The 2,116 figure was mis-transcribed; it resembles the OL-side
   `time_to_pressure_allowed` count. The item-e 2×2 is unaffected
   (39,571 / 4,030 / 811 / 644 plays).
4. **On the DL dropback frame, `time_to_qb_hurry` = 1,520 rows and
   `time_to_pressure` = 1,494 rows** with 0 superset violations. The 3,879 /
   3,804 counts were all-position counts, not DL.

5. **Snap coverage is not identifiable from `player_play`.** Median share of
   team defensive plays with a row is 0.126 (0/88 players ≥ 0.90). That number
   is a *lower bound on recorded participation*, not a coverage measurement:
   the denominator counts every defensive snap, including snaps the player sat
   out. Rotation and recording omission are observationally equivalent here.
   Coverage therefore remains `PENDING_FULL_RUN` and must not be turned into a
   gate FAIL from this ratio alone.
