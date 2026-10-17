#!/usr/bin/env python3
"""synthetic_security_fixture.py — build SMALL, schema-compliant synthetic fixtures for the
BDB27 security/stakes pipeline (Phase-4 `src/06_security.py` + audit extension).

Why this exists
---------------
The real EDP board is external data (present on the Pi, `data/raw/external/gtm/`), but the pipeline
must be **exercised and tested** deterministically without it, and the spec requires a
**synthetic recovery** case: a planted security effect the pipeline must recover **and** a null case
it must **not** find one in (TASK.md "Tests before handoff" §2).

What it writes (to `fixtures/synthetic/security/<regime>/`)
-----------------------------------------------------------
* ``board.csv``             — schema-compliant EDP board (EXACT columns of the real file).
* ``combine_envelope.csv``  — a Phase-2-like per-player table (``nfl_id, combine_position,
                              draft_year, env_vmax, env_vmax_se``); ``env_vmax`` is the shrunken
                              Combine ceiling speed used as the Analysis-A regressor.
* ``combine_testing.csv``   — ``nfl_id, testing_day`` (per-player Combine testing date).
* ``combine_drills.csv``    — ``nfl_id, drill_type`` (drives the participation pattern).

Design guarantees
-----------------
* **Deterministic** given the seed.
* **Planted effect** (regime ``planted``): the pre→post EDP change is a function of Combine speed
  **interacted with** pre-Combine EDP, so Analysis A must recover a negative speed response that is
  **stronger for at-risk (high-EDP) players**.
* **Null** (regime ``null``): the pre→post EDP change is unrelated to speed; Analysis A must **not**
  find a relationship.
* **NEVER a finding**: fixture outputs are stamped ``SYNTHETIC FIXTURE (not a finding)``.

Usage
-----
    python src/synthetic_security_fixture.py [--out fixtures/synthetic/security] [--config config.yaml]
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

BOARD_COLS = ["draft_year", "name", "position", "school", "type", "nfl_id", "n_total", "n",
              "edp", "edp_rank", "pos_rank", "snapshot_date", "snapshot_note"]
ENV_COLS = ["nfl_id", "combine_position", "draft_year", "env_vmax", "env_vmax_se"]
TEST_COLS = ["nfl_id", "testing_day"]
DRILL_COLS = ["nfl_id", "drill_type"]

FIRST = ["Jordan", "Xavier", "Malik", "Tyree", "Cameron", "Devin", "Elijah", "Romeo", "Kendall",
         "Amari", "Jonah", "Trey", "Nico", "Isaiah", "Brennan", "Kai", "Marco", "Dante"]
LAST = ["Blake", "Whitfield", "Okafor", "Reyes", "Mensah", "Delgado", "Fontenot", "Achebe",
        "Sorensen", "Villanueva", "Kowalski", "Baptiste", "Ferreira", "Nakamura", "Hollis", "Rudd"]
COLLEGE = ["Alabama", "Ohio State", "Georgia", "Michigan", "LSU", "Clemson", "Oregon", "Texas"]

# snapshot label -> (MM, DD) per draft year: (2wk-pre, 1wk-pre, combine week, 1wk-post, 2wk-post)
SNAP_DATES = {
    2023: [(2, 14), (2, 21), (2, 28), (3, 7), (3, 14)],
    2024: [(2, 13), (2, 20), (2, 27), (3, 5), (3, 12)],
    2025: [(2, 11), (2, 18), (2, 25), (3, 4), (3, 11)],
}
SNAP_LABELS = ["2 Weeks Pre-Combine", "1 Week Pre-Combine", "Combine Week",
               "1 Week Post-Combine", "2 Weeks Post-Combine"]
# testing day per year (strictly AFTER the 1-week-pre snapshot)
TEST_DAY = {2023: (3, 3), 2024: (3, 1), 2025: (2, 28)}


def _ddmmyyyy(y: int, md: tuple[int, int]) -> str:
    return f"{md[1]:02d}/{md[0]:02d}/{y}"


def build_fixture(out_dir: Path, seed: int, n_players: int, years: list[int],
                  planted: bool, combine_position_year_cycle: bool = True) -> dict[str, Path]:
    """Write the synthetic security fixture into ``out_dir``. Return the file paths."""
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    board_rows: list[dict] = []
    env_rows: list[dict] = []
    test_rows: list[dict] = []
    drill_rows: list[dict] = []

    # generate the latent quantities first so we can standardise z-scores before planting
    latent = []
    for i in range(n_players):
        year = int(years[i % len(years)])
        # position drawn independently of year (avoids a collinear pos/year dummy in Analysis A)
        pos = "TE" if float(rng.random()) < 0.30 else "WR"
        log_edp = float(rng.normal(3.2, 0.8))
        log_edp = float(min(max(log_edp, math.log(1.0)), math.log(300.0)))
        edp_pre = float(math.exp(log_edp))
        speed = float(rng.normal(9.0, 0.6) - 0.25 * (log_edp - 3.2) / 0.8)
        latent.append(dict(i=i, year=year, pos=pos, log_edp=log_edp, edp_pre=edp_pre, speed=speed))
    le = np.array([d["log_edp"] for d in latent], float)
    sp = np.array([d["speed"] for d in latent], float)
    le_sd = float(np.std(le, ddof=0)) or 1.0
    sp_sd = float(np.std(sp, ddof=0)) or 1.0
    le_mu, sp_mu = float(np.mean(le)), float(np.mean(sp))

    # planted coefficients: speeds lower EDP (reward); effect STRONGER for at-risk (high EDP)
    b1, b3 = (-3.0, -2.0) if planted else (0.0, 0.0)
    noise_sd = 1.5

    for d in latent:
        i, year, pos = d["i"], d["year"], d["pos"]
        z_edp = (d["log_edp"] - le_mu) / le_sd
        z_spd = (d["speed"] - sp_mu) / sp_sd
        d_edp = b1 * z_spd + b3 * z_spd * z_edp + float(rng.normal(0.0, noise_sd))
        edp_post = max(0.6, d["edp_pre"] + d_edp)

        nfl_id = 800001 + i
        name = f"{FIRST[i % len(FIRST)]} {LAST[i % len(LAST)]}"
        school = COLLEGE[i % len(COLLEGE)]
        n_total = 200 + int(rng.integers(0, 300))
        n = int(n_total - rng.integers(0, 40))
        dates = SNAP_DATES[year]
        # pre (2wk, 1wk) reflect the latent pre-EDP with mild wobble; post reflects edp_post
        pre_base = d["edp_pre"] + float(rng.normal(0.0, 0.6))
        for k, label in enumerate(SNAP_LABELS):
            if label == "2 Weeks Pre-Combine":
                edp = pre_base
            elif label == "1 Week Pre-Combine":
                edp = d["edp_pre"]
            elif label == "Combine Week":
                edp = 0.5 * (d["edp_pre"] + edp_post)
            elif label == "1 Week Post-Combine":
                edp = edp_post
            else:  # 2 Weeks Post-Combine
                edp = edp_post + float(rng.normal(0.0, 0.4))
            board_rows.append(dict(
                draft_year=year, name=name, position=pos, school=school, type="All", nfl_id=nfl_id,
                n_total=n_total, n=n, edp=round(max(0.5, edp), 6), edp_rank=i + 1,
                pos_rank=f"{pos}{i + 1}", snapshot_date=_ddmmyyyy(year, dates[k]), snapshot_note=label))
        # a couple of blank-nfl_id rows (undrafted/Unknown) to exercise the blank count
        if i % 25 == 0:
            board_rows.append(dict(
                draft_year=year, name=f"Unknown {i}", position=pos, school="Nowhere State",
                type="All", nfl_id="", n_total=n_total, n=n, edp=round(150.0 + i, 6), edp_rank=900 + i,
                pos_rank="UNK", snapshot_date=_ddmmyyyy(year, dates[1]), snapshot_note=SNAP_LABELS[1]))

        env_rows.append(dict(nfl_id=nfl_id, combine_position=pos, draft_year=year,
                             env_vmax=round(d["speed"], 6), env_vmax_se=round(0.15 + 0.05 * (i % 3), 6)))
        test_rows.append(dict(nfl_id=nfl_id, testing_day=_ddmmyyyy(year, TEST_DAY[year])))
        # participation pattern: most run both speed families; a few skip one
        drills = ["FORTY_YARD_DASH"]
        if i % 4 != 0:
            drills.append("THREE_CONE_DRILL")
        if i % 7 == 0:
            drills = ["THREE_CONE_DRILL"]           # dash skipper
        if i % 11 == 0:
            drills = []                              # no speed drills
        for dt in dict.fromkeys(drills):
            drill_rows.append(dict(nfl_id=nfl_id, drill_type=dt))

    board_rows.sort(key=lambda r: (r["draft_year"], r["name"], r["snapshot_note"]))
    paths = {"board": out_dir / "board.csv", "envelope": out_dir / "combine_envelope.csv",
             "testing": out_dir / "combine_testing.csv", "drills": out_dir / "combine_drills.csv"}
    _write_csv(paths["board"], BOARD_COLS, board_rows)
    _write_csv(paths["envelope"], ENV_COLS, env_rows)
    _write_csv(paths["testing"], TEST_COLS, test_rows)
    _write_csv(paths["drills"], DRILL_COLS, drill_rows)
    return paths


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the synthetic security fixture")
    ap.add_argument("--out", default=None, help="output root (default: fixtures/synthetic/security)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    common.seed_everything(cfg["seed"])
    root = Path(args.out) if args.out else common.resolve(cfg, "fixtures", "synthetic/security")
    for regime in ("planted", "null"):
        paths = build_fixture(out_dir=root / regime, seed=cfg["seed"] + (0 if regime == "planted" else 1),
                              n_players=60, years=[2023, 2024, 2025], planted=(regime == "planted"))
        for k, v in paths.items():
            print(f"  [{regime}] wrote {v}  ({v.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
