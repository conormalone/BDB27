# Outcome test — result: no detectable link between break retention and separation

**Status: estimate reported, criterion NOT met. This is not a finding.** Written 2026-11-14.
Spec: `notes/outcome-test-spec.md` (committed 2b76348, before the first fit). Evidence:
`outputs/outcome_test/` (`summary.json`, `estimates.csv`, `attributed_turns.csv`); code
`src/bdb27/outcome_test.py`, `scripts/outcome_test.py`; tests `tests/test_outcome_test.py`.
`TASK.md`, the band table, the figure set and `notes/report-draft.md` are unchanged.

---

## The number

**On 1,731 DB 30–60° game turns from 102 players** — turns where the play carries exactly one
visible opposing route-runner with `separation_at_pass_forward` recorded `[FULL]` — a **0.10
higher `apex_retention` through the break is associated with 0.005 yd *more* separation at pass
forward, 95% CI −0.036 to +0.046 yd** (player-clustered, 102 clusters; p = 0.80; wild cluster
bootstrap p = 0.80).

Per 1.00 unit of retention the coefficient is **+0.05 yd, 95% CI −0.36 to +0.46**. The raw
correlation is 0.005. From the 10th to the 90th percentile of retention — a range of 0.64 — the
fitted change in separation is 0.03 yd, about an inch.

The sign is the opposite of the direction Conor specified, and the interval is narrow enough to
exclude anything worth reporting in either direction: **the largest effect these data can support
is 0.046 yd per 0.10 retention, roughly a quarter of the 0.20 yd effect that was pre-declared as
the floor for calling it a finding.**

### Does the outcome move at all? (yes — the estimator is not fitted to noise)

| Check | Result |
|---|---|
| separation by coverage family | MAN 2.12 yd (n=337) · ZONE 3.44 yd (n=1,091) · missing 3.05 yd (n=303) |
| separation by route | SCREEN 6.19 · FLAT 4.31 · CROSS 3.34 · HITCH 3.06 · GO 2.59 yd |
| R² of the control block without the exposure | 0.065 |
| R² with the exposure added | 0.065 |

The outcome responds to who was covering and what route was run, in the directions anyone would
expect. The retention coefficient is zero against that backdrop, not against a dead variable.

## The pre-stated criterion, and the verdict

| Test (spec §6) | Required | Observed | Met |
|---|---|---|---|
| (a) primary β₁ < 0 | negative | **+0.05** | **no** |
| (b) 95% CI excludes zero | hi < 0 | −0.36 to **+0.46** | **no** |
| (c) player-FE same sign, CI excludes zero | hi < 0 | −0.36 to +0.48 | **no** |
| (d) sign holds in MAN-only and one-per-play | both negative | MAN −0.46 · one-per-play **+0.11** | **no** |
| (e) \|β₁\| ≥ 2.0 yd per unit | ≥ 2.0 | 0.05 | **no** |

**Verdict: `ESTIMATE ONLY — OPPOSITE TO THE STATED EXPECTATION`.** No claim is upgraded.

Secondary and sensitivity estimates (all `outputs/outcome_test/estimates.csv`):

| Specification | β (yd per unit retention) | 95% CI | n |
|---|---:|---|---:|
| **Primary** — OLS + controls, player-clustered | **+0.05** | −0.36 to +0.46 | 1,731 |
| Player fixed effects (within-player) | +0.06 | −0.36 to +0.48 | 1,731 |
| MAN-only subsample | −0.46 | −1.34 to +0.41 | 337 |
| One attributed turn per play | +0.11 | −0.30 to +0.51 | 1,472 |
| Pass plays only (C/I) | +0.07 | −0.36 to +0.50 | 1,683 |
| `speed_retention` instead | −0.03 | −0.24 to +0.18 | 1,731 |
| Route-family fixed effects added | +0.06 | −0.31 to +0.43 | 1,731 |
| `log(separation)` | +0.02 | −0.12 to +0.16 | 1,731 |
| Clustered by play (1,598 clusters) | +0.05 | −0.42 to +0.52 | 1,731 |

Nine specifications, eight point estimates inside ±0.11 of zero, the ninth (MAN-only, where the
attribution is most credible) negative at −0.46 with an interval spanning zero. **Nothing in the
set meets the criterion, and the sign is not stable.**

## Why this cannot be read as "retention does not matter"

This is the important part, and it is why the number is an estimate and not a finding.

**The match is an inference, not an observation.** The dataset records no opponent identifier.
The turn is attached to the single opposing route-runner the file can see on that play; that
receiver need not be the man the DB was covering. The frozen 3-yd proximity rule that would
check it needs `(x, y)` for both players at the break frame, and every tracking file on this
machine is the 1,048,575-row truncation (333 games, 12,638 of 31,720 plays, four players max per
play). The rule is unevaluable here — not undecided, unevaluable — so this design cannot verify
attribution at all.

**That failure is classical measurement error, and it attenuates toward zero.** If a share of the
1,731 turns are attached to a receiver the DB was not covering, the exposure (his retention) is
unrelated to that receiver's separation by construction, and the estimated coefficient shrinks
toward zero in proportion to that share. The MAN-only subsample — 337 turns, where a one-to-one
assignment is the strongest prior available — is the least-attenuated slice, and its point
estimate is nine times larger in magnitude. It is still −0.46 with an interval spanning zero, so
it does not rescue the relationship; but it is a warning against reading +0.05 as "no effect".

**The population is selected twice over.** Only prospect-versus-prospect pairs are visible: the
DB and the opposing receiver must both be among the 497 named players, so this is not a random
slice of NFL coverage, and it selects on having been drafted into the cohort and on which
eligible players happen to be in the file. Turns whose play carries more than one visible
receiver — including every turn where the man and a second receiver are both tracked — are
dropped by the rule, so the sample is not coverage as played.

**Zone-assigned turns are included, and that is the weakest part of the design.** In zone coverage
the DB is not covering a specific receiver, so pairing him with the one visible receiver is close
to meaningless. They are 63% of the sample. Restricting to MAN is the sensitivity above (n=337);
it is smaller and noisier but points the same way. Both readings — "no relationship" and
"relationship diluted by wrong-receiver attribution" — survive these data.

**The unit of independence is the player.** 102 players, median 12 turns each, maximum 72. A
coefficient here is a sample of 102 careers, not of the league; the intervals are clustered
accordingly, and any statement about "DBs" in general is a statement about this cohort.

## What would be needed to make this a finding

1. **Verify the attribution.** The frozen 3-yd rule on the full tracking file: the matched
   receiver must be the nearest defender's man at the break frame, and the persistence check must
   hold at pass forward. Without it, attenuation cannot be bounded and a null cannot be
   distinguished from a diluted effect.
2. **An outcome nearer the break.** `separation_at_pass_forward` is measured after the route has
   continued, and it is recorded only on plays where the file has the receiver's row. A
   separation measured at the break itself, or a target-level outcome (was the receiver open when
   the ball was thrown), would need less of the break's signal to survive.
3. **More players, not more turns.** Precision is limited by 102 clusters. More turns per player
   barely helps; more players does.

None of the three is available on this machine. The honest position is that the outcome link
Conor asked for **cannot be demonstrated with these files**, and the specific reason is the
missing full tracking, not the absence of an effect.

## Provenance and reconciliation

- Band turns: 7,777 (`[FULL]`, `bdb27_full_db_turns.parquet`, 113 players, 7,198 plays).
- Attributed: **1,731 turns, 102 players, 1,598 plays** — exactly one visible opposing
  route-runner per play, with separation recorded.
- Main's brief quoted **1,621 turns**. Re-derived here from the raw `player_play.csv` under the
  rule stated in the spec, the count is 1,731. The 110-turn difference (6.4%) is **not
  explained**; the rule used here is written down in `notes/outcome-test-spec.md` §2 and is
  recomputed from raw files by `tests/test_outcome_test.py`. Every count in this note is the
  1,731-based one. The conclusion does not turn on the difference — all nine specifications are
  null on both.
- Nothing here is a `[SAMPLE]` number presented as `[FULL]`. The tracking file is used for
  nothing.

## Small fix shipped with this cycle

`notebooks/bdb27_full_game_turns.ipynb` §6 said the tracking sample covers "4,228 plays". 4,228 is
the count of distinct `play_id` values; `play_id` repeats across games, and distinct
`(game_id, play_id)` pairs number **12,638** of the 31,720 plays in those 333 games. The notebook
sentence is corrected, `notes/schema.md` and `notes/phase1-audit.md` carry dated corrections, and
a test recomputes both counts from the tracking file so the wrong figure cannot return.

## The sentence proposed for Conor

> **In 1,731 DB 30–60° turns from 102 players where exactly one opposing receiver is visible, a
> 0.10 higher share of entry speed retained through the break is associated with 0.005 yd *more*
> separation at pass forward (95% CI −0.04 to +0.05 yd) — no link in either direction, and the
> interval rules out the modest effect that would have counted (0.20 yd per 0.10 retention) by a
> factor of four; but because the receiver had to be inferred rather than observed, a diluted real
> effect cannot be excluded, and settling that needs the full tracking file.**

If Conor's result requires a report section, the paragraph to *propose* (not insert) is:

> *A direct test of the link between the break and coverage outcomes is not possible with the
> public tracking sample. On the 1,731 turns where a single opposing receiver was identifiable,
> the association between speed retained through a 30–60° break and separation at pass forward is
> indistinguishable from zero (+0.05 yd per unit retention; 95% CI −0.36 to +0.46). This is
> consistent with no relationship and with a genuine relationship diluted by unverified
> attribution; the full tracking file would separate the two.*
