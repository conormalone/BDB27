"""The Phase 1 artefacts must be complete, stamped and machine-readable."""

from __future__ import annotations

import csv
import json

import pytest

import common as C

ITEMS = list("abcdefghi")


def _rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def test_all_artefacts_exist(audit_run):
    for name in ("audit_items.csv", "gate.json", "combine_drill_inventory.csv",
                 "dl_rusher_counts.csv", "onset_combine_attempts.csv",
                 "units_measured.json"):
        assert (audit_run / name).exists(), name


def test_every_item_a_to_i_is_covered(audit_run):
    items = {r["item"] for r in _rows(audit_run / "audit_items.csv")}
    assert items == set(ITEMS)


def test_every_row_is_stamped(audit_run):
    rows = _rows(audit_run / "audit_items.csv")
    assert rows
    for r in rows:
        assert C.SAMPLE_STAMP in r["provenance"]


def test_every_row_declares_scope_and_status(audit_run):
    scopes = {"FULL_RAW", "SAMPLE_TRACKING", "DESIGN", "VALIDATION"}
    statuses = {"PASS", "FAIL", "INFO", "PENDING_FULL_RUN", "BLOCKED"}
    for r in _rows(audit_run / "audit_items.csv"):
        assert r["scope"] in scopes, r
        assert r["status"] in statuses, r
        assert r["name"], r
        assert r["evidence"], r


def test_gate_is_machine_readable(audit_run):
    g = json.loads((audit_run / "gate.json").read_text())
    assert "decision" in g
    assert isinstance(g["conditions"], list) and g["conditions"]
    ids = {c["id"] for c in g["conditions"]}
    assert {"combine_sprint_present", "depth_derivable", "onset_consistent",
            "get_off_reliability", "combine_vs_get_off", "snaps_per_rusher",
            "matched_dl"} <= ids
    for c in g["conditions"]:
        assert c["status"] in {"PASS", "FAIL", "PENDING_FULL_RUN"}
        assert c["condition"]


def test_gate_decision_is_consistent_with_its_conditions(audit_run):
    g = json.loads((audit_run / "gate.json").read_text())
    failed = [c["id"] for c in g["conditions"] if c["status"] == "FAIL"]
    pending = [c["id"] for c in g["conditions"] if c["status"] == "PENDING_FULL_RUN"]
    assert g["failed"] == failed
    assert g["pending"] == pending
    if failed:
        assert g["decision"].startswith("NO-GO")
    elif pending:
        assert g["decision"].startswith("CONDITIONAL GO")


def test_sample_tracking_rows_are_present_but_never_pass_the_gate(audit_run):
    rows = [r for r in _rows(audit_run / "audit_items.csv")
            if r["scope"] == "SAMPLE_TRACKING"]
    assert rows, "the audit should exercise the tracking sample"
    # A sample-measured number is informational or pending, never PASS/FAIL as a
    # finding. (Derivation *checks* live under scope=VALIDATION instead.)
    for r in rows:
        assert r["status"] in {"INFO", "PENDING_FULL_RUN"} or r["item"] == "f", r


def test_kill_test_statistics_are_marked_pending(audit_run):
    rows = [r for r in _rows(audit_run / "audit_items.csv") if r["item"] == "g"]
    assert rows
    assert all(r["status"] == "PENDING_FULL_RUN" for r in rows)


def test_combine_test_retest_is_computed_not_pending(audit_run):
    rows = [r for r in _rows(audit_run / "audit_items.csv") if r["item"] == "h"]
    assert any(r["name"].startswith("test-retest ICC") for r in rows)
    assert any(r["status"] in ("PASS", "FAIL") for r in rows)


def test_onset_attempt_table_is_populated(audit_run):
    rows = _rows(audit_run / "onset_combine_attempts.csv")
    assert rows
    assert any(r["ok"] == "True" for r in rows)


def test_units_measured_records_both_trackers(audit_run):
    u = json.loads((audit_run / "units_measured.json").read_text())
    assert "combine_tracking" in u and "game_tracking" in u
    assert u["game_tracking"]["smax"] < 13.5
