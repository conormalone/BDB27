# TASK: the Combine does not test the shallow break

NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

---

## The claim

**The Combine's defensive-back drill battery barely tests the 30–60° break, the band games
ask for six times more often by share, so the Combine should add a 30–60° turn drill to its
battery.**

That is the whole task. One claim, one piece of evidence for it (supply against demand by
turn angle), and one recommendation that follows from it (the drill in
`notes/drill-proposal.md`). The submission is an analysis of the test battery, not of any
player.

In numbers `[FULL]`: 30–60° turns are 13.26% of valid DB game turns and 2.21% of valid DB
Combine turns — a **6.01× shortfall in share**. The over-supply is at the other end: 120–150°
plants are 42.32% of Combine DB turns against 19.51% of game turns, 2.17× the game's share.

---

## The evidence

Nothing beyond these four blocks belongs in the writeup.

### 1. Supply against demand, by turn-angle band `[FULL]`

| Band | Combine | Share | Game | Share | Game ÷ Combine share |
|---|---:|---:|---:|---:|---:|
| 30–60° | 26 | 2.21% | 7,777 | 13.26% | **6.01×** |
| 60–90° | 237 | 20.10% | 13,077 | 22.29% | 1.11× |
| 90–120° | 159 | 13.49% | 13,016 | 22.19% | 1.65× |
| 120–150° | 499 | 42.32% | 11,444 | 19.51% | 0.46× |
| 150–180° | 128 | 10.86% | 8,137 | 13.87% | 1.28× |
| >180° | 130 | 11.03% | 5,213 | 8.89% | 0.81× |
| Total | 1,179 | | 58,664 | | |

Shares are of each setting's own valid turns. The same shortfall at 30–60° holds in all five
groups: DL 4.60% vs 9.76% (2.12×), WR 8.62% vs 14.45% (1.68×), TE 10.54% vs 14.23% (1.35×),
OL 6.51% vs 9.34% (1.43×) `[FULL]`.

Population `[FULL]`: 1,179 valid DB Combine turns from 122 players; 58,664 valid DB game
turns from 115 players over 2,234 player-games, seasons 2023–2025.

### 2. What the shallow Combine turns are `[FULL]`

The 26 turns in the 30–60° band are **not a test**. They come from 16 players across 9
different drills, median **1 turn per player**, maximum 4, and 14 of the 26 are fragments of
the 3-cone. No player is tested on that turn more than once, so the band is sampled by
accident, not by design.

### 3. The battery is built for a different turn `[FULL]`

The existing battery buys the deep plant. Measured per drill, median turn angle, entry speed,
exit speed and apex speed:

- **3-cone** — 70.6°, entry 3.98, exit 4.96, apex 3.16 yd/s: a **rounded accelerating cut**.
  Speed at the exit is higher than at the entry; the player rounds the corner and drives out.
  37.8% of its turns read as plants.
- **Short shuttle** — 179.6°, entry 3.55, exit 5.84, apex 0.34 yd/s: the **hard
  sink-and-reverse**. Speed collapses to a standstill and the player re-accelerates the other
  way. 97.1% of its turns read as plants.

Plant-ness rises with angle. In games, 15.3% of 30–60° turns are plants against 97.7% of
turns past 180° `[FULL]`. So the battery is not failing to test turning in general; it is
testing the decelerate-and-reverse turn, and the shallow break at speed is the shape it
leaves out.

**Do not write that games ask for "a single plant" at 30–60°.** They do not: 84.7% of game
turns in that band are arcs. The correct statement is that games ask for a shallow
**arc-like** break at moderate speed and the battery answers with a deep plant.

### 4. The drill that would close it `[PROPOSAL]`

The tempo break, from `notes/drill-proposal.md`: a 45° break at speed to both sides, six
reps (three each way), an 8–10 yd approach reaching 3–5 yd/s at the break, a 5 yd
re-acceleration through a gate, about 90 s, on the existing 3-cone footprint. It is a
**design proposal awaiting Conor's sign-off**, not a finding, and a 45° break sits on the
30–45/45–60 boundary, so it is a boundary sample and not a clean mid-band one.

---

## What may be claimed

- Band-level supply and demand, in counts and shares, from the FULL artefacts.
- Drill-level Combine medians from the real Combine detection pass at committed parameters
  (`outputs/sensitivity/speed_floor/combine_db_turns_floor2.5_control.csv`, 1,179 rows,
  reproduces the FULL band table exactly — D190). Tag these `[FULL]`.
- Plant shares, by band and by drill, from the same tables.
- Group-level statements about the battery: what it tests, what it tests repeatedly, what it
  samples by accident.
- The drill as a proposal, with its parameters.

## What may not be claimed

- **No per-prospect anything.** No Turn Coverage score, no Cut Card, no ranking, no player
  named as a case. Between-player capacity is not identified and this task does not try.
- **No outcome test.** No claim that anything here predicts pass-rush production, pressure,
  or any game result.
- **No transfer or feasibility test.** Those are not run and are not in scope.
- **No envelope or coverage arithmetic as a deliverable.** The feasibility region, coverage
  share and cut gaps are not part of the argument.
- **Not "the most common turn".** 30–60° is 13.26% of game DB turns; the modal bands are
  60–90° (22.29%) and 90–120° (22.19%). The claim is the **largest proportional shortfall**,
  which is what 6.01× states.
- **No cause.** The chips see the turn, not the reason for it: no claim about contact,
  scheme, technique or opponent.
- **No invented numbers, no extrapolation past the table, no sample number as a result.**

---

## Figures — exactly four, and no others

| # | Figure | File | Provenance | Role in the argument |
|---|---|---|---|---|
| 1 | The hole (DB band supply vs demand) | `outputs/figures/fig_the_hole.png` | **FULL** | the headline; quote its counts and shares |
| 2 | The same across positions | `outputs/figures/fig_all_positions.png` | **FULL** | the shortfall is not a DB artefact |
| 3 | Every Combine turn at its measured angle | `outputs/figures/fig_drill_angle_scatter.png` | `PLACEHOLDER_SAMPLE` | shows the battery's angles by drill; name it, state its stamp, quote nothing from it |
| 4 | The proposed drill geometry | `outputs/figures/fig_drill_schematic.png` | **SCHEMATIC** | a drawing of the proposal; carries no measurement |

No fifth figure. No other figure may be named in the writeup, the notebook or the draft.

**Video.** Conor holds a clip of an in-game run. If it is used it is illustration, placed
where the demand side of the argument needs a picture, captioned with what it shows and the
timestamp, and it supports no number. Nothing about its content may be asserted that Conor
has not stated.

---

## Honesty limits — all of these must appear in the writeup

1. **10 Hz heading noise.** Heading comes from smoothed x/y, and the smoother is the most
   consequential choice in the pipeline; every conclusion is reported under the alternative
   smoothers, which move the measured angle by about 6° median.
2. **30° angle floor.** The detector cannot see a turn below 30°, so the 30–60° band is
   censored at its own floor and its Combine count is a **lower bound**, not a census.
3. **The 3-cone's measured angle is the least reliable in the set.** It is the drill where
   angle disagrees most with drill geometry, so its contribution to the shallow band is a
   lower bound. The FULL-provenance quantification is `[PENDING]`; the sample-mode figure
   exists and must not be quoted.
4. **No orientation in the Combine.** `o` exists in game data only, so in the Combine a plant
   cannot be separated from a backpedal or a shuffle; facing-decoupled movement is invisible
   there.
5. **Combine entry speeds sit below game speeds.** Cell-level comparison is partly
   extrapolated by construction; band-level shares are the claim for that reason.
6. **Reversal-class turns are measured as plants** (apex, exit, regain), never as v·ω: a
   circle fitted to a hairpin reports a radius that makes the acceleration non-physical.
7. **Provenance.** `PLACEHOLDER_SAMPLE` output is never interpreted, quoted or tuned against.
   The FULL artefacts are `data/interim/full_run_20261111/` (band table, game turn table,
   manifest), the figures stamped FULL, and the real-Combine control pass above.
8. **Selection.** Tracked players are the drafted group: 122 Combine players and 115 game
   players, not all NFL defensive backs. The demand map describes who reached the league.
9. **Season coverage is uneven.** The 2023 tracking file is about a third the size of 2025's
   for the same number of games, so season rates are not comparable; read the pooled figure.

---

## Binding engineering rules

- **Provenance.** Every output carries `PLACEHOLDER_SAMPLE` (sample mode) or `FULL` (full
  mode); full-mode scripts assert `FULL`. Thresholds are never set from the sample.
- **No invented data.** Never invent a column, a number or a result. Missing or ambiguous
  data goes to `notes/blockers.md` and stops the work.
- **No new stages, no new analysis, no new threshold.** The pipeline below already produces
  everything the claim needs. If a number is not in a named artefact, it does not go in the
  writeup.
- **Memory-safe code.** Never load full CSVs into pandas; Parquet plus DuckDB or lazy Polars,
  explicit column selection, per-game or per-player chunks, downcast dtypes, wall time and
  peak RAM to `outputs/run_log.csv`.
- **One code path** for `--mode sample|full`; config lives in `config.yaml`, no magic numbers.
- **DB only**, unless Conor says otherwise (standing policy, D183).
- **Log decisions** in `notes/decisions.md`, with the reason. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, deterministic outputs.

---

## Existing pipeline (already built — no additions in scope)

`00_thresholds → 01_audit → 02_turns → 03_envelope → 04_demand_coverage → 05_validate →
06_figures`

Stage 02 produces the turn table both settings are counted from; stage 06 renders the four
figures. Stages 03, 04 and 05 built the envelope, coverage and validation machinery for the
framing this task supersedes; they stay in the repo, and **nothing they produce is part of
this claim**.

## Layout

`/data/raw` `/data/interim` `/src` `/notebooks` `/notes` `/outputs/figures`

## Stop and write to `notes/blockers.md` if

A number the claim needs is not in a FULL-provenance artefact; the band table and the drill
table disagree; or an instruction here conflicts with the competition rules.

---


## Out of scope — deleted from this project on 2026-11-13

Kept as a list so it is not reintroduced: the per-prospect **Turn Coverage score**; the
**Cut Card**; the **confirmatory transfer and feasibility** structure; the **DL outcome
tests**; **outcome-weighted demand**; the envelope and coverage arithmetic as a deliverable;
the **phased 0–7** task framing; and the **prereg gate** before a full-data run. The
superseded spec is archived at `notes/archive-tasks/TASK-turn-coverage-cut-card-2026-11-13.md`
and is history, not instruction.
