"""What a turn is worth: EPA and completion percentage by angle band.

For every defensive-back player-play in a game that contains at least one
detected DB turn, the exposure is the sharpest turn that back made on that
play: 30-60 degrees, 60-90, and so on, with a no-turn baseline for his plays
where the detector found nothing. The outcomes are the expected points added on
the play (from the defensive back's own row) and whether the pass was completed.

Only pass plays are counted, and plays nullified by a penalty are dropped. The
row count is player-plays, not plays: the same play can carry several backs.

This is an association, not a cause. A turn exists because a route broke, so
turning is endogenous to the play; nothing here estimates what a turn costs in
points.

Run ``python epa_bands.py`` after ``python turns.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from turns import (BAND_LABELS, DATA_DIR, MissingInput, OUT_DIR, band_of,
                   load_table, load_turns, valid_turns)

NO_TURN = "none"
BANDS = (NO_TURN, *BAND_LABELS)
DISPLAY = {"none": "no turn", "30-45": "30-45\u00b0", "45-60": "45-60\u00b0",
           "60-90": "60-90\u00b0", "90-120": "90-120\u00b0",
           "120-150": "120-150\u00b0", "150-180": "150-180\u00b0",
           ">180": ">180\u00b0"}
PASS_RESULTS = ("C", "I", "IN", "S")
REQUIRED = ("game_id", "play_id", "nfl_id", "team_abbr", "pass_result",
            "expected_points_added", "play_nullified_by_penalty")


def build_panel(turns: pd.DataFrame, play: pd.DataFrame) -> pd.DataFrame:
    game = valid_turns(turns, "game")
    cohort = set(game["nfl_id"].unique())
    games = set(game["game_id"].unique())

    sharpest = (game.groupby(["game_id", "play_id", "nfl_id"])["abs_turn_angle"]
                .max().rename("max_abs_turn_angle").reset_index())
    pp = play[play["nfl_id"].isin(cohort) & play["game_id"].isin(games)].copy()
    panel = pp.merge(sharpest, on=["game_id", "play_id", "nfl_id"], how="left")

    band = band_of(panel["max_abs_turn_angle"])
    panel["band"] = band.fillna(NO_TURN)

    for col in ("expected_points_added",):
        panel[col] = pd.to_numeric(panel[col], errors="coerce")
    pr = panel["pass_result"].astype("object")
    penalty = panel["play_nullified_by_penalty"].astype("object")
    panel["is_pass"] = pr.isin(PASS_RESULTS)
    panel["is_completion"] = pr == "C"
    panel["is_penalty"] = penalty.astype(str).str.upper().eq("Y")
    return panel


def band_means(panel: pd.DataFrame) -> pd.DataFrame:
    base = panel[panel["is_pass"] & ~panel["is_penalty"]]
    rows = []
    for key in BANDS:
        part = base[base["band"] == key]
        epa = pd.to_numeric(part["expected_points_added"], errors="coerce")
        rows.append({
            "band": DISPLAY[key],
            "band_key": key,
            "n_player_plays": int(len(part)),
            "n_plays": int(part[["game_id", "play_id"]].drop_duplicates().shape[0]),
            "n_players": int(part["nfl_id"].nunique()),
            "epa_mean": float(epa.mean()) if epa.notna().any() else np.nan,
            "epa_n": int(epa.notna().sum()),
            "completion_pct": (float(part["is_completion"].mean() * 100.0)
                               if len(part) else np.nan),
            "completion_n": int(len(part)),
        })
    return pd.DataFrame(rows)


def draw(table: pd.DataFrame, out_png: Path) -> None:
    x = np.arange(len(table))
    labels = list(table["band"])
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.6), dpi=150)
    colour = "#D55E00"

    ax = axes[0]
    ax.set_facecolor("#EBEBEB")
    ax.axhline(0.0, color="#666666", linewidth=0.9)
    ax.bar(x, table["epa_mean"], color=colour, width=0.62, zorder=2)
    ax.set_ylabel("Expected points added (allowed)", fontsize=9.5)
    ax.set_title("EPA", fontsize=11, loc="left")

    ax = axes[1]
    ax.set_facecolor("#EBEBEB")
    ax.bar(x, table["completion_pct"], color=colour, width=0.62, zorder=2)
    ax.set_ylabel("Completion rate (%)", fontsize=9.5)
    ax.set_ylim(0, 100)
    ax.set_title("Completion", fontsize=11, loc="left")

    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8.5)
        ax.grid(axis="y", color="white", linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    fig.suptitle("What a defensive back's sharpest turn on a pass play is worth",
                 fontsize=14, fontweight="bold", x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_png, facecolor="white", metadata={"Software": "bdb27-minimal"})
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default=str(DATA_DIR))
    p.add_argument("--out", default=str(OUT_DIR))
    args = p.parse_args(argv)
    out = Path(args.out)

    turns = load_turns()
    game = valid_turns(turns, "game")
    if game.empty:
        print("no game turns in the turn table, so there is no band to price. "
              "This table needs the full game tracking file; nothing was written.")
        return 0

    try:
        play = load_table("player_play", Path(args.data),
                          keep=("nfl_id", set(game["nfl_id"].unique())))
        missing = [c for c in REQUIRED if c not in play.columns]
        if missing:
            raise MissingInput(
                f"player_play is missing required column(s) {missing}; found "
                f"{sorted(play.columns)}")
    except MissingInput as exc:
        print(f"cannot build the EPA table: {exc}")
        print("The table needs player_play with expected_points_added and "
              "play_nullified_by_penalty on the defensive back's own row.")
        return 0

    panel = build_panel(turns, play)
    table = band_means(panel)
    print(f"{len(panel):,} DB player-plays on games with a detected turn; "
          f"{int((panel['is_pass'] & ~panel['is_penalty']).sum()):,} of them on "
          "pass plays with no penalty")
    print(f"\n{'band':>9} {'player-plays':>13} {'plays':>7} {'EPA':>8} {'cmp %':>7}")
    for _, r in table.iterrows():
        print(f"{r['band']:>9} {int(r['n_player_plays']):>13,} {int(r['n_plays']):>7,} "
              f"{r['epa_mean']:>8.3f} {r['completion_pct']:>7.2f}")
    base = table.loc[table["band_key"] == NO_TURN].iloc[0]
    print(f"\nagainst the no-turn baseline ({int(base['n_player_plays']):,} "
          f"player-plays, EPA {base['epa_mean']:.3f}, completion "
          f"{base['completion_pct']:.1f}%):")
    for _, r in table.iterrows():
        if r["band_key"] == NO_TURN:
            continue
        print(f"  {r['band']:>9}: EPA {r['epa_mean'] - base['epa_mean']:+.3f}, "
              f"completion {r['completion_pct'] - base['completion_pct']:+.1f} "
              f"points")

    out.mkdir(parents=True, exist_ok=True)
    png = out / "fig_epa_bands.png"
    draw(table, png)
    table.to_csv(out / "fig_epa_bands.csv", index=False)
    print(f"\nwrote {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
