"""dl_blockers — geometric rusher / blocker derivation from all-22 tracking (D75).

**PARKED (D78, 2026-11-02) — kept, not deleted, not called on this dataset.**
The 2027 tracking product carries combine prospects only (1.5 players per play
on full data, 1-4 on the provided sample), so no play reaches
``all22_min_players`` and stage 03 short-circuits before this module runs
(``tracking_product: prospect_only`` in ``config.yaml``).  The module and its
planted-truth tests stay in the tree for a future all-22 product; the §8
controls that depended on it were removed from ``notes/prereg.md`` §8 on
2026-11-02.  Nothing here is broken and nothing here is pending: it is parked.

``n_rushers``, ``blitz``, ``n_blockers`` and ``primary_blocker_group`` are
derived from tracked positions, **never** from ``player_play.blitzing``
(evidence F9: that column is a per-player rush assignment, ``TRUE`` on 98.3 % of
interior rows).  The rule is the geometric proxy fixed by ``notes/dl-design.md``
§5.4a and it is deliberately conservative.

Geometry.  Per play, ``los_x`` is the line-of-scrimmage candidate the tracked
players actually stand on, and ``depth = sign * (x - los_x)`` with ``sign = +1``
for ``play_direction == "right"``, so **positive depth is the defensive side**.
``line_y`` is the median snap ``y`` of the players inside ``±los_tolerance_yds``
of the line — the ball's lateral position.  ``advance = depth(snap) -
depth(window end)`` is positive when a player moves toward the offensive
backfield, so a **rusher** is a defender with ``advance >= rush_advance_min_yd``;
a **protector** is an offensive non-QB that did not advance (net retreat and
lateral travel inside their caps) and engaged a rusher — within
``blocker_radius_yds`` of one in ``>= primary_blocker_min_share`` of its frames;
and a **primary blocker** is, per rusher, the plurality nearest offensive non-QB
across the frames, accepted only when that proximity is sustained.  The QB
(deepest central offensive player) is excluded from the protector count *and*
from the engagement candidates, otherwise early pressure would count him as his
own blocker.

Label scope.  ``primary_blocker_group`` / ``primary_blocker_group_source`` are
**per rusher per snap**, not per play: the output row for ``(play, nfl_id)``
carries the group of the blocker *that rusher* primarily engaged, derived from
that rusher's own plurality winner and that winner's own roster/geometry
source.  Rows that are not rushers carry no label; no rusher ever inherits
another rusher's label.  This is the crossed random effect of §8 — a per-play
label would collapse the within-play variation it is meant to measure.

Side of the ball.  A player's team does not change inside a play, so motion
must never override the pre-snap side.  The precedence is: (1) **roster** —
``players.nfl_position`` in the offensive vocabulary (``QB RB FB HB WR TE T G C
OT OG OL LS``) is offence, in the defensive vocabulary (``DE DT NT DL EDGE OLB
ILB MLB LB CB DB S FS SS``) defence; (2) **pre-snap depth sign** —
``depth_snap > 0`` is defence (the defensive side of the line) and ``< 0``
offence; (3) only at exactly ``depth_snap == 0`` the old **motion** rule
(``advance >= rush_advance_min_yd`` defence; otherwise a stay-in ``offence``;
anything else ``unknown``).  If the motion rule is the only evidence and it
calls a player a defender who advanced by ``rush_advance_min_yd``, the row would
silently become a rusher; instead the whole play is ``NA`` with reason
``side_inference_ambiguous`` so ``n_rushers`` can never be contaminated.

Row statuses.  Every emitted row carries one of three statuses.  ``OK`` is a
rusher on a derivable play whose own primary blocker resolved.  ``NA`` is a
structural play failure (all rows) or an engagement failure on one rusher's own
row.  ``non_rusher`` is every non-rushing player on a derivable play — they are
not failures, but they are not rushers either, and they are **not** counted in
the gate denominator.  A non-rusher's reason is one of
``not_defensive_player`` (an offensive player, so it cannot be a rusher),
``not_a_pass_rush_snap`` (only when an optional boolean ``pass_rush`` column is
present and false for that row/play), ``wide_stunt`` (defensive, travelled
``>= interior_lateral_max_yd`` laterally and did not gain depth), ``containing``
(defensive, travelled that far laterally while gaining depth but stayed below
``rush_advance_min_yd``) or ``held_at_line`` (every other defensive non-rusher).
``derivation_expected`` is ``True`` exactly on the rows whose primary-blocker
derivation was required: rushers on a derivable play, including a rusher whose
own derivation failed.  It is ``False`` on every non-rusher and on every row of
a structurally ``NA`` play.

NA discipline.  Structural failures (geometry, tracking, line of scrimmage,
side of the ball, window, zero rushers) make the whole play ``NA`` with **one**
reason code and every derived column null.  Engagement failures are per
rusher: that rusher's row is ``NA`` with its own reason while the play's
``n_rushers``/``blitz``/``n_blockers`` stay filled for every row, and it never
falls back to another rusher's label.  Nothing is zero-filled and no row is
partially classified.  Guards run in the order of :data:`REASONS`; zero rushers
on a dropback means the derivation failed, so a real ``0`` is never emitted.  One vectorised pass builds the snap
and window geometry and the per-play loop broadcasts with numpy over players and
frames — never a per-frame Python loop over players.  Rows are emitted sorted by
``(game_id, play_id, nfl_id)``.  Every constant comes from ``bdb27_dl.game``: no
defaults, no tuning.  Units: positions yd, times s.  The summary is written to
``snaps_summary.json``; :data:`DERIVATION_COLUMNS` is the five label columns
plus ``derivation_expected``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

import numpy as np
import pandas as pd

from dl import dl_common as D

__all__ = ["BLOCKER_COLUMNS", "DERIVATION_COLUMNS", "REASONS",
           "derive_blockers"]

#: Columns this module contributes to the snaps table.
BLOCKER_COLUMNS: tuple[str, ...] = ("n_rushers", "blitz", "n_blockers",
                                    "primary_blocker_group",
                                    "primary_blocker_group_source")

#: The five label columns plus the gate-denominator flag.  ``BLOCKER_COLUMNS``
#: stays the original five because callers (and the structural-NA assertion)
#: treat exactly those as the columns a failed play must null.
DERIVATION_COLUMNS: tuple[str, ...] = (*BLOCKER_COLUMNS, "derivation_expected")

#: Roster position vocabularies.  Data-dictionary labels, not a tunable.
ROSTER_OFFENCE: frozenset[str] = frozenset({
    "QB", "RB", "FB", "HB", "WR", "TE", "T", "G", "C", "OT", "OG",
    "OL", "LS"})
ROSTER_DEFENCE: frozenset[str] = frozenset({
    "DE", "DT", "NT", "DL", "EDGE", "OLB", "ILB", "MLB", "LB", "CB",
    "DB", "S", "FS", "SS"})

#: Non-rusher reason categories (row ``reason`` when ``status == "non_rusher"``).
NON_RUSHER_REASONS: tuple[str, ...] = (
    "not_defensive_player", "not_a_pass_rush_snap", "wide_stunt",
    "containing", "held_at_line")

#: Reason codes, each with the sentence written to ``snaps_summary.json``.
REASONS: dict[str, str] = {
    "missing_play_geometry":
        "The play carries no line-of-scrimmage geometry, so depth and side of "
        "the ball are undefined and nothing is classified.",
    "insufficient_tracking":
        "Fewer than all22_min_players distinct players were tracked at the snap "
        "frame, so a team-level count would be a partial-22 artefact.",
    "ambiguous_los":
        "The two line-of-scrimmage candidates are closer together than "
        "los_tolerance_yds, so every depth would be arbitrary.",
    "unclassified_players":
        "A tracked player stood exactly on the line and its motion neither "
        "rushed nor stayed in, so its side of the ball is undecidable.",
    "side_inference_ambiguous":
        "A player with no roster position stood exactly on the line and only "
        "the motion rule would have made him a rusher, so the play's side "
        "inference is ambiguous and n_rushers would be contaminated.",
    "incomplete_window":
        "A tracked player has no frame inside post_snap_window_s, so its advance "
        "is undefined and the play cannot be fully classified.",
    "zero_rushers_implausible":
        "No defender advanced by rush_advance_min_yd inside the window; zero "
        "rushers on a dropback means the derivation failed, never a real zero.",
    "rusher_not_tracked":
        "A rusher has no tracking frame in the window, so its primary blocker "
        "cannot be identified and the play is withheld rather than filled.",
    "no_sustained_engagement":
        "A rusher's nearest offensive player was never inside blocker_radius_yds "
        "in primary_blocker_min_share of the frames, so he went unblocked.",
    "ambiguous_engagement":
        "Two or more offensive players were a rusher's nearest player in an "
        "equal number of frames, so the plurality winner is tied.",
}

#: ``REASONS`` order is the priority order: structural failures outrank engagement.
_RANK = {code: i for i, code in enumerate(REASONS)}

#: Roster position -> blocker group.  Data-dictionary vocabulary, not a tunable.
_GROUP_BY_POSITION: dict[str, str] = {
    "T": "OT", "G": "interior", "C": "interior", "TE": "TE",
    "RB": "RB", "FB": "RB", "HB": "RB",
}
#: Reason code for each non-``ok`` primary-blocker status.
_PRIMARY_REASON: dict[str, str] = {
    "untracked": "rusher_not_tracked",
    "tied": "ambiguous_engagement",
    "unsustained": "no_sustained_engagement",
}

_KEYS = ["game_id", "play_id", "nfl_id"]
_PLAY = ["game_id", "play_id"]


def _consts(block: Mapping[str, Any]) -> dict[str, Any]:
    """Read every constant — no defaults, so a missing key raises ``KeyError``."""
    out = {kk: float(block[kk]) for kk in (
        "los_tolerance_yds", "blocker_radius_yds", "rush_advance_min_yd",
        "protector_max_retreat_yd", "protector_max_lateral_yd",
        "qb_min_depth_yd", "qb_central_lateral_yd", "primary_blocker_min_share",
        "te_lateral_min_yd", "te_lateral_max_yd", "interior_lateral_max_yd")}
    for kk in ("blitz_rusher_threshold", "all22_min_players"):
        out[kk] = int(block[kk])
    out["post_snap_window_s"] = tuple(float(v) for v in
                                      block["post_snap_window_s"])
    return out


def _snap_rows(frames: pd.DataFrame, tol: float) -> pd.DataFrame:
    """Frame nearest ``t_rel == 0`` per player, kept only if within ``tol``."""
    near = frames.loc[frames["t_rel"].abs() <= tol].copy()
    near["_d"] = near["t_rel"].abs()
    near = near.sort_values(_KEYS + ["_d", "t_rel"], kind="mergesort")
    snap = near.drop_duplicates(subset=_KEYS, keep="first")
    return snap.drop(columns=["_d"]).reset_index(drop=True)


def _window_rows(frames: pd.DataFrame, window: tuple[float, float],
                 tol: float) -> pd.DataFrame:
    """End frame per player: nearest the window end, else the latest one."""
    w0, w1 = window
    win = frames.loc[frames["t_rel"].between(w0, w1)].copy()
    win["_k"] = win["_pk"] + "|" + win["nfl_id"].astype(str)
    win["_off"] = (win["t_rel"] - w1).abs()
    best = win.sort_values(["_k", "_off", "t_rel"], kind="mergesort")
    best = best.drop_duplicates(subset="_k", keep="first").set_index("_k")
    last = win.sort_values(["_k", "t_rel"], kind="mergesort")
    last = last.drop_duplicates(subset="_k", keep="last").set_index("_k")
    late = best["_off"] > tol                      # no frame near the window end
    chosen = best.copy()
    for col in ("t_rel", "x", "y"):
        chosen.loc[late, col] = last.loc[late, col]
    return chosen.reset_index(drop=True).drop(columns=["_off"])


def _frame_arrays(sub: pd.DataFrame,
                  ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-play position grid: rows are distinct frames, columns are ``ids``."""
    tv = np.round(sub["t_rel"].to_numpy(dtype=float), 3)
    uniq = np.unique(tv)
    code = np.searchsorted(uniq, tv) * ids.size + np.searchsorted(
        ids, sub["nfl_id"].to_numpy())
    _, first = np.unique(code, return_index=True)   # first row per cell
    xs, ys = np.full(uniq.size * ids.size, np.nan), np.full(uniq.size * ids.size,
                                                            np.nan)
    xs[code[first]] = sub["x"].to_numpy(dtype=float)[first]
    ys[code[first]] = sub["y"].to_numpy(dtype=float)[first]
    return xs.reshape(uniq.size, ids.size), ys.reshape(uniq.size, ids.size)


def _non_rusher_reason(side: str, pass_rush: Any, lateral: float,
                       depth_end: float, depth_snap: float,
                       k: Mapping[str, Any]) -> str:
    """Reason category for a non-rushing player on a derivable play.

    Order matters: an offensive player cannot be a rusher whatever he did, and
    ``pass_rush`` (when the optional column is supplied) outranks the geometry.
    """
    if side == "offence":
        return "not_defensive_player"
    if pass_rush is not None and pass_rush is not pd.NA and \
            bool(pass_rush) is False:
        return "not_a_pass_rush_snap"
    if abs(lateral) >= k["interior_lateral_max_yd"]:
        return "wide_stunt" if depth_end <= depth_snap else "containing"
    return "held_at_line"


def _constants_dict(k: Mapping[str, Any], tol: float) -> dict[str, Any]:
    """JSON-safe constants block shared by the full and the empty summary."""
    return {kk: (list(vv) if isinstance(vv, tuple) else vv)
            for kk, vv in k.items()} | {"frame_tol_s": tol}


def _empty_result(players: pd.DataFrame, k: Mapping[str, Any], tol: float
                  ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """The shaped empty result for a zero-row players frame.

    ``derive_blockers`` is called with whatever the coverage guard produced; a
    frame that carries no row must return the declared columns (so the caller's
    merge cannot fail on a missing ``_pk``) and a summary with the same keys as
    a scored run, not a partial one.
    """
    lead = [*_KEYS, *[c for c in players.columns if c not in _KEYS]]
    cols = list(dict.fromkeys(
        [*lead, *DERIVATION_COLUMNS, "status", "reason"]))
    out = pd.DataFrame({c: pd.Series(dtype=object) for c in cols})
    for c in ("game_id", "play_id", "nfl_id"):
        out[c] = pd.Series(dtype="int64")
    # The three counts are numeric; the two label columns are text and must
    # keep the object dtype the scored path emits, so a caller that inspects
    # dtypes before concatenating an empty shard sees the same schema.
    for c in ("n_rushers", "blitz", "n_blockers"):
        out[c] = pd.Series(dtype="float64")
    for c in ("primary_blocker_group", "primary_blocker_group_source"):
        out[c] = pd.Series(dtype=object)
    out["derivation_expected"] = pd.Series(dtype=bool)
    out["status"] = pd.Series(dtype=object)
    out["reason"] = pd.Series(dtype=object)
    summary: dict[str, Any] = {
        "n_plays_scored": 0,
        "n_plays_derivable": 0,
        "n_plays_na": 0,
        "reason_counts": {},
        "row_reason_counts": {},
        "n_players_at_snap": {"min": 0, "median": 0.0, "max": 0},
        "n_rushers_distribution": {"mean": None, "min": None, "max": None},
        "label_source_counts": {"roster": 0, "geometry": 0},
        "qb_rule_misses": 0,
        "constants": _constants_dict(k, tol),
        "rule": _RULE_TEXT,
        "n_rows": 0,
        "n_expected_rows": 0,
        "status_counts": {},
        "non_rusher_reason_counts": {},
        "side_method_counts": {"roster": 0, "depth": 0, "motion": 0},
        "n_side_inference_ambiguous": 0,
        "coverage_reason": ("no rows: the players frame is empty, so there is "
                            "nothing to score"),
        "empty_frame": True,
    }
    return out, summary


_RULE_TEXT = ("§5.4a geometric proxy: rusher = defender with advance >= "
              "rush_advance_min_yd; protector = stay-in offensive non-QB "
              "engaged with a rusher; primary blocker = plurality nearest "
              "offensive non-QB per rusher, and the group label is written "
              "on that rusher's own row.  Never zero-filled, never from "
              "player_play.blitzing.")


def _geometry_group(depth: float, y_rel: float, k: Mapping[str, Any]) -> str:
    """Alignment-derived group label, used when the roster lacks a position."""
    if -depth >= k["qb_min_depth_yd"]:
        return "RB"
    if k["te_lateral_min_yd"] <= abs(y_rel) <= k["te_lateral_max_yd"]:
        return "TE"
    return "OT" if abs(y_rel) >= k["interior_lateral_max_yd"] else "interior"


def _label(nfl_id: int, depth: float, y_rel: float, pos: Mapping[Any, str],
           k: Mapping[str, Any]) -> tuple[str, str]:
    """(group, source) for one blocker: roster position first, else geometry."""
    lab = _GROUP_BY_POSITION.get(str(pos.get(nfl_id, "")).strip().upper())
    if lab is not None:
        return lab, "roster"
    return _geometry_group(depth, y_rel, k), "geometry"


def _engagement(sub: pd.DataFrame, ids: np.ndarray, rushers: np.ndarray,
                cands: np.ndarray, k: Mapping[str, Any]
                ) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Engagement shares per candidate, and the primary blocker per rusher.

    ``shares`` is the fraction of a candidate's own window frames spent within
    ``blocker_radius_yds`` of a rusher.  Each primary is a dict with ``nfl_id``,
    ``status`` (``ok``/``untracked``/``tied``/``unsustained``) and ``blocker``.
    """
    xs, ys = _frame_arrays(sub, ids)
    radius, min_share = k["blocker_radius_yds"], k["primary_blocker_min_share"]
    ic, ir = np.searchsorted(ids, cands), np.searchsorted(ids, rushers)
    cx, cy, rx, ry = xs[:, ic], ys[:, ic], xs[:, ir], ys[:, ir]
    dist = np.hypot(cx[:, :, None] - rx[:, None, :],
                    cy[:, :, None] - ry[:, None, :])          # (F, C, R)
    near_any = np.nanmin(dist, axis=2) <= radius
    denom = (~np.isnan(cx)).sum(axis=0)
    shares = np.divide(near_any.sum(axis=0), denom, out=np.zeros(cands.size),
                       where=denom > 0)

    primaries: list[dict[str, Any]] = []
    for j, rid in enumerate(rushers):
        d, frames_r = dist[:, :, j], ~np.isnan(rx[:, j])
        if not frames_r.any():
            primaries.append({"nfl_id": int(rid), "status": "untracked",
                              "blocker": None})
            continue
        valid = frames_r[:, None] & ~np.isnan(d)
        best = np.argmin(np.where(valid, d, np.inf), axis=1)
        counts = np.bincount(best[valid.any(axis=1)], minlength=cands.size)
        win = np.flatnonzero(counts == counts.max())   # plurality winner(s)
        if win.size > 1:
            primaries.append({"nfl_id": int(rid), "status": "tied",
                              "blocker": None})
            continue
        ok = near_any[frames_r, int(win[0])].mean() >= min_share
        primaries.append({"nfl_id": int(rid),
                          "status": "ok" if ok else "unsustained",
                          "blocker": int(cands[int(win[0])]) if ok else None})
    return shares, primaries


def derive_blockers(cfg: Mapping[str, Any], *, frames: pd.DataFrame,
                    players: pd.DataFrame, plays: pd.DataFrame,
                    frame_tol_s: float, roster: pd.DataFrame | None = None
                    ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Derive rusher / blocker counts and the primary blocker group per rusher.

    ``frames`` is all-22 tracking (``game_id, play_id, nfl_id, t_rel, x, y``),
    ``players`` the snaps to score, ``plays`` the line-of-scrimmage geometry and
    ``roster`` an optional ``(nfl_id, nfl_position)`` table.  Returns the
    ``players`` rows plus :data:`BLOCKER_COLUMNS` and ``status``/``reason``,
    with a summary of counts, reasons and the constants used.

    The three count columns are play-level and identical on every row of a
    play.  The two group columns are per row: they are filled only on the row
    of the rusher whose own primary blocker produced the label.
    """
    k = _consts(D.dl(cfg) if "bdb27_dl" in cfg else cfg)
    tol = float(frame_tol_s)
    if len(players) == 0:
        return _empty_result(players, k, tol)
    out = players.copy()
    out["_pk"] = out["game_id"].astype(str) + "|" + out["play_id"].astype(str)
    scored = out[_PLAY + ["_pk"]].drop_duplicates("_pk")
    pos: dict[Any, str] = {}
    if roster is not None and len(roster):
        pos = dict(zip(roster["nfl_id"], roster["nfl_position"].astype(str)))

    f = frames.copy()
    f["_pk"] = f["game_id"].astype(str) + "|" + f["play_id"].astype(str)
    f = f.loc[f["_pk"].isin(set(scored["_pk"]))]
    geo = plays[_PLAY + ["los_c1", "los_c2", "play_direction"]].drop_duplicates(_PLAY)
    geo["_pk"] = geo["game_id"].astype(str) + "|" + geo["play_id"].astype(str)

    # --- 1-3. snap frame, line of scrimmage, depth, ball lateral ---------- #
    snap = _snap_rows(f, tol).merge(
        geo[["_pk", "los_c1", "los_c2", "play_direction"]], on="_pk", how="left")
    snap["sign"] = snap["play_direction"].map({"right": 1.0, "left": -1.0})
    los = pd.DataFrame({"_pk": snap["_pk"], "d1": (snap["x"] - snap["los_c1"]).abs(),
                        "d2": (snap["x"] - snap["los_c2"]).abs()})
    los = los.groupby("_pk", sort=True).median().reset_index().merge(
        snap.groupby("_pk", sort=True)[["los_c1", "los_c2"]].first().reset_index(),
        on="_pk", how="left")
    los["los_x"] = np.where(los["d1"] <= los["d2"], los["los_c1"], los["los_c2"])
    los["ambiguous_los"] = (((los["d1"] - los["d2"]).abs()
                             < k["los_tolerance_yds"])
                            | los[["d1", "d2"]].isna().any(axis=1))
    snap = snap.merge(los[["_pk", "los_x", "ambiguous_los"]], on="_pk", how="left")
    snap["depth_snap"] = snap["sign"] * (snap["x"] - snap["los_x"])
    band = snap["depth_snap"].abs() <= k["los_tolerance_yds"]
    ly = pd.DataFrame({
        "line_y": snap.loc[band].groupby("_pk", sort=True)["y"].median(),
        "line_y_all": snap.groupby("_pk", sort=True)["y"].median()}).reset_index()
    snap = snap.merge(ly, on="_pk", how="left")
    snap["line_y"] = snap["line_y"].fillna(snap["line_y_all"])
    snap["y_rel"] = snap["y"] - snap["line_y"]

    # --- 4. window end frame, advance, lateral ---------------------------- #
    end = _window_rows(f, k["post_snap_window_s"], tol)
    snap = snap.merge(end[["_pk", "nfl_id", "x", "y"]].rename(
        columns={"x": "x_end", "y": "y_end"}), on=["_pk", "nfl_id"], how="left")
    snap["depth_end"] = snap["sign"] * (snap["x_end"] - snap["los_x"])
    snap["advance"] = snap["depth_snap"] - snap["depth_end"]
    snap["lateral"] = snap["y_end"] - snap["y"]

    # --- 5. side of the ball: roster, then pre-snap depth sign, then motion - #
    # A team cannot change mid-play, so motion is only consulted at exactly
    # depth 0 (where the pre-snap sign carries no information).
    rush_min = k["rush_advance_min_yd"]
    stays = (snap["advance"] <= 0) | (
        snap["lateral"].abs() >= k["protector_max_lateral_yd"])
    motion = np.where(snap["advance"] >= rush_min, "defence",
                      np.where(stays, "offence", "unknown"))
    pos_series = snap["nfl_id"].map(
        lambda i: str(pos.get(i, "")).strip().upper())
    rost_off = pos_series.isin(ROSTER_OFFENCE).to_numpy()
    rost_def = pos_series.isin(ROSTER_DEFENCE).to_numpy()
    depth = snap["depth_snap"].to_numpy(dtype=float)
    # A NaN depth carries no sign information, so the side falls through to the
    # motion rule and the method must say so; testing ``depth != 0`` first would
    # label NaN as "depth" (NaN != 0 is True in numpy) and misattribute the
    # classification.
    method = np.select(
        [rost_off | rost_def, np.isnan(depth), depth != 0],
        ["roster", "motion", "depth"], default="motion")
    side = np.select(
        [rost_off, rost_def, depth > 0, depth < 0],
        ["offence", "defence", "defence", "offence"], default=motion)
    snap["side"] = pd.Series(side, index=snap.index, dtype=object).where(
        snap["advance"].notna())
    snap["side_method"] = pd.Series(
        method, index=snap.index, dtype=object).where(snap["advance"].notna())
    # The residual: no roster side and exactly on the line, so only motion
    # would classify the player and it would silently make him a rusher.
    snap["side_conflict"] = (
        snap["side_method"].eq("motion") & snap["side"].eq("defence")
        & (snap["advance"] >= rush_min))
    # Optional per-row pass-rush flag: when the caller supplies it, a false row
    # is a non-rusher by construction; when it is absent every scored play is
    # treated as a pass-rush snap (the module contract).
    if "pass_rush" in out.columns:
        flag = out[_KEYS + ["pass_rush"]].drop_duplicates(_KEYS)
        snap = snap.merge(flag, on=_KEYS, how="left")
        snap["_pass_rush"] = snap["pass_rush"].astype("boolean")

    snap = snap.sort_values(["_pk", "nfl_id"], kind="mergesort").reset_index(drop=True)
    n_snap = snap.groupby("_pk", sort=True).size().rename("n_snap")
    flags = snap.assign(u=snap["side"].eq("unknown"), m=snap["advance"].isna())
    n_unknown = flags.groupby("_pk", sort=True)[["u", "m"]].sum()

    # --- 6-11. per-play classification ----------------------------------- #
    plays_out: dict[str, dict[str, Any]] = {}
    missing_geo = (set(scored["_pk"]) - set(geo["_pk"])) | set(
        geo.loc[geo[["los_c1", "los_c2"]].isna().any(axis=1), "_pk"])
    n_rushers_all: list[int] = []
    qb_misses = 0
    src_counts = {"roster": 0, "geometry": 0}
    groups = {pk: g for pk, g in snap.groupby("_pk", sort=True)}
    frames_by = {pk: g for pk, g in f.groupby("_pk", sort=True)}

    def na(reason: str) -> dict[str, Any]:
        return {"status": "NA", "reason": reason}

    for pk in sorted(scored["_pk"]):
        if pk in missing_geo:
            plays_out[pk] = na("missing_play_geometry")
            continue
        if int(n_snap.get(pk, 0)) < k["all22_min_players"]:
            plays_out[pk] = na("insufficient_tracking")
            continue
        g = groups[pk]
        if bool(g["ambiguous_los"].iloc[0]):
            plays_out[pk] = na("ambiguous_los")
            continue
        n_u, n_m = [int(n_unknown[c].get(pk, 0)) for c in ("u", "m")]
        if n_u or n_m:
            plays_out[pk] = na("unclassified_players" if n_u
                               else "incomplete_window")
            continue
        if bool(g["side_conflict"].any()):
            plays_out[pk] = na("side_inference_ambiguous")
            continue

        is_def = g["side"].eq("defence").to_numpy()
        is_off = g["side"].eq("offence").to_numpy()
        adv = g["advance"].to_numpy(dtype=float)
        dep = g["depth_snap"].to_numpy(dtype=float)
        yrel = g["y_rel"].to_numpy(dtype=float)
        ids_all = g["nfl_id"].to_numpy()
        rush_mask = is_def & (adv >= rush_min)
        if not rush_mask.any():
            plays_out[pk] = na("zero_rushers_implausible")
            continue
        rushers = ids_all[rush_mask]

        # QB: deepest central offensive player, excluded from everything below.
        qb_ok = is_off & (dep <= -k["qb_min_depth_yd"]) \
            & (np.abs(yrel) <= k["qb_central_lateral_yd"])
        idx = np.flatnonzero(qb_ok)
        qb = int(ids_all[idx[np.argmin(dep[idx])]]) if idx.size else -1
        qb_misses += int(idx.size == 0)

        cand_mask = is_off & (ids_all != qb)
        cands, c_adv = ids_all[cand_mask], adv[cand_mask]
        sub = frames_by.get(pk)
        if cands.size and sub is not None:
            shares, primaries = _engagement(sub, ids_all, rushers, cands, k)
        else:
            shares = np.zeros(cands.size, dtype=float)
            primaries = [{"nfl_id": int(r), "status": "unsustained",
                          "blocker": None} for r in rushers]

        # --- 9. protectors (stay-in offensive players who engaged) ------- #
        lat = g["lateral"].to_numpy(dtype=float)[cand_mask]
        keep = ((c_adv < rush_min)
                & (-c_adv <= k["protector_max_retreat_yd"])
                & (np.abs(lat) <= k["protector_max_lateral_yd"])
                & (shares >= k["primary_blocker_min_share"]))
        n_blockers = int(keep.sum())

        # --- 10-11. per-rusher label from that rusher's own primary ------ #
        dlook = {int(i): (float(d), float(y))
                 for i, d, y in zip(ids_all, dep, yrel)}
        row_labels: dict[int, dict[str, Any]] = {}
        for p in primaries:
            rid = int(p["nfl_id"])
            if p["status"] != "ok":
                row_labels[rid] = {
                    "status": "NA", "reason": _PRIMARY_REASON[p["status"]],
                    "primary_blocker_group": None,
                    "primary_blocker_group_source": None}
                continue
            lab, src = _label(int(p["blocker"]), *dlook[int(p["blocker"])],
                              pos, k)
            src_counts[src] += 1
            row_labels[rid] = {
                "status": "OK", "reason": None,
                "primary_blocker_group": lab,
                "primary_blocker_group_source": src}
        n_rushers_all.append(int(rushers.size))

        # Every player on a derivable play gets a row status.  Rushers keep
        # their own label or their own engagement failure; everyone else is a
        # non-rusher with an explicit reason and no label.  The play-level
        # counts stay filled on every row.
        lat_all = g["lateral"].to_numpy(dtype=float)
        dep_end_all = g["depth_end"].to_numpy(dtype=float)
        side_all = g["side"].to_numpy()
        pr_all = (g["_pass_rush"].to_numpy() if "_pass_rush" in g
                  else np.array([None] * g.shape[0], dtype=object))
        row_recs: list[dict[str, Any]] = []
        for i, nid in enumerate(ids_all):
            if rush_mask[i]:
                lab = row_labels[int(nid)]
                row_recs.append({
                    "nfl_id": int(nid), "status": lab["status"],
                    "reason": lab["reason"],
                    "primary_blocker_group": lab["primary_blocker_group"],
                    "primary_blocker_group_source":
                        lab["primary_blocker_group_source"],
                    "derivation_expected": True})
                continue
            row_recs.append({
                "nfl_id": int(nid), "status": "non_rusher",
                "reason": _non_rusher_reason(
                    side_all[i], pr_all[i], float(lat_all[i]),
                    float(dep_end_all[i]), float(dep[i]), k),
                "primary_blocker_group": None,
                "primary_blocker_group_source": None,
                "derivation_expected": False})

        plays_out[pk] = {
            "status": "OK", "reason": None, "n_rushers": int(rushers.size),
            "blitz": float(rushers.size > k["blitz_rusher_threshold"]),
            "n_blockers": n_blockers, "rows": row_recs,
        }

    # --- 12. emit ---------------------------------------------------------- #
    res = pd.DataFrame([{"_pk": pk, **{kk: vv for kk, vv in v.items()
                                       if kk not in ("rows", "labels")}}
                        for pk, v in plays_out.items()])
    out = out.merge(res, on="_pk", how="left")

    # Row-level statuses keyed by the row's own (play, nfl_id).  Every row of a
    # derivable play owns its status; rows of a structurally NA play keep the
    # play-level NA and reason.  A rusher whose primary blocker failed keeps
    # the play's counts and is the only row that carries his failure reason.
    row_recs = [{"_pk": pk, "nfl_id": rec["nfl_id"],
                 "_rstatus": rec["status"], "_rreason": rec["reason"],
                 "_rgroup": rec["primary_blocker_group"],
                 "_rsource": rec["primary_blocker_group_source"],
                 "_rexp": rec["derivation_expected"]}
                for pk, play in plays_out.items()
                for rec in play.get("rows", [])]
    row_cols = ["_pk", "nfl_id", "_rstatus", "_rreason", "_rgroup",
                "_rsource", "_rexp"]
    rows = pd.DataFrame(row_recs, columns=row_cols)
    out = out.merge(rows, on=["_pk", "nfl_id"], how="left")

    has_row = out["_rstatus"].notna()
    out["status"] = out["status"].where(~has_row, out["_rstatus"])
    out["reason"] = out["reason"].where(~has_row, out["_rreason"])
    out["primary_blocker_group"] = out.pop("_rgroup")
    out["primary_blocker_group_source"] = out.pop("_rsource")
    out["derivation_expected"] = (out.pop("_rexp").fillna(False)
                                  .astype(bool))
    out = out.drop(columns=["_rstatus", "_rreason"])

    missing = [c for c in BLOCKER_COLUMNS if c not in out]
    out = out.reindex(columns=[*out.columns, *missing])
    out["status"] = out["status"].fillna("NA")
    out = out.sort_values(_KEYS, kind="mergesort").reset_index(drop=True)

    ordered = n_snap.reindex(sorted(scored["_pk"])).fillna(0).astype(int)
    counts = Counter(v["reason"] for v in plays_out.values()
                     if v["status"] == "NA")
    row_counts = Counter(rec["reason"] for play in plays_out.values()
                         for rec in play.get("rows", [])
                         if rec["status"] == "NA")
    status_counts = Counter(out["status"].astype(str))
    non_rusher_counts = Counter(
        out.loc[out["status"].eq("non_rusher"), "reason"])
    side_methods = {m: int(snap["side_method"].eq(m).sum())
                    for m in ("roster", "depth", "motion")}
    n_na = sum(counts.values())
    summary: dict[str, Any] = {
        "n_plays_scored": int(len(scored)),
        "n_plays_derivable": int(len(scored) - n_na),
        "n_plays_na": int(n_na),
        "reason_counts": dict(sorted(counts.items())),
        "row_reason_counts": dict(sorted(row_counts.items())),
        "n_players_at_snap": {"min": int(ordered.min()) if len(ordered) else 0,
                              "median": float(ordered.median()) if len(ordered)
                              else 0.0,
                              "max": int(ordered.max()) if len(ordered) else 0},
        "n_rushers_distribution": {
            "mean": float(np.mean(n_rushers_all)) if n_rushers_all else None,
            "min": int(np.min(n_rushers_all)) if n_rushers_all else None,
            "max": int(np.max(n_rushers_all)) if n_rushers_all else None},
        "label_source_counts": src_counts,
        "qb_rule_misses": int(qb_misses),
        "constants": _constants_dict(k, tol),
        "rule": _RULE_TEXT,
        "n_rows": int(len(out)),
        "n_expected_rows": int(out["derivation_expected"].sum()),
        "status_counts": dict(sorted(status_counts.items())),
        "non_rusher_reason_counts": dict(sorted(non_rusher_counts.items())),
        "side_method_counts": side_methods,
        "n_side_inference_ambiguous": int(
            out["reason"].eq("side_inference_ambiguous").sum()),
    }
    cols = list(dict.fromkeys(
        [*_KEYS, *[c for c in players.columns if c not in _KEYS],
         *DERIVATION_COLUMNS, "status", "reason"]))
    return out[[c for c in cols if c in out.columns]], summary
