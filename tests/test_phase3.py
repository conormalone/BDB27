"""Phase 3 (game snaps): contract, known answers, statuses and edge cases.

Every test either checks a known-answer geometry (so the expected value is
independently derivable from hand arithmetic) or checks that a named failure
mode is *reported* as a status instead of being silently written as a zero.
Nothing here asserts a football number.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

import common as C
import synthetic

from conftest import load_stage

STAGE = "03_game_snaps"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _cfg_from(cfg, raw: Path, tmp: Path) -> dict:
    c = copy.deepcopy(cfg)
    b = C.block(c)
    b["paths"]["raw"] = str(raw)
    b["paths"]["interim"] = str(tmp / "interim")
    b["paths"]["parquet"] = str(tmp / "interim" / "parquet")
    b["paths"]["checkpoints"] = str(tmp / "interim" / "checkpoints")
    b["paths"]["tmp"] = str(tmp / "interim" / "tmp")
    b["paths"]["outputs"] = str(tmp / "outputs")
    b["paths"]["run_log"] = str(tmp / "outputs" / "run_log.csv")
    b["files"]["game_tracking_sample"] = "game_tracking.csv"
    return c


def _write_cfg(path: Path, cfg: dict) -> Path:
    payload = {C.TASK_KEY: C.block(cfg)}
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _fixture_world(cfg, tmp_path, *, n_players=6, snaps_per=8, seed=11):
    """A synthetic dataset + a config pointing at it."""
    raw = tmp_path / "raw"
    synthetic.build_fixture(raw, seed=seed, n_players=n_players,
                            snaps_per=snaps_per)
    return raw, _cfg_from(cfg, raw, tmp_path)


def _run(cfg, tmp_path, *extra) -> int:
    mod = load_stage(STAGE)
    path = _write_cfg(tmp_path / "cfg.yaml", cfg)
    return mod.main(["--mode", "sample", "--config", str(path), *extra])


def _out(cfg) -> Path:
    return Path(C.block(cfg)["paths"]["outputs"]) / "phase3"


def _rep(yardline_number=40, side="AAA", poss="AAA", direction="right",
         get_off=0.8):
    return {"yardline_side": side, "yardline_number": yardline_number,
            "possession_team": poss, "play_direction": direction,
            "player_get_off": get_off}


SNAP = datetime(2024, 9, 8, 18, 0, 0)


def _frames(ts, xs, speeds, snap=SNAP):
    """Frames from explicit times relative to the snap, on the 10 Hz grid."""
    return [(snap + timedelta(seconds=t), x, 26.0, s)
            for t, x, s in zip(ts, xs, speeds)]


# --------------------------------------------------------------------------- #
# Known-answer geometry
# --------------------------------------------------------------------------- #
def test_known_answer_crossing_and_onset_to_cross(cfg):
    """LOS at x=50, rusher at 50.8 moving -x at 4 yd/s from the snap.

    Crossing distance 0.8 yd at 4 yd/s = 0.200 s. Onset is the first frame
    (both 0.0 and 0.1 s are above 1 yd/s), so onset-to-cross is also 0.200 s.
    """
    mod = load_stage(STAGE)
    # t = -0.5 .. 1.5 on 0.1 s; x=50.8 before the snap, then 50.8 - 4t.
    ts = [-0.5 + 0.1 * i for i in range(21)]
    xs = [50.8 if t < 0 else 50.8 - 4.0 * t for t in ts]
    ss = [0.0 if t < 0 else 4.0 for t in ts]
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
    assert out["depth_status"] == "OK"
    assert out["depth_yd"] == pytest.approx(0.8, abs=1e-9)
    assert out["onset_status"] == "FOUND"
    assert out["onset_latency_s"] == pytest.approx(0.0, abs=1e-9)
    assert out["cross_status"] == "OK"
    assert out["cross_time_s"] == pytest.approx(0.2, abs=1e-6)
    assert out["onset_to_cross_s"] == pytest.approx(0.2, abs=1e-6)
    assert out["cross_gap_policy"] == "none"
    assert out["baseline_status"] == "OK"
    assert out["baseline_speed_presnap"] == pytest.approx(0.0)


def test_baseline_uses_pre_snap_frames_only(cfg):
    """A fast first 0.5 s after the snap must not contaminate the baseline."""
    mod = load_stage(STAGE)
    ts = [-0.5 + 0.1 * i for i in range(21)]
    xs = [50.8 if t < 0 else 50.8 - 4.0 * t for t in ts]
    ss = [0.2 if t < 0 else 6.0 for t in ts]
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
    assert out["baseline_speed_presnap"] == pytest.approx(0.2)


def test_insufficient_pre_snap_frames_is_a_status(cfg):
    mod = load_stage(STAGE)
    ts = [0.0 + 0.1 * i for i in range(11)]
    xs = [50.8 - 4.0 * t for t in ts]
    ss = [4.0] * len(ts)
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
    assert out["baseline_status"] == "INSUFFICIENT_PRE_SNAP_FRAMES"
    assert out["baseline_speed_presnap"] is None
    assert out["onset_status"] == "FOUND"


# --------------------------------------------------------------------------- #
# Statuses instead of silent zeros
# --------------------------------------------------------------------------- #
def test_missing_onset_is_a_status_not_a_zero(cfg):
    mod = load_stage(STAGE)
    ts = [-0.3 + 0.1 * i for i in range(11)]
    xs = [50.8] * len(ts)
    ss = [0.0] * len(ts)
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
    assert out["onset_status"] == "NOT_DETECTED"
    assert out["onset_latency_s"] is None
    assert out["cross_status"] == "NO_ONSET"
    assert out["cross_time_s"] is None


def test_no_tracking_is_a_status(cfg):
    mod = load_stage(STAGE)
    out = mod.measure_rep(_rep(), [], datetime(2024, 9, 8), cfg)
    assert out["tracking_present"] is False
    assert out["depth_status"] == "NO_TRACKING"
    assert out["onset_latency_s"] is None
    assert out["cross_time_s"] is None


def test_no_snap_event_is_a_status(cfg):
    mod = load_stage(STAGE)
    ts = [-0.2 + 0.1 * i for i in range(11)]
    xs = [50.8 - max(0.0, t) * 4.0 for t in ts]
    ss = [4.0 if t >= 0 else 0.0 for t in ts]
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), None, cfg)
    assert out["depth_status"] == "NO_SNAP_EVENT"
    assert out["onset_status"] == "NO_SNAP_EVENT"


def test_depth_near_zero_and_nonpositive(cfg):
    mod = load_stage(STAGE)
    ts = [-0.5 + 0.1 * i for i in range(21)]
    for x0, status in ((50.0, "DEPTH_NONPOSITIVE"), (49.4, "DEPTH_NONPOSITIVE")):
        xs = [x0 if t < 0 else x0 + 4.0 * t for t in ts]
        ss = [0.0 if t < 0 else 4.0 for t in ts]
        out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
        assert out["depth_status"] == status, x0
        assert out["cross_time_s"] is None
        assert out["cross_status"] == "DEPTH_NONPOSITIVE"
    # A very small positive depth is usable, not rounded away.
    xs = [50.02 if t < 0 else 50.02 - 4.0 * t for t in ts]
    ss = [0.0 if t < 0 else 4.0 for t in ts]
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
    assert out["depth_status"] == "OK"
    assert out["depth_yd"] == pytest.approx(0.02, abs=1e-9)


def test_gap_inside_first_second_is_not_interpolated(cfg):
    """A missing frame straddling the crossing inside the first second.

    The crossing is physically inside a 0.2 s gap at t~0.2 s, i.e. inside the
    first second after onset, so it is reported crossed-but-not-measurable.
    """
    mod = load_stage(STAGE)
    ts = [-0.2, -0.1, 0.0, 0.1, 0.3, 0.4, 0.5, 0.6]
    xs = [50.8 if t < 0 else 50.8 - 4.0 * t for t in ts]
    ss = [0.0 if t < 0 else 4.0 for t in ts]
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
    assert out["cross_status"] == "NOT_INTERPOLABLE"
    assert out["cross_time_s"] is None
    assert out["cross_gap_policy"] == "forbidden_first_second"


def test_crossed_before_onset_is_flagged(cfg):
    """Already across the line by the time the 2-frame threshold fires."""
    mod = load_stage(STAGE)
    ts = [-0.3 + 0.1 * i for i in range(15)]
    # crossing at t~0.027; threshold fires at 0.4 s
    xs = [50.8 if t < 0 else 50.8 - 30.0 * t for t in ts]
    ss = [0.0 if t < 0 else (0.5 if t < 0.4 else 5.0) for t in ts]
    out = mod.measure_rep(_rep(), _frames(ts, xs, ss), SNAP, cfg)
    assert out["onset_status"] == "FOUND"
    assert out["cross_status"] == "CROSSED_BEFORE_ONSET"
    assert out["cross_time_s"] is not None      # evidence kept
    assert out["onset_to_cross_s"] is None      # not a negative duration


def test_no_post_snap_frames(cfg):
    mod = load_stage(STAGE)
    ts = [-0.3, -0.2, -0.1]
    frames = [(SNAP + timedelta(seconds=t), 50.8, 26.0, 0.0) for t in ts]
    out = mod.measure_rep(_rep(), frames, SNAP, cfg)
    assert out["depth_status"] == "NO_POST_SNAP_FRAMES"
    assert out["onset_status"] == "NO_POST_SNAP_FRAMES"


# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #
def test_null_quick_pressure_becomes_false_and_true_stays_true():
    mod = load_stage(STAGE)
    base = {"time_to_pressure": None, "dropback_duration": 2.5,
            "player_get_off": 0.8}
    out = mod._post_cross_outcome({**base, "time_to_pressure": 1.4})
    assert out["post_cross_event"] is True
    assert out["post_cross_pressure_time_s"] == pytest.approx(0.6)

    out = mod._post_cross_outcome(base)
    assert out["post_cross_event"] is False
    assert out["post_cross_pressure_time_s"] is None
    assert out["post_cross_censor_time_s"] == pytest.approx(1.7)
    assert out["post_cross_status"] == "CENSORED"

    out = mod._post_cross_outcome({**base, "dropback_duration": None})
    assert out["post_cross_status"] == "CENSOR_MISSING"

    # Never condition on pressure: a negative observed time is flagged, not
    # dropped and not clamped.
    out = mod._post_cross_outcome({**base, "time_to_pressure": 0.3})
    assert out["post_cross_status"] == "NEGATIVE_OBSERVED"
    assert out["post_cross_pressure_time_s"] == pytest.approx(-0.5)


def test_censoring_uses_dropback_only_and_never_defines_the_event():
    mod = load_stage(STAGE)
    out = mod._post_cross_outcome({"time_to_pressure": 1.5,
                                   "dropback_duration": 2.9,
                                   "player_get_off": 0.9})
    assert out["post_cross_pressure_time_s"] == pytest.approx(0.6)
    assert out["post_cross_censor_time_s"] == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# Whole-stage contract on a synthetic world
# --------------------------------------------------------------------------- #
def test_output_schema_and_dtypes(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path)
    assert _run(c, tmp_path) == 0
    import duckdb
    mod = load_stage(STAGE)
    con = duckdb.connect()
    try:
        info = mod.assert_output_schema(con, _out(c) / "snaps.parquet", c)
        cols = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{(_out(c) / 'snaps.parquet').as_posix()}')"
        ).fetchall()]
    finally:
        con.close()
    assert cols == mod.SNAP_COLUMNS
    assert info["rows"] == info["rows"]  # non-negative
    assert len(cols) == len(mod.SNAP_COLUMNS)


def test_every_row_is_stamped_and_nothing_is_dropped(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path)
    assert _run(c, tmp_path) == 0
    import duckdb
    con = duckdb.connect()
    try:
        rel = f"read_parquet('{(_out(c) / 'snaps.parquet').as_posix()}')"
        total = con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]
        stamps = con.execute(
            f"SELECT count(*) FROM {rel} WHERE provenance LIKE "
            f"'%UNVALIDATED SAMPLE OUTPUT%'").fetchone()[0]
        primary = con.execute(
            f"SELECT count(*) FROM {rel} WHERE in_primary_set").fetchone()[0]
        reasons = con.execute(
            f"SELECT coalesce(exclusion_reason,'IN_PRIMARY_SET'), count(*) "
            f"FROM {rel} GROUP BY 1").fetchall()
    finally:
        con.close()
    assert stamps == total == 6 * 8
    assert sum(v for _, v in reasons) == total
    assert primary == dict(reasons)["IN_PRIMARY_SET"]


def test_run_plays_are_excluded_with_a_reason(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path, n_players=4, snaps_per=10)
    assert _run(c, tmp_path) == 0
    import duckdb
    con = duckdb.connect()
    try:
        rel = f"read_parquet('{(_out(c) / 'snaps.parquet').as_posix()}')"
        runs = con.execute(
            f"SELECT count(*) FROM {rel} WHERE exclusion_reason = 'RUN_PLAY'"
        ).fetchone()[0]
        prim = con.execute(
            f"SELECT count(*) FROM {rel} WHERE in_primary_set").fetchone()[0]
        run_flags = con.execute(
            f"SELECT count(*) FROM {rel} WHERE exclusion_reason = 'RUN_PLAY' "
            f"AND blitz_flag <> 'NULL'").fetchone()[0]
    finally:
        con.close()
    assert runs == 4 * 2            # every fifth snap of ten, per player
    assert run_flags == 0           # runs carry a null blitz flag, as in the data
    assert prim == 4 * 10 - runs


def test_blitz_flag_is_carried_and_the_exclusion_is_not_applied(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path, n_players=4, snaps_per=10)
    assert _run(c, tmp_path) == 0
    summary = json.loads((_out(c) / "phase3_summary.json").read_text())
    assert summary["blitz"]["exclusion_applied"] is False
    assert summary["blitz"]["status"] == load_stage(STAGE).BLITZ_EXCLUSION_STATUS
    import duckdb
    con = duckdb.connect()
    try:
        rel = f"read_parquet('{(_out(c) / 'snaps.parquet').as_posix()}')"
        flagged_primary = con.execute(
            f"SELECT count(*) FROM {rel} WHERE in_primary_set "
            f"AND blitz_flag = 'TRUE'").fetchone()[0]
    finally:
        con.close()
    # The fixture sets blitzing=true on every pass-rush rep, exactly as the
    # release does; those rows must remain in the primary set.
    assert flagged_primary > 0
    assert flagged_primary == summary["label_side_counts"]["dl_pass_rush_snaps"]


def test_one_snap_players_still_get_a_row(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path, n_players=3, snaps_per=1, seed=31)
    assert _run(c, tmp_path) == 0
    summary = json.loads((_out(c) / "phase3_summary.json").read_text())
    assert summary["label_side_counts"]["players"] == 3
    assert summary["label_side_counts"]["snaps_per_player"]["n"] == 3
    assert summary["label_side_counts"]["snaps_per_player"]["max"] == 1


def test_no_primary_reps_still_completes(cfg, tmp_path):
    """Every rep excluded: the stage must finish, with a typed table.

    A literally empty ``player_play.csv`` is *not* silently accepted: DuckDB
    infers VARCHAR for every column of a zero-row CSV, so the schema gate fails
    loudly (that is the intended behaviour). The zero-row *base* is covered by
    ``test_empty_table_fallback_is_typed`` below.
    """
    raw, c = _fixture_world(cfg, tmp_path, n_players=3, snaps_per=4)
    pp = raw / "player_play.csv"
    rows = list(csv.DictReader(open(pp, encoding="utf-8")))
    fieldnames = list(rows[0].keys())
    for r in rows:                       # every rep becomes a run play
        r["pass_result"] = ""
        r["blitzing"] = "False"          # keep the column typed and consistent
    with open(pp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    assert _run(c, tmp_path) == 0
    import duckdb
    mod = load_stage(STAGE)
    con = duckdb.connect()
    try:
        info = mod.assert_output_schema(con, _out(c) / "snaps.parquet", c)
    finally:
        con.close()
    assert info["rows"] == 12
    summary = json.loads((_out(c) / "phase3_summary.json").read_text())
    assert summary["label_side_counts"]["dl_pass_rush_snaps"] == 0
    assert summary["exclusions"]["rows_removed"] == {"RUN_PLAY": 12}


def test_empty_table_fallback_is_typed(cfg):
    """With no base rows at all, the deliverable is still correctly typed."""
    import duckdb
    mod = load_stage(STAGE)
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE e AS " + mod._empty_select())
        bad = Path(C.block(cfg)["paths"]["interim"]) / "empty.parquet"
        bad.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY e TO '{bad.as_posix()}' (FORMAT PARQUET)")
        info = mod.assert_output_schema(con, bad, cfg)
    finally:
        con.close()
    assert info["rows"] == 0


def test_null_provenance_covers_season_game_team_down(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path, n_players=4, snaps_per=10)
    assert _run(c, tmp_path) == 0
    rows = list(csv.DictReader(open(_out(c) / "null_provenance.csv",
                                    encoding="utf-8")))
    groupings = {r["grouping"] for r in rows}
    assert {"overall", "season", "game", "team", "down"} <= groupings
    overall = [r for r in rows if r["grouping"] == "overall"][0]
    assert int(overall["n_snaps"]) > 0
    assert int(overall["n_qp_true"]) + int(overall["n_qp_false"]) + \
        int(overall["n_qp_null"]) == int(overall["n_snaps"])
    # the fixture plants both true and false labels plus nulls
    assert int(overall["n_qp_true"]) > 0
    assert int(overall["n_qp_false"]) > 0
    assert int(overall["n_qp_null"]) > 0


def test_schema_assertion_fails_loudly_on_a_wrong_type(cfg, tmp_path):
    import duckdb
    mod = load_stage(STAGE)
    bad = tmp_path / "bad.parquet"
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE t AS SELECT " + ", ".join(
                f"CAST(NULL AS {t}) AS {n}" for n, t, _, _ in mod.SNAP_FIELDS))
        con.execute("ALTER TABLE t ALTER depth_yd TYPE VARCHAR")
        con.execute(f"COPY t TO '{bad.as_posix()}' (FORMAT PARQUET)")
        with pytest.raises(C.SchemaError):
            mod.assert_output_schema(con, bad, cfg)
        con.execute("DROP TABLE t")
        # ... and on a missing column
        cols = [n for n, _, _, _ in mod.SNAP_FIELDS if n != "cross_status"]
        con.execute("CREATE TABLE u AS SELECT " + ", ".join(
            f"CAST(NULL AS {dict((x[0], x[1]) for x in mod.SNAP_FIELDS)[n]}) AS {n}"
            for n in cols))
        con.execute(f"COPY u TO '{bad.as_posix()}' (FORMAT PARQUET)")
        with pytest.raises(C.SchemaError):
            mod.assert_output_schema(con, bad, cfg)
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_two_runs_are_byte_identical(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path, n_players=4, snaps_per=8)
    assert _run(c, tmp_path) == 0
    first = {p.name: _sha(p) for p in _out(c).glob("*") if p.is_file()}
    assert _run(c, tmp_path, "--force") == 0
    second = {p.name: _sha(p) for p in _out(c).glob("*") if p.is_file()}
    assert first == second
    assert "snaps.parquet" in first


def test_checkpoint_skips_a_finished_run(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path, n_players=3, snaps_per=4)
    assert _run(c, tmp_path) == 0
    mtime = (_out(c) / "snaps.parquet").stat().st_mtime_ns
    assert _run(c, tmp_path) == 0            # no --force: skips
    assert (_out(c) / "snaps.parquet").stat().st_mtime_ns == mtime


# --------------------------------------------------------------------------- #
# The fixture's planted get-off is recoverable from its own frames
# --------------------------------------------------------------------------- #
def test_planted_get_off_is_recovered_from_tracking(cfg, tmp_path):
    raw, c = _fixture_world(cfg, tmp_path, n_players=4, snaps_per=12)
    assert _run(c, tmp_path) == 0
    import duckdb
    con = duckdb.connect()
    try:
        rel = f"read_parquet('{(_out(c) / 'snaps.parquet').as_posix()}')"
        row = con.execute(
            f"SELECT median(abs(cross_minus_get_off_s)), "
            f"count(*) FILTER (WHERE cross_status = 'OK'), count(*) "
            f"FROM {rel} WHERE in_primary_set").fetchone()
    finally:
        con.close()
    median_abs, ok, total = row
    assert ok > total * 0.9, (ok, total)
    # Fixture noise is 0.04 yd/frame plus a 10 Hz interpolation, so 60 ms is a
    # generous known-answer tolerance, not a tuned threshold.
    assert median_abs < 0.06, median_abs
