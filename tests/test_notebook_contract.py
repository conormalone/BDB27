"""Notebook contract: the notebook is the interface, and it may not lie.

Four things must stay true whatever the pipeline does:

  * the stage order is the spec's order (and matches ``src/run.py``);
  * the committed notebook carries no baked outputs;
  * a missing gate artefact renders ``NOT RUN``, never a silent blank;
  * a stub is never scored as a pass.

Plus the full-mode preflight: a missing full tracking file is named and the run is
blocked, with no silent fallback to the sample.
"""

from __future__ import annotations

import ast
import base64
import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_full_run.ipynb"
REAL_FIGURES = ROOT / "outputs" / "figures"
REAL_MANIFEST = REAL_FIGURES / "figures_manifest.json"

SAMPLE_STAMP = "PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE OUTPUT"

#: TASK.md "Layout and run order".
SPEC_ORDER = ["00_thresholds", "01_audit", "02_turns", "03_envelope",
              "04_demand_coverage", "05_validate", "06_figures"]

SEASON_FILES = ("game_tracking_2023.csv", "game_tracking_2024.csv", "game_tracking_2025.csv")


@pytest.fixture(scope="module")
def notebook():
    assert NOTEBOOK.exists(), f"notebook missing: {NOTEBOOK}"
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def source_of(notebook, marker):
    """The source of the first code cell containing ``marker``."""
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        if marker in source:
            return source
    raise AssertionError(f"no code cell contains {marker!r}")


def exec_cell(source, **globals_in):
    namespace = dict(globals_in)
    exec(compile(source, "<notebook cell>", "exec"), namespace)  # noqa: S102 - our own notebook
    return namespace


def test_stage_order_matches_the_spec(notebook):
    source = source_of(notebook, "STAGE_ORDER = [")
    order = None
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "STAGE_ORDER" for t in node.targets):
            order = ast.literal_eval(node.value)
    assert order == SPEC_ORDER, f"notebook stage order is {order}"

    sys.path.insert(0, str(ROOT / "src"))
    import run as runner
    assert order == runner.STAGES, "notebook order drifted from src/run.py"


def test_committed_notebook_has_no_baked_outputs(notebook):
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell.get("outputs", []) == [], f"code cell {index} carries baked outputs"
        assert cell.get("execution_count") is None, f"code cell {index} carries an execution count"


def test_missing_gate_artefact_renders_not_run(notebook, tmp_path):
    source = source_of(notebook, "def render_headline")
    out = io.StringIO()
    with redirect_stdout(out):
        exec_cell(source,
                  Path=Path,
                  GATE_JSON=tmp_path / "gate.json",
                  AUDIT_CSV=tmp_path / "audit_items.csv")
    text = out.getvalue()

    assert "NOT RUN — gate.json absent" in text
    for field in ("provenance", "verdict", "because", "primary_group",
                  "recommended_smoother"):
        assert f"NOT RUN — {field} absent" in text, f"{field} did not render NOT RUN"
    for number in range(1, 10):
        assert f"G{number}" in text
        assert "NOT RUN — condition absent" in text
    for letter in "abcdefghijklm":
        assert f"{letter} : NOT RUN — item absent" in text
    # An absent artefact must never be dressed up as a result banner.
    assert "NOT A RESULT" not in text


def test_a_stub_is_never_a_pass(notebook):
    namespace = exec_cell(source_of(notebook, "def classify_outcome"))
    classify = namespace["classify_outcome"]

    stub = classify(2, "[02_turns] STAGE NOT IMPLEMENTED — Phase 2 is not built yet.", False)
    assert stub["passed"] is False
    assert stub["stub"] is True
    assert stub["status"] == "NOT IMPLEMENTED (stub, exit 2)"

    reused = classify(0, "00_thresholds: /tmp/x/thresholds.json exists (use --force)", False)
    assert reused["passed"] is True
    assert reused["reused"] is True
    assert "checkpoint reused" in reused["status"]

    wrote = classify(0, "01_audit: 13 items", True)
    assert wrote["passed"] is True and wrote["reused"] is False

    refused = classify(2, "REFUSED: required inputs are absent", False)
    assert refused["passed"] is False and refused["stub"] is False
    assert "REFUSED" in refused["status"]

    failed = classify(1, "Traceback (most recent call last): ...", True)
    assert failed["passed"] is False and failed["stub"] is False
    assert "FAILED" in failed["status"]


def test_full_mode_preflight_names_the_missing_tracking(notebook, tmp_path):
    namespace = exec_cell(source_of(notebook, "def full_tracking_preflight"))
    preflight = namespace["full_tracking_preflight"]

    blocked, message = preflight(tmp_path, "game_tracking.csv")
    assert blocked is True
    assert "PENDING FULL RUN" in message
    for name in SEASON_FILES:
        assert name in message, f"{name} was not named"
    assert "game_tracking.csv" in message
    assert "sample data is NOT substituted" in message

    (tmp_path / "game_tracking.csv").write_text("", encoding="utf-8")
    blocked, message = preflight(tmp_path, "game_tracking.csv")
    assert blocked is False
    assert message == ""


# ------------------------------------------------- phase 3 envelope headline

def test_envelope_headline_absent_renders_not_run(notebook, tmp_path):
    source = source_of(notebook, "def render_envelope_headline")
    out = io.StringIO()
    with redirect_stdout(out):
        namespace = exec_cell(source, Path=Path, OUTPUTS_DIR=tmp_path)
        namespace["render_envelope_headline"](
            tmp_path / "manifest.json", tmp_path / "variance_components.csv")
    text = out.getvalue()

    assert "NOT RUN — envelope manifest absent" in text
    for field in ("provenance", "artefact_version", "settings_fitted",
                  "players_fitted", "tau2_route", "crosscheck", "n_components",
                  "n_at_boundary", "n_disagreements", "convergence"):
        assert f"NOT RUN — {field} absent" in text, f"{field} did not render NOT RUN"
    assert "NOT A RESULT" not in text


def test_envelope_headline_prints_route_boundary_and_convergence(notebook, tmp_path):
    manifest = {
        "provenance": "PLACEHOLDER_SAMPLE",
        "artefact_version": 2,
        "settings_fitted": ["combine"],
        "players_fitted": {"combine": 3},
        "variance": {
            "route": "moment_matched",
            "crosscheck": "player_grouped_kfold_heldout_check_loss",
            "n_components": 2,
            "n_at_boundary": 1,
            "n_disagreements": 1,
        },
        "convergence": {
            "n_fits": 5, "n_converged": 4, "n_not_converged": 1,
            "explanations": [{
                "setting": "combine", "position_group": "WR", "model": "capacity",
                "stop_reason": "max_iter", "iterations": 120, "n_obs": 100,
                "n_players": 10, "objective_gain_last": 1e-7,
            }],
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    components_path = tmp_path / "variance_components.csv"
    components_path.write_text(
        "setting,position_group,measure,tau2,tau2_status,tau2_heldout_optimal,routes_agree\n"
        "combine,WR,arc_capacity,0.0,AT_BOUNDARY,0.0,True\n"
        "combine,WR,plant_retention,1.2,INTERIOR,0.0,False\n",
        encoding="utf-8")

    source = source_of(notebook, "def render_envelope_headline")
    out = io.StringIO()
    with redirect_stdout(out):
        namespace = exec_cell(source, Path=Path, OUTPUTS_DIR=tmp_path)
        namespace["render_envelope_headline"](manifest_path, components_path)
    text = out.getvalue()

    assert "moment_matched" in text
    assert "player_grouped_kfold_heldout_check_loss" in text
    assert "AT_BOUNDARY" in text
    assert "1 of 2 component(s) AT_BOUNDARY" in text
    assert "disagreements=1" in text
    assert "4/5" in text and "non-converged=1" in text
    assert "combine/WR" in text


def test_envelope_headline_cell_has_no_baked_outputs(notebook):
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        if "def render_envelope_headline" in "".join(cell["source"]):
            assert cell.get("outputs", []) == []
            assert cell.get("execution_count") is None
            return
    raise AssertionError("the envelope headline code cell is absent")


# ------------------------------------------------ phase 4 demand/coverage

def test_demand_coverage_headline_absent_renders_not_run(notebook, tmp_path):
    source = source_of(notebook, "def render_demand_coverage_headline")
    out = io.StringIO()
    with redirect_stdout(out):
        namespace = exec_cell(source, Path=Path, OUTPUTS_DIR=tmp_path)
        namespace["render_demand_coverage_headline"](
            tmp_path / "manifest.json", tmp_path / "coverage_players.parquet",
            tmp_path / "coverage_position_ranking.csv")
    text = out.getvalue()

    assert "NOT RUN — demand/coverage manifest absent" in text
    for field in ("provenance", "positions_covered", "demand_mass",
                  "coverage_distribution", "boundary_share", "cut_gaps",
                  "outcome_status", "cut_cards"):
        assert f"NOT RUN — {field} absent" in text, f"{field} did not render NOT RUN"
    assert "NOT A RESULT" not in text


def test_demand_coverage_headline_prints_mass_boundary_and_cards(notebook, tmp_path):
    import pandas as pd

    manifest = {
        "provenance": "PLACEHOLDER_SAMPLE",
        "artefact_version": 1,
        "positions": ["DL", "WR"],
        "demand_counts": {
            "turns_in": 100, "eligible": 60, "total_demand_mass": 60.0,
            "per_group": {"DL": 25, "WR": 35},
        },
        "coverage": {
            "n_players": 8, "n_shared_players": 8,
            "n_groups": 2, "n_groups_with_players": 2, "n_shared_groups": 2,
            "all_group_level": True,
            "interval_method": "marginal_bernoulli_over_stage03_inside_share",
            "joint_bootstrap_draws": False,
            "boundary": {"n_components_at_boundary": 2, "n_components": 2},
            "distribution": {
                "combined": {"mean": 0.81, "min": 0.70, "max": 0.91,
                             "p05": 0.70, "p95": 0.91, "n": 8},
                "observed": {"mean": 0.75, "min": 0.60, "max": 0.88,
                             "p05": 0.60, "p95": 0.88, "n": 8},
                "extrapolated": {"mean": 0.95, "min": 0.90, "max": 1.0,
                                 "p05": 0.90, "p95": 1.0, "n": 8},
            },
        },
        "cut_gaps": {"n_players_with_gaps": 5, "n_player_cell_rows": 12,
                     "top_n": 5, "mean_gap_score": 0.021},
        "outcome_weighting": {
            "status": "NOT_ESTIMABLE", "reason": "label_file_absent",
            "positions": ["DL"], "counts": {"qualifying_turns_in_scope": 0},
        },
        "cut_cards": {"n_cards": 2, "selection": "median_coverage",
                      "rendering": "deferred_to_stage_06_figures"},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    pd.DataFrame({
        "nfl_id": [1, 2, 3], "position_group": ["DL", "WR", "WR"],
    }).to_parquet(tmp_path / "coverage_players.parquet", index=False)
    (tmp_path / "coverage_position_ranking.csv").write_text(
        "position_group,role,n_players,mean_combined,rank_coverage_combined\n"
        "WR,all,3,0.72,1\n"
        "DL,all,3,0.88,2\n", encoding="utf-8")

    source = source_of(notebook, "def render_demand_coverage_headline")
    out = io.StringIO()
    with redirect_stdout(out):
        namespace = exec_cell(source, Path=Path, OUTPUTS_DIR=tmp_path)
        namespace["render_demand_coverage_headline"](
            manifest_path, tmp_path / "coverage_players.parquet",
            tmp_path / "coverage_position_ranking.csv")
    text = out.getvalue()

    assert "UNVALIDATED SAMPLE OUTPUT" in text
    assert "positions covered" in text and "DL=25" in text and "WR=35" in text
    assert "demand mass" in text and "60 weighted turns" in text
    for scope in ("combined", "observed", "extrapolated"):
        assert f"coverage {scope:<13}" in text
    assert "mean=0.810" in text and "min=0.700" in text and "max=0.910" in text
    assert "8/8 players in a shared group" in text
    assert "2/2 components AT_BOUNDARY" in text
    assert "cut gaps" in text and "12 player-cell row(s)" in text
    assert "NOT_ESTIMABLE" in text and "label_file_absent" in text
    assert "2 selected" in text and "rendering deferred to stage 06" in text
    assert "3 Combine player(s)" in text
    assert "lowest coverage WR/all" in text


# ------------------------------------------------------- figure gallery (§5)

#: A real 1x1 PNG: the gallery displays from path, so the file has to exist.
PNG_1PX = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def gallery_source(notebook):
    return source_of(notebook, "def figure_gallery")


def run_gallery(notebook, manifest_path, fig_dir):
    """Exec the gallery cell, then run its function against the given manifest."""
    with redirect_stdout(io.StringIO()):
        namespace = exec_cell(gallery_source(notebook),
                              Path=Path, REPO=ROOT, FIG_DIR=Path(fig_dir),
                              MODE="sample", stage_results=[])
    shown = []
    out = io.StringIO()
    with redirect_stdout(out):
        returned = namespace["figure_gallery"](
            Path(manifest_path), Path(fig_dir), "sample", [],
            display_fn=lambda image: shown.append(image))
    return out.getvalue(), returned, shown


def write_manifest(tmp_path, figures, provenance="PLACEHOLDER_SAMPLE"):
    path = tmp_path / "figures_manifest.json"
    path.write_text(json.dumps({
        "provenance": provenance,
        "label": "UNVALIDATED SAMPLE OUTPUT" if provenance == "PLACEHOLDER_SAMPLE" else "FULL",
        "stamp": SAMPLE_STAMP if provenance == "PLACEHOLDER_SAMPLE" else "FULL",
        "generated_utc": "2026-11-08T00:00:00Z",
        "config_sha": "deadbeef",
        "figures": figures,
    }), encoding="utf-8")
    return path


def sample_entry(name, **overrides):
    entry = {
        "figure": name,
        "stage": "06_figures",
        "title": f"{name} title",
        "claim": f"{name} claim",
        "caveats": [f"{name} caveat one", f"{name} caveat two"],
        "sources": ["outputs/turns/turns.parquet"],
        "stamp": SAMPLE_STAMP,
        "png": f"outputs/figures/{name}.png",
    }
    entry.update(overrides)
    return entry


def test_gallery_lists_every_manifest_entry(notebook):
    """(a) Every figure the manifest carries appears in the rendered text."""
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["figures"]
    assert len(entries) == 18, f"stage 06 should list eighteen figures, found {len(entries)}"

    text, returned, shown = run_gallery(notebook, REAL_MANIFEST, REAL_FIGURES)

    for name, entry in entries.items():
        assert f"] {name}" in text, f"{name} was not named by the gallery"
        assert entry["title"] in text, f"{name} title was not rendered"
        if "claim" in entry:
            assert entry["claim"] in text, f"{name} claim was not rendered"
        else:
            assert "claim   : ABSENT" in text, f"{name} has no claim and did not say so"
        for caveat in entry["caveats"]:
            assert caveat in text, f"{name} caveat was not rendered"
    assert returned["displayed"] == len(entries) == len(shown)
    assert returned["missing"] == []
    assert "GALLERY RESULT — 18 of 18 figure(s) displayed" in text


def test_gallery_cell_reads_the_manifest_not_a_literal_figure_list(notebook):
    """(b) The gallery is driven by the manifest; no figure name is typed into it."""
    source = gallery_source(notebook)
    assert "figures_manifest.json" in source
    assert "json.loads" in source
    assert "manifest.get(\"figures\"" in source
    # `fig_dir` is the folder argument and `config_sha` carries the letters by accident;
    # any other fig_ token would be a typed-in figure name.
    literal = r"(?<!con)fig_(?!dir\b)[a-z0-9]"
    assert not re.search(literal, source), \
        "the gallery cell hard-codes a figure name"
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            body = "".join(cell["source"])
            assert not re.search(literal, body), \
                "a notebook cell names a figure literally"


def test_gallery_missing_png_is_missing_not_an_exception(notebook, tmp_path):
    """(c) A listed PNG that is absent prints MISSING against its manifest name."""
    path = write_manifest(tmp_path, {"fig_nowhere": sample_entry("fig_nowhere")})
    text, returned, shown = run_gallery(notebook, path, tmp_path)

    assert "MISSING — fig_nowhere" in text
    assert returned == {"figures": 1, "displayed": 0,
                        "missing": ["fig_nowhere"], "schematic": []}
    assert shown == [], "no image should be displayed for a missing PNG"
    assert "1 of 1 figure(s) displayed, 0 MISSING" not in text


def test_gallery_prints_the_sample_banner_once(notebook, tmp_path):
    """(d) Sample provenance is a banner at the top, never a silent per-figure fact."""
    (tmp_path / "fig_a.png").write_bytes(PNG_1PX)
    path = write_manifest(tmp_path, {"fig_a": sample_entry("fig_a")})
    text, returned, shown = run_gallery(notebook, path, tmp_path)

    assert text.count("SAMPLE BANNER") == 1
    assert SAMPLE_STAMP in text
    assert "NOT A RESULT" in text
    assert "PLACEHOLDER_SAMPLE" in text
    assert "1 of 1 figures below are sample output" in text
    assert returned["displayed"] == 1 and len(shown) == 1


def test_gallery_shows_the_schematic_as_a_schematic(notebook, tmp_path):
    """(e) The schematic wears SCHEMATIC; no sample badge is forced onto it."""
    (tmp_path / "fig_schematic.png").write_bytes(PNG_1PX)
    (tmp_path / "fig_data.png").write_bytes(PNG_1PX)
    schematic = sample_entry(
        "fig_schematic",
        status="SCHEMATIC",
        status_stamp="SCHEMATIC — illustrative geometry, not measured data",
        stamp="SCHEMATIC — illustrative geometry, not measured data",
        provenance_applied=False,
    )
    path = write_manifest(tmp_path, {
        "fig_data": sample_entry("fig_data"),
        "fig_schematic": schematic,
    })
    text, returned, shown = run_gallery(notebook, path, tmp_path)

    body = text.split("GALLERY RESULT")[0]
    blocks = [b for b in body.split("-" * 92) if re.match(r"\s*\[\d+/\d+\]", b)]
    schematic_block = next(b for b in blocks if "fig_schematic" in b)
    data_block = next(b for b in blocks if "fig_data" in b)
    assert "SCHEMATIC — illustrative geometry, not measured data" in schematic_block
    assert "PLACEHOLDER_SAMPLE" not in schematic_block
    assert "deliberately NOT sample-stamped" in schematic_block
    assert "PROPOSAL" not in body
    assert "proposed" not in body.lower()
    assert SAMPLE_STAMP in data_block, "a data figure keeps its sample stamp"
    assert returned["schematic"] == ["fig_schematic"]
    assert returned["displayed"] == 2 and len(shown) == 2
    assert "1 of 2 figures below are sample output" in text


def test_gallery_absent_manifest_says_not_run(notebook, tmp_path):
    """No manifest is reported with the stage reason, never as an empty gallery."""
    stage_results = [{
        "stage": "06_figures", "rc": 2, "seconds": 0.0, "output": "", "error": "",
        "changed": False,
        "outcome": {"status": "NOT IMPLEMENTED (stub, exit 2)", "passed": False,
                    "stub": True, "reused": False},
    }]
    with redirect_stdout(io.StringIO()):
        namespace = exec_cell(gallery_source(notebook),
                              Path=Path, REPO=ROOT, FIG_DIR=tmp_path,
                              MODE="sample", stage_results=stage_results)
    out = io.StringIO()
    with redirect_stdout(out):
        returned = namespace["figure_gallery"](
            tmp_path / "figures_manifest.json", tmp_path, "sample", stage_results,
            display_fn=lambda image: None)
    text = out.getvalue()

    assert "NOT RUN — figures manifest absent" in text
    assert "NOT IMPLEMENTED (stub, exit 2)" in text
    assert "SAMPLE BANNER" not in text
    assert returned["figures"] == 0 and returned["displayed"] == 0
