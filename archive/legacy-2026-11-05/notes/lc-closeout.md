# Closeout — leverage × ceiling task (WR/TE)

**Status: CLOSED 2026-11-01.** Superseded 2026-10-30 by the DL acceleration-ceiling
task (`TASK.md`, D68); the spec, prereg and deviation log are archived under
`notes/archive-tasks/`. This note is the record of what was tested, what was
found, what could not be measured, and where the assets went. It exists so the
work is not repeated and so the DL entry can cite it.

**Provenance of the numbers below.** The Phase 4 remainder figures are from the
single **FULL** run executed by the operator on 2026-11-01
(`outputs/game/lc/phase4`, 52,184 efforts · 143 players · 967 games). The
artefacts committed on this host under `outputs/game/lc/` remain sample-mode and
stamped `PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT`; the full run's own
files live on the operator's machine. Earlier chain results (gate, Phase 2
reliability, Phase 5 validation) were only ever produced in sample mode on the
Pi and are **not** findings.

---

## 1. What the task asked

Whether a player's effort on a route — max speed relative to his own
Combine-derived ceiling — falls when leverage (pre-snap win probability for the
offence) is low, and whether that shortfall is larger for players with less
headroom beneath their ceiling. The mechanism of interest was ceiling-binding:
a receiver already near his physical limit has less room to absorb a
low-motivation snap than one with headroom.

That is a causal question about effort, not a descriptive question about speed.

## 2. What was found (FULL run)

**Matched design — a tight null.**

- 5,957 within-player, within-exact-stratum pairs (route family × depth bin ×
  quarter × load bin), 120 of 143 eligible players.
- Matched estimate **−0.0003** log-speed (player-clustered bootstrap SE 0.0026,
  95% CI [−0.0053, +0.0049]). The interval excludes any effect larger than
  about half a percent of max speed.
- The same 11,914 matched rows refitted with player fixed effects and the nine
  controls give **−0.0049 per within-player SD**, which scales to **−0.0108**
  high-minus-low over the observed 2.2161 SD leverage gap.

The same rows, the same contrast, a thirty-fold difference. The gap is produced
entirely by the adjustment — so the adjusted estimate rests on the control set
and on linearity in leverage, and the unadjusted like-for-like comparison finds
nothing.

**Reach of the matched sample.** 2,736 strata matched; 9,323 one-sided strata
discarded; 2,794 high and 5,299 low efforts unmatched; 23 eligible players
contribute no pair. The surviving pairs are roughly 23% of the effort table, and
high-leverage efforts are structurally the ones least likely to have a
like-for-like twin. A null here is a statement about the overlap population, not
about the whole leverage range.

**Headroom moderation — null on all three moderators.** 119 players, 36,964
comparison rows, 24 players excluded as not estimable, baseline from an
independent half of low-leverage efforts.

- baseline utilisation: naive +0.0211 (SE 0.0293), λ 0.736, corrected +0.0287
  (SE 0.0398).
- vmax: naive −0.0003 (SE 0.0020), λ 0.963, corrected −0.0003 (SE 0.0021).
- tau: naive −0.0014 (SE 0.0050), λ 0.967, corrected −0.0015 (SE 0.0052).

The theoretically loaded moderator corrects upward by 36% and is still null;
the other two barely need correction and are null with intervals tight enough to
exclude anything material. The ceiling-binding story does not replicate.

**Ceiling bound check — passes.** 44,597 efforts carry a ceiling (7,587 do not).
Exceedance (effort fraction > 1.0): high bin 0.0003 (14,710), low 0.0000
(15,118), medium 0.0000 (14,769), overall 0.0001. Maximum predicted fraction at
high leverage 0.9735. The shrunken ceiling never predicts an impossible speed,
which is a positive statement about how the ceiling was built and survives
independently of the leverage question. Caveat: the observed maximum is 1.3918 —
a thin tail (≈4–5 efforts) sitting 39% above its own ceiling, consistent with
under-shrunk vmax or tracking error on those rows.

**Dose-response — the only p < 0.05, and weak.** Low reference n 17,395; medium
−0.0042 (SE 0.0021); high −0.0075 (SE 0.0029); monotone decreasing; trend
−0.0037 per bin (SE 0.0015, p 0.0131). Three limits: it is the adjusted
specification the matched design contradicts; p 0.0131 is unadjusted across a
battery of a dozen-plus contrasts; and −0.75% of max speed is ≈0.07 yd/s on a
9 yd/s player, below anything practically meaningful.

**Per-player slopes — withheld by the reliability gate.** Reliability 0.211
against the 0.50 threshold, 142 eligible players. Per-player heterogeneity is
not measurable at this sample size, so no per-player table was emitted and no
claim about individual players is supportable.

**TE replication — consistent, not established.** −0.0042 (SE 0.0031, 95% CI
[−0.0105, +0.0021]), 10,023 efforts / 38 players, over the 30-player
confirmatory-scale threshold. Same sign and magnitude as the main effect;
interval still admits zero.

**Not run — defender distance.** No defender-distance field is written by
`04_efforts` and the tracking table carries positions only, with no defender
attribution. Reported NOT RUN rather than proxied.

**Run-to-run instability.** On the 2,993-effort sample the matched estimate was
−0.0204 and dose was non-monotone (+0.0024, p 0.60); on the FULL run matched is
−0.0003 and dose is monotone −0.0037. The two headline numbers exchanged places
between runs, which places both in the range where composition decides the sign.

## 3. What was not done, and why

The one diagnostic that would have resolved the §2 contradiction cheaply — the
distribution of within-pair leverage gaps — was not run. If most pairs sit at a
small gap, the equal-weight pair mean is diluted toward zero while the
regression extrapolates across 2.2 SD, which would explain the discrepancy
without new modelling. It is recorded here rather than performed, because the
task was already retired and the diagnostic changes the explanation, not the
conclusion.

## 4. What is claimed, and what is not

**Claimed (descriptive):** in adjusted comparisons, in-game max speed falls by
roughly 0.4–0.8% per leverage bin; the within-stratum within-player matched
comparison does not reproduce that gradient; per-player heterogeneity is
unmeasurable at this n; the Combine-derived ceiling is never breached at a
material rate.

**Not claimed:** that low leverage causes players to run slower; that ceiling
headroom moderates effort; that any individual player is more or less
leverage-sensitive; that the dose gradient is a physiological effect.

## 5. Assets carried into the DL task

- **The shrunken Combine ceiling curve** (vmax, tau, a0 per player) — the same
  features that form the exposure in the DL task's P1, already fitted and
  reliability-screened.
- **The ceiling-bound exceedance check** — reusable as a methods sentence and as
  a guard against a ceiling that manufactures headroom.
- **The matched-design machinery** (CEM within player, seeded pairing, clustered
  bootstrap, unmatched counts reported) — generic, and the pattern to copy for
  any future like-for-like contrast.
- **The reliability-gated slope pattern** — the reason not to report per-player
  slopes without a gate, retained as a worked example.

## 6. Disposition

No further analysis on this line. The leverage × ceiling question is answered as
far as this data can answer it, and the remaining work belongs to the DL
acceleration-ceiling task, where the ceiling is a denominator rather than the
claim. Reopening requires a new question, not a new model.
