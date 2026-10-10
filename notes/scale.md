# Scale & memory notes — BDB27

Measured on the target device (Raspberry Pi 5, arm64, 4 GB RAM, 4 cores) on 2026-10-09, sample mode.

## Measured

| Artefact / stage | Input size | Output size | Peak RSS |
|---|---:|---:|---:|
| `combine_tracking.csv` → Parquet (ZSTD) | 83.8 MB (463,189 rows) | **14.2 MB** (5.9× smaller) | ~383 MB (whole audit) |
| `combine_results.csv` → Parquet | 41 KB (510 rows) | 14 KB | — |
| Whole Phase-1 audit | combine files | ~10 small CSVs + JSON | **383 MB** |

- The entire Phase-1 audit (a–g) runs in **≈6 s wall, ≈383 MB peak RSS** on the Pi. The Combine data fits comfortably in RAM — Phase 2 ("run for real if the Combine data fits in RAM") is viable on-device.
- DuckDB window functions over 463 k rows are effectively instant; the Parquet cache costs ~1.6 s once.

## Measured — Phase 2 (`02_features.py`, sample mode)

| Stage | Input | Output | Peak RSS |
|---|---|---:|---:|
| `02_features:kinematics` | 431,094 player frames (x/y/time) | per-attempt + per-segment tables | — |
| `02_features:impute_load` | + imputed rows | sessions / load / rest | — |
| `02_features:write` | 14,446 rows (6,310 observed + 8,136 imputed) | 3 Parquet + 4 CSV/txt/md | — |
| **Whole Phase-2 stage** | complete combine Parquet (14.2 MB) | `outputs/features/*` | **≈650 MB**, ≈12 s wall |

Phase 2 always processes the **complete** combine file (6,310 observed attempts; 431 k player frames) in both
modes, so its cost is **fixed** and does not scale with the game side. Peak RSS is dominated by the pandas
frame/segment tables held in memory (~650 MB) — comfortably inside the Pi's 4 GB and far below the game-side
risk. The per-attempt Python loop over 6,310 groups is ≈2 s.

## Extrapolation to full data (game side)

| Data | Rows | CSV size |
|---|---:|---:|
| `game_tracking_2023_sample.csv` (this device) | 1,048,575 (2²⁰−1 prefix) | 93.7 MB |
| One full season (2025, per prior-work scan) | ~4.3 M | ~975 MiB |
| **Three seasons (2023+24+25)** | **~13 M** | **~2.9 GB** |

**Rule:** the game side must be processed **per game or per player** and reduced to small summary tables;
modeling stages (Phase 3/5) read only those summaries. Never `pd.read_csv` a full game-tracking file.

## Stages that risk exceeding RAM if done naively

1. **`04_game_features.py`** — the biggest risk. Must stream Parquet with DuckDB and aggregate per
   `(game_id, nfl_id)`; emit a compact per-player-per-game table, not frame-level rows.
2. **`00_thresholds.py`** — scans all frames; must compute quantiles/quantiles via DuckDB streaming,
   not by materialising frames.
3. **Audit (e) full mode** — a `SELECT DISTINCT nfl_id` over ~13 M rows; DuckDB streams this, but the
   Parquet conversion of the ~2.9 GB CSVs is the slow step (disk-bound, low RAM).

**Extrapolated peak:** ≈ sample peak + (rows_full / rows_sample) × (frame working set). With per-game
chunking the working set stays ~O(one game), so peak should stay **< 1 GB**. Re-measure on the big
machine and update this file (`notes/scale.md`) — see `pending_full_run.md` P5.

## Measured — Phase 3 (`03_combine_model.py`, sample mode)

The Combine study frame is small (DB-only: 122 players, 1,432 model rows; 1,541 study rows
incl. imputed), so Phase 3 is dominated by **fit cost**, not data volume.

| Stage | Input | Cost | Peak RSS |
|---|---|---:|---:|
| `03_combine_model:load` | `combine_features_study.parquet` | ≈0.03 s | — |
| `03_combine_model:baseline` | 1,432 rows | ≈0.5 s | — |
| `03_combine_model:lmm` (primary, 4 optimisers) | 122 groups | ≈5 s | — |
| `03_combine_model:fallbacks` (uncorrelated RE, 4 optimisers) | 122 groups | ≈5.5 s | — |
| `03_combine_model:robustness` (3 variants × chain) | 122 groups | ≈33 s | — |
| `03_combine_model:permutation` (1,000 within-player shuffles) | 1,432 rows | ≈2.4 s | — |
| `03_combine_model:write` | 3 parquet + 5 csv/json/md | ≈0.4 s | — |
| **Whole Phase-3 stage** | complete Combine study parquet | **≈47 s wall** | **≈255 MB** |

**Cost driver.** The MixedLM fits dominate (robustness re-runs the fallback chain for 3 variants). The
**pathological** case is a (near-)constant performance field, where the degenerate likelihood makes the
4-optimiser primary fit ≈30 s (bounded by `model.lmm_maxiter`); real Combine data is well-behaved (≈47 s
total). Phase 3 always processes the **complete** combine file in both modes, so its cost is **fixed** and
does not scale with the game side. **No game-side data is touched** — Phase-3 outputs are small (≈140 KB).

**Extrapolation to full data.** Phase 3 is **unchanged** on the full run (the Combine study frame is already
complete); only the provenance label flips to `FULL`. It is not a RAM risk. The Phase-4/5 game side remains
the scale driver (see above).
