# Drafting spec — BDB27 submission draft (for the Writer)

**Owner:** PM. **Consumer:** the Writer role. **Output:** `notes/report-draft.md`.
**Binding task:** `TASK.md` — *Turn Coverage and the Cut Card*. Where this spec and
`writer-role.md` disagree, **this spec and `TASK.md` win**; `writer-role.md` is stale (it
still describes the superseded combine-fatigue task and an LMM structure) and must not be
used for structure, section list or method.

---

## 1. What the draft is

A ~2,000-word markdown draft of the Kaggle writeup for the **current** task, in
`TASK.md`'s section order, at most **5 figures**, every quantitative claim tagged with
its provenance, and an explicit block at the end naming what is still missing.

It is a **draft for Conor's review**, not the submission. It is allowed — required — to
have holes. It is not allowed to fill a hole with a number.

## 2. The honesty rule (the reason this spec exists)

Every stage output in `outputs/` (turns, envelope, demand_coverage, audit) is stamped
`PLACEHOLDER_SAMPLE`. Under `TASK.md` and the project rules those outputs may **never**
be interpreted, reported or tuned against. The only full-data artefacts that exist are:

| artefact | provenance |
|---|---|
| `data/interim/full_run_20261111/bdb27_full_game_bands.csv` | **FULL** |
| `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` | **FULL** |
| `data/interim/full_run_20261111/bdb27_full_game_manifest.json` | **FULL** (run record) |
| `outputs/figures/fig_the_hole.{png,csv}` | **FULL** |
| `outputs/figures/fig_all_positions.{png,csv}` | **FULL** |
| `outputs/figures/fig_drill_schematic.{png,csv}` | **SCHEMATIC** (a drawing, no data) |

Everything else is `PLACEHOLDER_SAMPLE`. Therefore the confirmatory analyses — transfer vs
noise ceiling, feasibility, DL outcome value, per-player coverage, the envelope parameters
— **do not exist on full data yet**. The draft states that plainly and describes what each
will test. It does not describe results that have not been produced.

## 3. Provenance tags used in the draft

Tag every number and every figure at the point of use, in square brackets:

- `[FULL]` — from the FULL artefacts above.
- `[PROPOSAL]` — a design choice from `notes/drill-proposal.md`, signed off by nobody yet.
- `[SCHEMATIC]` — a drawing, carries no measurement.
- `[PENDING]` — the section's analysis has not been run on full data.

`[SAMPLE]` numbers may **not** appear. If a sentence needs a sample number to work, the
sentence is wrong: rewrite it as what the analysis will test.

## 4. Section-by-section: what may be said, from where

Word budget in brackets; total ≈ 2,000 ±10%.

### 1. The football question (230)
Source: `TASK.md` (question, focus), `notes/phase1-audit.md` (drill battery *names* only).
Content: a DB has to break at moderate speed — the shallow cut — and the Combine's
change-of-direction tests are built around the deep plant. Ask the question directly: does
the Combine test the turn the job asks for?
Allowed: the FULL band numbers as the set-up; the five position groups.
Not allowed: any game-side number that is not in the band table.

### 2. Data audit (200)
Source: `notes/schema.md` (schema facts — allowed, they are read from the files, not stage
outputs), `notes/blockers.md` TB1–TB6, `notes/rules.md` (limits, marked unverified).
Content: what the two tracking files are; 10 Hz; Combine has no `o`; `o` exists in game and
is used only for contamination reporting; the game run covers 1,000 games / three seasons;
510 Combine attendees, 496 tracked in games; the draft's stated limits (2,000-word cap is a
working assumption, unverified against the 2027 rules page).
Allowed schema facts: row counts in `schema.md` §1, 10 Hz, `dir` convention, `dis` has no
documented unit (TB1), `player_play` is not a participation table (TB4).
Not allowed: audit statistics (heading MAE, speed error, overlap, ICC).

### 3. Kinematics and turn detection (320)
Source: `TASK.md` Phase 2, `notes/phase2-turns.md`, `notes/phase1-audit.md` (method only).
Content: one code path for both settings; Savitzky–Golay `g9p3` primary with alternative
smoothers carried as a spread; derivatives from smoothed x/y only, never `dir`; turn =
heading change ≥ 30° across 0.5 s with a 2.5 yd/s floor at entry and exit, segmented from
approach speed maximum to exit speed maximum; plant vs arc by apex retention; reversal-class
turns measured as plants, never as v·ω; gaps flagged, interpolated only outside the core.
State the detector's validation design (drill geometry, estimated cone positions on half the
turns and evaluated on the other half) and that **its numbers are `[PENDING]` a
FULL-provenance run** even though the Combine file is complete.
Not allowed: precision/recall values, angle-error values, 3.1° synthetic recovery.

### 4. The envelope (220)
Source: `TASK.md` Phase 3, `notes/phase3-envelope.md` (estimator design only).
Content: why an upper envelope and not a mean (observed turns are not maximal); the quantile
random-effects form; shrinkage; the friction-circle form as a sensitivity; the feasible
region on (entry speed × turn angle) with observed and extrapolated cells always separated;
bootstrap SEs, explicitly not a posterior. Then the honest sentence: the between-player
variance component decides whether any player-level statement is possible, and on full data
**that fit has not been run** `[PENDING]`.
Not allowed: any τ² value, the boundary count, any per-player capacity, "43 of 51".

### 5. The demand map (220)
Source: `TASK.md` Phase 4, FULL band table, `notes/phase4-demand-coverage.md` (design only).
Content: demand is measured, not assumed — every tracked DB turn in three seasons, binned by
angle band; **the FULL band distribution is the demand map at band resolution** and is a
result; the cell-level (speed × angle) map is `[PENDING]`; the denominator is all valid DB
game turns (58,664), not the snap-classified subset; tracked players are the drafted group,
which is a stated limitation of demand as well as supply.
Allowed: 58,664; 7,777; 13.26%; 115 players; 2,234 player-games; per-season split with its
caveat; 26.3 valid DB turns per player-game (arithmetic on the FULL table).
Not allowed: 2,262, 1,111, 49.6% `OTHER`, 4 vs 97, any cell-level count.

### 6. Coverage (200)
Source: `TASK.md` Phase 5, `notes/phase4-demand-coverage.md` (formula only).
Content: Turn Coverage as the demand-weighted share of a player's feasible region, split
observed/extrapolated; the Cut Card as an appendix artefact only; why it is not the
headline (no identified per-player capacity until the variance question is answered on full
data).
`[PENDING]` throughout. Not allowed: 0.961, any coverage score, any cut gap, any card.

### 7. Validation (260)
Source: `TASK.md` Phase 6, `notes/pending_full_run.md`.
Content: the three tests, each stated as a test — transfer against the noise ceiling;
feasibility (share of game turns outside the Combine region, by speed range); outcome value
for DL against a stopwatch-and-size baseline with player-grouped CV; smoother and definition
sensitivity; permutation and placebo; smallest detectable effect read off the power grid once
the full-data n is known. Say what a null would mean and what would falsify the story.
`[PENDING]`. **No sentence may imply any of these have been run.** Do not say "we find",
"the correlation is", "coverage predicts".

### 8. Uses for scouts and coaches (200)
Source: argument, grounded only in the FULL descriptive finding.
Content: what a team can do *now* with the band-level result (audit the drill battery, not
the player); what the proposed tempo break would add and what it would not (it buys
reliability per player and a band the battery does not test; it does not rank prospects);
why a per-prospect score is deliberately not offered.
Allowed: the drill design parameters `[PROPOSAL]` (45° cut, 6 reps, 3 each side, 8–10 yd
approach, 5 yd re-acceleration gate, ~90 s, existing 3-cone footprint) and the fact that the
45° break sits on the 30–45/45–60 edge (D188).
Not allowed: any claim about contact, scheme, coverage technique or opponent quality.

### 9. Limitations (200)
Source: `TASK.md` Phase 7 limitations list, `notes/blockers.md` TB1–TB6.
Content: contact invisible; 10 Hz heading noise; no Combine `o`; plants not separable from
backpedals and shuffles in the Combine; Combine entry speeds below game speeds so some cells
are extrapolated; tracked = drafted selection; vendor pressure labels; uneven per-season
tracking coverage (2023 file ≈ one third the size of 2025's for the same game count, so
season rates are not comparable); `dis` unusable; `player_play` not a participation table.

### Closing block — "What is still missing before submission" (150, not prose)
A list: each pending section, the artefact that will fill it, and one line on what would
change the argument if it comes back unfavourable.

## 5. Figures — exactly five, in `TASK.md`'s order

| # | slot (`TASK.md` CURRENT FOCUS) | file | provenance | how the draft uses it |
|---|---|---|---|---|
| 1 | (a) turn-band supply vs demand, DB | `outputs/figures/fig_the_hole.png` | **FULL** | the headline; quote its counts and shares |
| 2 | (b) the same across positions | `outputs/figures/fig_all_positions.png` | **FULL** | quote per-position shares and ratios |
| 3 | (c) the drill spec and the cells it would fill | `outputs/figures/fig_drill_schematic.png` | **SCHEMATIC** | describe the drawing; no measurement |
| 4 | (d) what the existing DB drills cover | `outputs/figures/fig_drill_coverage.png` | PLACEHOLDER_SAMPLE | name it, state its stamp, quote **nothing** from it |
| 5 | (e) measurement limits | `outputs/figures/fig_measurement_limits.png` | PLACEHOLDER_SAMPLE | name it, state its stamp, quote **nothing** from it |

Each figure: one caption line carrying the filename and the provenance tag. No other figure
may be referenced. Do not embed images; reference by path.

## 6. Voice

Active, concrete, no throat-clearing. No "this report will show". No bullet-point soup —
prose with tables where a table is shorter. No banned word list beyond common sense; the
`writer-role.md` anti-tic list still applies as style, not as structure.

## 7. What the Writer must not do

- Invent a number, a threshold, a citation, a player name, or a result.
- Present any `[SAMPLE]` figure as evidence, in text, caption or table.
- Describe the outcome test, the transfer correlation or the coverage score as if run.
- Add sections beyond the nine, or reorder them.
- Exceed five figures.
- Write past ~2,200 words.
- Rewrite `writer-role.md`, `TASK.md`, or any pipeline file.
