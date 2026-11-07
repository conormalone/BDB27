"""Contract on the Phase 1 gate artefacts: every item, honest statuses, stamps.

These read the committed evidence in outputs/audit/, so they fail if the audit
stops covering an item or drops the sample stamp.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "outputs" / "audit"

ALLOWED = {"PASS", "FAIL", "PARTIAL", "PENDING", "PENDING FULL RUN", "NOTE"}
VERDICTS = {"GO", "CONDITIONAL GO", "NO-GO"}


@pytest.fixture(scope="module")
def items() -> pd.DataFrame:
    return pd.read_csv(AUDIT / "audit_items.csv")


def test_every_item_a_to_m_is_present(items):
    assert list(items.item) == list("abcdefghijklm")


def test_statuses_come_from_the_allowed_vocabulary(items):
    assert set(items.status) <= ALLOWED, set(items.status) - ALLOWED


def test_every_evidence_file_exists(items):
    for name in items.evidence_file:
        for part in str(name).replace(" + ", ",").split(","):
            p = AUDIT / part.strip()
            if p.suffix in {".csv", ".json"}:
                assert p.exists(), f"missing evidence {p}"


def test_gate_verdict_and_conditions_agree():
    gate = json.loads((AUDIT / "gate.json").read_text())
    assert gate["verdict"] in VERDICTS
    conds = {c["id"]: c for c in gate["conditions"]}
    # the stop conditions are the three the spec names for the audit itself
    stop_failed = [cid for cid in ("G1", "G2", "G3") if conds[cid]["status"] == "FAIL"]
    if stop_failed:
        assert gate["verdict"] == "NO-GO"
    else:
        assert gate["verdict"] in {"CONDITIONAL GO", "GO"}
        # a verdict that is not NO-GO must name what is still pending
        assert "PENDING" in gate["because"].upper() or "pending" in gate["because"]
    # every pending condition must be named in the verdict text
    for cid, c in conds.items():
        if c["status"] in {"PENDING", "PENDING FULL RUN"}:
            assert cid in gate["because"], f"{cid} pending but not named in the verdict"


def test_sample_stamp_on_every_audit_artifact():
    for p in AUDIT.glob("*.csv"):
        df = pd.read_csv(p, nrows=5)
        assert "provenance" in df.columns, p.name
        assert set(df.provenance.dropna().unique()) <= {"PLACEHOLDER_SAMPLE"}, p.name
        if "label" in df.columns:
            assert set(df.label.dropna().unique()) <= {"UNVALIDATED SAMPLE OUTPUT"}, p.name
    for p in AUDIT.glob("*.json"):
        d = json.loads(p.read_text())
        assert d.get("provenance") == "PLACEHOLDER_SAMPLE", p.name


def test_kill_test_is_not_a_verdict_on_sample_data(items):
    j = items[items.item == "j"].iloc[0]
    assert j.status == "PENDING FULL RUN"
    assert int(j.interpretable) == 0


def test_primary_group_decision_is_recorded():
    d = json.loads((AUDIT / "primary_group.json").read_text())
    assert d["primary_group"] in {"DL", "WR/TE"}
    kinds = json.loads((AUDIT / "detector_geometry.csv").read_text() and "{}")  # noqa: F841
    geo = pd.read_csv(AUDIT / "detector_geometry.csv")
    assert set(geo.drill_name) == {"THREE_CONE_DRILL", "SHORT_SHUTTLE"}
