# TASK: Combine acceleration ceiling and pass-rush production in defensive linemen (DL)

> **Provenance.** Supplied by Conor in four consecutive messages, 2026-10-30 (Telegram). Transcribed
> verbatim; only a missing line break between "…synthetic fixtures." and "## Stop and write to
> blockers.md if" was repaired.
>
> **Supersedes** the 2026-10-27 leverage × ceiling spec (WR/TE), archived at
> `notes/archive-tasks/TASK-leverage-ceiling-2026-10-27.md`, together with its prereg and
> prereg-deviations.
>
> **Pre-registration is REINSTATED by this spec.** The 2026-10-27 process correction retired the
> prereg stage (D61) for the leverage task. This spec requires `/notes/prereg.md` before the first
> full-data run. The newer, explicit instruction wins: the prereg gate is live again. It is *not* a
> blocker against sample-mode work — only against `--mode full`.

NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

## Goal
Use each defensive lineman's Combine-derived acceleration ceiling to explain and predict in-game pass-rush outcomes, then test whether measuring in-game load relative to that ceiling predicts late-drive and late-game decline better than absolute load.

Questions:
1. **Primary (confirmatory):** do Combine acceleration features predict time to pressure on pass-rush snaps, beyond position, size and play context?
2. **Primary (confirmatory, second):** does ceiling-relative cumulative load predict later pass-rush outcomes better than absolute cumulative load, out of sample, with equal tuning freedom?
3. **Secondary:** is there a population-level decline in pass-rush outcomes with accumulated load under the better definition, and does a higher ceiling moderate it?
4. **Exploratory:** how much of the Combine effect on pressure runs through get-off (descriptive mediation only)?

Claims are limited to DL who reached the NFL (only draftees have game tracking). Interpret declines as in-game drops in rush output under the stated controls, not proven physiological fatigue. A within-session Combine fatigue analysis found no signal; do not assume the in-game result either way.

## Environment
You run on a Raspberry Pi with limited RAM. Game tracking here is a ~100 MB SAMPLE; the final run happens on a bigger machine. Produce correct, memory-safe, tested code that runs unchanged on full data. Scripts 01-05 must run end-to-end in `--mode sample` on sample or synthetic data. Label every sample output `UNVALIDATED SAMPLE OUTPUT`. Never interpret, report or tune against sample results.

## Rules
- Never invent data, columns or results. If something is missing or ambiguous, stop and write to /notes/blockers.md.
- Official competition docs and rules are the source of truth; third-party repos are unverified hints. No external data unless permitted.
- Column names below come from a data dictionary and must be verified against the actual files; log mismatches in /notes/schema.md.
- Log decisions and reasons in /notes/decisions.md. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, strict sequential gating.
- Before the first full-data run, write /notes/prereg.md fixing: Combine features, outcome definitions, censoring rule, both load definitions and tuning procedure, controls, minimum snaps per player, comparison metric, smallest effect of interest, reliability thresholds. Do not change it after seeing full-data results; log any deviation as exploratory.

## Variables to expect (verify in Phase 1)
Defender-side: `player_get_off` (seconds from snap to crossing the line of scrimmage), `time_to_pressure`, `time_to_qb_hurry`, `quick_pressure` (1.0 if pressure within 2.50 s of snap; may be null), `sack`, `blitzing`, `tackle`, `assist`, `tackle_for_loss`, `caused_forced_fumble`, `recovered_fumble`, `coverage_assignment`, `coverage_assignment_at_snap`.
Offensive-lineman-side (for context): `dropback_duration` (snap to release, sack or pressure), `pressure_allowed`, `time_to_pressure_allowed`, `peak_pressure_probability_allowed`, `pass_rushers_encountered`, `unblocked_pressure` (may be null).
Also expected: rusher-blocker assignments.

## Engineering
- Scripts take `--mode sample|full` and `--force`, one code path for both. Paths, thresholds and sizes live in config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit column selection. Process game data per game or player in chunks and write small per-snap summary tables; modeling stages read only summaries.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless `--force`.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough matched DL, generate a synthetic schema-compliant set with a planted ceiling, planted pressure effect and planted decay.

## Phases

**0. Setup.** data is one device. Summarize rules and limits (writeup and figure caps, notebook requirements, external data) in /notes/rules.md. Record schemas and file sizes in /notes/schema.md.

**1. Audit (gate: go/no-go).** Evidence, not assumptions:
a. Which DL drills exist in the Combine tracking (straight-line, shuttle, cone, others). Combine class years covered; attempts per player per drill.
b. Matched Combine-to-NFL DL; pass-rush snaps, games and drives per matched rusher; split of interior vs. edge (derive from alignment if not labeled).
c. **Snap coverage:** does the game data contain every pass-rush snap for each matched rusher, or only a sample of plays? For each rusher-game, report the share of the rusher's team defensive snaps present. Load and rest calculations require complete sequences.
d. **Null semantics:** for `quick_pressure`, `unblocked_pressure`, `time_to_pressure`, `time_to_qb_hurry`, `sack`, determine what null means (no event, not applicable, not charged to this player). Report null rates per player and per play type. Do not treat null as zero without evidence.
e. **Pressure label provenance:** read the documentation for how pressure, hurry and `peak_pressure_probability_allowed` are produced. Record whether any label is derived from tracking features of the rusher (if so, relating Combine kinematics to those labels is partly circular; flag it).
f. Alignment and pre-snap depth available or derivable; blitz flag; whether LBs and DBs on blitzes are included (primary group is DL only).
g. Rusher-blocker assignments: coverage, number of blockers assigned per rusher, stunts and twists, how assignments are labeled.
h. Is the provided Combine distance and acceleration reliable? Compare with values derived from x/y; check sampling rate, units, coordinates and Combine vs. game processing differences.
i. Test-retest reliability (ICC) of candidate Combine features: a0, tau, speed at 1.0 and 2.0 s, 10-yard split, peak acceleration, shuttle and cone mechanics.
j. Correlation between candidate relative and absolute load measures; if above the config threshold, the comparison has little power.
k. Power calculation: smallest hazard ratio for the Combine feature, and smallest difference in out-of-sample fit between load definitions, that are detectable.
Game-side items needing full data: script now, mark PENDING FULL RUN.
Go/no-go: stop if DL are absent from the Combine tracking, a required field is missing, matched DL or pass-rush snaps per rusher fall below config minima, snap coverage is too partial for load calculations (Part 2 is then dropped and logged; Part 1 may continue), null semantics cannot be resolved, key Combine features have ICC below the config threshold, or power is only adequate for implausibly large effects.

**2. Combine ceiling** (run for real if the Combine data fits in RAM).
- Smooth the speed curve and fit v(t) = vmax * (1 - exp(-t/tau)) per attempt on straight-line drills; derive a0 = vmax/tau. Do not differentiate raw positions for acceleration.
- Key features (choose at most three by ICC and VIF, fixed in prereg): a0, speed at 1.0 s (and 2.0 s), 10-yard split. For lateral ability use shuttle and cone mechanics (entry speed, braking distance, exit speed) as exploratory features.
- The fitted curve gives the expected speed at any elapsed time t, which is what rush-window comparisons need.
- Pool attempts per player with hierarchical shrinkage. Use shrunken estimates with standard errors everywhere downstream; never a best attempt. vmax and a0 are strongly negatively correlated within players; report it.
- Keep original attempt numbers and real clock time. Sanity checks: flag speeds above 12 yd/s, duplicate attempts, gaps inside the peak window. Interpolate gaps under 0.5 s only outside peak windows.
- Note: Combine runs start on the athlete's own initiative and get-off starts on the snap, so reaction time cannot be derived from the Combine. The ceiling captures the acceleration component only.
- Output a per-player feature table with uncertainty (SE).

**3. Game snaps and load** (full data only for final; scripted and unit-tested now).
- `00_thresholds.py` writes outputs/thresholds.json (effort cutoffs, load thresholds, minimum snaps). Provenance is "PLACEHOLDER_SAMPLE" in sample mode and "FULL" in full mode; scripts 03 to 05 assert provenance == "FULL" in full mode. Never set thresholds from the sample. Freeze before Phase 4.
- Primary snap set: pass-rush snaps by DL (non-blitz), with the rusher in a rush assignment. Blitzers and non-DL are an exploratory stratum.
- Per snap: `player_get_off`, pre-snap depth and alignment (edge vs. interior), assigned blocker(s) and number of blockers, blitz flag, stunt indicator if labeled, in-game speed at elapsed times matching the Combine window (for example 1.0 s and 2.0 s post-snap), dropback duration, down and distance, score margin, quarter, game clock.
- **Outcomes:** time to pressure (event = pressure, censor time = dropback end, using `dropback_duration` for censoring only), `quick_pressure` (binary, with null handling per audit), `player_get_off`, sack (exploratory, rare).
- **Never** use `dropback_duration` as a covariate (it ends at pressure, release or sack). Never analyze `time_to_pressure` only among pressured snaps.
- **Absolute load** before snap *s*: cumulative distance (or time) above a fixed speed threshold over earlier snaps in the game, with rest time between snaps accounted for. Threshold tuned on training folds only.
- **Relative load** before snap *s*: cumulative effort above a fraction *f* of the player's shrunken Combine speed at the matched elapsed time (v(t) from the fitted curve), summed over earlier snaps. *f* tuned on training folds only, with the same search budget as the absolute threshold. Carry the ceiling's SE into the load by simulating draws from the posterior.
- Both load definitions use the same recency weighting (if any) and the same number of tuned parameters. Also compute snaps in drive and time since last snap as covariates. Total distance is a benchmark.
- Keep the ceiling out of the primary decay outcomes (they are times and labels, not ceiling fractions), so ceiling error enters only through load.
- Write one row per snap; modeling stages read only these tables.

**4. Models.**
- **Part 1 (ceiling to pressure):** time-to-pressure survival model (Cox or accelerated failure time) with a random effect or frailty by rusher, crossed effects for assigned blocker and team or opponent, and fixed effects for alignment, pre-snap depth, blitz flag, number of blockers, down and distance, score margin, position (edge vs. interior), height and weight, years since Combine. Estimand: hazard ratio (or time ratio) per 1 SD of the primary Combine acceleration feature. Secondary: logistic mixed model for `quick_pressure`; linear mixed model for `player_get_off`. Players are the effective sample; use player-clustered SEs.
- **Part 2 (relative vs. absolute load):** same mixed or survival structure with load added, once with absolute and once with relative load, with random slope on load by rusher when estimable. Convergence fallbacks, logged in decisions.md: (1) uncorrelated random effects with an alternate optimizer; (2) per-player slopes with empirical Bayes shrinkage; (3) random intercept only, population-level results only.
- **Primary comparison:** player-grouped cross-validation (no player in train and test), held-out log score (or concordance for survival) for the absolute vs. relative model, with bootstrap CIs over players. Tune *f* and the absolute threshold inside each training fold. Report the difference and its interval; it is meaningful only if the interval excludes zero and exceeds the prereg smallest effect.
- Secondary: population-level load coefficient under the better definition; load x ceiling interaction with measurement-error-corrected versions alongside naive ones; model-based reliability for per-player slopes (slope variance / (slope variance + mean squared SE)). Do not use odd/even split-half.
- Exploratory mediation: decompose the Combine effect on pressure into a path through get-off and a direct path. Report as descriptive; state the assumptions.
- Interior and edge reported separately and pooled with interaction.

**5. Validation.**
- Ceiling permutation: shuffle ceilings across players within position group and rebuild relative load and Part 1 features; the effects should vanish. If the relative model's advantage survives, it comes from something other than ceiling information.
- Within-player permutation: shuffle snap order within player-game; the load effect should vanish.
- Placebos: load should not predict pre-snap features (alignment, depth); Combine height and weight should not mimic the acceleration effect after controlling for position.
- Mechanical coupling check: repeat with ceiling-fraction outcomes only as a check; the primary result rests on the ceiling-free outcomes.
- Rotation and survivorship: report results restricted to rusher-games with complete snap coverage and to rushers with high snap shares; report snaps by quarter; run a version weighted by probability of staying in the game.
- Context sensitivity: exclude garbage time (rule in prereg), the final two minutes of each half, and obvious passing situations separately; vary blocker controls; use unblocked_pressure to flag pressures not attributable to the rusher's own ability (exploratory).
- State final sample sizes and the smallest detectable effect. Run an equivalence test against the prereg margin; interpret a null as informative only if the interval excludes the smallest effect of interest.

**6. Deliverables.** Public Kaggle notebook and writeup within official limits (placeholders for results until the full run), max 5 figures. Sections: problem, data audit, ceiling model, outcomes and censoring, Part 1 results, Part 2 results, limitations. /notes/limitations.md lists every threat and mitigation (blocker and scheme effects, stunts and credit assignment, rotation and partial snap coverage, alignment effects on get-off, reaction versus acceleration, model-derived pressure labels, null semantics, draftee-only selection, short follow-up for the 2025 class, contact and strength invisible to the chips).

## Tests before handoff
1. Schema assertions on columns, dtypes and units; fail loudly on mismatch.
2. Synthetic recovery: simulated rushers with a planted ceiling, planted hazard ratio for a0, and two worlds for decay (one where decay depends on relative load, one on absolute). The pipeline must recover the planted effects and favor the right definition in each. Include a null world (the pipeline must not find effects) and a censoring world with rare pressures.
3. Edge cases: rushers with few snaps, null labels, snaps with no pressure and no dropback end time, games with missing quarters, one-attempt players, missing frames, no game data.
4. Scale: run on the sample, then on the sample concatenated several times with renamed IDs. Check roughly linear memory growth, extrapolate to full size in /notes/scale.md, and flag stages likely to exceed RAM.
5. Determinism: same seed gives identical outputs.

## Handoff
- README_RUN.md: clone, environment setup, data download, ordered commands, expected runtime and RAM per stage.
- Pinned requirements.txt that works on ARM and x86.
- /notes/pending_full_run.md: questions only full data can answer, with the script for each.
- /notes/assumptions.md: unverified assumptions about full data, each with a runtime assertion.
- .gitignore data; commit only code, configs, notes and synthetic fixtures.

## Stop and write to blockers.md if
DL are absent from the Combine tracking; a required Combine or game field is missing; matched DL or pass-rush snaps are below config minima; null semantics are unresolved; key Combine features are unreliable; thresholds provenance is not "FULL" in a full run; prereg.md is missing before a full run; or any instruction conflicts with competition rules. If only snap coverage fails, drop Part 2, log it, and continue with Part 1.

## Layout and run order
/data/raw /data/interim /src /notebooks /notes /outputs/figures
01_audit -> 02_combine_ceiling -> 00_thresholds -> 03_game_snaps -> 04_models -> 05_validate
