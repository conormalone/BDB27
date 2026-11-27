# Spec — figure 20, `fig_backpedal_route`

**PM spec, 2026-11-23. Owner: PM. Binding on the coder for this task only.**

## Why this figure exists

Conor, 2026-11-23 14:10, verbatim:

> "Ok is there a plot we could do that maybe show the route of backpedal 45 and an
> arrow showing where they were looking for the turn?"

Context he already has: the turn angle is the accumulated heading change of
*travel* (`kinematics.detect_turns`: `angle = unwrapped[hi] - unwrapped[lo]`), and
`BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION` reads a **median 113.1° over 133
detected turns from 71 players**. He wants to see the route itself to understand
why a drill with "45" in its name is measured at 113°.

### The one thing this figure may NOT show, and why

The Combine tracking file has columns `x, y, s, a, dis, dir` — **no `o`**. The
`dir` column is not body orientation: on this drill it equals the travel heading
derived from smoothed `x/y` to a median |difference| of **0.44°** (p95 1.48°,
n = 133 turn-start frames), and the pipeline already records that fact — for
`setting == "combine"`, `orientation_flag` is never computed
(`turns.py::_turn_row`, guarded on `self.setting == "game"`), which is exactly
TASK.md honesty limit 4 ("No orientation in the Combine").

So **there is no "where he was looking" channel**, and no arrow drawn from `dir`
can show it: such an arrow would redraw the travel heading the angle is already
computed from. The figure must say so rather than imply a facing measurement.
Everything drawn is a measurement of the path.

## Data

| Input | Path | Note |
|---|---|---|
| Turn table | `outputs/turns/turns.parquet` | `setting == "combine"`, `drill_name == "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION"`, `valid == True` → 133 rows, 71 players, 5 attempt numbers |
| Combine tracking | `data/raw/combine_tracking.csv` (parquet cache via `common.ensure_parquet(cfg, mode, "combine_tracking", ...)`) | complete Combine file in **both** modes; `entity_type == "PLAYER"` |

Measured population in the tracking file for this drill: **241 player-attempts
from 122 players**; 71 of those 122 players produced a detected turn. Both
numbers are printed (count with denominator), neither is a gate.

## Method — reconstruct the exact series the angle was measured on

Do **not** plot raw `x/y` and call it the measurement, and do **not** hand-parse
the CSV. Reproduce stage 02's own path, using the library functions:

1. Load rows through DuckDB exactly as `02_turns.load_combine` does:
   `SELECT drill_name, event_id, nfl_id, attempt, epoch_ms(time) AS ms, x, y
    FROM read_parquet(<combine_tracking parquet>) WHERE entity_type = 'PLAYER'
    AND drill_name = <drill> ORDER BY event_id, nfl_id, attempt, ms`,
   then drop duplicates on `["nfl_id","event_id","attempt","ms"]` (the config
   has `sampling.drop_duplicate_frames: true`). **A pandas `pd.to_datetime`
   parse is not equivalent** — it produced NaT and silently collapsed rows in
   the feasibility probe. Use `epoch_ms`.
2. Group by `["event_id", "nfl_id", "attempt"]` — the same unit stage 02 uses.
3. `ft, fx, fy, inserted, gaps = turns.fill_small_gaps(ms, x, y, cfg sampling.dt_s, cfg sampling.max_gap_s)`
4. `eng = turns.Engine.from_config(cfg, "combine")`;
   `xs, ys = kinematics.smooth_positions(ft, fx, fy, eng.smoother_specs[eng.primary])`
   (primary = `smoothing.primary`, currently `savgol_g9_p3`);
   `d = kinematics.derivatives(ft, xs, ys)`.
5. `frame_start` / `frame_centre` / `frame_end` are indices into `ft`. Heading
   used by the detector is `d["heading_unwrapped"]` because
   `detector.low_speed_heading_hold` is `false`; if that config is ever true,
   `kinematics.hold_heading_below_floor(d["heading"], d["speed"], eng.det.speed_floor)`
   must be applied first and then unwrapped. Read the flag, do not assume it.
6. **Consistency check, printed not gated:** recomputed
   `uw[frame_end] - uw[frame_start]` must equal the table's `turn_angle`. The
   feasibility probe gives max |diff| = 7.2e-6° over all 133 turns (float32
   storage). Print `n_agree` / `n_turns` with the tolerance used; carry a
   per-row `reconstruction_agrees` flag in the CSV; a mismatch is labelled on
   the row, never a crash and never a silent pass.

Nothing above is a new stage, a new threshold or a new analysis: it re-derives a
series stage 02 already computed, from the same inputs and the same config.

## The drawing

One PNG, three panels, width ~16 in, height ~5.6 in, dpi 170
(`metadata={"Software": "bdb27-figures"}`), white facecolor.

Drawn title (axes text on panel 1, **no digits**):
`Route and heading through the backpedal drill`

Panel 1 — *Every route, entry heading up*
- All 133 smoothed paths, each translated so `frame_start` is at the origin and
  rotated so the **entry travel heading points up (+y)**. Units are yards; do
  not rescale per route.
- Thin light-ink lines, low alpha; the pointwise median path in bold dark ink
  (resample each route on normalised progress 0→1 to its own `frame_end`, take
  the median in the rotated frame).
- One arrow from the common origin along +y, labelled `entry heading`.
- Annotation (axis text, digits allowed here): `133 turns from 71 of 122
  players tracked in this drill` — the numbers come from the data, not from
  constants in the drawing code.

Panel 2 — *One route, annotated*
- **Exemplar rule:** the turn whose `abs_turn_angle` is closest to the median of
  the 133. State the rule in the manifest entry, not on the image.
- The whole attempt's smoothed path in dark ink, equal aspect (`set_aspect
  ("equal")`), in yards; the raw tracking positions as small faint dots so the
  smoothing is visible, not hidden.
- That attempt's **every** detected turn: the exemplar's span (`frame_start` →
  `frame_end`) drawn thick in `ALERT` (`#B00020`); any other turn of the same
  attempt drawn thin and unlabelled.
- Apex (`frame_centre`, the speed minimum) as an open marker, labelled `apex`.
- An arrow at `frame_start` along the entry travel heading, labelled
  `entry` with its speed in yd/s (entry speed from the row); an arrow at
  `frame_end` along the exit heading, labelled `exit` with its speed.
- The swept angle drawn between the two heading directions, annotated with the
  measured `turn_angle` in degrees.
- One honesty line, small, inside the panel:
  `arrows are the direction of travel; the Combine records no facing`
- No legend box, no caption block, no subtitle.

Panel 3 — *Heading through the turn*
- x: seconds from `frame_start` (0 at the start of the span, positive after);
  y: heading change since `frame_start` in degrees (`uw[t] - uw[frame_start]`,
  unwrapped, i.e. the quantity whose endpoint *is* `turn_angle`).
- All 133 turns as thin light lines, the median as a bold dark line.
- Zero line at `y = 0` only. **No 45° reference line**: the drill's *name*
  carries "45", but the data do not define what the label measures, so drawing
  it would assert geometry nothing in the repo establishes. The panel's job is
  to show the accumulation; the manifest states the drill name and median.
- Axis labels: `seconds into the turn`, `heading change since the turn began (°)`.

Colour: `ALERT` for the highlighted span, `PALETTE["combine"]` if a second
colour is needed, ink greys elsewhere. A module-level constant block
(`BACKPEDAL_*`) for figsize/dpi/fonts/colours/linestyles, following
`POP_SHRINK_*`. **No new `config.yaml` key** — a new key moves `config_sha` and
would touch every committed figure CSV.

## Artefacts (house conventions, nothing new)

Under `outputs/figures/`:

- `fig_backpedal_route.png`
- `fig_backpedal_route.csv` — one row per drawn turn (133 rows), columns:
  `provenance, stamp, config_sha, figure, event_id, nfl_id, attempt,
  drill_name, frame_start, frame_centre, frame_end, n_points, attempt_frames,
  path_length_yd, net_displacement_yd, entry_heading_deg, exit_heading_deg,
  turn_angle_deg, abs_turn_angle_deg, apex_speed_yd_s, entry_speed_yd_s,
  exit_speed_yd_s, recon_angle_deg, reconstruction_agrees, dir_vs_heading_deg
  (median |dir − travel heading| over the span), is_exemplar`
- `fig_backpedal_route.drawn_text.json` — `figure_texts(fig)` read back off the
  object, plus title, `banned_tokens`, `banned_hits`, provenance, `config_sha` —
  the same shape as `population_shrink_drawn_text`. Figure-level `fig.texts`
  must be empty (or only `STAMP` when `on_image_stamp` is on).
- one merged entry in `figures_manifest.json`: `title` (the drawn title),
  `claim` (one sentence: the route and the 113° accumulation, with the count and
  the no-facing statement), `sources`, `caveats` (no facing channel; `dir`
  equals travel heading; sample-mode stamp; Combine file complete in both
  modes), `population` (`n_turns`, `n_players`, `n_players_tracked`,
  `n_attempts`, `median_abs_turn_angle`), `stamp`, `png`, `csv`,
  `drawn_text`, `stage`, `artefact_version`.

Stamp is `figures.stamp_text(mode)` like every other stage-06 figure: sample →
`PLACEHOLDER_SAMPLE`, full → `FULL DATA OUTPUT`.

## Wiring

- `src/bdb27/figures.py`: a self-contained section appended after figure 19
  (`FIGURE_KEY_BACKPEDAL = "fig_backpedal_route"`, loader, per-turn table,
  plotting, render, drawn-text dump, manifest payload).
- `src/06_figures.py`: wire-in beside figure 19, using `_write_manifest` to
  merge; extend the run-log `detail` line; no new CLI flag.
- `src/bdb27/figure_text_scan.py`: add `render_backpedal_route` to
  `RENDER_FUNCTIONS`.
- Runs in **both** modes: the Combine file is complete in both, so the figure is
  renderable on Conor's laptop in `--mode full` without the game tracking file.

## Tests — `tests/test_backpedal_route_figure.py`

Copy the shape of `tests/test_population_shrink.py` (module-scoped `cfg`,
recompute the numbers from the committed files rather than trusting the figure
code):

1. reconstruction reproduces `turn_angle` for every drawn turn (tolerance 1e-3°);
2. exactly one record per valid turn for the drill, and CSV rows == that count;
3. `exit_heading − entry_heading == turn_angle` per row (tolerance 1e-6);
4. `dir` ≈ travel heading: median |diff| < 1° over the drawn turns, **and** the
   combine tracking schema contains no `o` column — pinning "no facing" to the
   data rather than to prose;
5. drawn text: title present, no digits in the title, no `BANNED_TOKENS` hit, no
   figure-level text beyond `STAMP`, and every annotation string the panel draws
   appears in the dump;
6. no gate: rendering from a two-row input still returns a figure (no exception,
   no refusal);
7. CSV schema is exactly the column list above, with `provenance`, `stamp`,
   `config_sha`, `figure` populated;
8. manifest entry exists with its `caveats` and `population` counts, and the
   population numbers equal the ones recomputed from `turns.parquet` and the
   combine tracking.

Update existing counts: `tests/test_figures_stage.py` (19 → 20 figures, the two
figure-name sets, the per-figure file loop) and `tests/test_notebook_contract.py`
(gallery 22 → 23), plus the figure count in `README.md` /
`notebooks/README.md` where the stage table states it.

## Acceptance

- Full suite green; report the exact collected/passed/skipped counts before and
  after.
- Proof no existing measured output moved: run stage 06 in sample mode on the
  pre-change commit and again with the change, both into a **scratch** figures
  directory (override `paths.figures`), and diff the sha256 of every pre-existing
  PNG/CSV — all identical. Only `figures_manifest.json` may differ, by the one
  added entry. Then write the new figure's three files into the real
  `outputs/figures/` and merge its manifest entry; `git status` must show no
  modified existing figure.
- Deterministic: two consecutive renders of the new figure are byte-identical.
- `notes/decisions.md` gets the D212 entry (PM writes it; coder includes it in
  the commit).
- Commit message: `Figure 20 — the backpedal drill's route, and the turn it is measured as (D212)`.

## Non-goals

- No new claim, no new band, no per-prospect content (TASK.md forbids a Turn
  Coverage score, a Cut Card and any per-player ranking: 133 turns from 71
  players are drawn as a population, never as a ranking).
- No facing/orientation inference, no backpedal classification, no "45°"
  reference geometry.
- No change to `TASK.md`, the draft, `config.yaml`, stage 02, or any existing
  figure.
- **TASK.md's "Figures — exactly four, and no others" clause is noted and not
  silently overridden:** this figure is a diagnostic Conor commissioned on
  2026-11-23 and, like figures 5–19 added since D191, it is not part of the
  claim's four. It must not be quoted in the writeup. Main reports this to
  Conor.

> **Amended 2026-11-27 (D226).** The cap is withdrawn; this figure remains off-paper as a
> diagnostic (it is not in the six named in `TASK.md` §Figures) and is not quoted in the writeup.
