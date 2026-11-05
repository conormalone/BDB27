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

**Combine side (LIVE, Phase 2).**
`02_combine_physical_time.assert_sampling_rate` computes the per-attempt median
frame spacing over the 40-yard dash and raises `SchemaError` if it is more than
20 ms from 1/`onset.hz`. Measured on the complete Combine file: median
**0.100 s**, 100% of gaps within 20 ms.

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

## A13 — Calibration offsets can be estimated globally  · LIVE (asserts *not* estimable today)

**Assumption.** There are enough DL attempts, across enough stance/start types, to
estimate a global stance offset.

**Assertion (implemented in 02).** `outputs/calibration.json` is written with
`status = "PLACEHOLDER"` and `estimates = null` in sample mode;
`common.require_calibration_full` refuses any `status != "ESTIMATED"` or
provenance that is not `FULL`. Phase 2 itself refuses to complete a full run on a
non-estimable calibration. This assumption is currently **false** — no stance or
start-type label exists — and the code says so instead of inventing an offset
(blocker B8).

---

## Assumptions that are *not* made

* That stance is inferable from pre-snap kinematics — untested, and no labelled
  subset exists to test against.
* That `quick_pressure` null means "no pressure" — explicitly **not** assumed
  (`notes/blockers.md` B1).
* That `combine_position` is correct where it conflicts with
  `lined_up_position` — **not** assumed; by Q3 the in-game label is the authority
  and the conflict is reported, not reconciled (`notes/blockers.md` B3, D97).
* That the sample is representative of the season — it is a 1–4 player-per-play
  draw, and nothing is tuned on it.

---

## A14 — `pass_result` carries exactly the verified vocabulary · **LIVE (Phase 3)**

**Assumption.** `pass_result ∈ {"", "C", "I", "S", "R", "IN"}` in every season
file, and `""` means a run. Verified on the complete 2023-2025 label file
(`{"", C, I, IN, R, S}`); a new value would silently be treated as a run.

**Assertion.** `03_game_snaps.assert_input_assumptions` raises
`PASS-RESULT VOCABULARY CHANGED` listing the unexpected values, before any table
is built.

**Why it matters.** The primary set is "pass-rush snaps"; misclassifying a new
pass category as a run deletes those reps from every downstream model.

## A15 — `(game_id, play_id, nfl_id)` is unique in `player_play` · **LIVE**

**Assumption.** One row per player per play, so a per-snap table cannot silently
double-count a rep.

**Assertion.** Row count vs distinct-key count in
`assert_input_assumptions`; raises `PLAYER_PLAY KEY NOT UNIQUE` with the
duplicate count. Holds today: 314,197 rows / 314,197 keys.

## A16 — `dropback_duration` and `pass_result` are play-level · **LIVE**

**Assumption.** Both take one value per play, so the label-side join cannot pick
one of several. `pressure_allowed`, `time_to_pressure_allowed` and `sack_allowed`
are *not* play-level (they are per offensive player) and are not joined.

**Assertion.** `count(DISTINCT pass_result)` and `count(DISTINCT
dropback_duration)` per play must both be ≤ 1; otherwise
`PLAY-LEVEL FIELD NOT PLAY-LEVEL`.

**Evidence.** One distinct value per play in 114,954/114,954 plays (both columns);
`pressure_allowed` disagrees within a play in hundreds of plays, which is why the
offence-row sensitivity family is deferred to Phase 4 rather than aggregated here.

## A17 — `blitzing` is a pass-rush participation flag, not a blitz call · **LIVE**

**Assumption (D104).** On DL reps carrying a `player_get_off`, `blitzing` is set
on ≥95% of pass reps and on ≤5% of run reps. Observed: **99.78%** and **0.73%**.

**Assertion.** `assert_input_assumptions` computes both shares and raises
`BLITZ-FLAG SEMANTICS CHANGED` outside those bounds, telling the reader to re-open
B9. The bounds live in `config.yaml` (`snaps.blitz_flag_*`) and are validation
bounds, not analysis thresholds.

**Why it matters.** The whole "non-blitz primary set" question hinges on this. If
the full release differs, the primary set must be redefined by Conor, not
reinterpreted by the code.

## A18 — The Parquet cache is complete · **LIVE**

**Assumption.** A killed or partially written conversion is not mistaken for a
finished one, and every `game_id=<n>` partition directory is present.

**Assertion.** `ensure_parquet` re-derives the row count from Parquet metadata and
compares it with the manifest, and for a partitioned cache compares the number of
`game_id=` directories with `count(DISTINCT game_id)`; raises
`PARQUET CACHE INCOMPLETE` with `--force` in the message.

**Why it matters.** Phase 3 reads one game partition at a time. A missing
partition would silently produce rows with `tracking_present = false` — a
data-integrity failure disguised as "no tracking for this game".

## A19 — The label, games, Combine and player files are complete · **LIVE (Phase 3b)**

**Assumption.** `player_play.csv`, `games.csv`, `combine_tracking.csv` and
`players.csv` are complete files, so an analysis that reads only them is real
evidence and must **not** be stamped `UNVALIDATED SAMPLE OUTPUT`.

**Assertion.** `03b_reliability.input_evidence` raises `SchemaError` unless
`count(*) == count(DISTINCT (game_id, play_id, nfl_id))` in `player_play` (A15)
and unless `player_play` covers ≥99% of the games in `games.csv` (A7). It
measures and records, per file, the row count, players, attempts, drills, draft
years and games covered. `03b_reliability.reconcile_label_side` then requires the
Phase 3 table's label side to match a direct query on the complete
`player_play.csv` exactly (44,002 get-off rows and 33,061 primary rows) and
raises `LABEL-SIDE RECONCILIATION FAILED` otherwise.

**Why it matters.** The provenance stamp of `outputs/phase3b/` rests on this.
Without the reconciliation, a Phase 3 table accidentally rebuilt from a partial
label file would carry a `FULL` stamp and Conor would make a prereg decision on
numbers that are not the release.

**Not assumed.** That the release is a random sample of NFL defensive linemen —
it is pre-filtered to 510 Combine attendees (B5), stated as the study
population, not a limitation to be corrected.
