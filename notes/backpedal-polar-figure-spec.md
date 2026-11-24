# Spec — figure 21, `fig_backpedal_polar`

**PM spec, 2026-11-23. Owner: PM. Binding on the coder for this task only.**

## Why this figure exists

Conor, 2026-11-23 19:41, verbatim:

> "Ok maybe draw a circle where the line down is zero and then plot all the angles
> in the backpedal 45 on it"

confirmed with "That ok" when it was read back to him as: a polar plot, zero at
straight down, one point per detected turn in
`BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION`, placed at that turn's signed angle.

What he is looking for is the diagnostic, not a new claim: do the drill's measured
turns sit where a 45° break would put them (±45° from the entry line), or somewhere
else entirely — and how spread is each side.

## The population

`outputs/turns/turns.parquet`, `setting == "combine"`,
`drill_name == BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION`, `valid == True`:
**133 turns, 71 players** (of 122 tracked in this drill), all `position_group == "DB"`.
Recomputed at spec time: 63 positive, 70 negative, none zero, |angle| range
48.4°–146.4°, median |angle| 113.1°. No |angle| exceeds 180°, but the code still
wraps (see below) and prints how many it wrapped.

These counts are printed with their denominators in the figure and the manifest.
None of them is a gate: a two-row input renders.

## The geometry, and why

`turn_angle` is the signed accumulated heading change of travel,
`unwrapped[hi] - unwrapped[lo]`. In this pipeline heading 0 = +y (screen up) and
heading increases **clockwise** on screen (`kinematics.heading_deg`). Therefore:

- **"Line down is zero"** = the reference direction is data heading 180°.
- **Positive turn angle runs clockwise from the down line.** This is not a free
  choice: `exit heading = entry heading + turn_angle`, so with the entry drawn
  pointing down, the point's bearing from the down line **is the exit travel
  direction**. Plotting positive counter-clockwise would flip the handedness of a
  quantity whose sign is already defined.
- Display bearing = `turn_angle mod 360`, read clockwise from the down line on
  the same 0-360 scale the other plots use (revised at D214; the first cut used
  `wrap180`, which put the right half of the circle on a mirrored 135…0 scale).
  A |turn_angle| > 180° plots at its `mod 360` bearing and is flagged
  `bearing_wrapped` in the CSV (count printed; 0 at spec time).
- This is a **drawing convention only**. No measured angle changes; the CSV carries
  the measured `turn_angle_deg` alongside the drawn `bearing_deg`.

### What the figure may not say

The Combine tracks no body orientation (TASK.md honesty limit 4; the tracking file
has no `o`, and `dir` equals travel heading to a median 0.44° on this drill, D212).
So:
- no lobe may be labelled the player's left or his right; the labels are
  **clockwise** (positive) and **counter-clockwise** (negative) — the rotation sense
  of travel, nothing more;
- no 45° reference *line* is drawn as geometry of this drill. The 45° marks are
  protractor **scale ticks**, drawn every 45° like every other tick, and the
  manifest says so. The reader can see whether ±45 is occupied; the figure asserts
  nothing about what the drill's name measures.

## The drawing

One PNG, one polar axes, figsize ~7.2 × 7.4 in, dpi 170
(`metadata={"Software": "bdb27-figures"}`), white facecolor. Module-level constant
block `BACKPEDAL_POLAR_*`; **no new `config.yaml` key** (it would move `config_sha`
and touch every committed figure CSV).

Drawn title (axes text, **no digits**):
`The backpedal drill's turns, with zero straight down`

- Protractor: theta grid every 45°, one full circle labelled 0, 45, 90, 135, 180,
  225, 270, 315, 360 (D214); the "0" and "360" labels sit on the one bottom spoke
  and are nudged apart in points so they print side by side. Faint grey spokes;
  radial axis hidden (radius carries no variable).
- One dot per turn at `bearing_deg`, radius `1.0 + deterministic jitter` (fixed
  seed, ±0.10) so overlapping points are countable; single Combine colour, alpha.
- Each lobe's median drawn as a bold spoke from the centre to the rim; each lobe's
  middle half (25th–75th percentile of the signed angle) drawn as a thin arc at
  `r = 1.08`, so spread is visible, not just the centre.
- Axis-text annotations, digits allowed here:
  - `133 turns from 71 of 122 players tracked in this drill` (from the data);
  - per lobe: `clockwise side: n = 63, median 111.0°`, `counter-clockwise side:
    n = 70, median 243.7°` — decimals taken from the table, not typed, and the
    counter-clockwise median is a bearing on the 0-360 scale (D214), not −116.3°;
  - one honesty line: `each point is the exit direction of travel when the entry
    points straight down; positive is clockwise; the Combine records no facing`.
- No legend box, no caption, no subtitle, no figure-level text except `STAMP` when
  `on_image_stamp` is on.

## Artefacts (house conventions, nothing new)

Under `outputs/figures/`:

- `fig_backpedal_polar.png`
- `fig_backpedal_polar.csv` — one row per drawn turn, columns exactly:
  `provenance, stamp, config_sha, figure, event_id, nfl_id, attempt,
  frame_start, drill_name, turn_angle_deg, abs_turn_angle_deg, side,
  bearing_deg, bearing_wrapped, radius_drawn`
  (`side` ∈ {`positive`, `negative`, `none`}; `bearing_deg` is the drawn bearing
  on a 0-360 scale, clockwise from the down line; `turn_angle_deg` keeps the
  signed measurement).
- `fig_backpedal_polar.drawn_text.json` — same shape as
  `fig_backpedal_route.drawn_text.json`.
- one merged entry in `figures_manifest.json`: `title`, `claim`, `sources`,
  `caveats` (no facing; sign = rotation sense of travel; zero at down is a drawing
  convention; the 45 marks are scale ticks; stamp), `population` (`n_turns`,
  `n_players`, `n_players_tracked`, `median_abs_turn_angle`, `n_positive`,
  `n_negative`, `n_zero`, `n_wrapped`, per-lobe `n`, `median_deg`, `q25_deg`,
  `q75_deg`), `stamp`, `png`, `csv`, `drawn_text`, `stage`, `artefact_version`.

## Wiring

- `src/bdb27/figures.py`: a self-contained section after figure 20. It reuses
  figure 20's already-built route table (no second tracking read): a pure
  per-turn frame + plot + render + drawn-text + manifest payload.
- `src/06_figures.py`: wire-in beside figure 20, `_write_manifest` merge, extend
  the run-log `detail` line; no new CLI flag.
- `src/bdb27/figure_text_scan.py`: add `render_backpedal_polar` to
  `RENDER_FUNCTIONS`.

## Tests — `tests/test_backpedal_polar_figure.py`

Copy the shape of `tests/test_backpedal_route_figure.py`; recompute from the
committed turn table rather than trusting the figure code.

1. one row per valid turn, count == the turn table's, players == 71;
2. `side` partition sums to the total; `bearing_deg == turn_angle_deg mod 360`;
   the signed `turn_angle_deg` is still present and still negative for the
   counter-clockwise lobe;
3. the polar axes actually puts zero at the bottom and increases clockwise:
   read the axis object's zero offset and direction **and** transform a probe
   point through `ax.transData` to check screen position (theta 0 below centre,
   +90 to the left);
4. lobe counts and medians recomputed from `turns.parquet` equal the manifest's;
5. drawn text: title present, no digits in the title, no `BANNED_TOKENS` hit, no
   figure-level text beyond `STAMP`, the count line and both lobe labels present;
6. no gate: a two-row synthetic input still renders, and a 200° angle is drawn at
   −160° with `bearing_wrapped` true;
7. CSV schema is exactly the column list above, populated;
8. manifest entry exists with its caveats and population counts;
9. two renders are byte-identical.

Update existing counts: `tests/test_figures_stage.py` (20 → 21 figures, the
figure-name set, the PNG count, `figures_scanned`) and
`tests/test_notebook_contract.py` (gallery 23 → 24), plus the figure count in
`README.md` / `notebooks/README.md` where the stage table states it.

## Acceptance

- Red first: the new tests fail on the pre-change tree for the reason they name.
- Full suite green; exact collected/passed/skipped counts reported before and after.
- No existing measured output moved: stage 06 in sample mode to a **scratch**
  figures directory before and after the change, sha256-identical for every
  pre-existing PNG/CSV; only `figures_manifest.json` gains one entry.
- Two consecutive renders byte-identical.
- `notes/decisions.md` gets the D213 entry.
- Commit message: `Figure 21 — every backpedal turn on one protractor, zero down (D213)`.

## Non-goals

- No new claim, no band table, no per-prospect content.
- No facing, no left/right, no backpedal classification.
- No change to `TASK.md`, the draft, `config.yaml`, stage 02, or any existing figure.
- **TASK.md's "Figures — exactly four, and no others" is noted, not silently
  overridden:** figures 5–20 are diagnostics added since D191; this is figure 21,
  commissioned by Conor on 2026-11-23, and like them it is not one of the claim's
  four and must not be quoted in the writeup. Main reports this to Conor.

## Revision — the 0-360 scale (D214, 2026-11-23)

Conor, 2026-11-23 22:06: *"The other plots have it on a 360 scale so please do
the same."* The first cut labelled the circle on a mirrored half scale (0, 45,
90, 135, 180, 135, 90, 45), which matched no other plot in the set. The display
scale only changed:

- ticks: one full circle, 0 … 360;
- `bearing_deg`: `turn_angle mod 360` (range 0–360), so the counter-clockwise
  lobe's median reads 243.7° instead of −116.3°;
- `turn_angle_deg`, `abs_turn_angle_deg`, `side`, every count and every measured
  value are unchanged, and the signed angle stays in the CSV;
- the no-facing caveat and the DB-only population are untouched.

## Revision — zero at the top, no text on the plot (D215, 2026-11-24)

Conor, 2026-11-24 09:09: *"Could you reverse the X axis so zero is up and remove
the text on the plot."* Two changes, and only these two:

- **Zero moves from South to North** (`set_theta_zero_location("N")`); the sweep
  sense is unchanged, positive still clockwise, so the scale now reads like a
  compass rose: 0 at the top, 90 East, 180 South, 270 West. "0" and "360" share
  the top spoke and are still nudged apart in points.
- **The canvas carries the title and the 0-360 scale and nothing else.** The two
  lobe labels, the count line (`133 turns from 71 of 122 players…`) and the
  honesty note are no longer drawn. The counts, the lobe medians and the honesty
  statements stay in the manifest entry and the CSV, and
  `backpedal_polar_drawn_text` now raises if any string other than the title and
  the nine tick labels appears on the canvas.

`bearing_deg`, `turn_angle_deg`, `abs_turn_angle_deg`, `side`, `bearing_wrapped`,
`radius_drawn` and every count are unchanged; the CSV is byte-identical to the
one D214 committed. This is a drawing change only. The title wording follows the
drawing ("with zero at the top") because "zero straight down" would now be false
— that wording change is the PM's, flagged to Conor.

### Amendment to the revision — no 360 label (D215, 2026-11-24 09:12)

Conor: *"And just zero, remove the 360."* On the zero-up compass rose "0" and "360" are the same
top spoke, so the duplicate label is gone: ticks are 0, 45, 90, 135, 180, 225, 270, 315 (eight
labels, one per spoke). The nudge that used to separate the two labels and its constant are deleted
along with the label; `backpedal_polar_drawn_text` treats a returned "360" as a contract failure. No
data, count or CSV column changed — the CSV remains byte-identical to D214's.
