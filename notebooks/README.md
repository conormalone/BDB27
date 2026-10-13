# Notebooks

Runnable, self-contained `.ipynb` files. The **competition deliverable is a public Kaggle notebook +
writeup** (≤2,000 words, ≤5 figures); this folder holds that notebook.

## Deliverable notebook — `bdb27_full_run.ipynb`

**The primary interface; no terminal needed.** Open it, set `MODE` (`sample` | `full`) and — only if the
data is not in the usual place — `DATA_DIR`, then **Run All**. It:

- runs the `../src/` engine end-to-end in the frozen order
  `01_audit → 02_combine_envelope → 00_thresholds → 03_game_demand → 04_models → 05_validate`, invoking
  each stage through **its own entry point** (so `--mode` / `--force` / checkpoint behaviour and the
  pre-registration **freeze gate** all apply exactly as on the command line);
- is **cross-platform** (ARM64 Pi, Windows, macOS) and **Kaggle-ready** (auto-detects the attached dataset
  under `/kaggle/input/…`, otherwise uses `../data/raw`) — `pathlib` only, no bash-only calls, and the
  peak-RAM probe (`../src/common.py`) is portable too: POSIX `resource.getrusage` where present, a stdlib
  `ctypes`/`psapi` fallback on Windows, and a documented `-1.0` sentinel if neither is available;
- renders the **≤5 writeup figures** into `../outputs/figures/` and shows them inline;
- **degrades gracefully**: with only partial data it runs everything it can and reports the game-side
  stages as `PENDING FULL RUN`, demonstrating the game-side path on the committed synthetic fixture
  (planted-ceiling `binding` **and** `null`).

**Labels.** `sample` outputs → `UNVALIDATED SAMPLE OUTPUT`; `full` outputs → `FULL`; fixture outputs →
`SYNTHETIC FIXTURE (not a finding)`.

## Conventions
- **Self-contained**: each notebook opens with its imports + data paths and runs top-to-bottom, no hidden state.
- **Data path** points at `../data/raw` (symlink to the mounted drive) — never commit data.
- Figures go to `../outputs/figures/` (the ≤5 writeup figures).
- Keep heavy logic in `../src/` and import it — the notebook stays thin and the engine stays testable.
- Memory discipline (ARM64 Pi): the notebook never loads the large tracking CSVs into pandas; the `src/`
  stages stream them (Parquet + DuckDB).
- A separate "local variant" is unnecessary: the single deliverable notebook already runs on the Pi /
  laptop with its defaults and on Kaggle via auto-detection.
