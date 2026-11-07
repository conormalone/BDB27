# README_RUN — how to run BDB27 Turn Coverage

**Status: the notebook is the interface. `notebooks/bdb27_full_run.ipynb` runs the seven
stages in spec order and renders the headline from the audit artefacts.**
Phases 0–3 are implemented (stage 02 is the kinematics/turn table, stage 03 the turn
envelope); phases 4–6 (stages 04–06) are still loud stubs.

## 1. Environment

```bash
git clone <repo> BDB27 && cd BDB27
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # pinned, ARM64 + x86_64 wheels
```

Python 3.13 is what this repo is tested on; nothing needs a compiler. `requirements.txt`
includes `nbformat`, `nbconvert` and `ipykernel` for the notebook.

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
| `--mode full` | `game_tracking.csv` (full tracking; Kaggle ships it per season as `game_tracking_{2023,2024,2025}.csv`) | **no** — the notebook reports `PENDING FULL RUN` and names every missing file |

## 3. Run it — the notebook (primary interface)

```bash
jupyter notebook notebooks/bdb27_full_run.ipynb     # then Run All
# or, headless:
jupyter nbconvert --to notebook --execute notebooks/bdb27_full_run.ipynb \
    --output /tmp/bdb27_run.ipynb --ExecutePreprocessor.timeout=1800
```

Set `MODE` in §1; nothing below that cell needs editing.

* `MODE="sample"` exercises every implemented code path on the sample and stamps every
  artefact `UNVALIDATED SAMPLE OUTPUT`. No figure is produced and nothing is interpreted.
* `MODE="full"` is the final run. If the full game tracking is absent the notebook prints
  `PENDING FULL RUN`, **names the configured file and all three season files**, and runs
  nothing — it never falls back to the sample.
* `DATA_DIR` points the run at another data directory (a scratch config is written under
  `data/interim/tmp/`; its `config_sha` will differ from the committed evidence).
* `FORCE_STAGES` rebuilds named stages; `ONLY_STAGES` runs a subset; `CLEAN=True` with
  `CLEAN_CONFIRM="DELETE"` *moves* this chain's outputs and checkpoints to
  `data/interim/tmp/cleaned-<utc>/` (recoverable; `data/raw` is never touched).

The notebook prints one row per stage — exit code, wall time, status — then the headline
(gate verdict, why, primary group, recommended smoother, G1–G9, items a–m) read only from
`outputs/audit/`. A stub reads `NOT IMPLEMENTED (stub, exit 2)` and is never counted as a
pass. The committed notebook carries **zero baked outputs**.

## 4. Run it from the shell (fallback — same stages, same order)

```bash
python src/run.py 00_thresholds --mode sample     # writes outputs/thresholds.json
python src/run.py 01_audit       --mode sample     # writes outputs/audit/*
python src/run.py 02_turns       --mode sample     # writes outputs/turns/*
python src/run.py --mode sample                    # the whole chain; stops at 03
```

Individual stages, with `--force` to overwrite and `--config` for another file:

```bash
python src/01_audit.py --mode sample --force
python src/run.py 01_audit --mode sample --force --config /path/to/config.yaml
```

The shell runner stops at the first non-zero stage; the notebook does not — it reports every
stage so the headline can show what exists.

## 5. What exists

| stage | file | status | writes |
|---|---|---|---|
| 00 thresholds | `src/00_thresholds.py` | implemented | `outputs/thresholds.json` |
| 01 audit | `src/01_audit.py` | implemented (items a–m) | `outputs/audit/{audit_items.csv,gate.json,schema_*.json,…}` |
| 02 turns | `src/02_turns.py` | implemented (Phase 2) | `outputs/turns/{turns,attempts,summary}.parquet`, `sanity.csv`, `smoother_counts.csv`, `segmentation_sensitivity.csv`, `geometry_recovery.csv`, `manifest.json` |
| 03 envelope | `src/03_envelope.py` | implemented (Phase 3) | `outputs/envelope/{envelope_players,feasible_region}.parquet`, `envelope_fit.csv`, `variance_components.csv`, `envelope_modelfree.csv`, `feasible_region_summary.csv`, `manifest.json` |
| 04 demand + coverage | `src/04_demand_coverage.py` | **loud stub** | nothing |
| 05 validate | `src/05_validate.py` | **loud stub** | nothing |
| 06 figures | `src/06_figures.py` | **loud stub** | nothing |

A stub prints `STAGE NOT IMPLEMENTED`, exits 2, writes no artefact and creates no
checkpoint — a layout-only run can never look finished.

## 6. Expected runtime and memory (this host: Raspberry Pi, 8 GB)

| stage | wall time | peak RSS | notes |
|---|---:|---:|---|
| 00_thresholds | < 1 s | < 100 MB | pure config; skips if the file exists |
| 01_audit | **~2 min 45 s** | **~1.1 GB** | first run also converts CSVs to Parquet (~1 min extra) |
| 02_turns | **~7 min** | **~400 MB** | six smoothers on every attempt/snap plus the shuffled null; Combine points are held in memory, game tracking is chunked |
| 03_envelope | **~2 min 24 s** | **~206 MB** | fixed-`tau2` penalised quantile fits plus 200 player-weighted bootstrap replicates per group and the player-grouped held-out cross-check; no EM cycle (was ~9 min 48 s) |
| 04–06 | instant | — | stubs |
| whole notebook | **~10 min** | — | same work as the shell chain |

The audit converts each raw CSV to Parquet once under
`data/interim/parquet/<mode>/`; later runs skip the conversion. Game tracking is
processed in chunks of `resources.game_chunk_games` (12) games — nothing loads a
full game file into pandas.

## 7. Provenance rules (do not break these)

* `--mode sample` stamps every artefact `UNVALIDATED SAMPLE OUTPUT` and sets
  thresholds provenance to `PLACEHOLDER_SAMPLE`.
* `--mode full` writes `FULL` **only after** the full inputs exist, and stages 02–06
  assert `FULL` before doing anything.
* Nothing in this repo may be interpreted, reported or tuned against sample results.
* The notebook takes its banner from the artefact itself, not from the `MODE` variable.
* `notes/prereg.md` does not exist yet **on purpose**: it must fix the smoother,
  definitions, bin edges, envelope form, coverage formula and thresholds first, and
  a full run is refused until it does.

## 8. Tests

```bash
pytest -q          # 56 tests, no network, no full data needed
```

Covers: schema assertions on the real files (including that the spec's "no `s`/`a` in
Combine" claim is refuted), the synthetic fixture round-tripping through the real
loader, planted-turn recovery, stub behaviour, honest full-mode refusal, thresholds
provenance, dispatcher order, determinism, a contract on the gate artefacts, and the
**notebook contract** (`tests/test_notebook_contract.py`: stage order matches the spec and
`src/run.py`, zero baked outputs, missing `gate.json` → `NOT RUN`, a stub is never a pass,
and the full-mode preflight names every missing tracking file). Phase 2 adds
`tests/test_turns_stage.py`: the stage end to end on the fixture, planted-cone
precision/recall and angle recovery, sub-maximal and dropped-frame handling, stop/no-turn
edge cases, every smoother reproducing known speed and heading, cross-smoother spread,
byte-identical determinism, and the full-mode provenance refusal.

## 9. Layout

```
data/raw  data/interim   inputs (symlinked, gitignored)
src/                     stages 00–06 + bdb27/ shared package
notebooks/               bdb27_full_run.ipynb — the interface (committed unexecuted)
fixtures/synthetic/      committed generator + planted fixture
tests/                   pytest suite
notes/                   rules, schema, decisions, blockers, phase1-audit
outputs/audit/           committed Phase 1 evidence
outputs/turns/           Phase 2 turn table + summaries (gitignored)
outputs/                 thresholds.json, run_log.csv, figures/ (later)
archive/legacy-*/        superseded pipelines, with git history
```
