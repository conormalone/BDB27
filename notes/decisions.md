# Decisions log — BDB27

Append-only. Each entry: what was decided, why, and the evidence/constraint behind it. Binding spec =
`TASK.md`. **Prior (combine-fatigue) decisions D1–D25 are archived at git tag
`archive/combine-fatigue-v1`** (HEAD `fdc794c`); this log restarts fresh for the ceiling-binding task.

---

## D26 — Cycle 0: pivot to the ceiling-binding task; reset analysis, keep infra · 2026-10-11
**Decision.** Pivot the project to **"Does the Combine ceiling bind on max-effort routes? (WR primary, TE
replication)"** (`TASK.md`, superseding the combine-fatigue spec). This cycle = **repo restructure + Phase 0
setup only**; the 01–05 pipeline is **not** implemented yet.
**Kept (infra):** `src/common.py` (config loader + DuckDB/Parquet helpers), `src/README.md`,
`requirements.txt`, `.gitignore`, `research/` (prior-work scan), `data/` (symlinks + README), `notes/`,
`README.md`, `README_RUN.md`, plus the git history and the `archive/combine-fatigue-v1` tag.
**Removed (`git rm`):** the combine-fatigue analysis — `src/{00_thresholds,01_audit,02_features,
03_combine_model,03b_metric_panel,03d_warmup_phenotype,04_game_features,05_link}.py`, `src/tests/`,
`notebooks/*` (old run/build notebooks + prototype), `tools/`, `specs/`. Analysis artefacts under
`outputs/` deleted on disk (gitignored).
**Why.** The new task shares the data and the engineering discipline but not the analysis; keeping the old
scripts would confuse the run order and imply unimplemented stages.
**Evidence.** `TASK.md` (new binding spec); the archive tag preserves the prior work verbatim.

---

## D27 — Cycle 1: Phase 1 audit (go/no-go gate), sample mode · 2026-10-11
**Decision.** Implemented and ran **Phase 1 — Audit (`src/01_audit.py`)**, one code path for
`--mode sample|full` + `--force`, config-driven, Parquet+DuckDB (no full CSV in pandas), run-logged to
`outputs/run_log.csv`. Game-side items that need the full game-tracking files (c/d/e/h/i) are **scripted**
and emit **`PENDING FULL RUN`** placeholders in sample mode. Built a committed, schema-compliant
**synthetic fixture with a planted binding ceiling** (`src/synthetic_fixture.py`, `fixtures/synthetic/`)
to exercise those paths; fixture outputs are stamped `SYNTHETIC FIXTURE (not a finding)` and are never
interpreted. Added `src/tests/test_audit.py` (5 suites, all passing: schemas/units, sample outputs,
fixture + planted-ceiling recovery, determinism).

**Findings (UNVALIDATED SAMPLE OUTPUT; Combine + `player_play.csv` are complete, tracking is not).**
- **(a)** WR/TE Combine drills = `FORTY_YARD_DASH` (straight-line), `THREE_CONE_DRILL` + `SHORT_SHUTTLE`
  (cutting), and position skill blocks `SKILL_DRILLS_WR`/`SKILL_DRILLS_TE`. WR: 94/24/29 dash/cone/shuttle
  players; TE: 34/19/21.
- **(b)** Attempts/player are **thin**: 40-yd dash median **2** (WR 82/94 and TE 32/34 have ≥2); cutting
  drills mostly **1** attempt (WR cone 5/24, shuttle 6/29 have ≥2). Combine classes **2023/2024/2025**.
- **(c)** Match preview from the **complete `player_play.csv`**: matched **WR 106/107, TE 38/42**; route snaps
  (via `route_ran` NOT NULL) median **WR 261, TE 184**. Sample *tracking* prefix matches only WR 39/TE 11
  (non-representative). Authoritative frame-level match = `PENDING FULL RUN`.
- **(d)** **Route type IS provided** — `player_play.route_ran` is **categorical** (GO/HITCH/CROSS/IN/OUT/POST/
  CORNER/SLANT/FLAT/SCREEN/WHEEL/ANGLE; NULL = no route), *not* a boolean flag (config `route_snap_col`
  semantics corrected — see `schema.md`). Route **depth** provided via `pass_length` (integer yards).
  Trajectory-derived depth/break angle, 22-player tracking, and ball location = `PENDING FULL RUN`;
  time-to-throw available as a **proxy** (`dropback_duration`).
- **(e)** Route snaps **53,308**; **targeted 9,842 (18.5%)**, **non-targeted 43,466 (81.5%)** → plays **do**
  cover non-targeted routes (WR 81.2%, TE 83.3%). 3 seasons (2023/24/25), 1,002 games, 114,954 plays.
- **(f)** Provided **`dis` is unreliable** (confirmed): median under-count of the x/y path **8.9% on the
  40-yd dash** (80% of attempts beyond the 5% tolerance), **39–66% on cutting/skill drills**. Provided **`a`**
  agrees with `ds/dt` on the straight-line dash (r≈0.98) but not on cutting drills (r≈0) — expected, since
  the vendor `a` includes the lateral/centripetal component; recompute kinematics from `x,y`.
- **(g) Test-retest ICC** (repeat source = distinct `event_id` attempts of the same `(nfl_id, drill_type)`),
  **40-yd dash** (375 players with ≥2 attempts): `peak_speed` **0.975**, provided-`s` peak 0.986,
  `speed_at_20yd` 0.969, `min_speed_after_peak` 0.966, `speed_at_10yd` 0.883, `env_vmax` 0.643,
  `speed_at_t2.0s` 0.573, `max_decel` 0.413, `time_to_20yd` 0.403, `env_a0` 0.337, `time_to_10yd` 0.216,
  `env_tau` 0.097. **Cutting-drill features are unreliable** (ICC ≈ 0.1–0.3, several negative) with thin
  repeats (cone 38, shuttle 42 players ≥2). Some features are **explicitly `UNESTIMABLE`** (e.g.
  `SHORT_SHUTTLE speed_at_20yd`/`time_to_20yd`: zero players with ≥2 attempts, path too short) — recorded,
  never passed as reliable.
- **(h)/(i)** `PENDING FULL RUN` in sample mode. The synthetic **binding** fixture recovers an exceedance
  rate of **0.109** vs the planted **0.10**; the **null** fixture gives **1.0** — the game-side code path
  is exercised and the planted ceiling is recovered (fixture only; not a finding).

**Go/no-go:** **CONDITIONAL — thresholds not frozen.** `audit.min_matched_wr`, `audit.min_route_snaps_per_player`,
`audit.min_icc` are `null` (`prereg.md` skeleton); the sample cannot exercise the game-side gate. No required
Combine/game field is missing. A `--mode full` run remains **blocked** until `prereg.md` is frozen (itself a
blocker per `TASK.md`). Advisory (preview from the complete `player_play.csv`): matched WR 106 / TE 38 and
median route snaps ≫ any plausible minimum look sufficient.

**Guidance for Phase 2 feature selection (ICC first):** the reliable candidates are
`peak_speed`, `speed_at_10yd`/`speed_at_20yd`, and `env_vmax`; `env_a0`/`env_tau` and raw timing splits are
weak; cutting-drill cut metrics are not reliable enough to headline.

**Evidence.** `outputs/audit/*` (+ `SUMMARY.md`) and `outputs/audit/synthetic/*`, `outputs/run_log.csv`;
`src/01_audit.py`, `src/synthetic_fixture.py`, `src/tests/test_audit.py`; `fixtures/synthetic/{binding,null}/`.

---

## D28 — Cycle 2: freeze the pre-registration (Conor-approved values) · 2026-10-12
**Decision.** Filled every `TBD`/`null` in `notes/prereg.md` (now **FROZEN**) and `config.yaml` with the
values Conor approved, and implemented a runtime **freeze gate** (`src/gate.py`). Choices + rationale:

| Key | Value | Rationale |
|---|---|---|
| `models.quantiles` | `[0.5, 0.75, 0.9, 0.99]` | Conor-approved τ ladder; median reference + three upper tails to test tail-strengthening (prereg §1). |
| `game.top_k_efforts` / `game.route_snap_count` | `5` / `100` | approved; top-5 efforts from a fixed 100-snap denominator ⇒ comparable tail sampling (prereg §2). |
| `envelope.max_features` / `selection_rule` | `3` / `[icc, vif]` | approved ≤3 features, ICC then VIF (prereg §3). |
| `envelope.icc_min` = `audit.min_icc` | `0.5` | approved reliability floor; audit ICC splits the pool cleanly (`.97/.97/.88/.64` retained vs `.34/.10` dropped). |
| `audit.min_matched_wr` / `min_route_snaps_per_player` | `60` / `100` | approved gate minima with headroom below the audit preview (WR 106/107; median route snaps 261/184) (prereg §5). |
| `audit.exceedance_margin_rel` / `exceedance_reframe_rate` | `0.0` / `0.25` | approved reframe rule ">25% speed exceedance → envelope not binding → reframe as utilization": exceedance = strictly beat the ceiling (margin 0; same yd/s units), reframe if the overall rate exceeds 25% (prereg §6). |
| `envelope.vif_max` | `5.0` | standard deflaible VIF ceiling; used as the redundancy-pruning trigger (prereg §3). |
| `envelope.split_distances_yd` | `[5, 10, 20]` | short/medium/long splits across the dash; the 20-yd split is the reliable one (ICC 0.97). |
| `envelope.speed_at_times_s` | `[1.0, 2.0]` | model-free speed-at-time samples from movement onset (kept, but ICC .35/.57 → may be dropped). |
| `envelope.cut_metrics` | `[entry_speed, exit_speed, cod_deficit]` | change-of-direction metrics for the cutting drills (cone/shuttle); not headlined (audit g: ICC ≈0.1–0.3). |
| `envelope.min_attempts_per_player` | `1` | hierarchical shrinkage is designed to stabilise thin ceilings (dash median 2 attempts); requiring ≥2 would discard ~13% of players and bias toward two-attempt players (prereg §8). |
| `game.thresholds.high_speed_percentile` / `accel_percentile` | `0.90` / `0.90` | effort cut-offs at the 90th percentile — "max-effort" tail, matching the tail question (prereg §8). |
| `models.bootstrap_reps` | `1000` | standard; player-clustered bootstrap SE for the quantile slopes (prereg §8). |
| `models.controls_without_draft` / `controls_with_draft` | `[position, age, height, weight, bmi]` / `+ draft_position` | approved controls; draft excluded/included per prereg; "undrafted" is an explicit level (limitations §10). |
| `models.fallbacks` | `[refit_increased_max_iter, adjacent_tau_report, pool_positions, report_null]` | ordered, pre-declared stability chain mirroring the prior project's "report a null rather than force a result" discipline (prereg §7). |
| `validate.cv_folds` / `perm_reps` | `5` / `1000` | player-grouped CV folds and permutation reps (prereg §8). |
| `validate.measurement_error_correction` | `icc_attenuation` | divide the estimated slope by the feature reliability — the standard attenuation correction given the audit ICC (limitations §3, §6). |
| `validate.sensitivity` | `[alt_quantiles, drop_thin_repeats, exclude_optouts, margin_grid]` | pre-declared sensitivity analyses (tail ladder alternatives, thin-repeat players, Combine opt-outs, exceedance-margin grid). |
| `game.sample_placeholders.{high_speed_threshold_yds, accel_threshold_yds2}` | `8.0` / `3.0` | **a-priori** placeholders only (never derived from the sample); full runs must overwrite with `FULL` provenance. |

**Gate.** Added `src/gate.py`: a `--mode full` run of a stage that consumes pre-registered values must find
`notes/prereg.md` `FROZEN` **and** all required keys non-null, else it stops and points at
`notes/blockers.md`. `01_audit` (full) and `02_combine_envelope` (full) call `gate.enforce(...)`. Verified:
the freeze gate **passes** with the frozen prereg and **blocks** an unfrozen skeleton (tests).
**Evidence.** `notes/prereg.md` (FROZEN), `config.yaml`, `src/gate.py`, `src/tests/test_gate.py`.

---


## D29 — Cycle 2b: finish the freeze gate + Phase 2 Combine envelope · 2026-10-12
**Decision.** Resumed the **stranded Cycle-2 run** and completed it in one session (single-session
PM+Coder+Reviewer):

1. **Freeze gate wired + tested.** `src/gate.py` (freeze-only for Combine-side; freeze **+** audit go/no-go for
game-side) is now called by `01_audit` (full) and `02_combine_envelope` (full). `src/tests/test_gate.py`
(8 suites) proves it **passes** with the frozen prereg and **blocks** an unfrozen/missing prereg, a null
required key, and a non-passing audit decision, while sample mode is never gated.
2. **Phase 2 built + run for real.** New `src/02_combine_envelope.py` + shared `src/combine_features.py`:
per-attempt kinematics recomputed from `x,y`; envelope `v(t)=vmax(1-e^{-t/tau})` on `FORTY_YARD_DASH`
(`a0=vmax/tau`); model-free split / speed-at-time features; cut metrics (`entry_speed`, `exit_speed`,
`cod_deficit`) for the cutting drills; two-stage empirical-Bayes **hierarchical shrinkage**; **<=3 features by
ICC then VIF**; per-player table **with SEs**. Config-driven, DuckDB-only (no pandas), run-logged,
checkpointed. **Findings (FULL; Combine file complete):** 418 dash players / 793 attempts (43 single-attempt);
positions {WR 94, TE 34, DB 102, DL 93, OL 95}. **Retained = `peak_speed`, `speed_at_20yd`, `env_vmax`** — the
pre-declared set (prereg §3). ICC reproduces the audit exactly (`peak_speed` .9746, `speed_at_20yd` .9694,
`speed_at_10yd` .8832, `env_vmax` .6425 retained; `env_tau` .0968, `env_a0` .3373 dropped). Residual VIF>5
among retained: `peak_speed`, `speed_at_20yd` (flagged). WR shrunk ceilings: vmax median **11.67**
(10.79–13.21), tau 1.36, a0 8.51; TE: vmax median **10.78**, tau 1.31.
3. **Fit bounds kept audit-consistent.** The envelope bounds stay `vmax∈[0,30]`, `tau∈[0.05,10]` — the bounds
behind the audit evidence cited in prereg §4 — so the freeze-consistent ICC/selection is reproduced. The wide
`tau` bound admits a few noisy/thin attempts whose 2-parameter fit extrapolates implausibly (raw ceiling up to
~18 yd/s): these are **flagged** (`env_vmax_implausible`, raw ceiling > `envelope.flag_vmax_over_peak`(1.5)x
mean peak → **2 players**) rather than silently dropped (prereg §3.4 pattern); see `notes/limitations.md`.
*Rejected alternative:* a physically-tighter fit bound tames the outliers but **flips the VIF pruning to drop
`env_vmax`**, contradicting the frozen expected set — a HARKing risk — so the pre-registered configuration was
kept. Also refactored `01_audit._features_for_attempt` to use `combine_features.fit_envelope` (one config-driven
fitter, removing hardcoded bounds). **No frozen prereg value changed.**

**Evidence.** `src/{gate,combine_features,02_combine_envelope}.py`, `src/tests/{test_gate,test_envelope}.py`
(8+9 suites pass; audit 5/5 regression), `outputs/envelope/*` + `SUMMARY.md`, `outputs/run_log.csv`, `config.yaml`.
