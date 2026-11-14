# Outcome-link feasibility for DB 30–60° turns

**Status: scoping note. No result, no model, no coefficient.** Written 2026-11-14 by the PM for
this cycle. Spec: `notes/outcome-link-feasibility-spec.md`. Evidence:
`outputs/outcome_link/` (regenerate with `scripts/outcome_link_feasibility.py`).
Binding project spec: `TASK.md` — unchanged by this cycle.

Provenance stamps are carried on every number. **`[SAMPLE]`** = `outputs/turns/turns.parquet`
(manifest `PLACEHOLDER_SAMPLE`) joined to `data/raw/player_play.csv` and
`data/raw/game_tracking_2023_sample.csv`. **`[FULL]`** =
`data/interim/full_run_20261111/bdb27_full_db_turns.parquet`, used for turn and play **counts
only** — no outcome-bearing FULL artefact exists on this machine. A `[SAMPLE]` numerator is never
multiplied by a `[FULL]` denominator anywhere below.

## 1. What was asked

Conor, 2026-11-14 14:59:

> "Without a link to separation, coverage success, tackling or injury, the recommendation is an
> assertion. Even a modest result (for example, DBs who retain more speed through 30–60° breaks
> allow less separation, after controlling for speed and position) would turn this from an
> observation into a finding."

This note answers only whether such a link is **constructible**, by which route, at what n, with
what attribution ambiguity, and what it would control for. It does not test any link, and nothing
here may be quoted as one.

## 2. The structural constraint: no DB-side outcome column exists

`player_play.csv` holds 314,197 rows over 1,001 games, 114,954 plays and 497 players. The
receiver-side outcome is present; the defender-side outcome is not.

| Column | Receiver side `[SAMPLE]` | DB-alignment rows `[SAMPLE]` |
|---|---:|---:|
| `separation_at_pass_forward` | 46,737 | **0 of 75,428** |
| `target` | 9,842 plays | 0 |
| `route_ran` | 53,308 | 0 |

DB-alignment rows are CB 35,017, SLOT_CB 14,537, HIGH_SAFETY 25,608, BOX_SAFETY 266. (The
35,017 figure that circulated earlier is the CB rows only.) Separation is observed on 35,211 of
114,954 plays (30.6%) and a `target` row on 9,842 (8.6%), because rows exist only for the 497
named players — all 115 FULL DBs are inside that 497, and so is every receiver whose separation
can ever be read.

There is **no opponent, matchup or assignment identifier** in any of the 64 columns
(`coverage_assignment` is a MAN/zone/spot label, not a player). So:

> **A DB's turn cannot be joined to any receiver outcome. It has to be *inferred* geometrically.**
> Every such link in this note and in anything built from it is an **INFERENCE**.

That inference is the whole difficulty. The project has already refused blocker inference without
authorisation; this note proposes no inference into the claim.

## 3. What *is* on a DB's own row

On the 326 `[SAMPLE]` DB 30–60° turns:

| Field | Present | Share | Use |
|---|---:|---:|---|
| `cushion` | 218 | 66.9% | pre-snap control; also a possible match check |
| `coverage_assignment` | 155 | 47.5% | control (MAN 35, zone labels 113, `NO_ASSIGNMENT` 7) |
| `pass_result` (pass play) | 202 | 62.0% | play-level outcome |
| `tackle` | 40 | 12.3% | same-player event |
| `assist` | 13 | 4.0% | same-player event |
| `caused_forced_fumble` | 0 | 0% | — |
| `tackle_for_loss` | 1 | 0.3% | — |

Exposure variable: `apex_retention` (apex ÷ entry speed) has IQR 0.571–1.000, median 0.859
`[SAMPLE]`; entry speed IQR 2.75–4.96 yd/s. There is spread to analyse, if a route exists.

## 4. The five routes

Counts are `[SAMPLE]` unless marked `[FULL]`. "Usable" means the route can actually be executed
on that turn with its outcome observed.

| id | Route | Outcome | Attribution | Usable `[SAMPLE]` | Denominator `[FULL]` | Ambiguity measured? |
|---|---|---|---:|---:|---|---|
| R1 | Geometric proximity match | receiver separation | **INFERENCE** | **5** | 7,777 turns / 7,198 plays | no — denominator too small |
| R2 | R1 restricted to MAN | receiver separation | **INFERENCE** | **2** | not countable locally | no |
| R3 | Same-player events | tackle/assist/FF/TFL | exact | 326 (40 events) | 7,777 turns | attribution exact |
| R4 | Play-level outcomes | pass_result/yards/EPA | play (unit) | 202 pass-play turns | 7,198 plays | 17 of 309 plays |
| R5 | Injury | free-text mention | name resolution | 2 | 7,777 turns | 2,122 of 2,548 mentions |

### R1 — Geometric proximity match

- **Measures:** whether a DB whose break retains more speed ends up, at pass forward, with less
  separation from the receiver he is matched to.
- **Coverage:** 326 `[SAMPLE]` turns are candidates; 5 have a route-running opponent within 5 yd
  at the break frame **and** a separation value on that opponent's own row. `[FULL]`: 7,777 turns
  across 7,198 plays.
- **Attribution:** nearest opposing route-runner at the break frame, within a fixed radius.
  **INFERENCE**, at every step. Nothing in the data identifies the DB's assignment.
- **Ambiguity:** **not measurable on the local sample.** Only 63 of 326 turns have *any* opponent
  tracked at the break frame, 10 have one within 5 yd, and 0 of the 5 usable turns has a second
  route-runner inside 5 yd — because the sample tracks 1.16 players per play (median 1, max 4),
  not 22. Those numbers measure the sample's tracking density, not real-world proximity.
  The ambiguity rate must be measured on full tracking: for each band turn, count route-running
  opponents within r = 2, 3, 5 yd and the gap to the second-nearest; report the share with a
  unique candidate.
- **Controls available:** entry speed, roster position (CB/SS/FS), alignment, `coverage_assignment`,
  `cushion`, down and distance.
- **Would count as a finding:** an association between retention and separation on turns where the
  match is unique under a rule frozen before the outcome is seen. **Would not count:** a match that
  is "nearest of several" without a uniqueness rule; a radius chosen after seeing the outcome; any
  use of the matched receiver's separation to build the match itself.

### R2 — MAN-restricted proximity match

- **Measures:** as R1, on turns where the defence declared man coverage, where a one-to-one
  assignment is the strongest available prior.
- **Coverage:** 35 `[SAMPLE]` MAN turns (10.7%); 2 usable. `[FULL]`: not countable locally —
  requires the full player_play join.
- **Attribution:** as R1, **INFERENCE**, with the MAN label strengthening the prior but not
  identifying the receiver (rub routes, picks and zone-match still exist).
- **Ambiguity:** 0 of 2 locally. Uninformative; same full-data protocol as R1.
- **Controls:** as R1 minus `coverage_assignment` (it is the restriction).
- **Would / would not count:** as R1.

### R3 — Same-player event outcomes

- **Measures:** whether a DB who retains more speed through a 30–60° break is more likely to
  record a tackle or assist on that play.
- **Coverage:** 326 of 326 `[SAMPLE]` turns — the DB's own row always exists. 40 tackles (12.3%),
  13 assists (4.0%), 0 forced fumbles, 1 tackle for loss. `[FULL]`: 7,777 turns; event counts must
  be recomputed on full data (the 12.3% is a sample rate, not a forecast).
- **Attribution:** exact — same `nfl_id`, same play. No geometric inference.
- **Ambiguity:** none in attribution. What remains is temporal (the tackle may come seconds after
  the break) and causal (a tackle is not caused by a break; it may follow a completed pass or a
  run fitting the same gap).
- **Controls:** entry speed, position, alignment, down, distance. Role is the dominant confounder:
  a box safety tackles far more often than a perimeter corner, whatever his break looks like.
- **Would count as a finding:** nothing causal. At full n it could support a pre-registered
  within-player comparison of event rates. **Would not count:** a tackle rate presented as an
  effect of turn quality; the event is neither observed at the break nor caused by it.

### R4 — Play-level coverage-unit outcomes

- **Measures:** whether plays on which a DB's 30–60° break retains more speed end with a worse
  outcome for the offence — completion, yards, EPA.
- **Coverage:** 309 `[SAMPLE]` plays carry a band turn (292 with exactly one); 202 turns fall on
  pass plays; separation is available on 67 of the 326 turns. `[FULL]`: 7,198 plays, 6,656 with
  exactly one band turn (92.5%) — the one FULL ambiguity number that can be quoted without any
  outcome data.
- **Attribution:** the play. A player attribution is defensible only where exactly one DB turned;
  even then the outcome may belong to a different defender's coverage.
- **Ambiguity:** 17 of 309 `[SAMPLE]` plays (5.5%) have two DB band turns; `[FULL]` 542 of 7,198
  (7.5%). On the other 92.5% the *turn* is unambiguous, but the *cause* of the outcome is not.
- **Controls:** coverage assignment, down, distance, field position, entry speed, position.
- **Would count as a finding:** a **coverage-unit** statement, with the unit named. **Would not
  count:** any sentence attributing a play outcome to a named DB's turn. That is the sentence
  Conor asked for, and R4 cannot produce it.

### R5 — Injury

Main's premise that no injury signal exists is **refuted**, and the correction matters.

- **Measures:** `play_description` free text names the injured player, e.g. "CHI-C.Williams was
  injured during the play"; 3,663 `[SAMPLE]` plays carry "injur", 5,125 including "hurt".
- **Coverage:** 2,548 distinct "was injured during the play" mentions across those plays. Only 426
  (16.7%) resolve to a unique `nfl_id` through initial+surname against the 510-name roster (no
  team column in `players.csv`), of which 137 are DBs (CB 66, SS 52, FS 19). Of the 326 band
  turns, 8 fall on a play with an injury mention and **2** name the turning DB.
- **Attribution:** free-text name resolution — exact where it resolves, absent where it does not.
- **Ambiguity:** 2,122 of 2,548 mentions unresolved or ambiguous (the injured player is usually
  outside the 497-player roster — most often a lineman).
- **Controls:** none usable. There is no severity, no snap count after the play, no return-to-play
  record beyond free text.
- **Would count as a finding:** nothing. This is a mention, not a measured injury, and n = 2.

## 5. Why the local sample cannot answer the geometry question

`game_tracking_2023_sample.csv` is a 1,048,575-row file — exactly 2²⁰−1, a row cap, not a random
draw — covering 333 games, 12,638 plays and 154 players, all 2023. It tracks **1.16 players per
play** (median 1, maximum 4; 14,612 play-player pairs over 12,638 plays). A proximity match needs
the defender, the receiver and the rest of the coverage shell in the same frame; the sample
averages well under one player per side of that.

Consequently the 5-or-10-out-of-326 figures above are a statement about the sample, not about the
NFL. Any claim that "few DBs have a receiver within 5 yd at the break" read off this file would be
false. The rate is unmeasured.

The break frame itself is recoverable and was verified: for all 326 turns, the player's own
tracked position nearest the stored `(x_centre, y_centre)` lies within **0.063 yd**, so the
geometric machinery works — it simply has no opponents to point at.

## 6. What a full-data answer would cost

- **Data:** full 2023–2025 tracking (22 players per frame) plus the full `player_play`. Both are
  on Conor's laptop, not here. This is a notebook, not a CLI run.
- **Pass:** for each of the 7,777 `[FULL]` band turns, take the break timestamp and position, pull
  every player in that frame, keep opposing route-runners, and record distance, the count inside
  2/3/5 yd, and the nearest-vs-second gap. That is a measurement, not a model, and it can be run
  before any outcome is joined.
- **Kill criterion (proposal, for Conor to accept or change):** if fewer than 30% of band turns
  have a unique route-running opponent inside 3 yd under a frozen rule, R1 is dead and the
  recommendation stays an observation. This threshold is **my proposal**, not a project rule.
- **Ceiling to expect:** separation exists only for the 497-player population, so even a perfect
  match is limited to turns whose covered receiver is one of them — 30.6% of plays carry any
  separation value `[SAMPLE]`. The notebook must report how many band turns clear that gate
  before anyone looks at the outcome.

## 7. What this cycle did not do

No model was fitted; no coefficient, interval or p-value is reported. No notebook was shipped: a
full-tracking path cannot be executed or tested on this machine, and shipping an untested one is
the failure this project has already had. `TASK.md`,
`notes/report-draft.md`, the band table and the four figures are unchanged.

## 8. The decision

**One decision: fund the full-data proximity notebook (R1) as the next cycle, or stop at the
observation.**

Options, with what each buys:

| Option | Buys | Cost | Can it produce Conor's sentence? |
|---|---|---|---|
| **A. R1 notebook** (+R2 inside it) | the only route to a DB-level separation statement | full tracking, 22-player frames, 7,777 turns; ambiguity gate first | **yes, if the gate passes** |
| B. R3+R4 notebook | exact-attribution event and unit-level outcomes | same data, no geometry | no — unit-level at best |
| C. Stop | nothing | — | no |

**Recommendation: A.** It is the only route whose outcome is the quantity Conor named, and its
fatal risk (attribution ambiguity) is measurable *before* any outcome is joined — so it can be
killed cheaply rather than argued about later. B is worth doing only after A, as a robustness
check with exact attribution; on its own it cannot support a DB-level claim, and R5 cannot support
any claim at all.
