# README_RUN — how to run BDB27 Turn Coverage

**Status: Phase 0 + Phase 1 implemented. Phases 2–6 are loud stubs.**
The Kaggle notebook click-through interface **does not exist yet** — see
`notebooks/README.md`.

## 1. Environment

```bash
git clone <repo> BDB27 && cd BDB27
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # pinned, ARM64 + x86_64 wheels
```

Python 3.13 is what this repo is tested on; nothing needs a compiler.

## 2. Data

Competition data only — **no external data** (`notes/rules.md`). The files live on the
USB drive and are symlinked:

```
data/raw      -> /mnt/project_data/conor_downloads/bdb27
data/interim  -> /mnt/project_data/conor_downloads/bdb27_interim
```

Required files in `data/raw/` (see `notes/schema.md` for sizes and columns):
`combine_tracking.csv`, `combine_results.csv`, `players.csv`, `games.csv`,
`player_play.csv`, `player_career_successes.csv`, and game tracking.

| mode | game-tracking file | present here? |
|---|---|---|
| `--mode sample` | `game_tracking_2023_sample.csv` (~94 MB, 333 games, row sample) | yes |
| `--mode full` | `game_tracking.csv` (full tracking) | **no** — full mode refuses and names it |

## 3. Run order

```bash
python src/run.py 00_thresholds --mode sample     # writes outputs/thresholds.json
python src/run.py 01_audit       --mode sample     # writes outputs/audit/*
python src/run.py 02_turns       --mode sample     # STUB: exits 2, writes nothing
python src/run.py --mode sample                    # the whole chain; stops at 02
```

Individual stages, with `--force` to overwrite and `--config` for another file:

```bash
python src/01_audit.py --mode sample --force
python src/run.py 01_audit --mode sample --force --config /path/to/config.yaml
```

## 4. What exists

| stage | file | status | writes |
|---|---|---|---|
| 00 thresholds | `src/00_thresholds.py` | implemented | `outputs/thresholds.json` |
| 01 audit | `src/01_audit.py` | implemented (items a–m) | `outputs/audit/{audit_items.csv,gate.json,schema_*.json,…}` |
| 02 turns | `src/02_turns.py` | **loud stub** | nothing |
| 03 envelope | `src/03_envelope.py` | **loud stub** | nothing |
| 04 demand + coverage | `src/04_demand_coverage.py` | **loud stub** | nothing |
| 05 validate | `src/05_validate.py` | **loud stub** | nothing |
| 06 figures | `src/06_figures.py` | **loud stub** | nothing |

A stub prints `STAGE NOT IMPLEMENTED`, exits 2, writes no artefact and creates no
checkpoint — a layout-only run can never look finished.

## 5. Expected runtime and memory (this host: Raspberry Pi, 8 GB)

Measured on the 2026-11-06 sample run:

| stage | wall time | peak RSS | notes |
|---|---:|---:|---|
| 00_thresholds | < 1 s | < 100 MB | pure config |
| 01_audit | **~2 min 45 s** | **~1.1 GB** | first run also converts CSVs to Parquet (~1 min extra) |
| 02–06 | instant | — | stubs |

The audit converts each raw CSV to Parquet once under
`data/interim/parquet/<mode>/`; later runs skip the conversion. Game tracking is
processed in chunks of `resources.game_chunk_games` (12) games — nothing loads a
full game file into pandas.

## 6. Provenance rules (do not break these)

* `--mode sample` stamps every artefact `UNVALIDATED SAMPLE OUTPUT` and sets
  thresholds provenance to `PLACEHOLDER_SAMPLE`.
* `--mode full` writes `FULL` **only after** the full inputs exist, and stages 02–06
  assert `FULL` before doing anything.
* Nothing in this repo may be interpreted, reported or tuned against sample results.
* `notes/prereg.md` does not exist yet **on purpose**: it must fix the smoother,
  definitions, bin edges, envelope form, coverage formula and thresholds first, and
  a full run is refused until it does.

## 7. Tests

```bash
pytest -q          # 36 tests, no network, no full data needed
```

Covers: schema assertions on the real files (including that the spec's "no `s`/`a` in
Combine" claim is refuted), the synthetic fixture round-tripping through the real
loader, planted-turn recovery, stub behaviour, honest full-mode refusal, thresholds
provenance, dispatcher order, determinism, and a contract on the gate artefacts.

## 8. Layout

```
data/raw  data/interim   inputs (symlinked, gitignored)
src/                     stages 00–06 + bdb27/ shared package
fixtures/synthetic/      committed generator + planted fixture
tests/                   pytest suite
notes/                   rules, schema, decisions, blockers, phase1-audit
outputs/audit/           committed Phase 1 evidence
outputs/                 thresholds.json, run_log.csv, figures/ (later)
archive/legacy-*/        superseded pipelines, with git history
```
