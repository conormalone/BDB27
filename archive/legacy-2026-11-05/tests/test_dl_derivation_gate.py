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

#: The five checks §5.4a declares, in the order the gate reports them.
GATE_KEYS = ("derivation_n_rushers_mean", "derivation_n_blockers_mean",
             "derivation_blitz_share", "derivation_na_share",
             "derivation_side_conflicts")

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
                        group_na_share: float = 0.0,
                        expected_true: bool = True,
                        n_side_conflicts: int = 0) -> dict[str, np.ndarray]:
    """The four derived columns plus the gate-denominator bookkeeping.

    ``derivation_expected`` is True everywhere by default so the distribution
    and coverage checks keep their historical numbers; the mixed-world tests
    set it per row explicitly.
    """
    group = np.array(["OT", "interior", "TE", "RB"] * (n // 4 + 1))[:n]
    n_group_na = int(round(group_na_share * n))
    group = group.astype(object)
    group[:n_group_na] = np.nan
    reason = np.full(n, None, dtype=object)
    reason[:n_side_conflicts] = "side_inference_ambiguous"
    return {
        "n_rushers": _column_with_mean(n, n_rushers_mean),
        "n_blockers": _column_with_mean(n, n_blockers_mean),
        "blitz": _column_with_mean(n, blitz_share),
        "primary_blocker_group": group,
        "derivation_expected": np.full(n, bool(expected_true)),
        "derivation_reason": reason,
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


def test_sample_mode_on_an_all_na_frame_is_not_applicable_not_pending():
    """D78: an all-NA derivation on a prospect-only product is not *pending*.

    ``PENDING_FULL_RUN`` would promise a number the full run cannot produce:
    no play on this tracking product can reach ``all22_min_players``.  The
    honest status is ``NOT_APPLICABLE``, in sample mode as in full mode.
    """
    frame, _ = _gate_frame()
    for column in COLUMNS:
        frame[column] = np.nan
    gate = M.derivation_gate(_block(), frame, "sample")
    for name in GATE_KEYS:
        assert gate["checks"][name]["status"] == "NOT_APPLICABLE", name
        assert "prospects only" in gate["checks"][name]["reason"]
    assert gate["status"] == "NOT_APPLICABLE"
    assert gate["failing"] == [] and gate["pending"] == []
    assert set(gate["not_applicable"]) == set(GATE_KEYS)
    # A zero-row frame cannot establish that the product is prospect-only, so it
    # keeps the vacuity path: pending in sample mode (and FAIL in full mode,
    # see ``test_zero_row_frame_fails_explicitly``).  No empty frame ever passes.
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


# --------------------------------------------------------------------------- #
# The gate denominator is derivation_expected, not every row
# --------------------------------------------------------------------------- #
def _mixed_frame(n: int = 100, n_expected: int = 40, *,
                 expected_na: int = 0,
                 reps: int = 1) -> tuple[pd.DataFrame, dict]:
    """Rusher rows (expected) plus non-rusher rows (not expected).

    Every row carries in-range count columns; only the expected rows carry a
    primary-blocker label, which is what a real snap table looks like.  The
    non-rushers are correct classifications, not missing data.
    """
    frame = pd.DataFrame({
        "nfl_id": np.arange(n),
        "a0_icc": 0.6,
        "event": 0,
        "n_rushers": 4.0,
        "n_blockers": 5.8,
        "blitz": 0.31,
        "primary_blocker_group": np.array(
            ["OT", "interior"] * (n // 2 + 1))[:n].astype(object),
        "derivation_expected": np.array([True] * n_expected
                                        + [False] * (n - n_expected)),
        "derivation_reason": np.array([None] * n, dtype=object),
    })
    # The non-rushers have no label (their counts stay play-level).
    frame.loc[n_expected:, "primary_blocker_group"] = np.nan
    # Optionally break a few expected rows: a rusher whose own derivation failed.
    frame.loc[:expected_na - 1, "primary_blocker_group"] = np.nan
    if reps > 1:
        # The §15 gate also has player-count and median-snap conditions that no
        # 100-row frame can satisfy.  Tiling the same mixed population keeps
        # the derived-check numbers' proportions while making the frame a
        # realistic gate input (distinct players in every tile).
        parts = [frame.copy() for _ in range(reps)]
        frame = pd.concat(parts, ignore_index=True)
    info = {"a0": {"present": True, "non_null": len(frame), "fraction": 1.0},
            "blitz": {"present": True, "non_null": len(frame), "fraction": 1.0}}
    return frame, info


def test_mixed_rusher_non_rusher_world_passes_at_zero_na_share():
    cfg = _block()
    frame, info = _mixed_frame()
    dgate = M.derivation_gate(cfg, frame, "full")
    na = dgate["checks"]["derivation_na_share"]

    assert na["status"] == "PASS"
    assert na["value"] == pytest.approx(0.0)
    assert na["n"] == 40
    assert na["denominator"] == "derivation_expected"
    assert na["coverage"]["n_rows_all"] == 100
    assert na["coverage"]["na_share_all"]["primary_blocker_group"] == \
        pytest.approx(0.6)
    assert na["coverage"]["non_null_all"]["primary_blocker_group"] == 40
    assert na["columns"]["primary_blocker_group"] == pytest.approx(0.0)
    assert na["non_null"]["primary_blocker_group"] == 40

    # A 100-row frame cannot satisfy the gate's player-count / median-snap
    # conditions, so the verdict is checked on the same population tiled to a
    # realistic size (expected share unchanged at 40 %).
    big, big_info = _mixed_frame(reps=150)
    big_gate = M.derivation_gate(cfg, big, "full")
    assert big_gate["checks"]["derivation_na_share"]["value"] == \
        pytest.approx(0.0)
    gate = M.gate_evaluate(cfg, mode="full", field_info=big_info, frame=big,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=big_gate)
    assert gate["verdict"] == "GO"


def test_failed_rusher_derivation_fails():
    cfg = _block()
    frame, info = _mixed_frame(expected_na=1)
    dgate = M.derivation_gate(cfg, frame, "full")
    na = dgate["checks"]["derivation_na_share"]

    assert na["status"] == "FAIL"
    assert na["value"] == pytest.approx(1 / 40)
    assert na["n"] == 40
    assert "primary_blocker_group" in na["reason"]
    # The raw all-row share is still visible and equals (40 + 1) / 100.
    assert na["coverage"]["na_share_all"]["primary_blocker_group"] == \
        pytest.approx(61 / 100)
    assert "derivation_na_share" in dgate["failing"]

    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["verdict"] == "NO-GO"


def test_zero_row_frame_fails_explicitly():
    cfg = _block()
    frame = pd.DataFrame({
        "nfl_id": pd.Series(dtype="int64"),
        "a0_icc": pd.Series(dtype=float),
        "event": pd.Series(dtype="int64"),
        "n_rushers": pd.Series(dtype=float),
        "n_blockers": pd.Series(dtype=float),
        "blitz": pd.Series(dtype=float),
        "primary_blocker_group": pd.Series(dtype=object),
        "derivation_expected": pd.Series(dtype=bool),
        "derivation_reason": pd.Series(dtype=object),
    })
    dgate = M.derivation_gate(cfg, frame, "full")
    na = dgate["checks"]["derivation_na_share"]
    assert na["status"] == "FAIL"
    assert na["n"] == 0
    assert "0 rows" in na["reason"]
    assert [v["status"] for v in dgate["checks"].values()] == \
        ["FAIL"] * len(GATE_KEYS)
    assert set(dgate["failing"]) == set(GATE_KEYS)


def test_empty_expected_set_fails_by_vacuity():
    cfg = _block()
    frame, _ = _mixed_frame()
    frame["derivation_expected"] = False
    dgate = M.derivation_gate(cfg, frame, "full")
    na = dgate["checks"]["derivation_na_share"]
    assert na["status"] == "FAIL"
    assert na["status"] != "PASS"
    assert na["n"] == 0
    assert "vacuity" in na["reason"]
    assert "derivation_na_share" in dgate["failing"]


def test_missing_expected_column_fails():
    cfg = _block()
    frame, _ = _mixed_frame()
    frame = frame.drop(columns=["derivation_expected"])
    dgate = M.derivation_gate(cfg, frame, "full")
    na = dgate["checks"]["derivation_na_share"]
    assert na["status"] == "FAIL"
    assert "denominator is undefined" in na["reason"]


def test_object_dtype_expected_flags_are_parsed_by_value_not_truthiness():
    """A ``derivation_expected`` that arrives as text must not inflate the
    denominator: ``astype(bool)`` would count every non-empty string, ``"False"``
    included, as an expected row (85 of these 100 rows instead of 40)."""
    cfg = _block()
    frame, _ = _mixed_frame(expected_na=1)
    flags = pd.Series(
        ["True"] * 35 + [" true "] * 3 + ["1"] * 2          # 40 true rows
        + ["False"] * 30 + ["false"] * 5 + ["0"] * 5         # false by value
        + [None] * 5 + [np.nan] * 5                          # NA is false
        + ["yes"] * 5 + [""] * 5,                            # other is false
        dtype=object)
    assert len(flags) == len(frame) == 100
    frame["derivation_expected"] = flags
    assert frame["derivation_expected"].dtype == object

    mask = M._expected_mask(frame["derivation_expected"])
    assert mask.dtype == bool
    assert int(mask.sum()) == 40
    assert mask[:40].all() and not mask[40:].any()
    # the same helper on the native forms, unchanged
    assert M._expected_mask(pd.Series([True, False])).tolist() == [True, False]
    assert M._expected_mask(pd.Series([1, 0, np.nan])).tolist() == [True, False, False]

    dgate = M.derivation_gate(cfg, frame, "full")
    na = dgate["checks"]["derivation_na_share"]
    assert na["n"] == 40                       # the 40 "True" rows, no more
    assert na["coverage"]["n_rows_all"] == 100
    assert na["non_null"]["primary_blocker_group"] == 39
    assert na["columns"]["primary_blocker_group"] == pytest.approx(1 / 40)
    assert na["value"] == pytest.approx(1 / 40)
    assert na["coverage"]["na_share_all"]["primary_blocker_group"] == \
        pytest.approx(61 / 100)
    assert na["status"] == "FAIL"               # max_na_share is 0.0
    assert "derivation_na_share" in dgate["failing"]

    # Label every expected row and the same object flag column now passes.
    frame.loc[:39, "primary_blocker_group"] = "OT"
    na = M.derivation_gate(cfg, frame, "full")["checks"]["derivation_na_share"]
    assert na["n"] == 40
    assert na["value"] == pytest.approx(0.0)
    assert na["status"] == "PASS"


# --------------------------------------------------------------------------- #
# The motion-only residual is a hard fail
# --------------------------------------------------------------------------- #
def test_side_conflict_rows_fail_the_gate():
    cfg = _block()
    clean, _ = _mixed_frame()
    assert M.derivation_gate(cfg, clean, "full")[
        "checks"]["derivation_side_conflicts"]["status"] == "PASS"
    assert M.derivation_gate(cfg, clean, "full")[
        "checks"]["derivation_side_conflicts"]["value"] == 0

    frame, _ = _mixed_frame()
    frame.loc[0, "derivation_reason"] = "side_inference_ambiguous"
    check = M.derivation_gate(cfg, frame, "full")[
        "checks"]["derivation_side_conflicts"]
    assert check["status"] == "FAIL"
    assert check["value"] == 1
    assert "1 row(s)" in check["reason"]
    assert "derivation_side_conflicts" in M.derivation_gate(
        cfg, frame, "full")["failing"]

    sample = M.derivation_gate(cfg, frame, "sample")[
        "checks"]["derivation_side_conflicts"]
    assert sample["status"] == "PENDING_FULL_RUN"


# --------------------------------------------------------------------------- #
# D78 — prospect-only product: NOT_APPLICABLE is not a waiver
# --------------------------------------------------------------------------- #
def _full_field_info(cfg: dict, frame: pd.DataFrame) -> dict[str, dict]:
    """Every §8 control + key present with full coverage (a clean field set)."""
    return {f: {"present": True, "non_null": len(frame), "fraction": 1.0}
            for f in M.required_fields(cfg)}


def _all_na_frame() -> tuple[pd.DataFrame, dict]:
    frame, info = _gate_frame()
    for column in COLUMNS:
        frame[column] = np.nan
    frame["derivation_reason"] = "not_applicable_prospect_only"
    frame["derivation_expected"] = False
    return frame, info


def test_prospect_only_in_full_mode_is_not_applicable_and_does_not_block():
    """Direction 1: the dataset cannot support it → NOT_APPLICABLE, gate not blocked."""
    cfg = _block()
    assert cfg["game"]["tracking_product"] == "prospect_only"
    frame, _ = _all_na_frame()
    dgate = M.derivation_gate(cfg, frame, "full")

    assert dgate["status"] == "NOT_APPLICABLE"
    assert dgate["failing"] == [] and dgate["pending"] == []
    assert set(dgate["not_applicable"]) == set(GATE_KEYS)
    for name in GATE_KEYS:
        assert dgate["checks"][name]["status"] == "NOT_APPLICABLE", name
        assert dgate["checks"][name]["reason"] == M._PROSPECT_ONLY_DERIVATION_REASON

    gate = M.gate_evaluate(cfg, mode="full",
                           field_info=_full_field_info(cfg, frame), frame=frame,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["verdict"] == "GO", gate["failing"]
    assert gate["failing"] == [] and gate["pending"] == []
    assert set(gate["not_applicable"]) == set(GATE_KEYS)


def test_all22_product_with_an_all_na_derivation_still_fails():
    """Direction 2: the same frame on an all-22 product is a hard FAIL."""
    cfg = _block()
    cfg["game"]["tracking_product"] = "all22"
    frame, _ = _all_na_frame()
    dgate = M.derivation_gate(cfg, frame, "full")

    assert dgate["status"] == "BLOCKED"
    assert dgate["not_applicable"] == []
    # the four count/coverage checks fail; the motion residual is countable and
    # genuinely zero here (no row carries that reason), so it passes
    assert set(dgate["failing"]) == set(GATE_KEYS) - {"derivation_side_conflicts"}
    assert dgate["checks"]["derivation_n_rushers_mean"]["status"] == "FAIL"
    assert dgate["checks"]["derivation_na_share"]["status"] == "FAIL"

    gate = M.gate_evaluate(cfg, mode="full",
                           field_info=_full_field_info(cfg, frame), frame=frame,
                           p1={"status": "OK", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["verdict"] == "NO-GO"
    assert "derivation_n_rushers_mean" in gate["failing"]


def test_absent_required_control_still_fails_under_a_not_applicable_derivation():
    """The amendment removes three controls; it does not excuse the rest.

    A §8 control that is genuinely required and absent must FAIL even while the
    derivation items read NOT_APPLICABLE — otherwise the amendment would become
    a way to fit a reduced model silently.
    """
    cfg = _block()
    frame, _ = _all_na_frame()
    dgate = M.derivation_gate(cfg, frame, "full")
    assert dgate["status"] == "NOT_APPLICABLE"

    info = _full_field_info(cfg, frame)
    missing = "snaps_in_drive"
    assert missing in info                     # it is part of the §8 list
    info[missing] = {"present": False, "non_null": 0, "fraction": 0.0}

    gate = M.gate_evaluate(cfg, mode="full", field_info=info, frame=frame,
                           p1={"status": "NOT_ESTIMABLE", "n_events": 2000},
                           p2={"status": "OK"}, audit_decision=_audit(),
                           derivation=dgate)
    assert gate["checks"]["missing_required_field"]["status"] == "FAIL"
    assert gate["checks"]["missing_required_field"]["absent"] == [missing]
    assert gate["verdict"] == "NO-GO"
    # the derivation items did not absorb or mask the failure
    assert all(gate["checks"][k]["status"] == "NOT_APPLICABLE" for k in GATE_KEYS)
    assert gate["failing"] == ["missing_required_field"]


def test_the_removed_controls_are_not_in_the_required_field_list():
    """blitz / n_blockers / primary_blocker_group were removed from §8 entirely."""
    cfg = _block()
    fields = set(M.required_fields(cfg))
    for gone in ("blitz", "n_blockers", "primary_blocker_group"):
        assert gone not in fields
        assert gone not in cfg["models"]["controls"]
        assert gone not in cfg["models"]["crossed_fixed_effects"]
    for kept in ("position_group", "alignment", "down", "yards_to_go", "quarter",
                 "score_margin", "seconds_since_last_snap", "snaps_in_drive"):
        assert kept in fields
    for fe in ("team", "season"):
        assert fe in fields
        assert fe in cfg["models"]["crossed_fixed_effects"]


def test_a_prospect_only_declaration_never_overrides_values_in_the_frame():
    """If the frame carries derivations, they are evaluated — data beats the flag."""
    cfg = _block()
    assert cfg["game"]["tracking_product"] == "prospect_only"
    frame, _ = _gate_frame()
    dgate = M.derivation_gate(cfg, frame, "full")
    assert dgate["status"] == "OK"
    assert dgate["not_applicable"] == []
    assert dgate["checks"]["derivation_n_rushers_mean"]["status"] == "PASS"
