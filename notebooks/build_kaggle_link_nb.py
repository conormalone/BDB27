"""Generate the self-contained, Kaggle-ready link notebook.

``notebooks/build_kaggle_link_nb.py`` reads the repo ``src/`` modules + ``config.yaml``
and emits ``notebooks/run_kaggle_link.ipynb`` — a notebook that runs **top-to-bottom with
no CLI and no ``git clone``** (competition notebooks may forbid Internet). It embeds every
required module via ``%%writefile`` into the working dir, so there is **ONE source of
truth** (no drift) and the combine side **recomputes genuine FULL warm-up phenotypes** on
Kaggle. Driver cells: a PARAMS cell (INPUT_DIR / MODE / raw filenames), a config-rewrite
cell, an install-what's-missing cell, the run chain (02 -> 03b -> 03d -> 00 -> 04 -> 05),
inline rendering (markdown tables + <=5 figures) and a final "how to read this" cell.

Run:
    python notebooks/build_kaggle_link_nb.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NB_PATH = REPO / "notebooks" / "run_kaggle_link.ipynb"

# modules embedded into the notebook (path relative to src/) in run order
MODULES = [
    "common.py",
    "02_features.py",
    "03_combine_model.py",
    "03b_metric_panel.py",
    "03d_warmup_phenotype.py",
    "00_thresholds.py",
    "04_game_features.py",
    "05_link.py",
]

RUN_CHAIN = [
    ("02_features.py", False),
    ("03b_metric_panel.py", False),
    ("03d_warmup_phenotype.py", False),
    ("00_thresholds.py", True),
    ("04_game_features.py", True),
    ("05_link.py", True),
]


def _md(text: str, idx: int = 0) -> dict:
    return {"cell_type": "markdown", "id": f"md{idx}", "metadata": {},
            "source": text.splitlines(keepends=True)}


def _code(text: str, idx: int = 0) -> dict:
    return {"cell_type": "code", "id": f"code{idx}", "execution_count": None,
            "metadata": {}, "outputs": [], "source": text.splitlines(keepends=True)}


def _writefile(rel_path: str, content: str, idx: int = 0) -> dict:
    # %%writefile writes everything after the first line verbatim
    body = f"%%writefile {rel_path}\n" + content
    return _code(body, idx)


def build_cells() -> list[dict]:
    cells: list[dict] = []

    cells.append(_md(
        "# BDB27 — Combine **warm-up phenotype** → in-game **ramping** (Kaggle notebook)\n\n"
        "Self-contained: no CLI, no `git clone`. All modules are embedded below (single "
        "source of truth) and run in order `02 → 03b → 03d → 00 → 04 → 05`.\n\n"
        "**Reframe (D25).** The pre-registered fatigue-slope→game-decay link is GATED OFF "
        "(D20: combine fatigue-slope reliability 0.0). The viable link is **reliable combine "
        "warm-up phenotypes → in-game ramping**. The warm-up de-confounding was NOT robust "
        "(D24) ⇒ the link is **EXPLORATORY**, clearly labelled. Sample outputs carry "
        "`UNVALIDATED SAMPLE OUTPUT`.\n"))

    # PARAMS cell
    cells.append(_md("## 1. PARAMS — set inputs here\n\n"
                     "`MODE` = `SAMPLE` (a small, non-representative prefix; **never interpret/tune on it**) "
                     "or `FULL` (the real run). On Kaggle, point `INPUT_DIR` at your attached dataset and "
                     "list the raw filenames."))
    cells.append(_code(
        "import os, sys, json, subprocess\n"
        "from pathlib import Path\n\n"
        "# ---- user parameters -------------------------------------------------------\n"
        "MODE = 'SAMPLE'          # 'SAMPLE' | 'FULL'\n"
        "INPUT_DIR = None         # None -> <repo>/data/raw ; on Kaggle set '/kaggle/input/<dataset>'\n"
        "FILES = {\n"
        "    'combine_tracking': 'combine_tracking.csv',\n"
        "    'combine_results':  'combine_results.csv',\n"
        "    'players':          'players.csv',\n"
        "    'games':            'games.csv',\n"
        "    'player_play':      'player_play.csv',\n"
        "    'game_tracking_sample': 'game_tracking_2023_sample.csv',\n"
        "    'game_tracking_full': ['game_tracking_2023.csv', 'game_tracking_2024.csv', 'game_tracking_2025.csv'],\n"
        "}\n"
        "# ---------------------------------------------------------------------------\n\n"
        "mode = MODE.lower()\n"
        "CWD = Path.cwd()\n"
        "if Path('/kaggle/working').exists():\n"
        "    WORK = Path('/kaggle/working')\n"
        "elif (CWD / '..' / 'src').exists():\n"
        "    WORK = CWD.parent                       # local run from notebooks/\n"
        "else:\n"
        "    WORK = CWD\n"
        "os.chdir(WORK)\n"
        "SRC = WORK / 'src'\n"
        "SRC.mkdir(parents=True, exist_ok=True)\n"
        "(WORK / 'outputs').mkdir(parents=True, exist_ok=True)\n\n"
        "# data/raw -> INPUT_DIR (symlink on Kaggle; local repo already has it)\n"
        "if INPUT_DIR is not None:\n"
        "    raw = WORK / 'data' / 'raw'\n"
        "    raw.parent.mkdir(parents=True, exist_ok=True)\n"
        "    if not raw.exists():\n"
        "        os.symlink(INPUT_DIR, raw)\n"
        "(WORK / 'data' / 'interim' / 'parquet').mkdir(parents=True, exist_ok=True)\n\n"
        "print('WORK =', WORK)\n"
        "print('mode =', mode, '| FILES.game_tracking_sample =', FILES['game_tracking_sample'])\n"))

    # install cell
    cells.append(_md("## 2. Install only what is missing\n\n"
                     "`pandas`/`numpy`/`scikit-learn`/`statsmodels`/`scipy`/`matplotlib` exist in the "
                     "Kaggle base image; only `duckdb`/`pyarrow` may need installing."))
    cells.append(_code(
        "import importlib.util\n"
        "for _m in ['duckdb', 'pyarrow']:\n"
        "    if importlib.util.find_spec(_m) is None:\n"
        "        print('installing', _m)\n"
        "        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', _m], check=False)\n"
        "print('imports ok')\n"))

    # embed modules via %%writefile
    cells.append(_md("## 3. Embed the pipeline (single source of truth)\n\n"
                     "Each module below is written verbatim into `src/`."))
    for m in MODULES:
        content = (REPO / "src" / m).read_text(encoding="utf-8")
        cells.append(_writefile(f"src/{m}", content))

    cfg_text = (REPO / "config.yaml").read_text(encoding="utf-8")
    cells.append(_writefile("config.yaml", cfg_text))

    # config rewrite
    cells.append(_md("## 4. Rewrite the bundled `config.yaml` for this environment"))
    cells.append(_code(
        "import yaml\n"
        "cfg_path = WORK / 'config.yaml'\n"
        "if INPUT_DIR is not None or Path('/kaggle/working').exists():\n"
        "    cfg = yaml.safe_load(cfg_path.read_text())\n"
        "    raw = str(INPUT_DIR) if INPUT_DIR is not None else str(WORK / 'data' / 'raw')\n"
        "    cfg['paths']['raw'] = raw\n"
        "    cfg['files'].update(FILES)\n"
        "    cfg['mode']['default'] = mode\n"
        "    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))\n"
        "    print('config updated: paths.raw =', raw, '| mode.default =', mode)\n"
        "else:\n"
        "    print('local run: repo config.yaml used as-is (paths already correct)')\n"))

    # run chain
    cells.append(_md("## 5. Run the chain: `02 → 03b → 03d → 00 → 04 → 05`"))
    run_code = (
        "RUN_CHAIN = " + repr(RUN_CHAIN) + "\n\n"
        "for mod, force in RUN_CHAIN:\n"
        "    cmd = [sys.executable, str(SRC / mod), '--mode', mode]\n"
        "    if force:\n"
        "        cmd.append('--force')\n"
        "    print('=' * 70)\n"
        "    print('RUN', mod, '(force=%s)' % force)\n"
        "    print('=' * 70)\n"
        "    p = subprocess.run(cmd, cwd=str(WORK), capture_output=True, text=True)\n"
        "    tail = '\\n'.join((p.stdout or p.stderr or '').splitlines()[-8:])\n"
        "    print(tail)\n"
        "    if p.returncode != 0:\n"
        "        print(p.stderr[-3000:])\n"
        "        raise SystemExit('stage %s failed (rc=%d)' % (mod, p.returncode))\n"
        "print('\\nCHAIN COMPLETE')\n")
    cells.append(_code(run_code))

    # rendering
    cells.append(_md("## 6. Results — headline numbers + inline figures"))
    cells.append(_code(
        "import pandas as pd, numpy as np, json\n"
        "pd.set_option('display.width', 200); pd.set_option('display.max_colwidth', 60)\n"
        "game = pd.read_csv(WORK / 'outputs' / 'game' / 'game_player_slopes.csv')\n"
        "link = pd.read_csv(WORK / 'outputs' / 'link' / 'link_results.csv')\n"
        "cv = pd.read_csv(WORK / 'outputs' / 'link' / 'link_cv.csv')\n"
        "placebo = pd.read_csv(WORK / 'outputs' / 'link' / 'link_placebo.csv')\n"
        "matched = pd.read_csv(WORK / 'outputs' / 'link' / 'link_matched_counts.csv')\n"
        "rel = pd.read_csv(WORK / 'outputs' / 'game' / 'game_reliability.csv')\n"
        "wrel = pd.read_csv(WORK / 'outputs' / 'model' / 'warmup_reliability.csv')\n"
        "phen = pd.read_csv(WORK / 'outputs' / 'model' / 'warmup_phenotypes.csv')\n"
        "print('--- link results (naive + error-corrected) ---')\n"
        "display(link[['model','n','coef','se','p','r2','delta_r2','coef_corrected','reliability_predictor']])\n"
        "print('--- matched Combine<->NFL counts ---')\n"
        "display(matched)\n"
        "print('--- cross-validation ---')\n"
        "display(cv)\n"))

    cells.append(_code(
        "%matplotlib inline\n"
        "import matplotlib.pyplot as plt\n"
        "FIG = WORK / 'outputs' / 'figures'; FIG.mkdir(parents=True, exist_ok=True)\n"
        "def _save(fig, name):\n"
        "    fig.tight_layout(); fig.savefig(FIG / name, dpi=120); plt.show()\n\n"
        "# Fig 1: in-game ramp-slope distribution\n"
        "fig, ax = plt.subplots(figsize=(6, 3.2))\n"
        "ax.hist(game['ramp_slope_shrunk'].dropna(), bins=25, color='#4c72b0')\n"
        "ax.set_title('In-game ramp slopes (per player)'); ax.set_xlabel('ramp_slope_shrunk'); ax.set_ylabel('players')\n"
        "_save(fig, 'fig_link_ramp_dist.png')\n\n"
        "# Fig 2: warm-up phenotype reliability bars (top cells)\n"
        "fig, ax = plt.subplots(figsize=(7, 3.6))\n"
        "w = wrel[wrel['phenotype'].astype(str).str.startswith('warmup_slope')].copy()\n"
        "w = w.sort_values('reliability', ascending=False).head(8)\n"
        "ax.barh(w['detail'].astype(str).str.slice(0, 34), w['reliability'], color='#55a868')\n"
        "ax.axvline(0.2, color='r', ls='--', lw=1); ax.set_title('Warm-up slope reliability (gate 0.2)')\n"
        "ax.invert_yaxis(); _save(fig, 'fig_link_phenotype_reliability.png')\n\n"
        "# Fig 3: link scatter + naive fit\n"
        "ph = phen[(phen['load_code']=='L5') & (phen['outcome_code']=='O2')][['nfl_id','slope_raw']]\n"
        "gg = game[['nfl_id','ramp_slope_shrunk','ramp_slope_se']]\n"
        "m = ph.merge(gg, on='nfl_id').dropna()\n"
        "fig, ax = plt.subplots(figsize=(5.2, 4.2))\n"
        "ax.errorbar(m['slope_raw'], m['ramp_slope_shrunk'], yerr=m['ramp_slope_se'], fmt='o', ms=4, alpha=.8)\n"
        "if len(m) > 2 and m['slope_raw'].nunique() > 1:\n"
        "    b = np.polyfit(m['slope_raw'], m['ramp_slope_shrunk'], 1)\n"
        "    xs = np.linspace(m['slope_raw'].min(), m['slope_raw'].max(), 20)\n"
        "    ax.plot(xs, np.polyval(b, xs), 'r-')\n"
        "ax.set_xlabel('combine warm-up slope (L5xO2, raw)'); ax.set_ylabel('in-game ramp slope')\n"
        "ax.set_title('Link (EXPLORATORY)'); _save(fig, 'fig_link_scatter.png')\n\n"
        "# Fig 4: placebo\n"
        "fig, ax = plt.subplots(figsize=(5.2, 3.6))\n"
        "ax.hist(m['nfl_id'].map(game.set_index('nfl_id')['early_output_z']).dropna(), bins=20, color='#c44e52')\n"
        "ax.set_title('Placebo: early-game output distribution'); ax.set_xlabel('early_output_z')\n"
        "_save(fig, 'fig_link_placebo.png')\n\n"
        "# Fig 5: CV delta-R^2 with bootstrap CI\n"
        "fig, ax = plt.subplots(figsize=(4.6, 3.4))\n"
        "pt = float(cv['delta_r2_point'].iloc[0]); lo = float(cv['ci_lo'].iloc[0]); hi = float(cv['ci_hi'].iloc[0])\n"
        "ax.errorbar([0], [pt], yerr=[[pt-lo],[hi-pt]], fmt='o', capsize=6)\n"
        "ax.axhline(0, color='k', lw=.8); ax.set_xticks([0]); ax.set_xticklabels(['warmup -> ramp'])\n"
        "ax.set_title('Held-out delta-R^2 (CV)'); _save(fig, 'fig_link_cv_delta_r2.png')\n"
        "print('figures saved to', FIG)\n"))

    # final markdown
    cells.append(_md(
        "## 7. How to read this / limitations\n\n"
        "- **Reframe (D25).** This notebook tests the *pivot*: reliable combine **warm-up** "
        "phenotypes → in-game **ramping**. The pre-registered fatigue-slope→decay link is "
        "**GATED OFF** (D20: combine fatigue-slope reliability **0.0**).\n"
        "- **EXPLORATORY.** The warm-up phenotype de-confounding was **NOT robust** (D24) — the "
        "adjusted accel-cell coefficients are not consistently positive at ≥0.5× the raw effect. "
        "Do not read this as a confirmatory result.\n"
        "- **Error-in-both-slopes.** Read `link_results.csv`: the naive OLS is reported *alongside* "
        "the inverse-variance-weighted, reliability-corrected estimate (`coef_corrected`). "
        "The predictor is the per-player **raw** warm-up slope (`slope_raw`); the EB-shrunken "
        "`warmup_slope` collapses to the population mean (EB τ²=0) and is not usable as a predictor.\n"
        "- **Placebo.** If the phenotype is real, it must **not** predict **early-game** output "
        "(`link_placebo.csv`): a significant placebo coefficient would invalidate the link.\n"
        "- **Cross-validation.** Held-out ΔR² with bootstrap CIs (`link_cv.csv`); a CI spanning 0 "
        "(or negative) ⇒ no detectable link.\n"
        "- **Selection limitation.** Only players who **reached NFL games** have game data; the "
        "link is conditional on combine participant AND on-field. See `link_matched_counts.csv`.\n"
        "- **SAMPLE vs FULL.** In `SAMPLE` mode thresholds are the a-priori placeholders "
        "(`PLACEHOLDER_SAMPLE`); the sample is a non-representative prefix — never interpret or "
        "tune on it. In `FULL` mode `00_thresholds.py` derives cut-offs from the full game data "
        "(`FULL`) and `04`/`05` assert that provenance.\n"))

    return cells


def build() -> Path:
    cells = build_cells()
    for i, c in enumerate(cells):
        c["id"] = f"cell{i:02d}"
    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    NB_PATH.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    return NB_PATH


if __name__ == "__main__":
    p = build()
    print(f"wrote {p} ({p.stat().st_size} bytes)")
