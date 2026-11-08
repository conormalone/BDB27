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

# Figure 5 needs every plotted position. These mirror the verified sample band
# counts for the non-DB positions: (band_index, combine, game) per band, in the
# same left-closed bands. Assertions still recompute from the written Parquet.
POSITION_BAND_COUNTS = {
    "WR": [(0, 25, 58), (1, 65, 164), (2, 385, 464), (3, 256, 357),
           (4, 174, 247), (5, 55, 126), (6, 84, 88)],
    "TE": [(0, 3, 14), (1, 28, 28), (2, 104, 86), (3, 34, 72),
           (4, 29, 56), (5, 38, 25), (6, 58, 13)],
    "OL": [(0, 5, 34), (1, 57, 94), (2, 258, 260), (3, 97, 299),
           (4, 109, 270), (5, 199, 201), (6, 227, 92)],
    "DL": [(0, 34, 25), (1, 61, 76), (2, 539, 206), (3, 358, 215),
           (4, 387, 227), (5, 349, 206), (6, 339, 123)],
}
ALL_POSITIONS = ["DB", "WR", "TE", "OL", "DL"]

# Verified DB Combine inventory (valid turns per drill) that the synthetic
# table reproduces exactly; every assertion still recomputes from the Parquet.
DRILL_TOTALS = {
    "BOX_DRILL": 304,
    "TERYL_AUSTIN_DRILL_2": 199,
    "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION": 133,
    "BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL": 118,
    "TERYL_AUSTIN_DRILL_1": 108,
    "W_DRILL": 107,
    "THREE_CONE_DRILL": 98,
    "SHORT_SHUTTLE": 70,
    "GAUNTLET_DRILL": 41,
    "FORTY_YARD_DASH": 1,
}


def _drill_sequence() -> list[str]:
    """One drill label per Combine turn, shuffled with a fixed seed."""
    labels = [d for d, n in DRILL_TOTALS.items() for _ in range(n)]
    np.random.RandomState(20270106).shuffle(labels)
    return labels


def _turns_frame() -> pd.DataFrame:
    rows = []
    # 122 Combine players mirrors the real table, so the figure-3 projection
    # arithmetic under test is 6 reps x 122 DBs = 732, not a toy number.
    nfl = {"combine": list(range(1001, 1123)),
           "game": [2001, 2002, 2003]}
    drills = _drill_sequence()
    k = 0
    game_i = 0
    for lo, hi, c_count, g_count in BAND_COUNTS:
        for setting, n in (("combine", c_count), ("game", g_count)):
            angles = np.linspace(lo + 0.1, hi - 0.1, n)
            for j, angle in enumerate(angles):
                if setting == "combine":
                    game_id, snap, drill = None, None, drills[k]
                    k += 1
                    entry = (3.0 + 0.15 * j if (lo, hi) == (30.0, 45.0)
                             else 2.6 + 5.9 * (((j * 37) + int(lo)) % 100) / 100.0)
                else:
                    game_id = 3000 + (game_i % 3)
                    snap = ("COVERAGE" if game_i < 1111 else
                            ("OTHER" if game_i < 1111 + 1121 else "PASS_RUSH"))
                    game_i += 1
                    drill, entry = np.nan, 4.0
                rows.append({
                    "setting": setting,
                    "abs_turn_angle": float(angle),
                    "position_group": "DB",
                    "nfl_id": nfl[setting][j % len(nfl[setting])],
                    "game_id": game_id,
                    "snap_type": snap,
                    "drill_name": drill,
                    "entry_speed": float(entry),
                    # Figure 6 needs a real acceleration per turn. Monotone in
                    # angle with a deterministic ripple; assertions recompute
                    # the medians and p95 from this written table.
                    "peak_lateral_accel_yd_s2": float(
                        4.2 + 0.07 * angle + 0.9 * ((j * 13 + int(lo)) % 7) / 7.0),
                    "event_id": f"{setting[0]}{lo:g}_{j}",
                    "attempt": 1.0,
                    "valid": True,
                })
    assert k == sum(DRILL_TOTALS.values())
    return pd.DataFrame(rows)


def _extra_positions_frame() -> pd.DataFrame:
    """WR/TE/OL/DL rows for the five-panel figure.

    Deterministic and independent of the DB block above, so figure 1's expected
    counts are untouched by the fixture growth.
    """
    rows = []
    for i, (position, bands) in enumerate(POSITION_BAND_COUNTS.items()):
        combine_ids = list(range(5001 + i * 200, 5001 + i * 200 + 40))
        game_ids = combine_ids[:8]
        for band_index, c_count, g_count in bands:
            lo = EDGES[band_index]
            hi = (EDGES[band_index + 1] if band_index + 1 < len(EDGES)
                  else 400.0)
            for setting, n in (("combine", c_count), ("game", g_count)):
                angles = np.linspace(lo + 0.1, hi - 0.1, n) if n else []
                for j, angle in enumerate(angles):
                    ids = combine_ids if setting == "combine" else game_ids
                    rows.append({
                        "setting": setting,
                        "abs_turn_angle": float(angle),
                        "position_group": position,
                        "nfl_id": ids[j % len(ids)],
                        "game_id": None if setting == "combine" else 7000 + j % 4,
                        "snap_type": ("COVERAGE" if setting == "game" else None),
                        "drill_name": ("BOX_DRILL" if setting == "combine"
                                       else np.nan),
                        "entry_speed": float(3.0 + (j % 5) * 0.8),
                        "peak_lateral_accel_yd_s2": float(
                            4.4 + 0.065 * angle + 0.7 * ((j * 5 + i) % 5) / 5.0),
                        "event_id": f"{position}{setting[0]}{band_index}_{j}",
                        "attempt": 1.0,
                        "valid": True,
                    })
    return pd.DataFrame(rows)


#: The real file's 8 INTERIOR components, by (setting, position, measure). The
#: other 43 of the 51 synthetic components sit AT_BOUNDARY with tau2 = 0, so the
#: fixture reproduces the population the figure's count is written against.
INTERIOR_COMPONENTS = {
    ("game", "TE", "arc_capacity"),
    ("game", "DB", "plant_retention"),
    ("game", "DL", "plant_retention"),
    ("game", "OL", "plant_retention"),
    ("game", "TE", "plant_retention"),
    ("game", "WR", "plant_retention"),
    ("combined", "OTHER", "arc_capacity"),
    ("combined", "OTHER", "plant_retention"),
}

ML_POSITIONS = {"combine": ["DB", "DL", "OL", "TE", "WR"],
                "game": ["DB", "DL", "OL", "OTHER", "TE", "WR"],
                "combined": ["DB", "DL", "OL", "OTHER", "TE", "WR"]}
ML_MEASURES = ["arc_capacity", "plant_retention", "plant_regain"]


def _variance_frame() -> pd.DataFrame:
    """A 51-component variance table with exactly 43 AT_BOUNDARY rows."""
    rows = []
    for setting, positions in ML_POSITIONS.items():
        for i, position in enumerate(positions):
            for j, measure in enumerate(ML_MEASURES):
                interior = (setting, position, measure) in INTERIOR_COMPONENTS
                tau2 = 0.0 if not interior else 0.15 + 0.02 * (i * 3 + j)
                rows.append({
                    "provenance": "PLACEHOLDER_SAMPLE",
                    "setting": setting,
                    "position_group": position,
                    "measure": measure,
                    "n_players": 20 + i + j,
                    "tau2": tau2,
                    "tau2_p05": 0.0 if not interior else tau2 * 0.5,
                    "tau2_p95": 0.0 if not interior else tau2 * 1.6,
                    "tau2_status": "INTERIOR" if interior else "AT_BOUNDARY",
                })
    return pd.DataFrame(rows)


def _write_config(tmp: Path, *, edges=None) -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    block = cfg["turn_coverage"]
    block["paths"]["outputs"] = str(tmp / "outputs")
    block["paths"]["turns"] = str(tmp / "outputs" / "turns")
    block["paths"]["envelope"] = str(tmp / "outputs" / "envelope")
    block["paths"]["figures"] = str(tmp / "outputs" / "figures")
    block["paths"]["thresholds"] = str(tmp / "outputs" / "thresholds.json")
    block["paths"]["prereg"] = str(tmp / "notes" / "prereg.md")
    block["paths"]["run_log"] = str(tmp / "outputs" / "run_log.csv")
    if edges is not None:
        block["figures"]["hole"]["bands"]["turn_angle_deg"] = edges
    (tmp / "outputs" / "turns").mkdir(parents=True, exist_ok=True)
    (tmp / "outputs" / "envelope").mkdir(parents=True, exist_ok=True)
    (tmp / "outputs").mkdir(parents=True, exist_ok=True)
    (tmp / "outputs" / "thresholds.json").write_text(
        json.dumps({"provenance": "PLACEHOLDER_SAMPLE"}))
    pd.concat([_turns_frame(), _extra_positions_frame()], ignore_index=True
              ).to_parquet(tmp / "outputs" / "turns" / "turns.parquet",
                           index=False)
    _variance_frame().to_csv(
        tmp / "outputs" / "envelope" / "variance_components.csv", index=False)
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
            "drill_png": out / "fig_drill_coverage.png",
            "drill_csv": out / "fig_drill_coverage.csv",
            "ba_png": out / "fig_before_after.png",
            "ba_csv": out / "fig_before_after.csv",
            "ds_png": out / "fig_drill_schematic.png",
            "ds_csv": out / "fig_drill_schematic.csv",
            "ap_png": out / "fig_all_positions.png",
            "ap_csv": out / "fig_all_positions.csv",
            "ml_png": out / "fig_measurement_limits.png",
            "ml_csv": out / "fig_measurement_limits.csv",
            "gh_png": out / "fig_gap_honest.png",
            "gh_csv": out / "fig_gap_honest.csv",
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
    assert not (tmp_path / "outputs" / "figures" / "fig_drill_coverage.png").exists()


def test_full_mode_refuses_without_prereg(tmp_path):
    cfg = _write_config(tmp_path)
    (tmp_path / "outputs" / "thresholds.json").write_text(
        json.dumps({"provenance": "FULL"}))
    res = _run(cfg, mode="full")
    assert res.returncode != 0
    assert "prereg" in res.stderr
    assert not (tmp_path / "outputs" / "figures" / "fig_the_hole.png").exists()
    assert not (tmp_path / "outputs" / "figures" / "fig_drill_coverage.png").exists()


# ------------------------------------------------ figure 2: drill coverage
def _check_drill_csv(csv_df: pd.DataFrame, turns: pd.DataFrame) -> None:
    """CSV drill-total and target-cell counts must match the written Parquet."""
    valid = turns["valid"].fillna(False).astype(bool)
    db = turns[valid & (turns["position_group"] == "DB")
               & (turns["setting"] == "combine")]
    assert len(csv_df) == len(DRILL_TOTALS) + 1
    measured = csv_df[csv_df["kind"] == "measured"]
    assert len(measured) == len(DRILL_TOTALS)
    for drill, g in db.groupby("drill_name"):
        row = measured[measured["drill"] == drill]
        assert len(row) == 1, f"drill {drill} appears {len(row)} times"
        assert int(row["n_turns"].iloc[0]) == len(g), drill
        assert int(row["n_players"].iloc[0]) == g["nfl_id"].nunique(), drill
        assert np.isclose(float(row["median_angle_deg"].iloc[0]),
                          g["abs_turn_angle"].median())
        assert np.isclose(float(row["median_entry_speed_yd_s"].iloc[0]),
                          g["entry_speed"].median())
    assert int(measured["n_turns"].sum()) == len(db)
    target = db[(db["abs_turn_angle"] >= 30.0) & (db["abs_turn_angle"] < 45.0)
                & (db["entry_speed"] >= 2.0) & (db["entry_speed"] < 6.0)]
    assert int(measured["turns_in_target_cell"].sum()) == len(target)
    proposed = csv_df[csv_df["kind"] == "proposed"]
    assert len(proposed) == 1
    p = proposed.iloc[0]
    assert float(p["median_angle_deg"]) == 40.0
    assert float(p["median_entry_speed_yd_s"]) == 4.0
    assert float(p["median_reps_per_player"]) == 6.0


def test_drill_png_exists_non_empty_and_wide(rendered):
    _check_png(rendered["drill_png"])


def test_drill_csv_matches_artefact_counts(rendered):
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    csv_df = pd.read_csv(rendered["drill_csv"])
    _check_drill_csv(csv_df, turns)
    assert set(csv_df.loc[csv_df["kind"] == "measured", "drill"]) == set(DRILL_TOTALS)


def test_drill_provenance_stamp_in_csv_and_manifest(rendered):
    csv_df = pd.read_csv(rendered["drill_csv"])
    assert csv_df["stamp"].eq(STAMP).all()
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"]["fig_drill_coverage"]
    assert fig["stamp"] == STAMP
    assert man["provenance"] == "PLACEHOLDER_SAMPLE"
    assert float(fig["proposed"]["angle"]) == 40.0
    assert fig["target_angle_deg"] == [30.0, 45.0]
    assert fig["target_entry_speed_yd_s"] == [2.0, 6.0]
    assert "players × one session" in fig["caveats"][0]


def test_drill_two_runs_are_byte_identical(rendered):
    first = hashlib.sha256(rendered["drill_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    second = hashlib.sha256(rendered["drill_png"].read_bytes()).hexdigest()
    assert first == second


def test_drill_checks_can_fail(rendered):
    """A wrong drill total or target-cell count must fail the checker."""
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["drill_csv"])
    _check_drill_csv(good, turns)

    bad = good.copy()
    idx = bad.index[bad["kind"] == "measured"][0]
    bad.loc[idx, "n_turns"] = int(bad.loc[idx, "n_turns"]) + 1
    with pytest.raises(AssertionError):
        _check_drill_csv(bad, turns)

    bad_cell = good.copy()
    hit = bad_cell.index[(bad_cell["kind"] == "measured")
                         & (bad_cell["turns_in_target_cell"] > 0)]
    assert len(hit) > 0
    bad_cell.loc[hit[0], "turns_in_target_cell"] = 0
    with pytest.raises(AssertionError):
        _check_drill_csv(bad_cell, turns)

    empty = rendered["tmp"] / "empty_drill.png"
    empty.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty)


# ------------------------------------------- figure 3: before and after
BA_ANGLE_EDGES = [30.0, 45.0, 60.0, 90.0, 120.0, 150.0, 180.0]
BA_SPEED_EDGES = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
BA_PANEL_A = "(a) Before: measured Combine supply"
BA_PANEL_B = "(b) After: measured supply + proposed drill"


def _ba_cells(turns: pd.DataFrame):
    from bdb27 import figures
    return figures.supply_grid(turns, BA_ANGLE_EDGES, ">180", BA_SPEED_EDGES, "DB")


def _cfg_obj(path: Path):
    from bdb27 import common
    return common.load_config(path)


def _check_ba_csv(csv_df: pd.DataFrame, turns: pd.DataFrame) -> None:
    """Every measured cell must reconcile to the Parquet it was rendered from."""
    valid = turns["valid"].fillna(False).astype(bool)
    db = turns[valid & (turns["position_group"] == "DB")
               & (turns["setting"] == "combine")]

    measured = csv_df[csv_df["panel"] == "measured"]
    # 6 angle bands + the >=180 overflow class, 6 speed bands: 7 x 6.
    assert len(measured) == (len(BA_ANGLE_EDGES)) * (len(BA_SPEED_EDGES) - 1) == 42

    for _, row in measured.iterrows():
        lo_a, hi_a = float(row["band_low_deg"]), row["band_high_deg"]
        lo_s, hi_s = float(row["speed_low_yd_s"]), float(row["speed_high_yd_s"])
        amask = (db["abs_turn_angle"] >= lo_a)
        if pd.notna(hi_a):
            amask = amask & (db["abs_turn_angle"] < float(hi_a))
        else:  # the >= last edge overflow class
            amask = amask & (db["abs_turn_angle"] >= BA_ANGLE_EDGES[-1])
        smask = (db["entry_speed"] >= lo_s) & (db["entry_speed"] < hi_s)
        want = int((amask & smask).sum())
        assert int(row["count"]) == want, (
            f"{row['band']} x {row['speed_band']}: csv={int(row['count'])} "
            f"artefact={want}")

    assert int(measured["count"].sum()) == len(db)

    projected = csv_df[csv_df["panel"] == "projected"]
    assert len(projected) == 2
    assert projected["count"].isna().all(), "the region must not be split per cell"
    assert projected["region_count"].nunique() == 1
    assert float(projected["region_count"].iloc[0]) == 732.0
    assert projected["region_id"].nunique() == 1
    assert set(projected["band"]) == {"30–45°"}
    assert set(projected["speed_band"]) == {"2–4", "4–6"}


def test_before_after_png_exists_non_empty_and_wide(rendered):
    _check_png(rendered["ba_png"])


def test_before_after_csv_matches_artefact_counts(rendered):
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    csv_df = pd.read_csv(rendered["ba_csv"])
    _check_ba_csv(csv_df, turns)


def test_before_after_blank_cells_stay_blank_not_zero_grey(rendered):
    """The CSV records every grid cell; the heatmap plots only non-empty ones."""
    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    csv_df = pd.read_csv(rendered["ba_csv"])
    measured = csv_df[csv_df["panel"] == "measured"]
    assert (measured["count"] == 0).any(), "fixture must contain empty cells"

    table, context = _ba_cells(turns)
    proj = figures.projected_block(table, context,
                                   _cfg_obj(rendered["cfg"]))
    frames = figures.before_after_plot_frame(table, proj)
    assert (frames["cells"]["count"] > 0).all(), "empty cells must not be drawn"
    assert len(frames["cells"]) == 2 * int((table["count"] > 0).sum())
    assert (table["count"] == 0).sum() == context["n_empty"]


def test_before_after_projection_is_arithmetic_and_not_split(rendered):
    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = _ba_cells(turns)
    proj = figures.projected_block(table, context, _cfg_obj(rendered["cfg"]))
    assert proj["n_players"] == 122
    assert proj["reps"] == 6
    assert proj["total"] == 122 * 6 == 732
    assert proj["n_cells"] == 2
    assert proj["angle"] == 40.0
    assert proj["speed_window"] == (2.0, 6.0)


def test_before_after_two_runs_are_byte_identical(rendered):
    first = hashlib.sha256(rendered["ba_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    second = hashlib.sha256(rendered["ba_png"].read_bytes()).hexdigest()
    assert first == second


def test_before_after_provenance_stamp_present(rendered):
    from bdb27 import figures
    csv_df = pd.read_csv(rendered["ba_csv"])
    assert csv_df["stamp"].eq(STAMP).all()
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"]["fig_before_after"]
    assert fig["stamp"] == STAMP
    assert man["provenance"] == "PLACEHOLDER_SAMPLE"
    # the stamp is also on the image itself (caption text of the drawn plot)
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = _ba_cells(turns)
    proj = figures.projected_block(table, context, _cfg_obj(rendered["cfg"]))
    plot = figures.plot_before_after(table, context, proj,
                                     _cfg_obj(rendered["cfg"]))
    assert STAMP in plot.labels.get("caption", "")


def test_before_after_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"]["fig_before_after"]
    assert fig["grid"]["shape"] == [7, 6]
    assert fig["grid"]["angle_edges_deg"] == BA_ANGLE_EDGES
    assert fig["grid"]["speed_edges_yd_s"] == BA_SPEED_EDGES
    assert fig["projection"]["total_turns"] == 732
    assert fig["projection"]["per_cell_split"] is None
    assert fig["panels"]["left"] == "measured Combine DB supply"
    assert "arithmetic projection" in fig["panels"]["right"]
    assert fig["figure"] == "fig_before_after"
    assert fig["counts"] and len(fig["counts"]) == 42


def test_before_after_edges_come_from_config(rendered, tmp_path):
    cfg = _write_config(tmp_path)
    raw = yaml.safe_load(cfg.read_text())
    raw["turn_coverage"]["figures"]["before_after"]["angle_edges_deg"] = \
        [30.0, 45.0, 90.0, 180.0]
    cfg.write_text(yaml.safe_dump(raw, sort_keys=False))
    res = _run(cfg)
    assert res.returncode == 0, res.stderr
    csv_df = pd.read_csv(tmp_path / "outputs" / "figures" / "fig_before_after.csv")
    measured = csv_df[csv_df["panel"] == "measured"]
    assert len(measured) == 4 * 6
    assert set(measured["band"]) == {"30–45°", "45–90°", "90–180°", ">180°"}


def test_before_after_checks_can_fail(rendered):
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["ba_csv"])
    _check_ba_csv(good, turns)

    bad = good.copy()
    idx = bad.index[(bad["panel"] == "measured") & (bad["band"] == "30–45°")
                    & (bad["speed_band"] == "4–6")][0]
    bad.loc[idx, "count"] = 1  # that cell is genuinely empty
    with pytest.raises(AssertionError):
        _check_ba_csv(bad, turns)

    split = good.copy()
    split.loc[split.index[split["panel"] == "projected"], "count"] = 366
    with pytest.raises(AssertionError):
        _check_ba_csv(split, turns)

    empty = rendered["tmp"] / "empty_ba.png"
    empty.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty)


# ------------------------------------- figure 4: the drill schematic
#: The figure is a PROPOSAL: drawn from notes/drill-proposal.md section 2, not
#: from data. These tests pin the geometry to the config block and pin the
#: status stamp — they never treat this figure as a measurement.
DS_STAMP = "PROPOSED DRILL — not part of the task spec, awaiting sign-off"
DS_PREFIX = "figures.drill_schematic."


def _ds_block(cfg_path: Path) -> dict:
    raw = yaml.safe_load(cfg_path.read_text())
    return raw["turn_coverage"]["figures"]["drill_schematic"]


def _ds_rows(csv_df: pd.DataFrame) -> dict:
    return {row["element"]: row for _, row in csv_df.iterrows()}


def _check_schematic(csv_df: pd.DataFrame, block: dict) -> None:
    """Every drawn dimension must equal the configured (proposal) dimension."""
    assert set(csv_df["status"]) == {"PROPOSAL"}
    assert set(csv_df["status_stamp"]) == {DS_STAMP}
    rows = _ds_rows(csv_df)

    approach = rows["approach_line"]
    lo, hi = (float(x) for x in block["approach_length_yd"])
    drawn = float(approach["length_yd"])
    assert lo <= drawn <= hi, f"approach {drawn} yd is outside {lo}-{hi} yd"
    assert np.isclose(float(approach["x"]), -drawn)
    assert np.isclose(float(approach["y"]), 0.0)
    assert np.isclose(float(approach["y_end"]), 0.0)

    gate = float(block["gate_distance_yd"])
    angle = float(block["break_angle_deg"])
    for side in ("left", "right"):
        seg = rows[f"exit_{side}"]
        assert np.isclose(float(seg["length_yd"]), gate)
        assert np.isclose(abs(float(seg["angle_deg"])), angle)
        assert np.isclose(float(seg["x"]), 0.0) and np.isclose(float(seg["y"]), 0.0)
        mark = rows[f"gate_{side}"]
        assert np.isclose(np.hypot(float(mark["x"]), float(mark["y"])), gate)

    # both sides symmetric about the approach line
    left, right = rows["exit_left"], rows["exit_right"]
    assert np.isclose(float(left["x_end"]), float(right["x_end"]))
    assert np.isclose(float(left["y_end"]), -float(right["y_end"]))
    assert np.isclose(float(rows["gate_left"]["y"]), -float(rows["gate_right"]["y"]))

    # the plant is the break cone, at the end of the approach
    assert np.isclose(float(rows["plant"]["x"]), 0.0)
    assert np.isclose(float(rows["plant"]["y"]), 0.0)
    assert np.isclose(float(rows["cone_break"]["x"]), 0.0)

    # every printed number resolves to a config constant, and is printed
    ann = csv_df[csv_df["row_kind"] == "annotation"]
    for _, row in ann.iterrows():
        key = str(row["source_constant"])
        assert key.startswith(DS_PREFIX), f"untraceable annotation source {key}"
        want = block[key[len(DS_PREFIX):]]
        if not pd.isna(row["value_low"]):
            assert [float(row["value_low"]), float(row["value_high"])] == [
                float(x) for x in want]
        elif not pd.isna(row["value"]):
            assert np.isclose(float(row["value"]), float(want)), (
                f"{row['element']}: printed {row['value']} but config says {want}")
            assert f"{float(row['value']):g}" in str(row["text"])


def test_drill_schematic_png_exists_non_empty_and_wide(rendered):
    _check_png(rendered["ds_png"])


def test_drill_schematic_geometry_matches_config(rendered):
    csv_df = pd.read_csv(rendered["ds_csv"])
    _check_schematic(csv_df, _ds_block(rendered["cfg"]))
    rows = _ds_rows(csv_df)
    assert np.isclose(float(rows["exit_left"]["angle_deg"]), 40.0)
    assert np.isclose(float(rows["gate_left"]["length_yd"]), 5.0)
    assert 8.0 <= float(rows["approach_line"]["length_yd"]) <= 10.0


def test_drill_schematic_is_not_stamped_as_sample_data(rendered):
    raw = rendered["ds_csv"].read_text()
    assert "PLACEHOLDER_SAMPLE" not in raw
    assert "UNVALIDATED SAMPLE OUTPUT" not in raw
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"]["fig_drill_schematic"]
    assert fig["status"] == "PROPOSAL"
    assert fig["stamp"] == DS_STAMP
    assert fig["provenance_applied"] is False
    assert "deliberately NOT stamped" in fig["provenance_note"].lower() or \
        "Deliberately NOT stamped" in fig["provenance_note"]
    assert fig["spec"]["break_angle_deg"] == 40
    assert fig["spec"]["gate_distance_yd"] == 5
    assert fig["spec"]["approach_length_yd"] == [8, 10]
    assert fig["scale"]["to_scale"] is True
    assert fig["omitted_not_specified"], "omissions must be listed"
    assert any("Gate width" in t for t in fig["omitted_not_specified"])


def test_drill_schematic_badge_is_drawn(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    table, context = figures.drill_schematic_geometry(cfg)
    fig = figures.plot_drill_schematic(table, context, cfg)
    texts = [t.get_text() for t in fig.texts] + [t.get_text()
                                                for t in fig.axes[0].texts]
    assert any(DS_STAMP in t for t in texts), "the proposal badge is not drawn"
    assert any("not part of the task spec" in t for t in texts)
    import matplotlib.pyplot as plt
    plt.close(fig)


def test_drill_schematic_two_runs_are_byte_identical(rendered):
    first = hashlib.sha256(rendered["ds_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    second = hashlib.sha256(rendered["ds_png"].read_bytes()).hexdigest()
    assert first == second


def test_drill_schematic_numbers_are_all_traceable(rendered):
    """No number may appear in an annotation without a config source."""
    csv_df = pd.read_csv(rendered["ds_csv"])
    block = _ds_block(rendered["cfg"])
    _check_schematic(csv_df, block)
    texts = " ".join(str(t) for t in csv_df.loc[
        csv_df["row_kind"] == "annotation", "text"])
    # the figure-3 projection must NOT be repeated here
    assert "732" not in texts
    assert "97" not in texts


def test_drill_schematic_stage_06_still_renders_figures_1_to_3(rendered):
    keys = ["png", "drill_png", "ba_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k]
    assert len(pd.read_csv(rendered["csv"])) == 14
    assert len(pd.read_csv(rendered["drill_csv"])) == 11
    assert len(pd.read_csv(rendered["ba_csv"])) == 44
    man = json.loads(rendered["manifest"].read_text())
    assert set(man["figures"]) == {"fig_the_hole", "fig_drill_coverage",
                                   "fig_before_after", "fig_drill_schematic",
                                   "fig_all_positions",
                                   "fig_measurement_limits", "fig_gap_honest"}


def test_drill_schematic_checks_can_fail(rendered):
    csv_df = pd.read_csv(rendered["ds_csv"])
    block = _ds_block(rendered["cfg"])
    _check_schematic(csv_df, block)  # sanity: passes on the real drawing

    wrong_gate = csv_df.copy()
    idx = wrong_gate.index[wrong_gate["element"] == "exit_left"][0]
    wrong_gate.loc[idx, "length_yd"] = 4.0
    with pytest.raises(AssertionError):
        _check_schematic(wrong_gate, block)

    wrong_angle = csv_df.copy()
    idx = wrong_angle.index[wrong_angle["element"] == "exit_right"][0]
    wrong_angle.loc[idx, "angle_deg"] = -35.0
    with pytest.raises(AssertionError):
        _check_schematic(wrong_angle, block)

    asymmetric = csv_df.copy()
    idx = asymmetric.index[asymmetric["element"] == "gate_right"][0]
    asymmetric.loc[idx, "y"] = float(asymmetric.loc[idx, "y"]) * 0.9
    with pytest.raises(AssertionError):
        _check_schematic(asymmetric, block)

    invented = csv_df.copy()
    idx = invented.index[invented["row_kind"] == "annotation"][0]
    invented.loc[idx, "source_constant"] = "figures.not_a_constant"
    with pytest.raises(AssertionError):
        _check_schematic(invented, block)


# ------------------------------------- figure 5: the same hole across positions
AP_KEY = "fig_all_positions"


def _ap_cells(turns: pd.DataFrame, cfg_path: Path):
    from bdb27 import common, figures
    cfg = common.load_config(cfg_path)
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    positions = [str(p) for p in cfg.get("figures.all_positions.positions")]
    return figures.positions_table(turns, edges, overflow, positions), cfg


def _ap_expected(turns: pd.DataFrame, position: str, lo: float,
                 hi: float | None, setting: str) -> int:
    valid = turns["valid"].fillna(False).astype(bool)
    d = turns[valid & (turns["position_group"] == position)
              & (turns["setting"] == setting)]
    mask = d["abs_turn_angle"] >= lo
    if hi is not None:
        mask = mask & (d["abs_turn_angle"] < hi)
    return int(mask.sum())


def _check_ap_csv(csv_df: pd.DataFrame, turns: pd.DataFrame) -> None:
    """Every plotted share and count must reconcile to the turn table."""
    assert len(csv_df) == 5 * 7 * 2, "five positions x seven bands x two settings"
    assert list(csv_df["position"].unique()) == ALL_POSITIONS
    for position in ALL_POSITIONS:
        part = csv_df[csv_df["position"] == position]
        assert len(part) == 14
        valid = turns["valid"].fillna(False).astype(bool)
        totals = turns[valid & (turns["position_group"] == position)
                       ].groupby("setting").size().to_dict()
        for _, row in part.iterrows():
            hi = None if pd.isna(row["band_high_deg"]) else float(
                row["band_high_deg"])
            want = _ap_expected(turns, position, float(row["band_low_deg"]), hi,
                                row["setting"])
            assert int(row["count"]) == want, (
                f"{position} {row['band']} {row['setting']}: csv="
                f"{int(row['count'])} artefact={want}")
            assert int(row["position_setting_total"]) == totals[row["setting"]]
            assert np.isclose(float(row["share_within_setting"]),
                              want / totals[row["setting"]], atol=1e-12)
        assert int(part["count"].sum()) == sum(totals.values())


def test_all_positions_png_and_csv_exist(rendered):
    _check_png(rendered["ap_png"])
    assert rendered["ap_csv"].exists()


def test_all_positions_counts_reconcile_for_every_position(rendered):
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    csv_df = pd.read_csv(rendered["ap_csv"])
    _check_ap_csv(csv_df, turns)
    assert set(csv_df["position"]) == set(ALL_POSITIONS)


def test_all_positions_panels_share_one_scale(rendered):
    """Five panels, one axis: identical x and y limits, and bars in every one."""
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    (table, context), cfg = _ap_cells(turns, rendered["cfg"])
    plot = figures.plot_all_positions(table, context, cfg, turns=turns)
    fig = plot.draw()
    try:
        assert len(fig.axes) == 5, f"expected five panels, got {len(fig.axes)}"
        lims = {(tuple(np.round(ax.get_xlim(), 9)),
                 tuple(np.round(ax.get_ylim(), 9))) for ax in fig.axes}
        assert len(lims) == 1, f"panels do not share a scale: {lims}"
        (xlim, _), = lims
        assert xlim == (0.0, 70.0), f"shared x axis is {xlim}, expected 0-70%"
        for ax in fig.axes:
            bars = [c for c in ax.collections
                    if isinstance(c, PolyCollection)]
            assert sum(len(c.get_paths()) for c in bars) == 14, (
                "each panel must hold 14 bars (7 bands x 2 settings)")
        colours = {tuple(np.round(c, 2))
                   for c in fig.axes[0].collections[0].get_facecolors()}
        assert (0.0, 0.45, 0.7, 1.0) in colours  # accent at full opacity
        others = {tuple(np.round(c, 2))
                  for c in fig.axes[1].collections[0].get_facecolors()}
        assert all(c[3] == 0.6 for c in others), (
            f"non-DB panels must be drawn at 60% opacity, got {others}")
    finally:
        plt.close(fig)


def test_all_positions_marks_only_the_positions_the_spec_flags(rendered):
    """Exploratory marks come from TASK.md, not from the figure's taste."""
    import matplotlib.pyplot as plt

    from bdb27 import figures
    spec = (ROOT / "TASK.md").read_text()
    line = spec.splitlines()[62]
    assert "OL and DB are exploratory" in line, (
        "TASK.md line 63 no longer says OL and DB are exploratory — the "
        "figure's marks are pinned to that sentence")
    assert "OL and DB (exploratory)" in spec

    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    (table, context), cfg = _ap_cells(turns, rendered["cfg"])
    status = figures.all_positions_status(context, cfg)
    assert set(status) == {"DB", "OL"}, f"statuses drawn: {sorted(status)}"

    badges = figures.all_positions_badges(context, cfg)
    assert set(badges["position"].astype(str)) == {"DB", "OL"}

    plot = figures.plot_all_positions(table, context, cfg, turns=turns)
    fig = plot.draw()
    try:
        marked = {}
        for position, ax in zip(ALL_POSITIONS, fig.axes):
            marked[position] = [t.get_text() for t in ax.texts
                                if "EXPLORATORY" in t.get_text()]
        assert marked["DB"] and marked["OL"]
        assert not marked["WR"] and not marked["TE"] and not marked["DL"]
    finally:
        plt.close(fig)


def test_all_positions_counts_annotated_on_the_accent_panel_only(rendered):
    import matplotlib.pyplot as plt

    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    (table, context), cfg = _ap_cells(turns, rendered["cfg"])
    plot = figures.plot_all_positions(table, context, cfg, turns=turns)
    fig = plot.draw()
    try:
        numeric = {p: [t.get_text() for t in ax.texts
                       if t.get_text().replace(",", "").isdigit()]
                   for p, ax in zip(ALL_POSITIONS, fig.axes)}
        assert len(numeric["DB"]) == 14
        for position in ALL_POSITIONS[1:]:
            assert numeric[position] == [], (
                f"{position} panel must not carry count labels")
        db = table[table["position"] == "DB"]
        assert sorted(int(x.replace(",", "")) for x in numeric["DB"]) == sorted(
            int(n) for n in db["count"])
    finally:
        plt.close(fig)


def test_all_positions_positions_come_from_config(rendered, tmp_path):
    cfg = _write_config(tmp_path)
    raw = yaml.safe_load(cfg.read_text())
    raw["turn_coverage"]["figures"]["all_positions"]["positions"] = [
        "DL", "DB", "OL", "WR", "TE"]
    cfg.write_text(yaml.safe_dump(raw, sort_keys=False))
    res = _run(cfg)
    assert res.returncode == 0, res.stderr
    csv_df = pd.read_csv(tmp_path / "outputs" / "figures" / f"{AP_KEY}.csv")
    assert list(csv_df["position"].unique()) == ["DL", "DB", "OL", "WR", "TE"]


def test_all_positions_refuses_to_drop_a_flagged_position(rendered, tmp_path):
    """Dropping OL would erase its exploratory mark: the stage must refuse."""
    cfg = _write_config(tmp_path)
    raw = yaml.safe_load(cfg.read_text())
    raw["turn_coverage"]["figures"]["all_positions"]["positions"] = [
        "DB", "WR", "TE", "DL"]
    cfg.write_text(yaml.safe_dump(raw, sort_keys=False))
    res = _run(cfg)
    assert res.returncode != 0
    assert "exploratory" in res.stderr or "invisible" in res.stderr


def test_all_positions_two_runs_are_byte_identical(rendered):
    first = hashlib.sha256(rendered["ap_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    second = hashlib.sha256(rendered["ap_png"].read_bytes()).hexdigest()
    assert first == second


def test_all_positions_provenance_stamp(rendered):
    csv_df = pd.read_csv(rendered["ap_csv"])
    assert csv_df["stamp"].eq(STAMP).all()
    assert csv_df["provenance"].eq("PLACEHOLDER_SAMPLE").all()
    man = json.loads(rendered["manifest"].read_text())
    assert man["provenance"] == "PLACEHOLDER_SAMPLE"
    assert man["figures"][AP_KEY]["stamp"] == STAMP

    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    (table, context), cfg = _ap_cells(turns, rendered["cfg"])
    caption = figures.plot_all_positions(table, context, cfg, turns=turns
                                         ).labels.get("caption", "")
    assert STAMP in caption


def test_all_positions_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][AP_KEY]
    assert fig["figure"] == AP_KEY
    assert fig["positions"] == ALL_POSITIONS
    assert fig["emphasis_position"] == "DB"
    assert fig["shared_axis"]["identical_in_all_panels"] is True
    assert fig["shared_axis"]["max_pct"] == 70.0
    assert set(fig["spec_status"]["statuses"]) == {"DB", "OL"}
    assert "TASK.md:63" in fig["spec_status"]["source"]
    assert [b for b in fig["bands"]] == ["30–45°", "45–60°", "60–90°",
                                         "90–120°", "120–150°", "150–180°",
                                         ">180°"]
    assert len(fig["counts"]) == 70
    assert fig["count_labels"] == {"panel": "DB", "shown_for_other_panels": False}
    assert not any("rank" in k or "ratio" in k for k in fig), (
        "the manifest must not carry a ranking or ratio of positions")


def test_all_positions_caption_states_what_dl_shows(rendered):
    """DL is included, not excused: the caption states its panel, not 'fine'."""
    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    (table, context), cfg = _ap_cells(turns, rendered["cfg"])
    caption = figures.plot_all_positions(table, context, cfg, turns=turns
                                         ).labels.get("caption", "")
    assert "DL is included unchanged" in caption
    assert "speed story" in caption
    assert 'No panel is labelled "fine"' in caption
    assert "no ranking" in caption
    man = json.loads(rendered["manifest"].read_text())
    assert "fine" in man["figures"][AP_KEY]["claim"]


def test_all_positions_caption_names_the_turns_outside_the_panels(rendered):
    """The count is not a census: unclassified game turns are named."""
    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    (table, context), cfg = _ap_cells(turns, rendered["cfg"])
    caption = figures.plot_all_positions(table, context, cfg, turns=turns
                                         ).labels.get("caption", "")
    flat = " ".join(caption.split())
    assert (f"Excluded from these panels: {context['n_excluded']:,} valid game "
            "turns with position_group OTHER") in flat
    assert "not a census of all valid turns" in flat

    # Move seven DB game turns to OTHER: the caption's excluded count and the
    # plotted total must both follow, or the clause is decorative.
    tweaked = turns.copy()
    idx = tweaked.index[(tweaked["position_group"] == "DB")
                        & (tweaked["setting"] == "game")][:7]
    tweaked.loc[idx, "position_group"] = "OTHER"
    (t2, context2), cfg2 = _ap_cells(tweaked, rendered["cfg"])
    caption2 = figures.plot_all_positions(t2, context2, cfg2, turns=tweaked
                                          ).labels.get("caption", "")
    flat2 = " ".join(caption2.split())
    assert context2["n_excluded"] == context["n_excluded"] + 7
    assert (f"{context2['n_excluded']:,} valid game turns with position_group "
            "OTHER") in flat2
    assert context2["n_turns"] == context["n_turns"] - 7


def test_all_positions_leaves_figures_1_to_4_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    csv_before = {k: pd.read_csv(rendered[k]).shape
                  for k in ("csv", "drill_csv", "ba_csv", "ds_csv")}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 5 must not alter figures 1-4")
    for k, shape in csv_before.items():
        assert pd.read_csv(rendered[k]).shape == shape


def test_all_positions_checks_can_fail(rendered):
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["ap_csv"])
    _check_ap_csv(good, turns)

    bad = good.copy()
    idx = bad.index[(bad["position"] == "OL")
                    & (bad["band"] == "30–45°") & (bad["setting"] == "game")][0]
    bad.loc[idx, "count"] = int(bad.loc[idx, "count"]) + 1
    with pytest.raises(AssertionError):
        _check_ap_csv(bad, turns)

    dropped = good[good["position"] != "TE"].copy()
    with pytest.raises(AssertionError):
        _check_ap_csv(dropped, turns)

    empty = rendered["tmp"] / "empty_ap.png"
    empty.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty)


# --------------------------- figure 6: the measurement limits (slot e)
ML_KEY = "fig_measurement_limits"
ML_THRESHOLD_KEY = "figures.measurement_limits.reversal_zone_deg"


def _ml_build(rendered, threshold=None):
    """Rebuild figure 6 from the fixture artefacts (no re-run needed)."""
    import matplotlib.pyplot as plt

    from bdb27 import common, figures
    cfg_path = rendered["cfg"]
    if threshold is not None:
        raw = yaml.safe_load(cfg_path.read_text())
        raw["turn_coverage"]["figures"]["measurement_limits"][
            "reversal_zone_deg"] = threshold
        cfg_path = rendered["tmp"] / "config_ml_test.yaml"
        cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = common.load_config(cfg_path)
    vpath = rendered["tmp"] / "outputs" / "envelope" / "variance_components.csv"
    left, lctx = figures.variance_table(
        figures.load_variance_components(vpath))
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    right, rctx = figures.lateral_table(turns, cfg)
    fig = figures.build_measurement_limits(
        left, right, {"left": lctx, "right": rctx}, cfg)
    return fig, left, right, lctx, rctx, cfg, plt


def _ml_texts(fig) -> str:
    texts = [t.get_text() for t in fig.texts]
    for ax in fig.axes:
        texts.extend(t.get_text() for t in ax.texts)
        texts.append(ax.get_title())
        texts.append(ax.get_xlabel())
        texts.append(ax.get_ylabel())
    return "\n".join(texts)


def test_measurement_limits_png_exists_non_empty_and_wide(rendered):
    _check_png(rendered["ml_png"])


def test_measurement_limits_boundary_count_on_image_equals_csv(rendered):
    fig, left, right, lctx, rctx, cfg, plt = _ml_build(rendered)
    try:
        csv_df = pd.read_csv(rendered["ml_csv"])
        left_csv = csv_df[csv_df["panel"] == "left_variance"]
        n_csv = int((left_csv["tau2_status"] == "AT_BOUNDARY").sum())
        m_csv = int(len(left_csv))
        assert (n_csv, m_csv) == (43, 51), (n_csv, m_csv)
        assert n_csv == lctx["n_at_boundary"]
        assert m_csv == lctx["n_components"]
        # The count printed on the image must be the CSV count.
        assert f"{n_csv} of {m_csv} components" in _ml_texts(fig)
        # Every boundary component is drawn, not dropped.
        csv_pairs = set(zip(left_csv["setting"], left_csv["position_group"],
                           left_csv["measure"]))
        drawn_pairs = set(zip(left["setting"], left["position_group"],
                              left["measure"]))
        assert csv_pairs == drawn_pairs
    finally:
        plt.close(fig)


def _check_ml_left(csv_df: pd.DataFrame, src: pd.DataFrame) -> None:
    left = csv_df[csv_df["panel"] == "left_variance"].copy()
    assert len(left) == len(src) == 51
    merged = left.merge(src, on=["setting", "position_group", "measure"],
                        suffixes=("_fig", "_src"), validate="one_to_one")
    assert len(merged) == 51
    for col in ("tau2", "tau2_p05", "tau2_p95"):
        assert np.allclose(merged[f"{col}_fig"], merged[f"{col}_src"],
                           atol=1e-12, rtol=0)
    assert (merged["tau2_status_fig"] == merged["tau2_status_src"]).all()


def test_measurement_limits_left_reconciles_to_variance_file(rendered):
    src = pd.read_csv(rendered["tmp"] / "outputs" / "envelope"
                      / "variance_components.csv")
    _check_ml_left(pd.read_csv(rendered["ml_csv"]), src)


def test_measurement_limits_right_reconciles_to_turn_table(rendered):
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    valid = turns[turns["valid"].fillna(False).astype(bool)]
    right = pd.read_csv(rendered["ml_csv"])
    right = right[right["panel"] == "right_lateral_accel"]
    edges = [30.0, 45.0, 60.0, 90.0, 120.0, 150.0, 180.0]
    total = 0
    for _, row in right.iterrows():
        lo = float(row["angle_low_deg"])
        hi = row["angle_high_deg"]
        mask = (valid["abs_turn_angle"] >= lo)
        if pd.isna(hi):
            mask &= valid["abs_turn_angle"] >= edges[-1]
        else:
            mask &= valid["abs_turn_angle"] < float(hi)
        part = valid.loc[mask, "peak_lateral_accel_yd_s2"].dropna()
        assert int(row["n_turns"]) == len(part)
        assert np.isclose(float(row["median_lateral_accel_yd_s2"]),
                          float(part.median()), atol=1e-9)
        assert np.isclose(float(row["p95_lateral_accel_yd_s2"]),
                          float(part.quantile(0.95)), atol=1e-9)
        total += int(row["n_turns"])
    assert total == len(valid)


def test_measurement_limits_reversal_threshold_comes_from_config(rendered):
    # Threshold 120 is a bin edge, so bands 120-150, 150-180 and >180 are in.
    fig, left, right, lctx, rctx, cfg, plt = _ml_build(rendered, threshold=120.0)
    try:
        assert rctx["reversal"]["threshold_deg"] == 120.0
        lit = right[right["in_reversal_zone"]]
        assert list(lit["angle_band"]) == ["120–150°", "150–180°", ">180°"]
        assert "≥120°" in _ml_texts(fig)
        assert ML_THRESHOLD_KEY in _ml_texts(fig)
    finally:
        plt.close(fig)

    # A threshold that is not a bin edge refuses instead of shading a fraction.
    from bdb27 import figures
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    from bdb27 import common
    raw = yaml.safe_load(rendered["cfg"].read_text())
    raw["turn_coverage"]["figures"]["measurement_limits"][
        "reversal_zone_deg"] = 140.0
    cfg_path = rendered["tmp"] / "config_ml_bad.yaml"
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(common.StageRefused):
        figures.lateral_table(turns, common.load_config(cfg_path))


def test_measurement_limits_two_runs_are_byte_identical(rendered):
    first = hashlib.sha256(rendered["ml_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    second = hashlib.sha256(rendered["ml_png"].read_bytes()).hexdigest()
    assert first == second


def test_measurement_limits_leaves_figures_1_to_5_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 6 must not alter figures 1-5")


def test_measurement_limits_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][ML_KEY]
    assert fig["figure"] == ML_KEY
    assert fig["boundary"]["n_at_boundary"] == 43
    assert fig["boundary"]["n_components"] == 51
    assert "empty" in fig["boundary"]["encoding"].lower()
    assert fig["reversal_zone"]["threshold_deg"] == 150.0
    assert fig["reversal_zone"]["config_key"] == ML_THRESHOLD_KEY
    assert fig["reversal_zone"]["greyed_and_struck_through"] is True
    assert len(fig["components"]) == 51
    assert len(fig["lateral_bands"]) == 7
    assert fig["detector_evidence"]["angle_mae_deg"] == 3.1
    assert fig["detector_evidence"]["precision"] == 0.915
    assert fig["detector_evidence"]["recall"] == 0.931
    assert fig["detector_evidence"]["sources"] == [
        "notes/phase2-turns.md", "outputs/audit/gate.json"]
    # No per-player artefact may appear anywhere in this entry.
    flat = json.dumps(fig).lower()
    for banned in ("player_name", "nfl_id", "ranking of", "p_value"):
        assert banned not in flat


def test_measurement_limits_caption_carries_the_counterweight(rendered):
    fig, left, right, lctx, rctx, cfg, plt = _ml_build(rendered)
    try:
        text = _ml_texts(fig)
        assert "3.1° MAE" in text
        assert "precision 0.915" in text
        assert "recall 0.931" in text
        assert "not detector noise" in text
        assert "planted 45° turns" in text
        assert "apex, exit, regain" in text
        assert "not an error" in text
    finally:
        plt.close(fig)


def test_measurement_limits_detector_numbers_have_a_stored_source():
    """The caption's accuracy numbers must still exist in the repo's artefacts."""
    import re
    notes = (ROOT / "notes" / "phase2-turns.md").read_text()
    gate = (ROOT / "outputs" / "audit" / "gate.json").read_text()
    assert re.search(r"\*\*3\.1°\*\*", notes), "3.1° MAE source line is gone"
    for token in ("0.915", "0.931"):
        assert token in notes, f"{token} missing from {notes[:20]}..."
        assert token in gate, f"{token} missing from the audit gate"


def test_measurement_limits_stage_06_renders_all_seven(rendered):
    man = json.loads(rendered["manifest"].read_text())
    assert set(man["figures"]) == {
        "fig_the_hole", "fig_drill_coverage", "fig_before_after",
        "fig_drill_schematic", "fig_all_positions", "fig_measurement_limits",
        "fig_gap_honest"}
    out = rendered["tmp"] / "outputs" / "figures"
    for key in man["figures"]:
        assert (out / f"{key}.png").exists(), f"{key}.png missing"
        assert (out / f"{key}.csv").exists(), f"{key}.csv missing"


def test_measurement_limits_checks_can_fail(rendered):
    fig, left, right, lctx, rctx, cfg, plt = _ml_build(rendered)
    try:
        assert f"{lctx['n_at_boundary']} of {lctx['n_components']} components" \
            in _ml_texts(fig)
    finally:
        plt.close(fig)

    src = pd.read_csv(rendered["tmp"] / "outputs" / "envelope"
                      / "variance_components.csv")
    good = pd.read_csv(rendered["ml_csv"])
    _check_ml_left(good, src)  # sanity: the checker passes on the real file

    bad = good.copy()
    left_idx = bad.index[bad["panel"] == "left_variance"][0]
    bad.loc[left_idx, "tau2"] = 9.99
    with pytest.raises(AssertionError):
        _check_ml_left(bad, src)

    mislabelled = good.copy()
    mislabelled.loc[left_idx, "tau2_status"] = "INTERIOR"
    with pytest.raises(AssertionError):
        _check_ml_left(mislabelled, src)

    dropped = pd.concat([
        good[good["panel"] != "left_variance"],
        good[(good["panel"] == "left_variance")
             & (good["tau2_status"] != "AT_BOUNDARY")],
    ], ignore_index=True)
    with pytest.raises(AssertionError):
        _check_ml_left(dropped, src)

    empty = rendered["tmp"] / "empty_ml.png"
    empty.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty)


# ------------------- figure 7: how big is the gap, really? (the exposure attack)
GH_KEY = "fig_gap_honest"


def _gh_subset(turns: pd.DataFrame, cfg, universe: str) -> pd.DataFrame:
    """The turn table filtered exactly as one configured universe defines it."""
    spec = cfg.get(f"figures.gap_honest.universes.{universe}")
    valid = turns["valid"].fillna(False).astype(bool)
    position = str(cfg.get("figures.gap_honest.position_group"))
    df = turns[valid & (turns["position_group"] == position)].copy()
    floor = spec.get("exclude_angles_at_or_above_deg")
    if floor is not None:
        df = df[df["abs_turn_angle"] < float(floor)]
    snaps = [str(s) for s in (spec.get("game_snap_types") or [])]
    if snaps:
        game = df["setting"].astype(str) == "game"
        df = df[~game | df["snap_type"].astype(str).isin(snaps)]
    return df


def _gh_expected(turns: pd.DataFrame, cfg, universe: str,
                 setting: str) -> tuple[int, int, int, int]:
    """(band count, universe turns, players, player-appearances)."""
    df = _gh_subset(turns, cfg, universe)
    part = df[df["setting"].astype(str) == setting]
    lo, hi = [float(x) for x in cfg.get("figures.gap_honest.band_deg")]
    n_band = int(((part["abs_turn_angle"] >= lo)
                  & (part["abs_turn_angle"] < hi)).sum())
    if setting == "game":
        appearances = int(part.groupby(["nfl_id", "game_id"]).ngroups)
    else:
        appearances = int(part["nfl_id"].nunique())
    return n_band, int(len(part)), int(part["nfl_id"].nunique()), appearances


def _check_gh_csv(csv_df: pd.DataFrame, turns: pd.DataFrame, cfg) -> None:
    """Every count, denominator and ratio reconciles to the turn table."""
    from bdb27 import figures

    metrics = {panel: metric for panel, _, metric in figures.GAP_PANELS}
    assert set(csv_df["universe"]) == {"all_valid", "stage04"}
    assert csv_df.loc[csv_df["is_headline"], "universe"].unique().tolist() == [
        "all_valid"], "exactly one universe is the headline"
    for universe in ("all_valid", "stage04"):
        part = csv_df[csv_df["universe"] == universe]
        for panel, metric in metrics.items():
            rows = part[part["panel"] == panel]
            values = {}
            for setting in ("combine", "game"):
                row = rows[rows["setting"] == setting]
                assert len(row) == 1, f"{universe} {panel} {setting}"
                row = row.iloc[0]
                n_band, n_turns, _, n_app = _gh_expected(
                    turns, cfg, universe, setting)
                assert int(row["count"]) == n_band
                assert int(row["n_players"]) > 0
                assert int(row["n_appearances"]) == n_app
                denominator = n_turns if metric == "share_pct" else n_app
                assert int(row["denominator"]) == denominator, (
                    f"{universe} {panel} {setting} denominator")
                expected = {"count": float(n_band),
                            "share_pct": 100.0 * n_band / n_turns,
                            "per_appearance": n_band / n_app}[metric]
                assert np.isclose(float(row["value"]), expected, atol=1e-12)
                assert str(row["printed_value"]) == figures._gap_value_text(
                    panel, expected)
                values[setting] = row
            ratio = rows[rows["setting"] == "ratio"]
            assert len(ratio) == 1
            ratio = ratio.iloc[0]
            want = float(values["game"]["value"]) / float(
                values["combine"]["value"])
            assert np.isclose(float(ratio["ratio"]), want, atol=1e-12)
            assert np.isclose(float(ratio["value"]), want, atol=1e-12)
            assert np.isclose(float(ratio["ratio_numerator"]),
                              float(values["game"]["value"]), atol=1e-12)
            assert np.isclose(float(ratio["ratio_denominator"]),
                              float(values["combine"]["value"]), atol=1e-12)
            expression = str(ratio["ratio_expression"])
            assert str(values["game"]["printed_value"]) in expression
            assert str(values["combine"]["printed_value"]) in expression


def test_gap_honest_png_and_csv_exist(rendered):
    _check_png(rendered["gh_png"])
    assert rendered["gh_csv"].exists()
    csv_df = pd.read_csv(rendered["gh_csv"])
    assert len(csv_df) == 2 * 3 * 3, "2 universes x 3 panels x 3 rows"


def test_gap_honest_counts_reconcile_for_both_universes(rendered):
    import yaml as _yaml
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    _check_gh_csv(pd.read_csv(rendered["gh_csv"]), turns, cfg)
    raw = _yaml.safe_load(rendered["cfg"].read_text())
    assert raw["turn_coverage"]["figures"]["gap_honest"]["headline_universe"] \
        == "all_valid"


def test_gap_honest_ratios_equal_printed_arithmetic(rendered):
    """Each printed expression contains its own two numbers and its ratio."""
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    csv_df = pd.read_csv(rendered["gh_csv"])
    for _, row in csv_df[csv_df["setting"] == "ratio"].iterrows():
        part = csv_df[(csv_df["universe"] == row["universe"])
                      & (csv_df["panel"] == row["panel"])]
        combine = part[part["setting"] == "combine"].iloc[0]
        game = part[part["setting"] == "game"].iloc[0]
        want = float(game["value"]) / float(combine["value"])
        assert f"{want:.2f}×" in str(row["ratio_expression"]) or \
            f"{want:.1f}×" in str(row["ratio_expression"])
        assert str(combine["printed_value"]) in str(row["ratio_expression"])
        assert str(game["printed_value"]) in str(row["ratio_expression"])


def test_gap_honest_never_mixes_universes_in_a_panel(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.gap_universe_table(turns, cfg)
    frame, ratio_rows = figures.gap_plot_frame(table, context)
    assert set(frame["universe"]) == {context["headline"]}
    assert set(ratio_rows["universe"]) == {context["headline"]}
    # Each sensitivity ratio is computed inside its own universe's two rows.
    for universe in context["universes"]:
        part = table[(table["universe"] == universe)
                     & (table["setting"] != "ratio")]
        for panel in part["panel"].astype(str).unique():
            sub = part[part["panel"].astype(str) == panel]
            vals = dict(zip(sub["setting"], sub["value"]))
            ratio = table[(table["universe"] == universe)
                          & (table["panel"].astype(str) == panel)
                          & (table["setting"] == "ratio")].iloc[0]
            assert np.isclose(float(ratio["ratio"]),
                              float(vals["game"]) / float(vals["combine"]))


def test_gap_honest_png_is_wide_and_two_runs_are_identical(rendered):
    _check_png(rendered["gh_png"])
    first = hashlib.sha256(rendered["gh_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    second = hashlib.sha256(rendered["gh_png"].read_bytes()).hexdigest()
    assert first == second


def test_gap_honest_leaves_figures_1_to_6_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 7 must not alter figures 1-6")


def test_gap_honest_caption_states_the_headline_and_the_sensitivity(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.gap_universe_table(turns, cfg)
    plot = figures.plot_gap_honest(table, context, cfg)
    text = " ".join((plot.labels.get("title", "") + " "
                     + plot.labels.get("subtitle", "") + " "
                     + plot.labels.get("caption", "")).split())
    assert "Headline universe:" in text
    assert "All valid DB turns" in text
    assert "SENSITIVITY" in text
    assert "COVERAGE snaps only" in text
    assert "Never mixed with the headline panels" in text
    assert "exposure-confounded" in text
    frame, _ = figures.gap_plot_frame(table, context)
    strips = " ".join(str(s) for s in frame["strip"].unique())
    for panel, _, metric in figures.GAP_PANELS:
        expression = figures._gap_ratio_text(
            panel, context["universes"]["all_valid"]["values"]["game"][metric],
            context["universes"]["all_valid"]["values"]["combine"][metric])
        assert expression.replace(" ", "") in strips.replace(" ", "")


def test_gap_honest_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][GH_KEY]
    assert fig["figure"] == GH_KEY
    assert fig["headline_universe"] == "all_valid"
    assert set(fig["universes"]) == {"all_valid", "stage04"}
    assert fig["universes"]["all_valid"]["is_headline"] is True
    assert fig["universes"]["stage04"]["is_headline"] is False
    assert len(fig["panels"]) == 3
    assert {p["panel"] for p in fig["panels"]} == {
        "a_raw_counts", "b_share", "c_per_appearance"}
    assert fig["stamp"] == STAMP
    assert fig["png"].endswith("fig_gap_honest.png")


def test_gap_honest_refuses_a_headline_not_in_the_universes(rendered):
    from bdb27 import common, figures
    raw = yaml.safe_load(rendered["cfg"].read_text())
    raw["turn_coverage"]["figures"]["gap_honest"][
        "headline_universe"] = "not_a_universe"
    bad = rendered["tmp"] / "config_gh_bad.yaml"
    bad.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = common.load_config(bad)
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    with pytest.raises(common.StageRefused):
        figures.gap_universe_table(turns, cfg)


def test_gap_honest_checks_can_fail(rendered):
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["gh_csv"])
    _check_gh_csv(good, turns, cfg)  # sanity: passes on the real file

    bad = good.copy()
    idx = bad.index[(bad["universe"] == "all_valid")
                    & (bad["panel"] == "b_share")
                    & (bad["setting"] == "game")][0]
    bad.loc[idx, "count"] = int(bad.loc[idx, "count"]) + 1
    with pytest.raises(AssertionError):
        _check_gh_csv(bad, turns, cfg)

    mixed = good.copy()
    idx = mixed.index[(mixed["universe"] == "stage04")
                      & (mixed["panel"] == "a_raw_counts")
                      & (mixed["setting"] == "ratio")][0]
    mixed.loc[idx, "ratio"] = float(mixed.loc[idx, "ratio"]) + 0.5
    with pytest.raises(AssertionError):
        _check_gh_csv(mixed, turns, cfg)
