"""DL stage 05 — the ceiling permutation, and its honesty contract.

What is asserted, and why
------------------------
The permutation is a **null-calibration** device, so the tests fix its two
directions on committed synthetic worlds whose truth is planted:

* the ``absolute`` world plants an ``a0`` hazard ratio of 2.0 and *no* ceiling
  dependence in the load, so shuffling ceilings must destroy the effect and
  leave the permutation null centred on zero;
* the ``null`` world plants no effect at all, so the observed statistic must
  sit inside its own permutation null.

The other Phase-5 checks are stubs.  A stub that silently returned would read
as a passed check, so the tests call each one and demand it raises.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_fixtures as F
from dl import stage_05_validate as V

FIXTURES = D.REPO_ROOT / "fixtures" / "synthetic" / "dl"
SEED = 20270109


def _config(world: str, tmp_path: Path) -> dict:
    cfg = D.load_config()
    return F.world_config(cfg, FIXTURES / world, tmp_path)


def _frame(cfg: dict) -> pd.DataFrame:
    frame, _ = V.load_frame(cfg, "sample")
    return frame


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(V.STUBS))
def test_every_stub_refuses_loudly(name):
    cfg = _config("absolute", Path("/tmp/unused"))
    frame = _frame(cfg)
    with pytest.raises(NotImplementedError) as excinfo:
        V.STUBS[name](frame, cfg)
    message = str(excinfo.value)
    assert "NOT IMPLEMENTED" in message
    # the message must name the check it stands for, not a generic refusal
    normal = message.lower().replace("_", " ").replace("-", " ")
    assert name.replace("_", " ").lower() in normal


def test_unbuilt_checks_are_named_and_reasoned():
    assert "equivalence_tost" in V.UNBUILT_CHECKS
    assert "placebo_pre_snap_features" in V.UNBUILT_CHECKS
    assert "rotation_and_survivorship" in V.UNBUILT_CHECKS
    for reason in V.UNBUILT_CHECKS.values():
        assert len(reason) > 40


# --------------------------------------------------------------------------- #
# The shuffle itself
# --------------------------------------------------------------------------- #
def test_ceiling_shuffle_stays_within_position_group(tmp_path):
    cfg = _config("absolute", tmp_path)
    frame = _frame(cfg)
    groups, report = V.player_groups(frame, cfg)
    table, columns = V._ceiling_table(frame)
    assert columns and "a0" in columns
    assert report["n_unknown"] == 0, "every fixture player carries an alignment"

    rng = np.random.default_rng(SEED)
    permuted, info = V.permute_ceilings(frame, groups, table, rng)
    assert info["n_players"] == frame["nfl_id"].nunique()

    # Every player's new a0 is some player's old a0 from the same group, and
    # the multiset inside each group is preserved (a permutation, not a resample).
    for group in groups.unique():
        members = set(groups[groups == group].index)
        before = sorted(table.loc[sorted(members), "a0"].round(6))
        after = sorted(permuted.drop_duplicates("nfl_id")
                       .set_index("nfl_id").loc[sorted(members), "a0"].round(6))
        assert before == after, f"group {group!r} did not preserve its ceilings"

    # The whole ceiling travels together: a player's vmax/tau must be the donor's.
    origin = table.reset_index().set_index("nfl_id")
    moved = permuted.drop_duplicates("nfl_id").set_index("nfl_id")
    for pid in list(moved.index)[:10]:
        donor_id = float(moved.at[pid, "a0"])
        candidates = origin[origin["a0"].round(6) == round(donor_id, 6)]
        assert len(candidates) >= 1
        donor = candidates.iloc[0]
        assert moved.at[pid, "tau"] == pytest.approx(float(donor["tau"]), rel=1e-9)
        assert moved.at[pid, "vmax"] == pytest.approx(float(donor["vmax"]), rel=1e-9)


def test_singleton_group_keeps_its_own_ceiling(tmp_path):
    cfg = _config("absolute", tmp_path)
    frame = _frame(cfg)
    groups, _ = V.player_groups(frame, cfg)
    # Force one player into a group of his own.
    singleton = groups.index[0]
    groups = groups.copy()
    groups.loc[singleton] = "lonely"
    table, _ = V._ceiling_table(frame)
    permuted, _ = V.permute_ceilings(frame, groups, table,
                                     np.random.default_rng(SEED))
    before = float(table.at[singleton, "a0"])
    after = float(permuted.drop_duplicates("nfl_id").set_index("nfl_id")
                  .at[singleton, "a0"])
    assert after == pytest.approx(before)


# --------------------------------------------------------------------------- #
# Recovery and null calibration
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def permutation_worlds(tmp_path_factory):
    """One permutation per world, computed once for the module."""
    out = {}
    for world, reps in (("absolute", 19), ("null", 9)):
        cfg = _config(world, tmp_path_factory.mktemp(world))
        frame = _frame(cfg)
        out[world] = V.ceiling_permutation(
            frame, cfg, reps=reps, seed=SEED,
            relative_column="relative_load_0p7")
    return out


def test_planted_world_recovers_and_permutation_nulls_it(permutation_worlds):
    result = permutation_worlds["absolute"]
    a0 = result["a0"]
    inf = a0["inference"]
    # (a) the unpermuted refit recovers the planted HR 2.0
    assert a0["status"] == "OK"
    assert a0["hr"] > 1.5, "the planted a0 effect was not recovered"
    assert a0["rung"] == "aft_frailty_lognormal"
    # (b) the permutation null centres on zero
    assert abs(inf["null_mean"]) < 0.15, inf
    assert abs(inf["null_mean"]) < 0.35 * abs(inf["observed"])
    assert inf["null_q025"] < 0 < inf["null_q975"]
    # (c) the observed effect is outside the null: p is small
    assert inf["n_permutations"] == 19
    assert inf["p_two_sided"] <= 0.06, inf
    assert inf["reject_null"] is True


def test_planted_null_world_gives_a_large_p(permutation_worlds):
    result = permutation_worlds["null"]
    a0 = result["a0"]
    inf = a0["inference"]
    assert abs(inf["observed"]) < 0.25, "the null world produced a real a0 effect"
    assert inf["p_two_sided"] > 0.2, inf
    assert inf["reject_null"] is False


def test_permutation_is_deterministic_for_a_fixed_seed(tmp_path):
    cfg = _config("null", tmp_path)
    frame = _frame(cfg)
    kwargs = dict(reps=3, seed=SEED, relative_column="relative_load_0p7")
    first = V.ceiling_permutation(frame, cfg, **kwargs)
    second = V.ceiling_permutation(frame, cfg, **kwargs)
    assert first["rows"] == second["rows"]
    assert first["a0"]["inference"]["null_mean"] == pytest.approx(
        second["a0"]["inference"]["null_mean"], rel=0, abs=0)


# --------------------------------------------------------------------------- #
# Stage contract
# --------------------------------------------------------------------------- #
def test_stage_writes_honest_artefacts(tmp_path):
    cfg = _config("planted", tmp_path)
    cfg["bdb27_dl"]["validate"]["permutations"]["sample_reps"] = 3
    outcome = V.run(cfg, "sample", force=True)
    assert outcome["skipped"] is False
    out = outcome["path"]

    result = json.loads((out / "validation_result.json").read_text())
    assert result["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert result["reportable"] is False
    assert result["verdict"] == "NOT_INTERPRETED_SAMPLE"
    assert result["ceiling_permutation"]["status"] == "OK"
    # The stubs are listed, never passed and never absent.
    for name in V.STUBS:
        assert name in result["not_implemented"]
        assert result["checks"][name]["status"] == "NOT_IMPLEMENTED"
    for name in V.UNBUILT_CHECKS:
        assert name in result["not_implemented"]
        assert result["checks"][name]["status"] == "NOT_IMPLEMENTED"
    assert "PASS" not in {item["status"] for item in result["checks"].values()}

    summary = (out / "SUMMARY.md").read_text()
    assert summary.splitlines()[0].startswith("<!-- provenance: PLACEHOLDER_SAMPLE")
    assert "UNVALIDATED SAMPLE OUTPUT" in summary
    assert "NOT IMPLEMENTED" in summary

    null = pd.read_csv(out / "permutation_null.csv")
    assert set(null["statistic"]) == {"a0_log_hr", "relative_load_coef"}
    assert null["provenance"].str.startswith("PLACEHOLDER_SAMPLE").all()
    assert len(null) == 2 * 3

    for name in ("placebo_checks.csv", "rotation.csv", "context_sensitivity.csv"):
        table = pd.read_csv(out / name)
        assert len(table) == 1
        assert table["status"].iloc[0] == "NOT_IMPLEMENTED"
        assert len(str(table["reason"].iloc[0])) > 40

    equivalence = json.loads((out / "equivalence.json").read_text())
    assert equivalence["status"] == "NOT_IMPLEMENTED"


def test_sample_run_refuses_to_overwrite_a_full_artefact(tmp_path):
    cfg = _config("planted", tmp_path)
    out = V._out_dir(cfg, "sample")
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation_result.json").write_text('{"provenance": "FULL"}')
    with pytest.raises(RuntimeError):
        V.run(cfg, "sample", force=True)


def test_provenance_gate_signature_matches_stage04():
    """The stage contract is stage 04's: check_provenance, run, main."""
    assert callable(V.check_provenance)
    assert callable(V.run)
    assert callable(V.main)
    cfg = D.load_config()
    prov = V.check_provenance(cfg, "sample")
    assert prov["reportable"] is False
