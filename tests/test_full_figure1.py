"""Figure 1 from the uploaded full-data run (D185).

The real artefact is not in git (it is under ``data/interim``), so the
end-to-end test skips when it is absent. Every assertion either recomputes from
the artefact or reads the artefact the renderer wrote; nothing trusts a number
typed into this file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from bdb27 import common, figures, full_run

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / full_run.ARTEFACT_DIR
HAS_REAL = all((SOURCE / name).exists() for name in full_run.PINNED)
needs_real = pytest.mark.skipif(
    not HAS_REAL, reason="uploaded full-run artefacts are not on this host")

PINNED_SHA = {name: pin["sha256"] for name, pin in full_run.PINNED.items()}


@pytest.fixture(scope="module")
def cfg():
    return common.load_config(ROOT / "config.yaml")


def _run(cfg, out_dir: Path):
    return full_run.run(cfg, out_dir=out_dir, root=ROOT)


@needs_real
def test_figure1_redrawn_from_full_artefact(cfg, tmp_path):
    result = _run(cfg, tmp_path / "figures")

    # The headline, from the table the renderer built.
    assert int(result["combine"]["count"]) == 26
    assert int(result["combine"]["setting_total"]) == 1179
    assert int(result["game"]["count"]) == 7777
    assert int(result["game"]["setting_total"]) == 58664
    assert abs(result["ratio"] - 6.0114727679929505) < 1e-9
    assert result["title"] == (
        "The hole: games demand 6.0× the share of 30–60° DB breaks the "
        "Combine supplies")

    # 30-60 is still the largest proportional shortfall (the figure's guard
    # would have raised inside band_table/build_plot otherwise).
    table = result["table"]
    shares = table.pivot(index="band", columns="setting",
                         values="share_within_setting")
    ratios = shares["game"] / shares["combine"]
    assert ratios.idxmax() == "30–60°"
    assert ratios.max() == pytest.approx(result["ratio"])
    assert ratios["90–120°"] == pytest.approx(1.6452, abs=1e-3)

    # Provenance: CSV, manifest entry and the dedicated record.
    csv = pd.read_csv(result["csv"])
    assert set(csv["provenance"]) == {"FULL"}
    assert set(csv["label"]) == {"FULL DATA OUTPUT"}
    assert set(csv["stamp"]) == {"FULL DATA OUTPUT"}
    assert len(csv) == 12

    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    entry = manifest["figures"]["fig_the_hole"]
    assert manifest["provenance"] == "FULL"
    assert entry["provenance"] == "FULL"
    assert entry["stamp"] == "FULL DATA OUTPUT"
    assert "7,777 game turns" in entry["claim"]

    record = json.loads(result["provenance"].read_text(encoding="utf-8"))
    assert {a["name"]: a["sha256"] for a in record["source_artefacts"]} == PINNED_SHA
    assert record["crosscheck"]["matches_uploaded_band_table"] is True
    assert record["crosscheck"]["rows_checked"] == 12
    assert record["identity"]["config_sha"] == common.config_fingerprint(cfg)
    assert record["counts"]["game_share"] == pytest.approx(0.13256852584208373)
    assert record["counts"]["combine_share"] == pytest.approx(0.02205258693808312)

    # Per-season coverage is recorded, not hidden.
    seasons = record["season_coverage"]["by_season"]
    assert seasons["2023"]["valid_db_turns"] == 8016
    assert seasons["2024"]["valid_db_turns"] == 21275
    assert seasons["2025"]["valid_db_turns"] == 29373
    assert sum(v["valid_db_turns"] for v in seasons.values()) == 58664
    assert any("2023 tracking file is about a third" in caveat
               for caveat in entry["caveats"])


@needs_real
def test_drawn_text_is_heading_and_data_only(cfg, tmp_path):
    result = _run(cfg, tmp_path / "figures")
    dump = json.loads(result["drawn_text"].read_text(encoding="utf-8"))
    strings = dump["strings"]

    title = ("The hole: games demand 6.0× the share of 30–60° DB breaks the "
             "Combine supplies")
    bands = {"30–60°", "60–90°", "90–120°", "120–150°", "150–180°", ">180°"}
    known = {title, "(a) Raw counts (turns)",
             "(b) Share of turns within setting (%)", "Combine", "Game"} | bands
    counts = {"26", "499", "7777", "13077"}
    axes = {"0", "5000", "10000", "15000", "10", "20", "30", "40", "50"}
    assert set(strings) == known | counts | axes
    assert dump["banned_hits"] == []

    lowered = " ".join(strings).lower()
    for word in ("placeholder", "unvalidated", "sample output", "source:",
                 "caveat", "exposure", "denominator", "p-full"):
        assert word not in lowered


@needs_real
def test_two_full_renders_are_byte_identical(cfg, tmp_path):
    first = _run(cfg, tmp_path / "a")
    second = _run(cfg, tmp_path / "b")
    assert (hashlib.sha256(first["png"].read_bytes()).hexdigest()
            == hashlib.sha256(second["png"].read_bytes()).hexdigest())


@needs_real
def test_band_table_must_match_the_uploaded_band_table(cfg, tmp_path):
    game = full_run.load_game_turns(SOURCE)
    combine = full_run.load_combine_turns(ROOT / full_run.COMBINE_TURNS)
    turns = full_run.assemble_turns(game, combine)
    table, context = figures.band_table(
        turns,
        [float(x) for x in cfg.get("figures.hole.plot_bands.turn_angle_deg")],
        str(cfg.get("figures.hole.bands.overflow_label")),
        str(cfg.get("figures.hole.position_group")))

    bands = pd.read_csv(SOURCE / full_run.ARTEFACT_BANDS)
    mask = ((bands["position_group"] == "DB")
            & (bands["setting"] == "combine") & (bands["band"] == "30–60°"))
    bands.loc[mask, "count"] = 999
    tampered = tmp_path / "bands.csv"
    bands.to_csv(tampered, index=False)
    with pytest.raises(common.StageRefused, match="uploaded band table"):
        full_run.check_band_csv(tampered, table, context)


def test_checksum_mismatch_is_refused(tmp_path):
    for name in full_run.PINNED:
        (tmp_path / name).write_bytes(b"not the uploaded run")
    with pytest.raises(common.StageRefused, match="do not match their pins"):
        full_run.verify_sources(tmp_path, ROOT)


def test_sample_path_keeps_its_stamp():
    """The full renderer must not have moved the sample contract."""
    assert figures.stamp_text("sample") == (
        "PLACEHOLDER_SAMPLE — UNVALIDATED SAMPLE OUTPUT")
    assert common.provenance("sample") == "PLACEHOLDER_SAMPLE"
    # The sample turn table still carries the sample figure's denominators.
    turns = figures.load_turns(ROOT / "outputs/turns/turns.parquet")
    table, context = figures.band_table(
        turns, [30.0, 60.0, 90.0, 120.0, 150.0, 180.0], ">180", "DB")
    game = table[table["setting"] == "game"]
    assert int(game.loc[game["band"] == "30–60°", "count"].iloc[0]) == 326
    assert int(game["setting_total"].iloc[0]) == 2262
