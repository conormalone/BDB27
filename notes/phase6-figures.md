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
