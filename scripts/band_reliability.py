"""Write the band-reliability artefact (D206).

Reads the committed turn parquet and the turn-context parquet (for ``play_type``),
computes ICC(1) for the four scenarios and the adjusted ICC, prints the derived
quantities with their formulas, and writes ``outputs/band_reliability/`` with a
sha256 manifest.

Usage:
    .venv/bin/python scripts/band_reliability.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bdb27 import band_reliability as br  # noqa: E402

TURNS = ROOT / "data" / "interim" / "full_run_20261111" / "bdb27_full_db_turns.parquet"
CONTEXT = ROOT / "outputs" / "turn_context" / "turn_context.parquet"
OUT = ROOT / "outputs" / "band_reliability"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    turns = br.load_turns(TURNS)
    context = br.load_context(CONTEXT)
    result = br.build(turns, context)

    reliability = OUT / "reliability.json"
    reliability.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    table = pd.DataFrame(result["scenarios"])[
        ["name", "rule", "players", "turns", "kbar", "icc", "status"]]
    scenarios_csv = OUT / "scenarios.csv"
    table.to_csv(scenarios_csv, index=False)

    manifest = br.write_manifest(
        OUT, [TURNS, CONTEXT], [reliability, scenarios_csv],
        config={
            "band": result["band"],
            "scenarios": [s["name"] for s in result["scenarios"]],
            "entry_speed_window": [br.ENTRY_LO, br.ENTRY_HI],
            "adjustment_covariates": list(br.ADJUSTMENT_COVARIATES),
            "group": "nfl_id",
            "value": "apex_retention",
        },
        extra={"sample": result["sample"],
               "headline": {s["name"]: {"players": s["players"], "turns": s["turns"],
                                        "kbar": s["kbar"], "icc": s["icc"]}
                            for s in result["scenarios"]},
               "adjustment": result["adjustment"],
               "derived": result["derived"]})

    print(json.dumps({
        "scenarios": [{k: s[k] for k in ("name", "players", "turns", "kbar", "icc")}
                      for s in result["scenarios"]],
        "adjustment": {k: {"r_squared": v["r_squared"], "icc": v["icc"]}
                       for k, v in result["adjustment"].items()},
        "derived": result["derived"]["inputs"],
        "reliability_of_mean": result["derived"]["reliability_of_player_mean"],
        "rho_for_R70": result["derived"]["rho_needed_for_R_0_70_at_k30"],
        "k_for_R70": result["derived"]["k_needed_for_R_0_70_at_adjusted_rho"],
        "clusters_for_observed_effect":
            result["derived"]["clusters_for_observed_effect"]["clusters_required"],
        "manifest": str(OUT / "manifest.json"),
        "manifest_inputs": list(manifest["inputs"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
