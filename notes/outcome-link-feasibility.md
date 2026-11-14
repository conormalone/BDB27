# Outcome-link feasibility for DB 30–60° turns

**Status: scoping note — match rule frozen, test designed, no result.** Written 2026-11-14 by the
PM. Spec: `notes/outcome-link-feasibility-spec.md` (§0 records Conor's authorisation). Evidence:
`outputs/outcome_link/` (regenerate with `scripts/outcome_link_feasibility.py`). `TASK.md` is
unchanged by this cycle.

**Authorisation.** Conor, 2026-11-14 15:37: *"It's the only way, go for it."* Geometric
proximity-matching of a DB to an opposing receiver at the break frame is the attribution route.
It is an **INFERENCE** — the dataset records no opponent identifier — and it is flagged as one in
the artefact, per turn, and in every sentence below that depends on it.

Provenance stamps: **`[SAMPLE]`** = `outputs/turns/turns.parquet` (`PLACEHOLDER_SAMPLE`) joined to
`data/raw/player_play.csv` and `data/raw/game_tracking_2023_sample.csv`. **`[FULL]`** =
`data/interim/full_run_20261111/bdb27_full_db_turns.parquet`, used for turn and play **counts
only** — no outcome-bearing FULL artefact exists on this machine. A `[SAMPLE]` numerator is never
multiplied by a `[FULL]` denominator.

## 1. The structural constraint

`player_play.csv` holds 314,197 rows over 1,001 games, 114,954 plays and 497 players. The
receiver-side outcome is present; the defender-side outcome is not.

| Column | Receiver side `[SAMPLE]` | DB-alignment rows `[SAMPLE]` |
|---|---:|---:|
| `separation_at_pass_forward` | 46,737 | **0 of 75,428** |
| `target` | 9,842 | 0 |
| `route_ran` | 53,308 | 0 |

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

`unobservable` is **not** evidence that no receiver was near. The 2023 sample tracks 1.16 players
per play (median 1, max 4; 14,612 play-player pairs over 12,638 plays). A turn in that category
carries no information about proximity either way, and it is never counted as "no receiver".

### 3.1 The sweep `[SAMPLE]`, 326 band turns

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

**Attributed fraction at the primary radius: 4 of 326 = 1.2%** `[SAMPLE]`, of which 4 also have
the matched receiver's separation recorded (**1.2%** attributed *and* outcome observed). Median
match distance 2.19 yd. **No candidate at all at 3 yd: 322 of 326 (98.8%)** — 59 where opponents
are visible but none ran a route inside 3 yd, and 263 where the frame holds no opponent to check.

**Read the zero-tie row correctly.** `multiple` is 0 at every radius because the sample cannot put
two receivers in one frame; it is **not** evidence that ties are rare in the NFL. The tie rate —
the number this route lives or dies on — is **unmeasured** on this machine. Only the full tracking
file can measure it.

## 4. Routes, after the authorisation

`[SAMPLE]` = 326 band turns in 309 plays, 30 players, 140 games. `[FULL]` = 7,777 turns in 7,198
plays, 113 players, 809 games.

| id | Route | Outcome | Attribution | Attributed `[SAMPLE]` | Denominator `[FULL]` |
|---|---|---|---|---:|---|
| **R1** | **Proximity match — the route** | matched receiver's separation | **INFERENCE**, unique within 3 yd | **4 (1.2%)** | 7,777 turns |
| R2 | R1 on MAN turns | as R1 | **INFERENCE**, MAN prior | 1 of 35 (2.9%) | not countable locally |
| R3 | Same-player events | tackle/assist/FF/TFL | exact | 326 (40 events) | 7,777 turns |
| R4 | Play-level outcomes | pass_result/yards/EPA | play (unit) | 202 pass-play turns | 7,198 plays |
| R5 | Injury | free-text mention | name resolution | 2 | 7,777 turns |

**R1** is now the route. It measures whether a DB who retains more speed through a 30–60° break
ends with less separation from the receiver he was matched to. Controls: entry speed, roster
position (CB/SS/FS), alignment, coverage assignment, cushion, down and distance. Attributed
fraction is carried next to every number that depends on the match, including in the test design
below.

**R2** is R1 restricted to `coverage_assignment == 'MAN'`, where a one-to-one assignment is the
strongest prior available. It does not remove the inference: rub routes, picks and zone-match
still exist, and the label itself is present on only 47.5% of turns.

**R3** keeps exact attribution and needs no geometry (40 tackles, 13 assists, 0 forced fumbles,
1 tackle for loss on 326 turns `[SAMPLE]`). It remains a robustness check, not the route: a tackle
is neither observed at the break nor caused by it.

**R4** is play-level and can only ever support a coverage-unit statement. 17 of 309 `[SAMPLE]`
plays (5.5%) and 542 of 7,198 `[FULL]` plays (7.5%) carry two DB band turns; the rest are
single-turn but still not single-cause.

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

**Exposure.** `apex_retention` (primary); `speed_retention` (secondary, never substituted).

**Controls.** Entry speed (continuous), position (CB / SS / FS), `coverage_assignment`
(MAN / zone / other / missing as its own level), `cushion` (continuous + missing indicator); down
and distance as a secondary block.

**Estimator.** A within-player linear model (player fixed effects) of separation on retention plus
controls, with standard errors clustered by player. Players with a single attributed turn
contribute nothing to the within-player slope and are reported separately. No model is fitted in
this cycle; the sample cannot carry one (n = 4).

**Sensitivity, all pre-declared.** Radius 2 and 5 yd; any-opponent candidate definition;
`apex_retention` vs `speed_retention`; MAN-only; persistence-restricted vs all matched.

**What a result would support.** A statement of the form: *among uniquely matched, persistent
pairs, a 0.1 higher apex retention through the 30–60° break is associated with X yards less
separation at pass forward, conditional on entry speed, position, coverage assignment and
cushion.* It would be an association in a selected subsample — turns where a DB was close enough
to a receiver at the break to be matched — and it would be reported with the attributed fraction
beside it.

**What it would not support.** Any causal reading (the break is not randomised; coverage
assignment and opponent quality drive both retention and separation); any per-prospect ranking;
any projection to players outside the 497-player population; any claim from turns that were
ambiguous or unmatched, or from a radius chosen after seeing the outcome; any statement about
separation at the break itself (the outcome is measured at pass forward).

## 6. What the FULL notebook must compute

The full game tracking is on Conor's laptop; this machine cannot run it. The notebook is one
run-all with these cells, in order:

1. **Pre-flight (stop conditions).** Rows per play in `player_play`, and `team_abbr` coverage per
   player per play. If the full file averages well under ~15 rows per play — as this machine's copy
   does at 2.7 — opposing players cannot be typed by team, geometric matching is impossible, and
   the notebook must stop and say so rather than produce an empty result.
2. **Break frame.** For each of the 7,777 band turns, recover the break timestamp from
   `(x_centre, y_centre)` against the player's own frames, with the same ≤0.25 yd alignment gate
   used here (all 326 sample turns pass at ≤0.063 yd).
3. **Candidates.** Every player in the frame at that timestamp (±0.05 s); team from `player_play`;
   opposing route-runners as the primary candidate set, any opponent as the fallback.
4. **Attribution table.** `unique` / `multiple` / `none_observed` / `unobservable` at 1, 2, 3, 4,
   5, 6, 8 yd, plus the nearest-vs-second gap and the median match distance. **This table is the
   first output**; it decides whether the test is worth running.
5. **Persistence.** At the pass-forward frame, the matched DB's rank among defenders by distance
   to the matched receiver; persist if rank 1.
6. **Outcome join** and the estimator of §5, with the attributed fraction and the persistence rate
   printed next to every estimate.
7. **Provenance.** FULL stamp, input hashes, and the rule string, written into the artefact.

**Gate (proposal, for Conor to accept or change).** If fewer than 30% of band turns are `unique`
at 3 yd, or fewer than 500 attributed turns survive persistence, the estimate is descriptive only
and no sentence in the style Conor asked for may be written. These thresholds are the PM's
proposal, not a project rule.

## 7. Why no estimate is computed here

The sample attributes 4 of 326 turns (1.2%). A within-player slope needs players with several
attributed turns each; the largest possible here is four turns spread over four plays. Any
coefficient from this file would be an artefact of the tracking density, not a measurement. The
note therefore stops at the match and hands the estimation to the full run.

## 8. What this cycle did not do

No model fitted, no coefficient, no interval, no p-value. No notebook shipped: a full-tracking path
cannot be executed or tested on this machine, and an untested one is the failure this project has
already had. No change to `TASK.md`, `notes/report-draft.md`, the band table or the four figures.
The inference does not enter the claim.

## 9. The decision

**One decision: adopt the frozen match rule as written — unique route-runner within 3 yd, ties
dropped, sensitivity at 2 and 5 yd, with the 30%/500 gate — and fund the FULL notebook to run it.**

| Element | Frozen value |
|---|---|
| Break frame | the single centre frame, recovered geometrically |
| Candidate | opposing route-runner (fallback: any opponent) |
| Primary radius | 3 yd |
| Ties | dropped, never resolved |
| Unmatched | dropped, never imputed |
| Sensitivity | radius 2 and 5 yd; MAN-only; persistence-restricted |
| Gate | ≥30% unique; ≥500 attributed turns after persistence |

**Recommendation: adopt as written.** The rule is declared before any outcome is seen, the
ambiguity table is the first thing the notebook prints, and the gate makes the route cheap to kill
if the full tracking shows what the sample cannot: how often a DB at the break has more than one
plausible receiver.
