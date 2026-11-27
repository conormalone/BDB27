# Rebuild spec — BDB27 from scratch

**Status:** PROPOSED — awaiting Conor. **Spec only:** this cycle writes no code, moves no files,
deletes nothing.
**Date:** 2026-11-26
**Author:** PM (BDB27)
**Binding input:** `TASK.md` @ `9554769`. Where this spec and `TASK.md` disagree, `TASK.md` wins.
**What it replaces:** the tree. Not the task, not the claim, not the artefacts.

---

## 0. The instruction this implements

> "I'm going to say again, the existing files and file structures will be gone, I want to redo it
> from scratch." — Conor, 2026-11-26 15:31

Read literally. The target is a **new tree built bottom-up**, and the old one is retired only once
the new one reproduces the numbers. This is not a refactor, not a migration, and not a
reorganisation that keeps today's module boundaries because they happen to exist. If a module in
today's `src/` survives in name only, that is a coincidence, not a requirement.

Consequences that follow from the sentence, and that this spec holds to:

- No "move `figures.py`, split it later". Nothing is split; `figures.py` (12,704 lines) is not a
  unit of work.
- No preserved public API. Every import path in the new tree is new.
- No compatibility shims, no re-export modules, no deprecation cycle.
- The old tree is **not deleted in this cycle**. Conor said the files will be gone; this spec says
  *when* they may go (§8), not that they go now.

---

## 1. What the study needs functionally — derived from `TASK.md`

These are the jobs, not the current files. Each is traced to the line of `TASK.md` that requires
it. A module that does not serve one of F1–F11 is not in the tree.

| # | Functional need | Where `TASK.md` requires it |
|---|---|---|
| **F1** | Read both tracking sources (Combine attempts, game player-snaps) at ~10 Hz **without loading a full CSV**, in chunks, with explicit column selection and a declared peak-RAM budget. | "Memory-safe code. Never load full CSVs into pandas; Parquet plus DuckDB or lazy Polars… wall time and peak RAM to `outputs/run_log.csv`." |
| **F2** | **One** smoothing → derivative → candidate → validity path applied to both settings, with the 30° floor and the six band edges in one place. | "One code path"; block 1; honesty limit 2. The report draft: "One detector for both settings is what makes the comparison legitimate." |
| **F3** | Emit one turn table per setting with angle, entry/exit/apex speed, plant/arc label, band, player, drill or play, and a validity flag. | Blocks 1–3 all count from the same turn table. |
| **F4** | Aggregate counts and **within-setting** shares by band, and the game ÷ Combine share ratio. | Block 1: "Shares are of each setting's own valid turns." |
| **F5** | Repeat F4 by position group (DL, WR, TE, OL) for the cross-position exhibit. | Block 1 last paragraph; Figure 2. |
| **F6** | Per-drill Combine medians (angle, entry, exit, apex) and plant share. | Block 3: "Measured per drill, median turn angle, entry speed, exit speed and apex speed." |
| **F7** | Shallow-band provenance: how many 30–60° turns, from how many players and drills, turns per player, and how many are 3-cone fragments. | Block 2, in full. |
| **F8** | Per-band shape statistics (entry speed, apex retention, plant share) sufficient to separate the arc family from the plant family. | Block 3: "Plant-ness rises with angle… 15.3% … against 97.7%." |
| **F9** | Re-run turn assignment under alternative smoothers, the 2.0 yd/s floor and the alternative segmentation rule; report the movement **and** the band-share range. | Honesty limits 1 and 3; D190 control pass (`combine_db_turns_floor2.5_control.csv`, 1,179 rows). |
| **F10** | Render **six** figures — the set named in `TASK.md` §Figures — each with a provenance stamp and machine-readable sidecars (CSV, PNG, provenance JSON, drawn-text JSON). | "Figures — six, and these six are the figure set"; "These six are the figure set". |
| **F11** | Tie every published number to a named artefact cell; refuse to publish an untraced number. | "If a number is not in a named artefact, it does not go in the writeup"; "No invented data." |
| **F12** | Run from a notebook, Run All, no terminal. *(Standing constraint from Conor, not from `TASK.md`.)* | "notebooks are Conor's only interface (no CLI instructions to him, ever)". |

Two standing policies also bind the design and are **not** in `TASK.md`:

- **DB only by default** (D183). All claims are DB. Other position groups are computed **only** for
  Figure 2 and are labelled comparison-only; they never enter the headline.
- **No arbitrary gates** (Conor, 2026-11-15). No stage may branch on a count, a share, a player
  count or a coverage level. Diagnostics print numerator and denominator; they do not raise.

---

## 2. The target tree

Every file is named. The package is `bdb27/` at the repo root; the repo root is **not** wrapped in
a second `bdb27/`.

```
BDB27/
├── config.yaml                     # the single source of every threshold, path, seed and cap
├── pyproject.toml                  # package metadata + pytest config (pythonpath, markers)
├── requirements.txt                # pinned runtime dependencies
├── bdb27/
│   ├── __init__.py                 # version string only
│   ├── config.py                   # load + validate config.yaml; typed accessors; unknown/missing key fails
│   ├── provenance.py               # mode stamp, input sha256, git rev, run_log rows (wall s, peak RSS)
│   ├── schema.py                   # declared column contract for every frame crossing a module boundary
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── roster.py               # players.csv -> nfl_id/position_group; the only group definition
│   │   ├── combine.py              # combine_tracking.csv -> canonical attempt frames, chunked by event
│   │   └── games.py                # game_tracking_<season>.csv -> canonical player-snap frames, per game
│   ├── turns/
│   │   ├── __init__.py
│   │   ├── smooth.py               # the six-smoother registry; primary savgol g9 p3; all smoothing code
│   │   ├── detect.py               # derivatives, 30° floor, segmentation, validity caps, candidate rows
│   │   ├── classify.py             # band assignment, plant/arc rule, entry/exit/apex/retention
│   │   └── table.py                # both settings -> one turn-table schema; writes Parquet + manifest
│   ├── measure/
│   │   ├── __init__.py
│   │   ├── bands.py                # setting x group x band counts/shares + ratio: the band table
│   │   ├── drills.py               # per-drill medians, plant share, shallow-band provenance
│   │   ├── shape.py                # per-band entry/exit/apex/retention/plant share; arc vs plant
│   │   └── sensitivity.py          # smoother shift, floor 2.5/2.0, segmentation, ten-row drill delta
│   ├── figures/
│   │   ├── __init__.py
│   │   ├── common.py               # stamp, CSV+PNG+provenance+drawn-text sidecars, four-entry manifest
│   │   ├── hole.py                 # Figure 1 — fig_the_hole
│   │   ├── positions.py            # Figure 2 — fig_all_positions
│   │   ├── scatter.py              # Figure 3 — fig_drill_angle_scatter (stamp per §13 U3)
│   │   └── schematic.py            # Figure 4 — fig_drill_schematic (SCHEMATIC)
│   ├── report/
│   │   ├── __init__.py
│   │   └── claims.py               # every drafted number -> (artefact, field, sha); resolves or fails
│   ├── analyses/
│   │   ├── __init__.py             # empty namespace; may import core, core may never import it
│   │   └── README.md               # one question per subpackage; delete the subpackage to remove it
│   └── run/
│       ├── __init__.py
│       └── main.py                 # seven stage slots, --mode sample|full, loud NOT BUILT, run_all()
├── notebooks/
│   ├── bdb27_run.ipynb             # Conor's interface: MODE, Run All; artefact table, gallery, claims
│   └── README.md                   # what Run All does; contains no CLI instruction
├── tests/
│   ├── conftest.py
│   ├── test_layers.py              # dependency DAG, folder order, no cycles, analyses isolation
│   ├── test_config.py              # one home per constant, no orphan keys, no magic-number literals
│   ├── test_schema.py              # turn/band/drill/shape schemas and domains
│   ├── test_ingest.py              # inventory, sha256, missing-path naming, chunk boundaries
│   ├── test_turns.py               # smoother parity, detector fixtures, validity rules
│   ├── test_measure.py             # band table equality, drill and shape numbers, floor deltas
│   ├── test_figures.py             # six figures only, stamps, sidecar pairing, CSV equality
│   ├── test_claims.py              # every claim resolves to an artefact cell and sha
│   ├── test_notebook.py            # no outputs, Run All order == STAGES, gallery reads the manifest
│   ├── test_no_gates.py            # AST scan: no control flow gated on a count
│   └── fixtures/                   # tiny committed CSVs, schema-compliant
├── outputs/                        # tracked artefacts of record (§6)
├── data/                           # raw + interim; symlinked, not tracked
└── notes/                          # decisions.md, blockers.md, this spec, report draft
```

Output folders mirror the stage that writes them, one folder per stage, so a stage's output can be
deleted without touching another stage's:

```
outputs/thresholds.json   outputs/run_log.csv
outputs/audit/            outputs/turns/       outputs/bands/
outputs/drills/           outputs/shape/      outputs/sensitivity/
outputs/figures/          outputs/report/
```

---

## 3. Module count and what it replaces

**21 importable modules** (29 Python files including the eight `__init__.py` markers), plus one
notebook and ten test files.

| Today | New |
|---|---|
| `src/` 35 files / 32,668 lines, of which `figures.py` is 12,704 lines | 21 modules, none over ~600 lines |
| `scripts/` 11 files / 2,926 lines, unowned one-offs | 0 — every script becomes a stage or is dropped |
| `tests/` 37 files | 10 files, one per contract |
| `notebooks/` 7 notebooks | 1 |
| `config.yaml` 51 KB, one active block among dead ones | one file, every key reachable |

The old tree's 46 Python files are not "consolidated"; they are replaced. Nothing is owed to them.

---

## 4. Where this departs from Main's skeleton

Main's shape (15:35) was a proposal, not a requirement. I overturn five points and keep four.

| Main's item | Verdict | Why |
|---|---|---|
| `config/` — one file per stage | **Overturned** | `TASK.md` binds config to **`config.yaml`** ("config lives in `config.yaml`, no magic numbers"). A config *package* creates a second home. One `config.yaml` + `bdb27/config.py`, and a test that every key is read exactly once. |
| `capacity/` — combine-derived feasible region | **Deleted** | `TASK.md`: "No envelope or coverage arithmetic as a deliverable. The feasibility region, coverage share and cut gaps are not part of the argument." A module for it would rebuild the thing the task removed. |
| `coverage/` — Turn Coverage demand-weighted join | **Deleted** | Same line. Turn Coverage and the Cut Card are on the out-of-scope list. |
| `demand/` and `supply/` as separate packages | **Merged** into `measure/bands.py` | The band table is one function applied to two turn tables. Splitting supply from demand invites two share definitions, which is the failure mode F4 exists to prevent. |
| `analyses/` — one folder per question | **Kept, but empty** | Sound, but it is not a home for the four-block evidence. Isolated by test: core may never import `analyses`. |
| `figures/` one file per plot | **Kept** | Four files plus one `common.py`. |
| `report/` number → sentence | **Kept, narrowed** | `claims.py` checks a number against an artefact cell; it does not write prose. |
| `run/` stage entry points | **Kept** | One `main.py`, seven slots, same 00–06 names as `TASK.md`. |
| `ingest/` and `turns/` | **Kept** | With `turns/smooth.py` split out because the smoother is the most consequential choice in the pipeline (honesty limit 1). |

One further change: Main wrote the tree as `bdb27/` **inside** the repo. It is the package at the
repo root; there is no `BDB27/bdb27/bdb27/`.

---

## 5. The dependency rule, and how it is enforced

### The rule

Five rules, in order. "Earlier" and "later" refer to the package list:

```
config ← ingest ← turns ← measure ← figures ← report ← run
```

- **R1 — packages form a total order.** A module in package *P* may import only from packages
  **strictly earlier** than *P*. It may not import from a later package, from its own package's
  siblings, or from `analyses`.
- **R2 — one same-package exception.** `figures/common.py` may be imported by the figure
  modules; `common.py` itself imports only from earlier packages. No other same-package import is
  allowed.
- **R3 — `analyses/` is downstream only.** An `analyses/*` subpackage may import any core package;
  no core module may import `bdb27.analyses`. Deleting an analysis subpackage must break nothing.
- **R4 — folder order is dependency order.** The package directories under `bdb27/`, sorted as the
  test sees them, must equal the tuple above exactly. A new package cannot be added without
  inserting it into the order and justifying its layer.
- **R5 — one turn definition, one home per constant.** Only `turns/` may produce a turn table or
  compute an angle. Only `config.py` may read a threshold. No numeric literal equal to a declared
  threshold may appear anywhere else.

### The mechanical enforcement

`tests/test_layers.py`:

1. Reads `bdb27/` directory names (excluding `__pycache__`, `__init__.py`), asserts equality with
   `PACKAGE_ORDER` — R1/R4.
2. AST-parses every `bdb27/**/*.py`, collects `ImportFrom` targets starting `bdb27.`, maps each to
   a package index, asserts `target_index < own_index` — R1.
3. Allows exactly the pairs `figures/* -> figures/common` and `__init__ -> anything earlier` — R2.
4. Asserts no AST node anywhere under core imports `bdb27.analyses` — R3.
5. Runs a DFS over the collected module graph and asserts acyclic (belt and braces).

`tests/test_config.py`:

1. Loads `config.yaml`; asserts its key set equals a frozen list in the test, so adding a key is a
   deliberate edit, not a typo.
2. AST-scans `bdb27/**` for numeric literals and fails on any literal equal to a declared threshold
   (30, 60, 90, 120, 150, 180, 2.0, 2.5, 0.5, regression window, speed/accel caps), with
   `config.py` and a structural allowlist (0, 1, 2, indices) excepted — R5.
3. Asserts `savgol_filter`, `np.gradient` and `abs_turn_angle` as an assigned column appear only
   under `turns/` — R5.

`tests/test_no_gates.py`:

1. AST-scans for `if`/`assert` conditions that compare a name matching `n_|count|players|turns|
   share|coverage` against a literal and whose body raises or returns non-zero; fails.
2. Asserts the only control-flow exception classes are `MissingInput(path)`, `UnreadableInput(path)`,
   `MissingColumn(name, expected)`, `NotBuilt(stage)`, and that each message carries the path,
   column or stage.
3. Asserts every diagnostic writer's schema has both a numerator and a denominator column.

These are lints, not proofs. They catch the class of mistake that shipped an NFL-sized
`MIN_MEDIAN_PLAYERS_PER_PLAY` gate and a provenance variable defined in a late cell; they do not
replace review.

---

## 6. Acceptance: module → artefact → number

A module is done when it reproduces the named committed value from raw inputs in one full run.
**Tolerances:** counts exact; shares equal as stored floats (1e-12); medians to 0.1 as printed;
ratios to 2 dp. PNGs are never byte-compared across machines (matplotlib versions differ); the CSV,
`provenance.json` and `drawn_text.json` are.

### Artefacts of record and a provenance warning

Two of the numbers of record are **not in git**: `data/` is gitignored, so
`data/interim/full_run_20261111/bdb27_full_game_bands.csv` and
`.../bdb27_full_db_turns.parquet` exist on disk only. They must be pinned by sha256 in every
manifest, and the band table must be **copied into tracked `outputs/bands/`** by the new stage. A
"committed artefact" that is not committed is not an acceptance target.

| Artefact | Path | sha256 (start) |
|---|---|---|
| Band table, 66 rows | `data/interim/full_run_20261111/bdb27_full_game_bands.csv` | `7cd10b9d…` |
| Full game turn table, 58,664 valid rows | `data/interim/full_run_20261111/bdb27_full_db_turns.parquet` | `f2e7cd01…` |
| Real-Combine control pass, 1,179 rows | `outputs/sensitivity/speed_floor/combine_db_turns_floor2.5_control.csv` | `41b49c19…` |
| Ten speed-floor drill rows | `outputs/sensitivity/speed_floor/drill_delta.csv` | tracked |
| Figure 1 values | `outputs/figures/fig_the_hole.csv` | `0fd0654e…` |

### Stage 00 — `bdb27/config.py`

Writes `outputs/thresholds.json`. Must contain: 30.0° floor; band edges 30/60/90/120/150/180/∞;
primary smoother `savgol_g9_p3`; six smoother names; Combine floors 2.5 (control) and 2.0 (test);
game floor 2.0; 0.5 s window; seed; speed and acceleration caps; the DB group label.
**Done when:** the JSON loads, every stage key resolves, and `test_config.py` passes.

### Stage 01 — `ingest/`

| Module | Writes | Must reproduce |
|---|---|---|
| `roster.py` | `outputs/audit/population.csv` | 510 players mapped; **122** Combine players, **115** game players, **2,234** player-games, **858** games, seasons 2023–2025 |
| `combine.py` | `outputs/audit/combine_inventory.csv` | **1,432** DB attempts, **108,650** frames |
| `games.py` | `outputs/audit/games_inventory.csv` | three season files read in per-game chunks; 2023 file ≈ one third of 2025 for the same game count (honesty limit 9), stated as counts, not a gate |

Hard failures only: a required file is absent (name every path tried and what to set), unreadable,
or missing a required column. Print the inventory **before** raising.

### Stage 02 — `turns/`

| Module | Writes | Must reproduce |
|---|---|---|
| `smooth.py` | `outputs/turns/smoother_counts.csv` | six smoothers × two settings; primary `savgol_g9_p3`; **30–60° share range 8.64%–16.94%** (5,068–9,939 turns of 58,664) across the smoother min/max, with the median per-turn angle shift ≈ **6°** |
| `detect.py` | `outputs/turns/sanity.csv` | floor 2.5: **1,179** detections, 10.851 per 1,000 frames; shuffled null 1,131 (10.410), ratio **0.959** |
| `classify.py` | `outputs/turns/classification.csv` | Combine plant share: 3-cone **37.8%**, short shuttle **97.1%**; game plant share by band **15.3 / 24.3 / 51.5 / 79 / 93 / 98%**, **97.7%** past 180° |
| `table.py` | `outputs/turns/turns.parquet` + manifest | game **58,664** valid DB rows / 115 players; Combine **1,179** / 122; schema exactly as declared; band domain exactly the six strings |

### Stage 03 — `measure/bands.py`

Writes `outputs/bands/bands.csv` (tracked) + `bands_ratio.csv` + manifest.

**Done when** `bands.csv` equals `bdb27_full_game_bands.csv` row-for-row (66 rows: 11 setting-group
pairs × 6 bands), and the ratios are:

- DB 30–60°: **6.01×** (26/1,179 = 2.21% vs 7,777/58,664 = 13.26%)
- DB 120–150°: **0.46×** (42.32% vs 19.51%); the inverse statement **2.17×** is the Combine
  over-supply
- DL **2.12×**, WR **1.68×**, TE **1.35×**, OL **1.43×** at 30–60°

### Stage 04 — `measure/drills.py` and `measure/shape.py`

| Module | Writes | Must reproduce |
|---|---|---|
| `drills.py` | `outputs/drills/drills.csv`, `shallow_provenance.csv` | 3-cone **70.6°**, entry **3.98**, exit **4.96**, apex **3.16**, 98 turns; short shuttle **179.6°**, **3.55**, **5.84**, **0.34**, 70 turns; shallow band **26 turns / 16 players / 9 drills / median 1 / max 4 / 14 three-cone**; 60–90° **237 turns / 110 players**; 3-cone **47 of 98** at **67.1°**; Teryl Austin 2 **74 of 199** at **74.1°**; back-pedal-and-90 **65 of 118** at **84.3°** |
| `shape.py` | `outputs/shape/shape.csv` | band entry **3.68 / 3.63**; apex retention **0.857 / 0.934**; 90–120° plant **51.5%**, retention **0.717**; >120° apex medians **1.90 / 1.04 / 0.58**; >180 plant **97.7%** |

All of the drill numbers above were re-derived from the tracked control CSV while writing this
spec, so they are safe acceptance targets. The plant shares come from the plant rule and are
carried from `TASK.md`; the rebuild must match the rule, not a new one.

### Stage 05 — `measure/sensitivity.py`

Writes `outputs/sensitivity/floor_bands.csv`, `drill_delta.csv`, `nulls.csv`, `smoother_shift.csv`.

**Done when:**

- floor 2.5 control returns **1,179** and the exact six band counts 26/237/159/499/128/130;
- floor 2.0 returns **1,463** with 30–60° **32** (2.19%) and 120–150° **684**;
- `drill_delta.csv` reproduces all **ten** rows, including `THREE_CONE_DRILL` 98→112 turns and
  14→13 shallow, and `W_DRILL` 107→272 with 94→224 in 120–150°;
- the smoother range and the ~6° median shift are written to `smoother_shift.csv`.

### Stage 06 — `figures/`

Six figures, the set named in `TASK.md` §Figures. Each writes `.csv`, `.png`, `.provenance.json`, `.drawn_text.json`, and one
entry in `outputs/figures/figures_manifest.json`; all six must appear in the manifest, and no other
figure may be named in the paper.

| # | File | Stamp | CSV must equal |
|---|---|---|---|
| 1 | `fig_the_hole` | `FULL` | DB rows of `bands.csv`; headline **6.01×** |
| 2 | `fig_all_positions` | `FULL` | the four comparison groups' 30–60° rows; **2.12 / 1.68 / 1.35 / 1.43×** |
| 3 | `fig_drill_angle_scatter` | `PLACEHOLDER_SAMPLE` unless §13 U3 changes it | 1,179 control rows; nothing quoted from it |
| 4 | `fig_drill_schematic` | `SCHEMATIC` | no measurement; geometry only |

### Trace — `report/claims.py` (not a stage)

Writes `outputs/report/claims.csv`: one row per published number, with sentence, artefact path,
field, value and sha256. Run by `tests/test_claims.py` and by the notebook.

**Done when** every number in the drafted §3–§5 resolves to a cell with the same value and the
artefact's sha256 matches the manifest; unresolved count is **0**. A number that cannot resolve is
a publish blocker, not a warning.

### Runner — `run/main.py`

Seven stages, same names and order as `TASK.md`: `00_config → 01_ingest → 02_turns → 03_bands →
04_summaries → 05_sensitivity → 06_figures`. A stage not yet built exits **2** with
`NOT BUILT (stage)`. A stage whose upstream artefact is absent exits 2 naming the path.

---

## 7. Build order, in commits that leave the tree runnable

"Runnable" means: `pytest` is green, and `python -m bdb27.run.main --mode sample` either completes
every built stage or stops at the first unbuilt one with a named reason. No commit leaves a
half-written stage that reports success.

| Commit | Contents | Runnable proof |
|---|---|---|
| **C1 — skeleton and contracts** | `pyproject.toml`, `config.yaml`, `bdb27/{__init__,config,provenance,schema}.py`, `run/main.py` with seven `NOT BUILT` stubs, `tests/test_layers.py`, `test_config.py`, `test_no_gates.py`, `test_schema.py` | runner prints the seven stages and stops at `01_ingest`; layer/config/gate tests green |
| **C2 — ingest** | `ingest/{roster,combine,games}.py`, `test_ingest.py`, fixtures | sample inventory prints 510 / 1,432 / 108,650 and names any absent path |
| **C3 — smoothing** | `turns/smooth.py`, unit tests on fixtures | `--stage 02_turns` writes `smoother_counts.csv` in sample mode |
| **C4 — detector and turn table** | `turns/{detect,classify,table}.py`, `test_turns.py` | sample run writes a schema-valid `turns.parquet`; first full run is on the laptop (§8) |
| **C5 — band table** | `measure/bands.py`, `test_measure.py::bands` | reproduces 66 rows and 6.01× |
| **C6 — drill and shape tables** | `measure/{drills,shape}.py` | reproduces block 2 and block 3 |
| **C7 — sensitivity** | `measure/sensitivity.py` | reproduces 1,179 / 1,463 / ten rows / 6° |
| **C8 — figures** | `figures/{common,hole,positions,scatter,schematic}.py` | four manifest entries, stamps correct, CSVs equal |
| **C9 — claims trace** | `report/claims.py`, `test_claims.py` | zero unresolved numbers |
| **C10 — notebook** | `notebooks/bdb27_run.ipynb`, `test_notebook.py` | Run All on the laptop; contract tests green |
| **C11 — parity evidence** | `notes/switchover-evidence.md` only | full run table, no deletion |

C4 is the first expensive commit, and it happens on the machine that holds the full game tracking
(Conor's laptop), not on this Pi. Every later commit's acceptance runs there too. The Pi can build
and test C1–C3 and sample-mode C4–C10.

---

## 8. The switchover

The old tree is **not** deleted in this cycle, and this section does not authorise a delete. It
defines what must be true before one is even proposed.

### Evidence required, in order

1. **Parity table** in `notes/switchover-evidence.md`: every number in §6, old value, new value,
   delta, tolerance. Counts exact; shares 1e-12; medians 0.1; ratios 2 dp.
2. **One full run from raw** on the data host: `MODE="full"`, Run All, clean clone, no manual step,
   no warning about a missing path. Artefacts written under `outputs/`, provenance `FULL`.
3. **sha256 parity** for CSV and JSON artefacts against the committed values. PNGs are compared by
   their CSV, sidecar and drawn text, plus Conor's eye.
4. **Budget**: `outputs/run_log.csv` shows per-stage wall time and peak RSS within the declared
   laptop budget. The number is printed, not gated.
5. **Conor's written go-ahead**, recorded as a new D-number in `notes/decisions.md`, after he has
   run the notebook himself and seen the six figures.
6. **Old HEAD is named** in the evidence file, so the thing being retired is an exact commit.

### Retirement protocol (later cycle, on his word)

- Commit A: `git mv` the old `src/`, `scripts/`, `tests/`, `notebooks/`, and dead `outputs/`
  folders into `archive/legacy-2026-11-26/`. History preserved; nothing lost.
- Commit B: delete `archive/legacy-2026-11-26/`, only after Conor confirms the archive commit is
  what he wants gone.
- Never `rm` in the same commit that lands the new tree. Never delete `outputs/figures/`,
  `outputs/sensitivity/`, `notes/report-draft.md`, `notes/drill-proposal.md` or `TASK.md` as part
  of retirement.

`archive/` today is 80,362 lines / 179 files. It is dead weight and is deleted in the same later
cycle, not now.

---

## 9. Disposition of today's analyses

Recommendations only. Conor decides his paper.

### In the four blocks — survive, rebuilt

| Today | Disposition |
|---|---|
| `outputs/turns/` turn tables | **KEEP** — the spine; regenerate |
| `outputs/sensitivity/speed_floor/` (control 1,179, floor 2.0, ten drill rows) | **KEEP** — honesty limits 1 and 3, D190 |
| `outputs/audit/` schema, population, seasons, smoother sensitivity | **KEEP, narrowed** to inventory/population/seasons |
| `outputs/thresholds.json`, `outputs/run_log.csv` | **KEEP** |
| `outputs/figures/fig_the_hole`, `fig_all_positions`, `fig_drill_angle_scatter`, `fig_drill_schematic`, `fig_backpedal_polar`, `fig_population_receivers` | **KEEP** — the six in `TASK.md` §Figures |

### Not in the four blocks — recommend off-paper

| Today | Disposition | Why |
|---|---|---|
| `outputs/turn_importance/` (D223) | **OFF-PAPER; Conor's call.** Can live as `analyses/turn_importance/` | `TASK.md`: "Nothing beyond these four blocks belongs in the writeup." Its headline (30–60° is the mildest band) is a separate, sound analysis, but citing it needs a `TASK.md` change first — see §12 D1 |
| `outputs/band_reliability/` (ICC 0.003) | **OFF-PAPER; delete by default.** Keep only if he wants the "cannot score a player" paragraph evidenced | Block 2 already settles it by construction: max one shallow turn per player. The ICC is corroboration, not a new number |
| `outputs/fallback_validation/` | **DELETE** | Its discriminant-validity and speed-coverage claims are now made directly by `drills.csv` and `shape.csv` |

### Out of scope by `TASK.md` — delete

| Today | Why |
|---|---|
| `outputs/envelope/` | "No envelope or coverage arithmetic as a deliverable" |
| `outputs/demand_coverage/` (Turn Coverage, Cut Card) | Out-of-scope list; "No per-prospect anything" |
| `outputs/outcome_link/`, `outputs/outcome_test/` | "No outcome test" |
| `outputs/geometric_attribution/`, `_sample/` | "No per-prospect anything"; attribution is not in scope |
| `outputs/ambiguity_attribution/`, `_received/` | Same; multi-candidate attribution |
| `outputs/proof_d202/`, `proof_d202_final/` | Attribution proofs for a superseded question |
| `outputs/population_shrink_received/`, `outputs/turn_context/`, `outputs/reliability_figures/` | Per-player/shrinkage/context work outside the claim |

### The other nineteen figures

`fig_drill_coverage`, `fig_before_after`, `fig_measurement_limits`, `fig_gap_honest`,
`fig_turn_cost`, `fig_stopwatch`, `fig_both_hips`, `fig_shallow_sources`, `fig_ledger`,
`fig_bars_of_doubt`, `fig_supply_composition`, `fig_band_only`, `fig_entry_exit_speed`,
`fig_movement_family`, `fig_turn_angle_hist_30_90`, `fig_odd_vs_even`, `fig_season_vs_season`,
`fig_population_shrink`, `fig_backpedal_route`.

**DELETE from the figure stage.** No other figure is named in the writeup, the notebook or the
draft (`TASK.md` §Figures, six and no more). The underlying measurements survive in `shape.csv` and
`sensitivity.csv`; the pictures do not.

### Notebooks

Keep `bdb27_run.ipynb`. The other six are tied to analyses that are off-paper or out of scope; they
leave with the old tree. `bdb27_turn_importance.ipynb` survives only if D1 says turn importance
survives.

---

## 10. The notebook story

`notebooks/` is the interface; `bdb27/run/` is the machinery. They are not alternatives.

`notebooks/bdb27_run.ipynb`:

| § | Cell | Reads | On absence |
|---|---|---|---|
| 1 | Settings: `MODE`, `DATA_DIR`, `FORCE`, `ONLY_STAGES` | nothing | the only cell Conor edits |
| 2 | Preflight | every declared input path + sha256 | prints a present/absent row per path; a missing path is named and the run refuses **before** any stage |
| 3 | Run | calls `bdb27.run.main.run_all(mode, only=…)` | one row per stage: name, exit, wall s, status; unbuilt stage prints `NOT BUILT`; never a pass |
| 4 | Artefacts | `outputs/**/manifest.json` | path, stamp, row count, headline value; absent prints `NOT RUN` |
| 5 | Figures | `outputs/figures/figures_manifest.json` | four entries; PNG displayed **from its path**; missing prints `MISSING` |
| 6 | Claims | `outputs/report/claims.csv` | sentence, value, artefact, sha; unresolved prints `UNRESOLVED` |

Contract (`tests/test_notebook.py`):

1. The committed notebook carries **zero outputs**.
2. §3's stage order equals `bdb27.run.main.STAGES` — the notebook calls `run_all`, it does not
   re-implement the order.
3. §5 reads the manifest, not a literal figure list, and the notebook contains no figure filename.
4. A missing artefact renders a token (`NOT RUN` / `MISSING` / `UNRESOLVED`), never a blank.
5. The notebook contains no shell command and no instruction to use one.

`run/main.py` exists for tests and CI, and to give the notebook one function to call. It is not a
second interface. `notebooks/README.md` says so and contains no terminal instruction.

---

## 11. Constraints carried into every module

- **Provenance.** Every artefact carries `FULL` or `PLACEHOLDER_SAMPLE`; full mode asserts `FULL`
  and refuses a `*_sample` input. No threshold is ever set from the sample. Every manifest records
  input sha256, config sha, git rev.
- **DB only.** Headline and every claim are DB. Other groups appear only in Figure 2, labelled
  comparison-only.
- **No arbitrary gates.** No branch on players, turns, share, coverage or judgeable count. Every
  diagnostic prints numerator **and** denominator. The only exit-2 conditions are: a required file
  is missing (every path tried is named), unreadable, a required column is missing, or a stage is
  not built.
- **No invented numbers.** A number absent from an artefact is absent from the report. Missing or
  ambiguous data goes to `notes/blockers.md` and stops that piece of work, not the run.
- **Memory.** Chunked ingest to Parquet; DuckDB or lazy Polars downstream; no `read_csv` on game
  tracking outside `ingest/`; wall time and peak RSS to `outputs/run_log.csv`.
- **Determinism.** Fixed seed; sorted output; a repeated full run is byte-identical apart from
  timestamps. The check is a test, gated by an env flag so the default suite stays fast.
- **No new analysis.** The seven stages hold the same analyses as `TASK.md`'s 00–06. 03 and 04 are
  reused slots: 03 replaces envelope with the band table, 04 replaces demand-coverage with the
  drill and shape summaries. Nothing new is computed.

---

## 12. Decisions I need from Conor

**D1 — What is the paper, exactly?** `TASK.md` says the writeup is four blocks and "nothing
beyond these four blocks belongs in the writeup", and separately forbids an outcome test. That
excludes turn importance (D223). If turn importance is to be cited, `TASK.md` must change first,
and the rebuild gains an `analyses/turn_importance/` subpackage (module count 21 → 23). Which is
it: TASK as written, or TASK plus an importance section?

**D2 — One `config.yaml`, or Main's `config/` folder?** I have overturned Main and kept the single
file, because `TASK.md` names `config.yaml`. Confirm, or tell me the folder is what you want and
I will write the test around fragments instead.

**D3 — How does the old tree go?** I propose: parity evidence → archive by `git mv` → delete the
archive in a later cycle on your word. That keeps one cycle of recoverability after "gone". The
alternative is a hard delete at switchover. Which?

Secondary, lower stakes: (a) confirm `capacity/` and `coverage/` are dead, as `TASK.md` says;
(b) is derived arithmetic such as 26.3 vs 9.7 turns per player-game admissible, or is only the six
band rows admissible (the draft currently uses the former); (c) does Figure 3 keep its sample
stamp, or should the rebuild promote it to FULL from the 1,179-row control pass?

---

## 13. Uncertainties I am flagging rather than smoothing

**U1 — Two "committed" artefacts are not in git.** The band table and the 58,664-row turn table
live under gitignored `data/interim/`. If they are the numbers of record, either they get tracked
(copy the band CSV into `outputs/bands/`) or acceptance depends on a file that a clean clone does
not have. I have specified the copy and the sha256 pin; someone should confirm this is acceptable.

**U2 — The brief's description of the writeup does not match the file.** The cycle brief says the
writeup rests on "the band table, turn importance, the outcome test and the figures".
`notes/report-draft.md` cites neither turn importance nor an outcome test, and `TASK.md` forbids
the outcome test outright. Either the brief describes a planned writeup I have not seen, or it is
wrong. I have designed to `TASK.md` + the draft and raised D1 rather than guessing.

**U3 — Figure 3's stamp.** `TASK.md` calls it `PLACEHOLDER_SAMPLE`. The 1,179-row control pass is
real Combine data that reproduces the FULL band table exactly (D190), so the rebuilt figure *can*
be stamped FULL/SENSITIVITY without a new run. Whether to keep the sample stamp is a `TASK.md`
change, so I have not decided it.

**U4 — The 6° median hides a wide band-share range.** The FULL smoother sensitivity
(`outputs/turn_importance/test1_smoother_sensitivity.csv`) shows the 30–60° game share moving from
**8.64% to 16.94%** across smoother min/max, against 13.26% recorded. The direction of the claim
survives (still 3.9×–7.7× the Combine's 2.21%), but "about 6° median" understates the effect on
band membership. The rebuild reports both numbers; the writeup currently reports one. This is a
honesty-limit question for Conor, not a technical one.

**U5 — Turn importance is not in this tree.** If D1 includes it, its 84,225-row player-play panel
needs `player_play` context and a regression module that is deliberately absent from the 21. I have
not smuggled it in; it would be an explicit addition.

**U6 — Six smoothers, not five.** The audit table lists `savgol_g5_p2`, `g7_p3`, `g9_p3`, `g11_p3`,
`kalman_cv`, `spline_cubic`. `TASK.md` says "alternative smoothers" without a count. I have
specified six; if the paper's sensitivity section claims five, that needs reconciling.
