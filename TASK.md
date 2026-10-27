# TASK: Does situational leverage change how much of the ceiling receivers use on max-effort routes? (WR primary, TE replication)

NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

## Goal
Use each receiver's Combine-derived ceiling as a personal baseline and test whether effort on max-effort routes shifts in high-leverage moments (close games, late, high-stakes downs and field positions).

Questions:
1. **Primary (confirmatory):** within a player, is max-effort route speed higher (or lower) when pre-snap leverage is high, after controls for route, depth, game state and accumulated load?
2. **Secondary (headroom):** is the leverage effect larger for receivers who usually operate further below their ceiling? This is the main use of the ceiling: an effort effect should have room to appear only where there is headroom.
3. **Exploratory:** dose-response across leverage bins, and WR vs. TE.

Claims are limited to players who reached the NFL (only draftees have game tracking). Leverage is an observable situation, not a measured psychological state. Interpret any effect as a situation-related change in max-effort speed that may reflect effort, tactical demands or both. Never claim to have measured psychological load, pressure or intent.

## Environment
You run on a Raspberry Pi with limited RAM. Game tracking here is a ~100 MB SAMPLE; the final run happens on a bigger machine. Produce correct, memory-safe, tested code that runs unchanged on full data. Scripts 01-05 must run end-to-end in `--mode sample` on sample or synthetic data. Label every sample output `UNVALIDATED SAMPLE OUTPUT`. Never interpret, report or tune against sample results.

## Rules
- Never invent data, columns or results. If something is missing or ambiguous, stop and write to /notes/blockers.md.
- Official competition docs and rules are the source of truth; third-party repos are unverified hints. No external data or external win-probability models unless the rules permit them.
- Log decisions and reasons in /notes/decisions.md. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, strict sequential gating.
- Every design choice (leverage definition and bin cutoffs, max-effort definition, outcome, controls, matching rules, minimum efforts per player, smallest effect of interest, equivalence margin, reliability thresholds) is logged in /notes/decisions.md. **There is no pre-registration document in this project**; a missing document is never a blocker. A real blocker is missing required data or fields.

## Engineering
- Scripts take `--mode sample|full` and `--force`, one code path for both. Paths, thresholds and sizes live in config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit column selection. Process game data per game or player in chunks and write small per-effort summary tables; modeling stages read only summaries.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless `--force`.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough matched receivers, generate a synthetic schema-compliant set with a planted ceiling and a planted leverage effect.

## Phases

**0. Setup.** data is on local storage. Summarize rules and limits (writeup and figure caps, notebook requirements, external data) in /notes/rules.md. Record schemas and file sizes in /notes/schema.md.

**1. Audit (gate: go/no-go).** Evidence, not assumptions:
a. Which WR and TE drills exist in the Combine tracking; which are straight-line.
b. Attempts per player per drill; Combine class years covered.
c. Matched Combine-to-NFL players per position; games, plays and route snaps per matched receiver.
d. Leverage inputs in the game data: score, game clock, quarter, down, distance, yard line, possession, timeouts.
   - Is a provided win probability or expected points available? If not, can leverage be derived from fields in the data alone? If it would need an external model, check the rules before using one; otherwise stop and report.
e. Which weeks and season phases are covered (regular season only? any playoffs?).
f. Route type or depth provided, or derivable; ball location, time of throw; whether all 22 players are tracked per play; which plays are included (non-targeted routes too?).
g. Counts of max-effort efforts per receiver in each leverage bin. Report how many receivers have enough efforts in both high and low leverage for within-player comparison.
h. Reliability of Combine ceiling features (ICC) and the in-game exceedance rate.
i. Correlation between leverage, game time and cumulative load (high-leverage moments cluster late in games; this must be separable from fatigue).
j. Power calculation: smallest within-player leverage effect detectable given counts in g.

Game-side items needing full data: script now, mark PENDING FULL RUN.
Go/no-go: stop if a required field is missing, leverage cannot be defined without disallowed external data, too few receivers have efforts in both leverage extremes, leverage is nearly collinear with game time after load is accounted for, Combine features are unreliable, or the power calculation shows only implausibly large effects are detectable.

2. Combine ceiling (run for real if the Combine data fits in RAM).
- Smooth the speed curve and fit v(t) = vmax * (1 - exp(-t/tau)) per attempt on straight-line drills; derive a0 = vmax/tau. Do not differentiate raw positions for acceleration.
- Pool attempts per player with hierarchical shrinkage. Use shrunken estimates with standard errors everywhere downstream; never a best attempt.
- Keep at most three features, chosen by ICC and VIF fixed in notes/decisions.md. Keep original attempt numbers and real clock time.
- Sanity checks: flag speeds above 12 yd/s, duplicate attempts, gaps inside the peak window. Interpolate gaps under 0.5 s only outside peak windows.
- Output a per-player feature table with uncertainty (SE).

3. Game efforts and leverage (full data only for final; scripted and unit-tested now).
- 00_thresholds.py writes outputs/thresholds.json (effort cutoffs, leverage bin cutoffs, minimum efforts, garbage-time rule). Provenance is "PLACEHOLDER_SAMPLE" in sample mode and "FULL" in full mode; scripts 03 to 05 assert provenance == "FULL" in full mode. Never set thresholds from the sample. Freeze before Phase 4.
- Identify route snaps (WR; TE restricted to pass-route snaps; state that TE blocking exertion is invisible to the chips). Derive route family, and route depth **geometrically as depth at the break** (D61): rotate into the play frame, smooth with the existing centred median, find the first sustained ≥45° turn over 0.3 s after a ≥0.8 s stem, and take downfield displacement at that frame. A route with no detectable break keeps `break_found = False` and a **missing** depth — never impute it, and never use `pass_length` or start-to-end displacement as depth.
- Leverage (pre-snap information only): use the provided win probability if available, defined as the absolute change in win probability that the play outcome could produce, or a leverage index derived from score margin, time remaining, down, distance and field position with the formula fixed in notes/decisions.md. Bins (for example low, medium, high) set from counts, logged in notes/decisions.md. Never use post-snap information (play result, target, completion).
- Max-effort definition: fixed before looking at outcomes and independent of leverage and of play result where possible (for example, routes with a minimum depth and duration, not only targeted routes). If the definition requires the ball in the air or a target, note that this conditions on outcomes and run a sensitivity version without it.
- Per effort: max speed, peak acceleration, route family, depth, time to throw if available, defender distance if available, cumulative load before the effort (distance and count of high-intensity efforts so far in the game), game clock, quarter, score margin, down and distance, field position.
- Outcome: log of max speed on the effort. Secondary outcome: effort fraction (speed / shrunken vmax). Keep the ceiling out of the primary outcome so ceiling error cannot create an effect; the within-player design absorbs the ceiling itself.
- Write one row per effort; modeling stages read only these tables.

4. Models.
- Primary (within-player): log(max_speed) ~ leverage + route family + depth + time_to_throw (if available) + cumulative_load + quarter + score margin + down and distance + (1 + leverage | player). **Depth is the D61 break depth (continuous, centred) plus a `break_found` indicator**; it is a control — report it, never interpret it. Estimand: the population-level coefficient on leverage. Player-clustered SEs. **Power limitation (stated, not a gate):** at the configured 40 efforts/player the achieved analytic MDE is 0.0443 log-speed (D60), above the 0.03 smallest effect of interest; the report must state the achieved MDE alongside every interval. The smallest effect of interest stays 0.03 and no minimum-efforts-per-player requirement is imposed. Convergence fallbacks, logged in decisions.md: (1) uncorrelated random effects (1|player) + (0+leverage|player); (2) player fixed effects; (3) random intercept only.
- Matched design (sensitivity): within each player, coarsened exact matching of high- and low-leverage efforts on route family, depth bin, quarter and load bin; compare matched pairs with a clustered bootstrap.
- Headroom moderation (secondary): add leverage x baseline utilization, where baseline utilization = the player's median effort fraction on low-leverage max-effort routes. Estimate it in an independent half of the player's low-leverage efforts, not the ones used in the comparison, to avoid regression-to-the-mean artifacts. Also test leverage x ceiling (vmax, tau). Handle error in ceiling and utilization with posterior SEs and report corrected and naive estimates.
- Ceiling bound check: verify that predicted effort fraction under high leverage does not exceed the plausible ceiling; report exceedance rates by leverage bin.
- Dose-response: estimate the effect across leverage bins and report whether it is monotone.
- Repeat for TE as replication; label exploratory if the sample is small.
- Per-player leverage slopes: report only if model-based reliability (slope variance / (slope variance + mean squared SE)) exceeds the reliability threshold fixed in notes/decisions.md. Do not use odd/even split-half.

5. Validation.
- Leverage permutation: shuffle leverage labels within player-game (and within route family); the effect should vanish.
- Time-and-load confound check: refit with leverage orthogonalized to game time and cumulative load; report whether the effect survives. High leverage clusters late in games, so a leverage effect that disappears here is a fatigue or game-clock effect.
- Placebos: leverage should not predict route depth or other pre-snap features after matching; the effect should be weaker or absent on non-max-effort plays.
- Tactical-demand checks: include defender distance and time to throw where available; run a version restricted to plays with comparable time to throw. State that defensive adjustments and offensive play-calling in high-leverage situations cannot be fully separated from receiver effort.
- Garbage time: exclude it per the rule in notes/decisions.md; report results with and without.
- Sensitivity: leverage definition and bin cutoffs, effort threshold, matching tolerances, minimum efforts per player, excluding the final two minutes of each half, alternate ceiling features.
- State final sample sizes and the smallest detectable effect. Run an equivalence test against the equivalence margin fixed in notes/decisions.md; interpret a null as informative only if the interval excludes the smallest effect of interest.

6. Deliverables. Public Kaggle notebook and writeup within official limits (placeholders for results until the full run), max 5 figures. Sections: problem, data audit, ceiling model, leverage definition, results, headroom, limitations. /notes/limitations.md lists every threat and mitigation (tactical demand in high-leverage moments, confounding with fatigue and game time, conditioning on max-effort definitions, rare high-leverage efforts, draftee-only selection, short follow-up for the 2025 class, no direct measure of psychological state).

## Tests before handoff
1. Schema assertions on columns, dtypes and units; fail loudly on mismatch.
2. Synthetic recovery: simulated receivers with a planted ceiling, planted leverage effect and planted headroom moderation. The pipeline must recover them. Include a null world (the pipeline must not find an effect) and a confounded world where leverage is correlated with game time and load and has no true effect (the orthogonalized model must remove the spurious result).
3. Edge cases: receivers with efforts in only one leverage bin, one-attempt players, missing frames, ties, no game data, missing win probability.
4. Scale: run on the sample, then on the sample concatenated several times with renamed IDs. Check roughly linear memory growth, extrapolate to full size in /notes/scale.md, and flag stages likely to exceed RAM.
5. Determinism: same seed gives identical outputs.

## Handoff
- README_RUN.md: clone, environment setup, data download, ordered commands, expected runtime and RAM per stage.
- Pinned requirements.txt that works on ARM and x86.
- /notes/pending_full_run.md: questions only full data can answer, with the script for each.
- /notes/assumptions.md: unverified assumptions about full data, each with a runtime assertion.
- .gitignore data; commit only code, configs, notes and synthetic fixtures.

## Stop and write to blockers.md if
A required Combine or game field is missing; leverage cannot be defined from permitted data; too few receivers have efforts in both leverage extremes; leverage is nearly collinear with game time and load; Combine features are unreliable; thresholds provenance is not "FULL" in a full run; or any instruction conflicts with competition rules.

## Layout and run order
/data/raw /data/interim /src /notebooks /notes /outputs/figures
01_audit -> 02_combine_ceiling -> 00_thresholds -> 03_game_efforts -> 04_models -> 05_validate
