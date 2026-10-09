# Pending full run — questions only the full data can answer

These cannot be answered on this Pi (game tracking here is a non-representative 2²⁰-row sample) and are
**PENDING FULL RUN**. Each lists the script/step that answers it.

| # | Question | Script | Notes |
|---|---|---|---|
| P1 | **Audit (e): matched Combine→NFL players per position group.** How many of the 510 prospects appear in in-game tracking, by position? | `src/01_audit.py --mode full` | Needs `game_tracking_{2023,24,25}.csv`. Sample mode emits a PENDING FULL RUN placeholder. |
| P8 | **Matched-N per candidate population** (DB-only / DB+WR / DB+WR+DL+OL) — how many players have BOTH ≥3 observed combine attempts (all-drills family) AND game data? Feeds the D13 position-scope decision. | `src/01_audit.py --mode full` (`h_family_scope.csv`, `matched_*` columns) | Combine side already computed (all populations 100% ≥3); only the **matched** column is PENDING. |
| P2 | **In-game load thresholds** with `provenance="FULL"` (effort cutoffs, minimum snaps). | `src/00_thresholds.py --mode full` | Phase 4 asserts provenance == "FULL"; never set from the sample. |
| P3 | **In-game decay model** (Phase 4) on real per-game tracking. | `src/04_game_features.py --mode full` | Chunk per game/player; the scale driver. |
| P4 | **Link & validate** (Phase 5): does combine fatigue slope predict game decay slope beyond controls? | `src/05_link.py --mode full` | Includes error-in-both-slopes, grouped CV, placebo. |
| P5 | **Scale/RAM extrapolation** to the three full seasons. | `src/01_audit.py` + `notes/scale.md` | Confirm no stage exceeds the big machine's RAM. |
| P6 | **Timezone confirmation** for `time`. | 01_audit (a) + manual | Assumed UTC (D11). |
| P7 | **Competition cap re-verification** at source. | manual | `notes/rules.md` §6. |
| P9 | **Confirm the ≥3-observed-attempts counts on full data** under the all-drills family (the combine file is already full here, so this is a re-confirm, not new data). | `src/01_audit.py` (`h_family_scope.csv`) | Sample/combine output is already full-combine; marked for the record. |
