"""Tests for the geometric rusher / blocker derivation (design §5.4a, D75).

Every world here is hand-built: dataframes in memory, no fixture generator and
nothing written to disk.  The contract under test is the one in
``notes/dl-design.md`` §5.4a:

* the three count columns (``n_rushers``, ``blitz``, ``n_blockers``) are
  play-level and identical on every row of a play;
* ``primary_blocker_group`` / ``primary_blocker_group_source`` are **per
  rusher**, taken from that rusher's own primary blocker — the first test is
  the regression for the defect where every rusher got one play-level label;
* a structural failure nulls the whole play with one reason; an engagement
  failure nulls only that rusher's row and never borrows another rusher's
  label;
* nothing is zero-filled and two identical calls are byte-identical.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dl import dl_blockers as B
from dl import dl_common as D

# The module reads a flat constant block (design §5.4a constants live under
# ``bdb27_dl.game`` in ``config.yaml``); no constant is ever defaulted.
K = D.dl(D.load_config())["game"]

GAME, PLAY = "2023_01_AAA_BBB", "101"

# --- player ids: offence 1-11, defence 21-31 ------------------------------- #
LT, LG, C, RG, RT = 1, 2, 3, 4, 5
QB, RB, TE, WR1, WR2, WR3 = 6, 7, 8, 9, 10, 11
R_LE, R_LI, R_RI, R_RE = 21, 22, 23, 24
LB1, LB2, CB1, CB2, CB3, S1, S2 = 25, 26, 27, 28, 29, 30, 31

#: Positions used by the roster worlds.  ``None`` means "not in the cohort".
POSITIONS: dict[int, str] = {
    LT: "T", LG: "G", C: "C", RG: "G", RT: "T", QB: "QB", RB: "RB",
    TE: "TE", WR1: "WR", WR2: "WR", WR3: "WR",
}

#: Second LOS candidate, far enough to the defensive side that the median
#: distance rule unambiguously picks ``los_c1 = 50``.
LOS_X, LOS_C2 = 50.0, 56.0


def _times(start: float = 0.0, last: float = 2.0) -> tuple[float, ...]:
    """Shared tracking grid, 0.1 s apart, inclusive of both ends."""
    n0, n1 = int(round(start / 0.1)), int(round(last / 0.1))
    return tuple(round(0.1 * i, 1) for i in range(n0, n1 + 1))


def _build(spec: dict[int, tuple[float, float, float, float]], *,
           los_c1: float = LOS_X, los_c2: float = LOS_C2,
           times: tuple[float, ...] | None = None,
           direction: str = "right",
           y_at: dict[int, dict[float, float]] | None = None,
           positions: dict[int, str] | None = None,
           keep: set[int] | None = None):
    """Long tracking frame + snaps + plays (+ roster) for one synthetic play.

    ``spec`` maps ``nfl_id -> (x_snap, y_snap, x_end, y_end)``; positions are
    linearly interpolated over the tracking grid so that ``advance`` is
    ``y_snap - y_end`` by construction.  ``y_at`` overrides the ``y`` of a
    player on named frames (used only for the alternating-engagement world).
    """
    times = _times() if times is None else times
    span = times[-1]
    rows = []
    for nfl_id, (x0, y0, x1, y1) in spec.items():
        if keep is not None and nfl_id not in keep:
            continue
        for t in times:
            frac = t / span
            y = y0 + (y1 - y0) * frac
            if y_at and nfl_id in y_at and t in y_at[nfl_id]:
                y = y_at[nfl_id][t]
            rows.append((GAME, PLAY, nfl_id, t,
                         x0 + (x1 - x0) * frac, y))
    frames = pd.DataFrame(rows, columns=["game_id", "play_id", "nfl_id",
                                         "t_rel", "x", "y"])
    ids = sorted({r[2] for r in rows})
    players = pd.DataFrame({"game_id": GAME, "play_id": PLAY, "nfl_id": ids})
    plays = pd.DataFrame({"game_id": [GAME], "play_id": [PLAY],
                          "los_c1": [los_c1], "los_c2": [los_c2],
                          "play_direction": [direction]})
    roster = None
    if positions is not None:
        roster = pd.DataFrame({
            "nfl_id": sorted(positions),
            "nfl_position": [positions[i] for i in sorted(positions)]})
    return frames, players, plays, roster


# --------------------------------------------------------------------------- #
# Worlds
# --------------------------------------------------------------------------- #
def _standard_spec() -> dict[int, tuple[float, float, float, float]]:
    """4-man rush, 5-man protection, 22 tracked players."""
    spec = {
        # offensive line, drifting half a yard forward (advance < 0 -> stays)
        LT: (49.5, 45.0, 49.7, 45.0),
        LG: (49.5, 47.5, 49.7, 47.5),
        C: (49.5, 50.0, 49.7, 50.0),
        RG: (49.5, 52.5, 49.7, 52.5),
        RT: (49.5, 55.0, 49.7, 55.0),
        QB: (45.5, 50.0, 45.5, 50.0),
        RB: (45.5, 53.0, 45.5, 53.0),
        TE: (47.5, 57.0, 47.5, 57.0),
        WR1: (47.5, 38.0, 47.5, 38.0),
        WR2: (47.5, 62.0, 47.5, 62.0),
        WR3: (48.0, 43.0, 48.0, 43.0),
        # four rushers: two outside, two inside
        R_LE: (50.8, 45.0, 48.9, 45.0),
        R_LI: (50.8, 48.5, 48.9, 48.5),
        R_RI: (50.8, 51.5, 48.9, 51.5),
        R_RE: (50.8, 55.0, 48.9, 55.0),
        # non-rushing defenders, all clearly on the defensive side
        LB1: (51.5, 47.0, 51.5, 47.0),
        LB2: (51.5, 53.0, 51.5, 53.0),
        CB1: (52.0, 40.0, 52.0, 40.0),
        CB2: (52.0, 60.0, 52.0, 60.0),
        CB3: (52.0, 35.0, 52.0, 35.0),
        S1: (52.5, 43.0, 52.5, 43.0),
        S2: (52.5, 57.0, 52.5, 57.0),
    }
    return spec


def world_standard(positions: dict[int, str] | None = None, **kw):
    return _build(_standard_spec(), positions=positions, **kw)


def _blitz_spec() -> dict[int, tuple[float, float, float, float]]:
    """6-man pressure: four linemen, an edge LB and a second LB; back stays in."""
    spec = _standard_spec()
    spec.update({
        LT: (49.5, 45.0, 49.7, 45.0),
        RB: (48.9, 42.0, 48.9, 42.0),     # stays in, outside the tackle box
        TE: (47.5, 60.0, 47.5, 60.0),
        WR1: (47.5, 36.0, 47.5, 36.0),
        WR2: (47.5, 64.0, 47.5, 64.0),
        WR3: (48.0, 40.0, 48.0, 40.0),
        LB1: (50.4, 42.0, 48.9, 42.0),    # blitzing LB off the left edge
        LB2: (51.2, 56.0, 49.4, 56.0),    # sixth rusher on the right edge
        CB1: (52.0, 34.0, 52.0, 34.0),
        CB2: (52.0, 50.0, 52.0, 50.0),
        CB3: (52.0, 66.0, 52.0, 66.0),
        S1: (52.5, 38.0, 52.5, 38.0),
        S2: (52.5, 62.0, 52.5, 62.0),
        R_LE: (50.8, 45.0, 48.9, 45.0),
        R_LI: (50.8, 48.5, 48.9, 48.5),
        R_RI: (50.8, 51.5, 48.9, 51.5),
        R_RE: (50.8, 55.0, 48.9, 55.0),
    })
    return spec


def world_blitz(**kw):
    return _build(_blitz_spec(), positions=POSITIONS, **kw)


def world_double_team(**kw):
    """The TE chips the left edge: two protectors in radius, LT consistently nearer."""
    spec = _standard_spec()
    spec[TE] = (49.8, 46.3, 49.8, 46.3)
    return _build(spec, positions=POSITIONS, **kw)


def world_ambiguous(**kw):
    """Two equidistant protectors alternate as the nearest across all frames."""
    spec = {
        LT: (49.5, 45.0, 49.7, 45.0),
        LG: (49.5, 47.5, 49.7, 47.5),
        C: (49.5, 50.0, 49.7, 50.0),
        RG: (49.5, 52.5, 49.7, 52.5),
        RT: (49.5, 55.0, 49.7, 55.0),
        QB: (45.5, 50.0, 45.5, 50.0),
        RB: (49.5, 51.5, 49.5, 51.5),     # tied candidate B
        TE: (49.5, 48.5, 49.5, 48.5),     # tied candidate A
        WR1: (47.5, 38.0, 47.5, 38.0),
        WR2: (47.5, 60.0, 47.5, 60.0),
        WR3: (48.0, 43.0, 48.0, 43.0),
        R_LE: (50.8, 45.0, 49.2, 45.0),   # unambiguous rusher
        LB1: (50.8, 50.0, 49.2, 50.0),    # the ambiguous rusher
        CB1: (52.0, 36.0, 52.0, 36.0),
        CB2: (52.0, 44.0, 52.0, 44.0),
        CB3: (52.0, 58.0, 52.0, 58.0),
        S1: (52.5, 40.0, 52.5, 40.0),
        S2: (52.5, 62.0, 52.5, 62.0),
        LB2: (52.5, 54.0, 52.5, 54.0),
        32: (52.6, 30.0, 52.6, 30.0),
        33: (52.8, 68.0, 52.8, 68.0),
        34: (52.4, 34.0, 52.4, 34.0),
    }
    # 20 frames from 0.1 s; the 0.1 s row is the snap frame, so every frame
    # the engagement sees carries one of the two alternating lateral offsets.
    times = _times(0.1, 2.0)
    y_at = {LB1: {t: (49.0 if i % 2 == 0 else 51.0)
                  for i, t in enumerate(times)}}
    return _build(spec, times=times, y_at=y_at, positions=POSITIONS, **kw)


def world_truncated(**kw):
    keep = {LT, LG, C, RG, RT, QB, R_LE, R_LI, R_RI, R_RE, LB1, CB1}
    return _build(_standard_spec(), positions=POSITIONS, keep=keep, **kw)


def world_unclassified(**kw):
    """A defender drifts 0.5 yd off the line inside the LOS band: undecidable."""
    spec = _standard_spec()
    spec[S1] = (50.5, 58.0, 50.0, 58.0)
    return _build(spec, positions=POSITIONS, **kw)


def world_zero_rushers(**kw):
    spec = _standard_spec()
    for rid in (R_LE, R_LI, R_RI, R_RE):
        x0, y0, _x1, y1 = spec[rid]
        spec[rid] = (x0, y0, x0, y1)
    return _build(spec, positions=POSITIONS, **kw)


def world_ambiguous_los(**kw):
    return _build(_standard_spec(), positions=POSITIONS,
                  los_c2=LOS_X + 0.5, **kw)


WORLDS = {
    "standard": world_standard,
    "blitz": world_blitz,
    "double_team": world_double_team,
    "ambiguous": world_ambiguous,
    "truncated": world_truncated,
    "unclassified": world_unclassified,
    "zero_rushers": world_zero_rushers,
    "ambiguous_los": world_ambiguous_los,
}


def _derive(built) -> tuple[pd.DataFrame, dict]:
    frames, players, plays, roster = built
    return B.derive_blockers(K, frames=frames, players=players, plays=plays,
                             frame_tol_s=0.1, roster=roster)


def _labels(out: pd.DataFrame, ids) -> dict[int, object]:
    """Per-rusher label as seen on that rusher's own row."""
    sub = out.loc[out["nfl_id"].isin(ids)]
    return dict(zip(sub["nfl_id"], sub["primary_blocker_group"]))


# --------------------------------------------------------------------------- #
# 1. The regression: per-rusher labels, not one label per play
# --------------------------------------------------------------------------- #
def test_standard_play_counts_and_per_rusher_labels():
    out, summary = _derive(world_standard(positions=POSITIONS))

    assert len(out) == 22
    assert (out["n_rushers"] == 4).all()
    assert (out["blitz"] == 0.0).all()
    assert (out["n_blockers"] == 5).all()
    assert (out["status"] == "OK").all()

    labels = _labels(out, [R_LE, R_LI, R_RI, R_RE])
    assert labels == {R_LE: "OT", R_LI: "interior",
                      R_RI: "interior", R_RE: "OT"}
    # The defect: one plurality label broadcast to every rusher.  Two edges
    # and two interior rushers cannot share a single label.
    rusher_labels = out.loc[out["nfl_id"].isin([R_LE, R_LI, R_RI, R_RE]),
                            "primary_blocker_group"]
    assert rusher_labels.nunique() == 2
    assert len(set(rusher_labels)) > 1

    # Non-rushers carry no label; the counts are still play-level.
    non_rusher = out.loc[~out["nfl_id"].isin([R_LE, R_LI, R_RI, R_RE])]
    assert non_rusher["primary_blocker_group"].isna().all()
    assert non_rusher["primary_blocker_group_source"].isna().all()

    assert summary["n_plays_derivable"] == 1
    assert summary["n_plays_na"] == 0


def test_standard_labels_source_is_roster_when_positions_passed():
    out, summary = _derive(world_standard(positions=POSITIONS))
    rushers = out.loc[out["nfl_id"].isin([R_LE, R_LI, R_RI, R_RE])]
    assert set(rushers["primary_blocker_group_source"]) == {"roster"}
    assert summary["label_source_counts"] == {"roster": 4, "geometry": 0}


# --------------------------------------------------------------------------- #
# 2. Six-man pressure: the blitzing LB is blocked by the back, the edge by the T
# --------------------------------------------------------------------------- #
def test_blitz_play_counts_and_lb_row_label():
    out, _ = _derive(world_blitz())

    assert (out["n_rushers"] == 6).all()
    assert (out["blitz"] == 1.0).all()
    assert (out["n_blockers"] == 6).all()

    labels = _labels(out, [LB1, R_LE])
    assert labels[LB1] == "RB"
    assert labels[R_LE] == "OT"


# --------------------------------------------------------------------------- #
# 3. Double team: the consistently nearer protector's group wins, per rusher
# --------------------------------------------------------------------------- #
def test_double_team_nearer_blocker_wins_for_that_rusher_only():
    out, _ = _derive(world_double_team())

    assert (out["n_blockers"] == 6).all()    # five linemen + the chipping TE
    labels = _labels(out, [R_LE, R_LI])
    assert labels[R_LE] == "OT"              # LT nearer than the TE every frame
    assert labels[R_LI] == "interior"        # untouched by the left-edge fight
    assert labels[R_LE] != labels[R_LI]


# --------------------------------------------------------------------------- #
# 4. Ambiguous engagement: only that rusher's row goes NA
# --------------------------------------------------------------------------- #
def test_ambiguous_engagement_is_row_level():
    out, summary = _derive(world_ambiguous())

    assert (out["n_rushers"] == 2).all()
    assert (out["n_blockers"] == out["n_blockers"].iloc[0]).all()
    assert out["n_blockers"].notna().all()

    row = out.loc[out["nfl_id"] == LB1]
    assert len(row) == 1
    assert row["status"].iloc[0] == "NA"
    assert row["reason"].iloc[0] == "ambiguous_engagement"
    assert pd.isna(row["primary_blocker_group"].iloc[0])
    assert pd.isna(row["primary_blocker_group_source"].iloc[0])

    # The other rusher on the same play keeps his own label.
    other = out.loc[out["nfl_id"] == R_LE]
    assert other["status"].iloc[0] == "OK"
    assert other["primary_blocker_group"].iloc[0] == "OT"
    assert summary["row_reason_counts"] == {"ambiguous_engagement": 1}


# --------------------------------------------------------------------------- #
# 5-8. Structural failures: the whole play, one reason, five null columns
# --------------------------------------------------------------------------- #
def _assert_play_na(out: pd.DataFrame, reason: str) -> None:
    assert len(out) > 0
    assert (out["status"] == "NA").all()
    assert (out["reason"] == reason).all()
    for col in B.BLOCKER_COLUMNS:
        assert col in out.columns
        assert out[col].isna().all(), col


def test_truncated_tracking_is_insufficient_tracking():
    out, _ = _derive(world_truncated())
    assert len(out) == 12
    _assert_play_na(out, "insufficient_tracking")


def test_unclassified_band_player():
    out, _ = _derive(world_unclassified())
    _assert_play_na(out, "unclassified_players")


def test_zero_rushers_is_implausible_and_never_zero():
    out, _ = _derive(world_zero_rushers())
    _assert_play_na(out, "zero_rushers_implausible")
    assert (out["n_rushers"] == 0).sum() == 0
    assert out["n_rushers"].isna().all()


def test_ambiguous_los():
    out, _ = _derive(world_ambiguous_los())
    _assert_play_na(out, "ambiguous_los")


# --------------------------------------------------------------------------- #
# 9. Roster vs geometry: same labels, different declared source
# --------------------------------------------------------------------------- #
def test_roster_and_geometry_agree_where_geometry_is_unambiguous():
    with_roster, _ = _derive(world_standard(positions=POSITIONS))
    no_roster, _ = _derive(world_standard())

    ids = [R_LE, R_LI, R_RI, R_RE]
    roster_labels = _labels(with_roster, ids)
    geometry_labels = _labels(no_roster, ids)
    assert roster_labels == geometry_labels

    r_rows = with_roster.loc[with_roster["nfl_id"].isin(ids)]
    g_rows = no_roster.loc[no_roster["nfl_id"].isin(ids)]
    assert set(r_rows["primary_blocker_group_source"]) == {"roster"}
    assert set(g_rows["primary_blocker_group_source"]) == {"geometry"}


# --------------------------------------------------------------------------- #
# 10. Invariants on every world
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(WORLDS))
def test_invariants_hold_on_every_world(name):
    out, _ = _derive(WORLDS[name]())

    assert set(out["status"].unique()) <= {"OK", "NA"}
    na = out["status"] == "NA"
    assert out.loc[na, "reason"].notna().all()
    assert out.loc[~na, "reason"].isna().all()
    assert (out.loc[na, "reason"].astype(str).str.len() > 0).all()

    blitz = out["blitz"].dropna()
    assert set(blitz.unique()) <= {0.0, 1.0}

    # No zero-fill: a structural NA play carries none of the five columns; an
    # engagement NA is row-level and keeps the play's real counts.  A real
    # zero rush count is never emitted.
    structural = na & out["n_rushers"].isna()
    if structural.any():
        assert out.loc[structural, list(B.BLOCKER_COLUMNS)].isna().all().all()
    row_level = na & out["n_rushers"].notna()
    if row_level.any():
        assert out.loc[row_level, ["primary_blocker_group",
                                   "primary_blocker_group_source"]].isna().all().all()
        assert out.loc[row_level, ["n_rushers", "blitz",
                                   "n_blockers"]].notna().all().all()
    assert (out["n_rushers"] == 0).sum() == 0

    # A label and its source travel together.
    has_label = out["primary_blocker_group"].notna()
    assert out.loc[has_label, "primary_blocker_group_source"].notna().all()
    assert (out["primary_blocker_group_source"].notna() == has_label).all()
    assert set(out.loc[has_label, "primary_blocker_group_source"].unique()) \
        <= {"roster", "geometry"}

    again, _ = _derive(WORLDS[name]())
    pd.testing.assert_frame_equal(out, again)


# --------------------------------------------------------------------------- #
# 11. Determinism and order independence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["standard", "blitz", "ambiguous"])
def test_output_is_deterministic_and_row_order_independent(name):
    built = WORLDS[name]()
    frames, players, plays, roster = built
    first, _ = _derive(built)
    second, _ = _derive(built)
    pd.testing.assert_frame_equal(first, second)

    rng = np.random.default_rng(20270107)
    shuffled_frames = frames.iloc[rng.permutation(len(frames))].reset_index(drop=True)
    shuffled_players = players.iloc[rng.permutation(len(players))].reset_index(drop=True)
    shuffled, _ = _derive((shuffled_frames, shuffled_players, plays, roster))
    pd.testing.assert_frame_equal(first, shuffled)
