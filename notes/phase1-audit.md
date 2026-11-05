# Phase 1 audit — burst vs. jump

**Run:** 2026-11-05, `python src/run.py 01_audit --mode sample`, commit pending.
**Machine-readable artefacts:** `outputs/audit/audit_items.csv` (64 rows),
`outputs/audit/gate.json`, `outputs/audit/combine_drill_inventory.csv`,
`outputs/audit/dl_rusher_counts.csv`, `outputs/audit/onset_combine_attempts.csv`.

## How to read this document

Each finding carries a **scope**, because the three inputs have different status:

| scope | meaning |
|---|---|
| `FULL_RAW` | computed from a file provided in full (`player_play`, `combine_*`, `games`, `players`) |
| `SAMPLE_TRACKING` | computed from `game_tracking_2023_sample.csv`. **UNVALIDATED SAMPLE OUTPUT.** Never quote as a finding. |
| `VALIDATION` | a check that a derivation works (e.g. does the LOS formula land where rushers actually stand). Passing is meaningful; the magnitude is sample-only. |
| `DESIGN` | a power/design calculation, not a data estimate |

`PENDING_FULL_RUN` means the statistic is scripted and runs, but `TASK.md`
requires full-data game tracking before it can gate anything.

---

## Verdict

**CONDITIONAL GO.** No kill condition is triggered. Two gate conditions cannot be
settled without full-data game tracking (`get_off_reliability`,
`combine_vs_get_off`), and one input problem (`quick_pressure` nulls) constrains
the secondary analysis. Details in "Gate decision" below.

---

## a. Combine tracking for DL

| quantity | value |
|---|---:|
| Combine players classified `DL` | 118 |
| class years | 2023–2025 (3 classes) |
| distinct drills containing a DL player | 20 |
| straight-line sprint from a stance | **`FORTY_YARD_DASH`, present** |
| 40-yard attempts per DL player | 1.9 |
| DL players with ≥2 attempts | 84 |

The 40-yard dash is the only straight-line sprint in the file. The DL-specific
drills (`FOUR_BAG_AGILITY_DRILL`, `RUN_THE_HOOP_DRILL`, `RUN_AND_CLUB_DRILL`,
`FRONT_START_WAVE_DRILL_AND_LATERAL_REACTION`, …) are change-of-direction
drills and cannot substitute: their paths are not a straight line, so the
"time to cover *d* along the direction of travel" quantity is not defined on
them in the same way.

**Verdict: PASS** — a straight-line sprint from a stance exists.

## b. Matched Combine-to-NFL DL

| quantity | value |
|---|---:|
| players in `player_play` | 497 |
| matched to a Combine row | **497 (100%)** |
| DL rushers with ≥1 `player_get_off` | 196 |
| median pass-rush reps per rusher | 67 |
| rushers with ≥50 reps | 105 |
| median games per rusher | 9.5 |
| get-off reps at `EDGE` | 26,232 |
| get-off reps at `INTERIOR_LINE` | 17,330 |

**The matching rate is 100% by construction and that is the finding.** The whole
provided dataset is pre-filtered to the 510 Combine attendees: `players.csv`,
`combine_results.csv` and the `nfl_id` set in `player_play.csv` are the same 510
players. There is **no unmatched group**, so "is a Combine-to-NFL match
selection-biased?" cannot be tested on this data — the selection is total. The
study population is *"Combine attendees from the 2023–2025 classes who recorded
at least one NFL pass-rush rep in 2023–2025"*, not "NFL defensive linemen".

Two consequences, both binding on interpretation:

1. Every result is conditional on having been invited to the Combine and
   drafted/rostered. That is a much narrower population than "pass rushers".
2. `TASK.md`'s limitation "only drafted DL appear" is not a risk to be mitigated;
   it is the definition of the dataset. It must be stated in the writeup, not
   just in the limitations section.

The edge/interior split uses `lined_up_position`. It is **not** consistent with
`combine_position` (see `notes/schema.md` §5a and blocker B3): 53 players labelled
`EDGE` in games attended the Combine as `DB`, and 440 get-off reps sit on rows
labelled `CB`/`SLOT_CB`. No resolution rule exists in `TASK.md`.

## c. Can pre-snap depth and snap time be derived?

**Snap time: yes, directly.** `game_tracking.event == 'ball_snap'` is an explicit
event. Present in 12,606 of 12,638 sample plays (99.7%). No inference needed.

**Depth: yes, from metadata — and it does not depend on the tracking sample.**
The line of scrimmage is computed from `yardline_side`, `yardline_number` and
`play_direction` (formula in `notes/schema.md` §6), then depth is
`|x_at_snap − LOS_x|`.

Validation on the sample (`UNVALIDATED SAMPLE OUTPUT`): median depth **0.63 yd**,
p95 **1.24 yd**, **100%** of 2,915 DL reps inside [0, 5] yd. A wrong LOS formula
produces depths of tens of yards, so this is a sharp pass, not a soft one.

**This is the most consequential number in the audit.** A rusher starts ~0.6 yd
off the ball. The "physical time" that Phase 2 must supply is therefore the time
to cover **0.5–2.3 yd**, i.e. pure acceleration from a stance. That is precisely
the regime where a parametric speed curve is least identifiable, which is why
`TASK.md` insists on a model-free interpolation and treats the exponential fit as
a cross-check only.

**Stance: not labelled.** No column in any file matches `/stance/`. `TASK.md`
asks whether stance is "labeled … or inferable from pre-snap speed and position".
It is not labelled. Whether it is *inferable* has not been established, because
validating an inference needs a labelled subset and none exists here. Recorded as
**PENDING**, not answered.

## d. Exact definition of `player_get_off`

**Documentation status: NOT AVAILABLE.** No data dictionary ships with the files,
and the Kaggle pages are client-rendered (`notes/rules.md` §1). `TASK.md`'s
working definition — *"seconds from snap until the rusher crosses the line of
scrimmage"* — is the definition this project uses, and it is **not confirmed
against an official source**. This is recorded as a standing risk, not a solved
question.

What the data itself shows:

| observation | value | consequence |
|---|---:|---|
| max observed value | 2.0 s | a hard cap |
| reps exactly at the cap | 2 of 43,562 | censoring is negligible |
| empirical check vs tracking (sample) | see below | supports the working definition |

Empirical check, 1,912 DL snaps in the sample where an onset was detectable,
comparing the label against two tracking-derived crossings
(**UNVALIDATED SAMPLE OUTPUT**):

| projection | median absolute difference | median signed difference |
|---|---:|---:|
| along the rusher's own travel direction | 0.679 s | +0.283 s |
| along the x axis (the naive "crosses the line") | **0.383 s** | — |

The **x-axis crossing reproduces the label better than the along-travel
crossing**. That is evidence that `player_get_off` is measured as crossing the
line of scrimmage, not as travelling a fixed distance along the rusher's path.

The residual ~0.38 s is not random: onset detection needs two consecutive frames
above threshold, so a tracking-derived crossing starts **late** by roughly
0.1–0.2 s, and the crossing is then interpolated between 10 Hz frames. The sign
is consistent with a measurement lag. **Any snap-to-onset latency measure
inherits this lag** and must be reported with it (`notes/limitations.md`).

## e. Frames in the first second

| setting | sampling | frames in first second after onset | frames per 40yd attempt |
|---|---|---:|---|
| Combine | 10 Hz | 10 | 49 / 53 / 60 (min/median/max) |
| Game (sample) | 10 Hz | 10 | n/a (10 pre-snap frames median) |

Both trackers are on the **same 10 Hz grid** (verified: 1,033,962 consecutive
gaps of exactly 100 ms in the game sample and 100 ms in the Combine files).

**Onset detectability at the relevant scale.** A first yard at 8 yd/s takes
0.125 s — **1–2 frames**. The first second contains **10 samples**. So the onset
threshold is evaluated at the same resolution in both settings, which is what
`TASK.md` item (e) asks for, but the resolution is coarse: a threshold that fires
on frame *k* vs frame *k+1* moves the onset by 0.1 s, which is ~10% of a typical
get-off.

**Verdict: structurally PASS, empirically PENDING.** The *definition* can be
applied identically in both settings. Whether the *same threshold value* is
appropriate in both is a calibration question that Phase 2 must answer with
global offsets — deliberately not tuned here.

## f. Null semantics and label provenance

On the 43,562 DL rows carrying a `player_get_off`:

| column | non-null | null | null share |
|---|---:|---:|---:|
| `quick_pressure` | 3,378 | 40,184 | **92.2%** |
| `time_to_pressure` | 3,418 | 40,144 | 92.2% |
| `time_to_qb_hurry` | 3,488 | 40,074 | 92.0% |
| `unblocked_pressure` | 32,977 | 10,585 | 24.3% |

Cross-tab of the 3,418 rows that have a `time_to_pressure`: 2,334 are
`quick_pressure = false`, 1,044 are `true`, and 40 have a pressure time with a
**null** flag. The flag and the time are the same event field.

**`quick_pressure` cannot be used as a complete binary outcome.** Null is not
`false`; treating it as `false` would invent 40,184 negatives. This is blocker
**B1**.

**Provenance.** The file carries `peak_pressure_probability_allowed`,
`pressure_allowed`, `time_to_pressure_allowed` alongside `quick_pressure`. That
family is the signature of a **model-derived pressure label**, not a direct
function of the rusher's own tracking — and crucially it is not obviously derived
from the *rusher's* tracking at all, which would be circular. There is no
row-level provenance column to prove it. Logged as a threat.

## g. Kill-test statistics — PENDING FULL RUN

All of item (g) is **scripted and executes**; `TASK.md` marks it full-data only
and the gate stays PENDING. The numbers below come from the **complete label file
`player_play.csv` (1,001 of 1,002 games)**, not from the tracking sample, so they
are real — but they are *provisional* because (i) `TASK.md` requires a full-data
run to gate on them, and (ii) this split-half is unadjusted for pre-snap depth and
opponent.

| quantity | value |
|---|---:|
| split-half ICC of player mean get-off, odd vs even games | **0.634** |
| split-half Pearson *r* | 0.635 |
| Spearman-Brown stepped-up reliability | 0.777 |
| between-player SD of mean get-off | 0.176 s |
| within-player SD of rep get-off | 0.279 s |
| between-player share of total variance | **0.285** |

Reading, conditional on full-data confirmation: player mean get-off is reliable
above the 0.5 threshold, the between-player SD (0.176 s) is real but smaller than
the within-player SD (0.279 s), and ~29% of raw get-off variance is between
players. That 29% is an **upper bound** on what a physical model could explain —
it is not adjusted for depth, and depth is the thing Phase 2 predicts from.

## h. Combine test-retest reliability

Computed on the real Combine file (this one is **not** pending: the Combine data
is complete).

| quantity | value |
|---|---:|
| DL 40-yard attempts measured | 177 |
| attempts with a detectable onset | 177 (100%) |
| ICC(1,1), onset → 1 yd | **0.502** |
| ICC(1,1), onset → 2 yd | **0.559** |
| ICC(1,1), onset → 5 yd | **0.625** |

**This is the sharpest negative signal in the audit.** The Combine measurement is
only just reliable at 1 yard (0.50) — and 1 yard is inside the 0.5–2.3 yd range
that in-game depth actually requires. Reliability improves with distance, as
expected, because timing noise is a fixed ~1 frame while the signal grows.

Consequence for Phase 4: **`phys_i(d)` carries substantial measurement error at
exactly the distances that matter.** The measurement-error / regression-calibration
step in the Phase 4 model is therefore load-bearing, not a refinement. A naive
model that treats `phys_i` as observed will attenuate `b` towards zero.

## i. Power

| quantity | value |
|---|---:|
| players meeting the 50-rep minimum | 105 |
| median reps per eligible player | 261 |
| smallest detectable between-player variance share | **0.271** |
| smallest effect of interest (design) | 0.150 |
| design resolves the smallest effect of interest? | **NO** |
| model-based reliability at a 0.15 share, n = 261 | 0.979 |

With ~105 players, the sampling error of a variance-share estimate is
≈ 1.96·√(2/105) ≈ 0.27. A 15% share is **not distinguishable from zero at this
sample size**. Two honest options remain, and **neither has been chosen** because
both are the user's call (see open question Q1):

* raise the smallest effect of interest to ~0.25–0.30, or
* accept that a small jump share will be reported as "interval includes zero".

Note the two numbers together: *per-player mean* reliability is excellent
(0.98 at 261 reps), but *between-player variance share* precision is limited by
the **number of players**, not the number of reps. More snaps do not help.

## Gate decision

`outputs/audit/gate.json`:

| id | condition | status |
|---|---|---|
| `combine_sprint_present` | a straight-line Combine sprint exists | **PASS** |
| `depth_derivable` | pre-snap depth and LOS derivable | **PASS** |
| `onset_consistent` | onset definable consistently in both settings | **PASS** (structural) |
| `get_off_reliability` | split-half ICC ≥ 0.5 | **PENDING_FULL_RUN** (provisional 0.634) |
| `combine_vs_get_off` | Combine-predicted time relates to mean get-off | **PENDING_FULL_RUN** (Phase 2 not implemented) |
| `snaps_per_rusher` | ≥ 50 reps for ≥ 40 rushers | **PASS** (105) |
| `matched_dl` | ≥ 40 matched DL | **PASS** (196) |

**Decision: CONDITIONAL GO** — no kill condition triggered; two conditions await
full data.

> **Update 2026-11-05 (Phase 2 run).** Phase 2 is now implemented and produces
> per-player physical time at 0.25–40 yd (`outputs/phase2/`). The
> `combine_vs_get_off` condition nevertheless stays **PENDING**: relating
> physical time to mean get-off needs the Phase 3 snap table and a full run, not
> Phase 2 alone. The audit's gate artefact is historical and was not rewritten.

No `FAIL` is blocking, but three rows elsewhere carry `FAIL` and must be read as
constraints, not as go/no-go:

* `quick_pressure` is not a complete binary outcome → blocks the Phase 4
  secondary test until a null policy is agreed (B1).
* the design cannot resolve the 15% smallest effect of interest → Q1.
* (the `FAIL` on `share of depths` seen in earlier drafts is gone; the LOS check
  now passes at 100%.)

## What this audit did **not** establish

* Whether the onset threshold *value* should differ between the Combine and the
  snap. Calibration is Phase 2 and must not be tuned on the sample.
* Whether stance is inferable from pre-snap kinematics.
* Anything about the 2024 and 2025 seasons from tracking: the sample is 2023 only.
* The exact official definition of `player_get_off`.
* Any between-player variance share that is adjusted for depth — that is Phase 4.
