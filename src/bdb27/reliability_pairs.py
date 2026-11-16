"""Reliability pair figures: odd-vs-even halves and 2024-vs-2025 season means.

Conor, 2026-11-16 19:09 GMT: "Give me odds v evens and 2024 v 2025" — two of
the six throwaway prototypes are built properly here. Nothing else from that
prototype set is drawn.

Both figures use one slice, exactly:

    data/interim/full_run_20261111/bdb27_full_db_turns.parquet
    setting == 'game' & position_group == 'DB'
    & 30 <= abs_turn_angle < 60 & valid == True

Every run prints the slice counts and refuses (``common.StageRefused``) if any
count differs from the uploaded full run: 7,777 turns, 113 players, 7,198
plays, 809 games, 2,197 turns at exactly ``apex_retention == 1.0``. That is a
data-integrity check, not a threshold: no draw path branches on a thin subset.

The estimators are **imported, never reimplemented**:
``fallback_validation.split_half_halves``,
``fallback_validation.split_half_reliability``,
``fallback_validation.season_correlation``, ``season_means``,
``spearman_brown`` and ``_pearson``. The split-half correlation and its
bootstrap interval come from the committed
``outputs/fallback_validation/reliability.json``; this module only reads them
and prints the interval as-is (it is the bootstrap interval on **r**, never on
Spearman-Brown).

House rules honoured here:

* DB only — no other position group, not even as grey context;
* no player names and no ``nfl_id`` on any canvas, in any CSV, or in any
  drawn-text file;
* no arbitrary gate: every threshold is a printed number with its denominator,
  never control flow; a thin panel prints its ``n`` and ``r = not estimable``
  and still renders;
* the 2,197 ceiling turns are never removed or filtered from a distribution;
* the banned-token scan runs at render time and refuses;
* two runs on the same inputs produce byte-identical CSV and
  ``.drawn_text.json`` files (the drawn-text dump carries no timestamp; the PNG
  metadata is pinned to ``Software: bdb27-figures``).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from bdb27 import common, figures
from bdb27 import fallback_validation as fv
from bdb27.figure_text_scan import BANNED_TOKENS, figure_texts

STAGE = "06_figures"
ARTEFACT_VERSION = figures.ARTEFACT_VERSION

FIGURE_ODD = "fig_odd_vs_even"
FIGURE_SEASON = "fig_season_vs_season"

TITLE_ODD = ("Odd turns against even turns: the same player's two halves do "
             "not agree")
TITLE_SEASON = ("2024 against 2025: the apparent relationship lives in the "
                "thin per-season samples")

ROOT = Path(__file__).resolve().parents[2]
TURNS_PARQUET = Path("data/interim/full_run_20261111/bdb27_full_db_turns.parquet")
RELIABILITY_JSON = Path("outputs/fallback_validation/reliability.json")
BAND_RELIABILITY_JSON = Path("outputs/band_reliability/reliability.json")
SHARE_DIR = Path("outputs/reliability_figures")

ANGLE_LO, ANGLE_HI = 30.0, 60.0
SEASON_A, SEASON_B = 2024, 2025

#: The uploaded full run's own counts for the slice. Printed every run; a
#: mismatch prints what was found and then refuses.
EXPECTED = {
    "turns": 7777,
    "players": 113,
    "plays": 7198,
    "games": 809,
    "ceiling_at_1.000": 2197,
}

#: The committed coefficients this module reconciles against.
COMMITTED_SPLIT = {"r": -0.0501, "spearman_brown": -0.1055,
                   "n_players": 112, "n_dropped": 1}
COMMITTED_SEASON = {
    1: {"r": 0.1959, "n_players": 66},
    10: {"r": 0.0067, "n_players": 39},
}
COMMITTED_CI = {"lo": -0.2598, "hi": 0.1486}

GREY = "#8C8C8C"
HOLLOW = "#B3B3B3"
PANEL = "#EFEFEF"
BORDER = "#B3B3B3"

#: The figure banners already ban provenance hedging; this cycle also bans
#: reading words the case retired. Scanned case-insensitively at render time.
EXTRA_BANNED = ("VOID", "STABLE", "PREDICTS", "SEPARATION", "PRESSURE",
                "PRODUCTION")
BANNED = tuple(BANNED_TOKENS) + EXTRA_BANNED

#: Dot area in points^2 for a player's turn count: legend at 10 / 50 / 100.
DOT_BASE, DOT_SLOPE = 8.0, 1.15


# --------------------------------------------------------------------- helpers
def repo_path(path: Path | str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(repo_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _minus(text: str) -> str:
    """Typeset a minus sign as the Unicode minus the figures use elsewhere."""
    return text.replace("-", "\u2212")


def _fmt(value: float, digits: int = 3) -> str:
    return _minus(f"{float(value):.{digits}f}")


def _signed(value: float, digits: int = 3) -> str:
    return _minus(f"{float(value):+.{digits}f}")


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def visible_strings(fig) -> list[str]:
    """Every visible string on the canvas, in draw order (deterministic)."""
    from matplotlib.text import Text

    return [text.get_text() for text in fig.findobj(Text)
            if text.get_text().strip() and text.get_visible()]


def canvas_texts(fig) -> list[str]:
    """``figure_texts`` plus figure-level legend labels.

    ``figure_text_scan.figure_texts`` walks axes legends but not a legend
    attached to the ``Figure`` itself; both figures here use one, so the scan
    must see it.
    """
    texts = figure_texts(fig)
    for legend in fig.legends:
        texts.extend(text.get_text() for text in legend.get_texts())
        if legend.get_title() is not None and legend.get_title().get_text():
            texts.append(legend.get_title().get_text())
    return texts


def refuse_banned(texts: Sequence[str], figure: str) -> None:
    """Refuse at render time if any banned token is drawn."""
    hits = [(token, text) for text in texts for token in BANNED
            if token in text.upper()]
    if hits:
        raise common.StageRefused(
            f"{STAGE}: {figure} carries banned drawn text: {hits}")


def write_drawn_text(path: Path, payload: dict) -> Path:
    """Write a ``.drawn_text.json`` dump with no timestamp.

    ``common.write_json`` prepends ``generated_utc``; the drawn-text dump must
    be byte-identical between two runs on the same inputs, so it is written
    here without the volatile field. Provenance keys stay identical to the
    repo's other JSON artefacts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                               allow_nan=False),
                    encoding="utf-8")
    return path


# ------------------------------------------------------------------- the slice
def load_turns(path: Path | str = TURNS_PARQUET) -> pd.DataFrame:
    """Read the uploaded full-run turn table, naming anything absent."""
    full = repo_path(path)
    if not full.exists():
        raise common.StageRefused(
            f"{STAGE}: required input is absent — refusing to run.\n"
            f"    tried: {full}\n"
            "    (the uploaded full run lives at data/interim/"
            "full_run_20261111/)")
    try:
        turns = pd.read_parquet(full)
    except Exception as exc:  # unreadable file is a legitimate failure
        raise common.StageRefused(
            f"{STAGE}: cannot read {full}: {type(exc).__name__}: {exc}") from exc
    need = {"setting", "position_group", "valid", "abs_turn_angle",
            "apex_retention", "nfl_id", "season", "game_id", "play_id",
            "frame_start"}
    missing = sorted(need - set(turns.columns))
    if missing:
        raise common.StageRefused(
            f"{STAGE}: {full.name} lacks required column(s): {missing}")
    return turns


def select_slice(turns: pd.DataFrame, *, lo: float = ANGLE_LO,
                 hi: float = ANGLE_HI) -> pd.DataFrame:
    """Valid in-game DB turns in ``[lo, hi)`` — the one analysis slice."""
    mask = ((turns["setting"] == "game")
            & (turns["position_group"] == "DB")
            & (turns["valid"])
            & (turns["abs_turn_angle"] >= lo)
            & (turns["abs_turn_angle"] < hi))
    return turns[mask].copy()


def slice_counts(slice_df: pd.DataFrame) -> dict:
    """The five counts printed on every run, each with its denominator."""
    return {
        "turns": int(len(slice_df)),
        "players": int(slice_df["nfl_id"].nunique()),
        "plays": int(slice_df[["game_id", "play_id"]]
                     .drop_duplicates().shape[0]),
        "games": int(slice_df["game_id"].nunique()),
        "ceiling_at_1.000": int((slice_df["apex_retention"] == 1.0).sum()),
        "above_1.000": int((slice_df["apex_retention"] > 1.0).sum()),
    }


def verify_slice(slice_df: pd.DataFrame, *,
                 expected: dict | None = None) -> dict:
    """Print the slice counts and refuse on any data-integrity discrepancy."""
    expected = EXPECTED if expected is None else expected
    found = slice_counts(slice_df)
    print(f"{STAGE}: slice counts — " + ", ".join(
        f"{key}={found[key]:,}" for key in expected))
    bad = {key: (found.get(key), want) for key, want in expected.items()
           if found.get(key) != want}
    if bad:
        raise common.StageRefused(
            f"{STAGE}: the analysis slice does not match the uploaded full "
            f"run — refusing to draw.\n    found vs expected: {bad}")
    return found


# ----------------------------------------------------------- odd-vs-even half
def split_half_points(slice_df: pd.DataFrame) -> dict:
    """One point per player in both halves, from the imported split.

    The assignment is ``fallback_validation.split_half_halves`` unchanged:
    order ``(nfl_id, season, game_id, play_id, frame_start)``, mergesort,
    index 0, 2, 4 ... -> half A (odd-numbered turns chronologically),
    1, 3, 5 ... -> half B. No re-sort, no jitter.
    """
    halves = fv.split_half_halves(slice_df)
    players = halves["players"].rename(columns={
        "count_A": "count_odd", "count_B": "count_even",
        "mean_A": "mean_odd", "mean_B": "mean_even",
    })
    players = players.copy()
    players["n_turns"] = (players["count_odd"]
                          + players["count_even"]).astype(int)
    both = players[players["in_both"]]
    r = fv._pearson(both["mean_odd"].to_numpy(float),
                    both["mean_even"].to_numpy(float))
    counts = slice_counts(slice_df)
    return {
        "players": players,
        "r": float(r),
        "spearman_brown": fv.spearman_brown(r),
        "n_players": int(len(both)),
        "n_dropped": int((~players["in_both"]).sum()),
        "n_players_total": int(len(players)),
        "n_turns": counts["turns"],
        "n_ceiling": counts["ceiling_at_1.000"],
        "ceiling_share": (counts["ceiling_at_1.000"] / counts["turns"]
                          if counts["turns"] else math.nan),
        "n_above_one": counts["above_1.000"],
        "counts": counts,
    }


def reconcile_odd(stats: dict) -> None:
    """Equality proofs against the committed coefficients (not gates)."""
    r = round(float(stats["r"]), 4)
    sb = round(float(stats["spearman_brown"]), 4)
    assert r == COMMITTED_SPLIT["r"], (
        f"{FIGURE_ODD}: recomputed r = {stats['r']!r} does not reconcile with "
        f"the committed {COMMITTED_SPLIT['r']!r}")
    assert sb == COMMITTED_SPLIT["spearman_brown"], (
        f"{FIGURE_ODD}: Spearman-Brown {stats['spearman_brown']!r} does not "
        f"reconcile with the committed {COMMITTED_SPLIT['spearman_brown']!r}")
    assert stats["n_players"] == COMMITTED_SPLIT["n_players"], (
        f"{FIGURE_ODD}: plotted players {stats['n_players']} != committed "
        f"{COMMITTED_SPLIT['n_players']}")
    assert stats["n_dropped"] == COMMITTED_SPLIT["n_dropped"], (
        f"{FIGURE_ODD}: one-sided players {stats['n_dropped']} != committed "
        f"{COMMITTED_SPLIT['n_dropped']}")


def load_split_half_ci(path: Path | str = RELIABILITY_JSON) -> dict:
    """The committed bootstrap interval on r, read as-is."""
    full = repo_path(path)
    if not full.exists():
        raise common.StageRefused(
            f"{STAGE}: {full} is absent — the split-half bootstrap interval "
            "lives there. Regenerate it with scripts/fallback_validation.py.")
    try:
        document = json.loads(full.read_text(encoding="utf-8"))
    except Exception as exc:
        raise common.StageRefused(
            f"{STAGE}: cannot read {full}: {type(exc).__name__}: {exc}") from exc
    ci = document.get("split_half", {}).get("ci")
    if not ci or ci.get("lo") is None or ci.get("hi") is None:
        raise common.StageRefused(
            f"{STAGE}: {full} carries no split_half.ci — rerun "
            "scripts/fallback_validation.py.")
    return {
        "lo": float(ci["lo"]),
        "hi": float(ci["hi"]),
        "reps": int(document["split_half"].get("reps", fv.BOOTSTRAP_REPS)),
        "seed": int(document["split_half"].get("seed", fv.SEED)),
        "kind": ("percentile bootstrap interval on r; players resampled with "
                 "replacement and r recomputed (never the interval on "
                 "Spearman-Brown)"),
        "source": str(Path(path).as_posix()),
        "sha256": sha256(full),
    }


def odd_annotation_lines(stats: dict, ci: dict) -> list[str]:
    """The annotation block, every line carrying its denominator."""
    dropped, total = stats["n_dropped"], stats["n_players_total"]
    ceiling, turns = stats["n_ceiling"], stats["n_turns"]
    share = 100.0 * stats["ceiling_share"]
    if dropped == 1:
        dropped_line = (f"{dropped} of {total:,} players has no "
                        "even-numbered turn and is not plotted")
    else:
        dropped_line = (f"{dropped} of {total:,} players have no "
                        "even-numbered turn and are not plotted")
    if np.isfinite(stats["r"]) and np.isfinite(stats["spearman_brown"]):
        stat_line = (f"r = {_fmt(stats['r'], 3)} · Spearman\u2013Brown "
                     f"{_fmt(stats['spearman_brown'], 3)}")
    else:
        stat_line = "r = not estimable · Spearman\u2013Brown not estimable"
    return [
        f"n = {stats['n_players']:,} "
        f"{_plural(stats['n_players'], 'player', 'players')} · "
        f"{turns:,} turns",
        stat_line,
        f"95% bootstrap CI on r [{_signed(ci['lo'], 3)}, "
        f"{_signed(ci['hi'], 3)}]",
        dropped_line,
        f"{ceiling:,} of {turns:,} turns ({share:.1f}%) sit at exactly 1.000 "
        "\u2014 a measurement ceiling,",
        "not player structure; it pulls every player mean toward 1.0",
    ]


def odd_csv_frame(players: pd.DataFrame) -> pd.DataFrame:
    """One row per player slot; no identity columns, ever."""
    ordered = players.sort_values(["mean_odd", "mean_even"],
                                  ascending=True, na_position="last",
                                  kind="mergesort").reset_index(drop=True)
    table = pd.DataFrame({
        "player_slot": np.arange(1, len(ordered) + 1),
        "n_turns": ordered["n_turns"].astype(int),
        "count_odd": ordered["count_odd"].astype(int),
        "count_even": ordered["count_even"].astype(int),
        "mean_odd": ordered["mean_odd"].astype(float),
        "mean_even": ordered["mean_even"].astype(float),
        "in_both": ordered["in_both"].astype(bool),
    })
    return table


def draw_odd(stats: dict, ci: dict, *, colour: str, out_png: Path
             ) -> tuple[list[str], list[str]]:
    """Draw the odd-vs-even scatter and refuse if a banned token is inked."""
    players = stats["players"]
    both = players[players["in_both"]]
    x = both["mean_odd"].to_numpy(float)
    y = both["mean_even"].to_numpy(float)
    sizes = DOT_BASE + DOT_SLOPE * both["n_turns"].to_numpy(float)

    lo = float(min(x.min(), y.min()))
    hi = float(max(x.max(), y.max()))
    pad = 0.04 * (hi - lo) if hi > lo else 0.01
    lim = (lo - pad, hi + pad)

    fig, ax = plt.subplots(figsize=(9.4, 10.8), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor(PANEL)
    ax.scatter(x, y, s=sizes, c=GREY, edgecolors="none", alpha=0.75,
               zorder=3)
    ax.axline((0.0, 0.0), slope=1.0, color=colour, linewidth=1.0,
              linestyle="-", zorder=2)

    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("mean apex retention \u2014 odd-numbered turns (half A)",
                  fontsize=10)
    ax.set_ylabel("mean apex retention \u2014 even-numbered turns (half B)",
                  fontsize=10)
    ax.tick_params(labelsize=9)
    ax.grid(True, color="white", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(BORDER)

    handles = [Line2D([0], [0], marker="o", linestyle="none",
                      markersize=math.sqrt(DOT_BASE + DOT_SLOPE * n),
                      markerfacecolor=GREY, markeredgecolor="none", alpha=0.75,
                      label=f"{n} turns")
               for n in (10, 50, 100)]

    fig.text(0.5, 0.985, TITLE_ODD, ha="center", va="center", fontsize=14.5,
             fontweight="bold")
    fig.text(0.155, 0.915, "\n".join(odd_annotation_lines(stats, ci)),
             va="top", ha="left", fontsize=8.8, linespacing=1.5,
             color="#222222")
    fig.legend(handles=handles, title="player turns", loc="upper right",
               bbox_to_anchor=(0.94, 0.915), frameon=True, framealpha=0.92,
               edgecolor=BORDER, fontsize=8.5, title_fontsize=9.0)
    fig.subplots_adjust(left=0.155, right=0.94, top=0.72, bottom=0.06)

    texts = canvas_texts(fig)
    refuse_banned(texts, FIGURE_ODD)
    visible = visible_strings(fig)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return visible, texts


# --------------------------------------------------------- season-vs-season
def season_join(slice_df: pd.DataFrame, *, season_a: int = SEASON_A,
                season_b: int = SEASON_B) -> dict:
    """Players with at least one turn in both seasons, and the ``>= 10`` flag."""
    means = fv.season_means(slice_df)
    a = means[means["season"] == season_a].set_index("nfl_id")
    b = means[means["season"] == season_b].set_index("nfl_id")
    both = a.join(b, how="inner", lsuffix="_a", rsuffix="_b").reset_index()
    both = both.rename(columns={
        "k_a": "n_2024", "k_b": "n_2025",
        "mean_a": "mean_2024", "mean_b": "mean_2025",
    })
    both["n_2024"] = both["n_2024"].astype(int)
    both["n_2025"] = both["n_2025"].astype(int)
    both["in_min10"] = (both["n_2024"] >= 10) & (both["n_2025"] >= 10)
    both = both.sort_values("nfl_id", kind="mergesort").reset_index(drop=True)
    return {
        "players": both,
        "pool_2024": int((means["season"] == season_a).sum()),
        "pool_2025": int((means["season"] == season_b).sum()),
        "n_both": int(len(both)),
    }


def season_panels(slice_df: pd.DataFrame) -> dict:
    """Both season panels, with r taken from the imported estimator."""
    joined = season_join(slice_df)
    players = joined["players"]
    sub10 = players[players["in_min10"]]

    r1 = fv.season_correlation(slice_df, SEASON_A, SEASON_B, min_turns=1)
    r10 = fv.season_correlation(slice_df, SEASON_A, SEASON_B, min_turns=10)
    r1_plot = fv._pearson(players["mean_2024"].to_numpy(float),
                          players["mean_2025"].to_numpy(float))
    r10_plot = fv._pearson(sub10["mean_2024"].to_numpy(float),
                           sub10["mean_2025"].to_numpy(float))
    return {
        "joined": joined,
        "players": players,
        "sub10": sub10,
        "r1": float(r1_plot), "n1": int(len(players)),
        "r10": float(r10_plot), "n10": int(len(sub10)),
        "r1_estimator": r1, "r10_estimator": r10,
        "turns_2024_all": int(players["n_2024"].sum()),
        "turns_2025_all": int(players["n_2025"].sum()),
        "turns_2024_min10": int(sub10["n_2024"].sum()),
        "turns_2025_min10": int(sub10["n_2025"].sum()),
        "n_hollow": int((~players["in_min10"]).sum()),
    }


def reconcile_season(panels: dict) -> None:
    """Equality proofs: the plotted points reproduce the committed r and n."""
    for key, n_key, min_turns in (("r1", "n1", 1), ("r10", "n10", 10)):
        want = COMMITTED_SEASON[min_turns]
        got_r = round(float(panels[key]), 4)
        assert got_r == want["r"], (
            f"{FIGURE_SEASON}: recomputed r (min {min_turns}) = "
            f"{panels[key]!r} does not reconcile with {want['r']!r}")
        assert panels[n_key] == want["n_players"], (
            f"{FIGURE_SEASON}: plotted n (min {min_turns}) = {panels[n_key]} "
            f"!= committed {want['n_players']}")
        estimator = panels[f"r{min_turns}_estimator"]
        assert round(float(estimator["r"]), 4) == got_r, (
            f"{FIGURE_SEASON}: season_correlation r (min {min_turns}) = "
            f"{estimator['r']!r} != recomputed {panels[key]!r}")
        assert int(estimator["n_players"]) == panels[n_key], (
            f"{FIGURE_SEASON}: season_correlation n (min {min_turns}) = "
            f"{estimator['n_players']} != plotted {panels[n_key]}")


def season_csv_frame(players: pd.DataFrame) -> pd.DataFrame:
    """One row per plotted player; slot order by the 2024 then 2025 mean."""
    ordered = players.sort_values(["mean_2024", "mean_2025"],
                                  ascending=True, na_position="last",
                                  kind="mergesort").reset_index(drop=True)
    return pd.DataFrame({
        "player_slot": np.arange(1, len(ordered) + 1),
        "n_2024": ordered["n_2024"].astype(int),
        "n_2025": ordered["n_2025"].astype(int),
        "mean_2024": ordered["mean_2024"].astype(float),
        "mean_2025": ordered["mean_2025"].astype(float),
        "in_min10": ordered["in_min10"].astype(bool),
    })


def _r_text(r: float, n: int) -> str:
    """``r = 0.1959``, or ``r = not estimable`` for a thin panel."""
    if n < 3 or not np.isfinite(r):
        return "r = not estimable"
    return f"r = {_fmt(r, 4)}"


def season_panel_lines(n: int, r: float, turns_a: int, turns_b: int) -> list[str]:
    return [
        f"n = {n:,} {_plural(n, 'player', 'players')}",
        _r_text(r, n),
        f"{SEASON_A}: {turns_a:,} turns · {SEASON_B}: {turns_b:,} turns",
    ]


def season_pool_line(joined: dict) -> str:
    return (f"{joined['pool_2024']} players in {SEASON_A}, "
            f"{joined['pool_2025']} in {SEASON_B}, "
            f"{joined['n_both']} in both")


def ceiling_line(counts: dict) -> str:
    ceiling, turns = counts["ceiling_at_1.000"], counts["turns"]
    share = 100.0 * ceiling / turns if turns else math.nan
    return (f"{ceiling:,} of {turns:,} turns ({share:.1f}%) sit at exactly "
            "1.000 \u2014 a measurement ceiling, not player structure; it "
            "pulls every player mean toward 1.0")


def regression_note() -> str:
    """The regression-to-the-mean objection, answered in plain words."""
    return ("Regression to the mean: both axes are per-season means and are "
            "therefore noisy; splitting one player's turns by season enlarges "
            "that noise, which flattens the cloud even when a real player "
            "effect exists. Neither panel is an estimate of a player effect, "
            "and the contrast between the panels is the point.")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_season(panels: dict, counts: dict, joined_meta: dict, *,
                colour: str, out_png: Path) -> tuple[list[str], list[str]]:
    """Draw both season panels; thin panels render with ``not estimable``."""
    players = panels["players"]
    sub10 = panels["sub10"]
    hollow = players[~players["in_min10"]]

    xs = players["mean_2024"].to_numpy(float)
    ys = players["mean_2025"].to_numpy(float)
    lo = float(min(xs.min(), ys.min()))
    hi = float(max(xs.max(), ys.max()))
    pad = 0.04 * (hi - lo) if hi > lo else 0.01
    lim = (lo - pad, hi + pad)

    fig, axes = plt.subplots(1, 2, figsize=(13.8, 10.6), dpi=150)
    fig.patch.set_facecolor("white")
    specs = [
        (axes[0], players, panels["r1"], panels["n1"],
         panels["turns_2024_all"], panels["turns_2025_all"],
         f"at least 1 turn in each season"),
        (axes[1], sub10, panels["r10"], panels["n10"],
         panels["turns_2024_min10"], panels["turns_2025_min10"],
         "at least 10 turns in each season"),
    ]
    for ax, frame, r, n, turns_a, turns_b, subtitle in specs:
        ax.set_facecolor(PANEL)
        if ax is axes[0] and len(hollow):
            ax.scatter(hollow["mean_2024"].to_numpy(float),
                       hollow["mean_2025"].to_numpy(float),
                       s=40, facecolors="none", edgecolors=HOLLOW,
                       linewidths=1.0, alpha=0.95, zorder=3)
        ax.scatter(frame["mean_2024"].to_numpy(float),
                   frame["mean_2025"].to_numpy(float),
                   s=40, c=GREY, edgecolors="none", alpha=0.80, zorder=4)
        ax.axline((0.0, 0.0), slope=1.0, color=colour, linewidth=1.0,
                  linestyle="-", zorder=2)
        ax.set_xlim(lim)
        ax.set_ylim(lim)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, color="white", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        ax.set_xlabel(f"{SEASON_A} mean apex retention", fontsize=10)
        if ax is axes[0]:
            ax.set_ylabel(f"{SEASON_B} mean apex retention", fontsize=10)
        else:
            ax.set_ylabel("")
        ax.tick_params(labelsize=9)
        ax.set_title(subtitle, fontsize=11.5, loc="left", fontweight="bold",
                     pad=56)
        ax.text(0.0, 1.015,
                "\n".join(season_panel_lines(n, r, turns_a, turns_b)),
                transform=ax.transAxes, va="bottom", ha="left", fontsize=8.8,
                linespacing=1.4, zorder=5, color="#222222")

    hollow_handle = Line2D([0], [0], marker="o", linestyle="none",
                           markersize=6.5, markerfacecolor="none",
                           markeredgecolor=HOLLOW, markeredgewidth=1.0,
                           label=("light = fewer than 10 turns in a season "
                                  "(absent from the right panel)"))
    fig.legend(handles=[hollow_handle], loc="lower center", frameon=False,
               fontsize=9.0, bbox_to_anchor=(0.5, 0.168))

    fig.text(0.5, 0.966, TITLE_SEASON, ha="center", va="center", fontsize=14.5,
             fontweight="bold")

    foot_lines = ([season_pool_line(joined_meta)]
                  + _wrap(ceiling_line(counts), 128)
                  + _wrap(regression_note(), 128))
    fig.text(0.5, 0.118, "\n".join(foot_lines), ha="center", va="top",
             fontsize=8.2, linespacing=1.45, color="#333333")
    fig.subplots_adjust(left=0.05, right=0.985, top=0.80, bottom=0.235,
                        wspace=0.10)

    texts = canvas_texts(fig)
    refuse_banned(texts, FIGURE_SEASON)
    visible = visible_strings(fig)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150, facecolor="white",
                metadata={"Software": "bdb27-figures"})
    plt.close(fig)
    return visible, texts


# ------------------------------------------------- descriptive between-player share
def eta_squared(y: Sequence[float], groups: Sequence[Any]) -> dict:
    """``eta2 = SSB / SST`` — the raw descriptive between-player share.

    ``SSB = sum_i n_i (mean_i - grand)^2`` and ``SST = sum (y - grand)^2`` over
    the same observations. This is the observed share of variation that sits
    between players. It is **not** the ICC(1), which is the ratio of the
    between-player variance *component* to total variance under the one-way
    random-effects model.

    Arithmetic stays in the stored precision of ``y`` (float32 in the uploaded
    parquet): the quoted 0.0176796 is the stored-precision value, and the
    float64 recomputation (0.0176795) is written beside it in the artefact.
    """
    frame = pd.DataFrame({"y": pd.Series(y), "g": pd.Series(groups)}).dropna()
    if frame.empty:
        return {"eta2": None, "ssb": None, "sst": None, "grand_mean": None,
                "n_obs": 0, "n_players": 0}
    grand = frame["y"].mean()
    by = frame.groupby("g", sort=True)["y"].agg(["count", "mean"])
    ssb = (by["count"] * (by["mean"] - grand) ** 2).sum()
    sst = ((frame["y"] - grand) ** 2).sum()
    eta2 = float(ssb / sst) if sst else math.nan
    return {
        "eta2": eta2,
        "ssb": float(ssb),
        "sst": float(sst),
        "grand_mean": float(grand),
        "n_obs": int(len(frame)),
        "n_players": int(len(by)),
    }


def _player_moments(y: Sequence[float], groups: Sequence[Any]):
    """Per-player ``(k, sum y, sum y^2)`` so a cluster draw costs O(g)."""
    values = np.asarray(y, float)
    ids, inv = np.unique(np.asarray(groups), return_inverse=True)
    k = np.bincount(inv, minlength=len(ids)).astype(float)
    s1 = np.bincount(inv, weights=values, minlength=len(ids))
    s2 = np.bincount(inv, weights=values ** 2, minlength=len(ids))
    return ids, k, s1, s2


def _eta2_from_moments(k: np.ndarray, s1: np.ndarray, s2: np.ndarray) -> float:
    N = float(k.sum())
    grand = float(s1.sum() / N)
    ssb = float(np.sum(k * (s1 / k - grand) ** 2))
    sst = float(s2.sum() - N * grand ** 2)
    return ssb / sst if sst else math.nan


def cluster_bootstrap_eta2(slice_df: pd.DataFrame, *, reps: int,
                           stream: str = "descriptive_share",
                           level: float = 0.95) -> dict:
    """Player-level cluster bootstrap: resample players, keep whole turn sets."""
    _, k, s1, s2 = _player_moments(slice_df["apex_retention"].to_numpy(),
                                   slice_df["nfl_id"].to_numpy())
    g = int(len(k))
    rng = fv.rng_for(stream)
    draws = np.full(int(reps), np.nan)
    for i in range(int(reps)):
        idx = rng.integers(0, g, size=g)
        draws[i] = _eta2_from_moments(k[idx], s1[idx], s2[idx])
    good = draws[np.isfinite(draws)]
    alpha = (1.0 - level) / 2.0
    if good.size:
        lo, hi = np.quantile(good, [alpha, 1.0 - alpha])
    else:
        lo = hi = math.nan
    return {
        "lo": float(lo), "hi": float(hi), "level": float(level),
        "reps": int(reps), "n_estimable_reps": int(good.size),
        "seed": fv.SEED, "stream": stream,
        "method": ("player-level cluster bootstrap, percentile interval "
                   "(players resampled with replacement, whole turn sets kept)"),
    }


def descriptive_share(slice_df: pd.DataFrame, *,
                      reps: int = fv.BOOTSTRAP_REPS,
                      reps_precision: int = 20000) -> dict:
    """eta2 point estimate, its 2,000-rep interval, and the 20,000-rep run."""
    point = eta_squared(slice_df["apex_retention"].to_numpy(),
                        slice_df["nfl_id"].to_numpy())
    _, k, s1, s2 = _player_moments(slice_df["apex_retention"].to_numpy(),
                                   slice_df["nfl_id"].to_numpy())
    point_f64 = _eta2_from_moments(k, s1, s2)
    ci = cluster_bootstrap_eta2(slice_df, reps=reps)
    ci_precision = (cluster_bootstrap_eta2(slice_df, reps=reps_precision)
                    if reps_precision and reps_precision != reps else None)
    return {
        "point": point,
        "point_float64": float(point_f64),
        "ci": ci,
        "ci_precision": ci_precision,
        "counts": slice_counts(slice_df),
    }


def load_icc_committed(path: Path | str = BAND_RELIABILITY_JSON) -> dict:
    """The committed ICC(1) for the all-band scenario."""
    full = repo_path(path)
    if not full.exists():
        raise common.StageRefused(
            f"{STAGE}: {full} is absent — the committed ICC lives there.")
    try:
        document = json.loads(full.read_text(encoding="utf-8"))
    except Exception as exc:
        raise common.StageRefused(
            f"{STAGE}: cannot read {full}: {type(exc).__name__}: {exc}") from exc
    scenarios = document.get("scenarios") or []
    chosen = next((s for s in scenarios if s.get("name") == "all_band"),
                  scenarios[0] if scenarios else None)
    if not chosen or chosen.get("icc") is None:
        raise common.StageRefused(
            f"{STAGE}: {full} carries no scenario with an ICC value.")
    return {
        "value": float(chosen["icc"]),
        "rounded": round(float(chosen["icc"]), 6),
        "scenario": str(chosen.get("name")),
        "source": str(Path(path).as_posix()),
        "sha256": sha256(full),
        "statement": ("0.003382 is the ratio of the between-player variance "
                      "component to total variance under the one-way "
                      "random-effects model (ICC(1)); it must not be described "
                      "as the share of variance between players."),
    }


# ------------------------------------------------------------------- manifests
def manifest_entry_odd(stats: dict, ci: dict, cfg: common.Config,
                       png: Path, csv: Path) -> dict:
    numbers = {
        "n_players_plotted": stats["n_players"],
        "n_players_total": stats["n_players_total"],
        "n_turns": stats["n_turns"],
        "n_dropped_one_sided": stats["n_dropped"],
        "r": round(float(stats["r"]), 4),
        "r_3dp": round(float(stats["r"]), 3),
        "spearman_brown": round(float(stats["spearman_brown"]), 4),
        "spearman_brown_3dp": round(float(stats["spearman_brown"]), 3),
        "ci_lo": round(ci["lo"], 4),
        "ci_hi": round(ci["hi"], 4),
        "ci_lo_3dp": round(ci["lo"], 3),
        "ci_hi_3dp": round(ci["hi"], 3),
        "n_ceiling_at_1.000": stats["n_ceiling"],
        "ceiling_share_pct": round(100.0 * stats["ceiling_share"], 1),
        "n_above_1.000": stats["n_above_one"],
        "legend_turns": [10, 50, 100],
    }
    claim = (f"A player's odd-numbered and even-numbered band turns do not "
             f"agree: r = {stats['r']:.4f} (Spearman\u2013Brown "
             f"{stats['spearman_brown']:.4f}), 95% bootstrap CI on r "
             f"[{ci['lo']:.4f}, {ci['hi']:.4f}], {stats['n_players']} of "
             f"{stats['n_players_total']} players plotted. "
             f"{stats['n_ceiling']:,} of {stats['n_turns']:,} turns sit at "
             "exactly 1.000.")
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_ODD,
        "exploratory": True,
        "in_report": False,
        "title": TITLE_ODD,
        "claim": claim,
        "provenance": common.PROVENANCE_FULL,
        "stamp": figures.stamp_text("full"),
        "filter": ("bdb27_full_db_turns.parquet: setting == 'game' & "
                   "position_group == 'DB' & 30 <= abs_turn_angle < 60 & "
                   "valid == True"),
        "caveats": [
            (f"{stats['n_ceiling']:,} of {stats['n_turns']:,} turns sit at "
             "exactly 1.000: a measurement ceiling, not player structure, "
             "which pulls every player mean toward 1.0."),
            ("The interval is the bootstrap interval on r, not on "
             "Spearman-Brown."),
            "DB only; no other position group is drawn, not even as context.",
            ("Exploratory: not one of the four report figures; written output "
             "does not cite it."),
        ],
        "png": str(png.relative_to(ROOT)),
        "csv": str(csv.relative_to(ROOT)),
        "png_sha256": sha256(png),
        "csv_sha256": sha256(csv),
        "numbers": numbers,
        "split_half": {
            "method": "odd/even by (season, game, play, frame) + "
                      "Spearman-Brown",
            "ci": {"lo": ci["lo"], "hi": ci["hi"], "reps": ci["reps"],
                   "seed": ci["seed"], "kind": ci["kind"],
                   "source": ci["source"]},
        },
        "sources": [TURNS_PARQUET.as_posix(), ci["source"]],
        "source_artefacts": [
            {"path": TURNS_PARQUET.as_posix(), "sha256": sha256(TURNS_PARQUET),
             "provenance": common.PROVENANCE_FULL,
             "role": "uploaded full-run turn table (2023-2025), DB only"},
            {"path": ci["source"], "sha256": ci["sha256"],
             "provenance": common.PROVENANCE_FULL,
             "role": "committed split-half coefficient and bootstrap interval"},
        ],
        "git_rev": common.git_rev(),
        "config_sha": common.config_fingerprint(cfg),
    }


def manifest_entry_season(panels: dict, counts: dict, cfg: common.Config,
                          png: Path, csv: Path) -> dict:
    numbers = {
        "n_panel_min1": panels["n1"],
        "r_panel_min1": round(panels["r1"], 4),
        "turns_2024_min1": panels["turns_2024_all"],
        "turns_2025_min1": panels["turns_2025_all"],
        "n_panel_min10": panels["n10"],
        "r_panel_min10": round(panels["r10"], 4),
        "turns_2024_min10": panels["turns_2024_min10"],
        "turns_2025_min10": panels["turns_2025_min10"],
        "n_hollow_min1_panel": panels["n_hollow"],
        "pool_2024": panels["joined"]["pool_2024"],
        "pool_2025": panels["joined"]["pool_2025"],
        "pool_both": panels["joined"]["n_both"],
        "min_turns_rule": 10,
        "n_ceiling_at_1.000": counts["ceiling_at_1.000"],
        "n_turns": counts["turns"],
        "ceiling_share_pct": round(
            100.0 * counts["ceiling_at_1.000"] / counts["turns"], 1),
    }
    claim = (f"Per-player mean retention in 2024 against 2025: r = "
             f"{panels['r1']:.4f} across all {panels['n1']} players with a "
             f"turn in both seasons, and r = {panels['r10']:.4f} across the "
             f"{panels['n10']} players with at least 10 turns in each. The "
             "apparent relationship lives in the thin per-season samples.")
    return {
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "figure": FIGURE_SEASON,
        "exploratory": True,
        "in_report": False,
        "title": TITLE_SEASON,
        "claim": claim,
        "provenance": common.PROVENANCE_FULL,
        "stamp": figures.stamp_text("full"),
        "filter": ("bdb27_full_db_turns.parquet: setting == 'game' & "
                   "position_group == 'DB' & 30 <= abs_turn_angle < 60 & "
                   "valid == True"),
        "caveats": [
            (f"Measurement ceiling: {counts['ceiling_at_1.000']:,} of "
             f"{counts['turns']:,} turns sit at exactly 1.000, pulling every "
             "player mean toward the same value."),
            ("Regression to the mean: both axes are per-season means and "
             "therefore noisy; splitting one player's turns by season enlarges "
             "that noise, which flattens the cloud even when a real player "
             "effect exists. Neither panel estimates a player effect."),
            "DB only; no other position group is drawn, not even as context.",
            ("Exploratory: not one of the four report figures; written output "
             "does not cite it."),
        ],
        "png": str(png.relative_to(ROOT)),
        "csv": str(csv.relative_to(ROOT)),
        "png_sha256": sha256(png),
        "csv_sha256": sha256(csv),
        "numbers": numbers,
        "sources": [TURNS_PARQUET.as_posix()],
        "source_artefacts": [
            {"path": TURNS_PARQUET.as_posix(), "sha256": sha256(TURNS_PARQUET),
             "provenance": common.PROVENANCE_FULL,
             "role": "uploaded full-run turn table (2023-2025), DB only"},
        ],
        "git_rev": common.git_rev(),
        "config_sha": common.config_fingerprint(cfg),
    }


def merge_manifest(entry: dict, path: Path, cfg: common.Config) -> dict:
    """Insert one figure's entry; every other entry is left untouched."""
    path = Path(path)
    document: dict = {}
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    figures_map = dict(document.get("figures", {}))
    figures_map[entry["figure"]] = entry
    stamps = [str(value.get("stamp", "")) for value in figures_map.values()]
    full = sum(1 for s in stamps if s == figures.stamp_text("full"))
    schematic = sum(1 for value in figures_map.values()
                    if value.get("status") == "SCHEMATIC")
    sample = sum(1 for s in stamps
                 if s == figures.stamp_text("sample"))
    other = len(stamps) - full - sample - schematic
    if full == len(stamps) and stamps:
        provenance, label = "FULL", "FULL DATA OUTPUT"
    elif sample == len(stamps) and stamps:
        provenance, label = common.PROVENANCE_SAMPLE, common.SAMPLE_LABEL
    else:
        provenance = "MIXED"
        label = (f"MIXED PROVENANCE — {full} FULL, {sample} sample"
                 + (f", {schematic} schematic" if schematic else "")
                 + (f", {other} other" if other else ""))
    document["figures"] = figures_map
    document.update({
        "provenance": provenance,
        "label": label,
        "stamp": label,
        "stage": STAGE,
        "artefact_version": ARTEFACT_VERSION,
        "config_sha": common.config_fingerprint(cfg),
    })
    document.pop("generated_utc", None)
    common.write_json(document, path, "full", cfg)
    return document


# ------------------------------------------------------------------ artefact IO
def write_figure_artefacts(*, figure: str, entry: dict, visible: list[str],
                           texts: list[str], csv_path: Path,
                           prov_path: Path, text_path: Path,
                           cfg: common.Config) -> dict:
    """Write provenance JSON, drawn-text dump and the one manifest entry.

    The CSV and PNG are written by the caller; ``entry`` already carries both
    paths and their sha256 values. The drawn-text dump deliberately carries no
    ``generated_utc`` so two runs are byte-identical.
    """
    record = dict(entry)
    record["outputs"] = {
        "png": entry["png"], "png_sha256": entry["png_sha256"],
        "csv": entry["csv"], "csv_sha256": entry["csv_sha256"],
    }
    record["seed"] = fv.SEED
    common.write_json(record, prov_path, "full", cfg)
    write_drawn_text(text_path, {
        "provenance": common.PROVENANCE_FULL,
        "label": figures.stamp_text("full"),
        "config_sha": common.config_fingerprint(cfg),
        "figure": figure,
        "stamp": figures.stamp_text("full"),
        "strings": visible,
        "scan_strings": texts,
        "banned_tokens": list(BANNED),
        "banned_hits": [],
    })
    merge_manifest(entry, prov_path.parent / "figures_manifest.json", cfg)
    return record
