"""Tests for the derived-column distribution gate (design §5.4a, §15 vocabulary).

``derivation_gate`` guards the four columns the all-22 derivation produces:
the three that have a declared distribution (mean ``n_rushers``, mean
``n_blockers``, ``blitz`` share) and the coverage of all four.  In sample mode
nothing about it is estimable, so every item must read ``PENDING_FULL_RUN`` —
never ``PASS``, never ``FAIL`` — and a full-run ``FAIL`` must reach the gate
verdict as ``NO-GO``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dl import dl_common as D
from dl import dl_models_core as M

#: The four checks §5.4a declares, in the order the gate reports them.
GATE_KEYS = ("derivation_n_rushers_mean", "derivation_n_blockers_mean",
             "derivation_blitz_share", "derivation_na_share")

#: The four columns the derivation writes (design §5.4a).
COLUMNS = ("n_rushers", "blitz", "n_blockers", "primary_blocker_group")


def _block() -> dict:
    return D.dl(D.load_config())


def _column_with_mean(n: int, mean: float) -> np.ndarray:
    """A column whose arithmetic mean is ``mean`` exactly.

    The support is ``[floor(mean), ceil(mean)]``, so a mean anywhere — inside
    the declared range or outside it — is representable without inventing a
    distribution the derivation would never produce.
    """
    low = float(np.floor(mean))
    high = float(np.ceil(mean))
    if high == low:
        return np.full(n, low)
    high_count = int(round((mean - low) * n))
    return np.concatenate([np.full(n - high_count, low),
                           np.full(high_count, high)])


def _derivation_columns(n: int, n_rushers_mean: float = 4.0,
                        n_blockers_mean: float = 5.8, blitz_share: float = 0.31,
                        group_na_share: float = 0.0) -> dict[str, np.ndarray]:
    """The four derived columns, hitting the requested means by construction."""
    group = np.array(["OT", "interior", "TE", "RB"] * (n // 4 + 1))[:n]
    n_group_na = int(round(group_na_share * n))
    group = group.astype(object)
    group[:n_group_na] = np.nan
    return {
        "n_rushers": _column_with_mean(n, n_rushers_mean),
        "n_blockers": _column_with_mean(n, n_blockers_mean),
        "blitz": _column_with_mean(n, blitz_share),
        "primary_blocker_group": group,
    }


def _gate_frame(players: int = 60, snaps: int = 150, icc: float = 0.6,
                events: int = 2000, **derivation) -> tuple[pd.DataFrame, dict]:
    """The §15 gate frame from ``test_dl_models._gate_inputs``, plus the
    derived columns.  Every other stopping condition is satisfied."""
    n = players * snaps
    frame = pd.DataFrame({"nfl_id": np.repeat(np.arange(players), snaps),
                          "a0_icc": float(icc), "event": 0})
    frame.loc[:events - 1, "event"] = 1
    for column, values in _derivation_columns(n, **derivation).items():
        frame[column] = values
    info = {"a0": {"present": True, "non_null": len(frame), "fraction": 1.0},
            "blitz": {"present": True, "non_null": len(frame), "fraction": 1.0}}
    return frame, info


def _audit() -> dict:
    return {"counts": {"null_semantics": {"time_to_pressure": "EVENT_ABSENT"}}}


# --------------------------------------------------------------------------- #
# Sample mode: pending by construction, never pass and never fail
# --------------------------------------------------------------------------- #
def test_sample_mode_is_pending_even_when_the_frame_carries_values():
    frame, _ = _gate_frame()
    gate = M.derivation_gate(_block(), frame, "sample")
    assert set(gate["checks"]) == set(GATE_KEYS)
    for name in GATE_KEYS:
        assert gate["checks"][name]["status"] == "PENDING_FULL_RUN", name
        assert gate["checks"][name]["n"] == len(frame)
    assert gate["status"] == "PENDING"
    assert gate["failing"] == []
    assert set(gate["pending"]) == set(GATE_KEYS)
    assert "1-4 players per play" in gate["statement"]


def test_sample_mode_on_an_all_na_frame_is_still_pending():
    frame, _ = _gate_frame()
    for column in COLUMNS:
        frame[column] = np.nan
    gate = M.derivation_gate(_block(), frame, "sample")
    for name in GATE_KEYS:
        assert gate["checks"][name]["status"] == "PENDING_FULL_RUN", name
    # an empty frame does not turn a sample-mode item into a FAIL either
    empty = frame.iloc[0:0]
    assert all(v["status"] == "PENDING_FULL_RUN"
               for v in M.derivation_gate(_block(), empty, "sample")["checks"].values())


# --------------------------------------------------------------------------- #
# Full mode: the in-range world passes and the verdict is GO
# --------------------------------------------------------------------------- #
def test_full_mode_in_range_frame_passes_every_check_and_the_gate_goes():
    cfg = _block()
    frame, info = _gate_frame()
    dgate = M.derivation_gate(cfg, frame, "full")
    assert dgate["status"] == "OK" and dgate["failing"] == [] and dgate["pending"] == []
    assert all(v["status"] == "PASS" for v in dgate["checks"].values())
    assert dgate["checks"]["derivation_n_rushers_mean"]["value"] == pytest.approx(4.0)
    assert dgate["checks"]["derivation_n_blockers_mean"]["value"] == pytest.approx(5.8)
    assert dgate["checks"]["derivation_blitz_share"]["value"] == pytest.approx(0.31)
    assert dgate["checks"]["derivation_na_share"]["value"] == pytest.approx(0.0)

    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["verdict"] == "GO"
    for name in GATE_KEYS:
        assert gate["checks"][name]["status"] == "PASS"
    assert gate["failing"] == [] and gate["pending"] == []


# --------------------------------------------------------------------------- #
# Full mode: one column out of range at a time is a FAIL and a NO-GO
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kwargs, name", [
    ({"n_rushers_mean": 5.2}, "derivation_n_rushers_mean"),
    ({"n_blockers_mean": 4.0}, "derivation_n_blockers_mean"),
    ({"blitz_share": 0.40}, "derivation_blitz_share"),
])
def test_full_mode_out_of_range_check_fails_and_blocks_the_gate(kwargs, name):
    cfg = _block()
    frame, info = _gate_frame(**kwargs)
    dgate = M.derivation_gate(cfg, frame, "full")
    assert dgate["checks"][name]["status"] == "FAIL"
    assert dgate["failing"] == [name]
    assert dgate["status"] == "BLOCKED"
    # everything else in the derivation still passes, so the FAIL is the cause
    assert [k for k, v in dgate["checks"].items() if v["status"] == "PASS"] == \
        [k for k in GATE_KEYS if k != name]

    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["verdict"] == "NO-GO"
    assert name in gate["failing"]


def test_range_check_boundaries_are_closed():
    bounds = _block()["game"]["derivation_gate"]["n_rushers_mean"]
    assert M._in_closed_range(float(bounds[0]), bounds) is True
    assert M._in_closed_range(float(bounds[1]), bounds) is True
    assert M._in_closed_range(float(bounds[0]) - 0.0001, bounds) is False
    assert M._in_closed_range(float(bounds[1]) + 0.0001, bounds) is False
    assert M._in_closed_range(None, bounds) is None


def test_a_frame_that_sits_exactly_on_both_bounds_passes():
    cfg = _block()
    bounds = cfg["game"]["derivation_gate"]
    frame, _ = _gate_frame(
        n_rushers_mean=float(bounds["n_rushers_mean"][0]),
        n_blockers_mean=float(bounds["n_blockers_mean"][1]),
        blitz_share=float(bounds["blitz_share"][1]))
    dgate = M.derivation_gate(cfg, frame, "full")
    assert dgate["status"] == "OK"
    assert dgate["checks"]["derivation_n_rushers_mean"]["value"] == \
        pytest.approx(float(bounds["n_rushers_mean"][0]))
    assert dgate["checks"]["derivation_n_blockers_mean"]["value"] == \
        pytest.approx(float(bounds["n_blockers_mean"][1]))


# --------------------------------------------------------------------------- #
# Full mode: coverage failures
# --------------------------------------------------------------------------- #
def test_full_mode_all_na_primary_blocker_group_fails_on_na_share():
    cfg = _block()
    frame, info = _gate_frame(group_na_share=1.0)
    dgate = M.derivation_gate(cfg, frame, "full")
    na = dgate["checks"]["derivation_na_share"]
    assert na["status"] == "FAIL"
    # the categorical column is the only one missing, and it is named
    assert na["columns"]["primary_blocker_group"] == pytest.approx(1.0)
    assert na["columns"]["n_rushers"] == pytest.approx(0.0)
    assert "primary_blocker_group" in na["reason"]
    assert na["value"] == pytest.approx(1.0)
    # a coverage failure does not move the three distribution checks
    assert dgate["checks"]["derivation_n_rushers_mean"]["status"] == "PASS"

    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["verdict"] == "NO-GO"
    assert "derivation_na_share" in gate["failing"]


def test_full_mode_a_missing_column_is_a_fail_not_a_pass():
    cfg = _block()
    frame, _ = _gate_frame()
    frame = frame.drop(columns=["n_blockers"])
    dgate = M.derivation_gate(cfg, frame, "full")
    assert dgate["checks"]["derivation_n_blockers_mean"]["status"] == "FAIL"
    assert dgate["checks"]["derivation_n_blockers_mean"]["value"] is None
    assert dgate["checks"]["derivation_na_share"]["columns"]["n_blockers"] == \
        pytest.approx(1.0)
    assert dgate["status"] == "BLOCKED"


def test_full_mode_empty_frame_fails_every_check():
    cfg = _block()
    frame = pd.DataFrame({"nfl_id": [], "a0_icc": [], "event": []})
    dgate = M.derivation_gate(cfg, frame, "full")
    assert [v["status"] for v in dgate["checks"].values()] == ["FAIL"] * len(GATE_KEYS)
    assert dgate["checks"]["derivation_na_share"]["n"] == 0
    assert set(dgate["failing"]) == set(GATE_KEYS)

    valid, info = _gate_frame()
    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=valid,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["verdict"] == "NO-GO"
    assert "derivation_na_share" in gate["failing"]


# --------------------------------------------------------------------------- #
# The gate is unchanged when the caller does not pass the derivation
# --------------------------------------------------------------------------- #
def test_gate_evaluate_without_derivation_is_unchanged():
    cfg = _block()
    frame, info = _gate_frame()
    kwargs = dict(mode="full", field_info=info,
                  p1={"status": "OK", "n_events": 2000}, p2={"status": "OK"},
                  audit_decision=_audit())
    with_columns = M.gate_evaluate(cfg, frame=frame, **kwargs)
    without = M.gate_evaluate(cfg, frame=frame.drop(columns=list(COLUMNS)), **kwargs)

    assert with_columns == without
    assert with_columns["verdict"] == "GO"
    assert not any(k.startswith("derivation_") for k in with_columns["checks"])

    # and a frame with no derived columns is still GO on the same inputs, i.e.
    # the derivation checks are never evaluated behind the caller's back
    assert without["checks"].keys() == with_columns["checks"].keys()


def test_config_keys_are_required_not_defaulted():
    cfg = _block()
    frame, _ = _gate_frame()
    game = dict(cfg["game"])
    del game["derivation_gate"]
    with pytest.raises(KeyError):
        M.derivation_gate({"game": game}, frame, "full")
    partial = {k: v for k, v in cfg["game"]["derivation_gate"].items()
               if k != "max_na_share"}
    with pytest.raises(KeyError):
        M.derivation_gate({"game": {"derivation_gate": partial}}, frame, "full")
