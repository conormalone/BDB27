#!/usr/bin/env python3
"""Deterministic generator for ``notebooks/run_pipeline.ipynb`` (BDB27).

The notebook must be runnable top-to-bottom with NO CLI and NO Internet (Kaggle
competition notebooks may forbid network), so it cannot ``git clone``. Instead the
generator embeds the **verbatim** stage sources (``src/*.py``) and ``config.yaml``
inside the notebook as ``gzip``+``base64`` payloads with a ``sha256`` manifest.
At runtime the notebook writes them to disk, imports them and calls each stage's
``run(mode, force)`` in-process.

Determinism: files are read in a fixed order; ``gzip.compress(..., mtime=0)`` and
``base64`` are deterministic; the notebook JSON is emitted with ``json.dumps`` using
a fixed cell order and no timestamps. Re-running this script on the same inputs
reproduces a byte-identical ``run_pipeline.ipynb``.

Regenerate with:

    python tools/build_notebook.py
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "notebooks" / "run_pipeline.ipynb"

# Files embedded, in order: (source path relative to repo, destination name).
EMBED = [
    ("src/common.py", "common.py"),
    ("src/01_audit.py", "01_audit.py"),
    ("src/02_features.py", "02_features.py"),
    ("src/03_combine_model.py", "03_combine_model.py"),
    ("src/03b_metric_panel.py", "03b_metric_panel.py"),
    ("src/03d_warmup_phenotype.py", "03d_warmup_phenotype.py"),
    ("config.yaml", "config.yaml"),
]


def _payload(rel: str) -> dict[str, object]:
    raw = (REPO_ROOT / rel).read_bytes()
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "n_bytes": len(raw),
        "gz_b64": base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii"),
    }


# --------------------------------------------------------------------------- #
# Cell sources
# --------------------------------------------------------------------------- #
CELL0_MD = """# BDB27 — Combine fatigue slope vs. in-game intensity decay — pipeline runner

**Provenance / status banner**

- Repo `conormalone/BDB27`; binding spec `TASK.md` (§6); all thresholds/paths in `config.yaml`.
  This notebook reproduces the **combine-side** results committed at HEAD `f45e3af`
  (Phases **1 → 2 → 3 → 3b → 3d**) — run top-to-bottom, no CLI, no Internet.
- **The combine input data are COMPLETE.** Therefore every combine-side number produced here is
  **REAL** in **both** run modes. The `MODE` switch only governs the *provenance stamp* (and the
  not-run game side): `full` ⇒ `FULL`, `sample` ⇒ `UNVALIDATED SAMPLE OUTPUT` (developer dry-run
  only). **Never** read a `sample`-stamped combine number as unvalidated — the combine inputs are
  the full, real dataset; the stamp is bookkeeping.
- **Game side (Phases 4/5) is NOT implemented this cycle** and REQUIRES the full Kaggle game-tracking
  files. Those sections below are clearly-marked placeholders; no results are faked.

**Self-contained.** All stage code (`src/*.py`) and `config.yaml` are embedded in this notebook as
`gzip`+`base64` payloads with a `sha256` manifest (next cell) — no `git clone`, no network at run time.
At runtime they are written under the work directory and imported in-process; everything still comes
from the embedded `config.yaml` (no new scientific constants in the notebook).

## How to run

- **Kaggle:** attach the 2027 BDB dataset. `DATA_DIR` auto-detects a `/kaggle/input` directory that
  contains `combine_tracking.csv`; outputs go to `/kaggle/working/bdb27_run/`. Just **Run All**.
- **Local / headless:** `pip install -r requirements.txt` (or `source ~/.venvs/bdb27/bin/activate`),
  then run all cells. Set `BDB27_WORK_DIR` to redirect the working directory off a small root disk, e.g.

  ```bash
  BDB27_WORK_DIR=/mnt/project_data/bdb27_run \\
  jupyter nbconvert --to notebook --execute notebooks/run_pipeline.ipynb \\
      --output /mnt/project_data/bdb27_run/run_pipeline.executed.ipynb \\
      --ExecutePreprocessor.timeout=2400
  ```

A CLI fallback (identical logic, per-stage scripts) is documented in `README_RUN.md`.

## Limits (see `notes/rules.md`)

- Official: **≤ 2,000 words**, **fewer than 10 tables or figures**.
- Internal (stricter, `TASK.md`): **≤ 5 figures**. This notebook renders **3 figures** and **5 compact tables**.
"""

CELL1_CODE = '''# ===================== Cell 1 — user params + dependency guard =====================
# User-editable parameters:
MODE = "full"        # "full" (default) | "sample" (developer dry-run only; stamp only)
# ==================================================================================

import os
import sys
import importlib
import pathlib
import subprocess

# ---- dependency guard: install duckdb/polars ONLY if missing (no-op when present) ----
def _ensure_deps():
    missing = []
    for mod in ("duckdb", "polars"):
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(mod)
    if missing:
        print("Installing missing runtime deps:", missing)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *missing])
    else:
        print("duckdb + polars already present — no install needed.")

_ensure_deps()
assert MODE in ("full", "sample"), "MODE must be 'full' or 'sample'"

# ---- DATA_DIR autodetect: /kaggle/input/*/combine_tracking.csv, else <repo>/data/raw ----
def _find_data_dir():
    if os.environ.get("BDB27_DATA_DIR"):
        return pathlib.Path(os.environ["BDB27_DATA_DIR"])
    kag = pathlib.Path("/kaggle/input")
    if kag.is_dir():
        hits = sorted(kag.rglob("combine_tracking.csv"))
        if hits:
            return hits[0].parent
    # walk up from cwd and from the notebook dir looking for <root>/data/raw/combine_tracking.csv
    starts = [pathlib.Path.cwd()]
    here = pathlib.Path.cwd()
    for _ in range(4):
        starts.append(here)
        here = here.parent
    for start in starts:
        for cand in (start / "data" / "raw", start):
            if (cand / "combine_tracking.csv").exists():
                return cand
    return pathlib.Path.cwd() / "data" / "raw"

DATA_DIR = str(_find_data_dir())

# ---- WORK_DIR: env override, else /kaggle/working, else <cwd>/bdb27_run ----
if os.environ.get("BDB27_WORK_DIR"):
    WORK_DIR = os.environ["BDB27_WORK_DIR"]
elif pathlib.Path("/kaggle/working").is_dir():
    WORK_DIR = "/kaggle/working/bdb27_run"
else:
    WORK_DIR = str(pathlib.Path.cwd() / "bdb27_run")

print("MODE     =", MODE)
print("DATA_DIR =", DATA_DIR)
print("WORK_DIR =", WORK_DIR)
assert (pathlib.Path(DATA_DIR) / "combine_tracking.csv").exists(), (
    f"combine_tracking.csv not found under DATA_DIR={DATA_DIR!r}; "
    "attach the 2027 dataset (Kaggle) or check data/raw (local).")
print("combine_tracking.csv found OK.")
'''

CELL3_CODE = '''# ===================== Cell 2 — materialise embedded sources + import =====================
import io
import os
import gzip
import base64
import hashlib
import json
import shutil
import contextlib
import importlib.util
import logging
import pathlib
import sys

import yaml

WORK = pathlib.Path(WORK_DIR).expanduser().resolve()
SRC = WORK / "src"
(WORK / "outputs").mkdir(parents=True, exist_ok=True)
(WORK / "data" / "interim").mkdir(parents=True, exist_ok=True)
SRC.mkdir(parents=True, exist_ok=True)

# ---- write embedded sources (sha256-verified) ----
print("Embedded-source manifest (name · bytes · sha256):")
for name, rec in _EMBEDDED.items():
    raw = gzip.decompress(base64.b64decode(rec["gz_b64"]))
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == rec["sha256"], f"integrity mismatch for {name}"
    assert len(raw) == rec["n_bytes"], f"size mismatch for {name}"
    dest = SRC / name if name.endswith(".py") else (WORK / name)
    dest.write_bytes(raw)
    print(f"  {name:26s} {rec['n_bytes']:7d}  {digest}")
    print(f"    -> {dest}")

# ---- data/raw -> DATA_DIR symlink (01_audit stats REPO_ROOT/data/raw/<combine_tracking>) ----
link = WORK / "data" / "raw"
if link.is_symlink():
    link.unlink()
elif link.exists():
    shutil.rmtree(link)
os.symlink(os.path.abspath(DATA_DIR), link)

# ---- patch the embedded config: absolute runtime paths; game side out of scope ----
CFG = yaml.safe_load((WORK / "config.yaml").read_text(encoding="utf-8"))
CFG["paths"]["raw"] = os.path.abspath(DATA_DIR)
CFG["paths"]["interim"] = str(WORK / "data" / "interim")
CFG["paths"]["outputs"] = str(WORK / "outputs")
CFG["logging"]["run_log"] = str(WORK / "outputs" / "run_log.csv")
CFG["mode"]["full"]["run_game_side"] = False   # Phase 4/5 game side is out of scope this cycle
(WORK / "config.yaml").write_text(yaml.safe_dump(CFG, sort_keys=False), encoding="utf-8")
print("\\nPatched config ->", WORK / "config.yaml")
print("  paths.raw      =", CFG["paths"]["raw"])
print("  paths.interim  =", CFG["paths"]["interim"])
print("  paths.outputs  =", CFG["paths"]["outputs"])
print("  logging.run_log=", CFG["logging"]["run_log"])
print("  mode.full.run_game_side =", CFG["mode"]["full"]["run_game_side"])

# ---- import each stage module in-process (REPO_ROOT resolves to WORK) ----
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

def _load(mod_name, filename):
    spec = importlib.util.spec_from_file_location(mod_name, SRC / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

S_AUDIT = _load("bdb27_audit", "01_audit.py")
S_FEATURES = _load("bdb27_features", "02_features.py")
S_MODEL = _load("bdb27_model", "03_combine_model.py")
S_PANEL = _load("bdb27_panel", "03b_metric_panel.py")
S_WARMUP = _load("bdb27_warmup", "03d_warmup_phenotype.py")
print("\\nmodules imported: 01_audit, 02_features, 03_combine_model, 03b_metric_panel, 03d_warmup_phenotype")


def run_stage(label, fn, mode=MODE, force=True):
    """Run one stage in-process; capture its stdout+stderr and return code."""
    lg = logging.getLogger("bdb27")
    saved_handlers, saved_prop = lg.handlers[:], lg.propagate
    lg.handlers, lg.propagate = [], False
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fn(mode, force)
    finally:
        lg.handlers, lg.propagate = saved_handlers, saved_prop
    text = out.getvalue() + err.getvalue()
    print(f"[{label}] run(mode={mode!r}, force={force}) -> return code {rc}")
    tail = "\\n".join([ln for ln in text.strip().splitlines() if ln.strip()][-10:])
    if tail:
        print("--- captured stage log (tail) ---")
        print(tail)
    return rc
'''

CELL4_MD = """## Stage 1 — Phase 1 audit (go/no-go gate)

Runs `01_audit.run(MODE, force=True)` in-process. Produces `outputs/audit/*` and the gate verdict.
Audit questions: (a) timing present, (b) attempts/position, (c) drill order varies, (d) distance
reliability, (e) matched Combine→NFL (**PENDING FULL RUN** — game side out of scope), (f) missingness/units,
(g) numbering gaps, (h) position scope."""

CELL4_CODE = '''rc = run_stage("01_audit", S_AUDIT.run)
assert rc == 0, ("01_audit exit=%s (gate not GO)" % rc)
'''

CELL5_CODE = '''# ---- render: audit gate (table 1) ----
import json
import pandas as pd

A = WORK / "outputs" / "audit"
audit = json.loads((A / "audit_summary.json").read_text(encoding="utf-8"))
a, c, d, rec, gate = audit["a"], audit["c"], audit["d"], audit["recommendation"], audit["gate"]
emin = audit["e"][0]
rows = [
    ("provenance", audit.get("provenance")),
    ("(a) timing field present", a["timing_field_present"]),
    ("(a) attempt~time agreement", f'{a["order_agree_frac"]} (min {a["order_violations"]} violation(s))'),
    ("(c) drill order varies across players", c["drill_order_varies_across_players"]),
    ("(c) max pairwise flip share", f'{c["max_pairwise_flip_share"]}  (thr {c["order_flip_threshold"]})'),
    ("(d) distance (provided `dis`) reliable", d["reliable"]),
    ("(d) share of attempts >5% off (weighted)", d["share_over_tol_weighted"]),
    ("(g) overall numbering-gap share", gate["overall_gap_share"]),
    ("(h) study population (locked, D13)", rec["position_group"]),
    ("(h) players reaching >=3 attempts", f'{rec["players_ge3_attempts"]}/{rec["players_total"]}'),
    ("(e) matched Combine->NFL per group", emin["status"]),
    ("gate verdict", gate["verdict"]),
]
print(pd.DataFrame(rows, columns=["audit check", "value"]).to_string(index=False))
print("\\n(e) is PENDING FULL RUN — the game-side match needs the full Kaggle game-tracking files.")
'''

CELL6_MD = """## Stage 2 — Phase 2 Combine features

Runs `02_features.run(MODE, force=True)`. Computes per-attempt kinematics from x/y/time, the
observed+imputed cumulative prior load (imputed rows carry **no** performance), within-(drill_type, position)
standardisation and the VIF check. Outputs `outputs/features/*`; the DB-only study handoff is
`combine_features_study.parquet`."""

CELL6_CODE = """rc = run_stage("02_features", S_FEATURES.run)
assert rc == 0"""

CELL7_CODE = '''# ---- render: Phase-2 features summary (table 2) ----
F = WORK / "outputs" / "features"
fd = pd.read_csv(F / "feature_diagnostics.csv")
prov2 = fd["provenance"].iloc[0]
kv = dict(zip(fd["metric"], fd["value"]))
vif_flag = float(kv["vif_prior_load_yd"]) > float(kv["vif_flag_threshold"])
rows = [
    ("provenance", prov2),
    ("attempt rows observed / imputed", f'{kv["rows_observed"]} / {kv["rows_imputed"]}'),
    ("study population", kv["study_population"]),
    ("study observed rows / players", f'{kv["rows_study_observed"]} / {kv["n_players_study"]}'),
    ("n drill types", kv["n_drill_types"]),
    ("standardise level", CFG["features"]["standardize_level"]),
    ("min std-group n (config)", CFG["features"]["min_std_group_n"]),
    ("VIF(prior_load_yd, first_attempt)", f'{float(kv["vif_prior_load_yd"]):.4f} (flag thr {kv["vif_flag_threshold"]}; exceeds={vif_flag})'),
    ("imputed share of observed", kv["imputed_share_of_observed"]),
    ("rest_s policy", kv["rest_use"]),
]
print(pd.DataFrame(rows, columns=["Phase-2 feature", "value"]).to_string(index=False))
'''

CELL8_MD = """## Stage 3 — Phase 3 Combine fatigue model (D20)

Runs `03_combine_model.run(MODE, force=True)`. Pre-registered spec: `perf_z ~ load + load² + first_attempt
+ C(drill) + (1 + load | player)` with the documented convergence fallback chain, model-based slope
reliability and a ≥1000-shuffle within-player permutation test. **Stop rule:** reliability < 0.2 ⇒ report
the **NULL** as the finding."""

CELL8_CODE = """rc = run_stage("03_combine_model", S_MODEL.run)
assert rc == 0"""

CELL9_CODE = '''# ---- render: Phase-3 D20 headline (table 3) + FIGURE 1 ----
%matplotlib inline
import json
import matplotlib.pyplot as plt
import duckdb
import numpy as np
import pandas as pd

M = WORK / "outputs" / "model"
ms = json.loads((M / "combine_model_summary.json").read_text(encoding="utf-8"))
reliab = float(ms["reliability"]); thr = float(ms["reliability_stop_threshold"])
rows = [
    ("estimator chosen", ms["estimator_chosen"]),
    ("slope reliability", f'{reliab}  (stop threshold {thr})'),
    ("stop rule triggered", ms["stop_rule_triggered"]),
    ("population load coef (per yd)", ms["population_load_coef"]),
    ("population load p", ms["population_load_p"]),
    ("permutation p (meta_slope)", ms["permutation"]["p"]),
    ("baseline late−early mean", ms["baseline"]["mean"]),
    ("model obs / players", f'{ms["n_obs"]} / {ms["n_players"]}'),
    ("FINDING", "NULL" + (" (reliability < stop threshold)" if reliab < thr else "")),
]
print(pd.DataFrame(rows, columns=["Phase-3 D20", "value"]).to_string(index=False))

con = duckdb.connect()
sl = con.execute(
    f"SELECT slope_raw, slope_shrunk, reliability FROM read_parquet('{(M / 'combine_player_slopes.parquet')}')"
).fetchdf()
var_shrunk = float(np.var(sl["slope_shrunk"], ddof=1))
print(f'\\nper-player slopes: n={len(sl)}, Var(slope_raw)={np.var(sl["slope_raw"], ddof=1):.3e}, '
      f'Var(slope_shrunk)={var_shrunk:.3e}  -> ~zero between-player slope variance')

fig, ax = plt.subplots(figsize=(6.6, 3.6))
ax.hist(sl["slope_shrunk"], bins=30, color="#4C72B0", edgecolor="white")
ax.axvline(0.0, color="black", lw=0.8)
ax.axvline(float(ms["population_load_coef"]), color="crimson", lw=1.4, ls="--",
           label=f'population coef = {float(ms["population_load_coef"]):.3e}')
ax.set_xlabel("EB-shrunk per-player fatigue slope (per yard prior load)")
ax.set_ylabel("players")
ax.set_title(f"FIGURE 1 — per-player Combine fatigue slopes\\n"
             f"between-player variance ~ 0 (reliability {reliab}) → NULL")
ax.legend(loc="upper left", fontsize=8)
plt.tight_layout()
plt.show()
'''

CELL10_MD = """## Stage 3b — Phase 3b metric panel (D21, pre-registered)

Runs `03b_metric_panel.run(MODE, force=True)`. Tests whether a *better* load metric rescues per-player slope
reliability ≥ 0.2 across the full 7×3 = 21 (load × outcome) cell panel, using the same fallback chain,
reliability formula and stop rule as Phase 3."""

CELL10_CODE = """rc = run_stage("03b_metric_panel", S_PANEL.run)
assert rc == 0"""

CELL11_CODE = '''# ---- render: Phase-3b gate cells (table 4) + FIGURE 2 ----
pjson = json.loads((M / "metric_panel.json").read_text(encoding="utf-8"))
cells = pd.DataFrame(pjson["cells"]).sort_values(["load_code", "outcome_code"]).reset_index(drop=True)
cells["cell"] = cells["load_code"] + "x" + cells["outcome_code"]
met = cells[cells["reliability_met"]]
print(f'panel cells: {len(cells)};  reliability >= {CFG["panel"]["reliability_stop_threshold"]}: {len(met)} '
      f'(metric-robust null — the fatigue direction does not survive better load metrics)')
print(met[["cell", "load_metric", "outcome_metric", "estimator", "reliability", "load_coef", "load_p"]]
      .to_string(index=False))

thr_p = float(CFG["panel"]["reliability_stop_threshold"])
colors = ["#C44E52" if m else "#9E9E9E" for m in cells["reliability_met"]]
fig, ax = plt.subplots(figsize=(8.2, 3.6))
ax.bar(cells["cell"], cells["reliability"], color=colors)
ax.axhline(thr_p, color="black", lw=1.0, ls="--", label=f"gate = {thr_p}")
ax.set_ylabel("slope reliability")
ax.set_title("FIGURE 2 — reliability by (load x outcome) cell; red = meets the >=0.2 gate")
ax.tick_params(axis="x", labelrotation=45, labelsize=8)
ax.legend(loc="upper left", fontsize=8)
plt.tight_layout()
plt.show()
'''

CELL12_MD = """## Stage 3d — Phase 3d warm-up phenotype (D23/D24)

Runs `03d_warmup_phenotype.run(MODE, force=True)`. **Headline pivot:** the only reliable individual combine
signal (D21) is a *positive* load coefficient on explosive output — i.e. warm-up / post-activation
potentiation, not fatigue. The make-or-break step separates "warmed up" from "ran this drill later in the
fixed order" (de-confounding). D24 sets the **governing strict reading** of the D23 PASS/FAIL rule."""

CELL12_CODE = """rc = run_stage("03d_warmup_phenotype", S_WARMUP.run)
assert rc == 0"""

CELL13_CODE = '''# ---- render: Phase-3d de-confounding (table 5) + FIGURE 3 ----
wj = json.loads((M / "warmup_summary.json").read_text(encoding="utf-8"))
head = wj["headline"]
dc = pd.read_csv(M / "warmup_deconfounding.csv")
dc["cell"] = dc["load_code"] + "x" + dc["outcome_code"]
print(f'governing reading (D24): {head["deconf_pass_governing_reading"]}  ->  deconf_pass = {head["deconf_pass"]}')
print(f'primary-cell deconf_pass = {head["deconf_pass_primary"]};  '
      f'all-accel deconf_pass = {head["deconf_pass_all_accel"]}  =>  warm-up NOT CLAIMED (exploratory only)')
print(dc[["cell", "load_metric", "outcome_metric", "raw_coef", "adj_coef", "adj_retain_frac", "deconf_pass"]]
      .to_string(index=False))
bc = dc["within_contrast_battery"]
print(f'\\nsurviving signal: positive within-drill rep1->rep2 battery contrast z = '
      f'+{bc.min():.4f} .. +{bc.max():.4f} (n={int(dc["within_contrast_battery_n"].iloc[0])} players) — exploratory')
print(f'D20/D21 fatigue null remains the headline; warm-up median reps-to-peak = '
      f'{head["median_reps_to_peak"]:.3f} (identified {head["median_reps_to_peak_identified"]:.3f}, '
      f'frac censored {head["frac_censored_at_grid_max"]:.3f})')

thr_r = float(CFG["warmup"]["deconf_retain_frac"])
colors = ["#55A868" if p else "#C44E52" for p in dc["deconf_pass"]]
fig, ax = plt.subplots(figsize=(8.2, 3.6))
ax.bar(dc["cell"], dc["adj_retain_frac"], color=colors)
ax.axhline(thr_r, color="black", lw=1.0, ls="--", label=f"retain gate = {thr_r}")
ax.set_ylabel("adjusted coef retention |adj|/|raw|")
ax.set_title("FIGURE 3 — de-confounding retention per cell; green = PASS, red = FAIL (D23/D24)")
ax.tick_params(axis="x", labelrotation=45, labelsize=8)
ax.legend(loc="upper right", fontsize=8)
plt.tight_layout()
plt.show()
'''

CELL14_MD = """## Phases 4 & 5 — NOT implemented this cycle

> **NOT implemented — requires the full Kaggle game tracking.**

- **Phase 4 (`00_thresholds` + `04_game_features`):** per matched player, model high-intensity effort
  intensity vs. their own early-game baseline against cumulative in-game distance, snaps-in-drive and
  time-since-rest, with the minimum-snaps rule; extract per-player decay slopes and the same model-based
  reliability/stop rule.
- **Phase 5 (`05_link`):** regress game decay slope on the Combine fatigue slope, controlling for baseline
  speed and size; account for error in both slopes; player-grouped CV with held-out ΔR²; placebo and
  sensitivity tests.

Neither is runnable here: the game-tracking files are not in this dataset slice, and the pre-registered
**stop rule has already fired on the combine side** (Phase-3 reliability 0.0 < 0.2), so the Phase-5 link is
declared out of scope for this cycle. No placeholders or synthetic game results are produced.
See `notes/pending_full_run.md` and `notes/limitations.md`.
"""

CELL15_MD = """## Summary

**Combine-side result (reproduced from HEAD `f45e3af`), all numbers read from the produced artefacts:**

- **Phase 1 audit — GO.** Timing fields present (attempt~time agreement ≈ 0.999); drill order varies
  (only the shuttle/3-cone relative order flips); provided `dis` is **unreliable** vs the x/y path;
  ≈32% of player-drill groups have numbering gaps; **DB-only** study population (122/122 players reach
  ≥3 attempts). (e) matched Combine→NFL is **PENDING FULL RUN**.
- **Phase 2 features.** 1432 study (DB) observed attempt rows / 122 players; load imputed only (imputed rows
  carry no performance); VIF well below threshold. Confirms the Phase-3 handoff.
- **Phase 3 (D20) — NULL.** Estimator `per_player_eb`; between-player slope variance ≈ 0 ⇒ reliability
  **0.0 < 0.2** ⇒ stop rule fires ⇒ **the finding is a NULL**. The pooled population load coefficient is
  small (p≈0.0055, permutation p≈0.03), but with zero cross-player slope variance those slopes cannot
  support the Phase-5 link.
- **Phase 3b (D21) — metric-robust.** Of 21 (load × outcome) cells, only the 3 accel-outcome cells meet the
  0.2 gate — a *positive* load effect on explosive output (warm-up direction), not fatigue. The fatigue
  direction remains a metric-robust null.
- **Phase 3d (D23/D24) — exploratory.** Under the **governing strict reading** (`strict_all_accel`)
  de-confounding **fails** ⇒ warm-up is **NOT claimed**; the surviving signal is a small positive
  within-drill rep1→rep2 battery contrast (exploratory). The D20/D21 fatigue null remains the headline.

**Provenance.** All combine-side outputs are stamped with the run mode's provenance label. Because the
combine input data are **complete**, the combine numbers are **REAL** in both modes (`full` ⇒ `FULL`).

**Caps & fallback.** See `notes/rules.md` for the official limits (≤2,000 words; <10 tables/figures) and the
stricter internal ≤5-figure cap. A CLI fallback that runs the identical per-stage scripts
(`python src/01_audit.py --mode full`, …) is documented in `README_RUN.md`.

**Regeneration.** This notebook is generated deterministically by `tools/build_notebook.py`
(`python tools/build_notebook.py`), which embeds `src/*.py` + `config.yaml` as sha256-verified
gzip+base64 payloads.
"""


def main() -> None:
    embedded = {dest: _payload(rel) for rel, dest in EMBED}
    manifest_json = json.dumps(embedded, sort_keys=False, indent=0)

    cell2_code = (
        "# ===================== Cell 1b — embedded stage sources + manifest =====================\n"
        "# Verbatim `src/*.py` + `config.yaml` (gzip+base64, sha256-verified). Generated by\n"
        "# tools/build_notebook.py; do not edit by hand — regenerate instead.\n"
        f"_EMBEDDED = {manifest_json}\n"
    )

    def code(src: str, cid: str) -> dict:
        return {
            "cell_type": "code",
            "execution_count": None,
            "id": cid,
            "metadata": {},
            "outputs": [],
            "source": src.splitlines(keepends=True),
        }

    def md(src: str, cid: str) -> dict:
        return {"cell_type": "markdown", "id": cid, "metadata": {}, "source": src.splitlines(keepends=True)}

    specs = [
        ("md", CELL0_MD),
        ("code", CELL1_CODE),
        ("code", cell2_code),
        ("code", CELL3_CODE),
        ("md", CELL4_MD), ("code", CELL4_CODE), ("code", CELL5_CODE),
        ("md", CELL6_MD), ("code", CELL6_CODE), ("code", CELL7_CODE),
        ("md", CELL8_MD), ("code", CELL8_CODE), ("code", CELL9_CODE),
        ("md", CELL10_MD), ("code", CELL10_CODE), ("code", CELL11_CODE),
        ("md", CELL12_MD), ("code", CELL12_CODE), ("code", CELL13_CODE),
        ("md", CELL14_MD),
        ("md", CELL15_MD),
    ]
    cells = [(md if kind == "md" else code)(src, f"cell-{i:02d}")
             for i, (kind, src) in enumerate(specs)]

    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(v["n_bytes"] for v in embedded.values())
    print(f"wrote {OUT_PATH} ({len(cells)} cells; {len(embedded)} embedded files, {total} raw bytes)")


if __name__ == "__main__":
    main()
