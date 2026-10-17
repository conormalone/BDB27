# TASK: Does Combine stakes shape the Combine-to-game speed gap? (WR primary, TE replication)
NFL Big Data Bowl 2027 (Kaggle), deadline Jan 6, 2027 23:59 UTC.

## Goal
Among receivers who reached the NFL, test whether pre-Combine draft security (from expected draft position, EDP) predicts how much of their Combine speed ceiling shows up in games.
- **H1:** players under pressure (high EDP, falling, in danger of going undrafted) sit further below their Combine speed in games.
- **H2:** secure players (low EDP, rising, or skipping events) reach or exceed their Combine ceiling in games more often.
H1 and H2 are the two ends of one relationship (same sign of the security effect). Test it once as the pre-registered primary result, then examine which end drives it. Primary group: WR. Replication: TE (pass-route snaps only).
Claims are limited to players who reached the NFL (only draftees have game tracking). Interpret any finding as a Combine-to-game measurement gap whose causes include effort, preparation and day-level luck. Never claim intent, motivation or "gaming the Combine".

## Environment
You run on a Raspberry Pi with limited RAM. Game tracking here is a ~100 MB SAMPLE; the final run happens on a bigger machine. Produce correct, memory-safe, tested code that runs unchanged on full data. Scripts 01-06 must run end-to-end in `--mode sample` on sample or synthetic data. Label every sample output `UNVALIDATED SAMPLE OUTPUT`. Never interpret, report or tune against sample results.

## Rules
- Never invent data, columns or results. If something is missing or ambiguous, stop and write to /notes/blockers.md.
- Official competition docs and rules are the source of truth; third-party repos are unverified hints.
- The EDP board file is external data. Confirm in the competition rules that it is permitted (and whether it must be shared publicly) before using it. If not permitted, stop and report.
- Log decisions and reasons in /notes/decisions.md. Report null results honestly.
- Fixed seeds, scripts not ad-hoc cells, strict sequential gating.
- Before the first full-data run, write /notes/prereg.md fixing: primary exposure, outcome, model, k and N, tier cutoffs, quantile set, minimum sample sizes, reliability thresholds. Do not change it after seeing full-data results; log any deviation as exploratory.

## Data: draft boards
Columns: draft_year (2023-2025), name, position, school, type ("All"), nfl_id (matches Big Data Bowl IDs), n_total, n, edp (expected draft position; lower = more secure), edp_rank, pos_rank, snapshot_date, snapshot_note (Combine window: 1-2 weeks before and after, and week of).
- Use `nfl_id` for all joins; never fuzzy-match names without logging match quality.
- Pre-Combine = the 2-weeks-before and 1-week-before snapshots only. Post-Combine and week-of snapshots are never used as exposures, since they may reflect Combine results.

## Engineering
- Scripts take `--mode sample|full` and `--force`, one code path for both. Paths, thresholds and sizes live in config.yaml; no magic numbers.
- Never load full CSVs into pandas. Convert to Parquet, then query with DuckDB or lazy Polars with explicit column selection. Process game data per game or player in chunks and write small per-effort summary tables; modeling stages read only summaries.
- Downcast dtypes. Log wall time and peak RAM per stage to outputs/run_log.csv.
- Stages checkpoint to disk and skip finished work unless `--force`.
- Check how the sample was drawn and note which code paths it cannot exercise. If it lacks enough matched players, generate a synthetic schema-compliant set with a planted ceiling and a planted security effect.

## Phases
**0. Setup.** Download data via Kaggle API. Summarize rules and limits (writeup and figure caps, notebook requirements, external data) in /notes/rules.md. Record schemas and file sizes in /notes/schema.md.

**1. Audit (gate: go/no-go).** Evidence, not assumptions:
a. Which WR and TE drills exist in the Combine tracking; which are straight-line and which involve cuts or routes.
b. Attempts per player per drill; Combine class years covered.
c. Board snapshots: values of snapshot_note, snapshots per player, snapshot_date vs. actual Combine dates for each year and vs. each player's testing day. Define pre-Combine snapshots strictly before the player's own testing.
d. `nfl_id` match rate between boards, Combine tracking and game tracking; duplicates; position mismatches between board and competition files.
e. Prospect pool: how many board prospects have Combine tracking, how many have game tracking, and whether undrafted players appear in either.
f. Per-player drill participation (which straight-line and route drills have tracking); flag skippers.
g. Game seasons available per class (determines the range of years since Combine).
h. Matched receivers per security tier and per participation pattern; route snaps per matched player.
i. Is route type or depth provided in game data? If not, can depth and break angle be derived? Are all 22 players tracked per play? Is ball location or time-to-throw available? Which plays are included (non-targeted routes too)?
j. Is the provided Combine distance and acceleration reliable? Compare with values derived from x/y; check sampling rate, units, coordinates and Combine vs. game processing differences.
k. Test-retest reliability (ICC) of candidate Combine features (peak speed, speed at fixed times, splits, a0, deceleration and cut metrics).
l. Power calculation: smallest security effect detectable given matched sample sizes.
Game-side items needing full data: script now, mark PENDING FULL RUN.
Go/no-go: stop if external data is not permitted, a required field is missing, matched receivers or route snaps fall below config minima, key Combine features have ICC below the config threshold, or the power calculation shows the primary effect is undetectable at any plausible size. Log the decision.

**2. Combine envelope** (run for real if the Combine data fits in RAM).
- Smooth the speed curve and fit v(t) = vmax * (1 - exp(-t/tau)) per attempt on straight-line drills; derive a0 = vmax/tau. Do not differentiate raw positions for acceleration.
- Also compute model-free features (speed at fixed times, split times). For cutting drills, compute entry speed, braking distance, exit speed.
- Pool attempts per player with hierarchical shrinkage. Use shrunken estimates with standard errors everywhere downstream; never a best attempt.
- vmax and a0 are strongly negatively correlated within players. Report it, keep at most three features, choose by ICC and VIF fixed in prereg.
- Keep original attempt numbers and real clock time. Sanity checks: flag speeds above 12 yd/s, duplicate attempts, gaps inside the peak window. Interpolate gaps under 0.5 s only outside peak windows.
- Compute Combine-only peaking markers (best-minus-mean attempt gap, attempt count), definitions fixed in prereg.
- Output a per-player feature table with uncertainty.

**3. Game demand** (full data only for final; scripted and unit-tested now).
- `00_thresholds.py` writes outputs/thresholds.json (effort cutoffs, minimum snaps, route filters). Provenance is "PLACEHOLDER_SAMPLE" in sample mode and "FULL" in full mode; scripts 03 to 06 assert provenance == "FULL" in full mode. Never set thresholds from the sample. Freeze before Phase 5.
- Identify route snaps (WR; TE restricted to pass-route snaps; state that TE blocking exertion is invisible to the chips). Use provided route labels if present, otherwise derive depth and break angle.
- Per player take the top-k efforts (k fixed in prereg) per fixed number of route snaps; never use raw maxima.
- Per effort: max speed, peak acceleration, route family, depth, play context, nearest-defender distance and time to throw if available.
- Exceedance: for each player, subsample efforts to a fixed N (config), repeat >= 200 times, and compute the posterior probability that game speed exceeds the Combine ceiling, propagating the ceiling's uncertainty. Never compare a many-effort maximum with a few-attempt maximum directly.

**4. Security variables (pre-Combine information only).**
- Primary exposure: log(EDP) at the last pre-Combine snapshot (1 week before), continuous. Tier versions (cutoffs set from counts in prereg) are a sensitivity check only.
- Exploratory: EDP trend (1 week pre minus 2 weeks pre), consensus share n / n_total, and drill-participation pattern (skipping is not security by itself; keep it separate).
- Post-Combine EDP and the pre-to-post change are outcomes of the Combine, not exposures. Never include them in the primary model.

**5. Models.**
A. *Stakes check* (all board prospects with Combine tracking; no game data needed): regress pre-to-post EDP change on Combine speed (shrunken), interacted with pre-Combine EDP. Tests whether the market rewarded speed more for at-risk players, and verifies snapshot timing.
B. *Primary test (pre-registered, confirmatory):* log(game top-k speed) ~ log(shrunken Combine speed) + log(pre-Combine EDP) + route family, depth, snap share, position, height, weight, years since Combine (+ defender controls if available). Player-clustered SEs. Handle error in Combine speed (posterior SEs; regression calibration or joint model) and report naive and corrected estimates. The estimand is the security coefficient. The Combine coefficient is expected below 1 for everyone (regression to the mean) and is not a hypothesis test.
C. *Which end drives it:* add a spline or piecewise term in security; report fitted game speed relative to Combine speed at the 10th, 50th and 90th percentile of security. Report overall exceedance first, then by tier.
D. *Skippers:* among secure players with missing speed drills, compare game speed with predictions from size and completed drills; use any available injury information to separate injury skips from other skips. Exploratory.
E. *Selection model:* using the prospect pool, estimate probability of appearing in game data from pre-Combine EDP, position, size and Combine performance. Rerun B with inverse-probability weights and report sensitivity bounds. H1 is exposed to selection on being drafted, so treat it as exploratory unless this analysis supports it.
F. *Falsification and dose-response:* height and weight should not show the same pattern; the effect should be strongest on max-effort plays and absent on low-effort ones; Combine peaking markers should predict a larger gap among at-risk players if effort is the mechanism.
G. *Ceiling checks:* overall exceedance rate and tail behavior from the base design (quantile models at the prereg quantile set).
Fallbacks if estimation is unstable: top-k mean regression with cluster bootstrap, or fewer quantiles. Log in decisions.md.

**6. Validation.**
- Player-grouped cross-validation, held-out error against a baseline with position, size and route controls only; bootstrap CIs.
- Permutation: shuffle security across players within position; the effect should vanish.
- Sensitivity: k, N, quantile set, tier cutoffs, route families, minimum-snap filters (including a high-snap-only run), excluding extreme Combine outliers, TE vs. WR.
- State the smallest detectable effect and the final sample sizes.
- Report the excess Combine-to-game gap as an upper bound on effort-related peaking, since attempt-level reliability overstates day-level reliability.

**7. Deliverables.** Public Kaggle notebook and writeup within official limits (placeholders for results until the full run), max 5 figures. Sections: problem, data audit, method, ceiling and exceedance, security effect, limitations. /notes/limitations.md lists every threat and mitigation (selection on being drafted, regression to the mean, opportunity differences by tier, snap-count effects on maxima, Combine-specific preparation, small groups, forking paths).

## Headline gate
The security question leads the paper only if: external data is permitted; each security group meets the prereg minimum matched-receiver count; Combine ceiling reliability passes; and analysis A shows the market responded to speed. Fix these before any full-data run. If any fails, fall back to the ceiling and tail design as the paper's main framing and report security as exploratory.

## Tests before handoff
1. Schema assertions on columns, dtypes and units; fail loudly on mismatch.
2. Synthetic recovery: simulated receivers with a planted ceiling, planted security effect and known exceedance rate. The pipeline must recover them. Include a null case with no relationship (the pipeline must not find one) and a case with selection on being drafted.
3. Edge cases: one-attempt players, missing frames, ties, no game data, too few routes for top-k, missing board snapshots, players with no pre-Combine snapshot.
4. Scale: run on the sample, then on the sample concatenated several times with renamed IDs. Check roughly linear memory growth, extrapolate to full size in /notes/scale.md, flag stages likely to exceed RAM.
5. Determinism: same seed gives identical outputs.

## Handoff
- README_RUN.md: clone, environment setup, data download, ordered commands, expected runtime and RAM per stage.
- Pinned requirements.txt that works on ARM and x86.
- /notes/pending_full_run.md: questions only full data can answer, with the script for each.
- /notes/assumptions.md: unverified assumptions about full data, each with a runtime assertion.
- .gitignore data; commit only code, configs, notes and synthetic fixtures. Do not commit the board file if redistribution is restricted.

## Stop and write to blockers.md if
External board data is not permitted; a required Combine, board or game field is missing; matched receivers or route snaps are below config minima; key Combine features are unreliable; any exposure uses post-Combine or week-of information; thresholds provenance is not "FULL" in a full run; prereg.md is missing before a full run; or any instruction conflicts with competition rules.

## Layout and run order
/data/raw /data/interim /src /notebooks /notes /outputs/figures
01_audit -> 02_combine_envelope -> 00_thresholds -> 03_game_demand -> 04_security -> 05_models -> 06_validate
