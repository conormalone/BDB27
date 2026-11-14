# Spec — correction cycle: the outcome-link pre-flight was a false stop (D198)

**PM's spec for the correction cycle, 2026-11-14. Binding on this cycle.** Supersedes
`notes/outcome-link-feasibility-spec.md` §6.1 only; everything else in that spec stands.

## 0. Why this cycle exists

The committed note (`notes/outcome-link-feasibility.md`, commit 0f3f3c9) opens its
FULL-notebook design with a **pre-flight stop-check that is false**:

> "If the full file averages well under ~15 rows per play — as this machine's copy does at
> 2.7 — opposing players cannot be typed by team, geometric matching is impossible, and the
> notebook must stop."

The inference is wrong. `player_play.csv` is the full file (1,001 games, all seasons) and it
types both teams on the majority of plays. The 2.7 rows/play is the *design* of a
draft-prospect cohort file, not a defect: the file carries rows only for the 497 prospects in
`players.csv`, so a play has as many rows as it has prospects on the field. A low rows-per-play
count cannot support "opposing players cannot be typed by team", and the committed note also
carries the consequences of that error: a 1.2% attributed fraction and a **zero tie rate** that
the data it used could not have produced.

Conor's authorisation for geometric proximity matching is **standing and not re-litigated**
(2026-11-14 15:37, "It's the only way, go for it."). What is corrected here is the note's
*measurement* of that route's yield and ambiguity.

The PM has independently re-derived every number below from the raw files before writing this
spec (DuckDB, explicit column selection, `all_varchar` + `TRY_CAST` as in the existing script).
The coder must re-derive them again from the inputs — **no number in this spec is to be typed
into code, an artefact or a test as a fact.** They are acceptance targets.

## 1. Numbers the PM derived independently (acceptance targets)

### 1.1 `data/raw/player_play.csv` — the availability source

| Quantity | Value |
|---|---:|
| rows | 314,197 |
| distinct plays (`game_id`,`play_id`) | 114,954 |
| distinct `nfl_id` | 497 (all 497 are in `players.csv`, which holds 510) |
| distinct games | 1,001 |
| mean rows per play | 2.733 |
| max rows on one play | 11 (one play) |
| plays with rows from **both** `possession_team` and an opponent | 67,972 = 59.13% |

Rows-per-play distribution: 1→29,845, 2→29,057, 3→23,826, 4→16,202, 5→9,454, 6→4,214,
7→1,652, 8→530, 9→147, 10→26, 11→1.

`route_ran` is recorded only on skill-position rows (WR 28,074; SLOT_WR 18,740; TE 6,027;
RB 284; T 2) and on nothing else. `separation_at_pass_forward` is recorded on receiver rows
only: 0 of the 75,428 DB-alignment rows (CB 35,017, HIGH_SAFETY 25,608, SLOT_CB 14,537,
BOX_SAFETY 266).

### 1.2 `data/raw/game_tracking_2023_sample.csv` — not an availability source

| Quantity | Value |
|---|---:|
| data rows | 1,048,575 (exactly 2^20 − 1, as the FULL notebook's §6 already says) |
| games / plays / players | 333 / 12,638 / 154 |
| plays in `player_play` for those 333 games | 31,720 |
| play-player pairs | 14,612 |
| players per play: mean / median / max | 1.156 / 1 / **4** |
| plays with both sides present | 1,138 (9.0%) |

The file holds tracks for **154 of the 497 prospects** and for **39.8% of the plays** in its
333 games. Its ≤4-players-per-play is therefore a property of the sample, not of the tracking
data: two opposing players in one frame is *possible* in the file (1,138 plays have both sides)
but rare. The 2^20−1 row count is real; the per-play player count is not a truncation artefact
in the narrow sense — it is the sampling. Either way the file **cannot** characterise data
availability, and a `multiple = 0` measured on it is structural.

### 1.3 Candidate availability — the corrected pre-flight, both populations

Definition (all from `player_play` only; **no tracking, no geometry, no projection**):

For each band turn, take the turning DB's own `player_play` row to get `own_team = team_abbr`.
A **candidate** is a row on the same `(game_id, play_id)` with `team_abbr <> own_team` **and**
`route_ran` not null. Count candidates per turn; count how many carry a non-null
`separation_at_pass_forward`.

Turn key is `(game_id, play_id, nfl_id, frame_start)` — **not** `(game_id, play_id, nfl_id)`.
362 FULL turns and 17 SAMPLE turns are a second turn by the same DB on the same play; grouping
on the three columns silently drops them.

| | `[SAMPLE]` 326 turns | `[FULL]` 7,777 turns |
|---|---:|---:|
| turn's DB row present with a team label | 326 | 7,777 |
| no opposing player row on the play | 130 | 1,462 |
| **no opposing route-runner row** | **246 (75.5%)** | **4,768 (61.3%)** |
| **exactly one** opposing route-runner | **67 (20.6%)** | **2,002 (25.7%)** |
| two | 13 (4.0%) | 815 (10.5%) |
| three or more | 0 | 192 (2.5%) |
| **≥1 opposing route-runner (ceiling on `unique`)** | **80 (24.5%)** | **3,009 (38.7%)** |
| ≥1 candidate with a separation value | 67 (20.6%) | 2,601 (33.4%) |
| exactly one candidate with separation | 58 (17.8%) | 1,731 (22.3%) |
| two or more candidates with separation | 9 (2.8%) | 870 (11.2%) |

Every one of the 7,198 FULL band plays and all 809 games they sit in are present in
`player_play`, so **the FULL column above is measured, not extrapolated**. It is the first
number in this project that spans the full band population without a SAMPLE→FULL step.

Ceiling arithmetic (stated as arithmetic on measured counts, not as a result):
`unique ≤ turns with ≥1 candidate`. On FULL, 3,009 turns is 38.7% of 7,777, so the ≥500-turns
gate needs 500/3,009 = 16.6% of candidate turns to be unique at 3 yd, and the ≥30%-share gate
needs 2,333/3,009 = 77.5%. On SAMPLE the ceiling is 24.5%, below the gate — the local gate is
unreachable **before** any geometry is run, which is consistent with (and a better explanation
of) the note's "no estimate here".

### 1.4 The 13 two-candidate SAMPLE turns (tie characterisation)

All 13 are two **distinct** receivers (no duplicate rows in `player_play`); 12 distinct plays
(turns on `games 2024010709/play 3880` are two different DBs on one play). In **9** of the 13
both candidates carry a separation value. The nine separation pairs differ by 0.18–13.6 yd,
median 1.26 yd; in 6 of the 9 a `target` row identifies which receiver the ball went to — a
post-hoc outcome that may **not** be used to break a tie (the rule is frozen: ties dropped).

The pairs are ordinary two-receiver route combinations (WR/SLOT_WR/TE/RB with distinct routes,
e.g. `CORNER` 14.53 vs `IN` 0.93). Nothing about them is anomalous; what is unmeasured is
whether both are within 3 yd of the DB at the break frame.

## 2. The corrected pre-flight — definition and stop conditions

The pre-flight must run on `player_play` alone, on this machine, and must stop only on a
**real** condition:

* **P1 — siding.** Band turns whose DB row has no `team_abbr`, or whose `(game_id, play_id)`
  is absent from `player_play`. If this is non-zero the turn cannot be sided and the notebook
  must stop and name the turns. (Measured: 0 both populations.)
* **P2 — empty candidate pool.** Turns with ≥1 labelled opposing route-runner. If 0 → stop:
  the route is impossible in this dataset.
* **P3 — count gate unreachable.** If the ceiling (P2 count) < 500 → stop the estimator: the
  ≥500-turns gate cannot be met at any radius.
* **P4 — share gate unreachable.** If the ceiling < 30% of band turns → the ≥30%-unique gate
  cannot be met; print `GATE UNREACHABLE — descriptive only` and refuse to write an estimate.
* **P5 — cohort ceiling.** Always print: the share of turns whose candidate count is limited
  by the 497-player cohort rather than by the play. Wording is fixed in §3.3.

Measured verdicts: FULL → P2 3,009 (38.7%), P3 cleared, P4 **live** (38.7% ≥ 30%), P5 as
above. SAMPLE → P4 fires (24.5% < 30%) — which is the honest local statement.

The pre-flight must **never** read `game_tracking_2023_sample.csv`, and must contain no
rows-per-play threshold of any kind.

## 3. Artefacts

Extend `scripts/outcome_link_feasibility.py`; keep one code path by putting the shared logic in
a new `src/bdb27/outcome_link.py` used by both the script and the notebook.

1. `outputs/outcome_link/feasibility_candidates.csv` — one row per band turn per population
   with: population, the four key columns, `own_team`, `pp_opp_rows`, `pp_opp_route_rows`,
   `pp_opp_route_sep_rows`, `pp_candidate_status` ∈ {`none_on_play`, `single`, `multiple`}.
2. `outputs/outcome_link/feasibility_preflight.csv` — one row per population with the P1–P5
   quantities, the ceiling, both gate tests and the verdict string.
3. `outputs/outcome_link/feasibility_ambiguity_candidates.csv` — candidate-level ambiguity,
   the same status vocabulary as §1.3, per population, with the tie counts.
4. Existing `feasibility_ambiguity.csv` (geometric sweep on the 2023 tracking sample) stays,
   but its summary gains `geometry_scope_note` recording: rows in the file, players tracked,
   max tracked players per play, plays covered, and the sentence *"this table measures the
   sample file, not data availability; multiple = 0 here is structural."*
5. `feasibility_summary.json` gains a `preflight` block for both populations and keeps every
   existing key. `feasibility_routes.csv` R1/R2 gain candidate-ceiling columns; their
   `sample_turns_usable` stays the geometric count (`[SAMPLE]`), which is now labelled as a
   property of the 2023 file.
6. Determinism: two consecutive runs byte-identical apart from `generated_utc` and hashes of
   nothing that changed. Fixed order, no `set` iteration in output paths.

Runs must stay inside the memory rule (`SET memory_limit`, explicit columns, no full-CSV
pandas load). The FULL candidate pass must not materialise a 7,777 × 22 join in pandas.

## 4. Note correction (`notes/outcome-link-feasibility.md`)

* A dated **CORRECTION (D198)** block at the very top, above `## 1`, that (a) quotes the false
  stop-check verbatim, (b) says what is false and why, (c) gives the corrected candidate table
  and gate verdict, (d) says the geometric tie rate is **unmeasured, not zero**. It must be
  impossible to read the note without seeing it.
* §3/§3.1: the candidate-level table sits **beside** the geometric sweep; the sweep is
  labelled as a property of the 2023 sample file; `unobservable` is redefined to say what it
  is unobservable *in*.
* §4 R1 row: the `[SAMPLE]` 4/326 = 1.2% stays as a *geometric* count on the 2023 file, with
  the candidate ceiling next to it; the `[FULL]` ceiling 3,009/7,777 = 38.7% is stated.
* §6.1: replaced by the corrected pre-flight (P1–P5), no rows-per-play condition.
  §6 gains one line: the pre-flight now ships as an executable notebook (see §5).
* §7: corrected — the sample cannot carry an estimate because the candidate ceiling is 24.5%
  under the gate and the geometric hit rate is unmeasured, not because "the tracking density"
  explains it.
* Frozen rule, sensitivity set, gate numbers: **unchanged**. If any number in §1.3 forced a
  change, the note must say why; the PM's reading is that none does.
* Provenance stamps as before. No model, no coefficient, no interval, no finding.

## 5. Notebook

New `notebooks/bdb27_outcome_link_preflight.ipynb`, runnable on this machine with no full
tracking: `DATA_DIR` pattern as in `bdb27_full_game_turns.ipynb`, §1 config, §2 setup/find
repo, §3 inputs (`player_play` required, `players.csv` optional, band-turn table required;
`game_tracking_2023_sample.csv` **not** an input), §4 **PRE-FLIGHT** (P1–P5, printed, stops on
a real condition only), §5 candidate availability by band turn and its totals, §6 markdown
"what the geometric step must still do" — explicitly **not implemented**, naming the frozen
rule and the two gates and the fact that the tie rate is unmeasured.

Committed **unexecuted** with no baked outputs, UTF-8 encoding on every text write, `.as_posix()`
for every path in SQL, no POSIX-only code — the same portability rules as the other notebooks.
It must not import anything that does not exist on Windows.

## 6. Tests (`tests/test_outcome_link_feasibility.py`)

Recompute, never hard-code:

* `player_play` rows / plays / players / max rows per play / both-sides plays; the max is 11 and
  no test may assert 4 as a data property.
* `game_tracking_2023_sample.csv` row count 2^20−1, max 4 players per play, and that its
  player count caps at 154 of 497.
* The per-turn candidate counts on both populations, including the turn-key trap: the code must
  return 326/7,777 rows, and a test must fail if a three-column grouping is used.
* The nine two-candidate separation pairs (count, and that no tie is resolved anywhere).
* The pre-flight verdicts: FULL `GATE LIVE`; SAMPLE `GATE UNREACHABLE`; and that neither
  reads the tracking file (assert the module's preflight function takes no tracking argument).
* The note's correction block exists and the false sentence is quoted as false, not repeated as
  live instruction; `test_note_quotes_the_artefacts` must stop asserting "4 of 326" as the
  headline number.
* The notebook: parses, has no outputs, and its pre-flight cell executes against the real
  `player_play` and prints the verdict (skip when the data is not mounted, as now).

## 7. Out of scope

`TASK.md`, the band table, the four figures, `notes/report-draft.md`, the frozen match rule, the
sensitivity set, the gate thresholds. No model, coefficient, interval, p-value. No change to
the claim. No new figure.

## 8. Acceptance

1. `scripts/outcome_link_feasibility.py` runs clean twice, deterministic, memory-safe.
2. Every number in §1.3/§1.4 reproduced by the artefact and by a test.
3. The note carries the correction block and contains no live rows-per-play condition.
4. `pytest -q` green.
5. Nothing outside the files named in §3–§6 changes.
