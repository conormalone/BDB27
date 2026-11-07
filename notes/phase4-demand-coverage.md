# Phase 4 — the demand map and the supply-vs-demand case (stage 04)

Status: **implemented** (demand map, coverage, cut gaps, position × role ranking,
Cut Card *data*, manifest). Every number on this page comes from a stage run or
from the tests; sample-mode values are stamped `PLACEHOLDER_SAMPLE` and are **not
results**.

Binding spec: `TASK.md` @ `f8ff492` — **CURRENT FOCUS** (7 Nov 2026), which
supersedes the older §4/§5 framing wherever they disagree. Under that focus this
stage exists to produce two things the case is built from:

* **(a) the supply-vs-demand table by turn band** — how often each band is asked
  for in games versus how often the Combine tests it; and
* **(b) the position-level demand map** — the game-turn distribution the figures
  overlay the feasible region on.

The **per-player Turn Coverage score and the Cut Card are appendix-only**: with
43 of 51 variance components at the boundary there is no identified per-player
capacity, so neither can carry the headline. They remain in the artefacts because
the pipeline produces them and the appendix may quote them, not because the case
rests on them.

Inputs are Stage 02/03 artefacts only — this stage never refits anything and
never touches `outputs/turns/` or `outputs/envelope/`.

## Turn universe and band edges (settled 7 Nov 2026)

**Denominator.** The demand denominator for the case is **all DB game turns**.
On the sample that is **2,262** valid DB game turns. The snap-type-classified
subset (DB `COVERAGE`, **1,111** turns) is a **sensitivity band reported
alongside**, not the headline denominator. **1,121 of the 2,262 DB game turns
(49.6%) are snapped `OTHER`**; that share is stated wherever DB coverage is
quoted, because it is half the DB turn universe and a reader must know what the
classified subset leaves out.

The stage's `demand_map.csv` still carries the snap-classified map per position
(D152), with `snap_type_scope` on every row so the subset is never confused with
all turns; the all-turn DB counts above are the case denominator and are quoted
beside it. Switching the artefact's primary DB vector to all turns would change
the estimand and is **not** part of this landing.

**Band edges.** The case reports **30–45°** and **45–60°** as separate bands,
**left-closed** (`30 ≤ |turn| < 45`, `45 ≤ |turn| < 60`). The proposed drill
targets **40°**, inside the 30–45° band. These are reporting bands read from the
turn table, not the stage's cell grid: the demand map keeps the 30°-wide angle
bins (0–30, 30–60, …, 150–180) because those edges are shared with the Stage 03
feasible region and must not move without rebuilding it. On the sample, all DB
game turns give **97** turns in 30–45° and **229** in 45–60°; the 0–30° band is
empty because the turn detector's floor is 30°.

Adding the `demand:` block changes `config_fingerprint`, so an existing Stage 03
checkpoint rebuilds once on the next chain run. No Stage 03 code, schema or
artefact contract changed; the rebuild is the ordinary config-fingerprint rule.

## What the stage does

Item 1 is the deliverable under the current focus; items 2–5 are appendix material
produced by the same pass.

1. **Demand map (deliverable b).** Per position group, the distribution of *game*
   turns over the configured `bins.entry_speed × bins.turn_angle` grid (36
   cells), reported with the snap-type scope on every row. Frequency is the
   default weight; see **Turn universe** above for the all-turn DB denominator
   and the classified sensitivity. The 30–45° / 45–60° band counts used by the
   case are read from the turn table, not aggregated from these 30° cells.
2. **Turn Coverage (appendix).** For every Combine player, the demand-weighted
   share of his group's game-turn cells that fall inside his Combine-derived
   feasible region, reported **separately** for observed and extrapolated cells
   and **combined**.
3. **Cut gaps (appendix).** The highest-demand cells the player probably cannot
   execute (`inside_share < feasibility.inside_share_min`), ranked by demand
   weight.
4. **Position × role ranking (appendix).** The coverage distribution by position
   and role, not a headline ranking of players.
5. **Illustrative Cut Card data (appendix).** Cell-level playback data for one
   median-coverage player per configured group. **No figure is drawn here**;
   rendering, if any, belongs to Stage 06 and the appendix.

The supply-vs-demand *table* quoted in the case is a small read of these outputs
plus the turn table: game turns per band and per position (frequency weights on
the all-turn universe) beside the Combine counts from the transaction table. It
adds no new threshold and no new model.

## Eligibility (one ordered, mutually exclusive chain)

This chain describes the **stage's snap-classified artefact**. The case's all-turn
DB denominator (2,262) is a reporting aggregate over `setting == "game"` and does
not pass through `snap_type_not_relevant`; the 1,121 `OTHER` DB turns are in it
by construction.

Applied in this order to `setting == "game"` turns; every exclusion is counted by
reason in the manifest and the counts sum to `turns_in`:

| # | reason | rule |
|---|---|---|
| 1 | `invalid` | `valid` is not True |
| 2 | `position_not_mapped` | `position_group` not in `demand.positions` |
| 3 | `snap_type_not_relevant` | `snap_type` not in `demand.snap_types[group]` |
| 4 | `nonfinite_geometry` | entry speed or `abs_turn_angle` not finite |
| 5 | `entry_outside_bins` | entry speed outside `[bins.entry_speed[0], last)` |
| 6 | `angle_outside_bins` | angle outside `[bins.turn_angle[0], last)` |
| 7 | `eligible` | the remainder |

Binning is `np.searchsorted(edges, value, side="right") - 1`;
`cell_index = entry_idx * n_angle_bins + angle_idx`, which is exactly Stage 03's
index order. The stage asserts that the unique `(cell_index, entry_lo, entry_hi,
angle_lo, angle_hi)` table equals the unique cell table in
`feasible_region.parquet` (36 cells) and refuses if it does not.

**Angles above 180° are excluded, counted, and never clamped** (D151). The sample
has 575 such rows out of 7,143 game turns; they are a data-quality artefact of
heading wrap, and inventing a 181°+ edge or clipping them to 180° would put
fabricated mass in the top angle bin.

## Weights and labels

* `frequency` — every eligible turn weight 1; all five groups (**the default
  coverage demand vector**).
* `outcome_quick_pressure` — DL only. Weight 1 when the turn's rep is
  *qualifying*: `quick_pressure IS TRUE` **or** `time_to_pressure <=
  demand.short_time_to_pressure_s` (2.5 s).
* `outcome_quick_pressure_excl_unblocked` — the same, additionally weight 0 when
  `unblocked_pressure IS TRUE`.

The outcome map is defined only for `demand.outcome_weight_positions` (DL), so no
all-zero outcome rows are written for the other groups (D161).

Label join is on `(game_id, play_id, nfl_id)` with an explicit column list. The
label table is asserted unique on that key (`assert_one_to_one`); a duplicate
refuses the stage rather than multiplying the turn table. The merge asserts the
turn row count and row order are unchanged.

**Null semantics (D101, D153).** A null `quick_pressure` on a *present*
`player_play` row is **false** and is counted
(`null_quick_pressure_on_scope_rows`). A turn whose rep has **no** `player_play`
row is **excluded from outcome weighting** — never counted as a non-pressure rep
and never set to false. A missing label file does not fail the stage: the
frequency map is delivered and the DL outcome map is written with 36 null shares
and `NOT_ESTIMABLE`, with a named reason (`label_file_absent`,
`no_player_play_columns`, `labels_null_on_all_joined_reps`,
`no_qualifying_turns`).

## Bootstrap over players

The resampling unit is the **player**, not the turn: turns inside one player are
not independent. Per group a player × cell weight matrix is built over every
distinct eligible player (players with zero outcome weight stay in the matrix),
`bootstrap_reps` players are drawn with replacement, the drawn rows are summed and
normalised, and p05/p95 are the configured `demand.coverage_interval`
percentiles. The seed is
`SeedSequence([seed, demand.bootstrap_seed_offset, group_index])`, where
`group_index` is the group's index in the sorted configured position list — the
same seed for every weight mode of a group, so modes are compared on common
random numbers. Fewer than `bootstrap_min_players` players ⇒ null intervals.

## Coverage formula

For every Combine player whose group is in `demand.positions`:

```
w          = the group's frequency demand vector (36 cells)
combined   = Σ_c w_c · 1[inside_point_c] / Σ_c w_c
observed   = the same restricted to cells with observed == True
extrap     = the same restricted to cells with observed == False
expected_* = Σ_c w_c · inside_share_c / Σ_c w_c        (transparent, not a posterior)
```

`observed` is Stage 03's rule (the cell's entry-speed interval fully inside the
Combine entry-speed range); this stage never recomputes it. A scope with zero
demand mass gets a null value and `NO_DEMAND_MASS`; `combined` is
`NO_DEMAND_MASS` only when the total mass is zero. A player with no feasible
region (e.g. no plant turns) gets `NO_FEASIBLE_REGION` and null scores.

**Intervals are a marginal approximation (D156).** Stage 03 persists per-cell
`inside_share`, not joint draws, so for each distinct region and scope the stage
draws `coverage_draws` independent Bernoulli(`inside_share_c`) per demand cell,
computes the weighted coverage each draw, and reports the configured percentiles
plus the replicate SD. The manifest says
`interval_method="marginal_bernoulli_over_stage03_inside_share"` and
`joint_bootstrap_draws=false`. Cell correlation is therefore **not** propagated;
this is not a posterior and is not called one.

## Boundary policy (D157) — no fabricated spread

**Where τ² = 0, coverage equals the group value.** That is not an approximation
or a fallback: with no estimated between-player variance the random intercept is
zero for every player, so every player in the group has the same feasible region
and the same coverage. The artefact says so in every row (`coverage_shared_group`,
`shared_reason="tau2_at_boundary"`), and the appendix quotes the group value
rather than a player ranking.

Every Combine position group is `AT_BOUNDARY` for `plant_retention` on this
sample, so `tau2 = 0`, every player's random intercept is 0, and every player in a
group has an **identical** feasible region (identical `inside_share` and
`inside_point`). Coverage is computed **once per distinct region** and assigned to
every player in it:

* `coverage_shared_group = (n_distinct_regions_in_group == 1)`;
* `shared_reason = "tau2_at_boundary"` when the Stage 03 retention component is
  `AT_BOUNDARY`, else `"identical_feasible_region"`;
* `region_group_size` and `n_distinct_regions_in_group` are written per row;
* the manifest records `coverage.all_group_level`, the per-group `tau2_status`
  and `n_components_at_boundary`, and says in plain words that coverage is
  group-level and player-to-player variation is **not estimated**.

A test proves that two players in a boundary group have identical coverage and
identical intervals, and that no spread is emitted. The identical interval is not
a bug: with `tau2 = 0` there is no estimated between-player variation to draw
from, and manufacturing one would be inventing data.

## Roles

Per player, `role` is the **mode of `alignment`** over his `setting == "game"`
turns in the relevant snap types (all his game turns if he has none in scope),
mapped through `demand.roles[position_group]`; an unmapped alignment becomes
`other`, a player with no game turns becomes `unknown`. `role_source =
"game_turn_alignment_mode"` and `n_role_turns` are recorded. Roles feed only the
position × role tables; they do not change the demand map or the coverage
formula.

## Cut gaps and Cut cards (appendix only)

A **cut gap** is a cell with `w > 0` and `inside_share <
feasibility.inside_share_min` (0.50), sorted by `weight_share` desc then
`inside_share` asc, top `demand.cut_gap_top_n`. `gap_score = weight_share ×
(1 − inside_share)`. Per-player rows and a position × role aggregate (most common
gap cells, players/regions involved, mean weight/share/inside_share/gap) are
written.

**Cut Cards are data only (D160).** One player per group in
`demand.card_groups` (DL, WR, TE) is selected by `median_coverage`: among players
with a known role and `OK` coverage, sort by `coverage_combined` then `nfl_id` and
take the middle. `cut_card_cells.parquet` carries the full 36-cell playback data
(bins, observed, retention pred/p05/p95, inside_share, inside_point, demand
weight/share, gap score, `is_cut_gap`); `cut_card_players.csv` carries the card
metadata and the top gap. Both say
`rendering="deferred_to_stage_06_figures"`. No image is written by this stage.

## Config decisions

Every number below is a **design choice**, not an estimate from the sample, and
is logged in `notes/decisions.md`:

| key | value | why |
|---|---|---|
| `demand.positions` | DL, WR, TE, DB, OL | TASK §4; DB/OL exploratory |
| `demand.snap_types` | DL PASS_RUSH; WR/TE ROUTE; DB COVERAGE; OL OTHER | the 02_turns snap-type mapping; OL has no PASS_PRO label. This is the **classified sensitivity** scope; the case's DB denominator is **all 2,262 DB game turns** with the 1,121 `OTHER` share stated |
| `demand.short_time_to_pressure_s` | 2.5 | covers every observed `quick_pressure=True` row, excludes every False |
| `demand.bootstrap_reps` | 200 | stable bin interval at these player counts |
| `demand.bootstrap_min_players` | 2 | a spread needs at least two players |
| `demand.coverage_draws` | 2000 | marginal Bernoulli interval resolution |
| `demand.coverage_interval` | [0.05, 0.95] | p05/p95, matching Stage 03's convention |
| `demand.cut_gap_top_n` | 5 | a readable Card, not a ranking of every cell |
| `demand.card_groups` | DL, WR, TE | TASK's non-exploratory groups |
| `demand.card_players_per_group` | 1 | "do not generate cards for all players" |
| `demand.card_selection` | median_coverage | illustrative, deterministic, not best/worst |

## Files

| file | content |
|---|---|
| `outputs/demand_coverage/demand_map.csv` | long per group × weight mode × 36 cells: weights and player-bootstrap p05/p95 |
| `coverage_players.parquet` | one row per Combine player: combined/observed/extrapolated coverage, expected values, intervals, sharing flags, role, status, gap count |
| `coverage_position_ranking.csv` | position × role distribution and rank (1 = lowest mean combined coverage) |
| `cut_gaps_players.csv` | per player top gap cells |
| `cut_gaps_position.csv` | position × role most common gap cells |
| `cut_card_cells.parquet` | illustrative Card cell data |
| `cut_card_players.csv` | illustrative Card metadata |
| `manifest.json` | provenance, config fingerprint, inputs, counts, methods, boundary share, outcome status, limitations, warnings, open decisions |

Parquet uses `common.write_parquet` (provenance/label/config_sha metadata, no
timestamp); CSV uses `common.write_csv` (leading provenance/label/config_sha);
JSON uses `common.write_json` (strict, no NaN/Infinity).

`--mode full` refuses (exit 2, `REFUSED`) unless `outputs/thresholds.json` is
`FULL` **and** `notes/prereg.md` exists, naming every missing precondition. The
checkpoint is reused only when `config_sha`, `mode`, `provenance` and
`artefact_version` all match. `--mode sample` always runs and stamps
`PLACEHOLDER_SAMPLE`.

## Sample run — PLACEHOLDER_SAMPLE, not results

From `outputs/run_log.csv` (rebuild row): **2.9 s wall, 233.4 MB peak RSS**, 4,615
eligible turns, 508 Combine players, 3 cards.

```
DB turn universe : all=2262 valid game turns; snapped OTHER=1121 (49.6%),
                   COVERAGE=1111, PASS_RUSH=30; the classified artefact
                   below uses COVERAGE only (1017 eligible after bins)
case band counts : 30-45 deg=97, 45-60 deg=229 (all DB game turns, left-closed)
positions       : DL=799, WR=1375, TE=267, DB=1017, OL=1157 eligible turns
turns           : in=7143 eligible=4615 excluded(invalid=6,
                  position_not_mapped=749, snap_type_not_relevant=1377,
                  nonfinite_geometry=0, entry_outside_bins=0,
                  angle_outside_bins=396)
demand mass     : 4615 weighted turns (frequency)
coverage        : combined mean=0.961 [0.876,0.997];
                  observed mean=0.933 [0.820,0.976];
                  extrapolated mean=0.994 [0.974,1.000]
coverage rows   : 508 Combine player(s)
boundary share  : 508/508 players in a shared group (5/5 groups group-level);
                  plant_retention 5/5 components AT_BOUNDARY
cut gaps        : 508 player(s), 1230 player-cell row(s), top_n=5, mean gap=0.009
outcome weights : OK, positions=DL, qualifying turns=17/799 in scope
cut cards       : 3 selected (median_coverage); deferred to stage 06
```

The DL outcome map has 17 qualifying turns in 799 eligible DL turns; 728 of the
799 present rows have a null `quick_pressure`, which is exactly why the D101 rule
matters (null → false on a present row would otherwise turn 728 unknown reps into
non-pressure mass). The unblocked sensitivity drops the total from 17 to 16.

None of this is interpreted: the sample exists to prove the code path, the
schemas and the refusal rules.

## Limitations

* **DB turn universe.** The case denominator is all 2,262 DB game turns, of
  which 1,121 (49.6%) are snapped `OTHER`; the stage's coverage artefact is the
  COVERAGE-only sensitivity over 1,111 of them (1,017 in bins). Any DB coverage
  number must be read with that split in view, and neither half is a full
  coverage/pass-pro designation.
* **Selected group.** The demand map describes tracked players (drafted, in the
  data), not all NFL players. It is a sample of who reached the league.
* **Group-level coverage on this sample.** All five Combine groups are
  `AT_BOUNDARY`, so no player-to-player coverage variation is estimable here; the
  artefact says so in every row and in the manifest.
* **Marginal intervals.** Cell correlation is not propagated; the interval is a
  Bernoulli approximation over persisted shares.
* **Combine range.** Extrapolated cells are extrapolation: the retention model is
  being read outside the entry-speed range the Combine turns observed.
* **Outcome labels.** `player_play` pressure labels are vendor/modelled, not
  measured by this pipeline; DL-only and often null.
* **Snap-type proxies.** DB = COVERAGE and OL = OTHER are the closest available
  labels, not a true coverage/pass-pro designation.
* **Role is an alignment mode.** A player who plays multiple alignments gets one
  role, the most frequent one.

## What `notes/prereg.md` must freeze

1. demanded positions and the snap-type scope per position (including the DB/OL
   proxies) **and the turn universe**: all DB game turns as the case denominator,
   the classified subset as a sensitivity, with the `OTHER` share stated;
2. the demand bin edges (they are shared with the feasible region) **and the case
   band edges** — 30–45° and 45–60° separate, left-closed, drill target 40°;
3. the frequency default and the exact outcome-weighting definition
   (quick pressure OR short time to pressure), the threshold, and the
   unblocked-exclusion sensitivity;
4. the null semantics (present-row null → false; missing row → excluded);
5. the coverage formula, the observed/extrapolated split, and the interval method
   (marginal Bernoulli is an approximation that must be frozen as such);
6. the cut-gap rule, `inside_share_min` and `top_n`;
7. the bootstrap unit, replicate count and interval;
8. the role mapping and the card selection rule.

## Tests

`tests/test_demand_coverage_stage.py` runs the **real** stage in a subprocess on
synthetic Stage-02/03 worlds written directly into a temp tree, covering: the
binning sum, the snap-type filter, the three weight modes, labels absent
(`NOT_ESTIMABLE`), boundary-group identity (no fabricated spread), the
observed/extrapolated split, byte-identical determinism, the missing-column
refusal, the full-mode refusal (thresholds + prereg), the CSV/Parquet/manifest
contract, cut-gap ordering, the deferred-rendering Card, and the config bin grid.
