"""The multi-candidate notebook: the interface, and it may not lie (AC8).

Three things are pinned:

* **structure** — one config cell, every knob in it, and the committed file
  carries no baked outputs and no shell/CLI escape hatches;
* **the no-tracking path completes** — the whole notebook runs end to end on
  this machine, where no frame-level tracking exists, prints ``NOT EVALUABLE``
  and still reports ``N`` with its composition (it must not abort);
* **the number is assembled, not written down** — ``N`` equals the sum of the
  four slots, the frozen slot equals the frame's own row count, and every slot
  carries its label.

The execution test is the expensive one (it builds the FULL frames once); the
rest are static and free.
"""

from __future__ import annotations

import ast
import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "bdb27_multi_candidate.ipynb"
sys.path.insert(0, str(ROOT / "src"))

CONFIG_MARKER = "# ---- CONFIG"
KNOBS = ("TURNS", "PLAYER_PLAY", "PLAYERS", "TRACKING", "RADII", "SEED",
         "WRITE_ARTEFACTS", "OUT_DIR")


@pytest.fixture(scope="module")
def notebook() -> dict:
    assert NOTEBOOK.exists(), f"notebook missing: {NOTEBOOK}"
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def code_sources(nb: dict) -> list[str]:
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def test_notebook_is_valid_v4(notebook):
    assert notebook["nbformat"] == 4
    assert any(c["cell_type"] == "markdown" for c in notebook["cells"])
    assert len(code_sources(notebook)) >= 5


def test_exactly_one_config_cell_holds_every_knob(notebook):
    hits = [s for s in code_sources(notebook) if CONFIG_MARKER in s]
    assert len(hits) == 1, f"{len(hits)} cells carry {CONFIG_MARKER!r}"
    config = hits[0]
    for knob in KNOBS:
        assert re.search(rf"^{knob}\s*=", config, re.M), f"{knob} not in the config cell"
    # No other cell may set a knob: one place to edit, or none.
    for source in code_sources(notebook):
        if source == config:
            continue
        for knob in ("TRACKING", "RADII", "SEED", "WRITE_ARTEFACTS"):
            assert not re.search(rf"^{knob}\s*=[^=]", source, re.M), \
                f"{knob} is assigned outside the config cell"


def test_committed_notebook_carries_no_outputs(notebook):
    for i, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert cell.get("outputs") == [], f"cell {i} has baked outputs"
        assert cell.get("execution_count") is None, f"cell {i} has an execution count"


def test_notebook_has_no_cli_or_shell_escape(notebook):
    for source in code_sources(notebook):
        for line in source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("!", "%")), f"magic/shell: {stripped!r}"
        assert "argparse" not in source
        assert "sys.argv" not in source
        assert "subprocess" not in source


def test_every_code_cell_parses(notebook):
    for i, source in enumerate(code_sources(notebook)):
        ast.parse(source, filename=f"<cell {i}>")


def test_text_writes_name_their_encoding(notebook):
    """Windows' default code page is not UTF-8 and the labels carry em dashes."""
    for source in code_sources(notebook):
        for m in re.finditer(r"\.(to_csv|write_text)\(", source):
            call = source[m.start():m.start() + 400]
            assert 'encoding="utf-8"' in call, f"write without encoding: {call[:80]!r}"


@pytest.fixture(scope="module")
def executed(notebook, tmp_path_factory) -> dict:
    """Run every code cell in one namespace — the no-tracking path, end to end.

    ``OUT_DIR`` is redirected to a temporary directory so the module run writes
    real files without touching the committed ``outputs/ambiguity_attribution``
    (the notebook's default). Everything else runs as written.
    """
    out_dir = tmp_path_factory.mktemp("notebook_outputs")
    ns: dict = {"__name__": "__main__"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        for source in code_sources(notebook):
            if CONFIG_MARKER in source:
                source = re.sub(r"^OUT_DIR\s*=.*$", f"OUT_DIR = {str(out_dir)!r}",
                                source, flags=re.M)
            exec(compile(source, "<notebook cell>", "exec"), ns)  # noqa: S102
    ns["_stdout"] = buf.getvalue()
    ns["_out_dir"] = out_dir
    return ns


def test_notebook_runs_end_to_end_with_no_tracking(executed):
    out = executed["_stdout"]
    assert "Traceback" not in out
    assert executed["geo"]["status"].startswith("NOT EVALUABLE")
    assert executed["geo"]["n_geo_5"] == 0 and executed["geo"]["n_geo_8"] == 0
    assert executed["geo"]["n_turns_considered"] > 0      # it looked, and said so


# -------------------------------- the tracking banner (Conor, 2026-11-17)
def test_the_top_of_the_output_says_no_tracking_was_found(executed):
    """A no-tracking run must be impossible to mistake for a tracking run."""
    out = executed["_stdout"]
    status = executed["TRACKING_STATUS"]
    assert status["status"] == "NONE FOUND"
    assert "TRACKING: NONE FOUND" in out
    assert "TO EVALUATE GEOMETRY" in out
    assert "TRACKING = [" in out                  # names what to set in cell 1
    assert "looked in" in out                     # names the directory searched
    assert str(ROOT / "data" / "raw") in out      # ... and it is the real one
    # It is printed before the first table, not buried: the banner precedes the
    # funnel, so scrolling to the top shows it first.
    assert out.index("TRACKING: NONE FOUND") < out.index("band_turns")


def test_the_heading_status_is_echoed_beside_the_number(executed):
    """A reader who scrolls to the bottom sees the same status next to N."""
    out = executed["_stdout"]
    headline = [ln for ln in out.splitlines() if ln.startswith("N = ")]
    assert headline, "the headline N line is missing"
    assert "tracking: NONE FOUND" in headline[0]
    assert executed["assembled"]["N"] == 2295


# ------------------------------------- what the run writes (Conor, 2026-11-17)
#: The fifteen tables one complete run writes. Pinned here independently of
#: the library constant: a run that writes three of them is the defect.
#: ``precedence.csv`` is the Amendment 3 addition (2026-11-18).
FIFTEEN = (
    "assembly.csv", "bounds.csv", "composition.csv", "funnel.csv",
    "geometry.csv", "margin_buckets.csv", "multi_turns.csv",
    "precedence.csv", "random_pairings.csv", "route_a_audit.csv",
    "route_a_turns.csv", "single_turn_geometry.csv", "v4_nearest.csv",
    "variants.csv", "within_3yd.csv",
)


def test_every_table_is_written_not_just_three(executed):
    """A table that exists only in memory is how stale files got read as results."""
    out = executed["_out_dir"]
    assert sorted(p.name for p in out.glob("*.csv")) == sorted(FIFTEEN)
    assert (out / "manifest.json").is_file()
    assert (out / "run_status.txt").is_file()

    text = executed["_stdout"]
    assert f"outputs: {out}" in text
    assert "15 tables + run_status.txt + manifest.json" in text
    assert "17 files written by this run" in text


def test_the_manifest_stamps_the_run_and_says_no_tracking(executed):
    """A stale file is identifiable by its own contents, not by machine memory."""
    path = executed["_out_dir"] / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    assert m["written_utc"].endswith("+00:00")            # a UTC timestamp
    assert m["tracking"] == "none found"                  # this machine, no tracking
    assert m["config"]["mode"] == "notebook"
    assert len(m["outputs"]) == 15
    assert all(len(v["sha256"]) == 64 for v in m["outputs"].values())
    assert any(k.endswith("bdb27_full_db_turns.parquet") for k in m["inputs"])
    assert all(len(v["sha256"]) == 64 for v in m["inputs"].values())


def test_the_output_path_is_printed_at_the_top_and_beside_n(executed):
    text = executed["_stdout"]
    path = str(executed["_out_dir"])
    assert text.index(path) < text.index("band_turns")     # before any table
    line = [ln for ln in text.splitlines() if ln.startswith("N = ")][0]
    following = text.split(line, 1)[1].splitlines()[:3]
    assert any(path in ln and "17 files written" in ln for ln in following)


# ------------------------------- the on-disk run trail (Conor, 2026-11-17)
def test_run_status_records_every_step_and_the_tracking_answer(executed):
    """A run that dies leaves its last step on disk, not only in the console."""
    text = (executed["_out_dir"] / "run_status.txt").read_text(encoding="utf-8")
    assert "started (UTC)" in text
    assert "tracking: NONE" in text
    assert any(ln.startswith("repo") for ln in text.splitlines())
    assert "[exists]" in text                       # each input carries its flag
    for step in ("run started", "tracking searched", "funnel ok", "bounds ok",
                 "geometry NOT EVALUABLE", "route_a 564",
                 "precedence disjoint=True",
                 "singles NOT EVALUABLE",
                 "within_3yd/margin NOT EVALUABLE", "N = 2,295",
                 "wrote 17 files", "complete"):
        assert step in text, f"run_status is missing {step!r}"
    assert (text.index("funnel ok") < text.index("N = 2,295")
            < text.index("complete"))


def test_a_run_that_dies_before_analysis_still_leaves_a_status_file(
        notebook, tmp_path):
    """The folder and the file exist before cell 3 runs; that is the whole point."""
    ns: dict = {"__name__": "__main__"}
    with redirect_stdout(io.StringIO()):
        for source in code_sources(notebook):
            if CONFIG_MARKER in source:
                source = re.sub(r"^OUT_DIR\s*=.*$", f"OUT_DIR = {str(tmp_path)!r}",
                                source, flags=re.M)
            exec(compile(source, "<notebook cell>", "exec"), ns)  # noqa: S102
            if "TRACKING_STATUS = aa.tracking_status" in source:
                break                                  # stop before the funnel

    status = tmp_path / "run_status.txt"
    assert status.is_file(), "the status file must exist before any analysis"
    text = status.read_text(encoding="utf-8")
    assert "started (UTC)" in text
    assert "tracking: NONE" in text or "tracking: FOUND" in text
    assert "player_play.csv" in text and "[exists]" in text
    assert "game_tracking_2023_sample.csv" in text     # the raw listing, with sizes
    assert "bytes" in text
    assert "N =" not in text                           # nothing was measured yet
    assert "---- progress ----" in text


def test_the_default_output_directory_is_the_scripts(notebook):
    """Same directory as ``scripts/ambiguity_attribution.py``: one output set."""
    config = [s for s in code_sources(notebook) if CONFIG_MARKER in s][0]
    assert 'OUT_DIR         = "outputs/ambiguity_attribution"' in config
    assert "WRITE_ARTEFACTS = True" in config


def test_the_notebook_and_the_script_write_the_same_fifteen():
    """Two surfaces, one output set: neither may drop a table the other writes."""
    from bdb27 import ambiguity_attribution as aa

    assert sorted(aa.ARTEFACT_TABLES) == sorted(FIFTEEN)
    assert len(aa.ARTEFACT_TABLES) == 15
    script = (ROOT / "scripts" / "ambiguity_attribution.py").read_text(
        encoding="utf-8")
    for name in FIFTEEN:
        assert f'out / "{name}"' in script, f"the script does not write {name}"


def test_the_config_comment_describes_the_discovery_code(notebook):
    """The comment that lied: it must say globbed, by name, sample excluded."""
    config = [s for s in code_sources(notebook) if CONFIG_MARKER in s][0]
    assert "data/raw" in config
    assert "tracking" in config and "recursively" in config
    assert "sample" in config and "combine" in config
    # and it must not promise a glob it does not do
    assert "game_tracking*.csv" not in config


def test_the_headline_number_is_the_sum_of_its_slots(executed):
    asm = executed["assembled"]
    slots = asm["slots"]
    assert asm["N"] == sum(slots.values())
    assert slots["n_frozen"] == len(executed["single"])
    assert slots["n_route_a"] == len(executed["route_turns"])
    assert sum(c["n"] for c in asm["composition"]) == asm["N"]
    assert [c["slot"] for c in asm["composition"]] == ["frozen", "geo_5", "geo_8",
                                                      "route_a"]


def test_the_shares_of_n_are_printed_and_add_up(executed):
    asm = executed["assembled"]
    assert asm["rule_share_of_N"] + asm["inference_share_of_N"] == pytest.approx(1.0)
    assert asm["rule_share_of_N"] == pytest.approx(asm["slots"]["n_frozen"] / asm["N"])
    assert "RULE share of N" in executed["_stdout"]
    assert "INFERENCE share of N" in executed["_stdout"]


def test_the_single_turn_check_is_present_and_separate_from_recovery(executed):
    singles = executed["singles"]
    assert singles["n_turns"] == len(executed["single"])
    assert singles["status"].startswith("NOT EVALUABLE")
    assert singles["n_within_radius"] == 0
    out = executed["_stdout"]
    assert "D204 reported 471" in out
    assert "adds nothing to N" in out
    # The composition says which turns came from which route.
    assert "the frozen slot is the 1,731 single-candidate turns" in out
    assert "the variants on 1,731 + 870 = 2,601 turns" in out


def test_geometry_and_agreement_say_not_evaluable_rather_than_zero(executed):
    out = executed["_stdout"]
    assert "NOT EVALUABLE" in out
    assert "Cross-check where both routes fired: NOT EVALUABLE" in out
    assert "not a distance between two players" in out


def test_route_a_audit_accounts_for_every_eligible_turn(executed):
    audit = executed["route_audit"]
    assert set(audit["denominator"]) == {executed["route_eligible"]}
    attributed = audit.loc[audit["outcome"] == "attributed", "n"].sum()
    assert int(attributed) == len(executed["route_turns"])
    unresolved = audit.loc[audit["outcome"] != "attributed", "n"].sum()
    assert int(unresolved) > 0                            # misses are counted, not hidden
    assert (audit["n"] >= 0).all()


def test_the_run_does_not_touch_the_committed_artefacts(executed):
    """The redirected fixture writes to tmp; the committed directory is untouched."""
    assert executed["WRITE_ARTEFACTS"] is True
    assert ROOT / "outputs" / "ambiguity_attribution" != executed["_out_dir"]
    assert not (ROOT / "outputs" / "ambiguity_attribution_notebook").exists()


# ------------------------------------------------- amendment: 3 yd, V4, margin
def test_the_within_3yd_table_prints_denominators_and_not_evaluable(executed):
    within3 = executed["within3"]
    out = executed["_stdout"]
    assert within3["n_turns"] == 870
    assert within3["n_evaluated"] == 0
    assert "WITHIN 3 YD AT THE BREAK FRAME" in out
    assert "evaluable 0 of 870 ambiguous turns" in out
    for bucket in ("zero", "exactly one", "exactly two", "three or more"):
        assert bucket in out


def test_the_margin_table_is_its_own_table_with_counts(executed):
    margin = executed["margin"]
    out = executed["_stdout"]
    assert margin["n_turns"] == 870
    assert "MARGIN — second-nearest minus nearest" in out
    for bucket in ("< 0.25 yd", "0.25–0.5 yd", "0.5–1 yd", "> 1 yd"):
        assert bucket in out
    assert "No bucket is a gate" in out


def test_v4_is_beside_the_variants_and_wears_tie_break(executed):
    v4 = executed["v4"]
    out = executed["_stdout"]
    assert v4["tie_break"] is True
    assert "TIE-BREAK" in v4["variant"]
    assert "V1 INFERENCE — target" in out
    assert "V4 INFERENCE — nearest inside 3 yd (TIE-BREAK)" in out
    assert v4["n_frozen_turns"] == 1731
    assert v4["n_nearest_turns"] == 0            # no tracking on this machine


def test_v4_is_not_a_slot_of_the_headline_number(executed):
    out = executed["_stdout"]
    asm = executed["assembled"]
    assert asm["N"] == 2295
    assert "V4 (INFERENCE — TIE-BREAK, not a slot of N)" in out
    assert "headline N, its four slots" in out
    assert ("2,295 = 1,731 frozen + 0 geo(5) + 0 geo(8) + 564 Route A") in out
    assert "overlap with Route A's 564 turns" in out


# ------------- the precedence is executed, not hand-assembled (Amendment 3)
def test_the_slots_are_computed_by_one_pipeline_call_not_by_hand(notebook):
    """The defect: Route A and geometry were each handed ``exclude=frozen_keys``.

    Both therefore saw the same 1,277-turn pool, Route A ran before geometry, and
    a turn claimed by both was summed twice (2,884 on the tracked run). The order
    and the shared pool now live in ``aa.attribution_pipeline``; a notebook cell
    that calls either route directly can re-introduce the pool split, and a cell
    that calls ``assemble_attribution`` can re-introduce the unchecked sum.
    """
    sources = code_sources(notebook)
    pipeline_cells = [s for s in sources if "aa.attribution_pipeline" in s]
    assert len(pipeline_cells) == 1, (
        f"{len(pipeline_cells)} cells compute the four slots; the precedence must "
        "have one implementation")
    for source in sources:
        assert "aa.assemble_attribution" not in source, (
            "the notebook assembles N by hand instead of taking the pipeline's")
        assert "aa.route_a_tables(" not in source, (
            "a cell runs Route A itself, so its pool is not the pipeline's")
        assert "aa.geometry_slots(" not in source, (
            "a cell runs the radii itself, so its pool is not the pipeline's")
    script = (ROOT / "scripts" / "ambiguity_attribution.py").read_text(
        encoding="utf-8")
    assert "aa.attribution_pipeline(" in script
    assert "aa.route_a_tables(" not in script
    assert "aa.geometry_slots(" not in script


def test_the_four_slots_are_disjoint_and_the_overlap_is_stated(executed):
    pipe = executed["pipe"]
    asm = executed["assembled"]
    assert pipe["disjoint"] is True
    assert asm["n_double_counted"] == 0
    assert asm["disjoint"] is True
    assert asm["N"] == sum(asm["slots"].values())
    prec = pipe["precedence"]
    items = list(prec["item"])
    assert items[0] == "pool_with_visible_route_runner"
    assert "overlap_reference_with_geo" in items
    assert "N_if_both_passes_summed" in items
    assert prec["denominator"].notna().all(), "a count without its denominator"
    out = executed["_stdout"]
    assert "slots disjoint: True" in out
    assert "turns in more than one slot 0 of" in out
    assert "Route A overlap with the radii:" in out
    assert "upper bound on N, never N" in out


def test_route_a_is_a_slot_on_the_residual_not_a_parallel_pass(executed):
    pipe = executed["pipe"]
    asm = executed["assembled"]
    frozen = asm["slots"]["n_frozen"]
    geo_taken = len(pipe["geo_taken_keys"])
    assert pipe["route_eligible"] == pipe["frame_turns"] - frozen - geo_taken
    assert pipe["route_eligible_reference"] == pipe["frame_turns"] - frozen
    assert set(executed["route_audit"]["denominator"]) == {pipe["route_eligible"]}
    assert set(executed["route_reference_audit"]["denominator"]) == {
        pipe["route_eligible_reference"]}
    # the reference pass is a counterfactual: it is not the slot and adds nothing
    assert asm["slots"]["n_route_a"] == len(executed["route_turns"])
    assert pipe["overlap"] == 0          # no tracking here: nothing to overlap
    assert pipe["route_eligible"] <= pipe["route_eligible_reference"]
