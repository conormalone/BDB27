"""The synthetic fixture must round-trip through the real loaders and the
detector must recover the turns that were planted in it.

This is the recovery test TASK.md asks for at this stage (the envelope and
coverage recovery tests belong to the next cycle, when those stages exist).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdb27 import common
from bdb27.kinematics import DetectorParams, detect_turns, smooth_positions

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


@pytest.fixture(scope="module")
def truth() -> dict:
    return json.loads((FIXTURE / "truth.json").read_text())


@pytest.fixture()
def loaded(fixture_config):
    """Load the fixture through the real loader (CSV -> parquet -> pandas)."""
    cfg = common.load_config(fixture_config)
    a = common.ensure_parquet(cfg, "sample", "combine_tracking",
                              cfg.get("columns.combine_tracking"), force=True)
    con = common.connect(cfg)
    try:
        df = con.execute(
            "SELECT nfl_id, event_id, drill_name, attempt, epoch_ms(time) AS ms, "
            "x, y, s, a, dis, dir FROM read_parquet(?)", [str(a)]).df()
    finally:
        con.close()
    return cfg, df.sort_values(["nfl_id", "drill_name", "attempt", "ms"]).reset_index(drop=True)


def _detect(g, cfg, floor=None):
    t = (g.ms.to_numpy(float) - g.ms.iloc[0]) / 1000.0
    spec = {c["name"]: c for c in cfg.get("smoothing.candidates")}[cfg.get("smoothing.primary")]
    xs, ys = smooth_positions(t, g.x.to_numpy(float), g.y.to_numpy(float), spec)
    par = DetectorParams.from_config(cfg.get("detector"),
                                     floor if floor is not None else cfg.get("detector.speed_floor_combine"))
    return detect_turns(t, xs, ys, par)


def test_fixture_loads_through_the_real_loader(loaded):
    cfg, df = loaded
    assert len(df) > 500
    assert set(df.drill_name.unique()) == {"THREE_CONE_DRILL", "SHORT_SHUTTLE"}
    assert df.ms.is_monotonic_increasing is False  # sorted per attempt, not globally
    assert df.nfl_id.nunique() == 6


def test_detector_recovers_planted_shuttle_turns(loaded, truth):
    cfg, df = loaded
    shuttle = df[df.drill_name == "SHORT_SHUTTLE"]
    hits, total = 0, 0
    for (pid, att), g in shuttle.groupby(["nfl_id", "attempt"]):
        g = g.sort_values("ms")
        tr = _detect(g, cfg)
        total += 1
        assert len(tr) >= 2, f"player {pid} attempt {att}: {len(tr)} turns"
        xs = sorted(abs(x["x_centre"]) for x in tr)
        # planted turn lines at |x| = 5 yd, rounded corner puts the centre just inside
        if all(3.0 <= x <= 5.5 for x in xs):
            hits += 1
        assert all(abs(abs(x["turn_angle"]) - 180) < 40 for x in tr), tr
    assert hits == total


def test_detector_recovers_planted_cone_turns(loaded):
    """Every detected turn sits at a planted cone; >=90% of attempts find both.

    The one hard case is the sub-maximal attempt of the lowest-capacity player,
    where the second corner is traversed close to the speed floor. That is a real
    edge case for Phase 2 (not a fixture bug), so it is counted, not hidden.
    """
    cfg, df = loaded
    cone = df[df.drill_name == "THREE_CONE_DRILL"]
    planted = np.array([[5.0, 0.0], [5.0, 5.0]])
    found_both, total = 0, 0
    for (pid, att), g in cone.groupby(["nfl_id", "attempt"]):
        tr = _detect(g.sort_values("ms"), cfg)
        total += 1
        centres = np.array([[x["x_centre"], x["y_centre"]] for x in tr])
        d = np.linalg.norm(centres[:, None, :] - planted[None], axis=2)
        assert d.min(1).max() < 1.6, f"player {pid} attempt {att}: a turn is not at a cone: {centres}"
        found_both += int(d.min(0).max() < 1.6)
    assert found_both / total >= 0.9, f"{found_both}/{total} attempts recovered both planted turns"


def test_submaximal_attempt_is_slower(loaded):
    cfg, df = loaded
    peaks = (df.groupby(["nfl_id", "drill_name", "attempt"]).s.max().unstack())
    # attempt 1 is maximal, attempt 2 is planted at 0.8x
    assert (peaks[1] > peaks[2]).all(), peaks


def test_dropped_frames_are_present(loaded):
    """The fixture plants 5% dropped frames: frame counts must not be a clean grid."""
    cfg, df = loaded
    sizes = df.groupby(["nfl_id", "drill_name", "attempt"]).size()
    assert sizes.nunique() > 1


def test_dis_encoding_matches_the_documented_hypothesis(loaded):
    """dis = (whole yards / 30)^2 — 30*sqrt(dis) must be an integer."""
    cfg, df = loaded
    last = df.groupby(["nfl_id", "drill_name", "attempt"]).dis.max()
    implied = 30 * np.sqrt(last)
    assert np.all(np.abs(implied - np.round(implied)) < 1e-3), implied.head()


def test_fixture_is_deterministic(tmp_path):
    """Regenerating the fixture writes byte-identical files."""
    import subprocess
    import sys

    before = {p.name: p.read_bytes() for p in FIXTURE.glob("*.csv")}
    subprocess.run([sys.executable, str(FIXTURE / "make_fixture.py")],
                   check=True, capture_output=True)
    after = {p.name: p.read_bytes() for p in FIXTURE.glob("*.csv")}
    assert before == after


def test_detector_is_deterministic(loaded):
    cfg, df = loaded
    g = df[(df.drill_name == "SHORT_SHUTTLE") & (df.nfl_id == df.nfl_id.iloc[0])].groupby("attempt").get_group(1)
    a = _detect(g.sort_values("ms"), cfg)
    b = _detect(g.sort_values("ms"), cfg)
    assert a == b
