"""Phase 2 stage: real run, synthetic recovery, smoothers and edge cases."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from bdb27 import common
from bdb27.kinematics import DetectorParams, derivatives, smooth_positions, wrap180
from bdb27.turns import Engine, TURN_SCHEMA, attempt_schema, frame_of

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "synthetic"
SRC = ROOT / "src"


def _cfg(tmp_path: Path) -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    b = cfg["turn_coverage"]
    b["paths"]["raw"] = str(FIXTURE)
    b["paths"]["parquet"] = str(tmp_path / "parquet")
    b["paths"]["checkpoints"] = str(tmp_path / "checkpoints")
    b["paths"]["outputs"] = str(tmp_path / "outputs")
    b["paths"]["audit"] = str(tmp_path / "outputs" / "audit")
    b["paths"]["turns"] = str(tmp_path / "outputs" / "turns")
    b["paths"]["thresholds"] = str(tmp_path / "outputs" / "thresholds.json")
    b["paths"]["run_log"] = str(tmp_path / "outputs" / "run_log.csv")
    b["sample_files"] = {k: v for k, v in b["files"].items()}
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return p


def _run(cfgp: Path, *extra):
    return subprocess.run([sys.executable, str(SRC / "02_turns.py"), "--mode", "sample",
                           "--config", str(cfgp), *extra],
                          capture_output=True, text=True, cwd=str(ROOT))


@pytest.fixture(scope="module")
def stage(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("turns")
    cfgp = _cfg(tmp)
    res = _run(cfgp, "--force")
    assert res.returncode == 0, res.stderr
    out = tmp / "outputs" / "turns"
    return {"dir": out, "cfg": common.load_config(cfgp), "cfgpath": cfgp,
            "turns": pd.read_parquet(out / "turns.parquet"),
            "attempts": pd.read_parquet(out / "attempts.parquet"),
            "summary": pd.read_parquet(out / "summary.parquet"),
            "manifest": json.loads((out / "manifest.json").read_text())}


# ------------------------------------------------------------------- real run

def test_stage_runs_and_writes_the_turn_table(stage):
    t = stage["turns"]
    assert len(t) > 0
    assert list(t.columns) == [f.name for f in TURN_SCHEMA]
    assert set(t.setting.unique()) == {"combine", "game"}
    for col in ("entry_speed", "exit_speed", "apex_speed", "turn_angle", "duration_s",
                "path_radius_yd", "peak_lateral_accel_yd_s2", "tangential_accel_entry",
                "tangential_accel_exit", "speed_retention", "regain_entry_speed_s",
                "drill_name", "position_group", "snap_type", "nfl_id", "game_id"):
        assert col in t.columns
    assert stage["manifest"]["provenance"] == "PLACEHOLDER_SAMPLE"
    assert stage["manifest"]["open_decisions"] == ["D124 segmentation rule", "D125 drill geometry model"]
    assert set(stage["manifest"]["geometry_models_reported"]) == {"point", "line"}
    assert (stage["dir"] / "sanity.csv").exists()
    assert (stage["dir"] / "smoother_counts.csv").exists()


def test_context_columns_are_populated(stage):
    t = stage["turns"]
    comb = t[t.setting == "combine"]
    game = t[t.setting == "game"]
    assert comb.drill_name.notna().all() and comb.nfl_id.notna().all()
    assert game.game_id.notna().all() and game.snap_type.notna().all()
    assert game.orientation_divergence_share.notna().all()
    assert comb.orientation_flag.isna().all(), "orientation is a game-only column"


def test_summary_has_drill_and_player_levels(stage):
    s = stage["summary"]
    assert set(s.level.unique()) == {"drill", "player"}
    assert (s.n_turns.fillna(0) >= 0).all()
    drill = s[s.level == "drill"]
    assert (drill.n_units > 0).all()


def test_smoother_spread_is_reported(stage):
    t = stage["turns"]
    assert t.n_smoothers_matched.max() >= 3
    spread = (t.abs_turn_angle_smoother_max - t.abs_turn_angle_smoother_min).dropna()
    assert len(spread) > 0
    assert (spread >= 0).all() and spread.max() > 1.0
    counts = pd.read_csv(stage["dir"] / "smoother_counts.csv")
    assert counts.smoother.nunique() == len(stage["cfg"].get("smoothing.candidates"))


# ------------------------------------------------------- synthetic recovery

def _planted_angle(waypoints, i: int) -> float:
    """Direction change at waypoint i: angle between the incoming and outgoing legs."""
    p0, p1, p2 = (np.asarray(waypoints[i - 1], float), np.asarray(waypoints[i], float),
                  np.asarray(waypoints[i + 1], float))
    u, v = p1 - p0, p2 - p1
    return float(np.degrees(np.arccos(np.clip(
        np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v)), -1, 1))))


def test_recovery_precision_recall_and_angles_on_the_fixture(stage):
    truth = json.loads((FIXTURE / "truth.json").read_text())
    cfg = stage["cfg"]
    radius = float(cfg.get("validation.cone_radius_yd"))
    turns = stage["turns"][stage["turns"].setting == "combine"]
    tp = 0
    total = 0
    covered = 0
    expected = 0
    angle_err = []
    for att in truth["attempts"]:
        sub = turns[(turns.event_id == att["event_id"]) & (turns.attempt == att["attempt"])]
        corners = np.asarray(att["geometry"], float)[1:-1]
        if len(sub):
            pts = sub[["x_centre", "y_centre"]].to_numpy(float)
            d = np.linalg.norm(pts[:, None, :] - corners[None], axis=2)
            near = d.min(1) <= radius
            tp += int(near.sum())
            total += len(sub)
            covered += int((d.min(0) <= radius).sum())
            for k, row in enumerate(sub.itertuples()):
                ci = int(d[k].argmin())
                if d[k, ci] <= radius:
                    angle_err.append(abs(abs(row.turn_angle) - _planted_angle(att["geometry"], ci + 1)))
        expected += len(corners)
    precision = tp / max(total, 1)
    recall = covered / max(expected, 1)
    mae = float(np.mean(angle_err)) if angle_err else float("nan")
    print(f"fixture recovery: precision={precision:.3f} recall={recall:.3f} "
          f"angle_mae={mae:.1f} deg (n={len(angle_err)})")
    assert precision >= float(cfg.get("validation.detector_precision_min"))
    assert recall >= float(cfg.get("validation.detector_recall_min"))
    assert mae <= float(cfg.get("validation.recovery_angle_mae_max_deg"))


def test_submaximal_attempt_is_recovered_and_slower(stage):
    t = stage["turns"][stage["turns"].setting == "combine"]
    peaks = {}
    for (event, attempt), g in t.groupby(["event_id", "attempt"]):
        peaks[(event, attempt)] = g.entry_speed.median()
    checked = 0
    for (event, attempt), speed in peaks.items():
        if attempt != 2:
            continue
        assert (event, 1) in peaks, f"maximal attempt missing for {event}"
        assert speed < peaks[(event, 1)], f"sub-maximal attempt not slower: {event}"
        checked += 1
    assert checked >= 10


def test_dropped_frames_are_flagged_and_interpolated(stage):
    att = stage["attempts"]
    assert (att.interpolated_frames > 0).any(), "fixture drops frames; none were filled"
    t = stage["turns"]
    assert t.gap_in_turn.any(), "a dropped frame inside a turn was not flagged"
    assert (t.interpolated_in_turn > 0).any()
    # gaps under the config limit are repaired and never invalidate the turn
    assert (t.gap_above_limit).sum() == 0


# --------------------------------------------------------------- edge cases

def _engine(setting="combine") -> Engine:
    return Engine.from_config(common.load_config(), setting)


def test_straight_line_has_no_turns():
    t = np.arange(0, 4.0, 0.1)
    e = _engine()
    att, turns = e.analyse(t * 1000, 5.0 * t, np.zeros_like(t),
                           {"drill_name": "X", "event_id": "x", "attempt": 1,
                            "nfl_id": 1, "game_id": None, "play_id": None,
                            "position_group": "DL", "snap_type": None})
    assert turns == []
    assert att["turns_primary"] == 0
    assert att["null_detections"] >= 0


def test_stop_mid_drill_does_not_crash_or_invent_a_giant_turn():
    """Heading is undefined at zero speed; the stage must survive it."""
    dt = 0.1
    t = np.arange(0, 6.0, dt)
    x, y = [], []
    px = py = 0.0
    for ti in t:
        if ti < 2.0:
            v, hd = 6.0, 0.0
        elif ti < 2.6:
            v, hd = 6.0 * (1 - (ti - 2.0) / 0.6), 0.0
        elif ti < 3.6:
            v, hd = 0.0, 45.0
        else:
            v, hd = min(6.0, (ti - 3.6) * 5.0), 90.0
        px += v * np.sin(np.radians(hd)) * dt
        py += v * np.cos(np.radians(hd)) * dt
        x.append(px); y.append(py)
    rng = np.random.default_rng(7)
    x = np.asarray(x) + rng.normal(0, 0.05, len(t))
    y = np.asarray(y) + rng.normal(0, 0.05, len(t))
    e = _engine()
    att, turns = e.analyse(t * 1000, x, y,
                           {"drill_name": "STOP", "event_id": "stop", "attempt": 1,
                            "nfl_id": 2, "game_id": None, "play_id": None,
                            "position_group": "DL", "snap_type": None})
    assert isinstance(att, dict)
    for tr in turns:
        assert np.isfinite(tr["turn_angle"]) and abs(tr["turn_angle"]) <= 360.0


def test_no_game_data_returns_a_well_formed_empty_attempt():
    e = _engine("game")
    att, turns = e.analyse(np.array([]), np.array([]), np.array([]),
                           {"drill_name": None, "event_id": None, "attempt": None,
                            "nfl_id": 3, "game_id": 1, "play_id": 1,
                            "position_group": None, "snap_type": None})
    assert turns == [] and att["turns_primary"] == 0
    df = frame_of([att], attempt_schema(e.smooth_names, e.seg_options))
    assert len(df) == 1


def test_missing_labels_do_not_crash(tmp_path):
    """A game snap with no player_play row has snap_type/alignment absent."""
    t = np.arange(0, 4.0, 0.1)
    x = np.where(t < 2, 6 * t, 12 + 6 * (t - 2))
    y = np.where(t < 2, 0.0, 6 * (t - 2))
    e = _engine("game")
    _, turns = e.analyse(t * 1000, x, y,
                         {"drill_name": None, "event_id": None, "attempt": None,
                          "nfl_id": 4, "game_id": 9, "play_id": 9,
                          "position_group": None, "snap_type": None},
                         o=np.zeros_like(t), dirv=np.degrees(np.arctan2(np.gradient(x), np.gradient(y))) % 360)
    assert turns, "the constructed 90-degree cut should be detected"
    assert all(tr["snap_type"] is None and tr["position_group"] is None for tr in turns)


def test_identical_turns_are_deterministic_ties():
    """Two identical zig-zags: equal scores, deterministic NMS and output."""
    dt = 0.1
    t = np.arange(0, 8.0, dt)
    x, y = [], []
    px = py = 0.0
    hd = 0.0
    for ti in t:
        if 2.0 <= ti < 3.0 or 5.0 <= ti < 6.0:
            hd = min(90.0, hd + 900 * dt)
        elif 3.0 <= ti < 4.0 or 6.0 <= ti < 7.0:
            hd = max(0.0, hd - 900 * dt)
        v = 5.0
        px += v * np.sin(np.radians(hd)) * dt
        py += v * np.cos(np.radians(hd)) * dt
        x.append(px); y.append(py)
    e = _engine()
    ctx = {"drill_name": "ZZ", "event_id": "zz", "attempt": 1, "nfl_id": 5,
           "game_id": None, "play_id": None, "position_group": "DL", "snap_type": None}
    a1, r1 = e.analyse(t * 1000, np.array(x), np.array(y), ctx)
    a2, r2 = e.analyse(t * 1000, np.array(x), np.array(y), ctx)
    assert pd.DataFrame(r1).equals(pd.DataFrame(r2))  # NaN-safe exact comparison
    assert len(r1) >= 2


# ------------------------------------------------------------------ smoothers

def test_each_candidate_smoother_reproduces_known_speed_and_heading(stage):
    cfg = stage["cfg"]
    rng = np.random.default_rng(11)
    t = np.arange(0, 6.0, 0.1)
    v = 5.0
    x = v * t + rng.normal(0, 0.05, len(t))
    y = rng.normal(0, 0.05, len(t))
    mid = slice(len(t) // 4, 3 * len(t) // 4)
    for spec in cfg.get("smoothing.candidates"):
        xs, ys = smooth_positions(t, x, y, spec)
        d = derivatives(t, xs, ys)
        assert abs(float(np.mean(d["speed"][mid])) - v) < 0.15, spec["name"]
        assert float(np.mean(np.abs(wrap180(d["heading"][mid] - 90.0)))) < 4.0, spec["name"]
        assert float(np.std(d["speed"][mid])) < 0.3, spec["name"]


def test_primary_agrees_with_alternatives_on_a_sprint(stage):
    cfg = stage["cfg"]
    rng = np.random.default_rng(12)
    t = np.arange(0, 6.0, 0.1)
    x = 5.0 * t + rng.normal(0, 0.05, len(t))
    y = rng.normal(0, 0.05, len(t))
    specs = {c["name"]: c for c in cfg.get("smoothing.candidates")}
    xp, yp = smooth_positions(t, x, y, specs[cfg.get("smoothing.primary")])
    dp = derivatives(t, xp, yp)
    mid = slice(len(t) // 4, 3 * len(t) // 4)
    tol = float(cfg.get("smoothing.agreement_tolerance_yd_s"))
    for name, spec in specs.items():
        if name == cfg.get("smoothing.primary"):
            continue
        xs, ys = smooth_positions(t, x, y, spec)
        d = derivatives(t, xs, ys)
        assert np.median(np.abs(d["speed"][mid] - dp["speed"][mid])) < tol, name


# --------------------------------------------------------------- determinism

def test_same_seed_gives_byte_identical_turn_table(stage):
    cfgp = stage["cfgpath"]
    path = stage["dir"] / "turns.parquet"
    first = path.read_bytes()
    res = _run(cfgp, "--force")
    assert res.returncode == 0, res.stderr
    assert path.read_bytes() == first


# -------------------------------------------------------------- provenance

def test_full_mode_refuses_placeholder_thresholds(tmp_path):
    cfgp = _cfg(tmp_path)
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "thresholds.json").write_text(json.dumps({"provenance": "PLACEHOLDER_SAMPLE"}))
    res = subprocess.run([sys.executable, str(SRC / "02_turns.py"), "--mode", "full",
                          "--config", str(cfgp)], capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 2
    assert "expected FULL" in res.stderr
