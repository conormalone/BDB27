# Phase 1 audit — Turn Coverage and the Cut Card

**Binding spec:** `TASK.md` @ `52015cd`. **Run:** `python src/01_audit.py --mode sample`,
2026-11-06, provenance `PLACEHOLDER_SAMPLE`. **Evidence:** `outputs/audit/`
(`audit_items.csv`, `gate.json`, and one CSV/JSON per item).

## Verdict: CONDITIONAL GO

No stop condition fired on the data available here. **Combine tracking is the
complete file** (463,189 rows, 510 players, 2023–2025); the **game tracking is a
row sample** (333 games, 4,228 plays, 14,614 play-player pairs, 154 players), so
every game-side number below is a sample diagnostic and carries
`PENDING FULL RUN`.

DL stays the primary group: DL have direction changes in **13 drills**. The
detector validates against drill geometry at **precision 0.99/0.92, recall
0.96/0.93** (shuttle/3-cone), and derived heading and speed agree with the
provided fields far inside tolerance (heading MAE **1.08°**, speed relative error
**2.2%**). Conditions on the GO:

1. `notes/prereg.md` must fix the smoother (**`savgol_g9_p3`**, chosen by the
   diagnostics in item e), the turn/plant definitions, bin edges, envelope form,
   coverage formula and thresholds **before any full run**.
2. Reliability, entry-speed overlap and turns-per-player must be re-checked on
   full game tracking. **Warning:** the audit's provisional capacity proxy has
   **negative split-half reliability in the Combine (ICC −0.183, 484 players)**.
   That is a red flag for the Phase 3 envelope parameter, not a verdict on it.
3. Outcome null semantics (blocker B1) and the missing opponent ID (blocker L1)
   must be resolved or dropped and stated.

Full detail in `outputs/audit/gate.json`.

---

## (a) Combine drills per position, and does DL have direction changes? — **PASS**

`drills_by_position.csv`. A drill counts as containing direction changes when
≥50% of its attempts contain ≥1 detected turn (≥30°) and ≥10 players ran it.

**DL: 13 drills qualify** — PASS_RUSH_DRILL (118 players / 225 attempts),
FOUR_BAG_AGILITY_DRILL (112/112), FRONT_START_WAVE_DRILL_AND_LATERAL_REACTION
(112/112, 3.41 turns per attempt), RUN_AND_CLUB_DRILL (112/113),
BODY_CONTROL_DRILL (111/113), RUN_THE_HOOP_DRILL (109/119, 3.42 turns/attempt),
BACK_PEDAL_AND_REACT (59/62), plus the 3-cone and shuttle.

The two standardised change-of-direction drills are **thin for DL**: only
**45 DL** have `THREE_CONE_DRILL` tracking (60 attempts, 1.33/player) and **38**
have `SHORT_SHUTTLE` (44 attempts, 1.16/player). DL-specific agility drills are
where the depth is. `FORTY_YARD_DASH` correctly shows **0 turns** — a useful
specificity check.

The primary group therefore stays **DL**; the WR/TE fallback is not triggered.
Class years covered: 2023–2025 for every major drill.

## (b) Time and frame fields — **PASS**

`schema_verified.json`, `schema_compare.csv`.

* Combine: 424,784 player frame steps, **all exactly 0.1 s**, 0 off-grid, 0 duplicate
  player frames. 32,095 `BALL` rows share the player's `nfl_id` and duplicate his
  timeline — excluded everywhere. Frames per attempt: median 51 (p05 24, p95 145).
* Game sample: 1,033,963 frame steps, 1 off-grid (float rounding), 0 duplicates,
  0 gaps > 0.2 s.
* `dir`: 0–360°, 0 = +y, clockwise; measured offset against the velocity heading
  over 3,445 moving frames is **0.00°** (circular SD 0.17°, 100% within 5°).
* **The spec's "no speed or acceleration in Combine" claim is refuted**: `s` and
  `a` exist and are populated. They are used only as cross-checks.

## (c) Derived kinematics vs provided fields — **PASS**

`kinematics_crosscheck.csv`, `dis_reconstruction.csv`. Primary smoother, all drills:

| provided speed band | frames | speed bias (yd/s) | speed MAE (yd/s) | MAE / provided | heading MAE (deg) | accel bias (yd/s²) |
|---|---:|---:|---:|---:|---:|---:|
| 0–2 | 87,358 | +0.040 | 0.089 | 8.4% | 7.32 | −1.79 |
| 2–4 | 91,162 | +0.039 | 0.065 | 2.2% | 1.08 | −2.43 |
| 4–6 | 75,759 | +0.028 | 0.046 | 0.9% | 0.47 | −1.84 |
| 6–8 | 107,999 | +0.016 | 0.032 | 0.4% | 0.30 | −1.92 |
| 8–12 | 68,816 | +0.011 | 0.025 | 0.3% | 0.17 | −0.99 |

No lag (0 frames). Derived speed is unbiased; derived **acceleration is biased low
by ~2 yd/s²** (smoothing attenuates the second derivative) — a known consequence,
recorded so Phase 3 does not interpret it as physics.

`dis` is **not** a distance in yards: `30·√dis` is an exact integer in **47.5%** of
attempts (68.9% for the 40-yd dash) — consistent with `dis = (whole_yards/30)²` —
but the implied distance is 0.61–3.10× the path length depending on the drill.
**Neither confirmed nor refuted:** the "dis under-counts" claim. Blocker B2.

## (d) Detector validation against drill geometry — **PASS**

`detector_geometry.csv`, `cone_estimates.csv`. Cone positions are **estimated**
from detection density on half the attempts (even `event_id` hash) and evaluated on
the other half, so precision is not circular. Turn targets use the geometry the
drill imposes: a **point** for the 3-cone (run around cones) and a **line** for the
shuttle (touch a line 5 yd away, lateral position free).

| drill | cones | turns/attempt | precision | recall | point-model precision | angle error vs geometry |
|---|---|---:|---:|---:|---:|---:|
| SHORT_SHUTTLE | 2/2 (7.3 yd apart) | 2 | **0.991** | **0.959** | 0.711 | **2.6°** vs 180° |
| THREE_CONE_DRILL | 3/3 (min separation 1.8 yd) | 3 | **0.915** | **0.931** | 0.915 | **49.4°** (n=66) |

Config thresholds: precision ≥ 0.80, recall ≥ 0.70.

Two honest caveats. (i) The 2-D point-model precision for the shuttle is 0.711 and
is reported alongside: a line drill's lateral coordinate is unconstrained, which is
why the line model is the right one, but the number is there for the reader.
(ii) The **turn *angle* is the weakest measured quantity**: the median error against
the geometric cone-triple angle is 49°, because a serpentine turn is partly rounded
and the 10 Hz heading at low speed is noisy. Precision/recall (whether a turn is
found where the geometry says one is) are strong; angle is not, and Phase 2/3 must
carry that uncertainty.

Detector mechanics matter: a fixed 0.5 s measurement window records a 180° plant as
a ~30° turn, because at 10 Hz heading is undefined as speed passes through zero
(ω spikes to −860°/s at the plant). The detector therefore segments from the
approach speed maximum to the exit speed maximum.

## (e) Smoothing diagnostics — **PASS**, primary = `savgol_g9_p3`

`smoothing_diagnostics.csv`, 793 forty-yard attempts:

| smoother | accel-cap exceedance | monotonicity (accel phase) | peak speed | cross-smoother RMS | eligible |
|---|---:|---:|---:|---:|---|
| savgol_g5_p2 | 0.000 | 0.987 | 10.638 | 0.032 | yes |
| savgol_g7_p3 | 0.000 | 0.992 | 10.604 | 0.020 | yes |
| **savgol_g9_p3** | 0.000 | **0.994** | 10.603 | **0.017** | **yes — recommended** |
| savgol_g11_p3 | 0.000 | 0.994 | 10.592 | 0.022 | yes |
| kalman_cv | 0.000 | 0.949 | 10.570 | 0.263 | yes |
| spline_cubic | 0.000 | 0.993 | 10.518 | 0.387 | yes |

The acceleration cap (15 yd/s²) never binds on straight sprints, so the choice is
made on **monotonicity of the acceleration phase and cross-smoother agreement**,
never on downstream results. Kalman and the spline disagree with the ensemble by
0.26–0.39 yd/s — they move the speed curve too much. `savgol_g9_p3` is the config
primary from this cycle and must be frozen in prereg.

## (f) Matched players, snaps, games — **PASS**, per-player counts PENDING

`matching.csv`. All 510 Combine players match `players.csv` by `nfl_id`
(DB 122, DL 118, OL 121, WR 107, TE 42). **269 of them appear in the game-tracking
sample** (DL 94). Season-wide `player_play` snap counts exist but are **role rows,
not participation**: median 2 players per play. DL pass-rush rows 43,312, coverage
6,790 for DB… and **0 route rows for DL**, which is the expected pattern.

`games.csv` covers 2023–2025 (334 games each). The tracking sample is 2023 only, so
per-player games-in-sample is not a season count.

## (g) Turn counts and entry-speed overlap — **PENDING FULL RUN**

`entry_speed_overlap.csv`. Combine (complete file): **5,536 turns, 508 players,
median 10 turns/player** (p10 = 4). Game sample: 7,143 turns, 153 players.

| position | Combine entry p5/p95 (yd/s) | game sample p5/p95 | game turns inside Combine range | histogram overlap |
|---|---|---|---|---:|
| DL | 2.69 / 5.72 | 2.14 / 5.19 | 72.8% | 0.807 |
| DB | 2.60 / 7.42 | 2.19 / 6.24 | 84.2% | 0.936 |
| WR | 3.05 / 8.34 | 2.60 / 7.68 | 90.3% | 0.726 |
| TE | 2.78 / 7.24 | 2.44 / 6.69 | 86.4% | 0.767 |
| OL | 2.60 / 5.19 | 2.07 / 4.23 | 60.6% | 0.834 |

The sample suggests a **usable overlap** (threshold 0.50) with game speeds slightly
lower than Combine speeds, but this is **not a finding**: it is one season's row
sample. The full-run question is whether the *observed* cell coverage is enough to
avoid extrapolating most of the demand map.

## (h) Frame resolution and detection sensitivity — **PASS**

`frame_resolution.csv`. A detected turn spans **20 frames** (p05 10, p95 20) at
10 Hz — between one and two seconds of motion, so the turn core is well sampled.
Turn counts on cone+shuttle attempts move with the trigger window:
287 (0.3 s) → 771 (0.4 s) → **1,047 (0.5 s)** → 1,124 (0.6 s) → 1,162 (0.7 s).
The 0.5 s choice sits on the plateau; the window must be frozen in prereg and the
sensitivity reported with every result.

Heading precision on straight-line sprints (median absolute deviation from the
in-band heading, acceleration phase): **21.7° below 1 yd/s, 5.2° at 1–2, 9.3° at
2–3, 0.41° at 4–6, 0.35° at 6–9**. Heading is not usable below ~2 yd/s, which is
why the speed floor is 2.5 yd/s. Note that many Combine plants decelerate to
0–1.5 yd/s at the cone: the floor excludes the plant *itself* and measures the
approach and exit speeds, which is the capacity quantity Phase 2 defines.

## (i) Game-only orientation checks — **PENDING FULL RUN**

`orientation.csv`. `o` exists in game tracking, 0–360°, continuous. At steady
running (> 4 yd/s) the circular median of `o − dir` is **3.98°**, i.e. the same zero
reference and rotation as `dir`. Share of steady frames with |o − dir| > 60°:

| position | share | snapshot type | share |
|---|---:|---|---:|
| DL | 0.063 | PASS_RUSH | 0.058 |
| DB | 0.250 | COVERAGE | 0.297 |
| WR | 0.167 | ROUTE | 0.171 |
| TE | 0.197 | OTHER | 0.157 |
| OL | 0.107 | | |

Direction of the effect is exactly what the spec predicts: pass-rush (forward
movement) is clean; coverage and routes carry backpedal/shuffle contamination. The
shares are sample diagnostics only.

## (j) Kill-test statistics — **PENDING FULL RUN**, with a warning

`killtest_stats.csv`, `smoother_sensitivity.csv`.

| setting | turns | players | median turns/player | share ≥ 10 turns | split-half ICC (peak lateral accel) |
|---|---:|---:|---:|---:|---:|
| Combine (complete) | 5,536 | 508 | 10 | 53.9% | **−0.183** (484 players) |
| Game (sample) | 7,143 | 153 | 32 | 82.4% | 0.262 (147 players) |

* The **Combine-game correlation and the noise ceiling are PENDING FULL RUN** —
  the game side is a row sample.
* **The negative Combine ICC is a genuine warning.** The proxy (90th percentile peak
  lateral acceleration, split by alternating turns within the same drill) does not
  repeat within a player in the Combine. Possible reasons: two turns per drill is
  too few for a stable estimate; drills differ in geometry; the proxy is the wrong
  parameter. The prereg's capacity parameter (Phase 3 envelope) must be tested for
  reliability **before** the full run, and a value below 0.50 in either setting is
  a stop condition.
* Smoother sensitivity of the turn count (cone+shuttle): 1,064 / 1,047 / 1,047 /
  1,051 across SG5/7/9/11, 934 Kalman, 856 spline — the count is stable to ±2% across
  SG windows and falls ~10–18% for the two non-local smoothers.

## (k) Outcome labels — **PASS with a blocker**

`outcome_labels.csv`, `label_provenance.json`. For DL: `quick_pressure` is TRUE on
**405** of 23,604 rows and NULL on **94.3%**; `unblocked_pressure` and
`time_to_pressure` follow the same event-record pattern (populated together).
Null semantics are undocumented → treating null as false manufactures negatives
(blocker B1). WR, TE and DB have the same labels available (null share > 99% for
WR/TE, as expected — those labels are defensive).

**Provenance probe:** `player_get_off` could **not** be reproduced from the sample's
own tracking (first movement after `ball_snap`): 1,960 matched reps, median
difference **−0.753 s**, 0% within 0.15 s. So the probe is **inconclusive** — it
neither confirms nor refutes that a label is derived from the player's own chips.
Recorded as an open question, not a clean result.

## (l) Rusher–blocker / opponent IDs — **FAIL (blocker L1)**

`opponent_columns.json`. `player_play` has **no opponent or blocker assignment
column**. `pass_rushers_encountered` is a count, not an ID. The "random effects for
assigned opponent" term therefore has no data and must be dropped or replaced by a
team-game effect before prereg.

## (m) Power — **PASS**

`power.csv`, `power.json`. Design calculations over an n grid (never on the sample),
α = 0.05, power = 0.80:

* smallest detectable transfer correlation: **r = 0.591 at n=20, 0.492 at 30,
  0.431 at 40, 0.355 at 60, 0.309 at 80, 0.253 at 120**;
* smallest detectable held-out AUC gain (baseline AUC 0.65, single-arm
  Hanley–McNeil approximation): 0.251 at 20 events, 0.141 at 60, 0.099 at 120.

Read the full-data row off the grid when the real n is known. If DL matched players
with enough turns number ~80, a transfer correlation below ~0.31 is not detectable —
that is the number the prereg's smallest effect of interest must be set against.
