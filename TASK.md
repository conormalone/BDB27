# TASK: Combine fatigue slope vs. in-game intensity decay

> **Core project task spec — binding.** Captured from Conor 2026-10-09. Every role and sub-agent follows it; when this and convenience disagree, this wins.

NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

## Goal
Test whether performance decay across a Combine session (from Combine tracking) predicts decay in high-intensity output across games (from NFL tracking), beyond baseline speed, size and draft position (if provided).

## Environment
You run on a Raspberry Pi with limited RAM. Game tracking here is a ~100 MB SAMPLE; the final run happens on a bigger machine. Produce correct, memory-safe, tested code that runs unchanged on full data. Scripts 01-05 must run end-to-end in `--mode sample` on sample or synthetic data, and every sample output is labeled `UNVALIDATED SAMPLE OUTPUT`. Never interpret, report or tune against sample results.

## Rules
- Never invent data, columns or results. If something is missing or ambiguous, stop and write to /notes/blockers.md.
- Official competition docs and rules are the source of truth; third-party repos are unverified hints. No external data unless permitted.
- Log decisions and reasons in /notes/decisions.md. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, strict sequential gating.

## Engineering
- Scripts take `--mode sample|full` and `--force`, with one code path for both. Paths, thresholds and sizes live in config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit column selection. Process game data per game or player in chunks and write small summary tables; modeling stages read only summaries.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless `--force`.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough matched players, generate a synthetic schema-compliant set with a planted fatigue slope.

## Phases

**0. Setup.** Data is stored on the mounted drive. Summarize rules and limits (writeup and figure caps, notebook requirements, external data) in /notes/rules.md. Record schemas and Combine file size in /notes/schema.md.

**1. Audit (gate: go/no-go).** Evidence, not assumptions:
a. Timing and ordering fields for Combine attempts.
b. Attempts per player per drill, by position group.
c. Does drill order vary across players? (If fixed, fatigue and drill are confounded.)
d. Is the provided distance column reliable? Compare with distance from x/y.
e. Matched Combine-to-NFL players per position group (game side: scripted now, run in full mode only, mark PENDING FULL RUN).
f. Missingness, units, sampling rate, coordinates.
g. Gaps in attempt numbering: share of players affected, by drill and position.
Select ONE position group and ONE family of repeated maximal-effort drills with the best attempts and order variation. Stop if a required timing field is missing or no group qualifies.

**2. Combine features** (run for real if the Combine data fits in RAM).
- Per attempt, from x/y/time: peak speed, peak acceleration, time above 90% of the player's best, distance (effort cost), elapsed session time.
- Keep original attempt numbers and real clock time; never renumber. If explicit attempt IDs are absent, infer attempts from timestamps using a segmentation rule defined in config.
- Any gap in attempt numbering is lost data. Impute load only: add the player's median observed cost for that drill (drill-level median if none). Flag imputed rows. Imputed attempts are not model observations and carry no performance values.
- Cumulative prior load = observed plus imputed efforts since session start (distance and count of maximal efforts).
- Standardize performance within drill and position.
- `first_attempt` = 1 iff the original attempt number is 1 within that drill (not merely the first observed row). Check its collinearity with load (VIF) and report it.
- Frames: flag attempts with gaps inside the peak-speed or peak-acceleration window. Interpolate gaps under 0.5 s only elsewhere. Sanity checks: flag speeds above 12 yd/s, duplicate attempts.
- Rest time: compute from the clock, accounting for imputed attempts. Use it as a covariate or drop it; do not leave it unused.

**3. Combine fatigue model.**
- Baseline: within-player late-minus-early differencing on comparable efforts.
- Main LMM: `perf_z ~ load + load^2 + first_attempt + C(drill) + (1 + load | player)`. Drop `load^2` if pooled load values lack spread (document the criterion in config).
- Convergence fallbacks, logged in decisions.md: (1) uncorrelated random effects `(1|player) + (0+load|player)` with centered, rescaled load and an alternate optimizer; (2) per-player OLS slopes on centered load with empirical Bayes shrinkage, or a Bayesian hierarchical model with weak priors; (3) only if both fail, random intercept only, report population-level results only and skip the Phase 5 link.
- Robustness: refit with observed-only load (no imputed attempts); refit with and without `first_attempt`.
- Diagnostics: residuals, convergence, random-effect distributions. Reliability is model-based: slope variance / (slope variance + mean squared SE of per-player slopes), plus cross-drill-group slope correlation if players ran multiple drills. Permutation test (>= 1000 within-player order shuffles; the effect should vanish). Do not use odd/even split-half; attempts are too few.
- Stop if reliability < 0.2 (threshold fixed in config before running) and report the null as the finding.
- Extract shrunken per-player slopes with intervals. Fit all players at population level; the Phase 5 link requires >= 3 observed attempts per player.

**4. In-game decay** (full data only).
- `00_thresholds.py` writes outputs/thresholds.json (effort cutoffs, minimum snaps). In sample mode provenance is "PLACEHOLDER_SAMPLE"; in full mode it is "FULL". Scripts 04 and 05 assert provenance == "FULL" in full mode. Never set thresholds from the sample. Freeze thresholds before running 05; no tuning against Phase 5 results.
- Per matched player, model high-intensity effort intensity relative to their own early-game baseline against cumulative in-game distance, snaps in drive and time since rest. Controls: play type, role, down/distance, score margin. Apply the minimum-snaps rule.
- Extract shrunken per-player decay slopes. Report the same model-based reliability and stop-threshold as Phase 3.

**5. Link and validate** (full data only).
- Primary test: regress game decay slope on Combine fatigue slope, controlling for baseline speed, height/weight and draft position (if provided).
- Account for error in both slopes: inverse-variance weighting for the outcome, plus a reliability-corrected effect for the predictor (regression calibration using posterior SEs, or a joint bivariate hierarchical model), reported alongside the naive regression.
- Player-grouped cross-validation; held-out delta R^2 with bootstrap CIs.
- Placebo: Combine slope should not predict early-game output. Sensitivity: alternate thresholds, metrics, excluding low-snap players.
- State the selection limitation (only players who reached NFL games have game data).

**6. Deliverables.** Public Kaggle notebook and writeup within official limits (placeholders for results until the full run), max 5 figures. Writeup sections: problem, audit, method, reliability, link, implications, limitations. /notes/limitations.md lists every threat and mitigation, including the lost-attempts assumption, selection bias and slope measurement error.

## Tests before handoff
1. Schema assertions on columns, dtypes and units; fail loudly on mismatch.
2. Synthetic recovery: simulated players with known slopes, real attempt timings and randomly deleted attempts (with imputed load). Both the Combine and game models must recover the slopes. Include a fallback-path test.
3. Edge cases: one-attempt players, numbering gaps, missing frames, ties, constant performance, no game data.
4. Scale: run on the sample, then on the sample concatenated several times with renamed IDs. Check roughly linear memory growth, extrapolate to full size in /notes/scale.md and flag stages likely to exceed RAM.
5. Determinism: same seed gives identical outputs.

## Handoff
- README_RUN.md: clone, environment setup, data download, ordered commands, expected runtime and RAM per stage.
- Pinned requirements.txt that works on ARM and x86.
- /notes/pending_full_run.md: questions only full data can answer, with the script for each.
- /notes/assumptions.md: unverified assumptions about full data, each with a runtime assertion.
- .gitignore data; commit only code, configs, notes and synthetic fixtures.

## Stop and write to blockers.md if
A required timing or order field is missing; no position group qualifies; reliability is below threshold; thresholds provenance is not "FULL" in a full run; or any instruction conflicts with competition rules.

## Layout and run order
/data/raw /data/interim /src /notebooks /notes /outputs/figures
01_audit -> 02_features -> 03_combine_model -> 00_thresholds -> 04_game_features -> 05_link
