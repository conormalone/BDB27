# Outcome-link feasibility for DB 30–60° turns

**Status: scoping note — match rule frozen, test designed, no result.** Written 2026-11-14 by the
PM. **Corrected 2026-11-14 (D198):** the pre-flight stop-check in the first version of §6.1 was
false, and the zero tie rate it produced was an artefact of the file used. Read the correction
below before anything else. Spec: `notes/outcome-link-feasibility-spec.md`, with this cycle's
correction spec at `notes/outcome-link-correction-spec.md`. Evidence: `outputs/outcome_link/`
(regenerate with `scripts/outcome_link_feasibility.py`). `TASK.md` is unchanged by this cycle.

**Authorisation.** Conor, 2026-11-14 15:37: *"It's the only way, go for it."* Geometric
proximity-matching of a DB to an opposing receiver at the break frame is the attribution route.
It is an **INFERENCE** — the dataset records no opponent identifier — and it is flagged as one in
the artefact, per turn, and in every sentence below that depends on it.

Provenance stamps: **`[SAMPLE]`** = `outputs/turns/turns.parquet` (`PLACEHOLDER_SAMPLE`) joined to
`data/raw/player_play.csv` and `data/raw/game_tracking_2023_sample.csv`. **`[FULL]`** =
`data/interim/full_run_20261111/bdb27_full_db_turns.parquet`, read against the same full
`player_play.csv` — which covers all 7,198 band plays, so candidate availability is measured on
the full population, not projected. Turn and play **counts** are `[FULL]`; no outcome-bearing FULL
artefact exists on this machine. A `[SAMPLE]` numerator is never multiplied by a `[FULL]`
denominator; where the two populations appear side by side below, each column is measured on its
own rows.

---

## CORRECTION (D198) — the pre-flight was a false stop; the tie rate is unmeasured, not zero

The first version of §6.1 opened the FULL-notebook design with this stop condition:

> "Rows per play in `player_play`, and `team_abbr` coverage per player per play. If the full file
> averages well under ~15 rows per play — as this machine's copy does at 2.7 — opposing players
> cannot be typed by team, geometric matching is impossible, and the notebook must stop and say so
> rather than produce an empty result."

**That inference is false**, and the numbers it produced downstream were wrong with it.

### What is actually in `player_play`

| Quantity | Value |
|---|---:|
| rows | 314,197 |
| games / plays / players | 1,001 / 114,954 / 497 |
| mean rows per play | 2.733 |
| maximum rows on one play | **11** (not 4) |
| plays carrying rows from **both** `possession_team` and an opponent | **67,972 (59.1%)** |

The file holds rows only for the 497 prospects in `players.csv` (510 players; all 497 appear).
A play therefore has as many rows as it has prospects on the field. **A low rows-per-play is the
cohort's design, not a defect**, and it says nothing about whether opposing players can be typed
by team. They can, on 59.1% of plays, and no play in the 7,198 band plays lacks a team label on
the turning DB's own row.

The "1.16 players per play / 263 unobservable / max 4" figures came from
`data/raw/game_tracking_2023_sample.csv`, which is **1,048,575 data rows (2^20 − 1)**, holds
tracks for **154 of the 497 prospects** over **12,638 of the 31,720 plays** in its 333 games
(39.8%), and never carries more than **4** players on a play. Its per-play player count is a
property of that sample file — it cannot put two receivers in a frame, so a tie rate measured on
it is structurally zero. It must not be used to characterise data availability. `player_play` is
the source for who is present on a play.

### Corrected candidate availability — `player_play` only, no geometry

A **candidate** is an opposing-side row on the turn's play whose `route_ran` is recorded. Only
WR (28,074), SLOT_WR (18,740), TE (6,027), RB (284), FB (181) and an eligible T (2) carry a route
value — 53,308 rows in all; interior linemen and every defensive position carry none. Note that
the file's missing sentinel is the string `NA` (260,889 rows), so a plain `route_ran IS NOT NULL`
returns all 314,197 rows and would make every opponent a candidate; the null is set at read time.
Counted per turn, keyed on
`(game_id, play_id, nfl_id, frame_start)` — not on `(game_id, play_id, nfl_id)`, which collapses
373 `[FULL]` turns onto 362 player-keys and drops 17 `[SAMPLE]` turns: these are second turns by
the same DB on the same play, and grouping on three columns silently loses them. Even the
four-column key is not quite unique on `[FULL]` — 3 of its 7,777 keys carry two distinct turns
(7,774 distinct keys) — so every count here is taken per turn **row**, not per key.

| | `[SAMPLE]` 326 | `[FULL]` 7,777 |
|---|---:|---:|
| no opposing route-runner row on the play | 246 (75.5%) | 4,768 (61.3%) |
| exactly one | 67 (20.6%) | 2,002 (25.7%) |
| two | 13 (4.0%) | 815 (10.5%) |
| three or more | 0 | 192 (2.5%) |
| **≥1 — the ceiling on `unique` at any radius** | **80 (24.5%)** | **3,009 (38.7%)** |
| ≥1 with the receiver's separation recorded | 67 (20.6%) | 2,601 (33.4%) |
| exactly one with separation | 58 (17.8%) | 1,731 (22.3%) |
| two or more with separation | 9 (2.8%) | 870 (11.2%) |

**What this changes.** The route is not dead on arithmetic. On the FULL band the candidate pool is
3,009 turns, which is 38.7% of 7,777 — above the note's own 30% share gate, and enough that the
≥500-turns gate needs only 16.6% of candidate turns to be unique within 3 yd. The share gate needs
2,333 of 3,009 (77.5%) to be unique within 3 yd. Neither is established: **the geometric hit rate
is exactly what the full tracking would measure and this machine cannot.** On the `[SAMPLE]` band
the ceiling is 24.5%, so the 30% gate is unreachable there *before* geometry is run — a better
explanation of "no estimate here" than the tracking-density argument it replaces.

**What this does not restore.** The committed note said "multiple = 0 at every radius". At the
candidate level there are ties: 13 `[SAMPLE]` turns carry two labelled receivers, 9 of them with a
separation value on both, and the two separations differ by a median of 1.26 yd (range 0.18–13.6).
On the FULL band 1,007 turns (12.9%) carry two or more candidates. Whether two candidates are both
**within 3 yd at the break frame** is still unmeasured on this machine. **The geometric tie rate is
unmeasured, not zero**, and no sentence in this note may treat it as zero.

**What does not change.** The frozen rule (unique opposing route-runner within 3 yd, ties dropped,
never broken by radius, distance or row order), the radii (2 and 5 yd), the sensitivity set
(any-opponent, MAN-only, persistence-restricted), the gate (≥30% unique, ≥500 turns) and the
absence of any model, coefficient or finding. Nothing in the corrected numbers forces a change to
any of them.

**Selection, stated plainly.** A matched pair is **prospect versus prospect**: the DB and the
opposing receiver must both be among the 497 named players. That is not a random subset of NFL
coverage — it selects on having been drafted into this cohort, and within a play it selects on
which of the two teams' eligible players happen to be in the file. The attributed subsample is
therefore narrower than "DB 30–60° turns", and every estimate carries that beside it.

---

## 1. The structural constraint

`player_play.csv` holds 314,197 rows over 1,001 games, 114,954 plays and 497 players. The
receiver-side outcome is present; the defender-side outcome is not.

| Column | Receiver side `[SAMPLE]` | DB-alignment rows `[SAMPLE]` |
|---|---:|---:|
| `separation_at_pass_forward` | 46,737 | **0 of 75,428** |
| `target` | 9,842 | 0 |
| `route_ran` (non-`NA`) | 53,308 | 0 |

DB-alignment rows: CB 35,017, SLOT_CB 14,537, HIGH_SAFETY 25,608, BOX_SAFETY 266. (The 35,017
that circulated earlier is the CB rows only.) Separation is observed on 35,211 of 114,954 plays
(30.6%) and a `target` row on 9,842 (8.6%), because rows exist only for the 497 named players —
all 115 `[FULL]` DBs are inside that 497, and so is every receiver whose separation can be read.

No column in the 64-column file identifies an opponent, a matchup or an assignment
(`coverage_assignment` is a MAN/zone/spot label, not a player). **The match below is therefore an
inference from positions, not a join.** It is never used in the project claim.

## 2. What is on a DB's own row

On the 326 `[SAMPLE]` DB 30–60° turns:

| Field | Present | Share | Role |
|---|---:|---:|---|
| `cushion` | 218 | 66.9% | control; also a pre-snap consistency check |
| `coverage_assignment` | 155 | 47.5% | control (MAN 35, zone 113, `NO_ASSIGNMENT` 7) |
| `pass_result` (pass play) | 202 | 62.0% | play-level context |
| `tackle` / `assist` | 40 / 13 | 12.3% / 4.0% | same-player event (route R3) |
| `caused_forced_fumble` / `tackle_for_loss` | 0 / 1 | — | — |

Exposure: `apex_retention` (apex ÷ entry speed), IQR 0.571–1.000, median 0.859 `[SAMPLE]`; entry
speed IQR 2.75–4.96 yd/s. There is spread to analyse, if there were turns to analyse it on.

## 3. The match rule — frozen before any outcome is seen

**Rule.** At the break frame, a turn is attributed to **the single opposing route-runner within
3 yd**. Two or more candidates inside the radius → **ambiguous, dropped**. No candidate inside the
radius → **unmatched, dropped**. No tie is ever broken by radius choice, by distance order or by
row order; a tie simply leaves the turn out.

**Why 3 yd.** A defender within about a step and a half of the receiver at the break can contest
that break; 2 yd is a tight step and 5 yd is loose. 3 yd is a **design choice**, recorded as one,
and the whole radius sweep is reported below so the choice is visible rather than asserted.

**Statuses** (per turn, in `feasibility_turns.csv`):

| Status | Meaning |
|---|---|
| `unique` | exactly one opposing route-runner inside the radius — **attributed** |
| `multiple` | two or more — **ambiguous, dropped** |
| `none_observed` | opponents are tracked in the frame, none is a route-runner inside the radius |
| `unobservable` | no opposing player is tracked in the frame at all |

`unobservable` is **not** evidence that no receiver was near, and it is **not** a property of the
play. It is a property of `game_tracking_2023_sample.csv`, which holds tracks for 154 of the 497
prospects over 39.8% of the plays in its games and at most 4 players on any play. A turn in that
category carries no information about proximity either way, and it is never counted as "no
receiver". Candidate availability is measured from `player_play` instead — §3.2.

### 3.1 The geometric sweep `[SAMPLE]`, 326 band turns

**Scope of this table, stated before the numbers.** These counts measure *the 2023 sample
tracking file*, not data availability. It cannot put two receivers in one frame, so `multiple = 0`
here is structural, and the 263 `unobservable` turns are unobservable in a 154-player, 39.8%-of-
plays file. The table is kept because it is what the file supports; it is not evidence about the
NFL or about whether a match is possible.

Route-runner candidates (the rule's definition):

| radius | unique | multiple | none_observed | unobservable |
|---:|---:|---:|---:|---:|
| 1 yd | 1 | 0 | 62 | 263 |
| 2 yd | 1 | 0 | 62 | 263 |
| 3 yd **(primary)** | **4** | **0** | **59** | **263** |
| 4 yd | 5 | 0 | 58 | 263 |
| 5 yd | 5 | 0 | 58 | 263 |
| 6 yd | 5 | 0 | 58 | 263 |
| 8 yd | 6 | 0 | 57 | 263 |

Any-opponent candidates (the fallback definition, if `route_ran` is unavailable on full data):

| radius | unique | multiple | none_observed | unobservable |
|---:|---:|---:|---:|---:|
| 1 yd | 1 | 0 | 62 | 263 |
| 2 yd | 3 | 0 | 60 | 263 |
| 3 yd | 7 | 0 | 56 | 263 |
| 4 yd | 9 | 0 | 54 | 263 |
| 5 yd | 10 | 0 | 53 | 263 |
| 6 yd | 13 | 0 | 50 | 263 |
| 8 yd | 17 | 0 | 46 | 263 |

**Geometric attributed fraction at the primary radius: 4 of 326 = 1.2%** `[SAMPLE]`, of which 4
also have the matched receiver's separation recorded (**1.2%** attributed *and* outcome observed).
Median match distance 2.19 yd. **No candidate at 3 yd: 322 of 326 (98.8%)** — 59 where an opponent
is in the frame but none ran a route inside 3 yd, and 263 where the frame holds no opponent to
check.

Those 322 are a statement about the file, not about the plays. At the candidate level the same 326
turns look like this — the number that belongs in the pre-flight instead of the sweep.

### 3.2 Candidate availability from `player_play` — the corrected measure

Same 326 turns, no positions, no radius: how many turns carry a labelled opposing route-runner.

| | `[SAMPLE]` 326 | `[FULL]` 7,777 |
|---|---:|---:|
| no opposing route-runner row | 246 (75.5%) | 4,768 (61.3%) |
| exactly one | 67 (20.6%) | 2,002 (25.7%) |
| two or more | 13 (4.0%) | 1,007 (12.9%) |
| ceiling on `unique` | 80 (24.5%) | 3,009 (38.7%) |
| of those, with separation recorded | 67 (20.6%) | 2,601 (33.4%) |

The `[FULL]` column is **measured on all 7,777 band turns** against the full `player_play.csv`
(all 7,198 band plays and their 809 games are present), not extrapolated from the sample.

**The 13 two-candidate `[SAMPLE]` turns.** Twelve distinct plays (two turns are two different DBs
on one play). All pairs are two distinct receivers with distinct recorded routes (e.g. `CORNER`
at 14.53 yd and `IN` at 0.93 yd on the same play). Nine of the 13 carry a separation value for
both receivers; those pairs differ by a median of 1.26 yd (range 0.18–13.6). In 6 of the 9 a
`target` row names which receiver the ball went to — a post-hoc outcome, which may **not** be used
to break a tie; the rule drops ties. What is missing is the position of either receiver at the
break frame, which only the full tracking has.

The same characterisation on `[FULL]`, where it is not a sample: 815 turns carry **exactly two**
candidates (1,007 carry two or more), 704 of the 815 carry a separation value for both, 270 have a
`target` row, and the two separations differ by a minimum of 0.00 yd, a median of 1.53 yd and a
maximum of 13.61. The 0.00 yd cases are receivers who cannot be told apart by separation at all —
a tie that any radius-free or distance-ordered rule would break arbitrarily, which is why the rule
drops them instead.

**The tie rate.** Unmeasured, not zero. The measure that would settle it is the number of turns
with two or more candidates *inside 3 yd at the break frame*; that needs `(x, y)` for both sides
at one timestamp, which the full tracking supplies and this machine does not. The candidate level
gives the denominator to report it against: 1,007 `[FULL]` turns (12.9%) have two or more
candidates available to tie.

### 3.3 What the numbers say about the gate

| | `[SAMPLE]` | `[FULL]` |
|---|---:|---:|
| band turns | 326 | 7,777 |
| ceiling on `unique` | 80 (24.5%) | 3,009 (38.7%) |
| turns needed for ≥500 | 500 | 500 |
| share of candidates that must be unique at 3 yd for ≥500 | 625% — impossible | 16.6% |
| turns needed for ≥30% | 98 | 2,333 |
| share of candidates that must be unique at 3 yd for ≥30% | 122% — impossible | 77.5% |
| verdict | **gate unreachable** | **gate live, undecided** |

The `[SAMPLE]` row is why no estimate is computed here: the 30% gate cannot be met even if every
single labelled candidate were within 3 yd. The `[FULL]` row is the honest state of the route: it
is no longer killed by arithmetic, and it is not cleared either. A 77.5% within-3-yd hit rate
among candidate turns is a high bar for a coverage relationship and the note does not predict it.

## 4. Routes, after the authorisation

`[SAMPLE]` = 326 band turns in 309 plays, 30 players, 140 games. `[FULL]` = 7,777 turns in 7,198
plays, 113 players, 809 games.

| id | Route | Outcome | Attribution | Attributed `[SAMPLE]` | Ceiling `[FULL]` |
|---|---|---|---|---:|---:|
| **R1** | **Proximity match — the route** | matched receiver's separation | **INFERENCE**, unique within 3 yd | **4 (1.2%)** geometric | **3,009 (38.7%)** before geometry |
| R2 | R1 on MAN turns | as R1 | **INFERENCE**, MAN prior | 1 of 35 (2.9%) geometric | not counted in this cycle |
| R3 | Same-player events | tackle/assist/FF/TFL | exact | 326 (40 events) | 7,777 turns |
| R4 | Play-level outcomes | pass_result/yards/EPA | play (unit) | 202 pass-play turns | 7,198 plays |
| R5 | Injury | free-text mention | name resolution | 2 | 7,777 turns |

**R1** is now the route. It measures whether a DB who retains more speed through a 30–60° break
ends with less separation from the receiver he was matched to. Controls: entry speed, roster
position (CB/SS/FS), alignment, coverage assignment, cushion, down and distance. Both numbers are
carried next to every claim that depends on the match: the geometric 1.2% is what this machine's
tracking file yields, the 38.7% is what the full population makes available before any radius is
applied, and the difference between them is a file, not a finding.

**R2** is R1 restricted to `coverage_assignment == 'MAN'`, where a one-to-one assignment is the
strongest prior available. It does not remove the inference: rub routes, picks and zone-match
still exist, and the label itself is present on only 47.5% of turns. On the 35 `[SAMPLE]` MAN
turns, 15 have at least one labelled opposing route-runner (42.9%) — a higher candidate ceiling
than the band as a whole, but 15 turns is far under the count gate, and the share gate is defined
over all band turns, so this does not rescue the local estimate.

**R3** keeps exact attribution and needs no geometry (40 tackles, 13 assists, 0 forced fumbles,
1 tackle for loss on 326 turns `[SAMPLE]`). It remains a robustness check, not the route: a tackle
is neither observed at the break nor caused by it.

**R4** is play-level and can only ever support a coverage-unit statement. 17 of 309 `[SAMPLE]`
plays (5.5%) and 542 of 7,198 `[FULL]` plays (7.5%) carry **two or more** DB band turns (507 of
them exactly two); the rest are single-turn but still not single-cause.

**R5** — Main's premise that no injury signal exists is **refuted** and the correction stands.
`play_description` names injured players ("CHI-C.Williams was injured during the play"): 3,663
`[SAMPLE]` plays carry "injur" (5,125 with "hurt"), 2,548 distinct mentions, only 426 resolving to
a unique `nfl_id` (16.7%), 137 of them DBs. Of the 326 band turns, 8 fall on such a play and **2**
name the turning DB. It is a mention, not a measured injury, and it is unusable as an outcome.

## 5. The outcome test — design

This is the specification the FULL notebook implements. **Nothing in this section is estimated on
the sample.**

**Unit.** One DB 30–60° game turn with `match_status == unique` at 3 yd. Ambiguous and unmatched
turns are dropped, never imputed.

**Outcome.** `separation_at_pass_forward` read from the **matched receiver's own row** (yards).
Two checks are mandatory before it is used:

1. *Persistence.* At the pass-forward frame, is the matched DB the nearest defender to that
   receiver? If not, the separation belongs to a different defender's coverage and the pair is
   dropped from the primary estimate (reported as the persistence rate).
2. *Outcome presence.* The matched receiver must be one of the 497 players whose row exists;
   otherwise there is no separation value and the turn leaves the estimate.

**Selection.** Every matched pair is prospect-versus-prospect — both players in the 497. It is a
selected subsample, not a random sample of coverage, and it is reported as one everywhere.

**Exposure.** `apex_retention` (primary); `speed_retention` (secondary, never substituted).

**Controls.** Entry speed (continuous), position (CB / SS / FS), `coverage_assignment`
(MAN / zone / other / missing as its own level), `cushion` (continuous + missing indicator); down
and distance as a secondary block.

**Estimator.** A within-player linear model (player fixed effects) of separation on retention plus
controls, with standard errors clustered by player. Players with a single attributed turn
contribute nothing to the within-player slope and are reported separately. No model is fitted in
this cycle; the sample cannot carry one.

**Sensitivity, all pre-declared.** Radius 2 and 5 yd; any-opponent candidate definition;
`apex_retention` vs `speed_retention`; MAN-only; persistence-restricted vs all matched.

**What a result would support.** A statement of the form: *among uniquely matched, persistent
pairs, a 0.1 higher apex retention through the 30–60° break is associated with X yards less
separation at pass forward, conditional on entry speed, position, coverage assignment and
cushion.* It would be an association in a selected subsample — turns where a DB was close enough
to a receiver at the break to be matched — and it would be reported with the attributed fraction,
the persistence rate and the tie rate beside it.

**What it would not support.** Any causal reading (the break is not randomised; coverage
assignment and opponent quality drive both retention and separation); any per-prospect ranking;
any projection to players outside the 497-player population; any claim from turns that were
ambiguous or unmatched, or from a radius chosen after seeing the outcome; any statement about
separation at the break itself (the outcome is measured at pass forward).

## 6. What the FULL notebook must compute

Full game tracking is on Conor's laptop. Step 1 below now ships as an executable notebook that
needs no tracking and runs on this machine (`notebooks/bdb27_outcome_link_preflight.ipynb`); steps
2–7 remain specified and **unshipped**, because a full-tracking path cannot be executed or tested
here.

1. **Pre-flight — candidate availability, from `player_play` alone. This replaces the
   rows-per-play test, which was false.** For every band turn: the turning DB's `team_abbr`; the
   count of opposing rows with `route_ran`; the count of those with a separation value. Then:
   * **P1** turns the DB cannot be sided on (no row, or no `team_abbr`) — stop and name them;
   * **P2** turns with ≥1 candidate — if zero, the route is impossible in this dataset: stop;
   * **P3** if that count is below 500, the ≥500-turns gate is unreachable: stop the estimator;
   * **P4** if it is below 30% of band turns, the ≥30%-unique gate is unreachable: print
     `GATE UNREACHABLE — descriptive only` and refuse to write an estimate;
   * **P5** print the cohort ceiling beside the count.
   Measured today: `[FULL]` 3,009 of 7,777 (38.7%) — P2 and P3 clear, **P4 live and
   undecided**; `[SAMPLE]` 80 of 326 (24.5%) — P4 fires. The pre-flight reads no tracking file
   and contains no rows-per-play threshold.
2. **Break frame.** For each of the 7,777 band turns, recover the break timestamp from
   `(x_centre, y_centre)` against the player's own frames, with the same ≤0.25 yd alignment gate
   used here (all 326 sample turns pass at ≤0.063 yd).
3. **Candidates.** Every player in the frame at that timestamp (±0.05 s); team from `player_play`;
   opposing route-runners as the primary candidate set, any opponent as the fallback.
4. **Attribution table.** `unique` / `multiple` / `none_observed` / `unobservable` at 1, 2, 3, 4,
   5, 6, 8 yd, plus the nearest-vs-second gap and the median match distance. **This table is the
   first output**; it decides whether the test is worth running. Report it **next to** the
   candidate-level table of §3.2, and report the geometric tie rate as a count, not a zero.
5. **Persistence.** At the pass-forward frame, the matched DB's rank among defenders by distance
   to the matched receiver; persist if rank 1.
6. **Outcome join** and the estimator of §5, with the attributed fraction, the persistence rate
   and the tie rate printed next to every estimate.
7. **Provenance.** FULL stamp, input hashes, and the rule string, written into the artefact.

**Gate (proposal, for Conor to accept or change).** If fewer than 30% of band turns are `unique`
at 3 yd, or fewer than 500 attributed turns survive persistence, the estimate is descriptive only
and no sentence in the style Conor asked for may be written. These thresholds are the PM's
proposal, not a project rule. The candidate ceiling says the 30% arm is reachable on the FULL band
(38.7%) and unreachable on the sample (24.5%); only the geometric step can decide it.

## 7. Why no estimate is computed here

Two independent reasons, and the first is now the binding one:

1. **The gate is unreachable on the sample.** The candidate ceiling is 80 of 326 turns (24.5%),
   below the 30% arm before any radius is applied; even attributing every candidate at every
   turn would leave 80 turns against the 98 the gate needs.
2. **The geometric hit rate and the tie rate are unmeasured on this machine.** The 2023 sample
   tracking file carries at most 4 players per play and 154 of the 497 prospects, so it can
   produce neither a within-3-yd count that generalises nor a non-zero tie count.

A within-player slope would also need players with several attributed turns each. The file cannot
supply that either, but it is no longer the headline reason. The note stops at the match and hands
the estimation to the full run.

## 8. What this cycle did not do

No model fitted, no coefficient, no interval, no p-value. No full-tracking notebook: the
geometric path cannot be executed or tested on this machine, and an untested one is the failure
this project has already had. What shipped instead is the pre-flight notebook — the part that
needs only `player_play` — with its stop conditions tested against the real file. No change to
`TASK.md`, `notes/report-draft.md`, the band table or the figure set. The inference does not
enter the claim.

## 9. The decision

**Decision D197 (unchanged): adopt the frozen match rule as written — unique route-runner within
3 yd, ties dropped, sensitivity at 2 and 5 yd, with the 30%/500 gate — and fund the FULL notebook
to run it.** **Decision D198 (this correction): replace the rows-per-play pre-flight with
candidate availability from `player_play`, and treat the geometric tie rate as unmeasured.**

| Element | Frozen value |
|---|---|
| Break frame | the single centre frame, recovered geometrically |
| Candidate | opposing route-runner (fallback: any opponent) |
| Primary radius | 3 yd |
| Ties | dropped, never resolved |
| Unmatched | dropped, never imputed |
| Sensitivity | radius 2 and 5 yd; MAN-only; persistence-restricted |
| Gate | ≥30% unique; ≥500 attributed turns after persistence |
| Pre-flight (D198) | candidate availability from `player_play`; stop on an empty pool, an unreachable count gate, or an unreachable share gate |
| Tie rate | unmeasured until full tracking; never reported as zero |

**Recommendation: adopt as written.** The rule is declared before any outcome is seen, the
candidate-level table and the ambiguity table are the first things the notebook prints, and the
gate makes the route cheap to kill if the full tracking shows what the sample cannot: how often a
DB at the break has more than one plausible receiver inside 3 yd.
