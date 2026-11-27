# Spec — outcome-link feasibility for DB 30–60° turns

**Written by:** PM, 2026-11-14. **Status:** binding for this cycle.
**Deliverable:** `notes/outcome-link-feasibility.md` + its evidence artefacts.
**This spec is the PM's own; it is not a change to `TASK.md`.**

## 0. Revision — Conor's authorisation, 2026-11-14 15:37

> "It's the only way, go for it."

Geometric proximity-matching of a DB to an opposing receiver at the break frame is **authorised**
as the attribution route. It moves from "one candidate among several" to **the route**, and the
cycle is re-scoped as follows. Everything else in this spec stands.

1. **Ambiguity is load-bearing.** For DB 30–60° game turns, report how many have exactly one
   opposing receiver inside the match radius, how many have two or more, and how many have none —
   at **more than one radius**, so the radius choice is visible rather than asserted.
2. **Ambiguous and unmatched turns are dropped.** No tie is broken by radius choice or by row
   ordering. The attributed fraction is carried next to every number that depends on the match.
3. **The match is flagged as an inference in the artefact and the note:** a per-turn status, the
   radius, the rule, and the fact that the dataset records no opponent IDs.
4. **The note extends to the outcome-test design:** `separation_at_pass_forward` from the matched
   receiver; controls for entry speed, position, coverage assignment and cushion; an honest
   statement of what a result would and would not support.
5. **No model is fitted on the sample.** Feasibility numbers plus the test spec are the
   deliverable; the FULL-run notebook is the vehicle. Where the sample cannot carry an estimate,
   the note says so with the numbers and specifies exactly what the notebook must compute.

The inference remains outside `TASK.md`, the claim, the band table, the figures and the report
draft. No outcome claim is added to the project by this cycle.

## 1. Question

Conor, 2026-11-14 14:59 (verbatim):

> "Without a link to separation, coverage success, tackling or injury, the recommendation is an
> assertion. Even a modest result (for example, DBs who retain more speed through 30–60° breaks
> allow less separation, after controlling for speed and position) would turn this from an
> observation into a finding."

The cycle answers: **can a DB's 30–60° turn be linked to a defensive outcome at all; by which
route; at what sample size; with what attribution ambiguity; and at what cost.** It does not
answer whether the link exists.

## 2. Non-goals (hard)

- No model is fitted. No coefficient, odds ratio, p-value, confidence interval or effect size is
  reported as a result.
- No sentence may claim or imply a finding about turn quality and any outcome.
- No inferred DB↔receiver link enters `TASK.md`, `notes/report-draft.md`, the claim, the band
  table or any figure.
- No change to the existing claim, band table, the figure set or the report draft.
- No new position group. DB only (`position_group == 'DB'`).
- No extrapolation of a SAMPLE rate onto FULL counts. Denominators and numerators are carried
  separately, each with its own provenance stamp.

## 3. Definitions

- **Turn**: a row of the turn table with `valid == true`, `setting == 'game'`,
  `position_group == 'DB'`.
- **30–60° turn**: `30.0 <= abs_turn_angle < 60.0`, left-closed, matching the band table.
- **Retention**: `apex_retention = apex_speed / entry_speed`, the pipeline's existing column.
  `speed_retention = exit_speed / entry_speed` is reported alongside, never substituted.
- **Break frame**: the frame at the turn's centre. Recovered by matching `(x_centre, y_centre)`
  against the player's own tracking rows inside `[t_start, t_end]`; the nearest row must be
  within 0.25 yd or the turn is excluded from geometric work (alignment proof).
- **Opponent**: a tracked player whose `team_abbr` on that play differs from the turning player's.
- **Attribution**: the rule that assigns an outcome to a turn. Every non-same-player rule is an
  **INFERENCE** and is labelled so wherever it appears.

## 4. Routes to quantify

Each route is reported with: what it measures; how many DB 30–60° turns it covers (SAMPLE and
FULL, separately); how it is attributed; how often attribution is ambiguous; what controls exist;
what would count as a finding and what would not.

| id | route | outcome | attribution |
|---|---|---|---|
| R1 | geometric proximity match | receiver `separation_at_pass_forward`; target outcome | nearest opponent at the break frame — **INFERENCE** |
| R2 | man-restricted proximity match (subset of R1) | as R1 | as R1, prior strengthened by `coverage_assignment == 'MAN'` — **INFERENCE** |
| R3 | same-player event outcomes | `tackle`, `assist`, `caused_forced_fumble`, `tackle_for_loss` | exact: same `nfl_id`, same play |
| R4 | play-level coverage-unit outcomes | `pass_result`, `yards_gained`, `expected_points_added`, target separation | the play; player attribution only where exactly one DB turned |
| R5 | injury | injury mention in `play_description` | free-text named player, resolved by initial+surname |

### 4.1 Ambiguity is measured, not assumed

- R1/R2: ambiguity = the number of opponents inside the match radius at the break frame, and the
  nearest-vs-second-nearest distance gap. Where the local sample cannot measure it (fewer than 30
  turns with any opponent tracked at the break), the note says so and states the measurement
  protocol for full data. It does **not** report an assumed rate.
- R3: attribution is exact; the ambiguity that remains is temporal (a tackle can occur seconds
  after the break) and causal (a tackle is not caused by a break). Stated, not hidden.
- R4: ambiguity = DBs with a detected 30–60° turn in the same play. Measured as a distribution.
- R5: ambiguity = mentions that do not resolve to a unique `nfl_id`.

### 4.2 What would and would not count as a finding

- R1/R2 **would count**: an association between retention and separation among turns where the
  match is unique (one opponent inside the radius, or a gap to the second) with the match rule
  frozen before the outcome is seen, and the ambiguity rate reported. **Would not count**: any
  result where the match is the nearest of several candidates without a uniqueness rule; any
  result where the matched receiver's separation is the same quantity used to build the match
  (circular); any result that survives only at a radius chosen after seeing the outcome.
- R3 **would count**: nothing at this n as a causal statement. It can support a descriptive
  event count and, at full n, a pre-registered within-player comparison. **Would not count**: a
  tackle rate presented as a turn-quality effect, because the tackle is neither caused by the
  break nor observed at the break.
- R4 **would count**: a coverage-unit statement only, and only with the unit explicitly named.
  **Would not count**: any sentence attributing a play outcome to a named DB's turn.
- R5 **would count**: nothing. A free-text mention is not a measured injury, has no severity and
  no return-to-play record.

## 5. Provenance rules

- Local turn-level numbers are **SAMPLE**: `outputs/turns/turns.parquet` (manifest
  `PLACEHOLDER_SAMPLE`), `data/raw/player_play.csv`, `data/raw/game_tracking_2023_sample.csv`.
  Every table in the note carries the stamp.
- FULL numbers come only from `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` and are
  counts of turns and plays, never of outcomes (no outcome-bearing FULL artefact exists).
- A SAMPLE numerator is never multiplied by a FULL denominator.
- The note states that `player_play` on this machine carries rows only for the 497 players in
  `players.csv`; any outcome that lives on another player's row is structurally unavailable.

## 6. Acceptance criteria for the note

1. Five routes, each with all six fields of §4.
2. Every number traceable to `outputs/outcome_link/` artefacts produced by one committed script.
3. Every geometric attribution labelled **INFERENCE** at first use and in the route table.
4. Ambiguity either measured with a stated denominator, or explicitly marked *not measurable on
   the local sample*, with the protocol that would measure it.
5. A single decision for Conor, with a recommendation and one-sentence reason.
6. No finding language anywhere; grep for "predicts", "shows that", "evidence that", "effect of"
   returns only negations.
7. `TASK.md`, `notes/report-draft.md`, the band table and the figures are byte-identical before
   and after the cycle (verified with `git status`).

## 7. Proof requirements

- `scripts/outcome_link_feasibility.py` — deterministic, one code path, explicit column
  selection, writes `outputs/outcome_link/feasibility_routes.csv`,
  `feasibility_turns.csv`, `feasibility_summary.json`.
- `tests/test_outcome_link_feasibility.py` — asserts the headline counts against the inputs and
  the note's quoted figures.
- Full test suite green on the committed tree.
- `notes/decisions.md` entry (D-number) recording the method, the numbers and the limits.
