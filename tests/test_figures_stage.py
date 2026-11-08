"""Stage 06 figure 1 contract: counts, provenance, determinism, honest refusal.

The synthetic turn table mirrors the verified sample band counts, but every
assertion recomputes the counts from the Parquet it just wrote — nothing about
the numbers is trusted from the fixture definition.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
STAMP = "PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE OUTPUT"

# Mirrors outputs/turns/turns.parquet for DB: (combine, game) per band.
BAND_COUNTS = [
    (30.0, 45.0, 4, 97),
    (45.0, 60.0, 22, 229),
    (60.0, 90.0, 237, 476),
    (90.0, 120.0, 159, 508),
    (120.0, 150.0, 499, 460),
    (150.0, 180.0, 128, 307),
    (180.0, 400.0, 130, 185),
]
EDGES = [30.0, 45.0, 60.0, 90.0, 120.0, 150.0, 180.0]


def _turns_frame() -> pd.DataFrame:
    rows = []
    nfl = {"combine": [1001, 1002, 1003, 1004, 1005],
           "game": [2001, 2002, 2003]}
    game_i = 0
    for lo, hi, c_count, g_count in BAND_COUNTS:
        for setting, n in (("combine", c_count), ("game", g_count)):
            angles = np.linspace(lo + 0.1, hi - 0.1, n)
            for j, angle in enumerate(angles):
                if setting == "combine":
                    game_id, snap = None, None
                else:
                    game_id = 3000 + (game_i % 3)
                    snap = ("COVERAGE" if game_i < 1111 else
                            ("OTHER" if game_i < 1111 + 1121 else "PASS_RUSH"))
                    game_i += 1
                rows.append({
                    "setting": setting,
                    "abs_turn_angle": float(angle),
                    "position_group": "DB",
                    "nfl_id": nfl[setting][j % len(nfl[setting])],
                    "game_id": game_id,
                    "snap_type": snap,
                    "valid": True,
                })
    return pd.DataFrame(rows)


def _write_config(tmp: Path, *, edges=None) -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    block = cfg["turn_coverage"]
    block["paths"]["outputs"] = str(tmp / "outputs")
    block["paths"]["turns"] = str(tmp / "outputs" / "turns")
    block["paths"]["figures"] = str(tmp / "outputs" / "figures")
    block["paths"]["thresholds"] = str(tmp / "outputs" / "thresholds.json")
    block["paths"]["prereg"] = str(tmp / "notes" / "prereg.md")
    block["paths"]["run_log"] = str(tmp / "outputs" / "run_log.csv")
    if edges is not None:
        block["figures"]["hole"]["bands"]["turn_angle_deg"] = edges
    (tmp / "outputs" / "turns").mkdir(parents=True, exist_ok=True)
    (tmp / "outputs").mkdir(parents=True, exist_ok=True)
    (tmp / "outputs" / "thresholds.json").write_text(
        json.dumps({"provenance": "PLACEHOLDER_SAMPLE"}))
    _turns_frame().to_parquet(tmp / "outputs" / "turns" / "turns.parquet",
                              index=False)
    path = tmp / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return path


def _run(cfg_path: Path, mode: str = "sample") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SRC / "06_figures.py"), "--mode", mode,
         "--config", str(cfg_path)],
        capture_output=True, text=True, cwd=str(ROOT))


def _expected_counts(turns: pd.DataFrame, edges: list[float]) -> dict:
    valid = turns["valid"].fillna(False).astype(bool)
    db = turns[valid & (turns["position_group"] == "DB")]
    out = {}
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        key = (round(lo, 6), round(hi, 6))
        out[key] = db[(db["abs_turn_angle"] >= lo)
                      & (db["abs_turn_angle"] < hi)]["setting"].value_counts().to_dict()
    out[(round(edges[-1], 6), None)] = db[
        db["abs_turn_angle"] >= edges[-1]]["setting"].value_counts().to_dict()
    return out


def _check_counts(csv_df: pd.DataFrame, turns: pd.DataFrame,
                  edges: list[float]) -> None:
    expected = _expected_counts(turns, edges)
    seen = set()
    for _, row in csv_df.iterrows():
        key = (round(float(row["band_low_deg"]), 6),
               None if pd.isna(row["band_high_deg"])
               else round(float(row["band_high_deg"]), 6))
        assert key in expected, f"unexpected band {key}"
        want = int(expected[key].get(row["setting"], 0))
        assert int(row["count"]) == want, (
            f"{row['band']} {row['setting']}: csv={int(row['count'])} artefact={want}")
        total = int(turns[(turns["valid"].fillna(False).astype(bool))
                          & (turns["position_group"] == "DB")
                          & (turns["setting"] == row["setting"])].shape[0])
        assert np.isclose(float(row["share_within_setting"]),
                          want / total, atol=1e-12)
        seen.add((key, row["setting"]))
    assert len(seen) == len(expected) * 2


def _check_png(path: Path) -> None:
    assert path.exists(), "PNG is missing"
    assert path.stat().st_size > 0, "PNG is empty"
    with Image.open(path) as im:
        assert im.size[0] >= 1500, f"PNG is only {im.size[0]} px wide"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fig_hole")
    cfg = _write_config(tmp)
    res = _run(cfg)
    assert res.returncode == 0, res.stderr
    out = tmp / "outputs" / "figures"
    return {"tmp": tmp, "cfg": cfg, "png": out / "fig_the_hole.png",
            "csv": out / "fig_the_hole.csv",
            "manifest": out / "figures_manifest.json"}


def test_png_exists_non_empty_and_wide(rendered):
    _check_png(rendered["png"])


def test_csv_matches_artefact_counts_for_every_band(rendered):
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    csv_df = pd.read_csv(rendered["csv"])
    assert len(csv_df) == 14
    _check_counts(csv_df, turns, EDGES)


def test_provenance_stamp_in_csv_and_manifest(rendered):
    csv_df = pd.read_csv(rendered["csv"])
    assert csv_df["stamp"].eq(STAMP).all()
    man = json.loads(rendered["manifest"].read_text())
    assert man["stamp"] == STAMP
    assert man["provenance"] == "PLACEHOLDER_SAMPLE"
    assert man["label"] == "UNVALIDATED SAMPLE OUTPUT"
    fig = man["figures"]["fig_the_hole"]
    assert "30–45°" in fig["claim"]
    assert "2,262" in fig["caveats"][1]
    assert "1,111" in fig["caveats"][1]
    assert "49.6%" in fig["caveats"][1]


def test_two_runs_are_byte_identical(rendered):
    first = hashlib.sha256(rendered["png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    second = hashlib.sha256(rendered["png"].read_bytes()).hexdigest()
    assert first == second


def test_band_edges_come_from_config(rendered, tmp_path):
    cfg = _write_config(tmp_path, edges=[30.0, 45.0, 90.0, 180.0])
    res = _run(cfg)
    assert res.returncode == 0, res.stderr
    csv_path = tmp_path / "outputs" / "figures" / "fig_the_hole.csv"
    csv_df = pd.read_csv(csv_path)
    assert set(csv_df["band"]) == {"30–45°", "45–90°", "90–180°", ">180°"}
    turns = pd.read_parquet(tmp_path / "outputs" / "turns" / "turns.parquet")
    _check_counts(csv_df, turns, [30.0, 45.0, 90.0, 180.0])


def test_checks_can_fail(rendered, tmp_path):
    """The contracts above are only meaningful if a wrong artefact fails them."""
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["csv"])
    _check_counts(good, turns, EDGES)  # sanity: the checker passes on the real file

    bad = good.copy()
    idx = bad.index[(bad["band"] == "30–45°") & (bad["setting"] == "combine")][0]
    bad.loc[idx, "count"] = 5
    with pytest.raises(AssertionError):
        _check_counts(bad, turns, EDGES)

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty)

    man = json.loads(rendered["manifest"].read_text())
    assert man["stamp"] == STAMP


def test_full_mode_refuses_on_non_full_thresholds(tmp_path):
    cfg = _write_config(tmp_path)
    res = _run(cfg, mode="full")
    assert res.returncode != 0
    assert "thresholds provenance" in res.stderr
    assert "FULL" in res.stderr
    assert not (tmp_path / "outputs" / "figures" / "fig_the_hole.png").exists()


def test_full_mode_refuses_without_prereg(tmp_path):
    cfg = _write_config(tmp_path)
    (tmp_path / "outputs" / "thresholds.json").write_text(
        json.dumps({"provenance": "FULL"}))
    res = _run(cfg, mode="full")
    assert res.returncode != 0
    assert "prereg" in res.stderr
    assert not (tmp_path / "outputs" / "figures" / "fig_the_hole.png").exists()
