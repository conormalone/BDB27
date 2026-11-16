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
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdb27 import band_reliability as br

REPO = Path(__file__).resolve().parents[1]
BAND_REL = REPO / "outputs/band_reliability/reliability.json"
FALLBACK_REL = REPO / "outputs/fallback_validation/reliability.json"

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
