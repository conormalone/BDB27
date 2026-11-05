# Questions only full data can answer — with the script for each

Each item is a question, the exact command that answers it, and what answer would
matter. Nothing here is a prediction; these are open.

Conventions: run from the repo root with the venv active. `--mode full` requires
the three season tracking files in `data/raw` **and** a frozen `notes/prereg.md`;
the gate refuses otherwise.

---

## S1 — Does memory grow linearly with input size?

**Why.** The Pi cannot hold full tracking. If any stage is accidental quadratic,
the big machine will find out at hour four.

**How.** Concatenate the sample 2× and 4× with renamed ids, then run 01 and (once
it exists) 03 against each:

```bash
python - <<'PY'
import duckdb
con = duckdb.connect()
for k in (2, 4):
    con.execute(f"""
      COPY (
        SELECT game_id + {k}0000000 AS game_id, play_id, nfl_id,
               time, x, y, s, a, dis, o, dir, event
        FROM read_csv_auto('data/raw/game_tracking_2023_sample.csv', nullstr=['NA'])
        UNION ALL
        SELECT game_id, play_id, nfl_id, time, x, y, s, a, dis, o, dir, event
        FROM read_csv_auto('data/raw/game_tracking_2023_sample.csv', nullstr=['NA'])
      ) TO 'data/interim/scale_{k}x.parquet' (FORMAT PARQUET, COMPRESSION zstd)
    """)
PY
```

then point `files.game_tracking_sample` at each and compare `wall_s` and
`peak_rss_mb` in `outputs/run_log.csv`. **Expected:** wall ~2× and ~4×, peak
roughly flat (batching) or ~2×/~4× (whole-file). **Bad:** peak growing faster than
the input.

**Blocks:** the first full run. Run this first.

## S2 — What is the real rows-per-game and total row count?

**Why.** `notes/scale.md` extrapolates 120–170 M rows from a non-representative
sample. The real number sets every memory budget.

```bash
python -c "
import duckdb, yaml
cfg=yaml.safe_load(open('config.yaml'))['bdb27']
con=duckdb.connect(); con.execute('SET threads=1')
for f in cfg['files']['game_tracking_full']:
    r=con.execute(f\"select count(*), count(distinct game_id) from read_csv_auto('data/raw/{f}', nullstr=['NA'])\").fetchone()
    print(f, r)
"
```

**Matters because:** if it is 400 M rows, the Parquet cache needs a bigger disk
than the 7 GB free here.

## S3 — Does the onset threshold behave identically in both settings once calibrated?

**Why.** The audit shows the *definition* is shared, but not that
`speed_yds = 1.0` is the right value on a game snap, where the rusher may already
be drifting.

**How.** Phase 2 writes `outputs/calibration.json` with global stance/start
offsets; Phase 1's item (d) check is the diagnostic. Re-run:

```bash
python src/run.py 02_combine_physical_time --mode full
python src/run.py 01_audit --mode full          # item (d) now full-data
```

**Look at:** the median signed difference between the tracking-derived crossing
and `player_get_off`. If it is not stable across the three seasons, the onset
definition is not transportable.

## S4 — Is mean get-off reliable across games on full data? (gate condition)

**Why.** Provisional value from the complete label file is ICC 0.63 (odd/even
games). The gate needs the full-data number and a depth-adjusted version.

```bash
python src/run.py 01_audit --mode full --force
jq '.conditions[] | select(.id=="get_off_reliability")' outputs/audit/gate.json
```

**Decision rule from `TASK.md`:** stop if the split-half ICC is below
`audit.icc_min` (0.5). **Provisional forecast:** 0.63, so likely a pass — but it
is a forecast, not a result.

## S5 — Does Combine-predicted physical time relate to mean get-off at the player level? (gate condition)

**Why.** This is the load-bearing relation for the whole study. If `b` is ~0, there
is no physical part to decompose and the question is void.

```bash
python src/run.py 02_combine_physical_time --mode full
python src/run.py 03_game_snaps --mode full
python src/run.py 04_models --mode full
jq '.b, .b_interval, .variance_share' outputs/models/decomposition.json
```

**Decision rule:** stop if there is no relation at the player level. `b` near 1
supports "the Combine measures the same thing"; `b` far from 1 is a finding about
setting differences, not an automatic stop.

## S6 — How many rushers clear the snap minimum on full data?

**Why.** The audit's 105 comes from the label file and is already complete, but
the *tracked* count depends on full game tracking and on how many snaps survive
onset quality control.

```bash
python src/run.py 03_game_snaps --mode full
python -c "
import duckdb
q=duckdb.sql(\"select nfl_id, count(*) n from read_parquet('data/interim/snaps/*.parquet') group by 1\")
print(q.df()['n'].describe())
print('players >= 50:', q.df().query('n>=50').shape[0])
"
```

**Matters because:** it sets the power calculation (S7) on the *analysis* set
rather than the label set.

## S7 — What is the smallest detectable variance share, and does the design resolve the effect of interest?

**Why.** The audit already says the design cannot resolve a 15% share with 105
players (smallest detectable ≈ 0.27). If full data yields more players, this
improves; if not, Q1 must be answered before the prereg is frozen.

```bash
python -c "
import sys; sys.path.insert(0,'src')
import stats as ST
print(ST.min_detectable_share(<P>))
"
```

**Matters because:** it decides whether a null jump result is informative.
`TASK.md`: "Interpret a small jump share as informative only if the interval
excludes the smallest effect of interest."

**Status 2026-11-05:** Q1 is answered — the smallest effect of interest is 0.25
(D96). The realised floor is reported three ways in
`outputs/phase2/phase2_summary.json`: 0.271 at the audit's 105, 0.273 at the 103
primary-DL players, and **0.310 at the 80 players who have both >=50 DL reps and
a Combine 40-yard attempt**. The third is the design Phase 4 can actually fit and
is flagged for Conor.

## S8 — Is stance estimable from kinematics, given any labelled subset?

**Why.** Not answerable today: no stance label exists in the provided data.

**How.** Only if the full release adds a stance column. Check:

```bash
python -c "
import duckdb
con=duckdb.connect()
cols=con.execute(\"describe select * from read_csv_auto('data/raw/player_play.csv', nullstr=['NA'])\").df()
print([c for c in cols.column_name if 'stance' in c.lower()])
"
```

**If empty:** stance stays unadjusted, and the writeup says so.

## S9 — Does the pressure label's provenance become documented?

**Why.** `quick_pressure` is null on 92.2% of DL rows and its provenance is
inferred (`notes/blockers.md` B1).

**How.** Read the full data dictionary when it is available; check whether null
means "no pressure". Until then, do not model `quick_pressure` as binary.

## S10 — Season-over-season change for the 2025 class

**Why.** `TASK.md` exploratory Q4 needs ≥2 seasons per player. The 2025 class has
one.

```bash
python -c "
import duckdb
print(duckdb.sql('''
  select c.draft_year, g.season, count(distinct p.nfl_id) players
  from read_parquet(''data/interim/snaps/*.parquet'') p
  join read_csv_auto(''data/raw/games.csv'') g using(game_id)
  join read_csv_auto(''data/raw/combine_results.csv'', nullstr=[''NA'']) c using(nfl_id)
  group by 1,2 order by 1,2''').df())
"
```

**Matters because:** it decides whether Q4 is a two-point contrast for one class
or a real trend.

---

## Phase 2 at full scale (physical time)

Phase 2's *Combine* measurements are already computed on the complete Combine
file (793 attempts, 418 players), so the three items below are **confirmations
and calibrations**, not new Combine estimates. Two of them cannot be answered
until Phase 3 exists and the prereg gate is open.

### S11 — Global calibration offsets (stance / start type)

**Why.** `TASK.md` Phase 2 requires global (not per-player) offsets, with
provenance `FULL` in a full run. The sample file currently carries a
`PLACEHOLDER` with no estimates (blocker B8).

```bash
python src/run.py 02_combine_physical_time --mode full --force
cat outputs/calibration.json
```

**Look at:** `status` must be `ESTIMATED`, `provenance` must start with `FULL`,
and the offsets must be global scalars (one per stance/start level), not a
per-player vector. **Decision rule:** if stance/start labels are still absent,
the file stays `NOT_ESTIMABLE` and the full run must stop — do not substitute a
fitted player effect for the offset.

### S12 — Is `tau` stable, and does the parametric cross-check disagree where it matters?

**Why.** The exponential fit is only a cross-check, but at full scale it is worth
confirming that its disagreement with the model-free curve is a *small, depth-
monotone* offset rather than a depth-dependent artefact. The current run shows a
median +0.077 s at 0.25 yd falling to +0.020 s at 10 yd.

```bash
python src/run.py 02_combine_physical_time --mode full --force
python - <<'PY'
import json
s = json.load(open("outputs/phase2/phase2_summary.json"))
print(s["crosscheck"]["tau_s"])
print(s["crosscheck"]["disagreement_param_minus_modelfree_by_distance"])
PY
```

**Look at:** the sign and depth profile of the disagreement, and the between-
attempt spread of `tau_hat`. **Decision rule:** the estimand stays model-free
regardless; if the disagreement is not small at the in-game depths (0.5-2 yd),
say so and do not present the fitted curve as an alternative estimate.

### S13 — Per-depth posterior SEs carried into Phase 4

**Why.** `TASK.md` says "carry posterior SEs forward". Phase 4's measurement-error
model needs the per-player SE at each in-game depth, and the audit showed the
Combine instrument is weakest at exactly those depths.

```bash
python src/run.py 02_combine_physical_time --mode full --force
python - <<'PY'
import csv, collections, statistics
rows = [r for r in csv.DictReader(open("outputs/phase2/physical_time_by_player.csv"))
        if r["pooling_group"] == "primary_dl"]
for d in ("0.5", "1.0", "1.5", "2.0"):
    se = [float(r["posterior_sd_s"]) for r in rows
          if r["distance_yd"] == d and r["posterior_sd_s"]]
    print(d, len(se), statistics.median(se), max(se))
PY
```

**Look at:** the median and upper tail of `posterior_sd_s` at 0.5-2 yd relative to
the between-player SD (`sqrt(tau2_s2)` in `hierarchical_fit.csv`). If the SE is a
large fraction of the between-player SD, the regression-calibration step is
load-bearing, not optional. **Decision rule:** report the ratio; do not plug
`phys_i` in as if observed when the ratio is large.
