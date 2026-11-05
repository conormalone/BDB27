# Visualisation Concept Pack — BDB27

**NFL Big Data Bowl 2027 · "Combine to NFL Performance"**
WR primary, TE replication. Design cycle only — no pipeline code, no real data, no results.

This pack answers one question: *what should this entry look like so a judge remembers it?*
It contains 12 ranked concepts, a house style, six mock-ups rendered in matplotlib, and an
explicit account of what survives a null result.

---

## The constraint that shapes every decision

The deliverable is a **Kaggle notebook**. Every figure must render in-notebook from
matplotlib/plotly with no external assets, no dashboards, no D3, no interactive-only encoding.
Text has to be legible at notebook width, in a screenshot, and in a PDF someone prints.

Everything below is therefore built from static, labelled, two-colour panels. Where a concept
would be better as an interactive, it is either dropped or given a static fallback.

**The spine of the entry is a measurement gap, not a prediction contest.** The figures exist to
make one sentence visible: *the Combine ceiling is a number a player produced once, indoors, on
a straight line — and the game never quite reproduces it.* Whether pre-Combine draft-stock
movement moves that gap is the second question, and it is asked **after** the gap has been
measured and drawn.

---

## Summary

| # | Concept | Cost | Null-safe | Football-first |
|---|---------|------|-----------|----------------|
| 1 | The Ceiling Line | laptop-only (needs per-frame) | yes | **yes** |
| 2 | Route Replay, Speed-Painted | laptop-only | yes | **yes** |
| 3 | Every Player Loses Speed | Pi-friendly | yes | yes |
| 4 | Where the Gap Lives | Pi-friendly | yes | **yes** |
| 5 | Route Tree, Gap by Branch | Pi-friendly (summaries) + geometry | yes | **yes** |
| 6 | The Honest Null | Pi-friendly | **it *is* the null figure** | no |
| 7 | The Draft Board Slide | Pi-friendly | yes | no |
| 8 | Building the Yardstick | laptop-only | yes | partly |
| 9 | Top-k vs Bottom-k | Pi-friendly | yes, weaker | **yes** |
| 10 | The Iso-gap Plane | Pi-friendly | yes | no |
| 11 | The Gap Waterfall | Pi-friendly | yes | no |
| 12 | The Week-by-Week Close | laptop-only | **collapses** | yes |

**Pi-friendly** = renders in seconds from the per-player / per-effort summary CSVs already in
`outputs/`. **laptop-only** = needs raw per-frame tracking joined per play, or a resampling loop.
Seven of twelve are football-first: a non-analyst can see a receiver running without reading a
coefficient.

### Recommended cut for the notebook (5-figure cap)

If only five figures survive the official limit: **1, 3, 4, 5, 6**.
Hero = 1 (the gap, made physical). 3 gives the reader every player at once. 4 says *where* in the
distribution the gap lives. 5 asks whether role mix could fake the contrast. 6 carries the
result, whatever its sign, including the null.

Figure 2 is the most beautiful and the most expensive; it earns a slot only if the per-frame join
is cheap on the final machine. It is the one to fight for.

---

## 1. The Ceiling Line

- **The claim it makes.** Every receiver's game speed runs below his own Combine ceiling, and the
  distance between the two curves is a quantity you can point at.
- **Chart type / encoding.** Small multiples of speed-vs-time traces (2×2). x = time since snap
  (s), y = speed (yd/s). Solid line = one route effort, coloured by tier; dashed near-black line =
  the player's shrunken Combine envelope `v(t) = vmax·(1 − exp(−t/τ))` with a ±1 SE band; shaded
  region = the shortfall; vertical double arrow = the gap in yd/s at peak.
- **Data.** `outputs/envelope/per_player_envelope.csv` (`env_vmax`, `env_vmax_se`, `env_tau`,
  `shrink_weight_vmax`) joined to per-frame game tracking (`frame_id`, `x`, `y`, `s`) for one
  effort per player from `outputs/game/efforts.csv` (`game_id`, `play_id`, `peak_speed`,
  `route_ran`) via `outputs/security_models/group_assignment.csv` (`group`).
- **Build cost.** laptop-only — needs the raw tracking join. Envelope side is Pi-friendly.
- **Under a null result.** Works unchanged: it is descriptive. The group difference appears only
  in the panel footnote (`top-k / ceiling = 88%`), so a null simply means the four numbers sit
  close together. No panel is replaced.
- **Football-first?** Yes. You see a man accelerate, break, and decelerate.

## 2. Route Replay, Speed-Painted

- **The claim it makes.** The ceiling is approached in one place on a route — the top of the stem,
  or the step before the break — and only for a moment.
- **Chart type / encoding.** Half-field views (2×2), x = across the field (yd), y = yards
  downfield. The route path is drawn as a line collection coloured by instantaneous chip speed
  (neutral sequential ramp), with a hollow start marker, a break tick, and a ringed peak marker.
  Panel position carries tier; **colour carries speed only**.
- **Data.** Per-frame tracking (`x`, `y`, `s`, `frame_id`) per play, plus `route_ran`,
  `pass_length` from `outputs/game/efforts.csv`; tier from `group_assignment.csv`.
- **Build cost.** laptop-only. Also the most expensive figure in the pack: a per-play frame slice
  and a path-ordering pass. Cache one PNG-adjacent CSV per displayed play.
- **Under a null result.** Unchanged. It describes *where speed happens*, not who is faster. In a
  null it is the figure that still explains the measurement to a football reader.
- **Football-first?** Yes — this is the one a coach reads first.

## 3. Every Player Loses Speed

- **The claim it makes.** The gap is universal; the only question is whether one tier gives up
  less of it.
- **Chart type / encoding.** Sorted dumbbell. One row per player, sorted by gap. Hollow marker =
  Combine ceiling, filled marker = in-game top-k mean, connecting line length = the gap, colour =
  tier. The hero panel is **zero-based (0–23.2 yd/s)** so line length is the gap at true scale; a
  separate panel headed **"zoom: 18–23 yd/s"** exists because 111 rows are hard to read at full
  scale, and it carries the median-gap bracket. A narrow right-hand panel shows the two group means
  with 95% CIs. All three anchor the eye on the *difference*, not on a correlation.
- **Data.** `outputs/game/player_summary.csv` (`ceiling_speed_yds`, `topk_mean_speed`,
  `n_route_snaps`) + `group_assignment.csv` (`group`). Two files, no tracking.
- **Build cost.** Pi-friendly.
- **Under a null result.** Works: the group panel's two intervals overlap and the figure says so.
  Replace nothing; the annotation changes from a difference to "difference +0.0 yd/s".
- **Football-first?** Yes, at the level of "these are speeds, in yards per second, of players you
  can name" — no field, but no statistics either.

## 4. Where the Gap Lives

- **The claim it makes.** The ceiling is approached only in the extreme tail of a player's own
  efforts; the median effort is nowhere near it, and that is a property of football, not of effort.
- **Chart type / encoding.** Two panels sharing the x-axis of within-player effort percentile
  (50th→99th). Left: speed as % of own ceiling, two lines with an IQR ribbon and a ±1 SE band,
  reference line at 100%; the y-axis runs 66–104% and says so in-figure ("axis starts at 66% — the
  ceiling line at 100% is the reference"). Right: P(effort > ceiling) in %, zero-based, with Wilson
  intervals.
- **Data.** `outputs/game/efforts.csv` (`peak_speed`, `ceiling_speed_yds`, `max_over_ceiling`,
  `is_exceed`) grouped by player and percentile; or a precomputed quantile summary table.
- **Build cost.** Pi-friendly if percentiles are precomputed per player; laptop-only if computed
  on the fly over full efforts.
- **Under a null result.** Works. If the tiers coincide, the two lines and two ribbons overlay —
  which is itself the honest picture, and the right panel still shows how rarely the ceiling is
  beaten.
- **Football-first?** Yes — "how close does he get to his own top speed, and how often does he
  beat it" is a football question with a number attached.

## 5. Route Tree, Gap by Branch

- **The claim it makes.** The tier contrast is tested inside route family, so a difference in role
  mix cannot manufacture it.
- **Chart type / encoding.** Eight small field glyphs (go, post, corner, dig, out, cross, slant,
  screen), each with a Δ bar beneath: difference in mean ceiling utilisation in percentage points,
  moved_down minus high_secure, with a 95% interval. Grey bar = interval spans zero; amber =
  excludes zero. Zero spine is shared across all eight.
- **Data.** `route_ran` / derived route family and `pass_length` from `outputs/game/efforts.csv`;
  `topk_mean_envelope_fraction` from `player_summary.csv`; `group` from `group_assignment.csv`;
  geometry from a hand-drawn route template or a median observed path per family.
- **Build cost.** Pi-friendly for the statistics; the glyphs are static geometry (no tracking
  needed beyond a one-off template).
- **Under a null result.** Works, and is arguably at its most useful: eight grey bars all crossing
  zero is a direct answer to "maybe it is just role mix". Nothing collapses.
- **Football-first?** Yes — it is a route tree, the most recognisable object in the passing game.

## 6. The Honest Null

- **The claim it makes.** An interval inside a pre-declared equivalence margin is a finding, and
  the design's smallest detectable effect belongs on the page next to it.
- **Chart type / encoding.** Left: a difference ruler — x = Δ utilisation in pp, zero-centred,
  with a shaded ±3 pp equivalence band, a posterior density for the estimate, the point estimate
  and its 95% CI. Right: power curve — x = true difference, y = probability the CI excludes zero,
  vertical line at the MDE (80% power).
- **Data.** `b_primary.csv` (group coefficient, clustered SE, CI), `c_exceedance.csv`, and the
  pre-declared equivalence margin + MDE from `notes/prereg.md` / `outputs/validate/`.
- **Build cost.** Pi-friendly.
- **Under a null result.** It *is* the null figure. Under a positive result it flips polarity
  without redesign: the estimate moves right of the band and the MDE line stays put as the
  reference. That dual use is why it ranks above prettier options.
- **Football-first?** No. This is the figure for the data-science judge and the writeup.

## 7. The Draft Board Slide

- **The claim it makes.** The exposure is *movement in draft stock*, not the level of it — a player
  slipping from 1st to 2nd is still an elite prospect, and the figure has to say so out loud.
- **Chart type / encoding.** Slope / bump chart of expected draft position. x = two pre-Combine
  snapshots (2 weeks out, 1 week out), y = EDP (inverted, lower = better), one line per player.
  `moved_down` trajectories highlighted in colour with the ≥ +3.0 spot threshold drawn as a
  reference band; everyone else in grey. A marginal strip shows the level distribution, to prove
  `high_secure` is not a "low EDP" group.
- **Data.** `outputs/security/security_exposure.csv` (`edp_pre`, `edp_trend`, `has_trend`,
  `n_snapshots`) and the board snapshots behind it; `group_assignment.csv` (`group`, `stratum`,
  `basis`).
- **Build cost.** Pi-friendly.
- **Under a null result.** Unchanged — it defines the treatment and cannot be invalidated by the
  outcome. It is also the figure that pre-empts the most damaging referee objection.
- **Football-first?** No, but every football reader understands a draft board.

## 8. Building the Yardstick

- **The claim it makes.** The ceiling is estimated, not observed: it comes from two or three
  attempts, shrunk toward the position mean, with real uncertainty carried forward.
- **Chart type / encoding.** Two panels. Left: attempt-level speed curves (thin grey), the fitted
  envelope (bold), and the shrunken envelope (dashed) for the same player. Right: shrinkage weight
  vs number of attempts, one dot per player, showing exactly how much a two-attempt player is
  pulled toward the pool.
- **Data.** `outputs/envelope/attempt_features.csv` (per-attempt `vmax`, `tau`),
  `per_player_envelope.csv` (`raw_env_vmax`, `env_vmax`, `shrink_weight_vmax`, `n_attempts`),
  `feature_selection.csv` (ICC).
- **Build cost.** laptop-only (attempt-level curves); the right panel alone is Pi-friendly.
- **Under a null result.** Unchanged. This figure is about the instrument, not the finding.
- **Football-first?** Partly — a sprint curve is recognisable, the shrinkage panel is not.

## 9. Top-k vs Bottom-k

- **The claim it makes.** If the tier contrast is about effort, it must appear on maximum-effort
  reps and vanish on low-effort ones. This is the falsification, drawn as a dose-response.
- **Chart type / encoding.** Paired panels with identical axes: utilisation for top-k efforts
  (left) and bottom-k efforts (right), by tier, with CIs; a bracket between panels states the
  predicted direction of travel.
- **Data.** `outputs/game/top_k_efforts.csv` and the bottom-k companion; `falsification.csv`
  (`F2` placebo row).
- **Build cost.** Pi-friendly.
- **Under a null result.** Still works, but weakly: two flat pairs is a thinner story than one
  flat pair. Keep it as a supporting panel, never as the hero. If the primary is null *and* this
  is null, drop it and keep 6.
- **Football-first?** Yes — "his best plays vs his jog-throughs" is a football distinction.

## 10. The Iso-gap Plane

- **The claim it makes.** Two players can share a ceiling and still differ in what they realise;
  the iso-utilisation bands make the gap readable without a regression line.
- **Chart type / encoding.** Scatter of shrunken ceiling (x) vs in-game top-k mean (y), both in
  yd/s, with the 45° identity line, iso-utilisation bands at 80/85/90/95%, and group centroids
  with CIs. Full axes, both starting at zero — no truncation.
- **Data.** `player_summary.csv` (`ceiling_speed_yds`, `topk_mean_speed`) + `group`.
- **Build cost.** Pi-friendly.
- **Under a null result.** Works — the centroids sit on top of each other inside the bands.
- **Football-first?** No. It is the conventional figure, included because it is the one a
  data-science judge expects to find, and because it is the honest way to show the raw joint
  distribution. Rank it last and use it small.

## 11. The Gap Waterfall

- **The claim it makes.** Most of the raw Combine-to-game difference is not a mystery about
  motivation; it is measurement context, role and route mix — and the residual is what any
  incentive story has to explain.
- **Chart type / encoding.** Waterfall: raw difference (best Combine attempt vs best game effort)
  → subtract shrinkage/attempt-selection → subtract route-family and depth mix → subtract snap
  share and role → residual gap, with CI on the residual only.
- **Data.** `per_player_envelope.csv` (`peak_speed`, `env_vmax`), `player_summary.csv`,
  `efforts.csv` (`route_ran`, `pass_length`), and the fitted controls from `b_primary.csv`.
- **Build cost.** Pi-friendly (all summary-level).
- **Under a null result.** Works. The residual bar simply carries a CI that spans zero, which is
  exactly the point.
- **Football-first?** No.

## 12. The Week-by-Week Close — `ONLY VIABLE IF EFFECT IS POSITIVE`

- **The claim it makes.** If the gap is about incentive and preparation, a slipping player should
  close it as the season gives him more NFL reps.
- **Chart type / encoding.** Line chart, x = weeks since first route snap, y = rolling utilisation
  vs own ceiling, two lines by tier with ribbons.
- **Data.** `efforts.csv` (`game_id`, `season`, `season_type`, `week`, `peak_speed`,
  `ceiling_speed_yds`) — **note `week` must be joined from `games.csv`; it is not in the effort
  table today.**
- **Build cost.** laptop-only.
- **Under a null result.** **Collapses.** Two flat lines on top of each other is a figure with no
  content and no honest caption beyond "we looked". **Labelled positive-only for that reason: do
  not build it unless the primary contrast is positive and the trend is monotone.** It is included
  here as the pack's worked example of the failure mode — a concept whose entire legibility depends
  on the result going one way.

---

# House style

The pack is meant to read as one entry. Six mock-ups, one palette, one type scale, one margin.

### Palette

| Hex | Name | Semantic assignment |
|-----|------|---------------------|
| `#C4553B` | ember | **`moved_down`** — stock slipping ≥ +3.0 EDP spots. Never used for anything else. |
| `#2F5D7C` | slate | **`high_secure`** — trend < +3.0 (stable or rising). Never used for anything else. |
| `#D9922B` | amber | One accent per figure only: exceedance, a CI that excludes zero, or the point estimate. |
| `#141414` | ink | Text, reference lines, the Combine envelope. |
| `#3D3D3D` / `#6B6B6B` | ink-2 / muted | Secondary text, annotations, axis labels. |
| `#9A9A9A` | faint | Panel letters, leader lines, de-emphasised marks. |
| `#D6D6D0` | rule | Hairlines, panel rules. |
| `#E6E6E1` | grid | Gridlines. |
| `#F7F7F4` | panel | Stat blocks, equivalence bands. |
| `#EDF1E6` | turf | Field background only (not a data colour). |

Rules that keep it colourblind-safe:

1. **One variable per colour channel per figure.** If colour means speed (concept 2), tier is
   carried by panel position and a text label — never by a second colour.
2. **Group colour is always redundant** with a written label (`MOVED DOWN`, `HIGH / SECURE`) and
   with marker shape or panel position. A reader who cannot separate ember from slate loses nothing.
3. **No red/green contrasts, no rainbow.** Sequential ramps are neutral (grey) or single-hue.
4. Never colour a significant result green and a null red. Nulls are not failures.

### Typography

- **DejaVu Sans** for everything; **DejaVu Sans Mono** for numbers in stat blocks and annotations
  (tabular, and it visually separates "measured value" from "prose").
- DejaVu is the matplotlib default, so the notebook renders identically on Kaggle with no font
  installation. That is worth more than a prettier face that silently falls back.
- Scale: kicker 7.2 · deck 9.3 · title 15.6 bold · panel title 8.9–9.0 bold · axis label 8.6 ·
  tick 8.0 · annotation 7.3–7.6 · stat value 11.0 mono. Body text never below 7.0.
- Sentence case for titles. The only capitals are the two tier labels and the kicker.
- Numbers: one decimal for yd/s and pp, `+`/`−` signs on every difference, percentages to one
  decimal only when the sample supports it.

### Layout

- Default canvas **9.6 × 5.4 in at 200 dpi = 1920 × 1080 px**. Field figures go 9.6 × 7.4.
  Never wider than 2000 px: it must stay readable in a notebook cell.
- Margins: left 0.072–0.090, right 0.972, title block in the top 0.25 of the canvas, caption at
  y ≈ 0.02. Do not let an axes label sit in the caption band.
- Header is always: kicker → title → one-line deck → hairline rule. The rule is the only full-width
  horizontal line in the figure; gridlines never touch the edges of the canvas.
- Facets share y within a row and x within a column. Panel letters `a`, `b` in 8.2 bold faint at
  top-left, above the panel title, never inside the plot area.
- Small multiples repeat the same limits across panels. A per-panel autoscaled axis is a lie about
  magnitude.

### Axes and grid

- Horizontal gridlines only, `#E6E6E1`, 0.7 pt, drawn under the data. Vertical gridlines only when
  x is categorical and the reader needs the lane.
- Top and right spines off. Left and bottom spines `#B5B5AF`, 0.8 pt.
- **No truncated axes** on any quantity where the gap is the message: speed axes start at zero.
  If a zoom is genuinely needed (utilisation, 66–104%), the axis states the range and the figure
  says why — and the zoom lives in its own panel, headed as a zoom. A truncated scale is never
  applied silently to a hero panel.
- Units in every axis label: `speed (yd/s)`, `gap (yd/s)`, `Δ ceiling utilisation (pp)`,
  `P(effort > ceiling) (%)`, `time since snap (s)`.
- No dual y-axes anywhere in the pack.

### Annotation

- A callout is one leader line (0.7 pt, `#9A9A9A`, slight `arc3` bend) and one or two lines of
  text. No boxes, no arrows with heads, no drop shadows.
- Annotate the **thing being claimed**, not the mechanism: "median gap 1.7 yd/s", not "p < 0.05".
- Stat blocks: tiny label (6.9, muted) above a mono value (11.0, ink), left-aligned, on the panel
  background `#F7F7F4` when they sit over data.
- Never annotate a number the axes already show unambiguously.

### Attribution and watermark

- Every figure carries, bottom-left, 6.5–7.2 pt muted:
  `NFL Big Data Bowl 2027 · n = … · source: outputs/… · synthetic` (drop `synthetic` in the real run).
- Mock-ups additionally carry the diagonal band `MOCK — synthetic placeholder, not results` at
  ~6% opacity, plus a bottom-right corner stamp in a bordered box at full opacity carrying that
  exact string. The corner stamp is the element a screenshot reviewer reads first. **This text is
  not to be reused in the real notebook**; the corner slot then holds the data attribution instead.
- Figures are numbered in the caption (`Figure 3 ·`), never in the title, so the title can be a
  sentence.

---

# Why this stands out

### Football 30

The judges are asked whether the work understands football. Most entries answer with a metric.
This one opens with a man running: a speed trace under a dashed line that is *his own* ceiling,
with the shortfall drawn as a bracket in yards per second. Concepts 1, 2, 5 and 9 are built from
route geometry, route families, breaks and stems — the objects a coach already thinks in. The
analysis is restricted to **route snaps**, not all plays, and the top-k is taken per **fixed number
of route snaps** so a player cannot win by running more routes. That choice is visible in the
figures, not buried in a methods paragraph.

### Data Science 30

Three things are on screen that most entries leave in the appendix:

1. **The ceiling has error bars** and is shrunk toward the position pool (concept 8). Two attempts
   are not a measurement; the figure shows the pull.
2. **Exceedance is a probability**, not a maximum-versus-maximum comparison (concept 4, right
   panel). The design propagates ceiling uncertainty and subsamples efforts to a fixed N.
3. **The null is quantified** (concept 6): an equivalence margin, a CI, and the smallest effect
   this sample could have detected. That is the difference between "we found nothing" and "there
   is nothing here larger than X".

The falsification panel (9) and the route-family split (5) answer the two objections a strong
referee raises first: effort composition and role mix.

### Writeup 20

The writeup's job is to make a measured gap legible and to resist over-claiming. The figure order
does that work: define the exposure honestly (7), show the instrument (8), measure the gap (1, 3,
4), stress the role story (5), then report the contrast with its uncertainty (6). Every figure
caption can be written as a claim the figure itself supports — which is the test of whether the
prose is doing any work the picture isn't.

Language is constrained throughout: *incentive exposure*, never *tried harder*. The figures follow
the same rule. No figure is titled with a causal verb.

### Viz 20

The 30-second read: a dark dashed line, a coloured trace beneath it, a small bracket that says
`1.8`, and a panel footnote that says `top-k / ceiling 92%`. No legend hunting, no dual axis, no
3D, no rainbow, no dashboard. Six figures share one palette, one type scale and one margin, so the
entry looks like a paper rather than a folder of exports.

Honesty is part of the visual argument: zero-based speed axes, shared limits across facets,
pre-declared equivalence margins drawn on the chart, and placeholders that could never be mistaken
for results.

---

# Clichés to avoid

- **Generic scatter of two metrics with a fitted line.** The reader learns the sign of a
  coefficient and nothing about football. If a scatter appears at all (concept 10), it carries
  iso-utilisation bands and identity line so the *gap* is the subject.
- **Boxplot-only group comparison.** Collapses 111 players into two rectangles and hides the shape
  of the distribution, the tail, and the overlap. Use the sorted dumbbell or the fan instead.
- **Forest plot as the hero figure.** A wall of coefficients is a table with extra ink; it says
  "we ran many models", which is the opposite of a claim.
- **Dual y-axes.** Any two-series chart with two scales can be made to show any relationship. In
  this project it would be speed and probability on the same panel — refuse.
- **3D bars, 3D anything.** Perspective adds occlusion, not information, and destroys value
  reading.
- **Unordered rainbows for continuous quantities.** Rainbow ramps create false boundaries and are
  unreadable in greyscale and for colourblind readers. Neutral sequential ramps only.
- **"Dashboard" screenshots.** Six panels with six units, no hierarchy, no sentence. The notebook
  is an argument, not a control room.
- **Correlation heatmaps of everything.** They invite the reader to fish; they answer no question
  anyone asked.
- **Bar charts of p-values.** Encodes the least interesting quantity in the study and invites
  significance-star reading.
- **Truncated axes that manufacture a gap.** A 0.4 yd/s difference drawn on a 19–22 axis looks
  enormous and is indefensible. Zero-based, or an explicitly labelled zoom.
- **Animated or interactive-only encodings.** They do not survive the PDF, the screenshot, or the
  judge's skim, and the notebook must render without a server.
- **The hero photo of a player.** It dates the entry, needs rights clearance, and adds no
  measurement.

---

# Mock-ups

All six are synthetic. Generator scripts live beside the PNGs and are marked `MOCK-UP GENERATOR`
in their docstrings:

```
mockups/_style.py                 shared palette, type scale, watermark, field geometry
mockups/_gen_mock_fig01.py        The Ceiling Line
mockups/_gen_mock_fig02.py        Route Replay, Speed-Painted
mockups/_gen_mock_fig03.py        Every Player Loses Speed
mockups/_gen_mock_fig04.py        Where the Gap Lives
mockups/_gen_mock_fig05.py        Route Tree, Gap by Branch
mockups/_gen_mock_fig06.py        The Honest Null
mockups/_gen_contact_sheet.py     tiles all six
```

Run any of them with `python3 <script>` from inside `mockups/`. They need only matplotlib and
numpy, write PNGs at 200 dpi, and touch no pipeline data. `VIZ_CHECK=1` prints a text-collision
report for layout QA.

**Placeholder values are chosen to be unmistakable**: n = 111 and n = 222, ceilings of 21.4 yd/s,
differences of +0.0 pp, a 1.7 yd/s median gap. Every image carries the watermark
`MOCK — synthetic placeholder, not results`.

---

## What this pack does not do

- No real data, no pipeline run, no network, no new dependencies.
- It does not choose the final five; it ranks and recommends (see *Recommended cut*).
- It does not fix the existing `outputs/figures/fig1–fig5`; those are pipeline outputs and were
  left untouched. This is a menu for the next design pass, not a replacement.
