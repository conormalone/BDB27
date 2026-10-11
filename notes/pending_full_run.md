# Pending full run — questions only the full data can answer

These cannot be answered on this Pi (game tracking here is a non-representative 2²⁰-row sample; the full
`game_tracking_{2023,24,25}.csv` are **absent**). Each lists the stage that answers it. **Combine-side work
is NOT pending** — the Combine file is complete, so `02_combine_envelope` runs for real in both modes.

| # | Question | Stage | Notes |
|---|---|---|---|
| P1 | **Matched Combine→NFL receivers** (audit item c): how many of the 510 prospects appear in in-game tracking, by position (WR/TE focus)? | `01_audit` (c) | Needs `game_tracking_{2023,24,25}.csv`. Sample mode emits a PENDING placeholder. **Preview (audit 2026-10-11)** via the complete `player_play.csv`: **WR 106/107, TE 38/42**; sample *tracking* prefix matched only WR 39/TE 11 (non-representative). |
| P2 | **Route snaps/player** (audit item c) via `route_ran` — is the median above `audit.min_route_snaps_per_player`? | `01_audit` (c) | The go/no-go gate for the per-player tail. **Preview (audit 2026-10-11)**: median route snaps **WR 261, TE 184** (from complete `player_play.csv`). |
| P3 | **Audit items c/d/h/i game-side** — route/coverage field behaviour, effort distributions, coverage splits. | `01_audit` (c,d,h,i) | Sample non-representative → full run only. |
| P4 | **Route-effort thresholds** with provenance `FULL` (high-speed/accel cut-offs). | `00_thresholds` | Never set from the sample; models assert provenance == `FULL` in full mode. |
| P5 | **Per-effort game demand** (Phase 3): route snap counts, top-k efforts, per-effort speed/accel, envelope fraction. | `03_game_demand` | The scale driver — stream per game/player. |
| P6 | **Quantile models** (Phase 4): does the Combine→game link strengthen toward the upper tail? Exceedance rate; utilization `u`. | `04_models` | The core result. |
| P7 | **Validation** (Phase 5): player-grouped CV vs. controls-only baseline; permutation; measurement-error correction. | `05_validate` | Requires frozen `notes/prereg.md`. |
| P8 | **Scale/RAM extrapolation** to the three full seasons. | all game-side | Confirm no stage exceeds the big machine's RAM; update `notes/scale.md`. |
| P9 | **Timezone confirmation** for `time`. | `01_audit` (a) + manual | Assumed UTC. |
| P10 | **ICC of per-player Combine features** — which features are reliable enough to retain (≤3). | `01_audit` / `02_combine_envelope` | Combine-side; computable, but the threshold lives in `prereg.md`. |
| P11 | **Competition cap re-verification** at source. | manual | `notes/rules.md` §6. |

**Gate.** A full run must not start until: `notes/prereg.md` is frozen (not skeleton) **and** the audit
go/no-go gate (P1 matched-WR count, P2 route snaps, missing Combine fields, ICC) passes. Otherwise →
`notes/blockers.md`.
