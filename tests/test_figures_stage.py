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

#: Figure 9 fixture: the 98 THREE_CONE_DRILL rows are reassigned to these 24
#: players, and a combine_results.csv carrying one published time each is
#: written beside the fixture config. Counts sum to 98 and span 1-11 turns, so
#: the per-player reconcile and the feasibility gate are both exercised on real
#: structure rather than on one turn each. Times include near-identical pairs.
#: The ids MUST be existing fixture players: figure 3's projection counts the
#: distinct Combine DBs, and inventing 24 new ones would silently change it from
#: 122 x 6 reps to 146 x 6 reps. Reusing 24 of the 122 keeps every other figure
#: exactly as it was.
STOPWATCH_IDS = list(range(1001, 1025))
STOPWATCH_COUNTS = [11, 8, 8, 6, 6, 5, 5, 5, 4, 4, 4, 4, 4, 4, 3, 3,
                    2, 2, 2, 2, 2, 2, 1, 1]
STOPWATCH_TIMES = [6.62, 6.63, 6.71, 6.72, 6.75, 6.81, 6.82, 6.83,
                   6.88, 6.88, 6.90, 6.99, 7.00, 7.01, 7.05, 7.06,
                   7.08, 7.09, 7.10, 7.12, 7.12, 7.20, 7.26, 7.30]


def _assign_stopwatch_ids(frame: pd.DataFrame) -> None:
    """Give every synthetic three-cone turn a player who has a published time."""
    mask = frame["drill_name"].eq("THREE_CONE_DRILL")
    assert int(mask.sum()) == sum(STOPWATCH_COUNTS)
    labels: list[int] = []
    for player_id, count in zip(STOPWATCH_IDS, STOPWATCH_COUNTS):
        labels.extend([player_id] * count)
    frame.loc[mask, "nfl_id"] = labels


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
                # Figure 8 needs a retention per turn. Deterministic and
                # monotone enough to give every drawn band a real spread; the
                # figure-8 tests recompute the quartiles from this column.
                retention = 0.92 + 0.11 * (int(lo) // 10 % 5) + 0.02 * (j % 7)
                apex_retention = max(
                    0.05, 1.02 - 0.0055 * (angle - 30.0) + 0.02 * (j % 5) / 4.0)
                # Figure 10 needs the SIGNED angle and, for game rows, the
                # segment's frame range plus a play id, so the orientation
                # check can find the two ends of each turn in the fixture
                # tracking file. Signs alternate deterministically; no existing
                # figure reads `turn_angle`.
                signed = float(angle) if (j % 2 == 0) else -float(angle)
                # Figure 13 needs a smoother range per turn. The pattern is
                # deterministic and produces both a few single-smoother rows
                # (no spread) and widths beyond the drawing cap, so the cap
                # and crossing flags are exercised on the real code path.
                n_sm = (1 if (setting == "game" and j % 37 == 5)
                        else 2 + ((j * 7 + int(lo)) % 5))
                spread = (0.0 if n_sm == 1
                          else 1.0 + ((j * 11 + int(lo)) % 19))
                if n_sm > 2 and j % 13 == 0:
                    spread += 30.0
                smoother_min = max(0.0, float(angle) - 0.4 * spread)
                smoother_max = float(angle) + 0.6 * spread
                if setting == "combine":
                    play_id, frame_start, frame_end = None, None, None
                else:
                    play_id = 500000 + game_i
                    frame_start, frame_end = 0, 9
                rows.append({
                    "setting": setting,
                    "abs_turn_angle": float(angle),
                    "turn_angle": signed,
                    "position_group": "DB",
                    "nfl_id": nfl[setting][j % len(nfl[setting])],
                    "game_id": game_id,
                    "play_id": play_id,
                    "frame_start": frame_start,
                    "frame_end": frame_end,
                    "snap_type": snap,
                    "drill_name": drill,
                    "entry_speed": float(entry),
                    "speed_retention": float(retention),
                    "apex_retention": float(apex_retention),
                    # Figure 6 needs a real acceleration per turn. Monotone in
                    # angle with a deterministic ripple; assertions recompute
                    # the medians and p95 from this written table.
                    "peak_lateral_accel_yd_s2": float(
                        4.2 + 0.07 * angle + 0.9 * ((j * 13 + int(lo)) % 7) / 7.0),
                    "event_id": f"{setting[0]}{lo:g}_{j}",
                    "attempt": 1.0,
                    "n_smoothers_matched": int(n_sm),
                    "abs_turn_angle_smoother_min": smoother_min,
                    "abs_turn_angle_smoother_max": smoother_max,
                    "valid": True,
                })
    assert k == sum(DRILL_TOTALS.values())
    frame = pd.DataFrame(rows)
    _assign_stopwatch_ids(frame)
    return frame


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
                        "speed_retention": float(1.02 + 0.03 * band_index
                                                 + 0.01 * (j % 5)),
                        "apex_retention": float(
                            max(0.05, 1.0 - 0.006 * (angle - 30.0)
                                + 0.015 * (j % 4) / 3.0)),
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


def _write_game_tracking(raw: Path, turns: pd.DataFrame) -> None:
    """A tracking sample covering the fixture's DB *game* turns only.

    Ten frames per turn, ``s`` = 5 yd/s throughout, body orientation ``o``
    equal to the direction of travel, and ``dir`` swinging by exactly the row's
    ``turn_angle`` — so the orientation check has something real to agree (and,
    when flipped, disagree) with.
    """
    game = turns[(turns["setting"] == "game")
                 & (turns["position_group"] == "DB")
                 & turns["turn_angle"].notna()]
    rows = []
    for row in game.itertuples(index=False):
        start = 90.0
        end = (start + float(row.turn_angle)) % 360.0
        for k in range(10):
            frac = k / 9.0
            direction = (start + (end - start) * frac) % 360.0
            rows.append({
                "game_id": int(row.game_id),
                "play_id": int(row.play_id),
                "nfl_id": int(row.nfl_id),
                "time": f"2023-09-10 18:00:{k // 10:02d}.{k % 10 * 100:03d}",
                "x": 40.0 + 0.4 * k,
                "y": 25.0,
                "s": 5.0,
                "a": 0.0,
                "dis": 0.1 * k,
                "o": direction,
                "dir": direction,
                "event": "NA",
            })
    pd.DataFrame(rows).to_csv(raw / "game_tracking_2023_sample.csv", index=False)


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
    turns_frame = pd.concat([_turns_frame(), _extra_positions_frame()],
                            ignore_index=True)
    turns_frame.to_parquet(tmp / "outputs" / "turns" / "turns.parquet",
                           index=False)
    raw = tmp / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    _write_game_tracking(raw, turns_frame)
    pd.DataFrame({"nfl_id": STOPWATCH_IDS, "three_cone": STOPWATCH_TIMES}
                 ).to_csv(raw / "combine_results.csv", index=False)
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
            "tc_png": out / "fig_turn_cost.png",
            "tc_csv": out / "fig_turn_cost.csv",
            "sw_png": out / "fig_stopwatch.png",
            "sw_csv": out / "fig_stopwatch.csv",
            "bh_png": out / "fig_both_hips.png",
            "bh_csv": out / "fig_both_hips.csv",
            "ss_png": out / "fig_shallow_sources.png",
            "ss_csv": out / "fig_shallow_sources.csv",
            "lg_png": out / "fig_ledger.png",
            "lg_csv": out / "fig_ledger.csv",
            "bd_png": out / "fig_bars_of_doubt.png",
            "bd_csv": out / "fig_bars_of_doubt.csv",
            "sc_png": out / "fig_supply_composition.png",
            "sc_csv": out / "fig_supply_composition.csv",
            "bnd_png": out / "fig_band_only.png",
            "bnd_csv": out / "fig_band_only.csv",
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
                                   "fig_measurement_limits", "fig_gap_honest",
                                   "fig_turn_cost", "fig_stopwatch",
                                   "fig_both_hips", "fig_shallow_sources",
                                   "fig_ledger", "fig_bars_of_doubt",
                                   "fig_supply_composition", "fig_band_only"}


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


def test_stage_06_manifest_lists_every_figure(rendered):
    man = json.loads(rendered["manifest"].read_text())
    assert set(man["figures"]) == {
        "fig_the_hole", "fig_drill_coverage", "fig_before_after",
        "fig_drill_schematic", "fig_all_positions", "fig_measurement_limits",
        "fig_gap_honest", "fig_turn_cost", "fig_stopwatch", "fig_both_hips",
        "fig_shallow_sources", "fig_ledger", "fig_bars_of_doubt",
        "fig_supply_composition", "fig_band_only"}
    assert len(man["figures"]) == 15
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


# ------------- figure 8: what a turn costs, and who pays for it
TC_KEY = "fig_turn_cost"


def _tc_expected(turns: pd.DataFrame, cfg) -> pd.DataFrame:
    """Recompute every band's n, floor accounting and quartiles from scratch.

    Deliberately independent of ``figures.turn_cost_table``: the banding is
    redone here with plain masks, and the quartiles with numpy's linear
    interpolation, so a bug in the module cannot agree with itself.
    """
    edges = [float(x) for x in cfg.get("figures.hole.bands.turn_angle_deg")]
    overflow = str(cfg.get("figures.hole.bands.overflow_label"))
    from bdb27 import figures as _fig
    bands = _fig.band_labels(edges, overflow)
    position = str(cfg.get("figures.turn_cost.position_group"))
    min_n = int(cfg.get("figures.turn_cost.min_n_for_median"))
    column = str(cfg.get("figures.turn_cost.retention_column"))
    floors = {s: float(cfg.get(cfg.get(
        f"figures.turn_cost.entry_speed_floor_keys.{s}")))
        for s in ("combine", "game")}

    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)].copy()
    df["setting"] = df["setting"].astype(str)
    df["abs_turn_angle"] = pd.to_numeric(df["abs_turn_angle"], errors="coerce")
    df["entry_speed"] = pd.to_numeric(df["entry_speed"], errors="coerce")
    df[column] = pd.to_numeric(df[column], errors="coerce")
    totals = df.groupby("setting").size().to_dict()

    rows = []
    for i, band in enumerate(bands):
        if i < len(bands) - 1:
            mask = ((df["abs_turn_angle"] >= edges[i])
                    & (df["abs_turn_angle"] < edges[i + 1]))
        else:
            mask = df["abs_turn_angle"] >= edges[-1]
        for setting in ("combine", "game"):
            part = df[mask & (df["setting"] == setting)]
            floor = floors[setting]
            below = int((part["entry_speed"].isna()
                         | (part["entry_speed"] <= floor)).sum())
            usable = part[part["entry_speed"].notna()
                          & (part["entry_speed"] > floor)
                          & part[column].notna()][column]
            n_ret = int(len(usable))
            rows.append({
                "band_index": i + 1,
                "band": band,
                "is_reversal_class": i == len(bands) - 1,
                "setting": setting,
                "count": int(len(part)),
                "setting_total": int(totals[setting]),
                "share_within_setting": int(len(part)) / int(totals[setting]),
                "entry_speed_floor_yd_s": floor,
                "n_below_speed_floor": below,
                "n_apex_retention": n_ret,
                "q1_apex_retention": (float(np.percentile(usable, 25))
                                      if n_ret else np.nan),
                "median_apex_retention": (float(np.percentile(usable, 50))
                                          if n_ret else np.nan),
                "q3_apex_retention": (float(np.percentile(usable, 75))
                                      if n_ret else np.nan),
            })
    return pd.DataFrame(rows)


def _check_tc_csv(csv_df: pd.DataFrame, turns: pd.DataFrame, cfg) -> None:
    """Counts reconcile to the turn table; quartiles recompute; flags follow."""
    expected = _tc_expected(turns, cfg)
    min_n = int(cfg.get("figures.turn_cost.min_n_for_median"))
    assert len(csv_df) == len(expected) == 14, "7 bands x 2 settings"
    for _, row in csv_df.iterrows():
        match = expected[(expected["band"] == row["band"])
                         & (expected["setting"] == row["setting"])]
        assert len(match) == 1, f"unexpected row {row['band']} {row['setting']}"
        match = match.iloc[0]
        assert int(row["count"]) == int(match["count"]), (
            f"{row['band']} {row['setting']} count")
        assert int(row["setting_total"]) == int(match["setting_total"])
        assert int(row["n_below_speed_floor"]) == int(
            match["n_below_speed_floor"])
        assert int(row["n_apex_retention"]) == int(
            match["n_apex_retention"])
        assert np.isclose(float(row["share_within_setting"]),
                          float(match["share_within_setting"]), atol=1e-12)
        assert np.isclose(float(row["entry_speed_floor_yd_s"]),
                          float(match["entry_speed_floor_yd_s"]), atol=1e-12)
        if match["is_reversal_class"]:
            assert bool(row["is_reversal_class"]) is True
            assert pd.isna(row["median_apex_retention"]), (
                "the reversal class must carry no retention value")
            assert bool(row["insufficient"]) is True
            assert bool(row["drawn_in_panel_a"]) is False
            assert "reversal" in str(row["insufficient_reason"])
            continue
        assert bool(row["is_reversal_class"]) is False
        for col in ("q1_apex_retention", "median_apex_retention",
                    "q3_apex_retention"):
            assert np.isclose(float(row[col]), float(match[col]), atol=1e-9), (
                f"{row['band']} {row['setting']} {col} does not recompute")
        assert float(row["q1_apex_retention"]) \
            <= float(row["median_apex_retention"]) \
            <= float(row["q3_apex_retention"]), "quartiles are out of order"
        assert np.isclose(float(row["iqr_apex_retention"]),
                          float(match["q3_apex_retention"])
                          - float(match["q1_apex_retention"]), atol=1e-9)
        assert bool(row["insufficient"]) == bool(
            int(row["n_apex_retention"]) < min_n), (
                "insufficient flag ignores min_n")
        assert bool(row["drawn_in_panel_a"]) == bool(
            int(row["n_apex_retention"]) >= min_n)
        reason = ("" if pd.isna(row["insufficient_reason"])
                  else str(row["insufficient_reason"]))
        if row["insufficient"]:
            assert "insufficient" in reason.lower()
        else:
            assert reason == ""
    for setting in ("combine", "game"):
        part = csv_df[csv_df["setting"] == setting]
        assert np.isclose(float(part["share_within_setting"].sum()), 1.0,
                          atol=1e-12), "shares must sum to 100% of own turns"
        assert int(part["count"].sum()) == int(part["setting_total"].iloc[0])


def _tc_build(rendered, cfg_path=None):
    """Rebuild figure 8 from the fixture artefacts (no re-run needed)."""
    import matplotlib.pyplot as plt

    from bdb27 import common, figures
    cfg = common.load_config(cfg_path or rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.turn_cost_table(turns, cfg)
    fig = figures.plot_turn_cost(table, context, cfg)
    return fig, table, context, turns, cfg, plt


def test_turn_cost_png_and_csv_exist(rendered):
    _check_png(rendered["tc_png"])
    assert rendered["tc_csv"].exists()
    with Image.open(rendered["tc_png"]) as im:
        assert im.size[1] > 0


def test_turn_cost_counts_and_quartiles_reconcile(rendered):
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    _check_tc_csv(pd.read_csv(rendered["tc_csv"]), turns, cfg)


def test_turn_cost_floor_is_the_detector_heading_floor(rendered):
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    csv_df = pd.read_csv(rendered["tc_csv"])
    for setting, key in (("combine", "detector.speed_floor_combine"),
                         ("game", "detector.speed_floor_game")):
        rows = csv_df[csv_df["setting"] == setting]
        assert set(rows["entry_speed_floor_key"]) == {key}
        assert set(rows["entry_speed_floor_yd_s"]) == {float(cfg.get(key))}


def test_turn_cost_insufficient_band_is_drawn_as_insufficient(rendered):
    """A band below min_n is labelled, not plotted as a value."""
    fig, table, context, turns, cfg, plt = _tc_build(rendered)
    try:
        csv_df = pd.read_csv(rendered["tc_csv"])
        flagged = csv_df[(~csv_df["is_reversal_class"])
                         & (csv_df["insufficient"])]
        drawn = {tuple(x) for x in fig.bdb27_draw_audit["drawn_in_panel_a"]}
        insufficient = {tuple(x)
                        for x in fig.bdb27_draw_audit[
                            "insufficient_in_panel_a"]}
        assert insufficient == {(str(r["band"]), str(r["setting"]))
                                for _, r in flagged.iterrows()}
        assert drawn.isdisjoint(insufficient)
        assert len(drawn) == int(csv_df["drawn_in_panel_a"].sum())
        assert len(flagged) >= 1, "fixture must contain an unusable band"
        for band, setting in insufficient:
            n = int(csv_df[(csv_df["band"] == band)
                           & (csv_df["setting"] == setting)][
                               "n_apex_retention"].iloc[0])
            assert n < int(cfg.get("figures.turn_cost.min_n_for_median"))
        texts = " ".join(t.get_text() for ax in fig.axes for t in ax.texts)
        assert "insufficient" in texts.lower(), (
            "the image must say a band is insufficient")
        assert f"min n={int(cfg.get('figures.turn_cost.min_n_for_median'))}" \
            in texts
    finally:
        plt.close(fig)


def test_turn_cost_reversal_class_excluded_from_a_but_present_in_b(rendered):
    fig, table, context, turns, cfg, plt = _tc_build(rendered)
    try:
        audit = fig.bdb27_draw_audit
        assert not any(band == table["band"].iloc[-1]
                       for band, _ in audit["drawn_in_panel_a"])
        texts = " ".join(t.get_text() for ax in fig.axes for t in ax.texts)
        assert "excluded from (a)" in texts
        assert "reversal class" in texts
        # Panel (b) draws both settings in every band, reversal class included.
        ax_b = fig.axes[1]
        bars = [p for p in ax_b.patches if p.get_height() > 0]
        assert len(bars) == 14, f"panel b drew {len(bars)} bars, expected 14"
        hatched = [p for p in bars if p.get_hatch()]
        assert len(hatched) == 2, "the reversal class must be hatched in (b)"
        csv_df = pd.read_csv(rendered["tc_csv"])
        rev = csv_df[csv_df["is_reversal_class"]]
        assert set(rev["setting"]) == {"combine", "game"}
        assert (rev["count"] > 0).all()
        # The reversal class carries no retention value anywhere in (a).
        assert rev["median_apex_retention"].isna().all()
    finally:
        plt.close(fig)


def test_turn_cost_entry_speed_floor_removes_turns_when_raised(rendered):
    """The floor is applied, not decorative: raising it removes turns."""
    from bdb27 import common, figures
    raw = yaml.safe_load(rendered["cfg"].read_text())
    block = raw["turn_coverage"]["figures"]["turn_cost"]
    block["entry_speed_floor_keys"] = {
        "combine": "detector.speed_floor_combine",
        "game": "detector.speed_floor_game"}
    raw["turn_coverage"]["detector"]["speed_floor_combine"] = 3.5
    raw["turn_coverage"]["detector"]["speed_floor_game"] = 4.5
    path = rendered["tmp"] / "config_tc_floor.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = common.load_config(path)
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.turn_cost_table(turns, cfg)
    assert context["n_below_speed_floor"] > 0
    expected = _tc_expected(turns, cfg)
    assert int(table["n_below_speed_floor"].sum()) == int(
        expected["n_below_speed_floor"].sum())
    # Every game turn at 4.0 yd/s is now below the 4.5 floor: nothing to draw.
    assert not table[table["setting"] == "game"]["drawn_in_panel_a"].any()
    assert context["medians"]["game"] == {}


def test_turn_cost_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["tc_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["tc_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 8 differ"


def test_turn_cost_leaves_figures_1_to_7_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 8 must not alter figures 1-7")


def test_turn_cost_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][TC_KEY]
    assert fig["figure"] == TC_KEY
    assert fig["stamp"] == STAMP
    assert fig["measure"]["column"] == "apex_retention"
    assert fig["measure"]["definition"] == "apex_speed / entry_speed (stage 02)"
    assert fig["measure"]["not_plotted"]["column"] == "speed_retention"
    assert "by construction" in fig["measure"]["not_plotted"]["reason"]
    assert fig["panel_a"]["min_n_for_median"] == 20
    assert len(fig["panel_a"]["medians"]) == 2
    assert set(fig["entry_speed_floor"]) == {"combine", "game"}
    for setting, key in (("combine", "detector.speed_floor_combine"),
                         ("game", "detector.speed_floor_game")):
        assert fig["entry_speed_floor"][setting]["config_key"] == key
    assert fig["reversal_class"]["rule"] == "abs_turn_angle > 180"
    assert fig["revision"]["version"] == 2
    assert "speed_retention" in fig["revision"]["reason"]
    assert fig["png"].endswith("fig_turn_cost.png")
    assert fig["csv"].endswith("fig_turn_cost.csv")
    assert "no per-player values" in " ".join(fig["caveats"]).lower()


def test_turn_cost_caption_reports_the_curve_it_drew(rendered):
    """The caption is generated from the medians and names the right measure."""
    fig, table, context, turns, cfg, plt = _tc_build(rendered)
    try:
        text = " ".join(t.get_text() for t in fig.texts)
        flat = " ".join(text.split())
        assert "FINDING, AS MEASURED" in flat
        assert "cost is paid at the plant" in flat
        for setting in ("combine", "game"):
            med = context["medians"][setting]
            # The sentence lists every drawn median, and the next caption line
            # quotes the 150-180 degree band: each must appear with the number
            # actually drawn.
            for value in (max(med.values()), min(med.values()),
                          med[context["bands"][-2]]):
                assert f"{value:.2f}" in flat, (
                    f"caption omits the {setting} median {value:.2f}")
        assert "speed_retention" in flat, (
            "the footnote must name the measure that is not plotted")
        assert "never as a cost" in flat
        rises = any(
            seq[i + 1][1] > seq[i][1]
            for seq in ([ (b, context["medians"][s][b])
                           for b in context["bands"][:-1]
                           if b in context["medians"][s]]
                         for s in ("combine", "game"))
            for i in range(len(seq) - 1))
        assert ("not monotone" if rises else "monotone non-increasing") in flat
        assert "INSUFFICIENT" in flat
        assert "detector.speed_floor_combine" in flat
        assert "detector.speed_floor_game" in flat
        assert "detector.segmentation" in flat
        assert "0 turns" in flat or "0.0%" in flat
    finally:
        plt.close(fig)


def test_turn_cost_checks_can_fail(rendered):
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["tc_csv"])
    _check_tc_csv(good, turns, cfg)  # sanity: passes on the real file

    bad = good.copy()
    idx = bad.index[(bad["band"] == "60–90°") & (bad["setting"] == "game")][0]
    bad.loc[idx, "count"] = int(bad.loc[idx, "count"]) + 1
    with pytest.raises(AssertionError):
        _check_tc_csv(bad, turns, cfg)

    wrong_q = good.copy()
    idx = wrong_q.index[(wrong_q["band"] == "60–90°")
                        & (wrong_q["setting"] == "combine")][0]
    wrong_q.loc[idx, "median_apex_retention"] = (
        float(wrong_q.loc[idx, "median_apex_retention"]) + 0.05)
    with pytest.raises(AssertionError):
        _check_tc_csv(wrong_q, turns, cfg)

    quiet = good.copy()
    idx = quiet.index[(quiet["band"] == "30–45°")
                      & (quiet["setting"] == "combine")][0]
    quiet.loc[idx, "insufficient"] = False
    with pytest.raises(AssertionError):
        _check_tc_csv(quiet, turns, cfg)

    empty = rendered["tmp"] / "empty_tc.png"
    empty.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty)


# ------------- figure 9: same stopwatch, different turn
SW_KEY = "fig_stopwatch"


def _sw_expected(rendered) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Recompute the matched population without calling the module's table.

    Plain masks and an inner join on ``nfl_id``; the per-player aggregates are
    rebuilt from the written Parquet and the fixture combine results, so a bug
    in ``figures.stopwatch_table`` cannot agree with itself here.
    """
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    times = pd.read_csv(rendered["tmp"] / "data" / "raw" / "combine_results.csv")
    position = str(cfg.get("figures.stopwatch.position_group"))
    setting = str(cfg.get("figures.stopwatch.setting"))
    match = str(cfg.get("figures.stopwatch.drill_match"))
    valid = turns["valid"].fillna(False).astype(bool)
    sub = turns[valid
                & (turns["position_group"] == position)
                & (turns["setting"] == setting)
                & turns["drill_name"].astype("string").str.contains(
                    match, regex=False, na=False)].copy()
    joined = sub.merge(times.dropna(subset=["three_cone"]),
                       on="nfl_id", how="inner")
    agg = joined.groupby("nfl_id").agg(
        n_turns=("abs_turn_angle", "size"),
        three_cone_s=("three_cone", "first"),
        angle_min=("abs_turn_angle", "min"),
        angle_max=("abs_turn_angle", "max"),
        entry_min=("entry_speed", "min"),
        entry_max=("entry_speed", "max"),
        n_attempts=("attempt", "nunique"),
    ).reset_index().sort_values(["three_cone_s", "nfl_id"]).reset_index(drop=True)
    return agg, joined


def test_stopwatch_png_and_csv_exist(rendered):
    _check_png(rendered["sw_png"])
    assert rendered["sw_csv"].exists()
    with Image.open(rendered["sw_png"]) as im:
        assert im.size[0] >= 1500 and im.size[1] >= 900


def test_stopwatch_match_count_and_turn_counts_reconcile(rendered):
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    floor = int(cfg.get("figures.stopwatch.min_players"))
    expected, joined = _sw_expected(rendered)
    csv_df = pd.read_csv(rendered["sw_csv"])

    n_players = csv_df["nfl_id"].nunique()
    assert n_players == len(expected) == 24, "fixture must match 24 players"
    assert n_players >= floor, "the figure must clear its own stated floor"
    assert len(csv_df) == len(joined) == 98, "one row per matched turn"

    counts = csv_df.groupby("nfl_id").size()
    want = expected.set_index("nfl_id")["n_turns"]
    assert counts.sort_index().equals(want.sort_index())
    assert csv_df.groupby("nfl_id")["n_turns_player"].first().equals(
        want.astype(int))

    for player_id, part in csv_df.groupby("nfl_id"):
        row = expected[expected["nfl_id"] == player_id].iloc[0]
        assert np.isclose(float(part["three_cone_s"].iloc[0]),
                          float(row["three_cone_s"]), atol=1e-12)
        assert np.isclose(float(part["abs_turn_angle_deg"].min()),
                          float(row["angle_min"]), atol=1e-9)
        assert np.isclose(float(part["abs_turn_angle_deg"].max()),
                          float(row["angle_max"]), atol=1e-9)
        assert np.isclose(float(part["entry_speed_yd_s"].min()),
                          float(row["entry_min"]), atol=1e-9)
        assert np.isclose(float(part["entry_speed_yd_s"].max()),
                          float(row["entry_max"]), atol=1e-9)
        assert set(part["match_rule"]) == {csv_df["match_rule"].iloc[0]}

    # Rows are ordered by the published time, fastest first.
    times = csv_df.drop_duplicates("player_order").sort_values("player_order")
    assert times["three_cone_s"].is_monotonic_increasing
    assert (times["three_cone_s"].astype(float).diff().dropna() >= 0).all()
    assert csv_df["gate_passed"].all()


def test_stopwatch_pair_is_near_identical_and_labelled(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][SW_KEY]
    pair = fig["pair_illustration"]
    tol = float(fig["pair_illustration"]["tolerance_s"]) if pair else None
    assert pair is not None, "fixture carries a usable illustration pair"
    assert "illustration" in pair["status"]
    assert pair["delta_s"] <= tol + 1e-12
    assert pair["a"]["n_attempts"] == pair["b"]["n_attempts"], (
        "a pair with different attempt counts is confounded and must not be used")
    # The callout is in the caption, with its tolerance spelled out.
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    times = pd.read_csv(rendered["tmp"] / "data" / "raw" / "combine_results.csv")
    table, context = figures.stopwatch_table(turns, times, cfg)
    fig_obj = figures.plot_stopwatch(table, context, cfg)
    import matplotlib.pyplot as plt
    try:
        texts = " ".join(t.get_text() for t in fig_obj.texts)
        assert "ILLUSTRATION, NOT EVIDENCE" in texts
        assert "tolerance" in texts
        orders = fig_obj.bdb27_draw_audit["pair_orders"]
        assert len(orders) == 2
        circled = set(table[table["player_order"].isin(orders)]["nfl_id"])
        assert circled == {pair["a"]["nfl_id"], pair["b"]["nfl_id"]}
    finally:
        plt.close(fig_obj)


def test_stopwatch_computes_no_association_statistic(rendered):
    """No correlation, regression, fit or p-value in the figure 9 code path."""
    import inspect
    from bdb27 import figures

    banned_calls = ("corrcoef", "linregress", "polyfit", "spearmanr",
                    "pearsonr", "scipy", "statsmodels", "r_squared",
                    "p_value", "slope")
    for fn in (figures.stopwatch_table, figures.plot_stopwatch,
               figures._stopwatch_pair_sentence, figures._stopwatch_pair,
               figures.stopwatch_csv_frame,
               figures.stopwatch_manifest_payload):
        src = inspect.getsource(fn).lower()
        for token in banned_calls:
            assert token not in src, f"{fn.__name__} references {token!r}"

    csv_df = pd.read_csv(rendered["sw_csv"])
    for col in csv_df.columns:
        assert not any(t in col.lower() for t in banned_calls), (
            f"CSV column {col!r} looks like an association statistic")

    man = json.loads(rendered["manifest"].read_text())
    flat = json.dumps(man["figures"][SW_KEY]).lower()
    for token in ("correlation coefficient of", '"r":', '"p":', "r-squared"):
        assert token not in flat
    assert "any correlation" in flat and "regression" in flat


def test_stopwatch_refuses_below_the_floor(rendered):
    """The gate is real: 24 matched players vs a 25-player floor refuses."""
    from bdb27 import common, figures
    raw = yaml.safe_load(rendered["cfg"].read_text())
    raw["turn_coverage"]["figures"]["stopwatch"]["min_players"] = 25
    path = rendered["tmp"] / "config_sw_gate.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = common.load_config(path)
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    times = pd.read_csv(rendered["tmp"] / "data" / "raw" / "combine_results.csv")
    with pytest.raises(common.StageRefused) as exc:
        figures.stopwatch_table(turns, times, cfg)
    message = str(exc.value)
    assert "24 of 24" in message and "min_players=25" in message
    assert "no image was drawn" in message


def test_stopwatch_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["sw_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["sw_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 9 differ"


def test_stopwatch_leaves_figures_1_to_8_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png", "tc_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 9 must not alter figures 1-8")


def test_stopwatch_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][SW_KEY]
    assert fig["figure"] == SW_KEY
    assert fig["stamp"] == STAMP
    assert fig["match"]["gate"] == "PASSED"
    assert fig["match"]["join_key"] == "nfl_id"
    assert fig["match"]["min_players"] == 20
    assert fig["n_players"] == fig["match"]["n_matched_players"] == 24
    assert fig["n_turns"] == 98
    assert fig["match"]["n_unmatched_players"] == 0
    assert fig["spread"]["three_cone_s"][0] < fig["spread"]["three_cone_s"][1]
    assert fig["panel_b"]["entry_speed_floor"]["config_key"] == \
        "detector.speed_floor_combine"


# ------------- figure 10: the test never checks which way he turns
BH_KEY = "fig_both_hips"


def _bh_population(rendered) -> tuple[pd.DataFrame, "object"]:
    """The plotted population, rebuilt from the written Parquet with plain masks."""
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    low, high = (float(x) for x in
                 cfg.get("figures.both_hips.angle_band_deg"))
    position = str(cfg.get("figures.both_hips.position_group"))
    settings = [str(s) for s in cfg.get("figures.both_hips.settings")]
    valid = turns["valid"].fillna(False).astype(bool)
    sub = turns[valid
                & (turns["position_group"] == position)
                & (turns["setting"].isin(settings))
                & (turns["abs_turn_angle"] >= low)
                & (turns["abs_turn_angle"] < high)].copy()
    return sub, cfg


def _check_bh_csv(csv_df: pd.DataFrame, turns: pd.DataFrame, cfg) -> None:
    low, high = (float(x) for x in cfg.get("figures.both_hips.angle_band_deg"))
    position = str(cfg.get("figures.both_hips.position_group"))
    settings = [str(s) for s in cfg.get("figures.both_hips.settings")]
    valid = turns["valid"].fillna(False).astype(bool)
    sub = turns[valid
                & (turns["position_group"] == position)
                & (turns["setting"].isin(settings))
                & (turns["abs_turn_angle"] >= low)
                & (turns["abs_turn_angle"] < high)].copy()
    bars = csv_df[csv_df["row_kind"] == "bar"]
    assert len(bars) == 2 * len(settings), "one bar row per setting x direction"
    assert set(bars["setting"]) == set(settings)
    for _, row in bars.iterrows():
        part = sub[sub["setting"] == row["setting"]]
        sign = 1 if row["turn_sign"] == "positive" else -1
        want = int((np.sign(part["turn_angle"]) == sign).sum())
        assert int(row["count"]) == want, (
            f"{row['setting']} {row['turn_sign']}: csv={int(row['count'])} "
            f"artefact={want}")
        assert int(row["turns_setting"]) == len(part)
        assert int(row["n_players_setting"]) == int(part["nfl_id"].nunique())
        assert np.isclose(float(row["share_within_setting"]),
                          want / len(part), atol=1e-12)
        assert np.isclose(float(row["turns_per_player_median"]),
                          float(part.groupby("nfl_id").size().median()),
                          atol=1e-12)
        shared = float(bars["chirality_agree_share"].iloc[0])
        if not np.isnan(shared):
            assert np.isclose(float(row["chirality_agree_share"]), shared,
                              atol=1e-12)
        else:
            assert np.isnan(float(row["chirality_agree_share"]))
    assert int(bars["count"].sum()) == len(sub), "the bars must sum to the band"

    # Route-check rows, when present, are per family x side and reconcile.
    cells = csv_df[csv_df["row_kind"] == "route_check"]
    if len(cells):
        assert set(cells["alignment_side"]) <= {"y_above_mid", "y_below_mid"}
        for _, row in cells.iterrows():
            assert int(row["n_agree"]) <= int(row["n_turns_checked"])
            assert np.isclose(float(row["agreement_rate"]),
                              int(row["n_agree"]) / int(row["n_turns_checked"]),
                              atol=1e-12)
        per_family = cells.groupby("route_ran")["alignment_side"].nunique()
        assert (per_family == 2).all(), "every carried family needs both sides"


def test_both_hips_png_and_csv_exist(rendered):
    _check_png(rendered["bh_png"])
    assert rendered["bh_csv"].exists()
    with Image.open(rendered["bh_png"]) as im:
        assert im.size[0] >= 1500 and im.size[1] >= 900


def test_both_hips_counts_reconcile_to_the_turn_table(rendered):
    sub, cfg = _bh_population(rendered)
    csv_df = pd.read_csv(rendered["bh_csv"])
    _check_bh_csv(csv_df, pd.read_parquet(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet"), cfg)

    bars = csv_df[csv_df["row_kind"] == "bar"]
    by = bars.set_index(["setting", "turn_sign"])["count"]
    assert set(by.index) == {("combine", "positive"), ("combine", "negative"),
                             ("game", "positive"), ("game", "negative")}
    for setting in ("combine", "game"):
        part = sub[sub["setting"] == setting]
        assert by[(setting, "positive")] + by[(setting, "negative")] == len(part)
    assert bars[bars["setting"] == "combine"]["n_players_setting"].iloc[0] == \
        sub[sub["setting"] == "combine"]["nfl_id"].nunique()


def _bh_synthetic(cfg, *, mirror: bool = False, plays: int = 240):
    """A consistent route world: vendor labels, coordinates and turns agree.

    ``mirror`` flips the y axis and nothing else — coordinates then contradict
    the vendor labels, which is exactly what a wrong frame looks like.
    """
    mid = float(cfg.get("figures.both_hips.direction_check.chirality"
                        ".field_mid_y"))
    rows, game_rows, play_rows, target_rows = [], [], [], []
    gid = 9000
    for i in range(plays):
        direction = "right" if i % 2 == 0 else "left"
        side = "left" if (i // 2) % 2 == 0 else "right"
        high = (side == "left") == (direction == "right")
        y_target = 41.0 if high else 13.0
        if mirror:
            y_target = 53.3 - y_target
        pid = 1000 + i
        nfl = 5000 + i
        play_rows.append({"game_id": gid, "play_id": pid, "nfl_id": nfl,
                          "route_ran": np.nan,
                          "play_direction": direction,
                          "play_description": f"pass short {side} to X.",
                          "target": True})
        for k in range(3):
            target_rows.append({"game_id": gid, "play_id": pid, "nfl_id": nfl,
                                "y": y_target, "frame_idx": k})
    # Route turns: 40 per family, evenly split by starting side.
    tid = 0
    for family, sense in (("OUT", 1), ("IN", -1), ("SLANT", -1)):
        for j in range(40):
            direction = "right" if j % 2 == 0 else "left"
            start_high = (j // 2) % 2 == 0
            y0 = 41.0 if start_high else 13.0
            lateral = (1 if start_high else -1) * sense
            if mirror:
                y0 = 53.3 - y0
                lateral = -lateral
            dx = 1.0 if direction == "right" else -1.0
            pid = 2000 + tid
            nfl = 7000 + (tid % 40)
            tid += 1
            vx0, vy0 = dx, 0.0
            vx1, vy1 = dx * 0.7, lateral * 0.7
            h0 = np.degrees(np.arctan2(vx0, vy0))
            h1 = np.degrees(np.arctan2(vx1, vy1))
            turn = (h1 - h0 + 180.0) % 360.0 - 180.0
            xs, ys = [], []
            x, y = 40.0, y0
            for k in range(10):
                xs.append(x)
                ys.append(y)
                if k < 2:
                    x += vx0 * 0.1
                    y += vy0 * 0.1
                else:
                    x += vx1 * 0.1
                    y += vy1 * 0.1
            for k in range(10):
                game_rows.append({"game_id": gid, "play_id": pid,
                                  "nfl_id": nfl, "frame_idx": k,
                                  "x": xs[k], "y": ys[k], "s": 5.0,
                                  "o": 0.0, "dir": 0.0})
            rows.append({"game_id": gid, "play_id": pid, "nfl_id": nfl,
                         "route_ran": family, "play_direction": direction,
                         "frame_start": 2, "frame_end": 9,
                         "turn_angle": turn,
                         "abs_turn_angle": abs(turn)})
    return (pd.DataFrame(rows), pd.DataFrame(game_rows), pd.DataFrame(play_rows),
            pd.DataFrame(target_rows))


def test_both_hips_checks_resolve_direction_from_vendor_semantics(rendered):
    """Route + vendor labels pin the side; a mirrored y axis fails both checks."""
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns, frames, plays, targets = _bh_synthetic(cfg)
    route = {"turns": turns, "frames": frames, "plays": plays,
             "targets": targets}
    check, table = figures.both_hips_checks(route, cfg)
    assert check["chirality"]["n_plays"] == 240
    assert check["chirality"]["agree_share"] == 1.0
    assert check["chirality"]["resolved"] is True
    assert check["route"]["consistent"] is True
    assert set(check["route"]["families_carried"]) == {"OUT", "IN", "SLANT"}
    assert check["resolved"] is True
    assert figures.direction_labels(check) == {"positive": "RIGHT",
                                               "negative": "LEFT"}
    # Per-cell table: every family on both sides, and no NaN rates.
    assert len(table) == 6
    assert set(table["start_side"]) == {"y_above_mid", "y_below_mid"}
    assert (table["agreement_rate"] == 1.0).all()

    mirrored = {"turns": _bh_synthetic(cfg, mirror=True)[0],
                "frames": _bh_synthetic(cfg, mirror=True)[1],
                "plays": _bh_synthetic(cfg, mirror=True)[2],
                "targets": _bh_synthetic(cfg, mirror=True)[3]}
    bad, bad_table = figures.both_hips_checks(mirrored, cfg)
    assert bad["chirality"]["agree_share"] == 0.0
    assert bad["chirality"]["resolved"] is False
    assert bad["resolved"] is False
    assert figures.direction_labels(bad) == {"positive": "SIDE A",
                                             "negative": "SIDE B"}


def test_both_hips_labels_follow_the_recorded_check(rendered):
    """The fixture carries no route data, so the figure falls back and says so."""
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][BH_KEY]
    direction = fig["direction"]

    assert direction["chirality_check"]["resolved"] is False
    assert direction["chirality_check"]["reason"]
    assert direction["route_check"]["consistent"] is False
    assert direction["resolved"] is False or direction["labels"] == {
        "positive": "RIGHT", "negative": "LEFT"}
    csv_df = pd.read_csv(rendered["bh_csv"])
    bars = csv_df[csv_df["row_kind"] == "bar"]
    # Whatever the checks say, the labels on the bars are the labels the check
    # produced — never a hard-coded pair.
    expected = figures.direction_labels({
        "resolved": direction["resolved"]})
    assert set(bars["direction_label"]) == {expected["positive"],
                                            expected["negative"]}
    assert set(bars[bars["turn_sign"] == "positive"]["direction_label"]) == {
        expected["positive"]}

    assert figures.direction_labels({"resolved": False}) == {
        "positive": "SIDE A", "negative": "SIDE B"}
    assert figures.direction_labels({"resolved": True}) == {
        "positive": "RIGHT", "negative": "LEFT"}


def test_both_hips_orientation_check_can_fail(rendered):
    """Flip the body orientation and the consistency test flips too."""
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns = pd.DataFrame({
        "game_id": [1] * 30,
        "play_id": list(range(30)),
        "nfl_id": [7] * 30,
        "frame_start": [0] * 30,
        "frame_end": [9] * 30,
        "turn_angle": [40.0 if i % 2 == 0 else -40.0 for i in range(30)],
    })
    frames = pd.DataFrame({
        "game_id": [1] * 60,
        "play_id": [p for p in range(30) for _ in (0, 1)],
        "nfl_id": [7] * 60,
        "frame_idx": [0, 9] * 30,
        "x": [0.0] * 60,
        "y": [0.0] * 60,
        "s": [5.0] * 60,
        "dir": [90.0] * 60,
        "o": [90.0 + (40.0 if (p % 2 == 0) else -40.0) * (1 if f == 9 else 0)
              for p in range(30) for f in (0, 9)],
    })
    ok = figures.orientation_check(turns, frames, cfg)
    assert ok["n_tested"] == 30 and ok["n_agree"] == 30
    assert ok["sign_tracks_orientation"] is True

    flipped = frames.copy()
    flipped["o"] = 90.0 - (flipped["o"] - 90.0)
    bad = figures.orientation_check(turns, flipped, cfg)
    assert bad["n_tested"] == 30 and bad["n_agree"] == 0
    assert bad["sign_tracks_orientation"] is False

    absent = figures.orientation_check(turns, frames.iloc[0:0], cfg)
    assert absent["n_tested"] == 0 and absent["agree_share"] is None
    assert absent["sign_tracks_orientation"] is False


def test_both_hips_computes_no_symmetry_or_p_value(rendered):
    import inspect
    from bdb27 import figures

    banned = ("binomtest", "binom_test", "chisquare", "chi2_contingency",
              "ttest", "fisher_exact", "mcnemar", "p_value", "pvalue",
              "stderr", "conf_int", "ci_low")
    for fn in (figures.both_hips_table, figures.orientation_check,
               figures.chirality_check, figures.route_direction_check,
               figures.both_hips_checks, figures.direction_labels,
               figures.plot_both_hips, figures.both_hips_csv_frame,
               figures.both_hips_manifest_payload):
        src = inspect.getsource(fn).lower()
        for token in banned:
            assert token not in src, f"{fn.__name__} references {token!r}"

    csv_df = pd.read_csv(rendered["bh_csv"])
    for col in csv_df.columns:
        assert not any(t in col.lower() for t in banned), (
            f"CSV column {col!r} looks like a test statistic")

    man = json.loads(rendered["manifest"].read_text())
    flat = json.dumps(man["figures"][BH_KEY]).lower()
    assert "no symmetry test" in flat or "not tested for symmetry" in flat
    for token in ('"p":', '"p_value"', '"chi2"', '"odds_ratio"'):
        assert token not in flat


def test_both_hips_renders_every_figure(rendered):
    out = rendered["tmp"] / "outputs" / "figures"
    pngs = sorted(p.name for p in out.glob("fig_*.png"))
    assert len(pngs) == 15, f"stage 06 must render fifteen figures, found {pngs}"
    assert "fig_both_hips.png" in pngs


def test_both_hips_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["bh_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["bh_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 10 differ"


def test_both_hips_leaves_figures_1_to_9_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png", "tc_png", "sw_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 10 must not alter figures 1-9")


def test_both_hips_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][BH_KEY]
    assert fig["figure"] == BH_KEY
    assert fig["stamp"] == STAMP
    assert fig["band"]["position_group"] == "DB"
    assert fig["band"]["abs_turn_angle_deg"] == [30.0, 60.0]
    assert set(fig["counts"]) == {"combine", "game"}
    for setting in ("combine", "game"):
        block = fig["counts"][setting]
        assert block["positive"] + block["negative"] == block["total"]
    direction = fig["direction"]
    assert direction["chirality_check"]["field"].startswith("play_description")
    assert direction["route_check"]["min_cell"] >= 1
    assert direction["route_check"]["min_families"] >= 1
    assert isinstance(direction["route_check"]["table"], list)
    assert direction["resolved"] in (True, False)
    assert direction["labels"]["positive"] != direction["labels"]["negative"]
    assert fig["png"].endswith("fig_both_hips.png")
    assert fig["csv"].endswith("fig_both_hips.csv")


def test_both_hips_caption_carries_the_claim_and_the_refusal(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    sub, _ = _bh_population(rendered)
    frames = figures.load_game_frames(
        cfg, "sample", sub[sub["setting"] == "game"])
    route = {"turns": pd.DataFrame(), "frames": pd.DataFrame(),
             "plays": pd.DataFrame(), "targets": pd.DataFrame()}
    check, _ = figures.both_hips_checks(route, cfg)
    table, context = figures.both_hips_table(turns, frames, check, cfg)
    fig = figures.plot_both_hips(table, context, cfg).draw()
    import matplotlib.pyplot as plt
    try:
        flat = " ".join(" ".join(t.get_text().split()) for t in fig.texts)
        assert "NOT A CLAIM ABOUT THESE PLAYERS" in flat
        assert "not evidence" in flat
        assert "No symmetry test" in flat and "binomial" in flat
        assert "NOT A MODELLED EFFECT" in flat and "|turn_angle|" in flat
        assert "HOW THE LABELS WERE FIXED" in flat
        assert "FAMILIES THAT DID NOT CARRY IT" in flat
        assert f"{context['totals']['combine']} shallow turns" in flat
        assert f"{context['players']['combine']} players" in flat
        for _, row in table.iterrows():
            assert f"{int(row['count'])} {row['direction_label']}" in flat
        labels = context["labels"]
        assert labels["positive"] in flat and labels["negative"] in flat
    finally:
        plt.close(fig)


def test_both_hips_checks_can_fail(rendered):
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["bh_csv"])
    _check_bh_csv(good, turns, cfg)  # sanity: passes on the real file

    bad = good.copy()
    idx = bad.index[(bad["row_kind"] == "bar") & (bad["setting"] == "combine")
                    & (bad["turn_sign"] == "positive")][0]
    bad.loc[idx, "count"] = int(bad.loc[idx, "count"]) + 1
    with pytest.raises(AssertionError):
        _check_bh_csv(bad, turns, cfg)

    wrong_players = good.copy()
    wrong_players.loc[wrong_players["row_kind"] == "bar", "n_players_setting"] = 99
    with pytest.raises(AssertionError):
        _check_bh_csv(wrong_players, turns, cfg)

    empty = rendered["tmp"] / "empty_bh.png"
    empty.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty)


# --------------------------------------------------------------------------
# Figure 11: where the shallow turns come from
# --------------------------------------------------------------------------
SS_KEY = "fig_shallow_sources"


def _all_text(fig) -> str:
    """Every string drawn on the figure: fig-level and inside the panel."""
    parts = [t.get_text() for t in fig.texts]
    for ax in fig.axes:
        parts.extend(t.get_text() for t in ax.texts)
    return " ".join(" ".join(str(p).split()) for p in parts)


def _ss_population(rendered) -> tuple[pd.DataFrame, "object"]:
    """The plotted population, rebuilt from the written Parquet with plain masks."""
    from bdb27 import common
    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    low, high = (float(x) for x in
                 cfg.get("figures.shallow_sources.angle_band_deg"))
    position = str(cfg.get("figures.shallow_sources.position_group"))
    settings = [str(s) for s in cfg.get("figures.shallow_sources.settings")]
    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    sub = turns[valid
                & (turns["position_group"] == position)
                & (turns["setting"].isin(settings))
                & (angle >= low) & (angle < high)].copy()
    return sub, cfg


def _check_ss_csv(csv_df: pd.DataFrame, turns: pd.DataFrame, cfg) -> None:
    """Reconcile every per-player row against the turn table, or raise."""
    low, high = (float(x) for x in
                 cfg.get("figures.shallow_sources.angle_band_deg"))
    position = str(cfg.get("figures.shallow_sources.position_group"))
    settings = [str(s) for s in cfg.get("figures.shallow_sources.settings")]
    rule = int(cfg.get("envelope.tau2_min_turns_per_player"))
    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    sub = turns[valid
                & (turns["position_group"] == position)
                & (turns["setting"].isin(settings))
                & (angle >= low) & (angle < high)].copy()

    assert "player_name" not in csv_df.columns, "no name column may be written"
    assert set(csv_df["setting"]) == set(settings)
    assert set(csv_df["rule_min_turns"].astype(int)) == {rule}
    assert set(csv_df["rule_key"]) == {"envelope.tau2_min_turns_per_player"}
    expected_rows = int(sub.groupby("setting")["nfl_id"].nunique().sum())
    assert len(csv_df) == expected_rows, (
        f"csv rows={len(csv_df)} but there are {expected_rows} player-settings")
    assert not csv_df.duplicated(["setting", "nfl_id"]).any(), (
        "one row per player per setting")

    for _, row in csv_df.iterrows():
        part = sub[(sub["setting"] == row["setting"])
                   & (sub["nfl_id"] == row["nfl_id"])]
        assert len(part) == int(row["turns"]), (
            f"{row['setting']} player {row['nfl_id']}: csv turns "
            f"{int(row['turns'])} vs artefact {len(part)}")
        total = int((sub["setting"] == row["setting"]).sum())
        assert int(row["turns_setting"]) == total
        assert int(row["n_players_setting"]) == int(
            sub[sub["setting"] == row["setting"]]["nfl_id"].nunique())
        assert np.isclose(float(row["share_of_setting_turns"]),
                          int(row["turns"]) / total, atol=1e-12)
        if row["setting"] == "combine":
            drills = sorted({str(d) for d in part["drill_name"].dropna()})
            assert sorted(d for d in str(row["drill_names"]).split(";") if d) \
                == drills, "the CSV must name exactly the drills that carried him"
            assert int(row["n_drills"]) == len(drills)
            assert bool(row["labelled_by_drill"]) == (int(row["turns"]) >= 2)
        else:
            assert str(row["drill_names"]) in ("", "nan"), (
                "games carry no drill label")

    for setting in settings:
        part = sub[sub["setting"] == setting]
        rows = csv_df[csv_df["setting"] == setting]
        per = part.groupby("nfl_id").size()
        assert int(rows["turns"].sum()) == len(part)
        assert int(rows["turns"].max()) == int(per.max())
        assert int(rows["at_one_turns"].iloc[0]) == int((per == 1).sum())
        assert int(rows["median_turns_per_player"].iloc[0]) \
            == int(round(float(per.median())))
    assert int(csv_df["turns"].sum()) == len(sub), (
        "the dots must reconstruct the whole band, no player dropped")


def test_shallow_sources_png_and_csv_exist(rendered):
    _check_png(rendered["ss_png"])
    assert rendered["ss_csv"].exists()
    with Image.open(rendered["ss_png"]) as im:
        assert im.size[0] >= 1500 and im.size[1] >= 900


def test_shallow_sources_counts_reconcile_to_the_turn_table(rendered):
    sub, cfg = _ss_population(rendered)
    csv_df = pd.read_csv(rendered["ss_csv"])
    _check_ss_csv(csv_df, pd.read_parquet(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet"), cfg)
    assert int(sub.groupby("setting")["nfl_id"].nunique().sum()) == len(csv_df)


def test_shallow_sources_one_dot_per_player_no_aggregation(rendered):
    """The row shape IS the claim: every player is drawn, none are bucketed."""
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    sub, _ = _ss_population(rendered)
    table, context = figures.shallow_sources_table(sub, cfg)
    frame = figures.shallow_sources_plot_frame(table, context, cfg)
    assert len(frame) == sub.groupby("setting")["nfl_id"].nunique().sum()
    for _, row in frame.iterrows():
        part = sub[(sub["setting"] == row["setting"])
                   & (sub["nfl_id"] == row["nfl_id"])]
        assert int(row["turns"]) == len(part), "x must be that player's count"
    # Counts are printed on the image rather than left to the eye alone.
    fig = figures.plot_shallow_sources(table, context, cfg).draw()
    import matplotlib.pyplot as plt
    try:
        flat = _all_text(fig)
        assert f"{context['totals']['combine']} turns" in flat
        assert f"{context['players']['combine']} players" in flat
        assert f"{context['totals']['game']} turns" in flat
        assert f"{context['players']['game']} players" in flat
        assert f"{context['at_one']['combine']} at exactly 1" in flat
    finally:
        plt.close(fig)


def test_shallow_sources_rule_line_is_the_envelope_floor(rendered):
    """The line is read from config and drawn at that value, not hard-coded."""
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    want = int(cfg.get("envelope.tau2_min_turns_per_player"))
    sub, _ = _ss_population(rendered)
    table, context = figures.shallow_sources_table(sub, cfg)
    assert context["rule_min"] == want
    fig = figures.plot_shallow_sources(table, context, cfg).draw()
    import matplotlib.pyplot as plt
    try:
        drawn = []
        for artist in list(fig.axes[0].collections) + list(fig.axes[0].lines):
            paths = getattr(artist, "get_paths", lambda: [])()
            for path in paths:
                v = path.vertices
                if len(v) >= 2 and np.isclose(v[0, 0], v[-1, 0]):
                    drawn.append(float(v[0, 0]))
            segs = getattr(artist, "get_segments", lambda: [])()
            for seg in segs:
                if len(seg) >= 2 and np.isclose(seg[0, 0], seg[-1, 0]):
                    drawn.append(float(seg[0][0]))
        assert any(np.isclose(v, want) for v in drawn), (
            f"no rule line at {want}; drawn={drawn}")
        flat = _all_text(fig)
        assert f"vertical line at {want} turns" in flat
        assert "envelope.tau2_min_turns_per_player" in flat
    finally:
        plt.close(fig)

    # A different config value moves the line and the CSV column with it.
    alt = yaml.safe_load(rendered["cfg"].read_text())
    alt["turn_coverage"]["envelope"]["tau2_min_turns_per_player"] = want + 3
    alt_path = rendered["tmp"] / "config_rule_alt.yaml"
    alt_path.write_text(yaml.safe_dump(alt, sort_keys=False))
    alt_cfg = common.load_config(alt_path)
    alt_table, alt_context = figures.shallow_sources_table(sub, alt_cfg)
    assert alt_context["rule_min"] == want + 3
    alt_csv = figures.shallow_sources_csv_frame(alt_table, alt_context)
    assert set(alt_csv["rule_min_turns"]) == {want + 3}


def test_shallow_sources_labels_are_drills_not_player_names(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    sub, _ = _ss_population(rendered)
    csv_df = pd.read_csv(rendered["ss_csv"])

    # Craft a multi-turn Combine player so the labelling path is exercised on
    # the fixture, then check the label text names DRILLS and nothing else.
    combine_rows = sub.index[sub["setting"] == "combine"]
    if len(combine_rows) < 3:
        pytest.skip("fixture has too few Combine band turns to label")
    mutated = sub.copy()
    victim = int(mutated.loc[combine_rows[0], "nfl_id"])
    mutated.loc[combine_rows[:3], "nfl_id"] = victim
    combine = mutated[mutated["setting"] == "combine"]
    table, context = figures.shallow_sources_table(mutated, cfg)
    frame = figures.shallow_sources_plot_frame(table, context, cfg)
    labels = figures.shallow_sources_label_frame(frame, table, context)
    assert len(labels) >= 1, "the multi-turn player must be labelled"
    assert (labels["turns"] >= 2).all(), "only players above one are labelled"
    assert victim in set(labels["nfl_id"]), "the crafted player is labelled"
    text = str(labels[labels["nfl_id"] == victim]["text"].iloc[0])
    part = combine[combine["nfl_id"] == victim]
    for drill in part["drill_name"].dropna().unique():
        assert figures.shallow_drill_label(drill) in text, (
            f"label does not name the drill {drill}")
    assert str(victim) not in text, "a player id must never be printed"
    assert "nfl_id" not in text

    # No name reaches the CSV, the caption or the manifest.
    names = []
    full = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    if "player_name" in full.columns:
        names = [str(n) for n in full["player_name"].dropna().unique()
                 if str(n).strip()]
    blob = csv_df.to_csv(index=False)
    man = json.dumps(json.loads(rendered["manifest"].read_text())
                     ["figures"][SS_KEY])
    fig = figures.plot_shallow_sources(
        *figures.shallow_sources_table(sub, cfg), cfg).draw()
    import matplotlib.pyplot as plt
    try:
        caption = _all_text(fig)
    finally:
        plt.close(fig)
    for name in names:
        assert name not in blob, f"player name {name!r} leaked into the CSV"
        assert name not in caption, f"player name {name!r} leaked into the image"
        assert name not in man, f"player name {name!r} leaked into the manifest"


def test_shallow_sources_never_averages_or_tests(rendered):
    """The figure's own code path may count, but not summarise or test."""
    import inspect
    from bdb27 import figures
    source = "\n".join(inspect.getsource(fn) for fn in (
        figures.shallow_sources_table, figures.shallow_sources_plot_frame,
        figures.plot_shallow_sources, figures.shallow_sources_csv_frame))
    for banned in (".mean(", "geom_boxplot", "geom_smooth", "scipy", "ttest",
                   "linregress", "corr("):
        assert banned not in source, f"{banned!r} appears in the figure path"
    csv_df = pd.read_csv(rendered["ss_csv"])
    for banned in ("mean", "p_value", "coverage_score"):
        assert banned not in csv_df.columns


def test_shallow_sources_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["ss_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["ss_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 11 differ"


def test_shallow_sources_leaves_figures_1_to_10_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png", "tc_png", "sw_png", "bh_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 11 must not alter figures 1-10")


def test_shallow_sources_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][SS_KEY]
    assert fig["figure"] == SS_KEY
    assert fig["stamp"] == STAMP
    assert fig["band"]["position_group"] == "DB"
    assert fig["band"]["abs_turn_angle_deg"] == [30.0, 60.0]
    assert set(fig["counts"]) == {"combine", "game"}
    assert fig["rule"]["key"] == "envelope.tau2_min_turns_per_player"
    assert fig["rule"]["value"] >= 1
    assert fig["labels"]["player_names_used"] is False
    for setting in ("combine", "game"):
        block = fig["counts"][setting]
        assert block["players_at_exactly_one"] <= block["players"]
        assert block["turns_per_player_median"] >= 1
        assert block["max_turns_player"] >= block["turns_per_player_median"]
    assert fig["counts"]["combine"]["drills"], "Combine drills must be recorded"
    assert fig["png"].endswith("fig_shallow_sources.png")
    assert fig["csv"].endswith("fig_shallow_sources.csv")
    assert "mean" in json.dumps(fig["not_claimed"]).lower()


def test_shallow_sources_refuses_and_checks_can_fail(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    good = pd.read_csv(rendered["ss_csv"])
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    _check_ss_csv(good, turns, cfg)  # sanity: passes on the real file

    bad = good.copy()
    bad.loc[bad.index[0], "turns"] = int(bad.loc[bad.index[0], "turns"]) + 1
    with pytest.raises(AssertionError):
        _check_ss_csv(bad, turns, cfg)

    dup = pd.concat([good, good.iloc[[0]]], ignore_index=True)
    with pytest.raises(AssertionError):
        _check_ss_csv(dup, turns, cfg)

    sub, _ = _ss_population(rendered)
    empty = sub.copy()
    empty["abs_turn_angle"] = 200.0
    with pytest.raises(common.StageRefused):
        figures.shallow_sources_table(empty, cfg)

    only_combine = sub[sub["setting"] == "combine"].copy()
    with pytest.raises(common.StageRefused):
        figures.shallow_sources_table(only_combine, cfg)

    empty_png = rendered["tmp"] / "empty_ss.png"
    empty_png.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty_png)


# --------------------------------------------------------------------------
# Figure 12: the ninety-second ledger
# --------------------------------------------------------------------------
LG_KEY = "fig_ledger"


def _lg_build(rendered):
    """Rebuild the ledger from the written Parquet with the repo's own code."""
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns = figures.load_ledger_turns(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.ledger_table(turns, cfg)
    fig = figures.plot_ledger(table, context, cfg)
    return fig, table, context, cfg, turns


def _lg_population(turns: pd.DataFrame, cfg):
    """Plain-mask recount of the three ledger denominators, no figure code."""
    position = str(cfg.get("figures.ledger.position_group"))
    low, high = (float(x) for x in cfg.get("figures.ledger.angle_band_deg"))
    rel_low, rel_high = (float(x) for x in
                         cfg.get("figures.ledger.reliability_band_deg"))
    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    scope = valid & (turns["position_group"] == position) & angle.notna()
    band = turns[scope & (angle >= low) & (angle < high)]
    wide = turns[scope & (angle >= rel_low) & (angle < rel_high)]
    dbs = int(turns[scope & (turns["setting"] == "combine")]["nfl_id"].nunique())
    game = band[band["setting"] == "game"]
    return {
        "game_turns": int(len(game)),
        "game_players": int(game["nfl_id"].nunique()),
        "game_player_games": int(game.groupby(["nfl_id", "game_id"]).ngroups),
        "combine_turns": int(len(band[band["setting"] == "combine"])),
        "combine_players": int(
            band[band["setting"] == "combine"]["nfl_id"].nunique()),
        "combine_dbs": dbs,
        "band": band,
        "wide": wide,
    }


def test_ledger_png_and_csv_exist(rendered):
    _check_png(rendered["lg_png"])
    assert rendered["lg_csv"].exists()
    csv_df = pd.read_csv(rendered["lg_csv"])
    assert len(csv_df) == 3


def test_ledger_counts_reconcile_to_the_turn_table(rendered):
    _, table, context, cfg, turns = _lg_build(rendered)
    want = _lg_population(turns, cfg)
    reps = int(cfg.get("figures.ledger.reps"))
    assert reps >= 1
    csv_df = pd.read_csv(rendered["lg_csv"])
    by_row = csv_df.set_index("row")

    assert int(by_row.loc["game_demand", "count"]) == want["game_turns"]
    assert int(by_row.loc["game_demand", "players"]) == want["game_players"]
    assert int(by_row.loc["game_demand", "player_games"]) == \
        want["game_player_games"]
    assert int(by_row.loc["combine_supply", "count"]) == want["combine_turns"]
    assert int(by_row.loc["combine_supply", "combine_dbs"]) == \
        want["combine_dbs"]

    projection = int(by_row.loc["six_reps", "count"])
    assert projection == want["combine_dbs"] * reps
    assert projection == context["projection"]

    # The reliability counts the caption leans on, recounted independently.
    per = want["band"][want["band"]["setting"] == "combine"].groupby(
        "nfl_id").size()
    assert int(by_row.loc["six_reps", "counted_band_players"]) == int(per.size)
    assert int(by_row.loc["six_reps",
                          "counted_band_players_at_exactly_one"]) == \
        int((per == 1).sum())


def test_ledger_hatch_flag_is_true_only_for_the_projection(rendered):
    csv_df = pd.read_csv(rendered["lg_csv"])
    hatched = csv_df[csv_df["hatch"].astype(str).str.lower() == "true"]
    assert list(hatched["row"]) == ["six_reps"]
    assert list(hatched["source_kind"]) == ["projected"]
    measured = csv_df[csv_df["row"] != "six_reps"]
    assert measured["source_kind"].eq("measured").all()
    assert not measured["hatch"].astype(str).str.lower().eq("true").any()


def test_ledger_never_ratios_across_the_hatch(rendered):
    csv_df = pd.read_csv(rendered["lg_csv"])
    banned = [c for c in csv_df.columns
              if any(word in c.lower()
                     for word in ("ratio", "share", "per_appearance", "gap"))]
    assert banned == [], f"a cross-setting ratio column appeared: {banned}"
    man = json.loads(rendered["manifest"].read_text())["figures"][LG_KEY]
    assert "projection-to-measurement" in man["comparison_rule"]
    assert man["arithmetic"]["is_measurement"] is False
    assert man["arithmetic"]["verified"] is True
    # Three rows only: no subtracted fourth bar.
    assert [r["row"] for r in man["rows"]] == [
        "game_demand", "combine_supply", "six_reps"]


def test_ledger_caption_answers_the_oversampling_objection(rendered):
    fig, table, context, cfg, _ = _lg_build(rendered)
    try:
        text = _all_text(fig)
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
    rel = context["reliability"]
    wide = context["wide_reliability"]
    assert "OVERSAMPLING OBJECTION" in text
    assert "RELIABILITY PER PLAYER" in text
    assert context["rule_key"] in text
    assert str(context["rule_min"]) in text
    assert f"{wide['turns']} turns from {wide['players']} players" in text
    assert f"{wide['players_at_exactly_one']} of them with exactly one" in text
    assert f"{rel['turns']} turns from {rel['players']} players" in text
    assert f"{rel['players_at_exactly_one']} with exactly one" in text
    assert rel["players_at_exactly_one"] <= rel["players"]
    assert rel["players_at_or_above_rule"] <= rel["players"]
    assert "LOG AXIS" in text and "not proportional" in text.lower()
    assert "2027 class" in text
    assert "not in how many players can turn" in text


def test_ledger_projection_is_labelled_arithmetic_on_the_image(rendered):
    fig, table, context, cfg, _ = _lg_build(rendered)
    try:
        text = _all_text(fig)
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
    assert "arithmetic projection, not measured" in text
    assert f"{context['combine_dbs']} DBs × {context['reps']} reps" in text
    assert f"{context['projection']:,}" in text


def test_ledger_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["lg_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["lg_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 12 differ"


def test_ledger_leaves_figures_1_to_11_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png", "tc_png", "sw_png", "bh_png", "ss_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 12 must not alter figures 1-11")


def test_ledger_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][LG_KEY]
    assert fig["figure"] == LG_KEY
    assert fig["stamp"] == STAMP
    assert fig["band"]["position_group"] == "DB"
    assert fig["band"]["abs_turn_angle_deg"] == [30.0, 45.0]
    assert fig["log_axis"]["used"] is True
    assert "log(count)" in fig["log_axis"]["warning"]
    assert fig["reliability"]["rule"]["key"] == \
        "envelope.tau2_min_turns_per_player"
    assert fig["reliability"]["rule"]["value"] == 2
    for band in ("band", "wide_band"):
        block = fig["reliability"][band]
        assert block["players_at_exactly_one"] <= block["players"]
        assert block["turns"] >= block["players"]
    assert fig["png"].endswith("fig_ledger.png")
    assert fig["csv"].endswith("fig_ledger.csv")
    assert any("projection" in c for c in fig["not_claimed"])


def test_ledger_refuses_and_checks_can_fail(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    _, _, _, _, turns = _lg_build(rendered)

    empty = turns.copy()
    empty["abs_turn_angle"] = 200.0
    with pytest.raises(common.StageRefused):
        figures.ledger_table(empty, cfg)

    only_combine = turns[turns["setting"] == "combine"].copy()
    with pytest.raises(common.StageRefused):
        figures.ledger_table(only_combine, cfg)

    cfg_zero = yaml.safe_load(rendered["cfg"].read_text())
    cfg_zero["turn_coverage"]["figures"]["ledger"]["reps"] = 0
    bad_path = rendered["tmp"] / "config_zero_reps.yaml"
    bad_path.write_text(yaml.safe_dump(cfg_zero, sort_keys=False))
    with pytest.raises(common.StageRefused):
        figures.ledger_table(turns, common.load_config(bad_path))

    empty_png = rendered["tmp"] / "empty_lg.png"
    empty_png.write_bytes(b"")
    with pytest.raises(AssertionError):
        _check_png(empty_png)


# --------------------------------------------------------------------------
# Figure 13: bars of doubt
# --------------------------------------------------------------------------
BD_KEY = "fig_bars_of_doubt"


def _bd_build(rendered):
    """Rebuild figure 13 from the written Parquet with the repo's own code."""
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    turns = figures.load_bars_of_doubt_turns(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.bars_of_doubt_table(turns, cfg)
    fig = figures.plot_bars_of_doubt(table, context, cfg)
    return fig, table, context, cfg, turns


def _bd_population(turns: pd.DataFrame, cfg):
    """Plain-mask recount of the band population, no figure code."""
    position = str(cfg.get("figures.bars_of_doubt.position_group"))
    low, high = (float(x) for x in
                 cfg.get("figures.bars_of_doubt.angle_band_deg"))
    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    return turns[valid & (turns["position_group"] == position)
                 & angle.notna() & (angle >= low) & (angle < high)]


def _bd_caption_lines(fig) -> list[str]:
    """The caption's physical lines (the longest fig-level text object)."""
    caption = max(fig.texts, key=lambda t: len(t.get_text())).get_text()
    return caption.split("\n")


def test_bars_of_doubt_png_and_csv_exist(rendered):
    _check_png(rendered["bd_png"])
    assert rendered["bd_csv"].exists()
    csv_df = pd.read_csv(rendered["bd_csv"])
    assert len(csv_df) == 101
    assert csv_df["setting"].value_counts().to_dict() == {
        "game": 97, "combine": 4}


def test_bars_of_doubt_row_counts_match_the_band_population(rendered):
    _, table, context, cfg, turns = _bd_build(rendered)
    band = _bd_population(turns, cfg)
    csv_df = pd.read_csv(rendered["bd_csv"])

    assert len(csv_df) == len(band) == len(table) == 101
    for setting in ("combine", "game"):
        want = int((band["setting"] == setting).sum())
        assert int((csv_df["setting"] == setting).sum()) == want
        assert context["by_setting"][setting] == want
    # The band is left-closed and the row set is exactly the valid DB band.
    assert csv_df["position_group"].eq("DB").all()
    assert csv_df["abs_turn_angle"].between(30.0, 45.0, inclusive="left").all()
    assert list(csv_df["row_order"]) == sorted(csv_df["row_order"])


def test_bars_of_doubt_crossed_edge_share_is_recounted_from_the_parquet(rendered):
    _, table, context, cfg, turns = _bd_build(rendered)
    low, high = (float(x) for x in
                 cfg.get("figures.bars_of_doubt.angle_band_deg"))
    band = _bd_population(turns, cfg)
    crossed = ((band["abs_turn_angle_smoother_min"] < low)
               | (band["abs_turn_angle_smoother_max"] >= high))
    want = int(crossed.sum())
    share = want / len(band)

    csv_df = pd.read_csv(rendered["bd_csv"])
    flagged = csv_df["crossed_edge"].astype(str).str.lower().eq("true")
    assert int(flagged.sum()) == want
    assert context["n_crossed"] == want
    assert np.isclose(context["crossed_share"], share, atol=1e-12)

    man = json.loads(rendered["manifest"].read_text())["figures"][BD_KEY]
    assert man["crossing"]["rows"] == want
    assert np.isclose(man["crossing"]["share"], share, atol=1e-12)
    assert 0 < want <= len(band)
    assert np.isclose(share, want / 101, atol=1e-12)


def test_bars_of_doubt_cap_flag_is_applied_exactly_to_the_cap_width(rendered):
    _, table, context, cfg, _ = _bd_build(rendered)
    cap = float(cfg.get("figures.bars_of_doubt.max_whisker_width_deg"))
    half = cap / 2.0
    csv_df = pd.read_csv(rendered["bd_csv"])

    expected = ((csv_df["abs_turn_angle_smoother_min"]
                 < csv_df["abs_turn_angle"] - half - 1e-9)
                | (csv_df["abs_turn_angle_smoother_max"]
                   > csv_df["abs_turn_angle"] + half + 1e-9))
    got = csv_df["capped"].astype(str).str.lower().eq("true")
    assert got.equals(expected)
    assert int(got.sum()) == int(context["n_capped"])

    # The drawn whisker contains the dot, never exceeds the cap, and equals the
    # uncapped range wherever no end was cut.
    assert (csv_df["whisker_low_deg"]
            <= csv_df["abs_turn_angle"] + 1e-9).all()
    assert (csv_df["whisker_high_deg"]
            >= csv_df["abs_turn_angle"] - 1e-9).all()
    assert ((csv_df["whisker_high_deg"] - csv_df["whisker_low_deg"])
            <= cap + 1e-6).all()
    uncut = ~expected
    assert np.allclose(csv_df.loc[uncut, "whisker_low_deg"],
                       csv_df.loc[uncut, "abs_turn_angle_smoother_min"])
    assert np.allclose(csv_df.loc[uncut, "whisker_high_deg"],
                       csv_df.loc[uncut, "abs_turn_angle_smoother_max"])

    # The CSV keeps the uncapped spread even when the drawing is cut.
    assert np.allclose(csv_df["smoother_spread_deg"],
                       csv_df["abs_turn_angle_smoother_max"]
                       - csv_df["abs_turn_angle_smoother_min"])
    assert csv_df.loc[got, "smoother_spread_deg"].max() > cap


def test_bars_of_doubt_single_smoother_rows_have_no_spread(rendered):
    _, _, context, _, _ = _bd_build(rendered)
    csv_df = pd.read_csv(rendered["bd_csv"])
    single = csv_df["n_smoothers_matched"].eq(1)
    assert int(single.sum()) == context["n_single"] == 3
    sub = csv_df[single]
    assert np.allclose(sub["abs_turn_angle_smoother_min"],
                       sub["abs_turn_angle_smoother_max"])
    assert np.allclose(sub["abs_turn_angle_smoother_min"],
                       sub["abs_turn_angle"])
    assert int(csv_df["single_smoother"].astype(str).str.lower()
               .eq("true").sum()) == 3


def test_bars_of_doubt_detector_facts_are_separate_caption_lines(rendered):
    fig, _, _, _, _ = _bd_build(rendered)
    try:
        lines = _bd_caption_lines(fig)
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)

    mae_lines = [i for i, line in enumerate(lines) if "3.1° MAE" in line]
    gate_lines = [i for i, line in enumerate(lines)
                  if "precision 0.915" in line and "recall 0.931" in line]
    assert mae_lines, "the synthetic recovery fact is missing"
    assert gate_lines, "the real-schema gate fact is missing"
    assert not (set(mae_lines) & set(gate_lines)), (
        "the two detector facts must be separate caption lines")

    mae = lines[mae_lines[0]]
    gate = lines[gate_lines[0]]
    assert "48 planted turns" in mae
    assert "notes/phase2-turns.md" in mae
    assert "not the gate" in mae
    assert "three-cone" in gate
    assert "not combined" in gate
    # Neither line may carry the other fact's numbers.
    assert "0.915" not in mae and "0.931" not in mae
    assert "3.1" not in gate

    notes = (ROOT / "notes" / "phase2-turns.md").read_text()
    gate_json = (ROOT / "outputs" / "audit" / "gate.json").read_text()
    assert "**3.1°**" in notes
    for token in ("0.915", "0.931"):
        assert token in notes
        assert token in gate_json


def test_bars_of_doubt_counts_are_printed_on_the_image(rendered):
    fig, table, context, _, _ = _bd_build(rendered)
    try:
        text = _all_text(fig)
        legend_text = " ".join(
            t.get_text() for leg in fig.legends for t in leg.get_texts())
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
    assert f"{context['n_capped']} of {context['n_rows']} rows cut" in text
    assert f"{context['n_crossed']} of {context['n_rows']} turns" in text
    assert f"{context['crossed_share']:.1%}" in text
    assert f"{context['n_single']} rows" in text
    assert f"Combine — {context['by_setting']['combine']} turns" in legend_text
    assert f"Game — {context['by_setting']['game']} turns" in legend_text


def test_bars_of_doubt_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["bd_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["bd_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 13 differ"


def test_bars_of_doubt_leaves_figures_1_to_12_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png", "tc_png", "sw_png", "bh_png", "ss_png", "lg_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 13 must not alter figures 1-12")


def test_bars_of_doubt_manifest_entry(rendered):
    _, _, context, _, _ = _bd_build(rendered)
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][BD_KEY]
    assert fig["figure"] == BD_KEY
    assert fig["stamp"] == STAMP
    assert fig["band"]["position_group"] == "DB"
    assert fig["band"]["abs_turn_angle_deg"] == [30.0, 45.0]
    assert fig["population"]["turns"] == 101
    assert fig["population"]["by_setting"] == {"combine": 4, "game": 97}
    assert fig["population"]["rows_with_single_smoother"] == 3
    assert fig["cap"]["key"] == \
        "figures.bars_of_doubt.max_whisker_width_deg"
    assert fig["cap"]["width_deg"] == 20.0
    assert fig["cap"]["rows_capped"] == context["n_capped"]
    assert fig["crossing"]["rows"] == context["n_crossed"]
    assert np.isclose(fig["crossing"]["share"], context["crossed_share"],
                      atol=1e-12)
    assert fig["detector_evidence"]["stated_separately"] is True
    assert fig["detector_evidence"]["synthetic_recovery"]["angle_mae_deg"] == 3.1
    assert fig["detector_evidence"]["synthetic_recovery"][
        "n_planted_turns"] == 48
    assert fig["detector_evidence"]["real_schema_gate"]["precision"] == 0.915
    assert fig["detector_evidence"]["real_schema_gate"]["recall"] == 0.931
    assert "TWO SEPARATE FACTS" in fig["detector_evidence"]["note"]
    assert fig["png"].endswith("fig_bars_of_doubt.png")
    assert fig["csv"].endswith("fig_bars_of_doubt.csv")


def test_bars_of_doubt_refuses_missing_smoother_columns(rendered):
    from bdb27 import common, figures
    cfg = common.load_config(rendered["cfg"])
    _, _, _, _, turns = _bd_build(rendered)

    broken = turns.drop(columns=["abs_turn_angle_smoother_min"])
    with pytest.raises(common.StageRefused):
        figures.bars_of_doubt_table(broken, cfg)

    empty = turns.copy()
    empty["abs_turn_angle"] = 200.0
    with pytest.raises(common.StageRefused):
        figures.bars_of_doubt_table(empty, cfg)

    only_combine = turns[turns["setting"] == "combine"].copy()
    with pytest.raises(common.StageRefused):
        figures.bars_of_doubt_table(only_combine, cfg)


# ------------------- figure 14: where the Combine's turns come from
SC_KEY = "fig_supply_composition"
SC_BANDS = 7


def _sc_recount(rendered, cfg=None):
    """Per-drill x band counts recomputed from the Parquet under test.

    Nothing is taken from the figure's own table: this is the independent
    count every assertion is checked against.
    """
    from bdb27 import common, figures

    cfg = cfg or common.load_config(rendered["cfg"])
    block = "figures.supply_composition"
    position = str(cfg.get(f"{block}.position_group"))
    edges = [float(x) for x in cfg.get(f"{block}.bands.turn_angle_deg")]
    overflow = str(cfg.get(f"{block}.bands.overflow_label"))
    bands = figures.band_labels(edges, overflow)
    turns = pd.read_parquet(rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    valid = turns["valid"].fillna(False).astype(bool)
    df = turns[valid & (turns["position_group"] == position)
               & (turns["setting"] == "combine")].copy()
    out = {}
    for drill, part in df.groupby("drill_name"):
        counts, players = {}, {}
        for i, band in enumerate(bands):
            lo = edges[i]
            hi = edges[i + 1] if i + 1 < len(edges) else np.inf
            sub = part[(part["abs_turn_angle"] >= lo)
                       & (part["abs_turn_angle"] < hi)]
            counts[band] = int(len(sub))
            players[band] = int(sub["nfl_id"].nunique())
        out[str(drill)] = {
            "turns": int(len(part)),
            "players": int(part["nfl_id"].nunique()),
            "bands": counts,
            "band_players": players,
        }
    return out, len(df), int(df["nfl_id"].nunique())


def _check_sc(csv_df: pd.DataFrame, recount: dict, grand: int) -> None:
    """The CSV must be a within-drill composition of the recounted turns."""
    required = {"drill", "band", "turns", "share_of_drill", "players",
                "drill_turns", "drill_players", "share_start", "share_end",
                "band_index"}
    assert required <= set(csv_df.columns)
    assert set(csv_df["drill"]) == set(recount)
    assert int(csv_df["turns"].sum()) == grand
    for drill, want in recount.items():
        part = csv_df[csv_df["drill"] == drill]
        assert len(part) == SC_BANDS, f"{drill}: {len(part)} band rows"
        assert int(part["turns"].sum()) == want["turns"]
        assert int(part["drill_turns"].iloc[0]) == want["turns"]
        assert int(part["drill_players"].iloc[0]) == want["players"]
        assert np.isclose(float(part["share_of_drill"].sum()), 1.0,
                          atol=1e-12), f"{drill} shares do not sum to 1"
        for _, row in part.iterrows():
            assert int(row["turns"]) == want["bands"][row["band"]]
            assert int(row["players"]) == want["band_players"][row["band"]]
            assert np.isclose(float(row["share_of_drill"]),
                              int(row["turns"]) / want["turns"], atol=1e-12)
        ordered = part.sort_values("band_index")
        assert np.isclose(float(ordered["share_start"].iloc[0]), 0.0)
        assert np.isclose(float(ordered["share_end"].iloc[-1]), 1.0)
        assert np.allclose(ordered["share_start"].to_numpy()[1:],
                           ordered["share_end"].to_numpy()[:-1])


def _sc_build(rendered):
    from bdb27 import common, figures

    cfg = common.load_config(rendered["cfg"])
    turns = figures.load_supply_composition_turns(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.supply_composition_table(turns, cfg)
    return figures.plot_supply_composition(table, context, cfg), table, context, cfg


def _sc_texts(fig) -> str:
    import matplotlib.text

    return " ".join(t.get_text() for t in fig.findobj(matplotlib.text.Text))


def _sc_close(fig) -> None:
    import matplotlib.pyplot as plt

    plt.close(fig)


def test_supply_composition_png_and_csv_exist(rendered):
    _check_png(rendered["sc_png"])
    recount, grand, players = _sc_recount(rendered)
    csv_df = pd.read_csv(rendered["sc_csv"])
    assert len(csv_df) == len(recount) * SC_BANDS
    assert int(csv_df["turns"].sum()) == grand


def test_supply_composition_reconciles_to_the_turn_table(rendered):
    recount, grand, players = _sc_recount(rendered)
    csv_df = pd.read_csv(rendered["sc_csv"])
    _check_sc(csv_df, recount, grand)
    # every valid DB Combine turn is in exactly one drill x band cell
    assert int(csv_df["drill_turns"].unique().sum()) == grand
    assert csv_df["drill"].nunique() == len(recount)


def test_supply_composition_shares_are_within_each_drill(rendered):
    csv_df = pd.read_csv(rendered["sc_csv"])
    for drill, part in csv_df.groupby("drill"):
        assert np.isclose(float(part["share_of_drill"].sum()), 1.0, atol=1e-12), (
            f"{drill} is not a 100% composition")
        assert (part["share_of_drill"] >= 0).all()
        assert (part["share_of_drill"] <= 1).all()
    # no pooled row and no band share of the grand total is stored
    assert not csv_df["drill"].str.lower().isin(["all", "total", "pooled"]).any()


def test_supply_composition_rows_are_sorted_by_drill_total(rendered):
    _, table, context, _ = _sc_build(rendered)
    totals = [d["turns"] for d in context["drills"]]
    assert totals == sorted(totals, reverse=True)
    first_seen = table.drop_duplicates("drill")["drill_turns"].tolist()
    assert first_seen == totals
    assert sum(totals) == context["n_turns"]


def _sc_cells(fig) -> list:
    """The 70 cell patches (tagged in the figure) in draw order."""
    return [p for p in fig.axes[0].patches
            if (p.get_gid() or "").startswith("sc-cell:")]


def _sc_footer_totals(fig) -> list[str]:
    import matplotlib.text

    found = [t for t in fig.findobj(matplotlib.text.Text)
             if (t.get_gid() or "").startswith("sc-footer-total:")]
    return [t.get_text() for t in sorted(found,
                                         key=lambda t: t.get_position()[0])]


def test_supply_composition_prints_drill_and_turn_counts(rendered):
    from bdb27 import figures

    fig, table, context, cfg = _sc_build(rendered)
    try:
        texts = _sc_texts(fig)
        assert f"{context['n_turns']:,} valid DB Combine turns" in texts
        assert f"across {context['n_drills']} drills" in texts
        for drill in context["drills"]:
            assert figures._sc_count(drill["turns"], "turn") in texts, (
                f"{drill['drill']} total not printed on the row label")
            assert figures._sc_count(drill["players"], "player") in texts
        # every cell prints its own count and within-drill share
        for row in table.itertuples(index=False):
            if int(row.turns) > 0:
                assert f"{int(row.turns):,}" in texts
                assert f"{float(row.share_of_drill) * 100:.1f}%" in texts
        assert "All drills" in texts
    finally:
        _sc_close(fig)


def test_supply_composition_draws_a_drill_by_band_matrix(rendered):
    fig, table, context, cfg = _sc_build(rendered)
    try:
        ax = fig.axes[0]
        cells = _sc_cells(fig)
        assert len(cells) == context["n_drills"] * len(context["bands"])
        xs = {round(float(p.get_x() + p.get_width() / 2.0), 6)
              for p in cells}
        ys = {round(float(p.get_y() + p.get_height() / 2.0), 6)
              for p in cells}
        assert len(xs) == len(context["bands"])
        assert len(ys) == context["n_drills"]
        expected = {(context["n_drills"] - 1 - i, c)
                    for i in range(context["n_drills"])
                    for c in range(len(context["bands"]))}
        drawn = {(round(float(p.get_y() + p.get_height() / 2.0)),
                  round(float(p.get_x() + p.get_width() / 2.0)))
                 for p in cells}
        assert drawn == expected
        # exactly one axes: no inset panel
        assert len(fig.axes) == 1
    finally:
        _sc_close(fig)


def test_supply_composition_focus_column_is_filled_red(rendered):
    from matplotlib.colors import to_hex, to_rgba
    from bdb27 import common, figures

    cfg = common.load_config(rendered["cfg"])
    focus_fill = str(cfg.get("figures.supply_composition.focus_fill"))
    fig, table, context, cfg2 = _sc_build(rendered)
    try:
        focus_idx = figures.focus_band_index(
            context["edges"], figures.focus_band(cfg))
        cells = _sc_cells(fig)
        focus_cells = [p for p in cells
                       if p.get_gid().endswith(f":{focus_idx}")]
        assert len(focus_cells) == context["n_drills"]
        for patch in focus_cells:
            assert to_hex(to_rgba(patch.get_facecolor())).upper() == \
                focus_fill.upper()
            assert patch.get_alpha() == 1.0
        labels = fig.axes[0].get_xticklabels()
        assert labels[focus_idx].get_text() == context["bands"][focus_idx]
        assert to_hex(to_rgba(labels[focus_idx].get_color())).upper() == \
            focus_fill.upper()
        assert labels[focus_idx].get_fontweight() in ("bold", 700)
        # no other column is drawn in the focus fill
        others = [p for p in cells
                  if not p.get_gid().endswith(f":{focus_idx}")]
        for patch in others:
            assert to_hex(to_rgba(patch.get_facecolor())).upper() != \
                focus_fill.upper()
    finally:
        _sc_close(fig)


def test_supply_composition_zero_cells_print_a_dot(rendered):
    fig, table, context, cfg = _sc_build(rendered)
    try:
        texts = [t.get_text() for t in fig.findobj()
                 if hasattr(t, "get_text")]
        assert texts.count("·") == int((table["turns"] == 0).sum())
        assert int((table["turns"] == 0).sum()) > 0
        # no cell is blank and no zero cell prints a 0 / 0.0%
        nonzero = int((table["turns"] > 0).sum())
        assert texts.count("0") == 0
        assert not any(t == "0.0%" for t in texts)
        assert nonzero + texts.count("·") == len(table)
    finally:
        _sc_close(fig)


def test_supply_composition_footer_totals_match_the_csv(rendered):
    fig, table, context, cfg = _sc_build(rendered)
    try:
        csv_df = pd.read_csv(rendered["sc_csv"])
        sums = (csv_df.groupby("band_index")["turns"].sum()
                .sort_index().tolist())
        assert [int(x) for x in sums] == [int(x) for x in context["band_totals"]]
        assert _sc_footer_totals(fig) == [f"{int(x):,}" for x in sums]
        shallow_total = int(
            csv_df[csv_df["band"] == context["bands"][0]]["turns"].sum())
        assert shallow_total == context["shallow"]["turns"] == 4
        assert _sc_footer_totals(fig)[0] == "4"
        assert sum(sums) == context["n_turns"]
    finally:
        _sc_close(fig)


def test_supply_composition_never_says_too_small(rendered):
    fig, table, context, cfg = _sc_build(rendered)
    try:
        drawn = _sc_texts(fig).lower()
        assert "too small" not in drawn
        man = json.loads(rendered["manifest"].read_text())
        flat = json.dumps(man["figures"][SC_KEY]).lower()
        assert "too small" not in flat
        source = (ROOT / "src/bdb27/figures.py").read_text(encoding="utf-8")
        assert "too small" not in source.lower()
    finally:
        _sc_close(fig)


def test_supply_composition_rows_sum_to_one(rendered):
    fig, table, context, cfg = _sc_build(rendered)
    try:
        csv_df = pd.read_csv(rendered["sc_csv"])
        for drill, part in table.groupby("drill"):
            assert np.isclose(float(part["share_of_drill"].sum()), 1.0,
                              atol=1e-12), f"{drill} table row is not 100%"
        for drill, part in csv_df.groupby("drill"):
            assert np.isclose(float(part["share_of_drill"].sum()), 1.0,
                              atol=1e-12), f"{drill} CSV row is not 100%"
    finally:
        _sc_close(fig)


def test_supply_composition_shallow_band_is_stated_in_its_column(rendered):
    fig, table, context, cfg = _sc_build(rendered)
    try:
        sh = context["shallow"]
        texts = _sc_texts(fig)
        assert (f"{sh['turns']} turns, one in each of {sh['n_drills']} "
                "drills") in texts
        assert f"{sh['max_share_of_drill']:.1%}" in texts
        for item in sh["by_drill"]:
            assert item["label"] in texts
        assert len(fig.axes) == 1, "no inset panel may be added for the band"
        part = table[table["band"] == context["bands"][0]]
        assert int(part["turns"].sum()) == sh["turns"]
        assert int((part["turns"] > 0).sum()) == sh["n_drills"]
    finally:
        _sc_close(fig)


def test_supply_composition_never_names_a_player(rendered):
    csv_df = pd.read_csv(rendered["sc_csv"])
    for col in csv_df.columns:
        assert "name" not in col.lower(), f"CSV exposes {col!r}"
    fig, _, _, _ = _sc_build(rendered)
    try:
        man = json.loads(rendered["manifest"].read_text())
        flat = json.dumps(man["figures"][SC_KEY]).lower()
        assert "player_name" not in flat
        assert "player_names_used" not in flat or "false" in flat
        assert "display_name" not in flat
    finally:
        _sc_close(fig)


def test_supply_composition_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][SC_KEY]
    assert fig["figure"] == SC_KEY
    assert fig["stamp"] == STAMP
    assert fig["band"]["position_group"] == "DB"
    assert fig["band"]["bands_deg"] == EDGES
    assert fig["shares"]["within_drill"] is True
    assert fig["shares"]["pooled_bar_drawn"] is False
    assert fig["shares"]["band_share_of_all_turns_in_bars"] is False
    assert len(fig["drills"]) == fig["population"]["drills"]
    assert sum(d["turns"] for d in fig["drills"]) == fig["population"]["turns"]
    for drill in fig["drills"]:
        assert np.isclose(sum(b["share_of_drill"] for b in drill["bands"]),
                          1.0, atol=1e-12)
    assert fig["concentration"]["turns"] == sum(
        d["turns"] for d in fig["concentration"]["top_two"])
    assert fig["shallow_band"]["turns"] == sum(
        d["turns"] for d in fig["shallow_band"]["by_drill"])
    assert fig["png"].endswith("fig_supply_composition.png")
    assert fig["csv"].endswith("fig_supply_composition.csv")


def test_supply_composition_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["sc_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["sc_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 14 differ"


def test_supply_composition_leaves_figures_1_to_13_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png", "tc_png", "sw_png", "bh_png", "ss_png", "lg_png",
            "bd_png"]
    before = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
              for k in keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == before[k], (
            f"{k} changed: figure 14 must not alter figures 1-13")


def test_supply_composition_refuses_and_checks_can_fail(rendered):
    from bdb27 import common, figures

    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    recount, grand, players = _sc_recount(rendered)
    good = pd.read_csv(rendered["sc_csv"])
    _check_sc(good, recount, grand)  # sanity: the checker passes on the file

    wrong = good.copy()
    idx = wrong.index[0]
    wrong.loc[idx, "turns"] = int(wrong.loc[idx, "turns"]) + 1
    with pytest.raises(AssertionError):
        _check_sc(wrong, recount, grand)

    bad_share = good.copy()
    bad_share.loc[bad_share.index[0], "share_of_drill"] = 0.5
    with pytest.raises(AssertionError):
        _check_sc(bad_share, recount, grand)

    with pytest.raises(common.StageRefused):
        figures.supply_composition_table(turns.iloc[0:0], cfg)

    only_games = turns[turns["setting"] != "combine"]
    with pytest.raises(common.StageRefused):
        figures.supply_composition_table(only_games, cfg)

    no_drill = turns.copy()
    no_drill.loc[no_drill["setting"] == "combine", "drill_name"] = np.nan
    with pytest.raises(common.StageRefused):
        figures.supply_composition_table(no_drill, cfg)


# ------------------- figure 15: the 30-45 degree band on its own
BO_KEY = "fig_band_only"


def _bo_recount(rendered, cfg=None):
    """The band population recomputed from the Parquet under test."""
    from bdb27 import common

    cfg = cfg or common.load_config(rendered["cfg"])
    block = "figures.band_only"
    position = str(cfg.get(f"{block}.position_group"))
    low, high = (float(x) for x in cfg.get(f"{block}.angle_band_deg"))
    s_low, s_high = (float(x) for x in
                     cfg.get(f"{block}.speed_window_yd_s"))
    turns = pd.read_parquet(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    valid = turns["valid"].fillna(False).astype(bool)
    angle = pd.to_numeric(turns["abs_turn_angle"], errors="coerce")
    entry = pd.to_numeric(turns["entry_speed"], errors="coerce")
    df = turns[valid & (turns["position_group"] == position)
               & (angle >= low) & (angle < high)].copy()
    df["abs_turn_angle"] = angle[df.index]
    df["entry_speed"] = entry[df.index]
    return df, (low, high, s_low, s_high)


def _bo_build(rendered):
    from bdb27 import common, figures

    cfg = common.load_config(rendered["cfg"])
    turns = figures.load_band_only_turns(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    table, context = figures.band_only_table(turns, cfg)
    return figures.plot_band_only(table, context, cfg), table, context, cfg


def test_band_only_png_and_csv_exist(rendered):
    _check_png(rendered["bnd_png"])
    df, _ = _bo_recount(rendered)
    csv_df = pd.read_csv(rendered["bnd_csv"])
    assert len(csv_df) == len(df)
    assert csv_df["setting"].value_counts().to_dict() == \
        df["setting"].value_counts().to_dict()


def test_band_only_one_row_per_turn_and_all_inside_the_band(rendered):
    df, (low, high, _, _) = _bo_recount(rendered)
    csv_df = pd.read_csv(rendered["bnd_csv"])
    assert csv_df["abs_turn_angle"].between(low, high, inclusive="left").all()
    assert (csv_df["abs_turn_angle"] < high).all()
    # one row per turn: compare the multiset of measured coordinates
    want = sorted((str(r.setting), round(float(r.abs_turn_angle), 6),
                   round(float(r.entry_speed), 6))
                  for r in df.itertuples(index=False))
    got = sorted((str(r.setting), round(float(r.abs_turn_angle), 6),
                  round(float(r.entry_speed), 6))
                 for r in csv_df.itertuples(index=False))
    assert got == want
    assert csv_df["row_order"].tolist() == list(range(len(csv_df)))


def test_band_only_two_to_six_counts_match_the_turn_table(rendered):
    df, (_, _, s_low, s_high) = _bo_recount(rendered)
    csv_df = pd.read_csv(rendered["bnd_csv"])
    inside = (df["entry_speed"] >= s_low) & (df["entry_speed"] <= s_high)
    for setting in ("combine", "game"):
        want = int(inside[df["setting"] == setting].sum())
        part = csv_df[csv_df["setting"] == setting]
        assert int(part["in_target_window"].sum()) == want
        assert len(part) == int((df["setting"] == setting).sum())
        # the flag agrees with the measured speed on every row
        assert (part["in_target_window"]
                == part["entry_speed"].between(s_low, s_high)).all()
    # the fixture's Combine turns are all inside its 2-6 window
    comb = csv_df[csv_df["setting"] == "combine"]
    assert comb["in_target_window"].all()


def test_band_only_prints_ranges_and_the_point(rendered):
    fig, table, context, cfg = _bo_build(rendered)
    try:
        texts = " ".join(_sc_texts(fig).split())
        r = context["ranges"]
        assert (f"{r['combine_entry']['min']:.1f}–"
                f"{r['combine_entry']['max']:.1f} yd/s") in texts
        assert (f"{r['game_entry']['min']:.1f}–"
                f"{r['game_entry']['max']:.1f} yd/s") in texts
        assert (f"{context['inside_window']['game']} of "
                f"{context['by_setting']['game']} game turns") in texts
        assert "samples only the slow end" in texts
        for row in context["combine_rows"]:
            assert row["drill_label"] in texts, "a Combine drill is unlabelled"
        assert "not a distribution" in texts
        assert "confidence ellipse" in texts
        assert len(fig.axes) == 1, "the band must be one panel, not a grid"
        ax = fig.axes[0]
        assert len(ax.texts) == context["by_setting"]["combine"] + 1
    finally:
        _sc_close(fig)


def test_band_only_draws_no_curve_fit_box_or_ellipse(rendered):
    import inspect
    from matplotlib.collections import PathCollection
    from matplotlib.patches import Ellipse, Rectangle

    from bdb27 import figures

    for fn in (figures.band_only_table, figures.plot_band_only,
               figures._band_only_caption, figures.band_only_csv_frame,
               figures.band_only_manifest_payload):
        src = inspect.getsource(fn)
        for token in ("gaussian_kde(", "kdeplot", "polyfit", "linregress",
                      "lowess", "Ellipse(", "boxplot(", "violinplot(",
                      "conf_int", "stderr", "ttest"):
            assert token not in src, f"{fn.__name__} references {token!r}"

    fig, table, context, cfg = _bo_build(rendered)
    try:
        ax = fig.axes[0]
        assert len(ax.lines) == 0, "no line artist belongs in this figure"
        assert len(ax.images) == 0, "no raster/density layer may be drawn"
        assert all(isinstance(c, PathCollection) for c in ax.collections)
        assert len(ax.collections) == 2, "one scatter layer per setting"
        assert len(ax.patches) == 1
        assert isinstance(ax.patches[0], Rectangle)
        assert not any(isinstance(p, Ellipse) for p in ax.patches)
        # one marker per turn, at the drawn coordinate
        sizes = [len(c.get_offsets()) for c in ax.collections]
        assert sorted(sizes) == sorted(
            [context["by_setting"]["combine"], context["by_setting"]["game"]])
    finally:
        _sc_close(fig)


def test_band_only_offset_is_recorded_and_moves_no_combine_turn(rendered):
    csv_df = pd.read_csv(rendered["bnd_csv"])
    assert {"x_offset_yd_s", "y_offset_deg", "dodge_pair"} <= set(csv_df.columns)
    assert np.allclose(csv_df["x_draw"],
                       csv_df["entry_speed"] + csv_df["x_offset_yd_s"])
    assert (csv_df["y_draw"] == csv_df["abs_turn_angle"]).all()
    assert (csv_df["y_offset_deg"] == 0.0).all()
    comb = csv_df[csv_df["setting"] == "combine"]
    assert (comb["x_offset_yd_s"] == 0.0).all(), "a Combine dot was moved"
    assert (comb["dodge_pair"] == -1).all()
    moved = csv_df[csv_df["x_offset_yd_s"] != 0.0]
    assert len(moved) % 2 == 0
    assert set(np.round(moved["x_offset_yd_s"].abs().unique(), 6)) <= {0.04}
    # every dodged pair is one +step/2 and one -step/2, same order in the CSV
    for pid, part in moved.groupby("dodge_pair"):
        assert len(part) == 2, f"pair {pid} does not hold two turns"
        assert np.isclose(float(part["x_offset_yd_s"].sum()), 0.0)
    # no offset moved a turn out of the target window
    inside = csv_df["in_target_window"]
    assert csv_df.loc[inside, "x_draw"].between(
        csv_df.loc[inside, "speed_window_low_yd_s"],
        csv_df.loc[inside, "speed_window_high_yd_s"]).all()


def test_band_only_names_drills_not_players(rendered):
    from bdb27 import common

    cfg = common.load_config(rendered["cfg"])
    df, _ = _bo_recount(rendered)
    csv_df = pd.read_csv(rendered["bnd_csv"])
    for col in csv_df.columns:
        assert "name" not in col.lower(), f"CSV exposes {col!r}"
    assert csv_df[csv_df["setting"] == "game"]["drill"].fillna("").eq("").all()
    comb = csv_df[csv_df["setting"] == "combine"]
    assert comb["drill"].notna().all()
    assert set(comb["drill"]) == set(
        df[df["setting"] == "combine"]["drill_name"].astype(str))
    fig, table, context, _ = _bo_build(rendered)
    try:
        texts = _sc_texts(fig)
        for pid in df["nfl_id"].dropna().unique():
            assert str(int(pid)) not in texts
        man = json.loads(rendered["manifest"].read_text())
        flat = json.dumps(man["figures"][BO_KEY]).lower()
        assert "player_name" not in flat
        assert "nfl_id" not in flat
        assert context["combine_players"] == int(
            df[df["setting"] == "combine"]["nfl_id"].nunique())
        sizes = df[df["setting"] == "combine"].groupby("nfl_id").size()
        assert context["combine_turns_in_shared"] == int(
            sum(int(c) for c in sizes if int(c) > 1))
    finally:
        _sc_close(fig)


def test_band_only_manifest_entry(rendered):
    man = json.loads(rendered["manifest"].read_text())
    fig = man["figures"][BO_KEY]
    df, (low, high, s_low, s_high) = _bo_recount(rendered)
    assert fig["figure"] == BO_KEY
    assert fig["stamp"] == STAMP
    assert fig["sources"] == ["outputs/turns/turns.parquet"]
    assert fig["band"]["abs_turn_angle_deg"] == [low, high]
    assert fig["band"]["other_bands_drawn"] is False
    assert fig["target_window"]["entry_speed_yd_s"] == [s_low, s_high]
    assert fig["target_window"]["red_marks_data"] is False
    assert fig["population"]["turns"] == len(df)
    assert fig["population"]["by_setting"] == \
        df["setting"].value_counts().to_dict()
    for setting in ("combine", "game"):
        part = df[df["setting"] == setting]
        entry = fig["entry_speed_yd_s"][setting]
        assert np.isclose(entry["min"], part["entry_speed"].min())
        assert np.isclose(entry["max"], part["entry_speed"].max())
        assert np.isclose(entry["median"], float(np.median(part["entry_speed"])))
    d = fig["drawing"]
    assert d["one_dot_per_turn"] is True
    for key in ("density_curve", "fit", "box_plot", "confidence_ellipse",
                "mean_or_interval", "jitter"):
        assert d[key] is False, f"manifest claims {key}"
    assert d["dodge"]["combine_points_moved"] == 0
    assert d["dodge"]["recorded_per_row"] == ["x_offset_yd_s",
                                              "y_offset_deg"]
    assert any("a list" in line.lower() for line in fig["not_claimed"])
    assert fig["png"].endswith("fig_band_only.png")
    assert fig["csv"].endswith("fig_band_only.csv")


def test_band_only_two_runs_are_byte_identical(rendered):
    before = hashlib.sha256(rendered["bnd_png"].read_bytes()).hexdigest()
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    after = hashlib.sha256(rendered["bnd_png"].read_bytes()).hexdigest()
    assert before == after, "two renders of figure 15 differ"


def test_band_only_leaves_figures_1_to_14_untouched(rendered):
    keys = ["png", "drill_png", "ba_png", "ds_png", "ap_png", "ml_png",
            "gh_png", "tc_png", "sw_png", "bh_png", "ss_png", "lg_png",
            "bd_png", "sc_png"]
    csv_keys = ["csv", "drill_csv", "ba_csv", "ds_csv", "ap_csv", "ml_csv",
                "gh_csv", "tc_csv", "sw_csv", "bh_csv", "ss_csv", "lg_csv",
                "bd_csv", "sc_csv"]
    before_png = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
                  for k in keys}
    before_csv = {k: hashlib.sha256(rendered[k].read_bytes()).hexdigest()
                  for k in csv_keys}
    res = _run(rendered["cfg"])
    assert res.returncode == 0, res.stderr
    for k in keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == \
            before_png[k], f"{k} changed: figure 15 must not alter figures 1-14"
    for k in csv_keys:
        assert hashlib.sha256(rendered[k].read_bytes()).hexdigest() == \
            before_csv[k], f"{k} changed: figure 15 must not alter figures 1-14"


def test_band_only_refuses_and_checks_can_fail(rendered):
    from bdb27 import common, figures

    cfg = common.load_config(rendered["cfg"])
    turns = pd.read_parquet(
        rendered["tmp"] / "outputs" / "turns" / "turns.parquet")
    good = pd.read_csv(rendered["bnd_csv"])
    df, _ = _bo_recount(rendered)

    bad = good.copy()
    bad.loc[bad.index[0], "abs_turn_angle"] = 45.0
    assert not bad["abs_turn_angle"].between(30, 45, inclusive="left").all()

    wrong = good.copy()
    idx = int(wrong.index[wrong["setting"] == "game"][0])
    wrong.loc[idx, "in_target_window"] = not bool(
        wrong.loc[idx, "in_target_window"])
    assert int(wrong[wrong["setting"] == "game"]["in_target_window"].sum()) != \
        int(((df[df["setting"] == "game"]["entry_speed"] >= 2)
             & (df[df["setting"] == "game"]["entry_speed"] <= 6)).sum())

    with pytest.raises(common.StageRefused):
        figures.band_only_table(turns.iloc[0:0], cfg)

    no_speed = turns.drop(columns=["entry_speed"])
    with pytest.raises(common.StageRefused):
        figures.band_only_table(no_speed, cfg)

    only_games = turns[turns["setting"] != "combine"]
    with pytest.raises(common.StageRefused):
        figures.band_only_table(only_games, cfg)
