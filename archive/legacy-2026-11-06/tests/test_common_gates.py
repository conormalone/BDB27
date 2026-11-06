"""Provenance, gating and checkpoint behaviour.

The gates exist so that a sample run can never masquerade as a full run, and so
that a full run cannot start before the prereg is frozen.
"""

from __future__ import annotations

import json

import pytest

import common as C


def test_sample_stamp_contains_the_literal(cfg):
    stamp = C.provenance_stamp(cfg, "sample")
    assert C.SAMPLE_STAMP in stamp
    assert C.SAMPLE_STAMP == "UNVALIDATED SAMPLE OUTPUT"


def test_full_provenance_is_not_the_sample_stamp(cfg):
    assert C.provenance_stamp(cfg, "full") == "FULL"
    assert C.SAMPLE_STAMP not in C.provenance_stamp(cfg, "full")


def test_write_json_stamps_provenance(tmp_path, cfg):
    p = C.write_json(tmp_path / "x.json", {"a": 1}, mode="sample", cfg=cfg)
    d = json.loads(p.read_text())
    assert d["provenance"].startswith("PLACEHOLDER_SAMPLE")
    assert C.SAMPLE_STAMP in d["provenance"]
    assert d["a"] == 1


def test_write_csv_stamps_every_row(tmp_path, cfg):
    p = C.write_csv(tmp_path / "x.csv", [{"a": 1}, {"a": 2}], ["a"],
                    mode="sample", cfg=cfg)
    lines = p.read_text().strip().splitlines()
    assert lines[0].startswith("provenance,")
    assert all(C.SAMPLE_STAMP in ln for ln in lines[1:])


def test_write_markdown_stamps_line_one(tmp_path, cfg):
    p = C.write_markdown(tmp_path / "x.md", "# title", mode="sample", cfg=cfg)
    first = p.read_text().splitlines()[0]
    assert first.startswith("<!-- provenance:")
    assert C.SAMPLE_STAMP in first


def test_require_full_provenance_accepts_the_configured_full_mode(cfg):
    assert C.require_full_provenance(cfg, "full", "01_audit") == "FULL"


def test_require_full_provenance_refuses_a_mislabelled_full_mode(cfg):
    """mode=full with a PLACEHOLDER provenance must stop, not warn."""
    import copy

    c = copy.deepcopy(cfg)
    C.block(c)["mode"]["full"]["provenance"] = "PLACEHOLDER_SAMPLE"
    with pytest.raises(RuntimeError) as ei:
        C.require_full_provenance(c, "full", "01_audit")
    assert "refusing to run" in str(ei.value)


def test_full_mode_is_refused_without_full_tracking(cfg):
    with pytest.raises(RuntimeError) as ei:
        C.require_full_tracking(cfg, "full")
    msg = str(ei.value)
    assert "FULL MODE REFUSED" in msg


def test_prereg_gate_accepts_sample_mode_without_a_file(cfg, tmp_path):
    import copy
    c = copy.deepcopy(cfg)
    C.block(c)["paths"]["prereg"] = str(tmp_path / "nope.md")
    assert C.require_prereg(c, "sample", "01_audit")


def test_prereg_gate_refuses_full_mode_when_missing(cfg, tmp_path):
    import copy
    c = copy.deepcopy(cfg)
    C.block(c)["paths"]["prereg"] = str(tmp_path / "nope.md")
    with pytest.raises(RuntimeError) as ei:
        C.require_prereg(c, "full", "01_audit")
    assert "does not exist" in str(ei.value)


@pytest.mark.parametrize("text,expected", [
    ("**Status: FROZEN.**", True),
    ("**Status: DRAFT — NOT FROZEN**", False),
    ("**Status: Unfrozen**", False),
    ("**Status: PENDING**", False),
    ("# no status line", False),
    ("**Status: FROZEN after review**", True),
])
def test_prereg_status_parsing(text, expected):
    assert C.prereg_is_frozen(text) is expected


def test_checkpoint_round_trip(cfg):
    assert C.mark_done(cfg, "unit_test_stage", "sample", {"k": 1})
    assert C.is_done(cfg, "unit_test_stage", "sample") is True
    assert C.is_done(cfg, "unit_test_stage", "sample", force=True) is False


def test_config_fingerprint_is_stable_and_seed_sensitive(cfg):
    a = C.config_fingerprint(cfg, "01_audit")
    b = C.config_fingerprint(cfg, "01_audit")
    assert a == b
    import copy
    c = copy.deepcopy(cfg)
    C.block(c)["seed"] += 1
    assert C.config_fingerprint(c, "01_audit") != a


def test_mode_validation(cfg):
    with pytest.raises(ValueError):
        C.check_mode("test")


def test_disk_guard_runs(cfg):
    got = C.assert_disk_space(cfg, "sample")
    assert got["root_free_mb"] > 0
