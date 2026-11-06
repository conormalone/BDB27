"""Do the four deliverable figures actually draw?

Before this file, ``render_figures`` had only ever exercised its skip path: no
figure had ever been produced, so "the renderer works" was an untested claim.
These tests materialise a FULL-provenance synthetic fixture (``fixtures/
synthetic/full_provenance``) into a temp ``outputs/`` tree and prove:

* all four figures render to a non-empty PNG with finite axis limits;
* all four still refuse sample provenance (the existing rule, kept);
* a missing field names itself in the skip message instead of plotting a blank
  axis or raising out of the function.

**This is a smoke test of the renderer, not a result.** Every value in the
fixture is synthetic; the fixture artefact says so in its own ``fixture`` key.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import notebook_report as R

SAMPLE_STAMP = "PLACEHOLDER_SAMPLE: UNVALIDATED SAMPLE OUTPUT"


def _all_skip(result: dict) -> None:
    assert result["paths"] == []
    assert len(result["messages"]) == 4
    assert all("SKIPPED" in message for message in result["messages"])


def test_all_four_figures_render_on_the_full_fixture(full_outputs, tmp_path):
    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full", dpi=100)

    assert result["paths"], result["messages"]
    assert len(result["paths"]) == 4
    assert len(result["figures"]) == 4

    for record in result["figures"]:
        path = Path(record["path"])
        assert path.exists() and path.stat().st_size > 0, f"{path} is empty"
        assert record["width_px"] > 0 and record["height_px"] > 0
        assert all(math.isfinite(v) for v in record["xlim"] + record["ylim"]), record
    names = [record["name"] for record in result["figures"]]
    assert names == ["fig1_variance_split", "fig2_phys_vs_observed",
                     "fig3_reliability_agreement", "fig4_one_rusher_reps"]


def test_the_png_is_a_readable_image_with_content(full_outputs, tmp_path):
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import numpy as np

    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full", dpi=100)
    for record in result["figures"]:
        image = mpimg.imread(record["path"])
        assert image.shape[0] == record["height_px"]
        assert image.shape[1] == record["width_px"]
        assert np.isfinite(image).all(), f"{record['name']} contains non-finite pixels"
        assert float(image.std()) > 0.0, f"{record['name']} is a blank canvas"


def test_all_four_skip_on_sample_provenance(full_outputs, tmp_path):
    """The existing rule is kept: sample output is never plotted as a finding."""
    path = full_outputs / "phase4" / "decomposition.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["provenance"] = SAMPLE_STAMP
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full")
    _all_skip(result)
    assert all("sample output" in message for message in result["messages"])
    assert not (tmp_path / "figures").exists() or not any((tmp_path / "figures").iterdir())


def test_all_four_skip_in_sample_mode(full_outputs, tmp_path):
    result = R.render_figures(full_outputs, tmp_path / "figures", mode="sample")
    _all_skip(result)
    assert all("MODE='sample'" in message for message in result["messages"])


def test_a_missing_field_names_itself_and_the_other_figures_still_draw(full_outputs, tmp_path):
    path = full_outputs / "phase4" / "decomposition.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["edge"]["share_u"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full", dpi=100)

    assert len(result["paths"]) == 3, result["messages"]
    fig1 = [m for m in result["messages"] if m.startswith("figure 1")][0]
    assert "SKIPPED" in fig1
    assert "edge.share_u" in fig1


def test_a_missing_interval_is_named(full_outputs, tmp_path):
    path = full_outputs / "phase4" / "decomposition.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["interior"]["bootstrap"]["share_phys_ci95"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full")
    fig1 = [m for m in result["messages"] if m.startswith("figure 1")][0]
    assert "interior.bootstrap.share_phys_ci95 absent" in fig1


def test_a_missing_reliability_block_is_named(full_outputs, tmp_path):
    path = full_outputs / "phase4" / "decomposition.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["pooled"]["reliability"]["model_based"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full")
    fig3 = [m for m in result["messages"] if m.startswith("figure 3")][0]
    assert "pooled.reliability.model_based" in fig3
    assert "absent" in fig3


def test_missing_snaps_is_named_for_the_player_figures(full_outputs, tmp_path):
    (full_outputs / "phase3" / "snaps.parquet").unlink()

    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full")
    fig2 = [m for m in result["messages"] if m.startswith("figure 2")][0]
    fig4 = [m for m in result["messages"] if m.startswith("figure 4")][0]
    assert "phase3/snaps.parquet absent" in fig2
    assert "phase3/snaps.parquet absent" in fig4
    # figures 1 and 3 need only the decomposition artefact, so they still draw
    assert len(result["paths"]) == 2


def test_sample_provenance_in_the_snaps_table_is_named(full_outputs, tmp_path):
    import pandas as pd

    path = full_outputs / "phase3" / "snaps.parquet"
    snaps = pd.read_parquet(path)
    snaps["provenance"] = SAMPLE_STAMP
    snaps.to_parquet(path, index=False)

    result = R.render_figures(full_outputs, tmp_path / "figures", mode="full")
    fig2 = [m for m in result["messages"] if m.startswith("figure 2")][0]
    assert "snaps.parquet is not FULL provenance" in fig2
