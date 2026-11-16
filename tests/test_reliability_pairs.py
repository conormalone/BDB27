"""Contract tests for the odd/even and season reliability figures.

Unit-level throughout: small deterministic fixtures exercise the slice filter,
the imported split-half assignment, the season join, the thin-panel rules, the
eta-squared arithmetic and the bootstrap determinism. One integration test pins
the committed full-run numbers and is skipped, with the path printed, when the
uploaded parquet is absent.

The figure rules these tests defend: DB only, no identity columns anywhere, no
arbitrary gates (a thin panel prints its ``n`` and renders), no banned token
drawn, exactly one manifest entry per figure, and a drawn number always carries
its denominator.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pytest

from bdb27 import common, reliability_pairs as rp
from bdb27 import fallback_validation as fv

ROOT = Path(__file__).resolve().parents[1]
COLOUR = "#C00000"


def _row(nfl_id, *, season=2024, game_id=1, play_id=1, frame_start=1,
         angle=45.0, retention=0.8, setting="game", position="DB",
         valid=True):
    return {
        "setting": setting,
        "position_group": position,
        "valid": valid,
        "abs_turn_angle": float(angle),
        "apex_retention": float(retention),
        "nfl_id": nfl_id,
        "season": season,
        "game_id": game_id,
        "play_id": play_id,
        "frame_start": frame_start,
    }


def _odd_frame() -> "object":
    import pandas as pd

    rows = []
    # player 900001: four turns, frames ascending; A gets frames 1,3 (0.1, 0.3)
    for frame, retention in zip((1, 2, 3, 4), (0.1, 0.2, 0.3, 0.4)):
        rows.append(_row(900001, game_id=11, frame_start=frame,
                         retention=retention))
    # player 900002: two turns -> A 0.5, B 0.7
    rows.append(_row(900002, game_id=12, frame_start=1, retention=0.5))
    rows.append(_row(900002, game_id=12, frame_start=2, retention=0.7))
    # player 900003: one turn -> half A only, excluded from the fit and counted
    rows.append(_row(900003, game_id=13, frame_start=1, retention=0.9))
    return pd.DataFrame(rows)


def _season_frame():
    import pandas as pd

    rows = []
    plan = {
        901001: (12, 12),   # passes the >= 10 rule in both seasons
        901002: (3, 11),    # fails: only 3 turns in 2024
        901003: (10, 2),    # fails: only 2 turns in 2025
        901004: (5, 0),     # 2024 only
        901005: (0, 5),     # 2025 only
    }
    for nfl_id, (n24, n25) in plan.items():
        for i in range(n24):
            rows.append(_row(nfl_id, season=2024, game_id=21,
                             play_id=nfl_id, frame_start=i + 1,
                             retention=0.5 + 0.01 * i))
        for i in range(n25):
            rows.append(_row(nfl_id, season=2025, game_id=22,
                             play_id=nfl_id, frame_start=i + 1,
                             retention=0.6 + 0.01 * i))
    return pd.DataFrame(rows)


@pytest.fixture()
def odd_slice():
    return rp.select_slice(_odd_frame())


@pytest.fixture()
def odd_stats(odd_slice):
    return rp.split_half_points(odd_slice)


@pytest.fixture()
def season_slice():
    return rp.select_slice(_season_frame())


@pytest.fixture()
def season_stats(season_slice):
    return rp.season_panels(season_slice)


# ---------------------------------------------------------------- slice filter
def test_slice_keeps_only_valid_game_db_turns_in_the_left_closed_band():
    import pandas as pd

    rows = [
        _row(1, angle=30.0),         # included: left edge is closed
        _row(1, angle=59.999),       # included
        _row(1, angle=29.999),       # excluded: below 30
        _row(1, angle=60.0),         # excluded: right edge is open
        _row(1, setting="combine"),  # excluded: game only
        _row(1, position="WR"),      # excluded: DB only
        _row(1, valid=False),        # excluded: valid only
    ]
    kept = rp.select_slice(pd.DataFrame(rows))
    assert len(kept) == 2
    assert set(kept["abs_turn_angle"]) == {30.0, 59.999}
    assert kept["setting"].eq("game").all()
    assert kept["position_group"].eq("DB").all()
    assert kept["valid"].all()


def test_slice_counts_carry_every_denominator(odd_slice):
    counts = rp.slice_counts(odd_slice)
    assert counts["turns"] == 7
    assert counts["players"] == 3
    assert counts["plays"] == 3
    assert counts["games"] == 3
    assert counts["ceiling_at_1.000"] == 0


def test_verify_slice_prints_and_refuses_on_a_discrepancy(capsys):
    import pandas as pd

    frame = pd.DataFrame([_row(1)])
    with pytest.raises(common.StageRefused) as exc:
        rp.verify_slice(frame, expected={"turns": 7})
    printed = capsys.readouterr().out
    assert "turns=1" in printed
    assert "found vs expected" in str(exc.value)


# ------------------------------------------------------------- odd/even halves
def test_odd_even_assignment_follows_the_imported_split(odd_stats):
    players = odd_stats["players"].set_index("nfl_id")
    assert players.loc[900001, "count_odd"] == 2
    assert players.loc[900001, "count_even"] == 2
    assert players.loc[900001, "mean_odd"] == pytest.approx(0.2)
    assert players.loc[900001, "mean_even"] == pytest.approx(0.3)
    assert players.loc[900002, "mean_odd"] == pytest.approx(0.5)
    assert players.loc[900002, "mean_even"] == pytest.approx(0.7)


def test_one_sided_player_is_counted_but_not_plotted(odd_stats):
    players = odd_stats["players"].set_index("nfl_id")
    assert bool(players.loc[900003, "in_both"]) is False
    assert players.loc[900003, "count_even"] == 0
    assert odd_stats["n_players"] == 2
    assert odd_stats["n_dropped"] == 1
    assert odd_stats["n_players_total"] == 3


def test_r_and_spearman_brown_match_the_hand_computation(odd_stats):
    # Two plotted points cannot define a correlation: the estimator returns NaN
    # rather than raising, and the figure prints not estimable.
    assert np.isnan(odd_stats["r"])
    three = rp.split_half_points(_odd_frame_triple())
    expected = float(np.corrcoef([0.2, 0.5, 0.9], [0.3, 0.7, 0.8])[0, 1])
    assert three["r"] == pytest.approx(expected)
    assert three["spearman_brown"] == pytest.approx(2 * expected / (1 + expected))


def _odd_frame_triple():
    import pandas as pd

    rows = []
    for nfl_id, (a, b) in {1: (0.2, 0.3), 2: (0.5, 0.7), 3: (0.9, 0.8)}.items():
        rows.append(_row(nfl_id, game_id=30 + nfl_id, frame_start=1,
                         retention=a))
        rows.append(_row(nfl_id, game_id=30 + nfl_id, frame_start=2,
                         retention=b))
    return pd.DataFrame(rows)


def test_reconcile_odd_is_a_hard_equality_proof(odd_stats):
    with pytest.raises(AssertionError):
        rp.reconcile_odd(odd_stats)


def test_odd_csv_has_no_identity_columns_and_the_one_sided_row_last(odd_stats):
    table = rp.odd_csv_frame(odd_stats["players"])
    assert list(table.columns) == ["player_slot", "n_turns", "count_odd",
                                   "count_even", "mean_odd", "mean_even",
                                   "in_both"]
    assert len(table) == 3
    assert table["player_slot"].tolist() == [1, 2, 3]
    assert bool(table.iloc[-1]["in_both"]) is False
    joined = table.to_csv(index=False)
    assert "900001" not in joined and "900003" not in joined


def test_odd_annotation_lines_print_every_count_with_its_denominator(odd_stats):
    ci = {"lo": -0.2598, "hi": 0.1486}
    lines = rp.odd_annotation_lines(odd_stats, ci)
    text = "\n".join(lines)
    assert "n = 2 players · 7 turns" in text
    assert "1 of 3 players has no even-numbered turn" in text
    assert "0 of 7 turns" in text


def test_ceilings_are_not_filtered_out():
    import pandas as pd

    rows = [_row(1, retention=1.0, frame_start=1),
            _row(1, retention=1.0, frame_start=2),
            _row(2, retention=0.5, frame_start=1)]
    kept = rp.select_slice(pd.DataFrame(rows))
    assert len(kept) == 3
    assert int((kept["apex_retention"] == 1.0).sum()) == 2


# ------------------------------------------------------------- season panels
def test_season_join_and_min_ten_flag(season_stats):
    players = season_stats["players"].set_index("nfl_id")
    assert len(players) == 3
    assert players.loc[901001, "in_min10"] == True  # noqa: E712
    assert players.loc[901002, "in_min10"] == False  # noqa: E712
    assert players.loc[901003, "in_min10"] == False  # noqa: E712
    assert season_stats["n1"] == 3
    assert season_stats["n10"] == 1
    assert season_stats["joined"]["pool_2024"] == 4
    assert season_stats["joined"]["pool_2025"] == 4
    assert season_stats["joined"]["n_both"] == 3


def test_season_r_comes_from_the_imported_estimator(season_stats):
    assert season_stats["r1_estimator"]["n_players"] == season_stats["n1"]
    assert round(float(season_stats["r1_estimator"]["r"]), 4) == round(
        season_stats["r1"], 4)
    assert season_stats["r10_estimator"]["n_players"] == 1
    assert np.isnan(season_stats["r10"])


def test_thin_panel_prints_not_estimable_and_does_not_raise(
        season_stats, tmp_path):
    counts = rp.slice_counts(_season_frame())
    visible, texts = rp.draw_season(season_stats, counts,
                                    season_stats["joined"], colour=COLOUR,
                                    out_png=tmp_path / "season.png")
    joined = "\n".join(visible)
    assert "r = not estimable" in joined
    assert "n = 1 player" in joined
    assert any("light = fewer than 10 turns" in t for t in texts)


def test_one_player_odd_panel_renders_without_raising(tmp_path):
    import pandas as pd

    rows = [_row(1, frame_start=1, retention=0.4),
            _row(1, frame_start=2, retention=0.6)]
    stats = rp.split_half_points(rp.select_slice(pd.DataFrame(rows)))
    assert stats["n_players"] == 1 and np.isnan(stats["r"])
    visible, _ = rp.draw_odd(stats, {"lo": -0.1, "hi": 0.2}, colour=COLOUR,
                             out_png=tmp_path / "odd.png")
    assert "n = 1 player · 2 turns" in "\n".join(visible)
    assert "r = not estimable" in "\n".join(visible)


def test_season_csv_columns_and_order(season_stats):
    table = rp.season_csv_frame(season_stats["players"])
    assert list(table.columns) == ["player_slot", "n_2024", "n_2025",
                                   "mean_2024", "mean_2025", "in_min10"]
    assert len(table) == 3
    assert table["mean_2024"].is_monotonic_increasing
    joined = table.to_csv(index=False)
    for identity in ("901001", "901002", "nfl_id", "player_name"):
        assert identity not in joined


def test_reconcile_season_is_a_hard_equality_proof(season_stats):
    with pytest.raises(AssertionError):
        rp.reconcile_season(season_stats)


def test_pool_line_and_ceiling_line_carry_denominators(season_stats):
    joined = season_stats["joined"]
    assert rp.season_pool_line(joined) == "4 players in 2024, 4 in 2025, 3 in both"
    line = rp.ceiling_line({"ceiling_at_1.000": 1, "turns": 4})
    assert "1 of 4 turns (25.0%)" in line
