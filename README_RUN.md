# README_RUN — how to run the BDB27 burst-vs-jump pipeline

Binding spec: `TASK.md` (commit `bcfd48b`). This file is the run book; the
analysis notes are in `notes/`.

---

## 1. What this pipeline is

Split between-player differences in a defensive lineman's **get-off** into a
**physical** part measured at the Combine and a residual **"jump"**, then test
whether the residual is a stable trait that matters on the field.

Run order (fixed, strictly sequential):

```
01_audit -> 02_combine_physical_time -> 03_game_snaps -> 04_models -> 05_validate
```

**Status of each stage on 2026-11-05:**

| stage | status | what it does |
|---|---|---|
| `01_audit` | **complete** | Phase 1 items a–i, gate decision |
| `02_combine_physical_time` | **complete (sample)** | Phase 2: onset, model-free time-at-distance, hierarchical pooling, calibration placeholder |
| `03_game_snaps` | **stub** — exits 3 | Phase 3; needs full game tracking |
| `04_models` | **stub** — exits 3 | Phase 4; needs 02 and 03 |
| `05_validate` | **stub** — exits 3 | Phase 5; needs 04 |

A stub **exits non-zero and writes nothing**, so a layout-only run can never be
mistaken for a finished one. "Complete (sample)" means the stage is implemented
and tested, and runs on the complete Combine file; `--mode full` still refuses
without a frozen prereg and cannot complete without a `FULL` calibration.

## 2. Clone and set up

```bash
git clone <repo> BDB27 && cd BDB27
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` is pinned for ARM64 and x86_64. On the Pi, `pandas`, `pyarrow`
and `duckdb` all have aarch64 wheels; nothing needs compiling.

## 3. Data

`data/raw` is a **symlink** to the USB volume:

```
data/raw -> /mnt/project_data/conor_downloads/bdb27
data/interim -> /mnt/project_data/conor_downloads/bdb27_interim
```

Required files in `data/raw`:

| file | needed for | present here |
|---|---|---|
| `combine_tracking.csv` | Phase 1 (a), (e), (h); Phase 2 | yes (84 MB) |
| `combine_results.csv` | Phase 1 (a), (b) | yes |
| `players.csv` | Phase 1 (b) | yes |
| `games.csv` | Phase 1 (b), Phase 3 | yes |
| `player_play.csv` | Phase 1 (b), (d), (f), (g) | yes (147 MB) |
| `game_tracking_2023_sample.csv` | Phase 1 (c), (d), (e) | yes (94 MB) |
| `game_tracking_{2023,2024,2025}.csv` | `--mode full` only | **no** |

**Download (not done here — no Kaggle credentials on this machine).** On a
machine with `~/.kaggle/kaggle.json`:

```bash
kaggle datasets download -d nfl/nfl-big-data-bowl-2027 -p data/raw --unzip
```

The download step of `TASK.md` Phase 0 is satisfied by the local files above; no
network access was used or needed.

## 4. Run

```bash
python src/run.py --list                       # stages and order
python src/run.py 01_audit --mode sample       # ~43 s, ~440 MB peak
python src/run.py 01_audit --mode sample --force
python src/run.py 02_combine_physical_time --mode sample          # ~10 s, ~300 MB
python src/run.py 02_combine_physical_time --mode sample --force
```

`--mode sample|full` and `--force` are defined once, in `src/common.py`; every
stage takes the same three flags (`--mode`, `--force`, `--config`).

`--mode full` **will refuse to run here**, by design, for three independent
reasons it reports explicitly: the three season files are missing; the row count
does not exceed the sample cap; `notes/prereg.md` does not exist.

### What runs on the Pi

| stage | Pi (4 GB) | notes |
|---|---|---|
| `01_audit` | **yes** | 43 s, 440 MB |
| `02_combine_physical_time` | **yes** | 10 s, 273–303 MB; Combine is 463 k rows / 84 MB |
| `03_game_snaps` | sample only; full needs 2–4 GB peak | stream per game; see `notes/scale.md` |
| `04_models` | **no** | PyMC/Stan belongs on the bigger machine |
| `05_validate` | sample only | refits the model |

## 5. Outputs

| path | contents |
|---|---|
| `outputs/audit/audit_items.csv` | 64 rows: item, name, status, value, unit, scope, evidence |
| `outputs/audit/gate.json` | machine-readable go/no-go with per-condition status |
| `outputs/audit/combine_drill_inventory.csv` | drills available to DL |
| `outputs/audit/dl_rusher_counts.csv` | reps and games per rusher |
| `outputs/audit/onset_combine_attempts.csv` | per-attempt onset and time-to-distance |
| `outputs/audit/units_measured.json` | measured extremes + tracking contract |
| `outputs/phase2/physical_time_by_player.csv` | **Phase 2 deliverable**: physical time vs distance, per player, posterior mean + SE |
| `outputs/phase2/combine_attempts.csv` | one row per attempt: onset, QC flags, parametric fit |
| `outputs/phase2/combine_attempt_times.csv` | attempt × distance: model-free time, cross-check, gap policy |
| `outputs/phase2/combine_attempt_curves.csv` | per-frame elapsed/along curve for exact-depth interpolation |
| `outputs/phase2/hierarchical_fit.csv` | variance components per distance and pooling group |
| `outputs/phase2/position_conflicts.csv` | Q3 excluded/conflicting players with rep counts |
| `outputs/phase2/phase2_summary.json` | counts, QC flags, cross-check disagreement, design floor |
| `outputs/calibration.json` | **PLACEHOLDER** in sample mode; no estimates |
| `outputs/run_log.csv` | one row per stage run: wall time, peak RSS, provenance |

Every file carries a `provenance` column or key. In `--mode sample` it reads
`PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT`.

## 6. Tests

```bash
python -m pytest -q          # ~4 min on the Pi, 142 tests
python -m pytest -q -k "not audit_stage_is_byte_identical"
```

The suite covers: schema/dtype/unit mismatches (each must **raise**), the tracking
contract, provenance stamping, the full-run gates, onset/crossing primitives
against known-answer series, reliability estimators, run order and stub exit
codes, determinism (byte-identical re-runs of Phases 1 and 2), Phase 2's gap
policy (inside/outside the first second, under/over 0.5 s), backward-start QC,
hierarchical shrinkage and its degenerate fallbacks, the calibration full-mode
gates, and the edge cases named in `TASK.md`. Tests needing the raw data **skip**
with a reason rather than silently passing.

## 7. Notes map

| file | what it answers |
|---|---|
| `notes/phase1-audit.md` | items a–i with evidence, and the gate decision |
| `notes/schema.md` | every file, column, type, unit; sample provenance; mismatch log |
| `notes/rules.md` | competition limits, with provenance and what is unverified |
| `notes/blockers.md` | B1–B8: what is blocked and what would clear it |
| `notes/limitations.md` | the threat/mitigation list |
| `notes/assumptions.md` | unverified beliefs about full data + their runtime assertions |
| `notes/scale.md` | measured memory/time and the full-data extrapolation |
| `notes/pending_full_run.md` | questions only full data can answer, with the command for each |
| `notes/decisions.md` | D84–D100 for this cycle (append-only) |

## 8. Repo layout

```
data/raw        -> USB (read-only inputs, gitignored)
data/interim    -> USB (parquet, checkpoints, tmp)
src/common.py   infra: config, DuckDB, Parquet, provenance, run log, gates
src/schema.py   verified data contract + unit and vocabulary assertions
src/physical.py onset detection, travel direction, time-at-distance
src/stats.py    ICC, split-half, power approximation
src/synthetic.py schema-compliant fixtures with planted burst and jump
src/01_audit.py ... 05_validate.py
src/run.py      dispatcher
tests/          pytest suite (testpaths = tests)
notes/          analysis notes
outputs/        generated artefacts (gitignored except figures/)
archive/        retired analyses, preserved with git history
```

## 9. Known non-obvious behaviour

* **`wc -l` lies on `player_play.csv`** — 12,823 embedded newlines. Use DuckDB.
* **`dis` is per-frame yards, not cumulative distance.** Rebuild distance from
  `x`/`y`.
* **`combine_tracking` contains a `BALL` entity** with speeds up to 25.8 yd/s.
  Filter `entity_type = 'PLAYER'`.
* **`player_play.lined_up_position` uses `EDGE`/`INTERIOR_LINE`, never
  `DE`/`DT`/`NT`.**
* **DuckDB must be told `nullstr=['NA']`** or the pressure columns misparse.
* **Phase 2 calibration is a placeholder, not a zero offset.** The file has
  `estimates = null`; `common.require_calibration_full` refuses it in full mode.
* **Two Combine attempts walk backwards >0.5 yd** before starting forward. They
  are flagged `qc_exclude` and left out of pooling, not re-origined at the
  turnaround (D99).
