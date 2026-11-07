# Viz menu — the visual story for the turn-coverage case

**Status: PROPOSAL. No figure here is built or signed off.** Written 2026-11-07 from the
sample-mode artefacts (`outputs/turns/turns.parquet`, `outputs/envelope/`,
`outputs/demand_coverage/`), all stamped `PLACEHOLDER_SAMPLE`.

**Every count on this page is a sample count.** The numbers quoted below are what the sample
files contain; they exist to size the layout and to prove the encoding works, not to be
published. What survives to full data is the *shape* of each chart and the *direction* of each
gap. Where a number is used as a running example it is written as "sample:".

Binding spec: `TASK.md` @ the 7 Nov amendment. Five figure slots, fixed order:
(a) turn-band supply vs demand, DB primary; (b) the same across positions; (c) the drill spec
with the cells it fills; (d) what the existing DB drills cover; (e) measurement limits.

---

## 0. The one thing the figures must do

A judge who looks at the paper for ten seconds should come away with one sentence:

> *The Combine makes a defensive back turn hard and slow down, and never makes him break
> shallow at speed — which is most of what a game asks him to do.*

Everything below either states that sentence, proves it, or defends it. A figure that does
none of the three does not go in the paper.

Three rules the whole menu obeys:

1. **No per-player ranking anywhere outside the appendix.** Sample coverage is identical for
   every player in a group (all five groups sit `AT_BOUNDARY`); a chart that ranks players
   would be drawing noise. Any encoding that reads as "player X is better" is a defect.
2. **Grey context, one accent.** Games are the grey reference layer; the Combine or the
   proposed drill is the single accent. Colour never encodes more than one thing per panel.
3. **Annotate with counts, not adjectives.** "97 vs 4" beats "substantially more". Counts are
   printed on the marks, never left to the bar-length eyeball, because bar length lies when
   the denominators differ.

---

## 1. The ranked menu

Ranked by (how much of the argument it carries) × (how cheaply it can be built honestly).

### 1. The Hole — *slot (a), hero*

**Sees.** One horizontal paired bar per turn-angle band. Grey bar = share of game DB turns in
that band. Accent bar = share of Combine DB turns in that band. Seven bands, top to bottom
shallow to deep.

**Proves.** The Combine spends almost none of a DB's turn budget on the 30–45° break that
games spend a twelfth of theirs on, and overspends on the hairpin.

**Data.** `outputs/turns/turns.parquet` — `setting`, `position_group`, `abs_turn_angle`,
`valid`. Exists today. One `groupby` on `pd.cut(abs_turn_angle, [30,45,60,90,120,150,180,360])`
per setting. No new stage.

**Design.**
- Chart: paired horizontal bars, share of turns within setting (not counts), 0–70% axis.
- y = band, ordered shallow → deep (30–45 at the top, where the eye lands first).
- x = share of that setting's turns. Grey `#B9BEC4`, accent `#1F4E79` at 0.85 alpha.
- Direct count labels at the bar end: "97 (4.3%)" / "4 (0.3%)". No legend — label the two
  bars once at the top of the chart with a short key line.
- One annotation only, an arrow into the 30–45 row: *"3 of 122 DBs produced a turn this
  shallow. None of them were trying to."*
- A thin secondary axis or a right-hand mini-column showing turns-per-player is tempting.
  Resist it in this panel; put it in concept 8.
- **Deliberately absent:** any total, any average, any "coverage score", gridlines between
  bars, a title longer than eight words.

**Football read.** "So we test the 180 plant all day and never test the comeback break. That's
backwards — the comeback is the ball."

**Risk.** *This is the highest-risk figure in the set, and the risk is arithmetic.* Sample
counts are 4 vs 97 — a raw-count ratio of 24×. That ratio compares **122 Combine players
measured once** with **33 tracked players over 299 player-games**. It is exposure-confounded
and a reviewer will find it. The exposure-free statements from the same rows:
- share of turns: **0.34% vs 4.29%** → **12.6×**;
- turns per player-appearance: **0.033 vs 0.324** (97/299 player-games) → **9.9×**.

So the honest headline is "roughly ten to thirteen times under-sampled", and it is still
damning. Either quote a share, or quote per-appearance, and say which. Do not print "24×"
next to two bar lengths without stating the denominators. (Flagged to Main; a decision for
Conor, not for me.)

Second risk: which turn universe. The 4-vs-97 table counts **all** game DB turns and keeps
angles above 180° as their own band. Stage 04 **excludes** angles above 180° as a heading-wrap
artefact (D151, 575 of 7,143 game turns) and restricts DB demand to `COVERAGE` snaps. In that
stricter universe the sample is **4 vs 41**. Both are true; they are different populations.
The figure must state its scope in the caption in one clause, and the paper must use one
universe for the headline and the other for the coverage arithmetic — or reconcile them
explicitly.

**Effort.** 3 h. Standalone figure script; needs no notebook, but must be reproducible from
`06_figures.py` so the notebook can re-render it.

**Slot.** (a). Non-negotiable.

---

### 2. One Turn, Seventy Frames — *slot (c) top panel, else appendix A1*

**Sees.** A single real turn, drawn three ways on shared time: (i) the smoothed x/y path from
above with the plant point and the two heading arrows; (ii) speed against time with entry,
apex and exit marked; (iii) heading against time with the 30–45° window shaded. Four labelled
stills: *approach, plant, break, re-accelerate*.

**Proves.** A "turn" is not a metaphor — it is a measurable event with a start, a cost in
speed, and a recovery, and the paper measures all three the same way in both settings.

**Data.** **Does not exist yet.** `turns.parquet` stores summary rows plus `x_centre`/`y_centre`
only — there is no per-frame path. Needs a small read of `data/interim` for one chosen
`(setting, event_id/play_id, nfl_id, attempt, frame_start..frame_end)` and the same smoothing
call as `Engine.analyse`. Roughly 60 lines: pick the turn, re-smooth, emit three small arrays.
Choosing the exemplar: a game turn in the 30–45° band, median entry speed, `n_smoothers_matched`
high, `gap_in_turn` false, `orientation_flag` false. Print the choice rule in the caption.

**Design.**
- Three stacked panels, one shared x (time, seconds), total width ~1 column of the page.
- Top panel is the path: 2-D, equal aspect, yards, no axes ticks — just a 1-yard scale bar.
  Path in accent, the pre-plant segment heavier; a filled dot at the plant; two short heading
  arrows.
- Middle: speed line, grey fill under it, entry/apex/exit dots with values.
- Bottom: heading line with the ±30–45° band shaded and the detected angle bracketed.
- Annotations: four words only — approach, plant, break, drive.
- **Deliberately absent:** raw (unsmoothed) positions, per-smoother spaghetti, the player's
  name and headshot, any grade or score.

**Football read.** "Yep. That's a break. He's at three and a half, he sinks to two-seven, and
he needs a second to get it back. That's the play."

**Risk.** A cherry-picked exemplar is a story, not evidence — say so, and pair it with
concept 8 (the full distribution) so the reader can see the chosen turn is typical. Second
risk: drawing an *arc*-classified turn and calling it a break; pick a turn whose `turn_type`
is stated in the caption. Third: the figure is the one place a reader will try to read
per-player skill, so the caption must say "one turn, chosen for legibility".

**Effort.** 7 h (the path export is most of it). Standalone script; the four stills can live
in the notebook as an inline matplotlib panel.

**Slot.** (c) top panel — you cannot specify a drill without first defining a turn. If (c) is
too dense, this becomes appendix A1 and (a) carries the definition in a caption.

---

### 3. Before and After — *slot (c) main*

**Sees.** Two small-multiple heatmaps of the same grid (entry speed × turn angle, 6 × 6 cells),
side by side: **today's supply** on the left, **supply with the tempo break added** on the
right. A dark outline marks the cells the new drill fills. The 40° row is called out.

**Proves.** The drill does not "add a drill" — it fills a specific set of empty cells at the
speed and angle the games actually ask for, and you can count what it buys.

**Data.** Left panel: existing supply counts per cell exist in the **demand-map grid only for
game turns**; Combine supply per cell must be computed from `turns.parquet`
(`entry_speed`, `abs_turn_angle`, `position_group == DB`, `setting == combine`) on the
`bins.entry_speed` × `bins.turn_angle` edges. Right panel is arithmetic, not measurement:
122 players × 6 reps = 732 projected turns, all in the 40°/3–5 yd/s region, spread across the
two cells that region touches. Both are small computations on existing data.

**Blocker, and it is the important one.** `config.yaml` → `bins.turn_angle` is
`[0, 30, 60, 90, 120, 150, 180]`. A 40° break lands in the 30–60° cell, so the figure cannot
show the 30–45 band the whole paper is about — the hero band has no cell. Before this figure
can be honest, the angle grid needs a **45° edge** (and the drill's 40° then sits inside
30–45, one cell). That is a config change plus a prereg line (bin edges are frozen by prereg),
so it is Conor's call, not mine. Without it, the best available version is "the 2–6 yd/s ×
30–60° cells hold 134 game turns against 24 Combine turns" (sample), which is weaker but
still true.

**Design.**
- Two 6 × 6 cell grids, shared sequential ramp (one hue, light→dark), shared scale, one
  colourbar between them.
- Cell labels: turn count. Empty cells left blank, never zero-grey — a blank cell is the
  point.
- Outline the drill's target cells on the right panel in the accent colour, 2 pt.
- No axes on both panels: label entry speed once (left, yd/s) and angle once (bottom, degrees).
- **Deliberately absent:** a difference panel (it is the third panel nobody reads), the
  game-demand layer (that is figure (a)'s / the Cut Card's job), any smoothing of the cells.

**Football read.** "Six reps, three each way, at three to five yards a second, and it lands
right where the empty box is. That costs ninety seconds."

**Risk.** The right-hand panel is a **projection**, and a heatmap is very good at making
projections look like measurements. Its cells must be hatched or clearly labelled "projected,
6 reps × 122 DBs" and the caption must say the left is measured and the right is arithmetic.
Second risk: if the 45° edge is not added, the "before" panel shows a partly-filled cell and
the drill's contribution looks marginal when it is not. Third: the projection uses 122 players
from this sample; say so.

**Effort.** 4 h after the bin decision; 1 h of that is re-running stage 03/04 with the new edge
and re-checking the grid assertion. Standalone.

**Slot.** (c). This is what slot (c) is for.

---

### 4. What Each Drill Actually Asks For — *slot (d)*

**Sees.** A small matrix: rows are the ten DB Combine drills, columns are five turn-angle
bands; each cell is a dot sized by turn count and coloured by whether that drill's turns sit in
the game-shaped band (2–6 yd/s × 30–120°). A right-hand column shows players measured and
median entry speed.

**Proves.** No existing drill is a shallow-break test, and the two drills that come closest are
already the best-attended ones — so this is an add, not a swap.

**Data.** `turns.parquet` — `drill_name`, `abs_turn_angle`, `entry_speed`, `nfl_id`,
`position_group == DB`, `setting == combine`. Exists. The per-drill "share in the game shape"
column is one groupby; the numbers are already in `notes/drill-proposal.md` §3 and should be
recomputed from the artefact rather than copied.

Sample: `BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL` is 90% game-shaped over 118 turns / 102
players; `BOX_DRILL` is 2% over 304 turns / 119 players; `THREE_CONE_DRILL` is 62% but from
only 24 players and 98 turns.

**Design.**
- Dot matrix, not a bar chart: dot area ∝ turn count, one accent for "in the game shape", grey
  for "not". Row order by game-shape share, best at the top.
- A single vertical rule at the 30–45 column, and that column left visibly near-empty for every
  row — the emptiness is the argument.
- Right-hand numeric column, right-aligned, tabular figures: players, median entry, % game shape.
- **Deliberately absent:** drill diagrams (that is (c)), per-drill verdicts, any colour beyond
  the two states, 3-D anything.

**Football read.** "The three-cone's the only one with any short-area work and only 24 of our
guys even ran it. Box drill is a hundred and nineteen players doing a hundred and eighty
degree turn. That's not a break, that's a U-turn."

**Risk.** `THREE_CONE_DRILL`'s 24 players is a sample-coverage artefact of which drills the
sample happened to include, not a fact about the Combine — an empty-looking row may mean "not
in this sample". Show `n_players` prominently and let the dot size carry it, or a reader will
conclude a drill does not exist. Second risk: `GAUNTLET_DRILL` at 7.8 yd/s median looks like
the "at speed" drill; it is, but it has no direction change — that nuance belongs in the
caption, not the chart.

**Effort.** 3 h. Standalone.

**Slot.** (d).

---

### 5. The Same Hole in Every Position — *slot (b)*

**Sees.** Five small multiples (DB, WR, TE, OL, DL), each a miniature of the hero: share of
turns by band, grey vs accent. DB is outlined as the focus; the others are context.

**Proves.** This is not a DB quirk — every position is tested on a different mix of turns than
it plays, and the shallow break is under-sampled everywhere it matters.

**Data.** `turns.parquet`, `position_group`, `abs_turn_angle`, `setting`. Exists.

**Design.**
- 5 panels in a row (or 3 + 2), identical axes and identical scale, so panels are comparable
  by eye without a shared axis label repeated five times.
- DB panel in full accent; the other four at 60% opacity, same encoding.
- One shared caption; no per-panel titles beyond the position code.
- Annotation only under the DB panel.
- **Deliberately absent:** per-position statistical tests, confidence bands on shares (they
  are descriptive shares of a census of turns, not estimates of a population), and any
  "ranking" of positions by gap size.

**Football read.** "Everyone gets tested on something they don't do. The DB one's the worst
because his whole job is the break."

**Risk.** DL has 2,067 Combine turns vs 1,078 game turns and its gap is a *speed* story, not
an angle story (games barely ask DL to turn above 6 yd/s). A judge may read the DL panel as
"DL is fine", which undermines the message by dilution. Consider dropping DL from the figure
and saying in text why — or keep it and annotate it as the counter-example that proves the
method is not rigged to find gaps everywhere. **DL's inclusion is a judgement call for
Conor.** Second risk: OL/DB are exploratory (contamination, snap-type proxies); the panels
must carry an "exploratory" mark.

**Effort.** 2 h. Standalone.

**Slot.** (b).

---

### 6. Where the Shallow Turns Come From — *appendix A2, or slot (a) panel C*

**Sees.** A dot strip: one dot per DB who produced any 30–60° turn in the Combine — 16 dots —
positioned by how many he produced. Eleven of them sit at exactly 1. Underneath, the game
side: 25 dots, median 4 turns each.

**Proves.** The shallow turns the Combine does contain are fragments of drills built for
something else, one per player — not a test of that turn.

**Data.** `turns.parquet` — `nfl_id`, `drill_name`, `abs_turn_angle` ∈ [30, 60),
`setting == combine`. Game side: same band, `setting == game`, count per `nfl_id`. Exists.

**Design.**
- Two rows of dots, jittered vertically, x = turns per player, 1…10.
- Combine row in accent, game row grey. A vertical rule at 2 = `tau2_min_turns_per_player`.
- Label the two players who have more than one, by drill not by name.
- **Deliberately absent:** player names, a box plot over 16 points, any mean.

**Football read.** "Sixteen of a hundred and twenty-two guys ever turned this shallow, and
mostly once, by accident, inside a drill built for something else. You can't evaluate a
hundred and twenty-two players on that."

**Risk.** Reads as a complaint rather than a measurement if the axis is unlabelled. The point
is *depth per player*, so the vertical rule at 2 (the envelope's minimum) does the work — but
that rule needs one line of explanatory caption or it is a mystery line. Also: 16 dots is a
thin figure; at full data it may be richer, and the layout should scale to ~40 dots without
redesign.

**Effort.** 2 h. Standalone.

**Slot.** Appendix, or a third panel of (a) if (a) is given a full page.

---

### 7. Bars of Doubt — *slot (e), panel 1*

**Sees.** Every DB turn in the 30–45° band drawn as a horizontal dot with a whisker to its
smoother-to-smoother range, sorted by angle, with the 45° band edge as a vertical rule. You
can see how many turns could belong to the neighbouring band depending on the smoother.

**Proves.** The band is real but its edges are soft — the honest version of "we can measure
this, and here is how precisely".

**Data.** `turns.parquet` — `abs_turn_angle`, `abs_turn_angle_smoother_min/max`,
`n_smoothers_matched`, `setting`. Exists. Sample: median spread 3.1° inside the band, p90 much
wider (smoothers disagreeing about where the turn starts); 39% of band members could cross 45°
under their own spread.

**Design.**
- Dot-and-whisker strip, one row per turn, ~70 rows — a "caterpillar", no y axis.
- Whiskers grey, dots accent for game / grey for Combine, band edges as thin rules.
- One bracket annotation: "median rounding ±3°; band width 15°".
- **Deliberately absent:** a histogram on top (redundant), a p-value, error bars on the
  *count*.

**Football read.** "So the line between a break and a cut is a bit blurry at ten hertz. Fine —
but four of them is still four."

**Risk.** An honest version of this figure can be read as "the measurement is unreliable, so
the whole paper is". The caption must carry the counterweight explicitly: detection recovers
planted 45° turns at 3.1° MAE and precision 0.92 / recall 0.93 on the three-cone geometry, so
the band is measurable — the spread is a *reporting* width, not noise swamping the signal.
Second risk: p90 spread will look wild in a strip plot; cap the whiskers at a stated value and
count what is capped.

**Effort.** 3 h. Standalone.

**Slot.** (e), first panel.

---

### 8. Two Numbers That Cannot Be a Ranking — *slot (e), panel 2*

**Sees.** Two panels. Left: for each setting × position × measure, one bar of between-player
variance, with the 43-of-51 components that sit exactly at the boundary drawn as zero-height
bars punched out of the baseline — visibly *empty*. Right: peak lateral acceleration against
turn angle, with the reversal zone beyond ~150° greyed out and struck through as "not
physical".

**Proves.** We cannot tell these players apart with this data, and we will not pretend to.

**Data.** `outputs/envelope/variance_components.csv` — `tau2`, `tau2_status`, `tau2_p05/p95`,
`setting`, `position_group`, `measure`; and `turns.parquet` — `peak_lateral_accel_yd_s2`,
`abs_turn_angle`, `path_radius_yd`. Both exist. Sample: 43 of 51 components `AT_BOUNDARY`;
median lateral acceleration rises from ~5.4 yd/s² at 30–45° to ~33 yd/s² past 180°.

**Design.**
- Left: dot plot of `tau2` per component with its p05–p95 bar, grouped by measure, one row per
  setting × position; `AT_BOUNDARY` rows drawn as hollow circles sitting on the zero line, and
  a count in the corner: "43 of 51 at the floor".
- Right: binned median line plus p95 band of lateral acceleration vs angle, log y; the
  >150° region shaded and annotated "hairpin: a 1.8 yd circle fit, not a capacity".
- **Deliberately absent:** the word "reliability" without a definition, any ICC number we
  cannot compute (kill-test statistics are PENDING FULL RUN), any suggestion that the boundary
  is a bug.

**Football read.** "So the model says every corner is the same corner? No — it says this data
can't tell them apart, which is a different sentence, and the difference matters."

**Risk.** Reads as "the model failed" if the caption does not say `AT_BOUNDARY` is a *finding
about the data*, not an error (the stage's own notes say exactly this). Second risk: the
acceleration panel needs the g conversion stated once (1 g ≈ 10.7 yd/s²), or "33" means
nothing. Third risk: this figure can accidentally become the paper's conclusion. It is a
caveat, not the finding — keep it last and small.

**Effort.** 4 h. Standalone.

**Slot.** (e), second panel. (7 and 8 share the slot; if only one fits, keep 8 — the ranking
caveat is the one a judge will test you on.)

---

### 9. The Speed Ladder — *appendix A3*

**Sees.** Two ridgelines or two histograms of entry speed for shallow turns (30–60°), Combine
above, game below, with the 3–5 yd/s demanded band shaded.

**Proves.** The shallow turns games ask for happen at a specific jogging-to-striding speed, and
that is the band the new drill is built to hit.

**Data.** `turns.parquet` — `entry_speed`, `abs_turn_angle` ∈ [30, 60). Exists. Sample: game
178 / 115 / 33 turns at 2–4 / 4–6 / 6–8 yd/s; Combine 16 / 8 / 2.

**Design.** Two panels, shared x, counts on y; shaded 3–5 yd/s target; medians marked with a
rule and printed. No overlapping densities — they hide the n.

**Football read.** "Three to five miles an hour — that's a bang-bang comeback, not a sprint.
You can build that in ten yards."

**Risk.** The Combine bars are so small (26 turns total) that the panel can look like an empty
chart and invite "there's no data" rather than "there's no test". Label the counts directly on
the bars. Low priority overall — it is supporting detail for (c), not a standalone claim.

**Effort.** 2 h. Standalone.

**Slot.** Appendix.

---

### 10. The Ledger — *appendix A4, or a small panel inside (c)*

**Sees.** A simple three-row ledger, no chart: *what games ask a DB for* (326 shallow turns
across 299 player-games), *what the Combine supplies* (26, from 16 players), *what six reps
would supply* (732, six per player, every player). Bars drawn to scale under each row.

**Proves.** The arithmetic of the fix is small and specific: ninety seconds of cone space buys
repeated measurement of the exact turn nobody has measured.

**Data.** Arithmetic over `turns.parquet` counts plus the drill spec. No new data. The 732 is a
**projection** (122 × 6) and must be labelled as one.

**Design.** Three horizontal bars, same axis, counts at the end, log scale if needed; the third
bar hatched as a projection. One sentence under it.

**Football read.** "Ninety seconds a player. That's the whole ask."

**Risk.** Mixing a projection with measurements in one chart is the same sin as concept 3's
right panel; hatch it, label it, and do not compute a ratio across the hatch. Second risk: 732
turns is *more* than games demand, which a hostile reader will call oversampling. The answer is
that the six reps buy **reliability per player**, not volume — the point of six is that
`tau2_min_turns_per_player` is 2 and eleven of the sixteen currently sit at exactly 1. Put that in the
caption, not in the bar.

**Effort.** 2 h. Standalone.

**Slot.** Appendix, or a corner panel of (c).

---

### 11. Both Hips — *appendix A5*

**Sees.** A paired bar of left- versus right-foot breaks in the 30–60° band, Combine vs game.

**Proves.** The test does not check that a DB can break both ways, and games demand both.

**Data.** `turns.parquet` — `turn_angle` sign, `abs_turn_angle` ∈ [30, 60). Exists. Sample:
Combine 11 left / 15 right; game 154 / 172 — the game is balanced, the test is 26 turns.

**Design.** Two bars, four numbers printed. Fifteen minutes of work.

**Football read.** "You'd never draft a corner who can only turn one way, and you never test
which way he turns."

**Risk.** With 26 Combine turns, a 11/15 split is not evidence of asymmetry — it is evidence of
*absence of any design*. Say that; do not imply a left/right bias exists. Also: the envelope
deliberately does not model left/right asymmetry, so this stays descriptive.

**Effort.** 1 h. Notebook cell.

**Slot.** Appendix.

---

### 12. The No-Go Zone — *appendix A6, or merged into 8's right panel*

**Sees.** The speed × angle grid with the cells that are *infeasible by construction* (any
reversal under a 90% speed-retention rule) struck out, so the reader sees at a glance which
"cut gaps" are thresholds talking rather than physics.

**Proves.** We are not claiming players fail turns they physically cannot fail; the claim is
about coverage, not about a failure rate.

**Data.** `feasible_region.parquet` — `retention_pred`, `inside_point`, `entry_lo`, `angle_lo`.
Exists.

**Design.** The grid from concept 3, one panel, with the impossible region hatched and a single
sentence. No colour ramp needed.

**Football read.** "Right, you don't keep ninety per cent of your speed through a spin move.
Nobody does. So don't score that."

**Risk.** Duplicates concept 3's visual language and may confuse the two grids. Only worth
building if the paper ends up leaning on the cut-gap language anywhere — which, per the 7 Nov
amendment, it should not. **Build last, or not at all.**

**Effort.** 2 h. Standalone.

**Slot.** Appendix.

---

## 2. The five-figure build plan

| slot | concept | hours | depends on |
|---|---|---:|---|
| (a) | 1. The Hole | 3 | decision on denominator (share vs per-appearance) |
| (b) | 5. Same hole, every position | 2 | decision on whether DL stays |
| (c) | 2. One Turn + 3. Before and After | 11 | per-frame path export; **45° bin edge** |
| (d) | 4. What each drill asks for | 3 | none |
| (e) | 7. Bars of Doubt + 8. Two Numbers | 7 | none (kill-test stats stay PENDING FULL RUN) |

Roughly **26 h** for the five-figure set, of which **8 h** is blocked on two decisions and one
small code addition. Appendix figures are 1–2 h each and can be cut without loss.

**Build order if time is short:** 1 → 4 → 3 → 5 → 8. That order front-loads the argument
(hero, then the drill's justification) and leaves the honesty figure to last, which is the
wrong priority in principle but the right one if only three figures ever get built — the three
that carry the claim are 1, 4 and 3.

---

## 3. What these figures need that does not exist yet

1. **A 45° bin edge.** `bins.turn_angle` is `[0,30,60,90,120,150,180]`. The paper's focus band
   (30–45°) and the drill's 40° break have no cell of their own. This is the one structural
   blocker in the set, and it is a prereg decision because bin edges are frozen there.
2. **A per-frame path export.** `turns.parquet` keeps summaries and a centre point, not the
   trajectory. Concept 2 needs ~60 lines that re-read one attempt and re-run the same smoother.
3. **A reconciled turn universe.** All-turns (4 vs 97, includes >180°, all snap types) versus
   demand-eligible (4 vs 41, `COVERAGE` only, ≤180°). Both are defensible; the paper must not
   use one for the headline and the other for the arithmetic without saying so.
4. **An exposure denominator.** Combine supply is 122 players × one session; game demand is 33
   players × 299 player-games. Any count-vs-count ratio mixes the two. Shares or
   turns-per-appearance do not.
5. **Kill-test reliability numbers.** PENDING FULL RUN. Until they exist, concept 8 can show
   `tau2` and `AT_BOUNDARY` but must not show an ICC.

---

## 4. Football film to track down

Two lists. Search strings are given exactly as I would type them; I have not verified that any
specific clip exists, so treat titles, channels and URLs as unconfirmed until you have the
clip open in front of you.

### 4a. Football film / broadcast

**The turn itself, in games (30–45° breaks at speed).**

| search string | what to look for |
|---|---|
| `all 22 cornerback comeback break slow motion` | A CB in off coverage driving downhill on a comeback: hips open, two steps, plant, drive back to the ball. This is the shape the paper calls a 30–45° break. |
| `cornerback curl flat break on the ball film room` | The break on a curl: the DB arriving as the receiver turns, not after. Shows entry speed and the plant. |
| `defensive back hip flip transition drill film` | Hip flip on a deep out or a post-wheel; the moment of transition is the same event as the 45° break, at higher speed. |
| `red zone fade back shoulder cornerback technique` | Late, shallow, high-speed breaks in compressed space — the tightest version of the same turn. |
| `cover 2 corner sink drive flat footed break` | Sink-and-drive from a deep half: a 40-ish degree break taken from a backpedal, which is the exact transition the Combine cannot see. |
| `slot cornerback option route break quick game` | Slot DBs breaking at short angles repeatedly — the highest-frequency version of the demanded turn. |
| `safety break on the ball angle of approach` | Angle of approach coaching points; useful vocabulary for the anatomy panel's annotations. |

Route concepts worth naming in the paper when describing demand: **curl, comeback, deep out,
speed out, dig, post-wheel, option route, back-shoulder fade**. The shallow break lives most
often on the curl, the comeback and the speed out.

**The test, as broadcast.**

| search string | what to look for |
|---|---|
| `NFL Combine defensive back drills full` | The broadcast block: backpedal-and-break, the 90° break to the ball, the Teryl Austin drill. Watch where the athlete is *slowest* — the break is taken from a near-stop, which is the point of the paper. |
| `NFL Combine 3 cone drill defensive back` | The L-shaped cone drill. Historically real and in this data as `THREE_CONE_DRILL`; the turns are 60–90° at ~4 yd/s. |
| `NFL Combine backpedal and break drill` | `BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL` in our table is the best existing drill on the game-shape measure (90%). Worth a still for figure (d). |
| `NFL Combine Teryl Austin drill` | The named DB drill appears in this dataset as `TERYL_AUSTIN_DRILL_1/2`; verify the name is the broadcast's, not an internal label, before printing it. **Unverified.** |
| `NFL Combine W drill defensive backs` | `W_DRILL` — 107 turns, only 3% in the game shape. Good visual for "we test the U-turn". |
| `NFL Combine box drill` | `BOX_DRILL` — the single largest source of Combine DB turns, median angle 139°. The clearest picture of the oversampling. |

**Broadcast / film series worth hunting** (not verified — check each before citing):
the league's official combine coverage on NFL Network; the all-22 coaches-film product on the
league's streaming service; team-produced "film room" segments; and the college all-22 that
shows the same routes pre-draft. Avoid highlight reels: they cut on the catch, which is exactly
the frame where the break is.

**What to harvest from a clip, rather than the clip itself:** a timestamped still of the plant,
and a rough sense of the entry speed by eye ("he is striding, not jogging"). The paper does not
need video; it needs one honest sentence about what the turn looks like, and the still for the
anatomy panel comes from our own tracking, not from broadcast footage we cannot license.

### 4b. Visualisation precedent

| search string | what to lift |
|---|---|
| `NFL Next Gen Stats passing chart` | The discipline of a field-aligned grid, one sequential ramp, and completion probability printed as a number next to colour rather than implied by it. Lift the **layout**, not the branding. |
| `MLB Statcast catch probability chart` | The closest analogue to the whole paper: a difficulty surface (probability of making a play, by distance and time) with observed plays scattered on top. Lift the idea of **a difficulty surface plus the plays that beat it**. |
| `NBA shot chart hexbin` | How to show density without a smooth blob; hexbin or small multiples beat a kernel density when n is in the hundreds. Lift the **binning honesty**. |
| `NBA shot quality expected field goal percentage map` | A modelled surface shown behind raw counts, with the model's uncertainty made visible. Lift the **two-layer** structure (surface + counts). |
| `soccer pass map sonar StatsBomb` | Direct labelling on the pitch, no legend, one accent per player. Lift the **direct labelling** habit. |
| `soccer xG shot map` | Dot size for probability, grey for context, one accent for the story. Lift the **grey context layer**. |
| `tennis serve placement heatmap` | A grid that is genuinely discrete (court zones) and does not pretend to be continuous. Lift the **discrete-cell honesty**. |
| `Kirk Goldsberry basketball shot chart` | Long-form sports visual storytelling: one chart, one sentence, no dashboard. Lift the **one-claim-per-figure** rule. |
| `F1 track dominance map` | Colour as a signed difference from a reference, with a neutral midpoint. Only relevant if we ever build a "gap vs neutral" panel. |

**Three things to steal outright:** (i) Statcast's habit of printing the probability on the
play rather than only encoding it in colour; (ii) Next Gen Stats' field-aligned grid, so a
football reader's eye is already oriented; (iii) the grey-context-plus-one-accent palette that
every good sports viz shop uses, which is what makes a single bar legible in a crowd.

**Three things not to steal:** dashboard panels with eight small charts, radar/spider plots for
"player profiles" (they imply comparable dimensions we do not have), and any animation. The
Kaggle notebook renders static figures; motion would hide the absence of data behind movement.

---

## 5. Questions this note cannot answer

1. **Which denominator does the paper headline** — share of turns (12.6× in the sample), turns
   per player-appearance (9.9×), or raw counts (24×)? Only the first two survive a reviewer.
2. **Does the 45° bin edge go in** before the prereg is frozen? Without it figure (c) cannot
   show the band the paper is about.
3. **Does DL stay in figure (b)?** Its gap is a speed story, not an angle story, and its
   presence may dilute the message or, read correctly, prove the method is not rigged.
4. **Is the deliverable a drill proposal or a gap analysis with the drill as discussion?**
   `notes/drill-proposal.md` §6 asks this and the figure set changes shape depending on the
   answer: a proposal needs concepts 3 and 10; an analysis needs only (a), (b), (d), (e).
