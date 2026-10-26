# Pending full run — questions only the full data can answer

These cannot be answered on this Pi (game tracking here is a non-representative 2²⁰-row sample; the full
`game_tracking_{2023,24,25}.csv` are **absent**). Each lists the stage that answers it. **Combine-side work
is NOT pending** — the Combine file is complete, so `02_combine_envelope` runs for real in both modes.

| # | Question | Stage | Notes |
|---|---|---|---|
| P1 | **Matched Combine→NFL receivers** (audit item c): how many of the 510 prospects appear in in-game tracking, by position (WR/TE focus)? | `01_audit` (c) | Needs `game_tracking_{2023,24,25}.csv`. Sample mode emits a PENDING placeholder. **Preview (audit 2026-10-11)** via the complete `player_play.csv`: **WR 106/107, TE 38/42**; sample *tracking* prefix matched only WR 39/TE 11 (non-representative). |
| P2 | **Route snaps/player** (audit item c) via `route_ran` — is the median above `audit.min_route_snaps_per_player`? | `01_audit` (c) | The go/no-go gate for the per-player tail. **Preview (audit 2026-10-11)**: median route snaps **WR 261, TE 184** (from complete `player_play.csv`). |
| P3 | **Audit items c/d/h/i game-side** — route/coverage field behaviour, effort distributions, coverage splits. | `01_audit` (c,d,h,i) | Sample non-representative → full run only. |
| P4 | **Route-effort thresholds** with provenance `FULL` (high-speed/accel cut-offs). | `00_thresholds` | Never set from the sample; models assert provenance == `FULL` in full mode. **Stage BUILT (D30 2026-10-12)** and exercised on the synthetic fixture; the real `--mode full` derivation is `PENDING FULL RUN`. |
| P5 | **Per-effort game demand** (Phase 3): route snap counts, top-k efforts, per-effort speed/accel, envelope fraction. | `03_game_demand` | The scale driver — stream per game/player. **Stage BUILT (D30 2026-10-12)** and exercised on the synthetic fixture (planted ceiling: binding 0.109 / null 1.00 exceedance); the real `--mode full` run is `PENDING FULL RUN`. |
| P6 | **Quantile models** (Phase 4): does the Combine→game link strengthen toward the upper tail? Exceedance rate; utilization `u`. | `04_models` | The core result. **Stage BUILT (D31 2026-10-12)** and exercised on the synthetic fixture (binding exceedance 0.109 / verdict `binding`, u median 0.93; null exceedance 1.00 / `not_binding_use_utilization`, u 1.22); the real `--mode full` run is `PENDING FULL RUN`. |
| P7 | **Validation** (Phase 5): player-grouped CV vs. controls-only baseline; permutation; measurement-error correction. | `05_validate` | Requires frozen `notes/prereg.md`. **Stage BUILT (D31 2026-10-12)**, exercised on the synthetic fixture (both regimes); real run `PENDING FULL RUN`. |
| P8 | **Scale/RAM extrapolation** to the three full seasons. | all game-side | Confirm no stage exceeds the big machine's RAM; update `notes/scale.md`. |
| P9 | **Timezone confirmation** for `time`. | `01_audit` (a) + manual | Assumed UTC. |
| P10 | **ICC of per-player Combine features** — which features are reliable enough to retain (≤3). | `01_audit` / `02_combine_envelope` | **DONE (Combine-side, D29 2026-10-12).** Threshold frozen (`envelope.icc_min=0.5`). Retained = `peak_speed`, `speed_at_20yd`, `env_vmax`; `env_tau`/`env_a0` and raw timing splits dropped. |
| P11 | **Competition cap re-verification** at source. | manual | `notes/rules.md` §6. |

**Gate.** A full run must not start until: `notes/prereg.md` is frozen (not skeleton) **and** the audit
go/no-go gate (P1 matched-WR count, P2 route snaps, missing Combine fields, ICC) passes. Otherwise →
`notes/blockers.md`. **Status (2026-10-12):** `notes/prereg.md` is **FROZEN** and the freeze gate passes
(`src/gate.py`), so **Combine-side** `--mode full` runs (`02_combine_envelope`) proceed. **Game-side** stages
(`01_audit` full items c/d/h/i, `03/04/05`) additionally require the audit go/no-go gate — still `PENDING FULL RUN`.

**Stage status (2026-10-12, D31):** all game-side stages are now **implemented and tested** —
`00_thresholds`, `03_game_demand` (D30), and `04_models` + `05_validate` (D31) — with the synthetic fixture
exercising each end-to-end for the planted-ceiling **binding** *and* **null** regimes. Only their real
`--mode full` execution (P4/P5/P6/P7) remains **data-blocked** by the absent `game_tracking_{2023,24,25}.csv`.
The full game-side pipeline (`03 → 04 → 05`) is ready to run as soon as the tracking files are mounted.

## Cycle 2 — security / incentive-exposure models (D38/D39), all `PENDING FULL RUN`

| # | Question | Stage | Notes |
|---|---|---|---|
| P12 | **Real group counts** — how many WR/TE fall in `moved_down` / `high_secure` under the pre-Combine sort key (trend vs level-fallback stratum, per ε), and are both groups above `min_group_n=15`? | `07_security_models` | The whole two-group contrast is underpowered if a group is thin. Reported in `SUMMARY.json::sort.counts` + `group_assignment.csv`. Synthetic fixture only proves the rule. |
| P13 | **B (primary)** — does `moved_down` show a **smaller** Combine→game gap (positive `group_coef`) in WR, TE and pooled, with player-clustered SEs and the ICC-attenuation correction? | `07_security_models` (B) | Needs the full game tracking (top-k efforts) + `FULL` thresholds + the EDP board. Both naive and corrected coefficients are emitted; the corrected group SE comes from the player-clustered bootstrap. |
| P14 | **C** — is there a monotone continuous `log(EDP_pre)` gradient, and what are the fitted gaps at P10/P50/P90 (mean ceiling and within-player)? What is the **overall** top-k exceedance rate (Wilson CI) and by group? | `07_security_models` (C) | Overall exceedance is reported first; >0.25 would trigger the pre-registered utilisation reframe (prereg §6). |
| P15 | **D/E/F/G** — skipper residual (D), appearance model + IPW vs unweighted (E), falsification panels (F), tail quantile coefficients and utilisation P50/P90/P99 (G). | `07_security_models` (D–G) | D and the E/H1 link stay exploratory unless E supports it. G nulls unstable τ estimates rather than forcing them. |
| P16 | **Runtime + peak RAM** of `07_security_models` on the full data (3 scopes × 4 specs + C + E logit/IPW + G quantile bootstrap). | `07_security_models` | The Pi cannot measure it (no full game tracking). The G bootstrap is the cost driver; `security_models.g.bootstrap_reps` is config-driven (60 now) and may be raised on the big machine. |
| P17 | **Analysis-A attenuation appendix on the full board** — already computed (Combine-side complete): `outputs/security/analysis_a_attenuation.csv`. Needs no further full run. | `06_security` | `env_vmax` ICC 0.6425 → corrected speed coef 6.74 (p unchanged, approximate). Kept **appendix-only** by D38. |

**Stage status (2026-10-17, D39):** `07_security_models` is **implemented and tested** end-to-end on the
committed game-side fixture for the **planted**, **null** and **selection** regimes; the real `--mode full`
run (P12–P16) is **data-blocked** by the absent `game_tracking_{2023,24,25}.csv`. Run order:
`01_audit → 02_combine_envelope → 06_security → 00_thresholds → 03_game_demand → 07_security_models →
00b_external_proxies → 04_models → 05_validate`.

---

## DB-decay task (2026-10-25 pivot, D51) — `PENDING FULL RUN`

The active task's game side is blocked by the same absent files; the Combine side is **complete** and already
run (`outputs/audit/db_decay/`). Commands below are the exact invocations; config keys are the ones that
change, and "unblocks" is the decision that cannot be made without the answer. All sample-mode outputs are
stamped `UNVALIDATED SAMPLE OUTPUT` and are **never** reportable (D57).

| # | Question | Exact command / stage | Config key that changes | Unblocks |
|---|---|---|---|---|
| **P18** | **Matched DBs with frame-level tracking** (audit c): how many of the 118 Combine DBs appear in `game_tracking_{2023,24,25}.csv`? (sample: **33**, non-representative) | `python src/01_db_audit.py --mode full --force` → `outputs/audit/db_decay/c_matched_levels.csv`, gate row `matched_dbs_game_tracking` | `db_decay.audit.min_matched_dbs` (40) | The go/no-go gate; whether the study has a population at all |
| **P19** | **Efforts per DB** (high-intensity episodes/player-game and per season) — above the 20 floor for population models and the 60 floor for slopes? | `01_db_audit --mode full` (i) + `python src/03_game_efforts.py --mode full` → effort summary table | `db_decay.audit.min_efforts_per_db` (20), `min_efforts_per_db_slope` (60) | Whether per-player slopes are reportable at all |
| **P20** | **Total efforts** across the DB population | same as P19 → `i_power.csv` / gate row `total_efforts` | `db_decay.audit.min_total_efforts` (4000) | Power; whether a null is informative |
| **P21** | **Per-quarter tracking substitution / survivorship**: does tracking coverage per DB degrade by quarter, and by how much? (audit e; sample `e_tracking` is `PENDING FULL RUN`, and `e_snap_distribution_by_quarter.csv` was unusable until the P27 fix, 2026-10-26) | `01_db_audit --mode full` (e) → `e_tracking_by_quarter.csv`, `e_attrition.csv` | `db_decay.audit.pending_full_run` (drop `e_tracking` once answered) | The Q1-not-Q4 restriction and the inverse-probability weighting in validation |
| **P22** | **Exceedance rate**: how often does in-game peak speed beat the shrunken ceiling, on max-effort episodes? (sample: 0.0% speed on 2,934 units — diagnostic only) | `01_db_audit --mode full` (h) → `h_exceedance_summary.csv` | `db_decay.audit.exceedance_margin_rel` (0.0; margin grid is a sensitivity) | Whether the ceiling is a bound or a scaling reference — the framing of the whole writeup |
| **P23** | **MDE recalibration**: smallest detectable load β with the real DB count, efforts/DB, games/DB and **observed load SD** (audit assumed `load_sd_per10 = 2.0`) | `python src/05_validate.py --mode full` → power block | `db_decay.power.assumed_load_sd_per10`, `assumed_efforts_per_db`, `assumed_games_per_db`, `smallest_effect_of_interest` (0.01, fixed) | Whether "no decay" is an informative null or an underpowered shrug |
| **P24** | **Nearest-receiver / co-location availability**: does full tracking carry opponent rows and the ball on the same play? | `01_db_audit --mode full` (d) → `d_players_per_play.csv`, `d_sample_blocked_paths.csv` | *(no key yet — add `db_decay.game.matchup_covariate` only if the answer is yes)* | Whether matchup demand can be conditioned on, or stays a declared confound (D56) |
| **P25** | **Is full tracking all-22 or combine-class only?** (sample: 0 plays with 22 tracked, 1.16 players/play) | `01_db_audit --mode full` (d), same outputs as P24 | *(no key)* | The `within 5 yd of the ball carrier` condition in the max-effort subset; lateral alignment derivation (T4) |
| **P26** | **Thresholds with `FULL` provenance** (effort speed/accel cuts, sustain times, garbage-time margins, min snaps) | `python src/00_thresholds.py --mode full` → `outputs/thresholds.json` | `db_decay.game.effort_*`, `min_effort_duration_s`, `garbage_time_*`, `min_snaps_per_player`; `db_decay.audit.provenance_thresholds` → `FULL` | Stage 03 (it asserts `FULL`); every number downstream |
| **P27** | ~~**Pre-full-run code fix, not a data question:** `e_snap_distribution_by_quarter.csv` reports `n_db_games = 1` and `snaps_median ≈ 9,592` for CB Q1 — that is a total, not a per-game median, so the per-quarter snap distribution is currently unreadable.~~ **FIXED 2026-10-26 (D58):** the query now groups by (player, game, quarter), so the distribution is over DB-games (CB Q1: 889 DB-games, median 11 snaps, IQR 7–15); covered by `test_snap_distribution_is_per_db_game`. Kept here as a closed item, not a pending question. | `01_db_audit` (e) — no action left | *(none)* | P21's interpretation; any substitution/survivorship claim |

**Gate status (2026-10-26).** A DB-decay full run must not start until: (1) `notes/prereg.md` is **FROZEN**
(it is currently `DRAFT — NOT FROZEN`), (2) the audit go/no-go gate has been re-expressed as "≥1 primary
Combine feature passes ICC" (D53) and passes on full data, and (3) `00_thresholds` has written `FULL`
provenance. Otherwise → `notes/blockers.md`.
