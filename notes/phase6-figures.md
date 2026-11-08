# Phase 6 — figures (delivered incrementally)

## Figure 1 — "The Hole"
`src/06_figures.py --mode sample` renders `outputs/figures/fig_the_hole.png`
(1725 × 1140 px, 150 dpi) plus `fig_the_hole.csv` and
`figures_manifest.json`. DB only, valid turns, bands from `figures.hole`
(D164): 30–45, 45–60, 60–90, 90–120, 120–150, 150–180, and a separate >180
class. Panel (a) is raw counts; panel (b) is each setting's own share, which is
the exposure-corrected view (122 Combine players × one session vs 33 game
players × 299 player-games). The 30–45° band is highlighted and directly
labelled.

**Verified sample counts (recomputed at render time, never hard-coded):**

| band | Combine | Game |
|---|---:|---:|
| 30–45° | 4 | 97 |
| 45–60° | 22 | 229 |
| 60–90° | 237 | 476 |
| 90–120° | 159 | 508 |
| 120–150° | 499 | 460 |
| 150–180° | 128 | 307 |
| >180° | 130 | 185 |

Caption caveats: the Combine contains no deliberate 30–45° break at speed (the
4 detections are incidental fragments, not a test); exposure differs by
setting; the DB demand denominator is all 2,262 valid DB game turns, with the
snap-classified `COVERAGE` subset at 1,111 and 1,121 turns (49.6%) snapped
"other". Every artefact carries `PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE
OUTPUT`. Two runs produce a byte-identical PNG.

**Deliberately not built in this run:** figures 2–3 (position comparison and
the drill spec). Stage 06 stays narrow until figure 1 is accepted.

## Figure 6 — "The measurement limits" (slot e)
`outputs/figures/fig_measurement_limits.png` (1980 × 1290 px, 150 dpi) plus
`fig_measurement_limits.csv` and a manifest entry. Two panels against two
source files: `outputs/envelope/variance_components.csv` (left) and
`outputs/turns/turns.parquet` (right, valid turns). Left: `tau2` with its
p05–p95 span, one row per setting × position × measure; **43 of 51** components
are `AT_BOUNDARY` and are drawn as zero-width empty bars on the baseline with a
hollow point, counted on the image. Right: binned median (solid) and p95
(dashed) peak lateral acceleration by angle; bands at or above 150° are greyed
and struck through as "not physical" because reversal-class turns are plant
measurements, not v·ω. The threshold is
`figures.measurement_limits.reversal_zone_deg` and must be a bin edge.

Sample values recomputed at render time: pooled median lateral acceleration
9.5 yd/s², p95 53.1; reversal zone (≥150°) median 29.1 yd/s² (2.7 g), p95 73.9
(6.9 g) over 3,184 turns. The caption carries the counterweight: 3.1° MAE on
planted 45° turns (48 turns) and three-cone precision 0.915 / recall 0.931,
sourced from `notes/phase2-turns.md` and `outputs/audit/gate.json`.
No per-player ranking, player names, scores, p-values or error bars on counts
appear anywhere in the figure, CSV or manifest.
