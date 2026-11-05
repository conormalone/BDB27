# Limitations and threats to validity

Every threat named by `TASK.md` Phase 6, plus the ones this audit discovered.
Each entry is: **what the threat is**, **why it biases the answer**, **what the
pipeline does about it**, and **what remains**. A mitigation that is only a
sentence in a limitations section is not a mitigation — the "implemented in" line
names the code or note that carries it.

---

## 1. Stance and start-definition differences between Combine and snap

**Threat.** At the Combine the athlete starts from a track stance with a hand
down or standing, on a flat track, with no opponent, no cadence, and no snap. In
a game the rusher starts from a three-point or two-point stance, often with a
hand in the dirt, keyed on the ball, and must react. If the two settings have
different effective start conditions, then "time to cover *d*" is not the same
quantity in both, and the coefficient `b` on `phys_i` in the Phase 4 model will
not be near 1 for physical reasons rather than football reasons.

**Mitigation.** (a) One onset definition, applied identically
(`src/physical.py`, `onset.*` in `config.yaml`), so the *definition* is shared
even if the setting is not. Phase 2 uses the same `detect_onset` function.
(b) `TASK.md` Phase 2 asks for **global (not per-player) calibration offsets**
for stance and start type, written to `outputs/calibration.json`. That file is
currently a **PLACEHOLDER with no estimates**: stance and start type are not
labelled anywhere, and the Combine-to-game offset needs Phase 3 in-game times
(blocker B8). `common.require_calibration_full` refuses the placeholder in a
full run, so the offset cannot leak into a result as if it existed.
(c) `b` is reported *with an interval* and is itself the diagnostic: `b` far from
1 is a finding about setting differences, not a modelling failure.

**Implemented in.** `src/physical.py` (shared onset), `config.yaml → onset`,
`src/02_combine_physical_time.py` (calibration record + estimator spec),
`common.require_calibration_full`, Phase 4 model coefficient `b`.

**Residual.** Stance is not labelled (`notes/schema.md` §8), so no stance-specific
offset exists — not even a single pooled one. If stance varies systematically
between the players who look good and bad, the uncalibrated physical time will
absorb it. The only anchor is the in-game onset-to-cross time (Phase 3), which
has a different error structure.

## 2. Alignment effects

**Threat.** A wide-9 edge rusher and a shaded nose tackle have different
distances to the quarterback and different angles to the line. If alignment
correlates with the physical profile (it does: heavier players play inside), then
an unadjusted get-off comparison confounds alignment with physique.

**Mitigation.** Pre-snap depth is computed per rep from the line of scrimmage
(`notes/schema.md` §6) and enters the model as `phys_i(d_ij)` — i.e. *as a
function of the actual depth of that rep*, not a player average. Alignment
(edge/interior/shade) is a covariate in the Phase 4 model, and Phase 5 re-runs the
decomposition with alignment effects removed.

**Implemented in.** `01_audit` item (c) derivation; Phase 4/5 (stubs).

**Residual.** Shade is not labelled. Only the coarse `lined_up_position` split is
available, and that split itself is unreliable (threat 9).

## 3. 10 Hz resolution in the first second

**Threat.** One frame is 0.1 s. A typical get-off is ~0.96 s (median), so there
are ~10 samples; the first yard takes 1–2 frames. A threshold that trips on frame
*k* rather than *k+1* moves the onset by 0.1 s, ~10% of the measurement. This
inflates within-player variance and therefore *deflates* the between-player share.

**Mitigation.** (a) Onset requires **two consecutive** frames above threshold, so
a single-frame spike cannot start the clock (`onset.sustained_frames = 2`).
(b) Crossing times are **linearly interpolated** between frames rather than
snapped to the nearest sample (`physical.time_at_distance`). (c) Phase 5 varies the
onset threshold over a config grid and reports the sensitivity of the variance
shares.

**Implemented in.** `src/physical.py`; tests
`test_physical.py::test_single_frame_spike_is_not_an_onset`,
`test_time_at_distance_is_exact_on_a_linear_ramp`.

**Residual.** Interpolation recovers sub-frame *timing* but not sub-frame
*detection*: the onset itself is still a whole-frame decision. The residual
±0.1 s is a floor on measurement error and is the reason threat 4 matters.

## 4. Shared baseline error across a player's reps

**Threat.** If a player's onset definition is systematically early or late (a
quicker first step trips the threshold sooner; a slow starter later), that error
is **shared across all of his reps**. It does not average out with more reps. It
inflates the between-player variance and can manufacture a stable "jump" that is
pure measurement artefact — the most dangerous failure mode for this project,
because it produces exactly the result the study is looking for.

**Mitigation.** (a) `TASK.md` Phase 4 requires **measurement-error / regression
calibration** treatment of `phys_i`, not a plug-in. (b) `TASK.md` Tests require a
synthetic world with a **shared baseline error** to confirm the model corrects
the inflation; `src/synthetic.py` can plant one, and the test suite already
exercises the null world (`test_edge_cases.py`). (c) Phase 5's permutation test
shuffles physical time across players; a jump that survives permutation is
suspect.

**Implemented in.** `src/synthetic.py`, `tests/test_edge_cases.py`; Phase 4 (stub).

**Residual.** A shared error and a real jump are not identifiable from the same
data without an external anchor. The only anchor available is the **direct
snap-to-onset latency** measured from tracking (Phase 3), which has a *different*
error structure. Agreement between the residual and that latency is the strongest
available evidence; disagreement is a finding, not a nuisance.

## 5. Model-derived pressure labels

**Threat.** `quick_pressure`, `time_to_pressure` and friends sit alongside
`peak_pressure_probability_allowed` and `pressure_allowed`; this is the signature
of a pressure model's output, not an independent observation. If the pressure
label was produced using the rusher's own tracking, then "does jump predict
pressure" is partly circular.

**Mitigation.** (a) Provenance recorded in `notes/schema.md` §7 and item (f) of
the audit. (b) The Phase 4 outcome test estimates `u` and `phys` from
**odd-numbered games** and predicts in **even-numbered games**, so get-off cannot
mechanically contribute to the outcome it predicts. (c) `sack` (an objective
event) is available as an exploratory check on any pressure result. (d) The
offence-row family (`pressure_allowed`, `time_to_pressure_allowed`,
`sack_allowed`) is a pre-specified sensitivity comparison.

**Q2 assumption (Conor, 2026-11-05, D101).** Null `quick_pressure` is treated as
false, so the outcome is `(quick_pressure IS TRUE)` over all non-blitz DL
pass-rush snaps. This manufactures **40,184 negative labels** from the 92.2% null
share; only 2,334 rows are explicit `false`. If null actually means "unknown" or
"not evaluated" rather than "no pressure", the outcome is misclassified and the
Phase 4 effect will be attenuated or biased. The assumption is stated here and
must be stated in the prereg; it is an assumption, not a verified semantic.

**Implemented in.** Audit item (f); Phase 4 split (stub); Q2 decision D101.

**Residual.** The odd/even split removes mechanical leakage *within* the labelled
snaps but not the possibility that the label itself is a function of the same
underlying tracking quality that drives get-off. Unresolvable without label
documentation.

## 6. Rotation

**Threat.** Rushers are rotated by situation (pass-rush specialists play obvious
pass downs against tired tackles). Get-off measured on a player's reps is
therefore measured on a selected subset of situations, and the selection differs
by player. A "stable trait" could be a stable *role*.

**Mitigation.** Down, distance, score margin, quarter and team-game random effects
enter the Phase 4 model; Phase 5 reports snap counts per player and re-weights to
equalize snaps per player, so a player with 1,800 reps cannot dominate.

**Implemented in.** Phase 3/4/5 (stubs).

**Residual.** A player with 3 reps cannot be meaningfully adjusted. The 50-rep
minimum (`audit.min_pass_rush_snaps_per_rusher`) is a blunt instrument; 105 of 196
rushers clear it, so **91 rushers are excluded from the primary estimate**.

## 7. Draftee-only selection

**Threat.** Only players invited to the Combine appear, and the dataset is
pre-filtered to those 510 players (`notes/blockers.md` B5). Undrafted rushers,
late bloomers and players who skipped the Combine are absent. The "jump" may be
what distinguishes *Combine-calibre* players from each other, not what
distinguishes football players in general.

**Mitigation.** Nothing can fix it in this dataset. It is stated as the study
population in the writeup: *"Combine attendees from the 2023–2025 classes with at
least one recorded NFL pass-rush rep."* The audit reports the matching rate as
100% by construction so the omission is visible rather than implied.

**Implemented in.** `notes/phase1-audit.md` item (b); `notes/schema.md` §5.

**Residual.** Total. Any generalisation beyond the population above is unsupported.

## 8. Short follow-up for the 2025 class

**Threat.** `TASK.md` exploratory Q4 asks whether get-off improves year to year
through the residual. The dataset covers seasons 2023–2025 and draft classes
2023–2025. The 2025 class has at most one NFL season, so the year-over-year
question is answerable for the 2023 and 2024 classes only, and for 2024 only with
two points.

**Mitigation.** Report the year-over-year analysis **split by draft class**, with
the number of players contributing to each contrast, and treat the 2025 class as
descriptive only. `notes/pending_full_run.md` records the exact cohort counts
needed.

**Implemented in.** Phase 4 exploratory (stub).

**Residual.** With two seasons for one class, a year-to-year change is
indistinguishable from a change in role or in opponent quality.

---

## Additional threats found in this audit

### 9. Position labels are inconsistent between files

53 players labelled `EDGE` in games attended the Combine as `DB`; 440 get-off reps
sit on rows labelled `CB`/`SLOT_CB`. The edge/interior split is therefore
**noisy at the player level**. **Resolved by rule (Q3, Conor 2026-11-05):**
`player_play.lined_up_position` is the in-game authority; the vocabularies are
not required to agree. CB/SLOT get-off reps are excluded from the primary set and
listed in `outputs/phase2/position_conflicts.csv` with rep counts, so the
exclusion is explicit and reversible for a sensitivity analysis. Residual: the
labels themselves may still be wrong; the data cannot say.

### 10. `player_get_off` is capped at 2.0 s

Two of 43,562 reps sit exactly at 2.0 s. Censoring is negligible in practice, but
the cap means the variable is not truly continuous. Mitigation: flag any rep at
the cap rather than treating it as a measured value; report the count.

### 11. Depth is tiny, so the Combine curve is extrapolated

Medians: depth 0.63 yd, p95 1.24 yd. The in-game requirement is 0.5–2.3 yd. At
those distances the Combine measurement is at its least reliable (test-retest ICC
0.50 at 1 yd vs 0.63 at 5 yd). Mitigation: model-free interpolation on the
tracked distance curve, measurement-error treatment in Phase 4, and a reported
`b` with an interval. Residual: the estimand itself is dominated by the region
where the instrument is weakest — this is inherent to the question, not fixable
by method.

### 12. The measured design is smaller than the label-file design

Per-player mean reliability is high, but **between-player variance** precision is
driven by the number of players. Three computed counts matter (Phase 2 summary):
105 players clear the 50-rep minimum on the unfiltered get-off count (floor
0.271); 103 clear it under the Q3 primary-DL rule (floor 0.273); and only **80**
of those have a Combine 40-yard attempt (floor **0.310**). The smallest effect of
interest was raised to 0.25 by Conor (Q1), but 0.25 remains below the floor of
the design that can actually be fitted. This is surfaced in `D96` as a number
needing Conor's confirmation, not silently retuned. Mitigation: report intervals,
not point estimates, and interpret a small jump share as informative only if the
interval excludes the smallest effect of interest.

### 13. Development on a non-random sample

The Pi has one season's player-in-play sample, not a random draw
(`notes/schema.md` §3). Code was exercised against it and against synthetic
fixtures with planted effects. **No threshold, calibration or interpretation was
set from the sample.** Mitigation: every sample artefact is stamped
`UNVALIDATED SAMPLE OUTPUT`, and `src/common.py` refuses to let a sample run
assert `FULL` provenance.

### 14. No blocker control (Q4)

**Threat.** There are no rusher–blocker assignments in the data. Opponent quality
and double-teams are therefore uncontrolled: a player who draws the left tackle
every week and a player who draws a rotating guard will have different get-off
opportunities, and the residual "jump" can absorb that matchup difference.

**Mitigation.** None in the data. Conor decided (Q4, D98) to drop the blocker term
from every model and **not** to infer assignments from tracking geometry. The
Phase 4 model contains team-game random effects but no blocker term. The writeup
must state that the physical/jump split is *unadjusted for opponent quality*.

**Implemented in.** `TASK.md` Phase 3/4 amendment; `notes/blockers.md` B2
(resolved — not applicable); this entry.

**Residual.** Total for opponent quality. This is a design limitation, not a
missing-data problem that more tracking could fix without an assignment rule.

---

## 15. Null → false manufactures most of the negative labels (Q2)

**Threat.** `quick_pressure` is null on **29,686 of 33,061** DL pass-rush snaps
(89.8%). The agreed policy (Q2/D101) turns every one of those into `false`. If a
null actually means "not recorded" rather than "no pressure", the secondary
outcome is mostly an artefact of missingness, and its base rate (3.1%) is a
recording rate, not a pressure rate.

**Evidence that it is *probably* "no pressure".** `quick_pressure` and
`time_to_pressure` are populated together (2,334 `false` + 1,041 `true` + 40 with
a time but a null flag); 29,646 rows have neither. The null share is stable
across seasons (89.2-90.1%) and downs (86.9-91.3%), and varies 84.7-96.1% across
teams.

**Evidence that it matters.** In **152 of 907 games every snap is null**, and in
3 games none is. A team-game random effect cannot rescue a game with zero
positives; those games contribute no information about pressure, only about
get-off.

**Mitigation.** Keep the assumption explicit in the prereg; report the null rate
by season / game / team / down (`outputs/phase3/null_provenance.csv`) and the
co-occurrence table (`null_cooccurrence.csv`); run the offence-row family
(`pressure_allowed`, `time_to_pressure_allowed`, `sack_allowed`) as the
pre-specified sensitivity comparison; never present `quick_pressure` as a
complete binary. **Residual: total for semantic uncertainty.**

## 16. The primary set is not "non-blitz"

**Threat.** `TASK.md` asks for non-blitz pass-rush snaps. This release has no
blitz-call field, and the only candidate (`blitzing`) behaves like a pass-rush
participation flag: true on 99.8% of DL pass-rush reps, false on 98.1% of the
CB/SLOT rows of the same plays, null on 99.3% of DL run-play reps.

**Mitigation.** The exclusion is not applied (D104/B9); the raw flag is carried
per row and both readings are counted, so the decision is reversible without a
re-run. **Residual.** Blitzers are in the primary set. If blitzing changes get-off
(plausible: a blitzing DL may have a different stance and a clearer path), the
estimated player effects mix blitz and base reps. The writeup must not claim a
non-blitz sample.

## 17. The tracked crossing leads the published `player_get_off` by ~0.37 s

**Threat.** On the sample the interpolated x-axis crossing is earlier than the
label (median signed difference **−0.370 s**, n=1,368; median absolute 0.370 s).
The audit attributed the earlier 0.383 s gap to onset/10 Hz lag; the sign here is
the opposite of "detection starts late" for the *crossing* itself, which suggests
the label's clock and the tracking clock are not identical (snap event vs play
clock, or a crossing plane that is not exactly `LOS_x`).

**Mitigation.** The label is never overwritten and is the outcome in Phase 4; the
tracked crossing is carried separately with `cross_minus_get_off_s` as evidence.
`onset_to_cross_s` therefore inherits any label-clock offset and is **not**
directly comparable to `player_get_off` in level — only in spread. Any
Combine-vs-game comparison must use the model-free Combine curve and the tracked
crossing, not a difference of levels.

**Residual.** A constant or game-dependent offset. `notes/pending_full_run.md`
S15 checks whether it is stable across the three seasons.

## 18. Phase 3's tracking columns are sample-only in this cycle

**Threat.** The tracking sample is 2023 only, 1-4 players per play, and partial
per game. Only **40 of 158** primary players and **1,477 of 33,061** primary
snaps have any tracking. Every onset, crossing, depth and latency number in
`outputs/phase3/` is therefore `UNVALIDATED SAMPLE OUTPUT`.

**Mitigation.** The label-side counts (primary set, exclusions, per-player
snap/game counts, null provenance) come from the complete label file and are
real; they are labelled `FULL_RAW_LABEL_FILE` in `phase3_summary.json`. Tracking
blocks are labelled `SAMPLE_TRACKING`. The stage is wired so the same code path
produces the full numbers with `--mode full` once the files and the prereg exist.
