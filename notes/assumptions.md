# Assumptions about full data — and the runtime assertion for each

Everything here is **unverified**: the Pi holds a sample, so these are beliefs
about files we have not opened. Each one has an assertion that fires at the start
of the stage that depends on it. An assumption without an assertion is a bug
waiting to happen, so the "implemented" column is part of the entry, not an
afterthought.

Legend: **LIVE** = the assertion exists and runs today.
**PLANNED** = the assertion is specified but the stage is a Phase 2–5 stub.

---

## A1 — The full tracking files are named and located as configured  · LIVE

**Assumption.** `game_tracking_2023.csv`, `game_tracking_2024.csv`,
`game_tracking_2025.csv` exist under `data/raw`.

**Assertion.** `common.require_full_tracking` fails with `FULL MODE REFUSED` if any
is missing, listing the full paths. It then parses the row count and refuses if it
does not exceed the 2²⁰−1 sample cap.

**Why it matters.** The single most likely full-run failure is running on the
sample and calling it full. The row-count gate makes that impossible.

## A2 — The full files have the same 12 columns and dtypes  · LIVE

**Assumption.** Full tracking has `game_id, play_id, nfl_id, time, x, y, s, a,
dis, o, dir, event` with the same DuckDB types as the sample.

**Assertion.** `schema.assert_schema(con, cfg, "game_tracking", mode="full")` at
stage start; raises `SchemaError` naming the offending column and both types.

**Note.** The sample may carry a *subset of rows*, but it is not evidence that the
full files carry the same *columns*. Kaggle has changed tracking schemas between
editions before.

## A3 — Full tracking is 10 Hz  · LIVE

**Assumption.** The modal frame interval is 0.1 s in every file.

**Assertion.** `schema.tracking_contract` computes the median and p99 lag per
(game, play, player) and fails if the median is more than 20 ms from 1/`onset.hz`.
Measured on the sample: median **0.100 s**, p99 **0.100 s**.

**Why it matters.** Every onset threshold is expressed in frames. A 30 Hz file
would make `sustained_frames = 2` mean 0.067 s instead of 0.2 s.

## A4 — `ball_snap` is present on ≥95% of plays  · LIVE

**Assumption.** The snap is an explicit event, not something to be inferred.

**Assertion.** `schema.tracking_contract`; raises if the share falls below 0.95.
Measured on the sample: **0.997** (12,606 / 12,638).

## A5 — No duplicate (game, play, nfl_id, time) keys  · LIVE

**Assertion.** `schema.tracking_contract` counts duplicate keys and raises if any
exist. Measured on the sample: **0**.

**Why it matters.** A duplicate row silently doubles a player's weight in any
per-frame average and can fabricate a zero-length frame gap.

## A6 — Full tracking carries ~22 players per play  · LIVE (enforced in full mode only)

**Assumption.** Full game tracking is complete, so the sparsest play still has an
offence and a defence.

**Assertion.** `schema.tracking_contract` requires `min(players per play) >= 20`
when `mode == "full"`. In sample mode it is **reported, not enforced**, because
the provided sample is by design a 1–4 player draw (measured: min 1, median 1,
max 4).

**Why it matters.** This is the assertion that catches "someone put the sample on
the big machine and renamed it".

## A7 — `player_play` covers ~every game in `games.csv`  · PLANNED

**Assumption.** For the full release, every `game_id` in `games.csv` has rows in
`player_play.csv`.

**Measured today:** 1,001 of 1,002 games (one game absent). That is close enough
to be a rounding artefact, but not proof about another release.

**Assertion (to add in 03_game_snaps).** `assert missing_games <= 1` and log the
missing ids. Fail if the share of covered games drops below 0.99.

## A8 — Position vocabulary is unchanged  · LIVE

**Assumption.** `player_play.lined_up_position` still uses `EDGE` /
`INTERIOR_LINE` and still never uses `DE`/`DT`/`NT`.

**Assertion.** `schema.assert_vocabulary` raises if a configured DL value is
absent, and separately raises if `DE`/`DT`/`NT` appear at all.

## A9 — `NA` remains the only null marker  · PLANNED

**Assumption.** The full files use the literal `NA` for missing values and do not
use empty string as null.

**Assertion (to add in 01/03).** After parsing with `nullstr=['NA']`, count rows
where a numeric column is the empty string; fail if > 0.

## A10 — Pre-snap depth stays in [0, 5] yd  · LIVE (as a derivation check)

**Assertion.** Audit item (c) computes `|x_at_snap − LOS|` and fails the
`share of depths in [0,5] yd` check if fewer than 95% of reps fall inside.

**Measured on the sample:** 100% (median 0.63 yd, p95 1.24 yd). A wrong LOS formula
produces depths of tens of yards, so this is a sharp test.

**Caveat.** Depth being small is not an assumption that will *break*; it is a
design constraint that will *hold* (threat 11 in `notes/limitations.md`).

## A11 — No rep exceeds the 12 yd/s sanity cap by much  · PLANNED

**Measured today:** the fasting tracked player in the sample is **12.63 yd/s**,
above `onset.speed_cap_yds = 12.0`, so the cap will flag a handful of frames.

**Assertion (to add in 02/03).** Count frames above the cap and **report** them;
do not drop silently. Fail only if the *share* exceeds 0.5%, which would indicate
a unit or parsing problem rather than a genuinely fast player.

## A12 — Combine tracking is complete and needs no sampling  · LIVE (implicitly)

**Assumption.** `combine_tracking.csv` is the full Combine file, not a sample.

**Evidence, not assumption:** it contains 510 players, 56 drills, 6,301
(player, attempt) pairs, and 40-yard attempts with 1–4 attempts per player. A
sample would not preserve attempt counts like that.

**Assertion.** `schema.assert_schema` + `assert_units` scoped to
`entity_type='PLAYER'` and the straight-line drill. Measured: max player speed
11.85 yd/s, `dis` ≤ 1.44 yd — consistent with a 40-yard dash at 10 Hz, i.e. the
units are right.

## A13 — Calibration offsets can be estimated globally  · PLANNED

**Assumption.** There are enough DL attempts, across enough stance/start types, to
estimate a global stance offset.

**Assertion (to add in 02).** Refuse to write `outputs/calibration.json` with
provenance `FULL` unless each calibration cell has ≥ 30 attempts; otherwise write
the cell as `null` with a reason. Never fall back to a per-player offset.

---

## Assumptions that are *not* made

* That stance is inferable from pre-snap kinematics — untested, and no labelled
  subset exists to test against.
* That `quick_pressure` null means "no pressure" — explicitly **not** assumed
  (`notes/blockers.md` B1).
* That `lined_up_position` is correct where it conflicts with `combine_position` —
  explicitly **not** resolved (`notes/blockers.md` B3).
* That the sample is representative of the season — it is a 1–4 player-per-play
  draw, and nothing is tuned on it.
