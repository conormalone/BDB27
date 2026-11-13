# Drafting spec — BDB27 submission draft (for the Writer)

**Owner:** PM. **Consumer:** the Writer role. **Output:** `notes/report-draft.md`.
**Binding task:** `TASK.md` @ (this commit) — *the Combine does not test the shallow break*.

**`writer-role.md` is overridden, not amended.** It still describes the superseded
combine-fatigue project (LMM, in-game decay, executive summary, "2–3 ways to use the model").
None of that applies. This spec and `TASK.md` are the only structure; where any other document
disagrees with them, they win. The anti-tic list in `writer-role.md` still applies as style.

---

## 1. What the draft is

A **~2,000-word** markdown draft of the Kaggle writeup for one claim: the Combine barely tests
the 30–60° break that games ask for six times more often by share, so it should add a 30–60°
turn drill. Seven sections, **exactly four figures**, every number tagged with provenance. The
limits live in §6 and §7; the former closing list of what is not claimed is folded into them
(D194).

It is a draft for Conor's review. It is not allowed to fill a hole with a number, and it is
not allowed to widen the claim to fill space.

## 2. The honesty rule

Every number in the draft comes from a named artefact in §4. If a sentence needs a number that
is not there, the sentence is wrong — rewrite it as a statement about the battery, or cut it.

`PLACEHOLDER_SAMPLE` output is **never** evidence. It may be named (figure 3 only), with its
stamp, and nothing read from it.

## 3. Provenance tags

- `[FULL]` — band table, game turn table, figures 1–2, and the real-Combine control pass.
- `[PROPOSAL]` — the drill design, unsigned.
- `[SCHEMATIC]` — figure 4: a drawing, no measurement.
- `[PENDING]` — an analysis not run on full data (only used for the 3-cone geometry check).

`[SAMPLE]` numbers may not appear anywhere, in text, table or caption.

## 4. Where each number comes from

| # | Source | Provenance | What may be quoted |
|---|---|---|---|
| A | `data/interim/full_run_20261111/bdb27_full_game_bands.csv` | **FULL** | every band count and share, both settings, all five groups (1,179 / 58,664; 26 / 7,777; 2.21% / 13.26%; 6.01×; 499 / 11,444; 42.32% / 19.51%) |
| B | `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` | **FULL** | 58,664 valid DB game turns; 115 players; 2,234 player-games; per-band medians of entry, apex and exit speed and apex retention; plant share by band — 15.3% at 30–60°, 24.3% at 60–90°, 51.5% at 90–120°, 79.0% / 93.4% / 97.7% at 120–150° and above. Band values are read as **two families with a step at 90–120°**, never as one gradient between the 30–60° and >180° endpoints (D181, D194) |
| C | `outputs/sensitivity/speed_floor/combine_db_turns_floor2.5_control.csv` | **FULL** (real Combine detection at committed parameters; reproduces A exactly — D190) | 1,179 Combine turns, 122 players; 26 in band from 16 players across 9 drills, median 1, max 4, 14 from the 3-cone; per-drill medians: 3-cone 70.6° / entry 3.98 / exit 4.96 / apex 3.16; shuttle 179.6° / 3.55 / 5.84 / 0.34; plant share by drill (3-cone 37.8%, shuttle 97.1%); composition of the 60–90° band — 237 turns from 110 players, of which Teryl Austin 2 contributes 74 (median 74.1°), the back-pedal-and-90 break 65 (84.3°), the 3-cone 47 (67.1°) and the gauntlet 36 (84.2°) |
| D | `outputs/figures/fig_the_hole.{png,csv}` | **FULL** | as A |
| E | `outputs/figures/fig_all_positions.{png,csv}` | **FULL** | per-position 30–60° shares: DL 4.60 vs 9.76, WR 8.62 vs 14.45, TE 10.54 vs 14.23, OL 6.51 vs 9.34 |
| F | `outputs/figures/fig_drill_angle_scatter.{png,csv}` | `PLACEHOLDER_SAMPLE` | **name only, quote nothing** |
| G | `outputs/figures/fig_drill_schematic.{png,csv}` | **SCHEMATIC** | describe the drawing, no number from it |
| H | `notes/drill-proposal.md` §2 | `[PROPOSAL]` | 45°, six reps (three each way), 8–10 yd approach, 3–5 yd/s at the break, 5 yd re-acceleration gate, ~90 s, existing 3-cone footprint |
| I | `notes/schema.md`, `notes/rules.md` | schema facts | 10 Hz; Combine has no `o`; `dir` = degrees, 0 at +y, clockwise; `dis` has no documented unit; 2,000-word cap is a working assumption |

Arithmetic on A and B is allowed and is tagged `[FULL, arithmetic]` — for example 26.3 valid
DB game turns per player-game against 9.7 per Combine player.

## 5. Sections and word budget (total ≈ 2,000 ± 10%)

### 1. The football question (250)
Hook with the job: a defensive back's living is the break at moderate speed, not the
decelerating reversal. State the claim in the first paragraph with its headline numbers
(13.26% vs 2.21%, 6.01×, both `[FULL]`), and say plainly what the answer is: the Combine does
not test that turn.

### 2. What the data is (250)
Two tracking files at ~10 Hz across 2023–2025. Populations: 122 Combine players with 1,179
valid DB turns; 115 game players with 58,664 valid DB turns over 2,234 player-games `[FULL]`.
One detector for both settings, so the comparison is like-for-like. Orientation exists in the
game file and not in the Combine. Provenance tags explained in one sentence.

### 3. Supply against demand (380)
The band table (all six bands), then the 6.01× in prose. **Figure 1.** Then the per-position
check **Figure 2** — the shortfall holds in all five groups, so it is not a DB artefact. Then
the sharpest fact in the paper: the 26 shallow Combine turns are 16 players across 9 drills,
median one turn each, 14 of them 3-cone fragments `[FULL]`. That is accident, not design, and
it is the difference between "the battery samples it" and "the battery tests it".
Optionally anchor this section with Conor's in-game clip (see §8).

### 4. What the battery actually tests (350)
The honest mechanism, and the section the last draft got wrong. The 3-cone is a **rounded
accelerating cut** — median 70.6°, entry 3.98, exit 4.96, apex 3.16 yd/s, exit faster than
entry, 37.8% of its turns read as plants `[FULL]`; at 70.6° it sits in the 60–90° band. The
short shuttle is the **hard sink-and-reverse** — 179.6°, entry 3.55, exit 5.84, apex 0.34,
97.1% plants `[FULL]`.

Then the game data as **two families with a step**, never as one gradient: 30–60° and 60–90°
are one arc family (entry 3.68 vs 3.63 yd/s, apex retention 0.857 vs 0.934, plant share 15.3%
vs 24.3%); 90–120° is neither (51.5% plants, 0.717 retention); above 120° is the plant family
(79.0% / 93.4% / 97.7% plants, apex 1.90 → 1.04 → 0.58 yd/s) `[FULL]`.

Conclusion: the battery's arc testing sits at 60–90° — the 3-cone, with Teryl Austin 2 and the
back-pedal-and-90 break also landing in that band — and its plant testing at 120–150° and
above (499 turns in that band) `[FULL]`. What goes unsampled is the **shallow half of the arc
family**, not turning at speed in general. The battery is not short of turns; it is 1,179
turns aimed at a different shape. **Figure 3** here, named with its stamp and nothing read
from it.

### 5. The drill that would close it (330)
The tempo break `[PROPOSAL]`, with parameters and the reason each parameter was chosen (45° at
speed, 3–5 yd/s entry, six reps). What it buys: deliberate, repeated measurement of the same
turn within a player, and coverage of a band the current battery reaches only by fragment. What
it does not buy: a ranking, or a clean mid-band sample — 45° sits on the 30–45/45–60 boundary.
Add rather than replace: games still demand the big plant in volume. **Figure 4** — the
geometry, a schematic, explicitly not a finding.

### 6. What this does not say (220)
No per-player score, no Cut Card, no ranking: between-player capacity is not identified and a
score would be the group's value wearing a player's name. No outcome claim, no transfer claim,
no feasibility claim — none of that is run and none of it is needed for the recommendation. No
cause: the chips see the turn, not the contact or the scheme.

### 7. Measurement limits (220)
10 Hz heading noise and the ~6° median smoother spread on measured angle; the 30° detector
floor censoring the shallow band's own lower edge, making the 26 a lower bound; the 3-cone as
the least reliable measured angle in the set, `[PENDING]` on full provenance; no orientation in
the Combine, so plants cannot be separated from backpedals and shuffles; Combine entry speeds
below game speeds, so band shares rather than cells carry the claim; reversal-class turns
measured as plants, never v·ω; tracked players are the drafted group; uneven per-season
tracking coverage.

### Closing block — folded into §6 and §7 (0) — D194
There is no separate "What is not claimed" list any more. Its five shorthand limits are already
stated in full in §6, and the sixth point — 60–90° and 90–120° are the modal bands and 30–60°
carries the largest proportional shortfall — sits in §7 as the reason that band carries the
argument. Do not reinstate the list: repetition cost 80 words against a ceiling that was
already breached.

## 6. Figures — exactly four, in this order

| # | File | Provenance | Use |
|---|---|---|---|
| 1 | `outputs/figures/fig_the_hole.png` | **FULL** | the headline; quote its counts and shares |
| 2 | `outputs/figures/fig_all_positions.png` | **FULL** | the shortfall is not a DB artefact |
| 3 | `outputs/figures/fig_drill_angle_scatter.png` | `PLACEHOLDER_SAMPLE` | name it, state the stamp, quote nothing |
| 4 | `outputs/figures/fig_drill_schematic.png` | **SCHEMATIC** | the proposed geometry; a drawing, no measurement |

No other figure may be named — not in text, not in a caption, not in a comparison. Do not
embed images; reference by path. One caption line per figure carrying the filename and the
provenance tag.

## 7. Words that must not appear

`Turn Coverage`, `Cut Card`, `coverage score`, `feasible region`, `cut gap`, `transfer`,
`noise ceiling`, `outcome test`, `quick pressure`, `held-out`, `prereg`, `Phase 1`…`Phase 7`,
`envelope`, `per-prospect`, `rank`, `most common turn`, `single plant`.

"The envelope" as a physics word is also banned — the argument does not use one. "Capacity" is
allowed only in the general sense of a player's turning limit, never as an estimated quantity.

## 8. The video

Conor is using a clip of an in-game run alongside the four plots. The draft does **not**
describe footage it has not seen. Place a single bracketed slot at the end of section 3 —
where the demand side of the argument is established — reading like:

> `[Video — in-game DB break. Caption: one line, what the clip shows and the timestamp. No number is read from it.]`

and add one sentence of surrounding prose that the slot earns: the clip is there to make the
demand concrete, not to add evidence. Do not propose a fifth figure. Do not invent a timestamp,
a player, a down, a distance or a result. If section 3 runs long, the slot moves to the start
of section 1 and the prose stays the same.

## 9. Voice

Kaggle writeup register: first person plural is fine, active voice, concrete, no
throat-clearing, no "this report will show". Tables where a table is shorter than prose. State
the finding in the first sentence of each section and then prove it. No bullet soup outside the
two lists this spec asks for.

## 10. What the Writer must not do

- Invent a number, a threshold, a citation, a player name, a down, or a result.
- Present any `PLACEHOLDER_SAMPLE` figure as evidence, in text, caption or table.
- Describe any analysis as run when it is not.
- Name a figure outside the four.
- Write past ~2,200 words, or add sections beyond the seven plus the closing list.
- Restore the sentence "games mostly ask for a single plant at 30–60°" — it is false and it is
  what made the last draft wrong.
- Write "plant-ness rises with angle", or quote the 30–60° and >180° plant shares as the two
  ends of a gradient. The band table is two families with a step at 90–120° (D181, D194).
- Edit `TASK.md`, this spec, or any pipeline file.
