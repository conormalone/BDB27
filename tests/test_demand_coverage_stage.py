"""Stage 04 — the real demand/coverage stage on synthetic Stage-02/03 worlds.

Every test runs the **actual** stage in a subprocess:
``python src/04_demand_coverage.py --mode sample --config <tmp> --force``.

The turn table and the Stage 03 artefacts are written directly into a temp tree
with the published schemas (rather than re-running stage 03), so each world is
small and the assertion is about this stage's contract, not about a refit. The
bootstrap and coverage draw counts are overridden through the temp config.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
import yaml

from bdb27 import demand_coverage

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

BASE = yaml.safe_load((ROOT / "config.yaml").read_text())["turn_coverage"]
ENTRY_EDGES = [float(x) for x in BASE["bins"]["entry_speed"]]
ANGLE_EDGES = [float(x) for x in BASE["bins"]["turn_angle"]]
N_ANGLE = len(ANGLE_EDGES) - 1
N_CELLS = (len(ENTRY_EDGES) - 1) * N_ANGLE

ENTRY_MID = [0.5 * (ENTRY_EDGES[i] + ENTRY_EDGES[i + 1])
             for i in range(len(ENTRY_EDGES) - 1)]
ANGLE_MID = [0.5 * (ANGLE_EDGES[j] + ANGLE_EDGES[j + 1])
             for j in range(N_ANGLE)]


def cells() -> list[dict]:
    out = []
    for i in range(len(ENTRY_EDGES) - 1):
        for j in range(N_ANGLE):
            out.append({"cell_index": i * N_ANGLE + j,
                        "entry_lo": ENTRY_EDGES[i], "entry_hi": ENTRY_EDGES[i + 1],
                        "angle_lo": ANGLE_EDGES[j], "angle_hi": ANGLE_EDGES[j + 1]})
    return out


CELLS = cells()


def geometry(cell_index: int) -> tuple[float, float]:
    """Entry speed and |turn angle| at the centre of a cell."""
    i, j = divmod(int(cell_index), N_ANGLE)
    return ENTRY_MID[i], ANGLE_MID[j]


# ------------------------------------------------------------------- builders

def turn_row(*, nfl_id, group, snap="PASS_RUSH", alignment="EDGE",
             entry=3.0, angle=45.0, game_id=2023010100, play_id=1,
             valid=True, name=None, position=None):
    return {
        "setting": "game",
        "game_id": float(game_id),
        "play_id": float(play_id),
        "nfl_id": np.int64(nfl_id),
        "player_name": name or f"P{nfl_id}",
        "position": position or group,
        "position_group": group,
        "alignment": alignment,
        "snap_type": snap,
        "entry_speed": np.float32(entry),
        "abs_turn_angle": np.float32(angle),
        "valid": np.bool_(valid),
    }


def turn_in(*, cell_index, **kwargs):
    entry, angle = geometry(cell_index)
    return turn_row(entry=entry, angle=angle, **kwargs)


def default_region(*, inside=None, observed_prefix=12, inside_share=None) -> dict:
    inside = np.zeros(N_CELLS, dtype=bool) if inside is None else np.asarray(inside, bool)
    observed = np.zeros(N_CELLS, dtype=bool)
    observed[:observed_prefix] = True
    if inside_share is None:
        share = inside.astype(float)
    else:
        arr = np.asarray(inside_share, dtype=float)
        share = np.full(N_CELLS, float(arr)) if arr.ndim == 0 else arr
    return {
        "inside_point": inside,
        "observed": observed,
        "inside_share": share,
        "retention_pred": np.where(inside, 1.0, 0.5),
        "retention_p05": np.where(inside, 0.9, 0.4),
        "retention_p95": np.where(inside, 1.0, 0.6),
    }


def write_config(tmp: Path, demand_overrides: dict | None = None) -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    block = cfg["turn_coverage"]
    (tmp / "raw").mkdir(parents=True, exist_ok=True)
    block["paths"]["raw"] = str(tmp / "raw")
    block["paths"]["parquet"] = str(tmp / "parquet")
    block["paths"]["checkpoints"] = str(tmp / "checkpoints")
    block["paths"]["tmp"] = str(tmp / "tmp")
    block["paths"]["outputs"] = str(tmp / "outputs")
    block["paths"]["audit"] = str(tmp / "outputs" / "audit")
    block["paths"]["figures"] = str(tmp / "outputs" / "figures")
    block["paths"]["turns"] = str(tmp / "outputs" / "turns")
    block["paths"]["envelope"] = str(tmp / "outputs" / "envelope")
    block["paths"]["demand_coverage"] = str(tmp / "outputs" / "demand_coverage")
    block["paths"]["run_log"] = str(tmp / "run_log.csv")
    block["paths"]["thresholds"] = str(tmp / "thresholds.json")
    block["paths"]["prereg"] = str(tmp / "prereg.md")
    block["demand"]["bootstrap_reps"] = 10
    block["demand"]["coverage_draws"] = 50
    for key, value in (demand_overrides or {}).items():
        block["demand"][key] = value
    path = tmp / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def write_turns(tmp: Path, rows: list[dict]) -> Path:
    out = tmp / "outputs" / "turns" / "turns.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out, index=False)
    return out


def write_stage03(tmp: Path, players: list[dict], regions: dict[int, dict],
                  variance: list[dict] | None = None) -> Path:
    out = tmp / "outputs" / "envelope"
    out.mkdir(parents=True, exist_ok=True)
    feas = []
    for p in players:
        reg = regions[int(p["nfl_id"])]
        for c in CELLS:
            i = c["cell_index"]
            feas.append({
                "setting": "combine",
                "nfl_id": np.int64(p["nfl_id"]),
                "player_name": p.get("name", f"P{p['nfl_id']}"),
                "position": p.get("position", p["group"]),
                "position_group": p["group"],
                "cell_index": np.int16(i),
                "entry_lo": c["entry_lo"], "entry_hi": c["entry_hi"],
                "angle_lo": c["angle_lo"], "angle_hi": c["angle_hi"],
                "observed": np.bool_(reg["observed"][i]),
                "retention_pred": np.float64(reg["retention_pred"][i]),
                "retention_p05": np.float64(reg["retention_p05"][i]),
                "retention_p95": np.float64(reg["retention_p95"][i]),
                "inside_share": np.float64(reg["inside_share"][i]),
                "inside_point": np.bool_(reg["inside_point"][i]),
            })
    pd.DataFrame(feas).to_parquet(out / "feasible_region.parquet", index=False)

    pl = pd.DataFrame([{
        "setting": "combine",
        "nfl_id": np.int64(p["nfl_id"]),
        "player_name": p.get("name", f"P{p['nfl_id']}"),
        "position": p.get("position", p["group"]),
        "position_group": p["group"],
        "retention_at_boundary": np.bool_(p.get("retention_at_boundary", True)),
        "tau2_at_boundary": np.bool_(p.get("tau2_at_boundary", True)),
        "reliable": np.bool_(p.get("reliable", True)),
    } for p in players])
    pl.to_parquet(out / "envelope_players.parquet", index=False)

    if variance is None:
        groups = sorted({p["group"] for p in players})
        variance = [{"setting": "combine", "position_group": g,
                     "measure": "plant_retention", "tau2_status": "AT_BOUNDARY"}
                    for g in groups]
    pd.DataFrame(variance, columns=demand_coverage.VARIANCE_COLUMNS).to_csv(
        out / "variance_components.csv", index=False)
    return out


def write_labels(tmp: Path, rows: list[dict]) -> Path:
    path = tmp / "raw" / "player_play.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = demand_coverage.LABEL_COLUMNS
    frame = pd.DataFrame([{c: r.get(c) for c in columns} for r in rows],
                         columns=columns)
    frame.to_csv(path, index=False, na_rep="NA")
    return path


def run_stage(cfg_path: Path, mode="sample", force=True) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SRC / "04_demand_coverage.py"), "--mode", mode,
           "--config", str(cfg_path)]
    if force:
        cmd.append("--force")
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))


def run_world(tmp: Path, rows: list[dict], players: list[dict],
              regions: dict[int, dict], *, labels: list[dict] | None = None,
              variance: list[dict] | None = None,
              demand_overrides: dict | None = None) -> dict:
    write_turns(tmp, rows)
    write_stage03(tmp, players, regions, variance)
    if labels is not None:
        write_labels(tmp, labels)
    cfg_path = write_config(tmp, demand_overrides)
    result = run_stage(cfg_path)
    out = tmp / "outputs" / "demand_coverage"
    payload = {
        "tmp": tmp, "cfg": cfg_path, "result": result, "dir": out,
        "stdout": result.stdout, "stderr": result.stderr,
    }
    if result.returncode == 0:
        payload["manifest"] = json.loads((out / "manifest.json").read_text())
        payload["demand"] = pd.read_csv(out / "demand_map.csv")
        payload["coverage"] = pd.read_parquet(out / "coverage_players.parquet")
        payload["ranking"] = pd.read_csv(out / "coverage_position_ranking.csv")
        payload["gaps"] = pd.read_csv(out / "cut_gaps_players.csv")
        payload["gaps_position"] = pd.read_csv(out / "cut_gaps_position.csv")
        payload["card_cells"] = pd.read_parquet(out / "cut_card_cells.parquet")
        payload["card_players"] = pd.read_csv(out / "cut_card_players.csv")
    return payload


def simple_world(*, n_dl=2, n_wr=2, region=None):
    """A minimal DL + WR world with one turn per player in cell 0."""
    players = []
    rows = []
    regions = {}
    for g, n in (("DL", n_dl), ("WR", n_wr)):
        for k in range(n):
            nid = (1000 if g == "DL" else 2000) + k
            snap = "PASS_RUSH" if g == "DL" else "ROUTE"
            align = "EDGE" if g == "DL" else "WR"
            players.append({"nfl_id": nid, "group": g})
            rows.append(turn_in(cell_index=0, nfl_id=nid, group=g, snap=snap,
                                alignment=align, play_id=nid))
            regions[nid] = region or default_region(inside=np.ones(N_CELLS, bool))
    return rows, players, regions


# --------------------------------------------------------------- 1. binning

def test_demand_bins_sum_to_eligible_turn_counts(tmp_path):
    rows = [
        turn_in(cell_index=0, nfl_id=1001, group="DL", play_id=1),
        turn_in(cell_index=7, nfl_id=1001, group="DL", play_id=2),
        turn_in(cell_index=18, nfl_id=1002, group="DL", play_id=3),
        turn_in(cell_index=3, nfl_id=2001, group="WR", snap="ROUTE",
                alignment="WR", play_id=4),
        turn_in(cell_index=5, nfl_id=2001, group="WR", snap="ROUTE",
                alignment="WR", play_id=5),
        turn_row(nfl_id=1001, group="DL", entry=3.0, angle=45.0,
                 valid=False, play_id=6),
        turn_row(nfl_id=1002, group="DL", entry=3.0, angle=220.0, play_id=7),
        turn_row(nfl_id=3001, group="RB", entry=3.0, angle=45.0, play_id=8),
    ]
    players = [{"nfl_id": 1001, "group": "DL"}, {"nfl_id": 1002, "group": "DL"},
               {"nfl_id": 2001, "group": "WR"}]
    regions = {p["nfl_id"]: default_region(inside=np.ones(N_CELLS, bool))
               for p in players}
    stage = run_world(tmp_path, rows, players, regions)

    counts = stage["manifest"]["demand_counts"]
    assert counts["turns_in"] == 8
    assert counts["eligible"] == 5
    assert counts["excluded"]["invalid"] == 1
    assert counts["excluded"]["angle_outside_bins"] == 1
    assert counts["excluded"]["position_not_mapped"] == 1
    assert counts["per_group"]["DL"] == 3
    assert counts["per_group"]["WR"] == 2
    assert counts["sum_frequency_n_turns"] == 5

    freq = stage["demand"][stage["demand"].weight_mode == "frequency"]
    assert int(freq.n_turns.sum()) == 5
    for group, expected in (("DL", 3), ("WR", 2)):
        assert int(freq[freq.position_group == group].n_turns.sum()) == expected


# ------------------------------------------------------- 2. snap restriction

def test_snap_type_restriction_actually_filters(tmp_path):
    rows = [
        turn_row(nfl_id=1001, group="DL", snap="PASS_RUSH", play_id=1),
        turn_row(nfl_id=1001, group="DL", snap="PASS_RUSH", play_id=2),
        turn_row(nfl_id=1001, group="DL", snap="OTHER", play_id=3),
        turn_row(nfl_id=1001, group="DL", snap="COVERAGE", play_id=4),
        turn_row(nfl_id=2001, group="WR", snap="ROUTE", alignment="WR", play_id=5),
        turn_row(nfl_id=2001, group="WR", snap="COVERAGE", alignment="WR", play_id=6),
    ]
    players = [{"nfl_id": 1001, "group": "DL"}, {"nfl_id": 2001, "group": "WR"}]
    regions = {p["nfl_id"]: default_region(inside=np.ones(N_CELLS, bool))
               for p in players}
    stage = run_world(tmp_path, rows, players, regions)

    counts = stage["manifest"]["demand_counts"]
    assert counts["per_group"]["DL"] == 2
    assert counts["per_group"]["WR"] == 1
    assert counts["excluded"]["snap_type_not_relevant"] == 3
    assert stage["manifest"]["snap_types"]["DL"] == ["PASS_RUSH"]


# --------------------------------------------------------- 3. weight modes

def test_weight_modes_differ_only_where_specified(tmp_path):
    # Four DL reps in four cells; WR has none of the outcome machinery.
    rows = [
        turn_in(cell_index=0, nfl_id=1001, group="DL", play_id=1, game_id=1),
        turn_in(cell_index=6, nfl_id=1001, group="DL", play_id=2, game_id=2),
        turn_in(cell_index=7, nfl_id=1002, group="DL", play_id=3, game_id=3),
        turn_in(cell_index=18, nfl_id=1002, group="DL", play_id=4, game_id=4),
        turn_in(cell_index=0, nfl_id=2001, group="WR", snap="ROUTE",
                alignment="WR", play_id=5, game_id=5),
    ]
    players = [{"nfl_id": 1001, "group": "DL"}, {"nfl_id": 1002, "group": "DL"},
               {"nfl_id": 2001, "group": "WR"}]
    regions = {p["nfl_id"]: default_region(inside=np.ones(N_CELLS, bool))
               for p in players}
    labels = [
        # rep 1: quick pressure, unblocked -> excluded in the sensitivity mode
        {"game_id": 1, "play_id": 1, "nfl_id": 1001, "quick_pressure": True,
         "unblocked_pressure": True, "time_to_pressure": 1.0},
        # rep 2: not flagged but a short time to pressure counts as qualifying
        {"game_id": 2, "play_id": 2, "nfl_id": 1001, "quick_pressure": False,
         "unblocked_pressure": False, "time_to_pressure": 2.0},
        # rep 3: slow pressure -> not qualifying
        {"game_id": 3, "play_id": 3, "nfl_id": 1002, "quick_pressure": False,
         "unblocked_pressure": False, "time_to_pressure": 4.0},
        # rep 4: present row, both labels null -> false (D101), not qualifying
        {"game_id": 4, "play_id": 4, "nfl_id": 1002, "quick_pressure": None,
         "unblocked_pressure": None, "time_to_pressure": None},
        {"game_id": 5, "play_id": 5, "nfl_id": 2001, "quick_pressure": True,
         "unblocked_pressure": False, "time_to_pressure": 1.0},
    ]
    stage = run_world(tmp_path, rows, players, regions, labels=labels)
    demand = stage["demand"]

    freq = demand[demand.weight_mode == "frequency"]
    assert len(freq) == len(stage["manifest"]["positions"]) * 36
    assert float(freq[freq.position_group == "DL"].weight.sum()) == 4.0
    assert float(freq[freq.position_group == "WR"].weight.sum()) == 1.0
    assert float(freq[freq.position_group == "TE"].weight.sum()) == 0.0

    outcome = demand[demand.weight_mode == "outcome_quick_pressure"]
    assert set(outcome.position_group) == {"DL"}          # outcome map is DL only
    assert float(outcome.weight.sum()) == 2.0             # reps 1 and 2
    assert int(outcome.n_turns_weighted.sum()) == 2

    excl = demand[demand.weight_mode == "outcome_quick_pressure_excl_unblocked"]
    assert float(excl.weight.sum()) == 1.0                # rep 1 is unblocked
    assert int(excl.n_turns_weighted.sum()) == 1

    counts = stage["manifest"]["outcome_weighting"]["counts"]
    assert counts["qualifying_turns_in_scope"] == 2
    assert counts["null_quick_pressure_on_scope_rows"] == 1
    assert counts["reps_missing_label_in_scope"] == 0


# ------------------------------------------------- 4. labels absent, honest

def test_outcome_weight_not_estimable_when_labels_absent(tmp_path):
    rows, players, regions = simple_world()
    stage = run_world(tmp_path, rows, players, regions, labels=None)

    assert stage["result"].returncode == 0, stage["stderr"]
    manifest = stage["manifest"]
    outcome = manifest["outcome_weighting"]
    assert outcome["status"] == "NOT_ESTIMABLE"
    assert outcome["reason"] == "label_file_absent"

    demand = stage["demand"]
    freq = demand[demand.weight_mode == "frequency"]
    assert len(freq) == 5 * 36                      # the frequency map is delivered
    dl_outcome = demand[(demand.position_group == "DL")
                        & (demand.weight_mode == "outcome_quick_pressure")]
    assert len(dl_outcome) == 36
    assert not dl_outcome.estimable.any()
    assert dl_outcome.status.eq("NOT_ESTIMABLE").all()
    assert dl_outcome.weight.isna().all()
    assert dl_outcome.weight_share.isna().all()


# ------------------------------------------------ 5. boundary groups share

def test_boundary_group_coverage_equals_group_value(tmp_path):
    region = default_region(inside=np.ones(N_CELLS, bool), inside_share=0.9)
    rows, players, regions = simple_world(n_dl=2, n_wr=0, region=region)
    players = [{"nfl_id": p["nfl_id"], "group": "DL",
                "retention_at_boundary": True} for p in players]
    regions = {p["nfl_id"]: region for p in players}
    stage = run_world(tmp_path, rows, players, regions)

    coverage = stage["coverage"].sort_values("nfl_id")
    assert len(coverage) == 2
    assert bool(coverage.coverage_shared_group.all())
    assert bool((coverage.shared_reason == "tau2_at_boundary").all())
    assert int(coverage.n_distinct_regions_in_group.iloc[0]) == 1
    assert stage["manifest"]["coverage"]["all_group_level"] is True
    assert stage["manifest"]["coverage"]["n_shared_groups"] == 1

    for column in ("coverage_combined", "coverage_observed",
                   "coverage_extrapolated", "coverage_combined_p05",
                   "coverage_combined_p95", "coverage_observed_p05",
                   "coverage_observed_p95"):
        values = coverage[column].to_numpy(dtype=float)
        if np.isnan(values).all():
            continue
        assert not np.isnan(values).any()
        assert np.ptp(values) < 1e-12, f"{column} fabricated a player spread"
    assert coverage.coverage_combined.iloc[0] == pytest.approx(1.0)


# ------------------------------------------- 6. observed/extrapolated split

def test_observed_and_extrapolated_split_is_honoured(tmp_path):
    inside = np.zeros(N_CELLS, bool)
    inside[0:6] = True
    region = default_region(inside=inside, observed_prefix=12)

    # Demand only in observed cells -> extrapolated has no mass.
    rows = [turn_in(cell_index=0, nfl_id=1001, group="DL", play_id=1)]
    players = [{"nfl_id": 1001, "group": "DL"}]
    only_observed = run_world(tmp_path / "observed", rows, players,
                              {1001: region})
    coverage = only_observed["coverage"].iloc[0]
    assert float(coverage.coverage_observed) == pytest.approx(1.0)
    assert pd.isna(coverage.coverage_extrapolated)
    assert coverage.status_extrapolated == "NO_DEMAND_MASS"

    # Mixed: 3 turns in an observed inside cell, 1 in an observed outside cell,
    # 2 in an extrapolated outside cell.
    rows = [
        turn_in(cell_index=0, nfl_id=1001, group="DL", play_id=1),
        turn_in(cell_index=0, nfl_id=1001, group="DL", play_id=2),
        turn_in(cell_index=0, nfl_id=1001, group="DL", play_id=3),
        turn_in(cell_index=6, nfl_id=1001, group="DL", play_id=4),
        turn_in(cell_index=18, nfl_id=1001, group="DL", play_id=5),
        turn_in(cell_index=18, nfl_id=1001, group="DL", play_id=6),
    ]
    mixed = run_world(tmp_path / "mixed", rows, players, {1001: region})
    coverage = mixed["coverage"].iloc[0]
    assert float(coverage.coverage_combined) == pytest.approx(3 / 6)
    assert float(coverage.coverage_observed) == pytest.approx(3 / 4)
    assert float(coverage.coverage_extrapolated) == pytest.approx(0 / 2)
    assert float(coverage.demand_mass) == pytest.approx(6.0)
    assert float(coverage.demand_mass_observed) == pytest.approx(4.0)
    assert float(coverage.demand_mass_extrapolated) == pytest.approx(2.0)
    # Transparent expected values use inside_share, not the point indicator.
    assert float(coverage.coverage_expected_combined) == pytest.approx(3 / 6)


# ------------------------------------------------------- 7. determinism

def test_bootstrap_intervals_deterministic_under_fixed_seed(tmp_path):
    rows = []
    for k in range(3):
        rows.append(turn_in(cell_index=k, nfl_id=1001 + k, group="DL",
                            play_id=10 + k))
        rows.append(turn_in(cell_index=0, nfl_id=1001 + k, group="DL",
                            play_id=20 + k))
    players = [{"nfl_id": 1001 + k, "group": "DL"} for k in range(3)]
    regions = {p["nfl_id"]: default_region(inside=np.ones(N_CELLS, bool),
                                           inside_share=0.8)
               for p in players}
    stage = run_world(tmp_path, rows, players, regions)
    out = stage["dir"]
    first = {p.name: p.read_bytes() for p in out.iterdir() if p.name != "manifest.json"}
    again = run_stage(stage["cfg"])
    assert again.returncode == 0, again.stderr
    second = {p.name: p.read_bytes() for p in out.iterdir() if p.name != "manifest.json"}
    assert first == second
    assert stage["manifest"]["bootstrap"]["unit"] == "player"
    assert int(stage["manifest"]["bootstrap"]["reps"]) == 10


# --------------------------------------------------- 8. must-fail schema guard

def test_must_fail_schema_guard(tmp_path):
    rows = [turn_in(cell_index=0, nfl_id=1001, group="DL")]
    write_turns(tmp_path, rows)
    # Remove a required column after writing, exactly as a corrupt upstream
    # artefact would.
    path = tmp_path / "outputs" / "turns" / "turns.parquet"
    frame = pd.read_parquet(path).drop(columns=["abs_turn_angle"])
    frame.to_parquet(path, index=False)
    write_stage03(tmp_path, [{"nfl_id": 1001, "group": "DL"}],
                  {1001: default_region(inside=np.ones(N_CELLS, bool))})
    cfg_path = write_config(tmp_path)
    result = run_stage(cfg_path)

    assert result.returncode != 0
    text = result.stderr + result.stdout
    assert "abs_turn_angle" in text
    assert "REFUSED" in text


# ------------------------------------------------------ 9. full-mode refusal

def test_full_mode_refuses_without_prereg_and_full_thresholds(tmp_path):
    rows, players, regions = simple_world()
    write_turns(tmp_path, rows)
    write_stage03(tmp_path, players, regions)
    cfg_path = write_config(tmp_path)
    result = run_stage(cfg_path, mode="full")

    assert result.returncode == 2
    text = result.stderr + result.stdout
    assert "REFUSED" in text
    assert "thresholds" in text
    assert "prereg" in text
    assert not (tmp_path / "outputs" / "demand_coverage").exists()


# ------------------------------------------------ 10. schema/provenance

def test_schema_and_provenance_contract(tmp_path):
    rows, players, regions = simple_world()
    stage = run_world(tmp_path, rows, players, regions)
    out = stage["dir"]

    for name in ("demand_map.csv", "coverage_position_ranking.csv",
                 "cut_gaps_players.csv", "cut_gaps_position.csv",
                 "cut_card_players.csv"):
        frame = pd.read_csv(out / name)
        assert list(frame.columns[:3]) == ["provenance", "label", "config_sha"]
        assert frame["label"].eq("UNVALIDATED SAMPLE OUTPUT").all()
        assert frame["provenance"].eq("PLACEHOLDER_SAMPLE").all()

    for name in ("coverage_players.parquet", "cut_card_cells.parquet"):
        table = pq.read_table(out / name)
        metadata = table.schema.metadata or {}
        for key in (b"provenance", b"label", b"config_sha"):
            assert key in metadata

    text = (out / "manifest.json").read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    manifest = json.loads(
        text, parse_constant=lambda c: pytest.fail(f"non-JSON constant {c!r}"))
    assert manifest["provenance"] == "PLACEHOLDER_SAMPLE"
    assert manifest["label"] == "UNVALIDATED SAMPLE OUTPUT"
    assert manifest["artefact_version"] == demand_coverage.ARTEFACT_VERSION
    for key in ("inputs", "bins", "snap_types", "exploratory_positions",
                "weight_modes", "outcome_weighting", "demand_counts",
                "bootstrap", "coverage", "cut_gaps", "cut_cards",
                "limitations", "warnings", "open_decisions", "primary_group"):
        assert key in manifest
    assert manifest["open_decisions"] == ["D124", "D125"]
    assert manifest["coverage"]["joint_bootstrap_draws"] is False
    assert manifest["coverage"]["interval_method"] == \
        "marginal_bernoulli_over_stage03_inside_share"
    assert manifest["cut_cards"]["rendering"] == "deferred_to_stage_06_figures"
    assert any("tracked players only" in item
               for item in manifest["limitations"])

    coverage = stage["coverage"]
    assert list(coverage.columns) == [f.name for f in demand_coverage.COVERAGE_SCHEMA]


# ------------------------------------------------------------ 11. cut gaps

def test_cut_gaps_rank_high_demand_low_feasibility(tmp_path):
    share = np.ones(N_CELLS)
    share[0] = 0.1     # high demand, low feasibility -> top gap
    share[6] = 0.9     # high demand, feasible -> not a gap
    share[7] = 0.2     # low demand, low feasibility
    region = default_region(inside=np.ones(N_CELLS, bool), inside_share=share)
    rows = (
        [turn_in(cell_index=0, nfl_id=1001, group="DL", play_id=i)
         for i in range(5)]
        + [turn_in(cell_index=6, nfl_id=1001, group="DL", play_id=10 + i)
           for i in range(3)]
        + [turn_in(cell_index=7, nfl_id=1001, group="DL", play_id=20)]
    )
    players = [{"nfl_id": 1001, "group": "DL"}]
    stage = run_world(tmp_path, rows, players, {1001: region},
                     demand_overrides={"cut_gap_top_n": 2})

    gaps = stage["gaps"].sort_values("gap_rank")
    assert list(gaps.cell_index) == [0, 7]
    top = gaps.iloc[0]
    assert float(top.demand_weight) == pytest.approx(5.0)
    assert float(top.demand_weight_share) == pytest.approx(5 / 9)
    assert float(top.inside_share) == pytest.approx(0.1)
    assert float(top.gap_score) == pytest.approx((5 / 9) * 0.9)
    assert 6 not in set(gaps.cell_index)
    assert int(stage["coverage"].iloc[0].n_cut_gaps) == 2
    # The position aggregate names the same most common cell.
    pos = stage["gaps_position"]
    assert pos[pos.role == "all"].iloc[0].cell_index == 0


# ------------------------------------------------------- 12. cut card data

def test_cut_card_data_is_small_and_rendering_deferred(tmp_path):
    rows, players, regions = simple_world(n_dl=2, n_wr=2)
    # add a TE so every configured card group has a candidate
    players.append({"nfl_id": 3001, "group": "TE"})
    rows.append(turn_in(cell_index=0, nfl_id=3001, group="TE", snap="ROUTE",
                        alignment="TE", play_id=99))
    regions[3001] = default_region(inside=np.ones(N_CELLS, bool))
    stage = run_world(tmp_path, rows, players, regions)

    cards = stage["card_players"]
    assert len(cards) <= 3
    assert cards["rendering"].eq("deferred_to_stage_06_figures").all()
    assert stage["manifest"]["cut_cards"]["rendering"] == \
        "deferred_to_stage_06_figures"
    assert int(stage["manifest"]["cut_cards"]["n_cards"]) == len(cards)
    assert len(stage["card_cells"]) == 36 * len(cards)
    assert set(stage["card_cells"].card_id) == set(cards.card_id)
    images = [p for p in stage["dir"].iterdir()
              if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg", ".pdf")]
    assert images == []


# ------------------------------------------------------------ 13. cell grid

def test_cell_grid_matches_config_bins(tmp_path):
    rows, players, regions = simple_world()
    stage = run_world(tmp_path, rows, players, regions)
    freq = (stage["demand"][stage["demand"].weight_mode == "frequency"]
            .drop_duplicates("cell_index").sort_values("cell_index"))
    assert len(freq) == N_CELLS == 36
    assert list(freq.cell_index.astype(int)) == list(range(N_CELLS))
    assert freq.entry_lo.to_numpy(dtype=float).min() == ENTRY_EDGES[0]
    assert freq.entry_hi.to_numpy(dtype=float).max() == ENTRY_EDGES[-1]
    assert freq.angle_lo.to_numpy(dtype=float).min() == ANGLE_EDGES[0]
    assert freq.angle_hi.to_numpy(dtype=float).max() == ANGLE_EDGES[-1]
    assert sorted(set(freq.entry_lo)) == ENTRY_EDGES[:-1]
    assert sorted(set(freq.entry_hi)) == ENTRY_EDGES[1:]
    assert sorted(set(freq.angle_lo)) == ANGLE_EDGES[:-1]
    assert sorted(set(freq.angle_hi)) == ANGLE_EDGES[1:]
    bins = stage["manifest"]["bins"]
    assert bins["entry_speed"] == ENTRY_EDGES
    assert bins["turn_angle"] == ANGLE_EDGES
    assert bins["n_cells"] == 36
