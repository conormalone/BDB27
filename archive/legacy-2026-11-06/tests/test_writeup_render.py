"""The writeup renderer's contract.

The renderer is presentation only, and its whole value is in what it refuses:
it must never put a sample number into the writeup, must never leave a silent
``{{...}}`` or a blank where a result belongs, and must name the field it could
not fill. These tests use the FULL-provenance synthetic fixture (a smoke test,
not a result) plus hand-written tiny artefacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import writeup_render as W

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "notes" / "writeup-draft.md"

SAMPLE_STAMP = "PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT"


def _full_payloads():
    return ({
        "provenance": "FULL",
        "pooled": {"share_phys": 0.25, "share_u": 0.75,
                   "bootstrap": {"share_phys_ci95": [0.10, 0.40],
                                 "share_u_ci95": [0.60, 0.90]}},
    }, {"provenance": "FULL", "checks": {"permutation": {"n_requested": 200}}})


def _write_artefacts(outputs: Path, *, decomp: dict, validation: dict) -> None:
    (outputs / "phase4").mkdir(parents=True, exist_ok=True)
    (outputs / "validation").mkdir(parents=True, exist_ok=True)
    (outputs / "phase4" / "decomposition.json").write_text(json.dumps(decomp), encoding="utf-8")
    (outputs / "validation" / "validation.json").write_text(json.dumps(validation), encoding="utf-8")


def _draft(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "draft.md"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# the refusal
# --------------------------------------------------------------------------- #
def test_refuses_to_fill_from_sample_provenance(tmp_path):
    decomp, validation = _full_payloads()
    decomp["provenance"] = SAMPLE_STAMP
    validation["provenance"] = SAMPLE_STAMP
    _write_artefacts(tmp_path / "outputs", decomp=decomp, validation=validation)
    draft = _draft(tmp_path, "share is {{PENDING_FULL_RUN: decomposition.pooled.share_phys}}\n")

    result = W.render(draft, tmp_path / "outputs")

    assert result["status"] == W.REFUSED
    assert result["text"] is None, "a refused render must not produce text"
    assert "decomposition=SAMPLE" in result["reason"]
    assert "Only FULL-provenance" in result["reason"]
    # and nothing is written, even when a destination is supplied
    out = tmp_path / "writeup.md"
    assert W.write(result, out) is None
    assert not out.exists()


def test_refuses_an_unknown_stamp_too(tmp_path):
    decomp, validation = _full_payloads()
    decomp["provenance"] = "EXPERIMENTAL"
    _write_artefacts(tmp_path / "outputs", decomp=decomp, validation=validation)
    draft = _draft(tmp_path, "b {{PENDING_FULL_RUN: decomposition.pooled.share_phys}}\n")

    result = W.render(draft, tmp_path / "outputs")
    assert result["status"] == W.REFUSED
    assert "decomposition=UNKNOWN" in result["reason"]
    assert not (tmp_path / "writeup.md").exists()


def test_absent_artefacts_are_unresolved_not_refused(tmp_path):
    draft = _draft(tmp_path, "a {{PENDING_FULL_RUN: decomposition.pooled.share_phys}} "
                             "b {{PENDING_FULL_RUN: validation.checks.permutation.n_requested}}\n")
    result = W.render(draft, tmp_path / "outputs")

    assert result["status"] == W.UNRESOLVED
    assert result["text"] is not None
    assert result["unresolved"] == ["decomposition.pooled.share_phys",
                                    "validation.checks.permutation.n_requested"]
    assert "{{PENDING_FULL_RUN:" not in W.strip_comments(result["text"]), \
        "no silent token may survive"
    assert "[[UNRESOLVED: decomposition.pooled.share_phys]]" in result["text"]


# --------------------------------------------------------------------------- #
# filling
# --------------------------------------------------------------------------- #
def test_fills_a_complete_full_draft_with_the_fixture(full_outputs):
    result = W.render(DRAFT, full_outputs)

    assert result["status"] == W.OK, result["unresolved"]
    assert result["filled"] >= 25
    assert result["unresolved"] == [] and result["malformed"] == []
    assert "{{PENDING_FULL_RUN:" not in W.strip_comments(result["text"])
    assert "[[UNRESOLVED" not in result["text"]
    # a real assignment landed, formatted once (three decimals)
    assert "0.318" in result["text"]
    # the fixture warns about itself
    assert result["fixture"] == ["decomposition", "validation"]
    assert "synthetic fixture" in W.report(result).lower()


def test_the_rendered_draft_is_inside_the_word_cap(full_outputs):
    result = W.render(DRAFT, full_outputs)
    assert result["word_count"] <= 2000, result["word_count"]


def test_missing_field_is_named_not_guessed(full_outputs):
    path = full_outputs / "phase4" / "decomposition.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["pooled"]["share_phys"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = W.render(DRAFT, full_outputs)

    assert result["status"] == W.UNRESOLVED
    assert "decomposition.pooled.share_phys" in result["unresolved"]
    assert "[[UNRESOLVED: decomposition.pooled.share_phys]]" in result["text"]
    assert "{{PENDING_FULL_RUN:" not in W.strip_comments(result["text"])


def test_null_field_is_unresolved_not_rendered_as_none(full_outputs):
    path = full_outputs / "phase4" / "decomposition.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["pooled"]["agreement"]["u_vs_onset_latency"]["r"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = W.render(DRAFT, full_outputs)
    assert "decomposition.pooled.agreement.u_vs_onset_latency.r" in result["unresolved"]
    assert "None" not in result["text"]


def test_unknown_artefact_is_malformed_and_named(tmp_path):
    decomp, validation = _full_payloads()
    _write_artefacts(tmp_path / "outputs", decomp=decomp, validation=validation)
    draft = _draft(tmp_path, "x {{PENDING_FULL_RUN: nowhere.field}}\n")

    result = W.render(draft, tmp_path / "outputs")
    assert result["status"] == W.UNRESOLVED
    assert result["malformed"] == ["nowhere.field"]
    assert "[[UNRESOLVED: nowhere.field]]" in result["text"]


def test_documentation_tokens_inside_comments_are_not_fields(tmp_path):
    decomp, validation = _full_payloads()
    _write_artefacts(tmp_path / "outputs", decomp=decomp, validation=validation)
    draft = _draft(tmp_path,
                   "<!-- syntax: {{PENDING_FULL_RUN: <artefact>.<path>}} -->\n"
                   "value {{PENDING_FULL_RUN: decomposition.pooled.share_phys}}\n")

    result = W.render(draft, tmp_path / "outputs")
    assert result["status"] == W.OK
    assert result["filled"] == 1
    # the comment survives verbatim, unsubstituted
    assert "{{PENDING_FULL_RUN: <artefact>.<path>}}" in result["text"]
    assert "0.250" in result["text"]


def test_yaml_like_lists_render_as_an_interval(tmp_path):
    decomp, validation = _full_payloads()
    _write_artefacts(tmp_path / "outputs", decomp=decomp, validation=validation)
    draft = _draft(tmp_path, "{{PENDING_FULL_RUN: decomposition.pooled.bootstrap.share_phys_ci95}}\n")
    result = W.render(draft, tmp_path / "outputs")
    assert result["text"].strip() == "[0.100, 0.400]"


def test_render_is_deterministic(full_outputs):
    first = W.render(DRAFT, full_outputs)
    second = W.render(DRAFT, full_outputs)
    assert first["text"] == second["text"]
    assert first["word_count"] == second["word_count"]


def test_strict_write_refuses_an_incomplete_document(tmp_path):
    decomp, validation = _full_payloads()
    _write_artefacts(tmp_path / "outputs", decomp=decomp, validation=validation)
    draft = _draft(tmp_path, "{{PENDING_FULL_RUN: decomposition.pooled.b}} "
                             "{{PENDING_FULL_RUN: decomposition.pooled.share_u}}\n")
    result = W.render(draft, tmp_path / "outputs")
    assert result["status"] == W.UNRESOLVED

    out = tmp_path / "out.md"
    assert W.write(result, out) is not None          # lenient: visible markers
    out.unlink()
    assert W.write(result, out, strict=True) is None  # strict: nothing published
    assert not out.exists()
