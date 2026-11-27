"""Supply and demand: the share of defensive-back turns by angle band.

What the Combine supplies, against what the game demands. Each setting's own
turns are expressed as a share of that setting's total, because the two are not
sampled alike: 122 draft prospects run one drill session each, while the game
side is every tracked defensive back across three seasons.

The 30-45 and 45-60 bands are merged into one 30-60 bar on this chart. The
table printed below reports every band separately.

Run ``python supply_demand.py`` after ``python turns.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from turns import OUT_DIR, load_turns, valid_turns

#: Reporting bands on this chart: 30-45 and 45-60 are merged.
EDGES = (30.0, 60.0, 90.0, 120.0, 150.0, 180.0)
LABELS = ("30\u201360\u00b0", "60\u201390\u00b0", "90\u2013120\u00b0",
          "120\u2013150\u00b0", "150\u2013180\u00b0", ">180\u00b0")
FOCUS_COLOUR = "#C00000"
COLOURS = {"combine": "#0072B2", "game": "#D55E00"}
SETTING_LABEL = {"combine": "Combine", "game": "Game"}


def band_counts(frame: pd.DataFrame) -> pd.Series:
    angle = pd.to_numeric(frame["abs_turn_angle"], errors="coerce")
    out = {}
    for i in range(len(EDGES) - 1):
        out[LABELS[i]] = int(((angle >= EDGES[i]) & (angle < EDGES[i + 1])).sum())
    out[LABELS[-1]] = int((angle >= EDGES[-1]).sum())
    assert sum(out.values()) == len(frame)
    return pd.Series(out)


def build_table(turns: pd.DataFrame) -> pd.DataFrame:
    combine = valid_turns(turns, "combine")
    game = valid_turns(turns, "game")
    rows = []
    for label in LABELS:
        row = {"band": label}
        for setting, frame in (("combine", combine), ("game", game)):
            n = int(band_counts(frame).get(label, 0)) if len(frame) else 0
            row[f"{setting}_n"] = n
            row[f"{setting}_share"] = (n / len(frame) * 100.0) if len(frame) else np.nan
            row[f"{setting}_total"] = len(frame)
            row[f"{setting}_players"] = int(frame["nfl_id"].nunique()) if len(frame) else 0
        if len(combine) and len(game):
            row["ratio_game_over_combine"] = (row["game_share"] / row["combine_share"]
                                              if row["combine_share"] else np.nan)
        else:
            row["ratio_game_over_combine"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def print_table(table: pd.DataFrame, combine: pd.DataFrame, game: pd.DataFrame) -> None:
    print(f"\n{'band':>9} {'Combine n':>10} {'share %':>8} {'Game n':>9} "
          f"{'share %':>8} {'ratio':>6}")
    for _, r in table.iterrows():
        game_n = f"{int(r['game_n']):,}" if len(game) else "n/a"
        game_s = f"{r['game_share']:.2f}" if len(game) else "n/a"
        ratio = f"{r['ratio_game_over_combine']:.1f}x" if len(game) else "n/a"
        print(f"{r['band']:>9} {int(r['combine_n']):>10,} {r['combine_share']:>8.2f} "
              f"{game_n:>9} {game_s:>8} {ratio:>6}")
    print(f"{'total':>9} {len(combine):>10,} {100.0:>8.2f} "
          f"{(f'{len(game):,}' if len(game) else 'n/a'):>9}")
    print(f"players   {combine['nfl_id'].nunique():>10,} "
          + (f"{game['nfl_id'].nunique():>9,}" if len(game) else f"{'n/a':>9}"))
    if len(game):
        print(f"player-games on the game side: "
              f"{game.groupby(['nfl_id', 'game_id']).ngroups:,}")


def draw(table: pd.DataFrame, combine: pd.DataFrame, game: pd.DataFrame,
         out_png) -> None:
    x = np.arange(len(LABELS))
    width = 0.38 if len(game) else 0.62
    fig, ax = plt.subplots(figsize=(11.5, 7.0), dpi=150)
    ax.set_facecolor("#EBEBEB")
    ax.axvspan(-0.5, 0.5, color=FOCUS_COLOUR, alpha=0.10, zorder=0)
    if len(combine):
        ax.bar(x - (width / 2 if len(game) else 0), table["combine_share"], width,
               color=COLOURS["combine"], label="Combine", zorder=2)
    if len(game):
        ax.bar(x + (width / 2 if len(combine) else 0), table["game_share"], width,
               color=COLOURS["game"], label="Game", zorder=2)

    ax.set_xticks(x)
    ax.set_xticklabels(LABELS, fontsize=10)
    top = float(np.nanmax(table[["combine_share", "game_share"]].to_numpy()))
    ax.set_ylim(0, (int(top) // 10 + 1) * 10)
    ax.set_ylabel("Share of turns within setting (%)", fontsize=9.5)
    ax.grid(axis="y", color="white", linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    if len(game) and len(combine):
        c0 = float(table.loc[0, "combine_share"])
        g0 = float(table.loc[0, "game_share"])
        ratio = g0 / c0 if c0 else float("nan")
        ax.set_title(f"The hole: games demand {ratio:.1f}\u00d7 the share of 30\u201360\u00b0 "
                     "DB breaks the Combine supplies", fontsize=15, fontweight="bold",
                     loc="left")
    else:
        ax.set_title("Share of defensive-back turns by angle band", fontsize=15,
                     fontweight="bold", loc="left")
        ax.text(0.0, -0.12, "Game side not drawn: no game tracking file was found.",
                transform=ax.transAxes, fontsize=8.5, color="#555555")
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    fig.savefig(out_png, facecolor="white", metadata={"Software": "bdb27-minimal"})
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_DIR))
    args = p.parse_args(argv)
    out = Path(args.out)

    turns = load_turns()
    combine = valid_turns(turns, "combine")
    game = valid_turns(turns, "game")
    if combine.empty and game.empty:
        print("no valid DB turns in the turn table; run turns.py first")
        return 0
    if combine.empty:
        print("no Combine turns: the Combine files were not available when the "
              "turn table was built")
    if game.empty:
        print("no game turns: the game side needs the full game tracking file "
              "(the partial sample on hand is not it)")

    table = build_table(turns)
    print_table(table, combine, game)
    out.mkdir(parents=True, exist_ok=True)
    png = out / "fig_supply_demand.png"
    draw(table, combine, game, png)
    table.to_csv(out / "fig_supply_demand.csv", index=False)
    print(f"\nwrote {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
