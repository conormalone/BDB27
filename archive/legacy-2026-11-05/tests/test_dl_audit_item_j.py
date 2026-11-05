"""Regression: item j must not crash when both load columns are entirely NA.

``read_csv_auto`` infers a VARCHAR type for an all-null column, so an unguarded
``corr(absolute_load, relative_load)`` fails with a DuckDB binder error.  Sample mode produces
exactly that table (both loads are NA by construction), so this pins the TRY_CAST fix.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import yaml

from dl import dl_audit_power as J
from dl import dl_common as D


def test_item_j_all_null_loads_is_pending_not_an_error(tmp_path, cfg):
    clone = copy.deepcopy(cfg)
    block = clone["bdb27_dl"]
    block["paths"].update({
        "raw": str(tmp_path / "raw"),
        "interim": str(tmp_path / "interim"),
        "parquet": str(tmp_path / "interim" / "parquet"),
        "checkpoints": str(tmp_path / "interim" / "checkpoints"),
        "tmp": str(tmp_path / "interim" / "tmp"),
        "outputs": str(tmp_path / "out"),
        "figures": str(tmp_path / "out" / "figures"),
        "thresholds": str(tmp_path / "out" / "thresholds.json"),
        "run_log": str(tmp_path / "out" / "run_log.csv"),
    })
    block["runtime"]["min_free_mb_root"] = 1
    block["runtime"]["min_free_mb_interim"] = 1
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(clone, sort_keys=False), encoding="utf-8")
    loaded = D.load_config(cfg_path)

    snaps_dir = Path(block["paths"]["outputs"]) / block["game"]["snap_out_subdir"]
    snaps_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"absolute_load": ["NA"], "relative_load": ["NA"]}).to_csv(
        snaps_dir / "snaps.csv", index=False)

    out_dir = tmp_path / "audit"
    out_dir.mkdir()
    result = J.item_j(loaded, "sample", out_dir, {})

    assert result["status"] == J.PENDING
    rows = pd.read_csv(out_dir / "j_load_collinearity.csv")
    assert int(rows["n"].iloc[0]) == 0
