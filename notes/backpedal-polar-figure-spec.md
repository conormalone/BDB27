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

## Revision — retitle (D216, 2026-11-24 11:03)

Conor: *"Make the title 'every turn in the backpedal'."* One constant changed:
`BACKPEDAL_POLAR_TITLE = "Every turn in the backpedal"`. The drawing, the eight
tick labels, the dots, the median spokes and the IQR arcs are unchanged; the
canvas still carries the title and the scale and nothing else. The CSV is
byte-identical to D215's (same sha256), so every measured value and count stands.

## Revision — the overhead player at the centre (D217, 2026-11-24 11:24)

Conor: *"Could you also put an overhead view of a player at the centre to show
that it's the way they are facing."* A schematic seen from above, drawn as three
vector patches at the origin, with the front on the 0° spoke at the top:

- **shoulders** — an ellipse 0.62 × 0.38 of the glyph's extent, wider across than
  deep, so the shoulder line is horizontal and perpendicular to the facing;
- **chest** — a grey wedge rising out of the shoulder line to a point on the 0°
  spoke: the body's own direction cue, no arrow head, no label, no legend;
- **head** — a white circle 0.30 of the extent on the shoulder line, over the
  wedge.

Sizes are radial units of the protractor (`BACKPEDAL_POLAR_MARKER_EXTENT = 0.56`),
converted to axes fraction by `_polar_axes_fraction_per_unit` because a patch in
polar data coordinates would be sheared by the projection; the glyph is therefore
vector-only and round in pixels (the head is asserted round, not assumed).

**It cannot cover a turn.** The glyph's furthest vertex is at r = 0.56; the
nearest drawn turn on the committed data is at r = 0.9017, and the glyph is also
inside the 0.80 × RMAX = 1.072 canvas budget from Main. It is drawn at zorder
2.5–2.7, under the turns at 3, so a dot always wins; a test asserts the clearance
against `min(radius_drawn)` recomputed from the drawn table, not a guess.

**One dependent drawing change.** The lobe median spokes start at the origin, so
they would have crossed the glyph's head. They are now drawn at zorder 2.0, under
the glyph (2.5–2.7), so the two bearing lines emerge from the player's shoulders
instead. Nothing else moves: the spokes still run from the origin to 0.80 and the
turns start at 0.90, so the dots never met a spoke either way and no dot pixel
changes.

**Unchanged.** No on-canvas string is added or removed (title plus 0 … 315); the
CSV is byte-identical (`f186ebbc…`), lobes 111.0° n = 63 and 243.7° n = 70; the
drawn-text dump and the manifest entry are byte-identical; no other figure's PNG
changes. Grey ink only — red stays the lobes' colour.

## Revision — the supplied image replaces the vector player, and no red (D218, 2026-11-24 13:35)

Conor, with an image attached: *"Use this image instead of whatever that is and drop all the red."*
The three vector patches D217 drew are gone, and so is every red element of figure 21.

**The marker is the committed image.** The source (612 × 408 JPEG) is committed byte-for-byte at
`assets/fig_backpedal_polar_marker.jpg` (sha256 `0d8ea506…`), because the inbound media path is
transient; the digest, path and every masking number travel in
`outputs/figures/fig_backpedal_polar.drawn_text.json` under `"marker"`.

**Masking, estimated not guessed.** The background is the light grey studio gradient. Its red
dominance (`R − (G+B)/2`) is measured on a 6 px border ring — p99 = **−0.0118** on this image — and
the subject threshold is that value plus 0.05 = **0.0382**, printed in the sidecar. A 5 × 5 close,
hole fill and 3 × 3 open turn the red-dominant pixels into a silhouette of **29,261 px** (crop
321 × 262, bbox x 146–467, y 109–371 with a 4 px pad), keeping the subject's white and dark interior
detail as holes in the mask. Luminance inside the silhouette is then mapped linearly onto a neutral
grey ramp (0.12–0.72); every painted pixel has R = G = B, so the marker carries no red at all, and
its alpha channel is the mask, so it is a silhouette, not a grey rectangle.

**Placement.** The image is drawn at the polar origin with `imshow`, extent in axes fractions,
`aspect="equal"` so the polar axes stay circular and the image keeps its own 262 : 321 aspect after
rotation (asserted from the drawn extent). `BACKPEDAL_POLAR_MARKER_HALF_HEIGHT = 0.46` radial units
gives a half-width of 0.37545 and a corner reach of **0.59377**; the nearest drawn turn is at
**0.9017**, so the clearance is **0.3079 radial units** against a 0.05 margin, asserted through the
transform chain against `min(radius_drawn)`, never a constant. It is clipped to the polar patch and
drawn at zorder 2.5 — above the median spokes (2.0), below the turns (3).

**Orientation: 270° → 0°.** Conor, 2026-11-24 13:41, after the first render: *"The player is facing
270 degrees so rotate him"*. On this dial 0 is up, 90 East, 180 down, 270 West, so the source
player faces screen-left; a quarter turn clockwise on screen (+90° in the dial's clockwise-positive
sense) turns 270 into 0. `PIL.Image.rotate` is counter-clockwise-positive, so the constant is
`BACKPEDAL_POLAR_MARKER_ROTATION_DEG = -90.0`. The image is re-cropped after rotation and the
clearance assertions still hold.

**No red anywhere.** The lobe median spokes and the middle-half IQR arcs keep their geometry and
length but are drawn in neutral ink (`#555555` spokes, `#6b6b6b` arcs) instead of `ALERT`; the
provenance stamp, when enabled, is neutral too. A test scans a freshly rendered PNG and counts
reddish pixels (R > 0.35 and R − max(G, B) > 0.10): **0** remain, against **4,521** before.

**Unchanged.** On-canvas strings are exactly `Every turn in the backpedal`, `0`, `45`, `90`, `135`,
`180`, `225`, `270`, `315`. The CSV is byte-identical (`f186ebbc…`, lobes 111.0° n = 63, 243.7°
n = 70); no other figure's PNG or CSV changed; two renders are byte-identical (`775e1988…`).

## Revision — the marker in the player's own colours (D219, 2026-11-24)

Conor: *"Ok, try it again, use the original colours of the player this time"*. Amends D218 in one
respect: the masked subject is painted from its **source RGB** instead of a neutral-grey luminance
ramp.

- Removed: `BACKPEDAL_POLAR_MARKER_INK_DARK/_LIGHT` and the ramp they fed; the sidecar's
  `luminance_range`; the 0.95 alpha multiplier (`BACKPEDAL_POLAR_MARKER_ALPHA = 1.0`).
- Unchanged: the border-estimated background threshold, close/fill/open morphology, crop,
  `BACKPEDAL_POLAR_MARKER_ROTATION_DEG = -90.0`, half-height 0.46, aspect, zorder 2.5, clip and the
  measured clearance against `min(radius_drawn)`.
- Alpha is still the mask (Gaussian-antialiased at the edge); RGB is carried through, with the
  transparent surround filled from the nearest subject pixel so resampling does not pull grey into
  the silhouette edge. The sidecar's `"marker"` block gains `"colour_mode": "original"`.
- The neutral-ink lobe medians, IQR arcs and stamp stay: "drop all the red" (D218) governs the plot
  furniture, not Conor's image.
- The D218 "zero reddish pixels on the PNG" test is replaced by "red only inside the marker's drawn
  footprint": **24,381 inside, 0 outside** on the committed artefact, with the inside count
  asserted positive.
- CSV byte-identical (`f186ebbc…`); manifest entry identical; strings unchanged; no other figure's
  PNG or CSV changed; two renders byte-identical (`7eed0cbd…`).
