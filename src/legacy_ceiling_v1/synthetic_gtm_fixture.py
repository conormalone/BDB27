#!/usr/bin/env python3
"""synthetic_gtm_fixture.py — build a SMALL, schema-compliant synthetic Grinding-The-Mocks fixture.

Why this exists
---------------
The Phase 0.5 spec (`notes/phase-0.5-external-proxies.md`) ingests external **Grinding The Mocks**
(GTM) mock-draft history and maps it onto Kaggle `players.csv`. As of the bounded acquisition attempt
the live GTM export is **not machine-obtainable** (its front end is a Shiny websocket app; `robots.txt`
disallows crawling; no static export / GitHub mirror / usable Wayback capture — see
`notes/external_data_manifest.md` and `notes/blockers.md`). We must **not fabricate data**, so instead
this fixture lets the *identical* ingest → February-filter → EDP-aggregation → fuzzy-match → strict-
parquet pipeline run end to end, deterministically, and be tested.

What it writes (to `fixtures/synthetic/gtm/`)
---------------------------------------------
* ``kaggle_players.csv``  — a schema-compliant **subset** of the Kaggle players table
  (``nfl_id, display_name, draft_year, nfl_position, college_name, draft_round, draft_overall_pick``).
* ``gtm_mock_history.csv`` — raw GTM mock history: one row per (mock, prospect) with a projected pick
  and a snapshot date. Columns: ``mock_id, mock_source, snapshot_date, draft_year, prospect_name,
  position, college, projected_pick``.

Design guarantees
-----------------
* **Deterministic** given the seed (no wall-clock, no unseeded RNG).
* **Exercises the February filter**: each prospect also gets a **January** and a **March** snapshot
  whose projected picks are extreme outliers, so a filter bug visibly moves the EDP.
* **Exercises fuzzy matching**: ``n_near_miss`` prospects appear in GTM with a *spelling variant* of
  their Kaggle name (they must still match); ``n_unmatched_gtm`` prospects have no Kaggle counterpart
  (they must land in ``unmatched_external_ids.csv``).
* **NEVER a finding**: fixture outputs are stamped ``SYNTHETIC FIXTURE (not a finding)``.

Usage
-----
    python src/synthetic_gtm_fixture.py [--out fixtures/synthetic/gtm] [--config config.yaml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

# small, fixed vocabularies (deterministic; no wall-clock / network)
FIRST = ["Jordan", "Xavier", "Malik", "Tyree", "Cameron", "Devin", "Elijah", "Romeo",
         "Kendall", "Amari", "Jonah", "Trey", "Nico", "Isaiah", "Brennan", "Kai"]
LAST = ["Blake", "Whitfield", "Okafor", "Reyes", "Mensah", "Delgado", "Fontenot", "Achebe",
        "Sorensen", "Villanueva", "Kowalski", "Baptiste", "Ferreira", "Nakamura", "Hollis", "Rudd"]
COLLEGE = ["Alabama", "Ohio State", "Georgia", "Michigan", "LSU", "Clemson", "Oregon",
           "Notre Dame", "Texas", "Penn State", "Florida State", "Iowa"]
MOCK_SOURCES = ["MockSource_A", "MockSource_B", "MockSource_C", "MockSource_D", "MockSource_E"]

UNMATCHED_FIRST = ["Zephyr", "Quillon", "Obadiah"]
UNMATCHED_LAST = ["Nightingale", "Castellanos", "Featherstone"]


def _spell_variant(name: str, rng: np.random.Generator) -> str:
    """Return a small, realistic spelling perturbation of `name` (keeps fuzzy match viable)."""
    modes = ["drop_last_char", "double_letter", "swap", "nickname"]
    mode = modes[int(rng.integers(0, len(modes)))]
    first, _, last = name.partition(" ")
    if mode == "drop_last_char" and len(last) > 3:
        return f"{first} {last[:-1]}"
    if mode == "double_letter" and len(last) > 3:
        return f"{first} {last[:2]}{last[1:]}"
    if mode == "swap" and len(first) > 3:
        return f"{first[1]}{first[0]}{first[2:]} {last}"
    nick = {"Jordan": "Jordyn", "Cameron": "Kamron", "Devin": "Devyn", "Isaiah": "Issaiah",
            "Malik": "Mallic", "Xavier": "Zavier"}
    return f"{nick.get(first, first + 'n')} {last}"


def build_fixture(out_dir: Path, seed: int, n_players: int, n_mocks_per_player: int,
                  n_unmatched_gtm: int, n_near_miss: int,
                  years: list[int], positions: list[str]) -> dict[str, Path]:
    """Write the synthetic GTM fixture (players subset + raw mock history). Return the paths."""
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    pl_rows: list[dict] = []
    gtm_rows: list[dict] = []
    mock_id = 1

    for pi in range(n_players):
        first = FIRST[pi % len(FIRST)]
        last = LAST[pi % len(LAST)]
        name = f"{first} {last}"
        pos = positions[pi % len(positions)]
        year = int(years[pi % len(years)])
        college = COLLEGE[pi % len(COLLEGE)]
        nfl_id = 900001 + pi
        drafted = (pi % 5 != 0)                       # every 5th prospect undrafted (explicit NA tier)
        rnd = (pi % 7) + 1 if drafted else None
        overall = ((rnd - 1) * 32 + (pi % 32) + 1) if drafted else None
        pl_rows.append(dict(
            nfl_id=nfl_id, display_name=name, draft_year=year, nfl_position=pos,
            college_name=college,
            draft_round=(rnd if drafted else "NA"),
            draft_overall_pick=(overall if drafted else "NA"),
        ))

        # GTM uses the *variant* spelling for the first `n_near_miss` prospects
        gtm_name = _spell_variant(name, rng) if pi < n_near_miss else name

        # base "true" expected pick, then February mocks scattered around it
        base = 20 + 12 * pi
        feb_picks = [max(1, int(base + rng.integers(-8, 9))) for _ in range(n_mocks_per_player)]
        # outlier snapshots OUTSIDE February -> must be filtered out (January / March)
        jan_pick = max(1, base + 90)
        mar_pick = max(1, base - 15)                  # post-Combine drop (would bias EDP if kept)
        snaps = [(f"{year}-02-{5 + (m % 20):02d}", feb_picks[m]) for m in range(n_mocks_per_player)]
        snaps += [(f"{year}-01-15", jan_pick), (f"{year}-03-10", mar_pick)]
        for m, (date, pick) in enumerate(snaps):
            gtm_rows.append(dict(
                mock_id=f"GTM{mock_id:06d}", mock_source=MOCK_SOURCES[m % len(MOCK_SOURCES)],
                snapshot_date=date, draft_year=year, prospect_name=gtm_name,
                position=pos, college=college, projected_pick=int(pick),
            ))
            mock_id += 1

    # unmatched GTM prospects (no Kaggle counterpart) — exercise unmatched_external_ids.csv
    for ui in range(n_unmatched_gtm):
        first = UNMATCHED_FIRST[ui % len(UNMATCHED_FIRST)]
        last = UNMATCHED_LAST[ui % len(UNMATCHED_LAST)]
        pos = positions[ui % len(positions)]
        year = int(years[ui % len(years)])
        for m in range(2):
            gtm_rows.append(dict(
                mock_id=f"GTM{mock_id:06d}", mock_source=MOCK_SOURCES[m % len(MOCK_SOURCES)],
                snapshot_date=f"{year}-02-{10 + m:02d}", draft_year=year,
                prospect_name=f"{first} {last}", position=pos, college="Nowhere State",
                projected_pick=int(150 + ui * 20 + m),
            ))
            mock_id += 1

    players = pd.DataFrame(pl_rows)
    gtm = pd.DataFrame(gtm_rows)
    paths = {
        "players": out_dir / "kaggle_players.csv",
        "gtm": out_dir / "gtm_mock_history.csv",
    }
    players.to_csv(paths["players"], index=False)
    gtm.to_csv(paths["gtm"], index=False)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the synthetic GTM fixture (Phase 0.5)")
    ap.add_argument("--out", default=None, help="output dir (default: fixtures/synthetic/gtm)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    ext = cfg["external"]
    syn = ext["synthetic"]
    common.seed_everything(cfg["seed"])
    out = Path(args.out) if args.out else common.resolve(cfg, "fixtures", syn["fixture_dir"])
    paths = build_fixture(
        out_dir=out,
        seed=int(syn["seed"]),
        n_players=int(syn["n_players"]),
        n_mocks_per_player=int(syn["n_mocks_per_player"]),
        n_unmatched_gtm=int(syn["n_unmatched_gtm"]),
        n_near_miss=int(syn["n_near_miss"]),
        years=[int(y) for y in ext["years"]],
        positions=[str(p) for p in ext["positions"]],
    )
    for k, v in paths.items():
        print(f"  wrote {v}  ({v.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
