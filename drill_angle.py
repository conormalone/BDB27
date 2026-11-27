"""Every Combine turn, sorted by drill.

One row per drill in the Combine's defensive-back repertoire, ordered by the
drill's median absolute turn angle. Every valid turn is drawn at its own angle,
so a drill's row is its whole distribution, not an average. The shaded band is
30-45 degrees at 2-6 yd/s: the cell a short-area break test would want to fill.

A plant is a turn where the player slows to below 80% of his entry speed at the
apex and then re-accelerates out of it; everything else is an arc.

Run ``python drill_angle.py`` after ``python turns.py``.
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

SHORT = {
    "BOX_DRILL": "Box",
    "TERYL_AUSTIN_DRILL_2": "Teryl Austin 2",
    "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION": "Back pedal + 45\u00b0 reaction",
    "BACK_PEDAL_AND_90_DEGREE_BREAK_TO_BALL": "Back pedal + 90\u00b0 break",
    "TERYL_AUSTIN_DRILL_1": "Teryl Austin 1",
    "W_DRILL": "W drill",
    "THREE_CONE_DRILL": "3-cone",
    "SHORT_SHUTTLE": "Short shuttle",
    "GAUNTLET_DRILL": "Gauntlet",
    "FORTY_YARD_DASH": "40-yard dash",
    "LINE_DRILL": "Line drill",
}
TARGET_LO, TARGET_HI = 30.0, 45.0
TARGET_SPEED = (2.0, 6.0)
COLOURS = {"arc": "#0072B2", "plant": "#E69F00"}


def summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for drill, g in frame.groupby("drill_name"):
        rows.append({
            "drill": drill,
            "drill_label": SHORT.get(drill, drill),
            "n_turns": int(len(g)),
            "n_players": int(g["nfl_id"].nunique()),
            "median_abs_angle": float(g["abs_turn_angle"].median()),
            "q1_abs_angle": float(g["abs_turn_angle"].quantile(0.25)),
            "q3_abs_angle": float(g["abs_turn_angle"].quantile(0.75)),
            "median_entry_speed": float(g["entry_speed"].median()),
            "share_plant_pct": float(g["plant"].mean() * 100.0),
        })
    table = pd.DataFrame(rows).sort_values("median_abs_angle").reset_index(drop=True)
    return table


def draw(frame: pd.DataFrame, table: pd.DataFrame, out_png: Path) -> None:
    order = list(table["drill"])
    ypos = {d: i for i, d in enumerate(order)}
    rng = np.random.default_rng(11)
    y = frame["drill_name"].map(ypos).to_numpy(float)
    y = y + rng.uniform(-0.16, 0.16, size=len(frame))

    fig, ax = plt.subplots(figsize=(11.5, 7.2), dpi=150)
    ax.set_facecolor("#EBEBEB")
    ax.axvspan(TARGET_LO, TARGET_HI, color="#C00000", alpha=0.10, zorder=0)
    for kind, colour in (("arc", COLOURS["arc"]), ("plant", COLOURS["plant"])):
        mask = frame["turn_type"].to_numpy() == kind
        ax.scatter(frame.loc[mask, "abs_turn_angle"], y[mask], s=16, color=colour,
                   alpha=0.55, edgecolors="none", label=kind, zorder=2)

    for _, r in table.iterrows():
        i = ypos[r["drill"]]
        ax.plot([r["q1_abs_angle"], r["q3_abs_angle"]], [i, i], color="#333333",
                linewidth=1.0, zorder=3)
        ax.plot([r["median_abs_angle"]] * 2, [i - 0.26, i + 0.26], color="#111111",
                linewidth=2.2, zorder=4)

    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([SHORT.get(d, d) for d in order], fontsize=9.5)
    ax.set_ylim(-0.7, len(order) - 0.3)
    ax.set_xlim(0, 200)
    ax.set_xlabel("Absolute turn angle at the detected turn (\u00b0)", fontsize=9.5)
    ax.grid(axis="x", color="white", linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.set_title(f"Every Combine turn, by drill: {len(frame):,} turns from "
                 f"{frame['nfl_id'].nunique()} defensive backs",
                 fontsize=15, fontweight="bold", loc="left")
    ax.text(TARGET_LO + 0.8, len(order) - 0.45,
            "30-45\u00b0 at 2-6 yd/s\n(the cell a break test would fill)",
            color="#C00000", fontsize=8.2, fontweight="bold", va="top")
    ax.legend(frameon=False, loc="lower right", title="turn type")
    fig.tight_layout()
    fig.savefig(out_png, facecolor="white", metadata={"Software": "bdb27-minimal"})
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_DIR))
    args = p.parse_args(argv)
    out = Path(args.out)

    turns = load_turns()
    frame = valid_turns(turns, "combine")
    if frame.empty:
        print("no valid Combine DB turns in the turn table; the Combine tracking "
              "file is needed to draw this figure. Nothing was written.")
        return 0
    frame = frame[frame["abs_turn_angle"].notna() & frame["entry_speed"].notna()]
    if frame.empty:
        print("the Combine turns carry no measured angle or entry speed; nothing "
              "was written.")
        return 0

    table = summary(frame)
    in_cell = frame[(frame["abs_turn_angle"] >= TARGET_LO)
                    & (frame["abs_turn_angle"] < TARGET_HI)
                    & (frame["entry_speed"] >= TARGET_SPEED[0])
                    & (frame["entry_speed"] < TARGET_SPEED[1])]
    print(f"{len(frame):,} valid Combine DB turns from "
          f"{frame['nfl_id'].nunique()} players, {frame['drill_name'].nunique()} drills")
    print(f"\n{'drill':<30} {'turns':>6} {'players':>8} {'median':>7} "
          f"{'entry':>6} {'plants':>7}")
    for _, r in table.sort_values("n_turns", ascending=False).iterrows():
        print(f"{r['drill_label']:<30} {int(r['n_turns']):>6} {int(r['n_players']):>8} "
              f"{r['median_abs_angle']:>7.1f} {r['median_entry_speed']:>6.2f} "
              f"{r['share_plant_pct']:>6.1f}%")
    print(f"\nthe 30-45 degree cell at 2-6 yd/s holds {len(in_cell)} of {len(frame):,} "
          f"turns ({len(in_cell) / len(frame) * 100:.1f}%), from "
          f"{in_cell['nfl_id'].nunique()} players across "
          f"{in_cell['drill_name'].nunique()} drills")

    out.mkdir(parents=True, exist_ok=True)
    png = out / "fig_drill_angle.png"
    draw(frame, table, png)
    table.to_csv(out / "fig_drill_angle.csv", index=False)
    print(f"wrote {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
