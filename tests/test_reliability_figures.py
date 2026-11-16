"""Pins for the band-reliability figures (D207).

Binding spec: ``notes/spec-reliability-figures.md``. The numbers here are pins,
not illustrations:

* **the share is the ICC** — ``sigma2_a / (sigma2_a + sigma2_e)`` reproduces the
  reported ``icc`` on every real scenario, and (for the adjusted rows) on both
  adjusted samples to 1e-12;
* **§3.1 is additive only** — the pre-existing values of ``scenarios[]``,
  ``adjustment.*``, ``derived``, ``sample`` and ``estimator`` are pinned by a
  digest taken from the artefact *before* the variance components were added,
  and the only new keys are ``sigma2_a``, ``sigma2_a_raw`` and ``sigma2_e``.

No network, no full parquet where a synthetic frame will do.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from bdb27 import band_reliability as br
from bdb27 import common
from bdb27 import reliability_figures as rf

REPO = Path(__file__).resolve().parents[1]
BAND_REL = REPO / "outputs/band_reliability/reliability.json"
FALLBACK_REL = REPO / "outputs/fallback_validation/reliability.json"
EXPOSURE_CSV = REPO / "outputs/band_reliability/turns_per_player.csv"
MANIFEST = REPO / "outputs/figures/figures_manifest.json"

#: Keys a scenario row carried before §3.1. ``scenarios[]`` already had its
#: variance components; the change only gives the *adjusted* rows the same pair.
SCENARIO_KEYS_BEFORE_3_1 = (
    "name", "rule", "players", "turns", "kbar", "icc", "clamped",
    "msb", "msw", "sigma2_a", "sigma2_e", "status",
)
#: Keys an adjustment block carried before §3.1.
ADJUSTMENT_KEYS_BEFORE_3_1 = (
    "r_squared", "n_parameters", "covariates", "players", "turns",
    "icc", "clamped", "status",
)
#: The keys §3.1 adds, and only these.
ADJUSTMENT_KEYS_ADDED_3_1 = ("sigma2_a", "sigma2_a_raw", "sigma2_e")

#: sha256 of the projection of the artefact onto ``*_KEYS_BEFORE_3_1`` plus
#: ``derived``, ``sample``, ``estimator``, ``band``, ``scenario_rules``,
#: ``provenance`` and ``stamp``, computed from
#: ``outputs/band_reliability/reliability.json`` at HEAD 5fe16a4 (D206, the
#: artefact *before* the D207 addition). Any drift in a pre-existing value
#: changes this digest; adding keys does not.
BAND_ARTEFACT_DIGEST_BEFORE_3_1 = (
    "5abfb35a4f28feec7ce8a3a870e4265bfe94aeef725305aade3c207edba427b1")


# ------------------------------------------------------------------ helpers
def _band_artefact() -> dict:
    assert BAND_REL.exists(), f"missing artefact: {BAND_REL}"
    return json.loads(BAND_REL.read_text())


def _projection(document: dict) -> dict:
    """The artefact as it stood before §3.1, for the digest pin."""
    return {
        "scenarios": [{k: s[k] for k in SCENARIO_KEYS_BEFORE_3_1}
                      for s in document["scenarios"]],
        "adjustment": {name: {k: v[k] for k in ADJUSTMENT_KEYS_BEFORE_3_1}
                       for name, v in document["adjustment"].items()},
        "derived": document["derived"],
        "sample": document["sample"],
        "estimator": document["estimator"],
        "band": document["band"],
        "scenario_rules": document["scenario_rules"],
        "provenance": document["provenance"],
        "stamp": document["stamp"],
    }


def _digest(document: dict) -> str:
    blob = json.dumps(_projection(document), sort_keys=True,
                      separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _frame(n_players: int = 60, k: int = 12, *, seed: int = 7,
           rho: float = 0.08) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic band frame + its one-row-per-key ``play_type`` context."""
    rng = np.random.default_rng(seed)
    sigma_a2 = rho / (1.0 - rho)
    rows = []
    for p in range(n_players):
        a = rng.normal(0.0, np.sqrt(sigma_a2))
        for j in range(k):
            rows.append({
                "setting": "game", "position_group": "DB", "valid": True,
                "game_id": 1000 + p // 3, "play_id": 100 + j, "nfl_id": p,
                "frame_start": 20 + j,
                "abs_turn_angle": 30.0 + 29.0 * (j % 5) / 4.0,
                "entry_speed": 3.2 + 1.6 * (j % 4) / 3.0,
                "apex_retention": 0.6 + a + rng.normal(0.0, 1.0),
            })
    frame = pd.DataFrame(rows)
    ctx = frame[list(br.KEY)].drop_duplicates().copy()
    ctx["play_type"] = ["pass" if i % 2 == 0 else "run" for i in range(len(ctx))]
    return frame, ctx


# --------------------------------------------- §3.1 pins: shares are the ICC
def test_every_real_scenario_share_is_its_icc():
    """Pin 1 (artefact side): ``sigma2_a/(sigma2_a+sigma2_e) == icc``."""
    doc = _band_artefact()
    assert len(doc["scenarios"]) == 4
    for row in doc["scenarios"]:
        share = row["sigma2_a"] / (row["sigma2_a"] + row["sigma2_e"])
        assert share == pytest.approx(row["icc"], abs=1e-12), row["name"]
        assert share == pytest.approx(row["icc"], abs=1e-9), row["name"]


def test_adjusted_components_reproduce_the_adjusted_icc():
    """Spec §3.1 acceptance: the identity holds to 1e-12 on both samples."""
    doc = _band_artefact()
    assert set(doc["adjustment"]) == {"all_band", "entry_3_5"}
    for name, block in doc["adjustment"].items():
        share = block["sigma2_a"] / (block["sigma2_a"] + block["sigma2_e"])
        assert share == pytest.approx(block["icc"], abs=1e-12), name
    # and the quoted adjusted ICCs are still the two D207 numbers
    assert doc["adjustment"]["all_band"]["icc"] == pytest.approx(0.003685, rel=1e-4)
    assert doc["adjustment"]["entry_3_5"]["icc"] == pytest.approx(0.014055, rel=1e-4)


def test_adjusted_icc_adds_exactly_three_keys_and_moves_nothing():
    """The code-level half of §3.1: same values, three new keys."""
    frame, _ = _frame(seed=13)
    got = br.adjusted_icc(frame)
    assert set(got) - set(ADJUSTMENT_KEYS_BEFORE_3_1) == set(ADJUSTMENT_KEYS_ADDED_3_1)
    assert set(ADJUSTMENT_KEYS_BEFORE_3_1) <= set(got)

    resid, r2, n_par = br.residualise(frame)
    raw = br.icc1(resid, frame["nfl_id"].to_numpy())
    assert got["r_squared"] == r2 and got["n_parameters"] == n_par
    assert got["icc"] == raw["icc"]
    assert got["clamped"] == raw["clamped"]
    assert got["players"] == raw["n_groups"] and got["turns"] == raw["n_obs"]
    assert got["status"] == raw["status"]
    assert got["sigma2_a"] == raw["sigma2_a"]
    assert got["sigma2_a_raw"] == raw["sigma2_a_raw"]
    assert got["sigma2_e"] == raw["sigma2_e"]
    assert got["sigma2_a"] / (got["sigma2_a"] + got["sigma2_e"]) == pytest.approx(
        got["icc"], abs=1e-12)


def test_section_3_1_is_additive_only_on_the_committed_artefact():
    """Pin 2: every pre-existing value is byte-for-byte unchanged."""
    doc = _band_artefact()
    assert _digest(doc) == BAND_ARTEFACT_DIGEST_BEFORE_3_1
    for name in ("all_band", "entry_3_5"):
        block = doc["adjustment"][name]
        assert set(block) == set(ADJUSTMENT_KEYS_BEFORE_3_1) | set(
            ADJUSTMENT_KEYS_ADDED_3_1)
    # the scenario rows gained nothing at all
    for row in doc["scenarios"]:
        assert set(row) == set(SCENARIO_KEYS_BEFORE_3_1)


def test_one_implementation_of_the_icc_still_holds():
    """``band_reliability.icc1`` is the D203 estimator, not a copy."""
    from bdb27 import fallback_validation as fv

    assert br.icc1 is fv.icc1


# --------------------------------------------------------------- figure 1
def _band_doc() -> dict:
    return rf.load_band_reliability(BAND_REL)


def _built_third() -> dict:
    return rf.build_third_of_one_percent(_band_doc())


def _known_player_ids() -> list[int]:
    """Ids the figure must never draw.

    Subject ids live in the per-player exposure artefact; where that artefact is
    not present (it lands with figures 3/4), the ids D203 already publishes in
    the split-half block stand in, and the check still runs.
    """
    ids: list[int] = []
    exposure = EXPOSURE_CSV
    if exposure.exists():
        ids.extend(int(v) for v in pd.read_csv(exposure)["nfl_id"].unique())
    fallback = json.loads(FALLBACK_REL.read_text())
    ids.extend(int(p["nfl_id"]) for p in
               fallback["split_half"].get("one_sided_players", []))
    return sorted(set(ids))


def test_pin1_between_share_in_the_figure_csv_is_the_icc():
    """Pin 1: ``between_share`` from the CSV equals the share and the ICC."""
    frame = _built_third()["csv_frame"]
    scenarios = frame[frame["status"] == rf.STATUS_OK]
    assert len(scenarios) == 4
    for _, row in scenarios.iterrows():
        share = row["sigma2_a"] / (row["sigma2_a"] + row["sigma2_e"])
        assert row["between_share"] == pytest.approx(share, abs=1e-12), row["scenario"]
        assert row["between_share"] == pytest.approx(row["icc"], abs=1e-9), row["scenario"]
        assert row["icc"] == pytest.approx(row["sigma2_a"] /
                                           (row["sigma2_a"] + row["sigma2_e"]), abs=1e-12)


def test_figure1_marks_are_the_printed_artefact_numbers():
    """The four marks, exactly as they are drawn."""
    built = _built_third()
    assert built["marks"] == [
        "0.34%  ·  7,777 T / 113 P  ·  k̄ 68.8",
        "0.93%  ·  3,385 T / 113 P  ·  k̄ 30.0",
        "0.85%  ·  2,232 T / 108 P  ·  k̄ 20.7",
        "0.46%  ·  5,197 T / 111 P  ·  k̄ 46.8",
    ]
    assert [r["rule_label"] for r in built["rows"]] == [
        "all band turns", "3–5 yd/s entry",
        "3–5 yd/s on pass plays", "pass plays only"]


def test_figure1_has_four_bars_and_no_fifth_adjusted_bar():
    """Spec §5.1: the two adjusted ICCs are CSV/manifest text, never a bar."""
    built = _built_third()
    assert len(built["rows"]) == 4
    assert all(r["status"] == rf.STATUS_OK for r in built["rows"])
    assert len(built["adjusted_rows"]) == 2
    assert all(r["status"] == rf.STATUS_ADJUSTED for r in built["adjusted_rows"])
    assert len(built["marks"]) == 4
    adjusting = built["adjusted_not_drawn"]
    assert set(adjusting) == {"all_band_adjusted", "entry_3_5_adjusted"}
    assert adjusting["all_band_adjusted"]["between_share"] == pytest.approx(
        0.003685, rel=1e-4)
    assert adjusting["entry_3_5_adjusted"]["between_share"] == pytest.approx(
        0.014055, rel=1e-4)
    fig = rf.draw_third_of_one_percent(built)  # four bars, never five
    try:
        assert len(fig.axes) == 1
        bars = fig.axes[0].patches
        rows_drawn = {round(float(b.get_y() + b.get_height() / 2.0), 6) for b in bars}
        assert len(rows_drawn) == 4  # one bar row per drawn scenario
        assert len(bars) == 8        # grey + accent segment per row
    finally:
        plt.close(fig)


def test_not_estimable_row_still_renders_and_says_so():
    """Spec §0.7: a null row prints the counts and "not estimable"."""
    doc = _band_doc()
    for row in doc["scenarios"]:
        if row["name"] == "entry_3_5":
            row["sigma2_a"] = None
            row["icc"] = None
    built = rf.build_third_of_one_percent(doc)
    row = next(r for r in built["rows"] if r["scenario"] == "entry_3_5")
    assert row["status"] == rf.STATUS_NOT_ESTIMABLE
    assert row["between_share"] is None
    assert row["mark"].startswith("not estimable")
    assert "3,385 T / 113 P" in row["mark"]
    fig = rf.draw_third_of_one_percent(built)  # never raises
    plt.close(fig)


def test_pin9_no_identity_no_banned_words_no_gates(tmp_path):
    cfg = common.load_config()
    result = rf.render_figure(rf.FIG_THIRD, _built_third(), tmp_path, cfg,
                              root=tmp_path)
    entry = result["entry"]
    drawn = [t for t in result["texts"] if t]
    scanned = json.loads((tmp_path / f"{rf.FIG_THIRD}.drawn_text.json").read_text())
    every_string = drawn + list(scanned["strings"])

    ids = _known_player_ids()
    assert ids, "no player ids available to test against"
    for text in every_string:
        assert "nfl_id" not in text.lower()
        for pid in ids:
            assert str(pid) not in text, (pid, text)
        assert not re.fullmatch(r"\d{4,6}", text.strip()), text
        for word in rf.BANNED_DRAW_WORDS:
            assert word.lower() not in text.lower(), (word, text)

    assert entry["in_report"] is False
    assert entry["gates_drawn"] is False
    assert entry["ranked_players"] is False
    assert entry["provenance"] == "FULL"

    frame = _built_third()["csv_frame"]
    assert "nfl_id" not in frame.columns
    committed = pd.read_csv(tmp_path / f"{rf.FIG_THIRD}.csv")
    assert "nfl_id" not in committed.columns


def test_pin10_render_is_deterministic_and_manifest_merge_is_additive(tmp_path):
    cfg = common.load_config()
    built = _built_third()
    first = tmp_path / "a"
    second = tmp_path / "b"
    one = rf.render_figure(rf.FIG_THIRD, built, first, cfg, root=tmp_path)
    two = rf.render_figure(rf.FIG_THIRD, built, second, cfg, root=tmp_path)

    assert (first / f"{rf.FIG_THIRD}.csv").read_bytes() == \
        (second / f"{rf.FIG_THIRD}.csv").read_bytes()
    assert (first / f"{rf.FIG_THIRD}.drawn_text.json").read_bytes() == \
        (second / f"{rf.FIG_THIRD}.drawn_text.json").read_bytes()

    manifest = tmp_path / "figures_manifest.json"
    before = json.loads(MANIFEST.read_text())
    # The committed manifest already carries this figure once it has been
    # rendered; drop that one entry so the merge is tested as an insertion.
    before["figures"].pop(rf.FIG_THIRD, None)
    assert len(before["figures"]) == 19
    manifest.write_text(json.dumps(before, indent=2) + "\n")
    rf.merge_manifest(one["entry"], manifest, cfg)
    after = json.loads(manifest.read_text())

    assert set(after["figures"]) - set(before["figures"]) == {rf.FIG_THIRD}
    for key, value in before["figures"].items():
        assert json.dumps(value, sort_keys=True) == json.dumps(
            after["figures"][key], sort_keys=True), key


def test_pin11_missing_band_artefact_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError) as err:
        rf.load_band_reliability(tmp_path / "absent_reliability.json")
    assert "absent_reliability.json" in str(err.value)


def test_pin11_missing_key_names_the_file_and_the_keys(tmp_path):
    path = tmp_path / "reliability.json"
    path.write_text(json.dumps({"scenarios": []}))
    with pytest.raises(KeyError) as err:
        rf.load_band_reliability(path)
    message = str(err.value)
    assert "reliability.json" in message
    assert "derived" in message and "adjustment" in message


def test_pin11_missing_exposure_csv_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError) as err:
        rf.load_turns_per_player(tmp_path / "absent_turns_per_player.csv")
    assert "absent_turns_per_player.csv" in str(err.value)
