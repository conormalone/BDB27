# Viz menu — band reliability (the measurement-limits slot)

**Status: PROPOSAL. No figure here is built, and none is signed off.** Written 2026-11-16 by
the Storyteller. Read-only on the repo; nothing committed, nothing rendered.

**Extends, does not replace, `notes/viz-menu.md`.** That note fixes the house rules and the
"(e) measurement limits" slot; this one fills that slot with the band-reliability evidence
that did not exist when it was written (viz-menu says "Kill-test reliability numbers.
PENDING FULL RUN." — they now exist).

**Every number below is `[FULL]`** and comes from one of two artefacts:

- `outputs/band_reliability/{reliability.json, scenarios.csv, manifest.json}` (D206) — four
  ICC scenarios, two adjusted ICCs, the derived reliability/attenuation/sample-size block.
- `outputs/fallback_validation/reliability.json` (D203) — ICC with bootstrap and analytic
  intervals, split-half, cross-season, per-player mean SE. Stamped `FULL` by its own `stamp`
  field and by `verdict.txt`.

`outputs/turns/turns.parquet` is **`PLACEHOLDER_SAMPLE`** and is not used for any number in
this note. The FULL turn rows live in
`data/interim/full_run_20261111/bdb27_full_db_turns.parquet` (58,665 rows) joined to
`outputs/turn_context/turn_context.parquet` (7,777 band rows) for `play_type`.

---

## 0. Read this before the menu: the figure count is a live conflict

`TASK.md` says, in a section headed "Figures — exactly four, and no others": *"No fifth
figure. No other figure may be named in the writeup, the notebook or the draft."* The
viz-menu's five-slot structure (with slot (e)) comes from the **7 Nov** amendment; the
**13 Nov** rewrite deleted slot (e) and cut the set to four.

So: **none of the candidates below may enter the deliverable as things stand.** They are a
menu for a figure Conor has not yet authorised. The three honest exits:

1. **Conor amends `TASK.md` to five figures**, with the reliability panel as figure 5 — the
   cleanest, and the paragraph in `notes/validation-section.md` already carries the same
   numbers as text, so the figure would illustrate, not introduce, the claim.
2. **The numbers stay text-only** (where they are now) and this menu is internal defence —
   what to draw if a judge or reviewer asks.
3. **One panel is folded into an existing figure** — the only slot that has room is figure 1's
   caption block, and I do not recommend it: fig 1 is the hero and a variance-decomposition
   inset would bend its single claim.

I have not assumed any of the three. The ranking below is on merit; the authorisation is
Conor's.

**Second conflict, smaller.** The existing `outputs/figures/fig_measurement_limits.png` is a
`PLACEHOLDER_SAMPLE` figure built on envelope `tau2`, not on ICC. If any candidate here is
built, it **supersedes** that figure rather than sitting beside it, and that PNG must not be
cited in the same breath as these numbers.

---

## 1. Rules these figures obey

The three house rules from `viz-menu.md` stand: no per-player ranking outside the appendix;
grey context and one accent, one meaning per panel; counts and denominators on the marks, not
adjectives. Four more, specific to reliability:

4. **DB only, and no other position as grey context.** The ICC is a DB-band statistic; a DL
   panel beside it would imply a comparison that was never run.
5. **No threshold lines.** `outputs/fallback_validation/reliability.json` carries a `label`
   block with `icc_stable 0.5`, `icc_weak 0.2`, `icc_void 0.2`, `icc_upper_void 0.4` and the
   headline `VOID`. Those are the old gate, and Conor's standing 2026-11-15 rule is that no
   arbitrary threshold may act as a control on a figure or a notebook. **Do not draw them.**
   The one line allowed is the `R = 0.70` *target* from `derived`, labelled as a target, not
   as a pass mark.
6. **Never compare ICCs without their k beside them.** The estimator here is ICC(1) on an
   unbalanced design, so ρ moves with turns per player. The four scenarios sit at k̄ 68.8,
   30.0, 20.7 and 46.8 on different subsets. Any row, bar or dot that shows an ICC must carry
   its `n_turns`, `n_players` and `k̄`, and no ratio between two rows may be computed.
7. **No outcome language.** `TASK.md` bans outcome tests. Attenuation (√R) and clusters-needed
   are arithmetic about a *hypothetical* criterion; a figure may say "any criterion", never
   "separation", "pressure" or "production".

---

## 2. The ranked menu

Ranked by (how much of the defence it carries) × (how cheaply it can be built honestly).

### 1. A third of one percent is the player — *variance decomposition*

**Sees.** Four horizontal 100%-stacked bars, one per scenario. Grey = within-player variance
(`sigma2_e`), accent = between-player variance (`sigma2_a`). The accent is a hairline:
0.34%, 0.93%, 0.85%, 0.46%. A right-hand numeric column prints each ICC with its turns,
players and k̄.

**Proves.** *"How little is between players."* The quantity the whole defence rests on: in the
30–60° band, 99.66% of the variation in `apex_retention` is turn-to-turn, not player-to-player.
Nothing downstream can rank players on 0.34% of the variance.

**Data.** `outputs/band_reliability/reliability.json` → `scenarios[].sigma2_a`, `sigma2_e`,
`turns`, `players`, `kbar`, `icc`. Complete; the stacked share **is** the ICC
(`sigma2_a/(sigma2_a+sigma2_e)` reproduces all four values to the printed precision). No new
stage. The two **adjusted** rows (ICC 0.0037 and 0.0141, from `adjustment.*.icc`) cannot be
stacked — the adjusted fits store no `sigma2_a`/`sigma2_e`. Either omit them here or see §4.1.

**Design.** Horizontal, one row per scenario, ordered whole-band → drill-speed → drill-speed
pass → pass-only. x is 0–100% with no interior ticks; a single "share of total variance" label
once. Grey `#B9BEC4`, accent `#1F4E79` at 0.85 alpha. Numbers right-aligned in tabular
figures: `0.34%  ·  7,777 T / 113 P  ·  k̄ 68.8`. The row label is the scenario rule in plain
words ("all band turns", "3–5 yd/s entry", "3–5 yd/s on pass plays", "pass plays only"). One
annotation, on the top row: *"the accent segment is the ICC, drawn to scale."*

**Honest failure mode.** (i) At true scale the 0.34% segment is about two pixels on a 600 px
bar. That invisibility **is** the finding, but it will be read as *zero*, which is a stronger
claim than the data supports: the point estimate is positive and its interval includes zero
(`[-0.001, 0.008]`, D203 bootstrap). The caption must say "tiny, and not distinguishable from
zero — both readings support the same conclusion". Do **not** add a zoomed inset: a zoom turns
a hairline into a feature and inverts the message. (ii) Four rows invite "the drill-speed
window has three times the reliability" — an invalid comparison across k (rule 6); print k̄
and refuse the ratio in the caption. (iii) `sigma2` is in retention² units; label the axis as
a share, never as a value. (iv) A judge will ask "what is a good ICC?" — the answer is not a
number from the old gate block, it is "one third of one percent of the variance is between
players".

**Cost.** Cheap. 2 h, standalone script, no new data. **Rank 1.**

---

### 2. Three independent routes, same answer — *ICC, split-half, cross-season*

**Sees.** Three small panels on one shared x-axis, *agreement* (−0.3 to 1.0, zero marked):
a dot with its interval in each. Left: ICC(1) 0.0034 [−0.001, 0.008], n = 7,777 turns /
113 players. Middle: split-half reliability (Spearman-Brown) −0.105 [−0.26, 0.15], 112
players. Right: season 2024 vs 2025 correlation, per-player means, 0.196 (n = 66) and 0.007
(n = 39 at ≥10 turns per season).

**Proves.** *The near-zero is not an artefact of the estimator or of the unbalanced design.*
This is the defence against the one objection that can sink figure 1 on its own: "your ICC is
just your ANOVA moments on unequal k". Odd/even halves of the same player's turns don't agree,
and neither do his two seasons — three different statistics, all near zero.

**Data.** `outputs/fallback_validation/reliability.json` → `icc1.icc` + `icc1.ci_bootstrap`,
`split_half.spearman_brown` + `split_half.ci`, `season_2024_2025.r` / `.n_players`,
`season_2024_2025_min10.r` / `.n_players`. All present, all FULL. **Two gaps:** there is no
interval for either cross-season correlation, and no interval for the split-half at the
`min4` cut. See §4.4.

**Design.** Three equal panels, shared x, grey `#B9BEC4` intervals, accent dots, zero as a thin
vertical rule across all three. Each panel titled by *method*, not by result. Under each dot:
the n. One annotation, under the middle panel: *"one player dropped (one-sided split), 112 of
113 used."* No player names, no ids, no scatter: the season panel is two dots, not 113.

**Honest failure mode.** (i) These are three different estimators of one idea. Placed on one
axis they look like three measurements of the same quantity — the caption has to say they are
different routes to "does it repeat", or a statistically literate judge will call it a category
error. (ii) **Negative reliability** reads as a bug. It is not: a near-zero true value plus
sampling error lands below zero, and the estimator is not constrained to be positive. Say so in
one clause, and show the interval crossing zero rather than clipping it. (iii) 0.196 is the
best-looking number on the page and the one most likely to be quoted out of context — it is
computed on 66 of 113 players with ≥1 turn in each season, and the ≥10-turn version is 0.007.
Draw both, or draw neither. (iv) The split-half drops `nfl_id 58383` (1 turn, one-sided); state
the count, never the id.

**Cost.** Cheap. 2 h, no new data for the dots as drawn (two intervals would need §4.4).
**Rank 2** — it is the cheapest panel in the set that defends against the strongest single
objection, and it needs no joined data at all.

---

### 3. What a player mean is worth — *reliability against turns*

**Sees.** x = turns per player (log, 1 → 300), y = reliability of that player's mean. One
accent curve at the adjusted drill-speed ρ = 0.0141; two thin grey context curves at the
unadjusted 0.0093 and the whole-band 0.0034. A dashed line at the `R = 0.70` target. Markers:
k = 30 → R = 0.30 (accent) and 0.22 (grey); the harmonic mean k = 14.09; and the crossing at
k = 164. A bottom rug of the 113 players' actual turns (median 48, min 1, max 300, 8 players
at ≤4 turns).

**Proves.** *What that costs you downstream.* At the turns actually held, a player mean is
about a fifth to a third reliable; anything correlated with it is attenuated by √R = 0.55
(adjusted) / 0.47 (unadjusted); and it states the fix in the same panel — ~164 turns per
player, ~18,500 turns against 3,385 held.

**Data.** `derived.reliability_of_player_mean`, `derived.attenuation_ceiling_sqrt_R`,
`derived.k_needed_for_R_0_70_at_adjusted_rho` (163.686),
`derived.turns_needed_for_R_0_70` (18,496.5 needed, 3,385 held), and the formula in
`derived.formulas`. The **curve** is that formula over a k grid — arithmetic from the
artefact, not a fit; only the marked points are printed artefact values. The rug needs
turns-per-player per player: **new small artefact**, §4.2. Without it the panel still works
with the manifest's `turns_per_player` min/median/max and `players_le_4_turns` as three
annotations.

**Design.** One panel, one accent curve (the drill-speed case — it is the only one the drill
proposal touches), two grey curves. Log x with ticks at 1, 10, 30, 164, 300 and both 30 and 164
labelled with their numbers. y 0 → 1. Dashed target line, labelled *"target R = 0.70"*. Rug at
y = 0 in grey, one tick per player. One annotation at the crossing: *"164 turns/player ≈ 18,500
turns; 3,385 held"*. Deliberately absent: a ribbon, a second y-axis, the clusters number (see
candidate 6).

**Honest failure mode.** (i) **The log axis is doing work for the argument.** It compresses
30 → 164 (5.5×) into a third of the width, and gives the empty 1 → 30 region two thirds. A
reader can leave thinking the fix is close. Mitigate by printing both numbers at their points
and stating the 5.5× ratio in the caption — or use linear x with the rug, which shows the gap
honestly but bunches every curve against the left edge. My call: log, with the numbers
printed. (ii) The curve is a **plug-in**: one ρ, no interval. It will look like a model with
known precision. There is a bootstrap CI for the whole-band ICC only (`[-0.001, 0.008]`), and
its lower bound is negative — the curve is not defined there — so a ribbon cannot be drawn
honestly today (§4.3). Caption: *"ρ is one point estimate; the curve is the formula applied to
it, not a fitted model."* (iii) k on the x-axis is "turns for a player with exactly k turns";
no player sits on the curve at his own k with any confidence — the rug is what says so.
(iv) Three curves at different ρ on one axis is the same cross-k trap as candidate 1; the rug
and the k̄ annotations are the mitigation, and the two grey curves must be labelled with their
own n and k̄.

**Cost.** Medium. 3 h plus a 20-minute groupby for the rug (§4.2); 2 h if the rug is dropped.
**Rank 3.**

---

### 4. The same turn, twice — *within-player vs between-player retention*

**Sees.** One axis, percentage retention. Grey histogram: all 7,777 band turns expressed as
deviations from their own player's mean. Accent histogram: the 113 player means. The accent
distribution is a thin spike inside the grey one — roughly a seventeenth as wide (arithmetic
from the artefact's `sigma2_a`/`sigma2_e`; the SD ratio is √ICC), because that is what an ICC
of 0.0034 looks like when one spread is drawn inside the other.

**Proves.** *"How little is between players", in the reader's own units.* Same claim as
candidate 1, but in speed-retention percent instead of variance shares, and with the shape of
the data visible — the honest version of "the turn-level data looks like this". It is also the
panel that pre-empts "your variance decomposition assumes normality": no distributional
assumption is drawn here, only the two empirical spreads.

**Data.** FULL turn parquet, filtered `setting == 'game'`, `position_group == 'DB'`,
`30 <= abs_turn_angle < 60`, `valid == True`, joined to `turn_context` only if the pass-play
version is wanted. Needs `nfl_id`, `apex_retention` and a per-player mean: **new small
artefact**, §4.2. Cheap.

**Design.** Two histograms, shared x, shared bin width, densities not counts (the n differ
17-fold and counts would make the accent invisible for the wrong reason). Grey behind, accent
in front at 0.85 alpha. x clipped at stated bounds with the number of turns outside the window
printed in the corner. Annotation: *"7,777 turns · 113 players · accent is the player means."*
No player identity anywhere, no ordering, no labels — this cannot be read as a ranking, by
construction.

**Honest failure mode.** (i) **The count difference is the confound.** 113 means are tighter
than 7,777 turns partly because there are fewer of them; the panel compares *spread*, and the
caption must say densities-not-counts and name the SD ratio, or a sharp reader will call it a
sampling artefact. (ii) `apex_retention` may have a heavy tail or values at the physical
bounds; if the window is chosen after looking, it is a choice that needs stating with the
count clipped. (iii) Two overlapping histograms can read as "the players are all the same" —
which is the right message — but they can also read as "there is no signal anywhere", which is
wrong: the *turn* is measured fine, it is the *player* that is not. One clause: *"turns are
measured; players are not distinguishable."*

**Cost.** Medium. 4 h (groupby + honest binning + clipping count). **Rank 4** — it carries the
same claim as candidate 1 at higher cost, so it is the *second* version of that claim, not the
first. Build it only if there is page space for one more panel.

---

### 5. The interval covers zero in every framing — *ICC by scenario, with intervals*

**Sees.** Four dot-and-interval rows, x = ICC from −0.01 to 0.03, zero marked. Each row: the
point estimate, its interval, and `n_turns / n_players / k̄` printed at the right. Every
interval touches or crosses zero.

**Proves.** *No scenario rescues the measure.* Not the whole band, not the drill's own entry
speeds, not the pass-only subset, not the drill-speed pass subset. A judge who suspects the
band was the wrong framing sees all four framings fail the same way.

**Data.** Point estimates: `outputs/band_reliability/reliability.json` → `scenarios[].icc`.
**Intervals exist for one scenario only** — `fallback_validation/reliability.json` →
`icc1.ci_bootstrap` ([-0.0012, 0.008], 2,000 reps, seed 20261115) and `icc1.ci_analytic`
([-0.0006, 0.0092]). The other three need a bootstrap over players: **new stage**, §4.3.

**Design.** Horizontal dot-and-whisker, one row per scenario, grey intervals, accent dots, zero
rule. Numbers right-aligned. `clamped: false` on all four, so no boundary caveat is needed —
say that in the caption, because the old envelope figure had 43 of 51 components at a boundary
and a reader will assume the same problem here.

**Honest failure mode.** (i) **Do not draw it half-built.** One row with a CI beside three
without is a lie of omission, and it is exactly the shape a reader will over-read. Either all
four intervals (§4.3) or draw only the whole-band row. (ii) An interval spanning zero is
"not detected", not "no variance exists"; those are different sentences and the caption must
use the first. (iii) A negative lower bound (`-0.0012`) must not be clipped at zero silently.
(iv) Cross-k comparison again: k̄ ranges 20.7 → 68.8 across the rows; annotate every row, and
no ratios.

**Cost.** Medium — cheap to draw, but honest only after a new bootstrap stage (§4.3, ~2 h of
cluster work plus a test). **Rank 5.**

---

### 6. What it would take — *turns and clusters*

**Sees.** Two bars, or a three-row ledger: turns held 3,385 against ≈18,497 needed at ~164 per
player; player clusters 84 against ≈591 for the effect D204 observed.

**Proves.** *What it would take to fix it* — the fix is 5.5× the drill-speed turns, and the
outcome question needs 7× the player clusters.

**Data.** `derived.turns_needed_for_R_0_70` (`turns_needed` 18,496.5, `turns_held` 3,385,
`k_needed` 163.686, `players` 113);
`derived.clusters_for_observed_effect` (effect −0.38, CI [−1.08, +0.33], 84 achieved,
`se_achieved` 0.3597, `se_needed` 0.1356, `clusters_required` 590.7, `scaling_factor` 7.03).
Both present, no new stage.

**Design.** If built at all: three ledger rows, bars to scale, counts at the end, the
"needed" bars hatched and labelled *"projection"*. No log axis — the 5.5× is the point and a
log axis would hide it.

**Honest failure mode.** (i) **The clusters half is out of scope.** It answers a question
about an outcome effect, and `TASK.md` says "No outcome test. No claim that anything here
predicts pass-rush production, pressure, or any game result." Drawing 591 clusters gives a
banned claim a picture and a number. It belongs in a *future work* sentence, not in a figure.
(ii) 18,497 is 163.686 × 113: it inherits the uncertainty of ρ **twice**, and it assumes the
113-player cohort and the same ρ at 164 turns — both unverified. `reliability.json` itself
carries the caveat: *"rho is a plug-in point estimate … every target here is order-of-magnitude,
not a design guarantee."* (iii) A big bar beside a small bar is the most quotable and most
over-claimable chart in the set. (iv) It duplicates candidate 3's crossing point, and 3 shows
it in context.

**Cost.** Cheap to draw (1.5 h), expensive to justify. **Rank last as a standalone figure.**
Recommendation: **fold "3,385 → ≈18,500" into candidate 3 as one annotation**, and keep the
clusters number as prose in `notes/validation-section.md`, where it already lives.

---

## 3. Build order and cost

| rank | figure | carries | new data | hours |
|---|---|---|---|---|
| 1 | A third of one percent | how little is between players | none | 2 |
| 2 | Three routes, same answer | not an estimator artefact | none (2 CIs optional) | 2 |
| 3 | What a player mean is worth | downstream cost + the fix | turns-per-player (§4.2) | 3 |
| 4 | The same turn, twice | claim 1, in reader's units | per-player means (§4.2) | 4 |
| 5 | Interval covers zero, all four | no framing rescues it | scenario bootstraps (§4.3) | 4–5 |
| 6 | What it would take | scale of the fix | none | 1.5 |

**Roughly 16 h for all six, 4 h for the two that carry the most.**

**If only one is ever built: candidate 1.** It is the claim, it is one artefact, it needs no
new stage, it contains no per-player element at all, and its failure mode is a caption
sentence rather than a data risk. **If two: candidate 1 + candidate 2** — the claim and the
proof that the claim is not a quirk of the estimator. Candidate 3 is the one Conor will want
on a page, and I would build it third, because it is the only one that needs a new artefact and
the only one whose axis choice can be argued with.

**Do not build a three-panel "story" figure** (sliver → curve → needed) even though it reads
well as a sequence. It is a dashboard, it breaks the one-claim-per-figure habit
`viz-menu.md` §4 steals from Goldsberry, and it makes all three numbers hostage to one
truncation.

---

## 4. What does not exist yet, and exactly what it needs

### 4.1 Adjusted variance components
`reliability.json` → `adjustment.{all_band,entry_3_5}` has `icc`, `r_squared`,
`n_parameters`, `players`, `turns` — but no `sigma2_a` / `sigma2_e`. Without them the two
adjusted ICCs cannot be stacked in candidate 1 and appear only as text. Fix: write
`sigma2_a` and `sigma2_e` from the same ANOVA moments the adjusted `icc1` call already
computes. One line in `src/bdb27/band_reliability.py`; no new data.

### 4.2 Per-player exposure and means
New artefact, e.g. `outputs/band_reliability/turns_per_player.csv`, columns
`nfl_id, scenario, n_turns` (and, for candidate 4, `mean_apex_retention, sd_apex_retention,
n_turns`). Source: `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` filtered to
`setting=='game'`, `position_group=='DB'`, `30 <= abs_turn_angle < 60`, `valid==True`, grouped
on `nfl_id`; scenarios that filter on pass plays join `play_type` from
`outputs/turn_context/turn_context.parquet` on `(game_id, play_id, nfl_id, frame_start)`.
`nfl_id` is written to the artefact and **must never reach a figure**.

### 4.3 Bootstrap intervals per scenario
Needed for candidate 5, and for any ribbon on candidate 3. Units: resample **players**
(`nfl_id`), not turns — turns within a player are not independent, which is the whole point of
the ICC. Columns required at source: `nfl_id, apex_retention, entry_speed, abs_turn_angle`
plus the scenario filters (`play_type` via the context join). 2,000 reps, fixed seed, write
`ci_bootstrap{lo,hi,reps,seed}` per scenario into `reliability.json`, matching the D203 shape.
Note the whole-band lower bound is negative, so any ribbon would have to be truncated and
labelled as such — which is a reason to skip the ribbon, not to skip the interval.

### 4.4 Intervals on the cross-season correlations
`fallback_validation/reliability.json` gives `season_2024_2025.r = 0.1959` (n = 66) and
`season_2024_2025_min10.r = 0.0067` (n = 39) with **no** interval. Same bootstrap-over-players
treatment. Until then candidate 2 draws those two as bare dots and says so.

### 4.5 Nothing else
No new thresholds, no new band edge, no re-run of stage 02. Every count in this note is already
committed in a FULL artefact at HEAD `5fe16a4`; the inputs' sha256 are in
`outputs/band_reliability/manifest.json`.

---

## 5. Questions only Conor can answer

1. **Does `TASK.md` change to five figures?** Without that, this whole note is internal
   defence and nothing gets drawn (§0).
2. **If a panel is built, is candidate 1 enough, or does he want 1 + 2?** The second defends
   against the estimator objection; the first alone is the claim.
3. **Is "≈590 clusters" allowed anywhere outside prose?** My read is no — it is an outcome
   quantity and `TASK.md` bans outcome claims — but it is his call, and if he wants it, it is a
   caption in a future-work paragraph, not a bar.
4. **Does the old `fig_measurement_limits.png` (sample, envelope `tau2`) get retired** if a
   reliability figure is built? Two measurement-limits images with different statistics will
   contradict each other on the page.
