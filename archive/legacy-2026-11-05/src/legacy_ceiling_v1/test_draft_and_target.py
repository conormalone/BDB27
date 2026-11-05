#!/usr/bin/env python3
"""Tests for ``src/09_draft_and_target.py`` — the two exploratory probes (D48).

Coverage is deliberately narrow and behavioural:

* a **planted** group effect is recovered in the target and non-target subsets;
* a **null** fixture comes back flat in every subset;
* a **thin** group is reported and never fitted;
* the target flag has ONE source (the ``efforts.csv`` key join) and a disagreement between the
  join and an in-row value is a hard error;
* the draft summaries/tests are directionally correct on a constructed population;
* the CLI writes the documented artefacts with ``provenance`` first;
* the notebook §10 exists and runs the stage in-notebook (no CLI instructions).
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import common  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DT = _load("_dt09", REPO / "src" / "09_draft_and_target.py")
SM = _load("_sm07_for_dt", REPO / "src" / "07_security_models.py")
SCRIPT = REPO / "src" / "09_draft_and_target.py"
GROUPS = ("moved_down", "high_secure")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cfg_dict(out: Path | None = None, raw: Path | None = None) -> dict:
    cfg = copy.deepcopy(common.load_config())
    cfg.pop("_config_path", None)
    if out is not None:
        cfg["paths"]["outputs"] = str(out)
    if raw is not None:
        cfg["paths"]["raw"] = str(raw)
    cfg["security_models"]["primary"]["attenuation_reps"] = 20
    cfg["draft_target"]["min_group_n"] = 15
    cfg["draft_target"]["min_obs"] = 40
    return cfg


def _row(nid: int, group: str, target: bool, y: float, ceiling: float = 2.4,
         fam: str = "VERTICAL", play: int = 0, pos: str = "WR") -> dict:
    return dict(
        nfl_id=nid, game_id=2024010100 + nid, play_id=nid * 10 + play,
        group=group, moved_down=1.0 if group == "moved_down" else 0.0,
        y=y, log_ceiling=ceiling, log_env_vmax=ceiling, log_peak_speed=ceiling,
        log_speed_at_20yd=ceiling, log_edp_pre=3.0 + (nid % 5) * 0.1,
        edp_pre=20.0 + (nid % 5), route_family=fam, pass_length=5.0 + 5.0 * play,
        snap_share=0.5 + 0.1 * (play % 4), position=pos,
        height=72.0 + (nid % 3), weight=200.0 + (nid % 7),
        years_since_combine=1.0 + (nid % 3), stratum="trend",
        target=target, route_ran=fam,
        draft_overall_pick=20 + (nid % 140), draft_round=1 + (nid % 7),
        draft_band=None, log_draft_pick=math.log(20 + (nid % 140)),
    )


def _planted_rows(n_down: int = 20, n_secure: int = 20, n_eff: int = 4,
                  effect: float = 0.08, seed: int = 5) -> list[dict]:
    """Planted group effect in BOTH target and non-target subsets."""
    rng = np.random.default_rng(seed)
    fams = ("VERTICAL", "BREAK_IN", "BREAK_OUT", "SHORT")
    out: list[dict] = []
    nid = 1000
    for group, n in (("moved_down", n_down), ("high_secure", n_secure)):
        md = 1.0 if group == "moved_down" else 0.0
        for p in range(n):
            for j in range(n_eff):
                ceiling = 2.4 + 0.02 * ((p + j) % 5)
                y = 0.70 * ceiling + 0.50 + effect * md + float(rng.normal(0, 0.010))
                out.append(_row(nid, group, target=(j % 2 == 0), y=y, ceiling=ceiling,
                                fam=fams[j % len(fams)], play=j, pos=("WR" if p % 2 else "TE")))
            nid += 1
    return out


def _fit(rows, cfg, subset=None):
    sub = rows if subset is None else [r for r in rows if r.get("target") is subset]
    return DT.fit_cell(sub, cfg, SM, {}, GROUPS)


# --------------------------------------------------------------------------- #
# 1. planted / null recovery (Part B)
# --------------------------------------------------------------------------- #
def test_planted_effect_is_recovered_in_both_target_subsets():
    cfg = _cfg_dict()
    rows = _planted_rows()
    for subset in (True, False, None):
        rec = _fit(rows, cfg, subset)
        label = "all" if subset is None else str(subset)
        assert rec["thin"] is False, (label, rec["note"])
        assert rec["group_coef"] is not None, (label, rec["note"])
        assert rec["group_coef"] > 0.05, (label, rec["group_coef"], rec["note"])
        assert rec["group_p"] < 0.01, (label, rec["group_p"])
        assert rec["n_players_moved_down"] >= 15 and rec["n_players_high_secure"] >= 15


def test_null_fixture_comes_back_flat_in_every_subset():
    cfg = _cfg_dict()
    rows = _planted_rows(effect=0.0, seed=11)
    for subset in (True, False, None):
        rec = _fit(rows, cfg, subset)
        label = "all" if subset is None else str(subset)
        assert rec["group_coef"] is not None, (label, rec["note"])
        assert abs(rec["group_coef"]) < 0.012, (label, rec["group_coef"])
        assert rec["group_p"] > 0.05, (label, rec["group_p"])


def test_thin_group_is_never_fitted():
    cfg = _cfg_dict()
    rows = _planted_rows(n_down=3, n_secure=30)
    rec = _fit(rows, cfg, True)
    assert rec["thin"] is True
    assert rec["group_coef"] is None and rec["group_p"] is None
    assert rec["n_players_moved_down"] == 3
    assert "min_group_n" in rec["note"]


# --------------------------------------------------------------------------- #
# 2. the target key join is the one source of truth
# --------------------------------------------------------------------------- #
def _write_efforts(path: Path, rows: list[dict]) -> None:
    fields = ["nfl_id", "game_id", "play_id", "target"]
    common.write_csv(path, [{k: r[k] for k in fields} for r in rows], fieldnames=fields)


def test_target_join_matches_the_in_row_flag():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_dt_join_"))
    path = tmp / "efforts.csv"
    rows = [_row(1, "moved_down", True, 2.6, play=0),
            _row(1, "moved_down", False, 2.5, play=1),
            _row(2, "high_secure", False, 2.4, play=0)]
    _write_efforts(path, rows)
    con = common.connect(1)
    try:
        tmap = DT.load_target_map(con, path, SM)
    finally:
        con.close()
    assert tmap[(1, 2024010101, 10)] is True
    assert tmap[(1, 2024010101, 11)] is False
    meta = DT.enrich_targets(rows, tmap, SM)
    assert meta["n_missing"] == 0
    assert all(r["target_join"] == "matched" for r in rows)


def test_target_join_disagreement_is_a_hard_error():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_dt_join_bad_"))
    path = tmp / "efforts.csv"
    # duplicate key with conflicting values -> the map itself refuses
    common.write_csv(path, [
        dict(nfl_id=1, game_id=2, play_id=3, target=True),
        dict(nfl_id=1, game_id=2, play_id=3, target=False),
    ], fieldnames=["nfl_id", "game_id", "play_id", "target"])
    con = common.connect(1)
    try:
        try:
            DT.load_target_map(con, path, SM)
        except ValueError as exc:
            assert "not unique/consistent" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("conflicting duplicate key accepted")
    finally:
        con.close()

    # a join value that disagrees with the row value must also fail
    good = Path(tmp / "good.csv")
    _write_efforts(good, [_row(1, "moved_down", True, 2.5)])
    con = common.connect(1)
    try:
        tmap = DT.load_target_map(con, good, SM)
    finally:
        con.close()
    row = _row(1, "moved_down", False, 2.5)
    try:
        DT.enrich_targets([row], tmap, SM)
    except ValueError as exc:
        assert "target disagrees" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("join/row disagreement accepted")


def test_missing_target_key_is_reported_not_imputed():
    rows = [_row(1, "moved_down", True, 2.5)]
    meta = DT.enrich_targets(rows, {}, SM)
    assert meta["n_missing"] == 1
    assert rows[0]["target"] is None
    assert rows[0]["target_join"] == "missing"


# --------------------------------------------------------------------------- #
# 3. draft summaries / tests are directionally correct
# --------------------------------------------------------------------------- #
def _assign_draft(n_per: int = 20):
    assignments = []
    draft = {}
    for g, picks in (("moved_down", range(100, 100 + n_per)),
                     ("high_secure", range(10, 10 + n_per))):
        for i, pick in enumerate(picks):
            nid = 5000 + (0 if g == "moved_down" else 1000) + i
            assignments.append(dict(nfl_id=nid, group=g, position_group="WR",
                                    edp_pre=float(pick), log_edp_pre=math.log(float(pick)),
                                    edp_trend=(4.0 if g == "moved_down" else 0.0),
                                    stratum="trend"))
            draft[nid] = dict(draft_year=2023, draft_round=min(7, 1 + (pick - 1) // 32),
                              draft_pick_within_round=1 + i, draft_overall_pick=pick)
    return {"T3": {"assignments": assignments}, "T5": {"assignments": assignments}}, draft


def test_draft_group_summary_and_rank_test_show_later_picks_for_moved_down():
    cfg = _cfg_dict()
    assign, draft = _assign_draft()
    rows = DT.draft_group_rows(assign, draft, cfg, GROUPS, 15)
    md = next(r for r in rows if r["panel"] == "T3_primary" and r["scope"] == "pooled"
              and r["group"] == "moved_down")
    hs = next(r for r in rows if r["panel"] == "T3_primary" and r["scope"] == "pooled"
              and r["group"] == "high_secure")
    assert md["n_matched"] == 20 and md["join_rate"] == 1.0
    assert md["median_overall_pick"] > hs["median_overall_pick"]
    assert md["share_R1_2"] == 0.0 and hs["share_R1_2"] == 1.0

    tests = DT.draft_test_rows(assign, draft, cfg, GROUPS)
    rank = next(t for t in tests if t["analysis"] == "overall_pick_rank"
                and t["panel"] == "T3_primary" and t["scope"] == "pooled")
    assert rank["p"] < 0.01 and rank["rank_biserial"] > 0.9
    bands = next(t for t in tests if t["analysis"] == "round_bands"
                 and t["panel"] == "T3_primary" and t["scope"] == "pooled")
    assert bands["p"] < 0.01


def test_draft_spearman_tracks_the_constructed_order():
    assign, draft = _assign_draft()
    rows = DT.draft_spearman_rows(assign, draft)
    for rec in rows:
        assert rec["n_players"] == 40
        assert rec["rho"] is not None and rec["rho"] > 0.99


# --------------------------------------------------------------------------- #
# 4. end-to-end on the committed fixture
# --------------------------------------------------------------------------- #
def _write_fixture_players(raw: Path) -> None:
    """A plausible players.csv keyed to the committed synthetic fixture's nfl_ids."""
    src = REPO / "fixtures" / "synthetic" / "security_game" / "planted" / "security_exposure.csv"
    rows = list(csv.DictReader(src.open(newline="", encoding="utf-8")))
    out = []
    by_pos: dict[str, list[dict]] = {}
    for r in rows:
        by_pos.setdefault(r["position_group"], []).append(r)
    for pos, rs in by_pos.items():
        def key(r):
            try:
                return float(r["edp_pre"])
            except (TypeError, ValueError):
                return 999.0
        rs.sort(key=key)
        for i, r in enumerate(rs):
            rnd = min(7, 1 + i // 32)
            out.append(dict(nfl_id=r["nfl_id"], display_name="fixture", draft_year=2023,
                            nfl_position=pos, birth_date="", college_name="",
                            college_conference="", draft_round=rnd,
                            draft_pick_within_round=1 + i, draft_overall_pick=1 + i))
    fields = ["nfl_id", "display_name", "draft_year", "nfl_position", "birth_date",
              "college_name", "college_conference", "draft_round", "draft_pick_within_round",
              "draft_overall_pick"]
    raw.mkdir(parents=True, exist_ok=True)
    with open(raw / "players.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out)


def _run_fixture(tmp: Path, mode: str = "synthetic") -> Path:
    raw = tmp / "raw"
    _write_fixture_players(raw)
    cfg = _cfg_dict(tmp / "out", raw)
    cfg["paths"]["fixtures"] = str(REPO / "fixtures")
    cfg_path = tmp / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    out = tmp / "out" / "security_models" / "draft_and_target"
    args = [sys.executable, str(SCRIPT), "--config", str(cfg_path), "--out-dir", str(out)]
    args += (["--synthetic", "--synthetic-regime", "planted"] if mode == "synthetic"
             else ["--mode", mode])
    r = subprocess.run(args, cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-4000:]}"
    return out / "synthetic" / "planted" if mode == "synthetic" else out


def test_cli_writes_the_documented_artefacts():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_dt_e2e_"))
    out = _run_fixture(tmp)
    expected = ("draft_by_group.csv", "draft_tests.csv", "draft_conditional_fits.csv",
                "draft_spearman.csv", "target_subset_fits.csv", "target_balance.csv",
                "draft_target.md")
    for name in expected:
        assert (out / name).exists(), f"missing {name}"
    for name in expected[:-1]:
        header = (out / name).read_text(encoding="utf-8").splitlines()[0].split(",")
        assert header[0] == "provenance", f"{name}: first column must be provenance"
    md = (out / "draft_target.md").read_text(encoding="utf-8")
    assert "not a pre-declared test" in md
    assert "POST-Combine" in md and "collider" in md
    assert "target-only is not a clean estimate" in md
    cond = list(csv.DictReader((out / "draft_conditional_fits.csv").open(newline="",
                                                                        encoding="utf-8")))
    assert any(r["model"] == "plus_log_draft_pick" for r in cond)
    assert any(r["model"].startswith("base_within_") for r in cond)
    tgt = list(csv.DictReader((out / "target_subset_fits.csv").open(newline="", encoding="utf-8")))
    assert {r["subset"] for r in tgt} == {"all", "target", "non_target"}
    assert all(r["group_coef"] == "" for r in tgt if r["thin"] == "True")
    assert any(r["group_coef"] != "" for r in tgt)


def test_full_mode_missing_inputs_names_them_and_exits_nonzero():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_dt_missing_"))
    raw = tmp / "raw"
    raw.mkdir()
    cfg = _cfg_dict(tmp / "out", raw)
    cfg["paths"]["fixtures"] = str(REPO / "fixtures")
    cfg_path = tmp / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    out = tmp / "out" / "security_models" / "draft_and_target"
    r = subprocess.run([sys.executable, str(SCRIPT), "--mode", "full", "--config",
                        str(cfg_path), "--out-dir", str(out)],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr[-1000:])
    assert "missing input" in r.stdout.lower()
    assert "players.csv" in r.stdout
    assert not (out / "draft_by_group.csv").exists()


def test_sample_mode_writes_a_labelled_placeholder():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_dt_sample_"))
    raw = tmp / "raw"
    raw.mkdir()
    cfg = _cfg_dict(tmp / "out", raw)
    cfg_path = tmp / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    out = tmp / "out" / "security_models" / "draft_and_target"
    r = subprocess.run([sys.executable, str(SCRIPT), "--mode", "sample", "--config",
                        str(cfg_path), "--out-dir", str(out)],
                       cwd=str(REPO), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    md = (out / "draft_target.md").read_text(encoding="utf-8")
    assert "PENDING FULL RUN" in md and "Missing inputs" in md


# --------------------------------------------------------------------------- #
# 5. notebook §10 contract (static, no execution)
# --------------------------------------------------------------------------- #
def test_notebook_has_an_in_notebook_section_10():
    nb = json.loads((REPO / "notebooks" / "bdb27_full_run.ipynb").read_text(encoding="utf-8"))
    cells = ["".join(c.get("source", [])) for c in nb["cells"]]
    md = next((c for c in cells if c.startswith("## 10.")), None)
    assert md is not None, "§10 markdown heading missing"
    code = "\n".join(cells)
    assert "09_draft_and_target" in code
    assert 'stage_runner.run_stage("09_draft_and_target"' in code
    assert "missing input" in code.lower()
    assert "python src/09_draft_and_target.py" not in code, \
        "§10 must not tell the user to run a CLI command"


if __name__ == "__main__":
    import json  # noqa: F401  (used by the notebook contract test under direct execution)
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
