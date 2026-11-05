# Blockers — burst vs. jump

A blocker is something that **stops a specified analysis from being done
correctly**, not something that is merely hard. Each entry states what is blocked,
the evidence, and exactly what would clear it. Nothing here has been worked
around with a proxy.

Maintained from 2026-11-05 (Phase 0+1 run). Previous blockers file (leverage ×
ceiling) is archived at
`archive/legacy-2026-11-05/notes/blockers-legacy.md`.

---

## B1 — `quick_pressure` null semantics  · **RESOLVED BY DECISION (Q2, Conor 2026-11-05)**

**Resolution.** Conor decided (Q2, D101): null -> **false**. The secondary
outcome is `(quick_pressure IS TRUE)` over all non-blitz DL pass-rush snaps, with
the null->false assumption stated explicitly in the prereg when it is written.
The offence-row family (`pressure_allowed`, `time_to_pressure_allowed`,
`sack_allowed`) is a **pre-specified sensitivity comparison**, not an alternative
primary. This manufactures 40,184 negative labels from nulls; that risk is now
recorded in `notes/limitations.md` §5, not treated as resolved evidence. No
implemented stage reads `quick_pressure` yet (Phase 3-4 are stubs).

**Blocks:** `TASK.md` Phase 4 secondary outcome test ("predict `quick_pressure` …
in held-out games"), and the `quick_pressure` half of Phase 3's outcome table.

**Evidence.** On the 43,562 DL rows that carry `player_get_off`:

* `quick_pressure` is non-null on 3,378 rows and **null on 40,184 (92.2%)**;
* `time_to_pressure` has exactly the same null pattern (3,418 non-null);
* the 3,418 rows with a pressure time split into 2,334 `false`, 1,044 `true`,
  and **40 with a null flag**.

`quick_pressure` and `time_to_pressure` behave like an **event record**: they are
populated together, and a null almost certainly means "no pressure on this rep".
But that is an inference. Treating null as `false` would manufacture 40,184
negative labels; treating null as missing makes the outcome available on 7.8% of
snaps only.

**What would clear it.** Either (a) an official data dictionary stating that null
means "no pressure", or (b) a documented decision by Conor to define the outcome
as `pressure_allowed`/`sack_allowed` on the offensive row, or as
`(quick_pressure IS TRUE)` with null→false and the assumption stated in the
prereg. **(b) was chosen explicitly on 2026-11-05; (a) remains the only route to
verified semantics.**

**Interim rule in the code.** No stage reads `quick_pressure` yet (Phase 3-4 are
stubs), so nothing is currently wrong. The audit records the null counts and the
`FAIL` row so this cannot be forgotten.

---

## B2 — rusher–blocker assignments do not exist as data  · **RESOLVED — NOT APPLICABLE (Q4, Conor 2026-11-05)**

**Resolution.** Conor decided that the blocker term is dropped from every model,
that assignments are **not** to be inferred from tracking geometry, and that the
blocker / blocker-count fields are removed from the Phase 3 field list. The gap
is no longer a question about a needed input: there is no blocker term to feed.
`TASK.md` Phase 3/4 were amended (D98). The consequence — the physical/jump split
is unadjusted for opponent quality — is recorded in `notes/limitations.md` §14.
The historical evidence below is kept for provenance.

**Blocks:** `TASK.md` Phase 3's per-snap fields "assigned blocker(s) and blocker
count", and the `+ blocker` term in the Phase 4 model.

**Evidence.** No column in `player_play.csv` or any tracking file names a blocker
for a rusher. The nearest fields are `pass_rushers_encountered` (an integer count
on the play), `unblocked_pressure` (a flag) and `pressure_allowed` /
`sack_allowed` (on the offensive player's row).

The blocker could in principle be *inferred* from tracking geometry
(nearest offensive lineman in front of the rusher at the snap), but:

* the provided tracking sample carries **1–4 players per play** (median 1), so the
  linemen are usually not present;
* `TASK.md` does not authorise an inference method, and inventing one would breach
  the "if the task is silent, that is a question" rule.

**What would clear it.** Full game tracking (22 players per play) plus a documented
matching rule — nearest opponent within *x* yards and in front along the rush
direction — approved by Conor and written into the prereg.

---

## B3 — position vocabularies disagree, and one is noisy  · **RESOLVED (Q3, Conor 2026-11-05)**

**Resolution.** Trust `player_play.lined_up_position` as the in-game
position/alignment authority; do **not** require `combine_position` to agree; do
not add a second rule. Players whose only get-off reps are on `CB`/`SLOT_CB` rows
are excluded from the primary set and listed in
`outputs/phase2/position_conflicts.csv` with their rep counts, so the exclusion is
documented rather than silent (D97). Recomputed counts match the evidence below:
53 EDGE-attended-as-DB, 9 INTERIOR_LINE-attended-as-DB, 25 CB/SLOT-attended-as-DL,
440 CB/SLOT get-off reps. The split remains *noisy* — that is a stated limitation,
not an unresolved question.

**Blocks:** the edge vs. interior split's *validity* (not its computability).

**Evidence** (`notes/schema.md` §5a):

* `player_play.lined_up_position` uses `EDGE` / `INTERIOR_LINE`, never `DE`/`DT`/`NT`.
* `players.csv` and `combine_results.csv` use `DE`/`DT`/`NT`/`DL`/`DB`.
* Cross-tab: **53** players labelled `EDGE` in games attended the Combine as `DB`;
  **9** `INTERIOR_LINE` as `DB`; **7** `CB` and **25** `SLOT_CB` as `DL`.
* **440** `player_get_off` reps sit on rows labelled `CB` or `SLOT_CB`.

At most one of the two labels is right for those players, and the data cannot say
which.

**What would clear it.** A rule from Conor, e.g. "trust `lined_up_position` because
it is the in-game alignment and drop the conflicting players", or "require both
labels to agree and report the excluded count". **Neither has been chosen.**
The audit reports the split using `lined_up_position` and states the conflict.

---

## B4 — no official reproduction of `player_get_off`'s definition  · **OPEN (accepted, wording fixed)**

**Status update (2026-11-05).** No data dictionary has appeared. Phase 2 does not
read `player_get_off`; it measures Combine kinematics from tracking only. The
working definition remains "as defined in the competition data", and the writeup
must not quote an official wording (see `notes/limitations.md`). This is a
standing interpretive limit, not a computational blocker.

**Blocks:** nothing computationally; it limits how strongly the label can be
interpreted.

**Evidence.** No data dictionary ships with the files, and the Kaggle pages are
client-rendered shells (`notes/rules.md` §1). The empirical check in the audit
(`notes/phase1-audit.md` item d) shows an x-axis crossing reproduces the label
better than an along-travel crossing, which is *consistent* with "crosses the
line of scrimmage" but does not prove the official wording.

**What would clear it.** A readable copy of the competition data dictionary.
Until then, the writeup must say "as defined in the competition data" rather than
quoting a definition as official.

---

## B5 — the provided dataset is pre-filtered to 510 Combine attendees  · **OPEN (structural)**

**Blocks:** any estimate of selection effects, and any claim about "NFL defensive
linemen" as a population.

**Evidence.** `players.csv`, `combine_results.csv` and the `nfl_id` set in
`player_play.csv` are the same 497–510 players (join is 100% both ways; there are
zero unmatched rows). Only 1,001 of 1,002 games appear in `player_play`.

**What would clear it.** Nothing, in this dataset. It is a property of the release.
It must be stated in the writeup as the study population, not buried as a
limitation.

---

## B6 — prereg is not written  · **OPEN (blocks every full run; Q5 answered, file still absent)**

**Status update (2026-11-05).** Q1 (smallest effect of interest) is now answered
and in `config.yaml` (D96). Q5 is now answered as **option D, a two-part freeze**
(D102): the confirmatory core is frozen next cycle before Phase 3 results, and an
operational annexe (onset threshold, distance grid, minimum snaps) is frozen
immediately after Phase 3. `notes/prereg.md` still does not exist during this
cycle. Phase 2 is therefore implemented and unit-tested but driven only in
`--mode sample` plus synthetic data. The full-mode gate was not weakened:
`python src/run.py 02_combine_physical_time --mode full` exits 4 with the prereg
refusal.

**Tension to resolve before Phase 3 (flagged in D102).** `TASK.md` requires the
prereg to fix the operational constants *before* the first full-data run; option
D freezes them *after* Phase 3. Either the annexe must be frozen before that run
too, or `TASK.md` must say the Phase 3 run is operational and only Phase 4 is
confirmatory. Not reconciled here.

**Blocks:** `--mode full`. The gate in `src/common.py` refuses to run in full mode
unless `notes/prereg.md` exists and declares `**Status: FROZEN.**`.

**Evidence.** The file does not exist. The previous prereg belonged to the retired
intra-drive-regeneration spec and has been archived at
`archive/legacy-2026-11-05/notes/prereg.md`.

**What would clear it.** Write and freeze the confirmatory core first, then the
operational annexe per Q5/D102. The gate only reads `notes/prereg.md` for
`**Status: FROZEN.**`; whichever document is authoritative for a given full run
must exist and be frozen at that moment.

---

## B7 — no full-data game tracking on this machine  · **OPEN (environmental)**

**Blocks:** every `PENDING_FULL_RUN` item.

**Evidence.** `data/raw` contains `game_tracking_2023_sample.csv` only. The three
full-season files named in `config.yaml` (`game_tracking_2023.csv`,
`game_tracking_2024.csv`, `game_tracking_2025.csv`) do not exist; there are no
Kaggle credentials at `~/.kaggle`.

**What would clear it.** The full tracking files on the larger machine. The code
path is already shared: `--mode full` selects them by config key and refuses to
run if they are missing.

**Not a blocker for this run:** Phase 1 is answerable because the Combine data and
the label file are complete; only tracking-derived checks are limited.

---

## B8 — Phase 2 calibration offsets are not estimable from the released files  · **OPEN (blocks a calibrated full run)**

**Blocks:** `TASK.md` Phase 2's "global (not per-player) calibration offsets for
stance and start type", and therefore any Phase 4 model that is supposed to use
calibrated physical times.

**Evidence.**

* No column in any provided file labels stance (three-point vs stand-up). Phase 1
  §c recorded this and did not test inferability.
* No column labels the Combine start type.
* The global Combine-to-game offset needs in-game onset-to-cross times, which are
  produced by Phase 3 — a stage that does not exist yet.
* `TASK.md` forbids calibrating from the sample, so nothing can be estimated in
  this run.

**What the code does.** Phase 2 writes `outputs/calibration.json` with
`status = "PLACEHOLDER"` (sample) and `estimates = null`, plus the exact
estimator specification. `common.require_calibration_full` refuses a placeholder
or non-estimable record in full mode. Phase 2 itself refuses to complete a full
run when calibration is not `ESTIMATED` (TASK.md stop condition).

**What would clear it.** A stance label (or a Conor-approved inference rule), a
recorded start type, and Phase 3's in-game times; then estimate the global
offsets on full data and re-run Phase 2 to write `status = "ESTIMATED"` with
`FULL` provenance.

---

## B9 — the blitz exclusion cannot be applied with this release  · **OPEN (needs Conor)**

**Blocks:** `TASK.md` Phase 3's "Primary snap set: pass-rush snaps by DL
(non-blitz). Blitzers ... are excluded from primary analyses", and any claim in
the writeup that the primary set is *non-blitz*.

**Evidence (complete label file, D104).** `player_play.blitzing` is true on
**32,987 of 33,061** DL pass-rush reps (99.8%), false on 27, and null on 47. On
the *same* plays, CB/SLOT rows are true 1.9% of the time. The column is null on
99.3% of DL reps on run plays. All **3,375** labelled pressure rows sit on
`blitzing = true` rows. Within a play, DL rows agree on the value in 5,834 of
5,836 multi-DL plays.

That pattern is a per-player **pass-rush participation** flag, not a blitz call.
Applying the literal rule (`blitzing IS NOT TRUE`) would leave **74** of 33,061
rows — i.e. it would delete the primary set, not trim it.

**What the code does (not a proxy).** The exclusion is **not applied**; the raw
tri-state `blitz_flag` is carried on every row with
`blitz_exclusion_status = NOT_APPLIED_FLAG_IS_NOT_A_BLITZ_INDICATOR`, and both
the literal-rule counts and the flag distribution are reported in
`outputs/phase3/phase3_summary.json` and `blitz_diagnostic.csv`. A future rule can
re-filter the existing table without a re-run.

**What would clear it.** (a) Conor accepts "all DL pass-rush reps" as the primary
set, with the unadjustable-blitz limitation stated; or (b) a data dictionary
documents `blitzing` and (if it really is a blitz call) explains the value
distribution above; or (c) another field identifies extra rushers (none does
today: `pass_rushers_encountered` is a per-blocker count, 0-3).

**Not decided by me.** This is recorded as a question for Conor (D104), because
choosing to keep blitzers changes what the primary set means.

---

## B1 — `quick_pressure` null semantics · **RESOLVED BY DECISION, now IMPLEMENTED**

**Status update (Phase 3 run).** The policy is implemented and the assumption is
measured. `quick_pressure_binary = (quick_pressure IS TRUE)` over the 33,061
non-blitz-candidate DL pass-rush snaps: **1,041 true**, **2,334 explicit false**,
**29,686 nulls converted to false**. The null share is 89.8% (not 92.2%, which was
the figure on *all* DL get-off rows including run plays).

The diagnostic that full data must confirm, already written per season / game /
team / down (`outputs/phase3/null_provenance.csv`):

* by season: 90.1% (2023), 89.2% (2024), 90.1% (2025);
* by down: 91.3% / 90.5% / 86.9% / 88.3% for downs 1-4;
* by team: 84.7% (HOU) to 96.1% (LV);
* by game: 0% in 3 games, 100% in **152** games. **That last number is the real
  threat**: in 152 of 907 games every snap is a manufactured negative. A
  game-level fixed effect does not fix a game with no positives.

`null` is still not documented as `false`. Treating it as `false` manufactures
29,686 negatives; treating it as missing leaves 3,375 usable labels. Conor chose
null → false explicitly (Q2/D101), so this is an assumption, not a data fact, and
it is stated in `notes/limitations.md` §15.

---

## B5 — the dataset is pre-filtered to 510 Combine attendees · **OPEN (structural)**

**Status update (Phase 3 run).** Unchanged and now quantified on the analysis set:
the label file yields **196** players with ≥1 `player_get_off`, **158** of them in
the Phase 3 primary set, and **40** of those appear in the 2023 tracking sample.
The restriction is a property of the release, not a sampling accident: there is
no unmatched group to compare against, so selection cannot be estimated, only
stated. The study population remains "*Combine attendees from the 2023-2025
classes who recorded ≥1 NFL pass-rush rep*".

---

## B7 — no full-data game tracking on this machine · **OPEN (environmental)**

**Status update (Phase 3 run).** The Phase 3 code path now exists and is driven
in `--mode sample`. In full mode it would (i) convert the three season files to
Parquet partitioned by `game_id`, (ii) read one game at a time, and (iii) refuse
before writing anything if `notes/prereg.md` is missing — which it still is, so
`--mode full` exits 4. The absence of the full files remains the blocker for every
`PENDING_FULL_RUN` item in `notes/pending_full_run.md`.

---

## B8 — Phase 2 calibration offsets are not estimable from the released files · **OPEN — NON-BLOCKING**

**Status update (Phase 3 run).** Phase 3 now produces the in-game side that B8
said was missing: `onset_to_cross_s` (1,253 usable reps in the sample) and the
tracked crossing. What is *still* missing is a stance label and a Combine start
type, so the global offsets remain unestimable and `outputs/calibration.json`
stays `PLACEHOLDER`. Phase 3 does not read calibration and does not depend on it.

**Decision (Conor, 2026-11-05, D110/D113).** This no longer blocks the prereg.
No stance inference rule is invented: stance is not a covariate, not a
stratifier and not imputed. Stance and start-type variation are absorbed into
the residual `u_i` and the rep-level error, and this is stated in
`notes/prereg.md` §6 as a documented component of what "jump" contains. The
calibration file stays `PLACEHOLDER`; Phase 4 does not read it.

---

## B10 — `player_get_off` is not a line-of-scrimmage crossing, and Phase 2's distance does not match it · **RESOLVED (Conor, 2026-11-05)**

**Resolution (Conor, 2026-11-05, D110).** The outcome moves to the **tracked
line-of-scrimmage crossing** on both sides of the model: `cross_time_s` is the
outcome, and `phys_i(d_ij)` is evaluated at the pre-snap depth to that same
line. `player_get_off` is demoted to a validation check and never enters a
model. No offset of any kind is applied; the 0.86 yd sample median is not
hard-coded. Phase 2's distance definition is therefore correct as written. The
prereg fixes this in §3 and §5. The evidence below is retained for provenance;
the "what would clear it" list is answered by option (b), with the caveat that
the tracked crossing adds the ≈0.29 yd integer-yard spot error to the outcome
(limitations §21).

**Blocks:** `TASK.md` Phase 2's "physical time for each in-game pre-snap depth *d*
(the distance the rusher must cover to cross the line)" **as a predictor of
`player_get_off`**, and the prereg item "distance definition". Phase 4's
`get_off_ij = a + b*phys_i(d_ij) + ...` currently compares two different
distances.

**Evidence** (full details in `notes/crossing-diagnostic.md`; every number is
`UNVALIDATED SAMPLE OUTPUT`, n = 1,819 reps / 44 players / 22 paired snaps):

* The line Phase 3 derives is correct: 99.7% of offensive players and 97.7% of
  defensive players sit on the expected side of it; the C/G/T body-centre
  offsets are −0.61 / −1.15 / −1.37 yd; a systematic line error is bounded at
  ≈0.3 yd (0.15 s), 39% of the gap. Direction and sign are right on 99.9% of
  reps. No code defect was found.
* `player_get_off` behaves as the crossing of a plane **0.86 yd past** the line
  (median; SD 0.31), as a **distance** (gap correlates +0.57 with crossing
  speed; a fitted clock offset leaves the residual SD at 0.205 s), and as a
  **plane** (paired within-play slope of implied distance on depth 0.99 ± 0.11,
  where a fixed first-step distance gives 0).
* The plane is **play-level**: two rushers on the same snap agree to 0.06 yd;
  between-play SD is 0.23 yd. So a per-player calibration cannot recover it.
* Phase 2's distance is 0.65 yd median against the label's 1.50 yd. The
  between-player cost is small (`corr = 0.979`, implied `b ≈ 1.31`) but it makes
  `b` and the intercept uninterpretable and would be misreported as a
  stance/start-type difference.

**What the code does (not a proxy).** Nothing is changed and nothing is
substituted: the label stays the outcome, `cross_minus_get_off_s` stays evidence,
`phys` is still computed at the pre-snap depth, and no Phase 4 input exists yet.
The diagnostic is a standalone script (`src/crossing_diagnostic.py`) deliberately
**not** added to `run.py`'s frozen stage order.

**What would clear it.** A decision from Conor on the **distance definition**,
one of:
(a) keep the label as the outcome and evaluate `phys` at `depth + c0`, with `c0`
    a preregistered definitional constant estimated on full data (never from the
    sample, never a time offset);
(b) define the outcome on the tracking (`snap → crossing`, or `onset → crossing`)
    and compute `phys` at the same distance — consistent, but a change of
    estimand and it adds the ±0.29 yd integer-yard spot error (±0.14 s) to the
    outcome;
(c) keep `phys(depth)` and state in the prereg that `b` and the intercept are
    not interpretable as the physics (weakest; the decomposition share survives).
Not acceptable: a time calibration offset, or silently replacing one measure
with the other.

**Chosen: (b).** Conor selected the tracked `snap → line-of-scrimmage crossing`
as the outcome (D110). The original ±0.29 yd spot-error caveat is now
limitation §21 and is carried in the prereg's power caveat.

**Not a TASK.md stop-list item.** The line and the depth *can* be derived; this
is a definitional gap, so the pipeline is not stopped — but the prereg cannot be
frozen, because "distance definition" is one of the items it must fix.
