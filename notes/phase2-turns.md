# Phase 2 — kinematics and turn detection (stage 02)

Status: **implemented**. Every number on this page is computed by the stage or by the
tests in this run. Values from `--mode sample` are stamped `PLACEHOLDER_SAMPLE` and are
**not results** — they are here to show that the machinery produces well-formed output on
real schemas, never as a finding.

Binding spec: `TASK.md` @ `52015cd`, Phase 2 + Engineering + Data constraints.

## What the stage does

`src/02_turns.py` walks every Combine attempt and every game snap through one engine,
`src/bdb27/turns.py::Engine.analyse`:

1. **Smooth** x/y with every configured candidate (`savgol_g5_p2`, `g7_p3`, `g9_p3`
   primary, `g11_p3`, `kalman_cv`, `spline_cubic`) on the same regular time grid.
2. **Differentiate once** per smoother: velocity, speed, heading (`atan2(vx, vy)`,
   differences wrapped to [-180, 180)), tangential acceleration, angular rate. Raw
   positions are never differentiated.
3. **Detect** turns with the shared detector: heading change ≥ 30° across 0.5 s, speed
   floor at entry and exit, segmentation from the last speed maximum before the apex to the
   first after (D124, still OPEN with Conor; `fixed_window` is retained as a named option).
4. **Measure** entry/exit/apex speed, turn angle, duration, kinematic and geometric path
   radius, peak lateral acceleration (v·ω) with a v²/r cross-check, tangential acceleration
   at entry and exit, speed retention, time to regain entry speed, and context.
5. **Classify** by kinematics only: `plant` or `arc` (D134).
6. **Carry smoother uncertainty**: the primary value plus min/max over every smoother that
   found the same turn (centre within 0.5 s), and the match count.
7. **Flag** gaps, inserted frames, speed/acceleration cap breaches, and — in game data only
   — sustained |o − dir| divergence.
8. **Stream** one row per turn to `outputs/turns/turns.parquet`, plus per-attempt,
   per-drill and per-player tables.

## Outputs

| file | content |
|---|---|
| `turns.parquet` | one row per turn; 64 columns; the only table Phase 3+ reads |
| `attempts.parquet` | one row per attempt/snap, per-smoother detection counts, gap/duplicate counts |
| `summary.parquet` | small per-drill and per-player aggregates |
| `sanity.csv` | speed cap, acceleration cap, duplicates, gaps, detections per 1,000 frames vs shuffled-heading null — per drill/snap and TOTAL |
| `smoother_counts.csv` | detections per smoother per setting |
| `segmentation_sensitivity.csv` | Combine turn counts under `speed_maxima` vs `fixed_window` |
| `geometry_recovery.csv` | precision/recall vs estimated cones under **both** point and line models |
| `manifest.json` | provenance, config fingerprint, smoother/segmentation/geometry settings, open decisions |

Parquet carries `provenance`, `label` and `config_sha` as file metadata. `generated_utc`
lives only in the manifest, so the same inputs give a byte-identical turn table.

## Synthetic recovery (the acceptance test for this stage)

`tests/test_turns_stage.py` runs the real stage on `fixtures/synthetic/`, whose planted
corners and capacities are known:

| quantity | value | config gate |
|---|---:|---|
| precision (detection within 1.5 yd of a planted corner) | **1.000** | ≥ 0.80 |
| recall (planted corners recovered) | **1.000** | ≥ 0.70 |
| turn-angle MAE vs the planted corner angle | **3.1°** (48 turns) | ≤ 25° |

Also covered: the sub-maximal attempt is recovered and slower than the maximal one;
dropped frames are interpolated and flagged inside turns; a stop mid-drill produces no
exception and no angle beyond 360°; a straight line produces no turns; a snap with no
`player_play` row carries null `snap_type`/`alignment` without crashing; identical turns
are deterministic.

## Real-schema check (`--mode sample` — PLACEHOLDER, not a result)

Detector against estimated drill geometry on the sample files, both target models:

| drill | gate model | precision (point) | recall (point) | precision (line) | recall (line) | angle MAE |
|---|---|---:|---:|---:|---:|---:|
| SHORT_SHUTTLE | line | 0.711 | 0.688 | **0.991** | **0.959** | 2.6° |
| THREE_CONE_DRILL | point | **0.915** | **0.931** | 0.967 | 0.952 | 47.0° |

These reproduce the audit's provisional numbers exactly, which is the intended
consistency check: Phase 2's default detector *is* the detector the gate validated.

Turn counts in the sample run: 5,536 Combine turns and 7,143 game turns (PLACEHOLDER).
Smoother spread (median across turns): entry speed 0.33 yd/s, exit 0.33, apex 0.18, |angle|
5.8°, speed retention 0.12, peak lateral acceleration 1.99 yd/s²; 58.5% of turns were
matched by all six smoothers. Sample sanity: 16 frames above the 12 yd/s cap (one tracking
glitch), 62 frames above the 15 yd/s² acceleration cap, 0 duplicate frames, 6 turns
invalidated by the speed cap, 0 by gaps above the limit. Orientation divergence
(|o − dir| > 60° sustained) flags 37.1% of game turns in this row sample — a
contamination diagnostic to be re-measured on full data, never part of the definition.

## What is deliberately absent

* No thresholds were invented: every parameter is in `config.yaml` with a design-choice
  comment. `00_thresholds` semantics are unchanged.
* No tuning against sample output, and no analysis of sample results.
* `notes/prereg.md` was not written: it is fixed before the first full run and needs the
  Phase 3 bin edges and envelope form.
* D124 (segmentation) and D125 (point vs line) are **open**, implemented as parameters,
  and both alternatives are reported.
