<!-- provenance: MIXED — definitional conclusions are FULL; every yard/second number is UNVALIDATED SAMPLE OUTPUT -->
# Crossing diagnostic — is the tracked crossing wrong, or is `player_get_off` a different quantity?

**Question (Conor, 2026-11-05 13:36).** On the 1,819 primary snaps where both are
available, the tracked crossing of the line of scrimmage precedes
`player_get_off` by median −0.379 s (p10 −0.664, p90 −0.191, SD 0.205), and the
label's own SD across reps is 0.268. The disagreement is nearly as large as the
whole signal and is not a constant. Which quantity is wrong, or why do they
differ? No calibration offset may be used to paper over it.

**Provenance.** Every number below is computed from
`game_tracking_2023_sample.csv` (one season, 154 players, 44 with a usable rep)
joined to the complete label file. It is therefore **UNVALIDATED SAMPLE
OUTPUT** and must never be presented as a finding about the full data. The
*definitional* conclusions — which quantity each measurement is — are marked
separately, because they do not depend on the sample.

Reproduce: `python src/crossing_diagnostic.py --mode sample` (34 s,
206 MB peak RSS, byte-identical on re-run). Artefacts in `outputs/crossing_diagnostic/`. The run reproduces the
brief's numbers exactly (1,819 reps; median −0.379; p10 −0.664; p90 −0.191).

---

## Verdict in one paragraph

> **The label is not what the task assumes. Our crossing is not wrong.**
>
> Phase 3 computes the crossing of the line of scrimmage derived from
> `yardline_side` / `yardline_number` / `play_direction`, at the tracked point.
> That formula, its sign convention and the depth sign are correct, and the
> error attributable to the line itself is bounded at ≈0.3 yd (≈0.15 s) — under
> half the gap. `player_get_off` behaves instead as the crossing of a plane
> displaced **past** the line by a **distance**, not as a clock offset: the
> median displacement is **0.86 yd** (sample), it is a plane crossing (a paired
> within-play slope of implied distance on depth of **0.99 ± 0.11** where a
> fixed first-step distance would give 0), and it is a **play-level** quantity —
> two rushers on the same snap agree about the plane to 0.06 yd while different
> plays differ by 0.23 yd between them. A constant time offset leaves the whole
> 0.205 s scatter untouched; a plane at +0.75 yd cuts the RMSE from 0.455 s to
> 0.167 s, which is the noise floor implied by the integer-yard ball spot.

---

## What was computed

For every rep with a tracked crossing and a label (1,819 sample rows; 1,368 in
the primary set), from the rep's own 10 Hz track and the same primitives Phase 3
uses (`physical.time_at_distance`, linear interpolation, snap frame as origin):

* `t_cross` — the crossing of the derived line (identical to Phase 3's column:
  max |difference| **0.0000 yd** on the LOS and **0.0000 yd** on depth, i.e. the
  diagnostic and the pipeline agree exactly);
* `d_star` — the **distance the label actually spans**: the trajectory is
  inverted at `t = player_get_off` on a 0.05 yd grid, giving the x-displacement
  at the moment the label says the rusher has "got off";
* `plane_offset = d_star − depth` — how far past the line the label's crossing
  sits, in yards, per rep.

Everything else is a statistic of those two columns plus independent checks from
the play metadata.

---

## Hypothesis 1 — a line-of-scrimmage error · **REJECTED**

The line cannot be checked against the ball: **the ball is not in the tracking
sample** (all 154 tracked `nfl_id`s are players in `players.csv`; there is no
ball row). The independent check available is the offensive line, whose body
centres must sit just behind the ball:

| check | n | value | reading |
|---|---:|---:|---|
| offensive players on the offensive side of the derived line | 7,002 | **99.67%** | the formula and its sign are right |
| defensive players on the defensive side | 5,420 | **97.69%** | ditto (the 2% are motion/formation cases) |
| centre body-centre offset from the line (yd) | 410 | **−0.61** | a snapper's box centre sits behind his hands |
| guard offset (yd) | 2,253 | −1.15 | deeper, as guards are |
| tackle offset (yd) | 1,785 | −1.37 | deepest, as tackles are |
| pooled within-player SD of the offset (yd) | 30 OL | 0.353 | per-play spot error + own alignment |

The ordering C < G < T is the expected body geometry and would be destroyed by a
formula error; a wrong branch of the formula puts the line 100−2n yards away and
would leave almost no player on the expected side. **A systematic line error is
bounded at ≈0.3 yd**: for the recorded line to be 0.86 yd deeper than the truth,
the snapper's bounding-box centre would have to sit 1.47 yd behind the ball, which
his own body geometry (hands on the ball, ≈1.7 yd from fingertips to heels)
excludes.

**Time equivalent.** At the median crossing speed of 2.05 yd/s, 0.30 yd is
**0.146 s** — **39% of the 0.379 s gap**, below the pre-declared 50% bar
(`diagnostic.min_share_of_gap_explained`). The line is not the answer.

**What the line check *does* explain is the scatter.** The recorded
`yardline_number` is an integer; the true ball spot is fractional and is not in
the file (every yardline field is integral, and successive plays' line changes
are integral, so the fraction cannot be recovered from the release). A paired
decomposition gives a **true** depth variation of 0.116 yd (within play, where
the line cancels) against a **between-play** variation of 0.290 yd — consistent
with a uniform ±0.5 yd spot rounding (SD 0.289) and **2.5× the true variation**.
That is 0.14 s of time noise, which is the size of the plane model's residual.
So the integer yardline explains why any per-rep comparison is noisy; it cannot
explain a bias of one direction.

## Hypothesis 2 — a direction or sign error · **REJECTED**

| check | n | value |
|---|---:|---:|
| defensive reps whose first-step x-displacement is against the offence | 1,819 | **99.89%** |
| reps with non-positive depth | 1,930 | 5.6% (all near zero, min −0.67) |

A mirrored axis, a flipped `play_direction` mapping or a mis-signed depth would
make the depth non-positive for essentially **every** rep (the rusher would be
measured as moving away from the line) and would invert the 99.89%. It does not.
The four side/direction branches of the formula are each checked against known
field positions in `tests/test_crossing_diagnostic.py`, and the depth sign is
tested for a defender on the defensive side and an attacker behind the line.

## Hypothesis 3 — the crossing reference point · **CONFIRMED (this is the explanation)**

Candidate definitions, scored against the label (residual = candidate − label):

| definition | parameter | n | median (s) | SD (s) | RMSE (s) |
|---|---:|---:|---:|---:|---:|
| line crossing (what Phase 3 computes) | 0.00 yd | 1,819 | −0.379 | 0.205 | **0.455** |
| line crossing + fitted clock offset | −0.406 s | 1,819 | +0.028 | 0.205 | 0.205 |
| plane past the line | +0.75 yd | 1,795 | −0.044 | 0.162 | **0.167** |
| fixed x-displacement from the snap | 1.50 yd | 1,792 | +0.004 | 0.129 | 0.130 |
| leading edge of the body | −0.50 yd | 1,819 | −0.716 | 0.284 | 0.799 |

Three things follow.

1. **It is a distance, not a clock.** The gap correlates **+0.57** with the
   crossing speed, which no constant offset can produce, and the fitted clock
   offset leaves the residual SD at 0.205 s — i.e. removes none of the scatter.
   A plane at +0.75 yd cuts the RMSE by 63% (0.455 → 0.167 s).
2. **It is a plane, not a fixed first step.** On the 22 snaps with two tracked
   rushers, removing the play mean gives a slope of **implied distance on depth
   of 0.989 ± 0.114**. A plane gives 1; a fixed 1.5 yd displacement gives 0. The
   play mean removes the line error exactly, so this estimate is free of the
   errors-in-variables that afflicts any across-play regression.
3. **The 1.50 yd fixed displacement wins on RMSE but is an artefact.** Its lower
   RMSE (0.130 vs 0.167) comes from *dropping* depth: the recorded depth is 2.5×
   noisier than the true depth (0.290 vs 0.116 yd), so ignoring it predicts
   better even when the truth is a plane. The paired test is the one that can
   tell the difference, and it does, at 9σ. This is exactly why the brief asks
   for the choice to be defensible rather than fitted: **RMSE alone picks the
   wrong definition here.**

**Is +0.86 yd defensible, or merely fitted?** Partly defensible, and not
identified. A plane offset of that size is what a **body-reference** definition
produces if the label's crossing point sits behind the tracked point: a lineman's
bounding box runs ≈1.7 yd from fingertips to heels in a stance, so its rear edge
is ≈0.85 yd behind its centre. That reading is consistent with all four
structural facts (slope 1, play-level, no height correlation, magnitude), and
with the fact that the offset does *not* scale with player height (r = −0.09) —
a pose/anchor offset need not. But **it cannot be verified**: the released files
contain no bounding box, no ball track and no data dictionary (blocker B4). What
*is* established is the sign and the structure: the label's "get off" happens a
distance beyond the line, at the same plane for both rushers on a snap.

## Hypothesis 4 — the onset definition · **REJECTED**

`cross_time_s` is computed from the snap with the snap frame as the search
origin; the onset enters only as a gate (a rep whose crossing precedes the
detected onset is flagged `CROSSED_BEFORE_ONSET` — 154 sample rows — and kept as
evidence, not repaired). The onset **cannot** move the crossing time. Empirically
corr(gap, onset latency) = **−0.13**, i.e. 1.8% of the gap's variance; the plane
offset barely moves with it either (r = −0.12). The label correlates +0.59 with
the onset because both measure how quickly the player starts — that is a shared
cause, not the onset absorbing the difference. Onset-to-cross (a different
quantity, used by Phase 4 as the in-game counterpart of Combine physical time)
is unaffected.

## Hypothesis 5 — per-group structure of the offset · **NOT A PLAYER CONSTANT; A PLAY-LEVEL DISTANCE**

Variance of the gap (seconds) and of the plane offset (yards):

| quantity | total SD | between player | between game | within player |
|---|---:|---:|---:|---:|
| gap (s) | 0.205 | 0.078 (14% of variance) | 0.144 (49%) | 0.198 |
| plane offset (yd) | 0.312 | 0.127 (17%) | 0.201 (41%) | 0.306 |

Paired design (44 reps on 22 snaps): plane offset total SD 0.232, between-play
**0.227**, within-play **0.060**. So **93% of the offset's variance is
play-level**: the two rushers on a snap agree about where the plane is
(to ≈0.06 yd!) and different plays disagree by more. A per-player calibration
therefore cannot recover it; a per-*play* plane can. Median offsets by alignment
are nearly identical — EDGE 0.85 yd (n=1,166), INTERIOR 0.88 yd (n=632) — so
alignment is not the structure either.

The offset's time expression `c / v` necessarily scales with the rusher's speed:
that is what makes it a distance. It is recoverable **in principle** (measure the
crossing at the same reference plane) but not **in this dataset** (the plane's
provenance is unidentifiable). It is *not* the case that the measurement is
unusable: the between-player distortion of a 0.86 yd distance error is small
(see Phase 2 below), because the Combine time–distance curves of these players
are nearly parallel over 0.65→1.50 yd.

---

## Which quantity to trust, and what it means for Phase 2/3

**Trust:** for "time from the snap until this player's tracked position crosses
the line of scrimmage", Phase 3's `cross_time_s` is the correct measurement, to
within one frame of interpolation and the ±0.3 yd line bound. For "the
competition's `player_get_off`", only the label is authoritative — but it is a
**different quantity from the one TASK.md assumes**: it is not a line crossing,
it is a crossing ≈0.86 yd (sample) beyond the line, per play.

**Does Phase 2 compute physical time for the right distance? No.** Phase 2
computes `phys_i(d)` with `d` = the pre-snap depth (median 0.65 yd), while the
label's clock covers a median **1.50 yd** of travel. On the Combine curves the
two differ by **0.250 s** on average (phys(0.65) = 0.342 s, phys(1.50) =
0.591 s), with a between-player SD of only 0.0115 s and a cross-player
correlation of **0.979**. In the Phase 4 regression that shows up as a
**coefficient `b ≈ 1.31`** (the slope of phys(1.50) on phys(0.65)) and an
intercept absorbing 0.25 s plus the reaction — i.e. the model would report a
"systematic difference between settings" that is really a distance
mis-specification. TASK.md asks for `b` precisely because a value far from 1 is
meaningful; this error would make `b ≈ 1.31` misleading.

**What must change.**

* **Phase 3's crossing code: nothing.** No defect was found in the LOS formula,
  the direction/sign handling, the depth convention, the body point or the onset
  gate. There is nothing to fix, so no committed artefact is invalidated by a
  code change. (The red-to-green regression tests for the LOS, the sign and the
  crossing interpolation are added anyway: `tests/test_crossing_diagnostic.py`.)
* **Phase 2's distance definition, and the prereg's "distance definition" item,
  must be aligned to the quantity the outcome actually measures.** That is a
  decision about the estimand, not a bug, and it is **not mine to make**
  (blocker B10). Options are in the next section.
* **Do not adopt 0.86 yd as a constant.** It is a sample median. If a distance
  correction is ever used it must be estimated on full data under a
  preregistered rule, and reported as a distance, never as a time offset.

## What Phase 4 should do about the offset

1. **Never a time calibration offset.** It does not fit (it leaves the full
   0.205 s scatter) and the brief forbids it for good reason: the residual is
   speed-dependent, so a constant would inject a player-specific bias.
2. **Primary (recommended, subject to Conor): keep the published label as the
   outcome and evaluate `phys` at the distance the label times.** With the
   definitional constant `c0` frozen in the prereg and estimated on full data
   (not from this sample), fit `phys_i(d_ij + c0)`; report `b` for both
   `c0 = 0` and the frozen `c0` as a paired sensitivity. If `c0` cannot be
   justified without the label (it cannot, from this release), the fallback is
   to report the decomposition with `phys(d)` and state that `b` and the
   intercept are **not** interpretable as the physics.
3. **Pre-specified sensitivity: define the outcome on the tracking instead**
   (`snap → crossing`, and `onset → crossing`) and compute `phys` at the same
   distance. This is internally consistent and free of the definitional
   mismatch, but it changes the estimand, adds the ±0.29 yd spot error (0.14 s)
   to the outcome, and needs Conor's approval. Do not do it silently.
4. **Do not substitute one measure for the other**, and do not treat
   `cross_minus_get_off_s` as a bias to be removed.

**Is `phys_i(d_ij)` unusable?** No — but it is mis-specified. The between-player
ranking survives (r = 0.979), so the *share* of between-player variance is not
destroyed; `b` and the intercept are. That is **not** a "stop the pipeline"
condition under TASK.md's stop list (the line *can* be derived, the depth *can*
be derived), but it **is** a stop-before-the-prereg-is-frozen condition, because
"distance definition" is one of the items the prereg must fix. Recorded as
**B10** in `notes/blockers.md`.

---

## Limits of this diagnostic

* Sample: one season, 44 players with a usable rep, 253 games, **22** plays with
  two tracked rushers. The paired slope (the load-bearing estimate) rests on
  those 22 plays; the sign and magnitude of the effect are unambiguous, the
  precision is not. Full data will multiply the paired design by ~20.
* The ball is not tracked, so the line is checked against the offensive line's
  body geometry, not against the ball itself. That bounds a systematic line
  error at ≈0.3 yd; it does not measure it.
* The label's provenance is unknown (B4): no data dictionary exists, and the
  interpretation as a rear-edge/body-anchor crossing is the best-supported of
  several that fit the structure, not a verified definition.
* `player_get_off` may be capped and interpolated by its producer (sample
  max 1.982, full-file max exactly 2.000); that affects the tails, not the
  median structure.
