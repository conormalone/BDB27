<!-- provenance: FULL -->
# Selection rule for the Phase 4 variance decomposition

**Question:** which players enter the Phase 4 decomposition, and how reliable is
a player-level get-off estimate? Evidence only. No threshold is chosen here and
no Phase 4 model is fitted.

**Provenance.** Every input is a complete file: `player_play.csv` (314,197 rows,
one per player-play), `games.csv` (1,002 games), `combine_tracking.csv` (510
attendees, 6,301 player-attempts), `players.csv`. The Phase 3 table's label side
is reconciled row-for-row against a direct query on the complete label file:
44,002 get-off rows and 33,061
primary rows on both sides. No tracking column is read, so these numbers are
**real evidence, not `UNVALIDATED SAMPLE OUTPUT`**.

## 1. Analysis set and the variance split

33,061 DL non-run pass-rush reps, 158 players, 907
games, 101 median reps per player;
123 players have a Combine 40-yard attempt (the
fittable set).

| quantity | value |
|---|---:|
| between-player SD (noise-corrected) | 0.1004 s |
| within-player SD (pooled) | 0.2494 s |
| SD of player means (uncorrected) | 0.1374 s |
| between-player share, noise-corrected | **0.139** |
| between-player share, Phase 1 method | 0.233 |

Phase 1's 0.285 was computed on all get-off rows (run plays and non-DL included)
without removing sampling error from the variance of player means; both inflate
the share. **0.139 is the honest ceiling for
what a physical model could explain here**, before depth or alignment adjustment.

## 2. How reliable is a player mean?

**(a) Split-half across games.** Games are ordered chronologically and
alternated into halves. 141 of
158 players have a rep in
each half; the other 17 have one game and cannot be
split. All estimable: **r = 0.452** (SB
0.622). The 119
players with 2+ games in *each* half:
**r = 0.736** (SB
0.848). Both are attenuated by unequal
halves:

| min reps/half | players | observed r | predicted r, rep-level | predicted r, three-level |
|---:|---:|---:|---:|---:|
| >= 2 | 129 | 0.695905 | 0.768201 | 0.688559 |
| >= 5 | 119 | 0.768611 | 0.863183 | 0.763733 |
| >= 10 | 108 | 0.82293 | 0.90564 | 0.809286 |
| >= 20 | 95 | 0.857583 | 0.932491 | 0.851151 |
| >= 30 | 88 | 0.877096 | 0.941971 | 0.86953 |
| >= 50 | 74 | 0.934178 | 0.955066 | 0.894402 |

The rise with half size is attenuation, not changing reliability. The rep-level
model misses the curve (RMSE 0.072); the
three-level model reproduces it (RMSE 0.018).

**(b) Model-based, as specified:** `k = sigma_e^2/sigma_u^2` from a one-way
random-effects fit: `sigma_e = 0.2494 s`,
`sigma_u = 0.1004 s`, so **k = 6.17**
(player-cluster bootstrap 4.798544–8.762238).
Reliability `n/(n+k)` = **0.942 at the median
101 reps**.

**(c) Model-based, games as a level:** `sigma_u =
0.0988 s`, `sigma_v = 0.0815 s`
(game within player), `sigma_e = 0.2364 s`.
Reliability `sigma_u^2/(sigma_u^2 + sigma_v^2/G + sigma_e^2/n)` =
**0.898 at the median player**. As a check that these
components are real: they predict the observed variance of player means at
0.01991 against
0.01888 observed.

**Where they disagree.** The rep-level figure is optimistic by
4 points because it treats ~100 reps as
independent observations when they are ~12 games with correlated reps. It also
cannot reproduce the split-half curve. Use the three-level column.

**Is it "very low"? No.** At the median it is about
0.90. It is low only in the tail:
33 players are below 0.5 and every one has
6 reps or fewer.

**Caveat.** This is the reliability of the **raw** mean. Phase 4's residual `u_i`
has less between-player variance, so its reliability is lower: 0.869 if the
physical term removes 25% of it, 0.815 at 50%. **These numbers are an upper bound
on the reliability of "jump".**

**Phase 1 control.** 0.634 is reproduced exactly (ICC(2,1) =
0.634145, r =
0.634872, n =
159) on all 44,002 get-off rows
with league-wide game-rank parity — a different sample from the primary set.

## 3. The two rules

**Rule A (raw reps)** admits anyone with ≥ c reps. Failure mode: it treats a rep
count as information, so 20 snaps in one game count the same as 20 across 15.

**Rule B (reliability)** admits anyone with `n/(n+k) ≥ r`. With a common `k`
this is algebraically `n ≥ k·r/(1−r)` — at k = 6.17 the cuts are
6.2, 9.3, 14.4, 24.7 reps for
r = 0.5, 0.6, 0.7, 0.8. **As specified, Rule B
is Rule A with a different label and cannot admit a different set.** It becomes a
distinct rule only if reliability is computed per player with games as a level
(`B_three_level_PROPOSED` below) — a proposal, not the specified rule.

| rule | cut | players | fittable | floor | meets SOI 0.3 | P_eff |
|---|---:|---:|---:|---:|---|---:|
| A_raw_reps | 20 | 115 | 90 | 0.2922 | yes | 89.271 |
| A_raw_reps | 30 | 109 | 85 | 0.3006 | no | 84.517 |
| A_raw_reps | 40 | 103 | 80 | 0.3099 | no | 79.727 |
| A_raw_reps | 45 | 100 | 78 | 0.3139 | no | 77.792 |
| A_raw_reps | 50 | 99 | 77 | 0.3159 | no | 76.823 |
| A_raw_reps | 60 | 92 | 71 | 0.3290 | no | 70.907 |
| B_reliability_rep_level | 0.5 | 125 | 99 | 0.2786 | yes | 97.24 |
| B_reliability_rep_level | 0.6 | 122 | 96 | 0.2829 | yes | 94.73 |
| B_reliability_rep_level | 0.7 | 118 | 93 | 0.2874 | yes | 92.037 |
| B_reliability_rep_level | 0.8 | 112 | 87 | 0.2972 | yes | 86.4 |
| B_three_level_PROPOSED | 0.5 | 125 | 99 | 0.2786 | yes | 97.24 |
| B_three_level_PROPOSED | 0.6 | 120 | 94 | 0.2859 | yes | 92.967 |
| B_three_level_PROPOSED | 0.7 | 112 | 88 | 0.2955 | yes | 87.403 |
| B_three_level_PROPOSED | 0.8 | 99 | 77 | 0.3159 | no | 76.826 |

Floor is `1.96·sqrt(2/P)` on the **fittable** set: a player with no Combine time
contributes nothing to the decomposition.

## 4. The power artefact

The floor assumes every admitted player is equally informative. Admitting
players whose means are mostly noise lowers the computed floor while the quantity
it describes gets worse — their observed means shrink toward the grand mean, so
the between-player share is biased down by roughly the mean reliability. **The
floor must be computed on the fittable set that carries information.** `P_eff` is
the Kish count `(sum r)^2/sum r^2` on three-level reliabilities.

Here the artefact is **small**: median fittable reliability is
0.94 at 20 reps
and 0.95 at 50; at the 20-rep cut
`P_eff` = 89.271 against 90 heads, and the floor moves
0.29218 → 0.293816. So 20 versus 50 reps is a **headcount** question, not a
reliability one. The artefact bites only in the tail Rule B at 0.5 reaches: the
10 players it admits below 20 reps have three-level reliability
0.51–0.70 (0.13 for the worst 1-rep
player). Every cut from 0.6 upward stops at 12 reps or more.

**Who each rule silently drops.** A at 50 drops 59
of 158 players: all with 1–49 reps, plus 22
with 50+ reps and no 40 time. B at 0.8 drops 46
and admits players from 26 reps (median
188 reps / 19 games). Exact membership:
`rule_membership.csv`; disagreement sets: `overlap_matrix.csv`.

## 5. A question for Conor, not a finding to act on

The corrected between-player share is **0.139**,
roughly half the 0.285 that SOI 0.30 was calibrated against. If the SOI means the
between-player share of **total** variance, no selection rule makes it
detectable: floor 0.2922 at 20 reps, 0.3159 at 50, 0.2499 over all 123 fittable
players. But `TASK.md` Phase 4 asks for the share of **between-player** variance
explained by `b*phys` versus `u` — a different denominator, not yet estimated.
**Which share does 0.30 refer to?** The prereg should not be frozen until that
is fixed.

**Not computable here:** reliability of the residual `u_i` (Phase 4); depth- and
opponent-adjusted reliability (needs tracking); anything for the
35 primary players with no 40 time.

All artefacts in `outputs/phase3b/`.
