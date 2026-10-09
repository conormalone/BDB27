# README_RUN — how to run the BDB27 pipeline

Stage scripts live in `src/` and take `--mode sample|full` and `--force`. All paths/thresholds come from
`config.yaml`. Heavy files are converted to Parquet once (checkpointed) and queried with DuckDB; no full
CSV is loaded into pandas.

## 1. Environment (ARM64 Pi / x86 Linux)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt          # pinned; verified on ARM64, Python 3.13
```

## 2. Data

Raw data is symlinked at `data/raw` → the mounted drive (`/mnt/project_data/conor_downloads/bdb27/`).
Game-tracking full files (`game_tracking_2023/24/25.csv`) are **not** in the repo — mount the drive first.

## 3. Run order (full pipeline)

```
01_audit → 02_features → 03_combine_model → 00_thresholds → 04_game_features → 05_link
```

Currently implemented: **`01_audit.py`** (Phase 0/1). Later stages follow in subsequent cycles.

```bash
# Phase 1 audit (sample)
python src/01_audit.py --mode sample
# rebuild Parquet caches
python src/01_audit.py --mode sample --force

# Phase 1 audit (full; needs the full game files) — audit (e) runs only here
python src/01_audit.py --mode full
```

## 4. Outputs

- `outputs/audit/*.csv` + `audit_summary.json` + `SUMMARY.md` (all stamped with a `provenance` column).
- `outputs/run_log.csv` — wall time + peak RAM per stage (appended each run).
- Sample-mode outputs are labelled **`UNVALIDATED SAMPLE OUTPUT`** and must never be interpreted.

## 5. Expected cost (Pi 5, 4 GB)

| Stage | Wall | Peak RAM |
|---|---:|---:|
| `01_audit --mode sample` | ≈6 s | ≈380 MB |

Full-mode costs depend on the big machine; see `notes/scale.md` and `notes/pending_full_run.md`.

## 6. Tests

```bash
python -m pytest src/tests -q       # schema assertions + determinism (if pytest available)
python src/tests/test_audit.py      # or run standalone
```
