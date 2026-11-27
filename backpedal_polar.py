"""Every turn in the backpedal 45: one dial, one dot per turn.

The drill is called "back pedal and transition 45 degree reaction". This draws
all of its detected turns on one protractor, zero at the top and the scale
running clockwise, so the question the drill's name raises can be answered by
looking: does a 45 degree break put the turns near 45?

Reading the dial: a point at bearing b is the exit direction of travel when the
entry points straight up, so 45 is a 45 degree break and 180 is a straight
reversal. Positive turn angles run clockwise because heading increases clockwise
from +y in this pipeline; a counter-clockwise turn of -116 degrees is drawn at
bearing 244. The measured signed angle stays in the CSV.

Run ``python backpedal_polar.py`` after ``python turns.py``.
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

DRILL = "BACK_PEDAL_AND_TRANSITION_45_DEGREE_REACTION"
TITLE = "Every turn in the backpedal"
SEED = 20261123          # deterministic radial jitter, not a statistical device
JITTER = 0.10            # +/- radial units; no variable rides the radius
RADIUS = 1.0
RMAX = 1.34
TICK_STEP = 45
COLOUR = "#0072B2"


def build_table(frame: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    jitter = rng.uniform(-JITTER, JITTER, size=len(frame))
    angles = frame["turn_angle"].to_numpy(float)
    return pd.DataFrame({
        "event_id": frame["event_id"].to_numpy(),
        "nfl_id": frame["nfl_id"].to_numpy(),
        "attempt": frame["attempt"].to_numpy(),
        "drill_name": frame["drill_name"].to_numpy(),
        "turn_angle_deg": angles,
        "abs_turn_angle_deg": np.abs(angles),
        "bearing_deg": np.mod(angles, 360.0),
        "radius_drawn": RADIUS + jitter,
    })


def lobe(table: pd.DataFrame, sign: int) -> dict:
    part = table[table["turn_angle_deg"] * sign > 0]
    if part.empty:
        return {"n": 0, "median": float("nan"), "q25": float("nan"),
                "q75": float("nan")}
    b = part["bearing_deg"].to_numpy(float)
    return {"n": int(len(b)), "median": float(np.median(b)),
            "q25": float(np.percentile(b, 25)), "q75": float(np.percentile(b, 75))}


def draw(table: pd.DataFrame, out_png: Path) -> None:
    fig = plt.figure(figsize=(7.4, 7.6))
    ax = fig.add_subplot(1, 1, 1, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ticks = list(range(0, 360, TICK_STEP))
    ax.set_thetagrids(ticks, labels=[str(t) for t in ticks])
    ax.set_ylim(0.0, RMAX)
    ax.set_yticks([])
    ax.grid(color="#D0D0D0", linewidth=0.8)
    ax.spines["polar"].set_color("#D0D0D0")

    around = np.linspace(0.0, 2.0 * np.pi, 241)
    ax.plot(around, np.full_like(around, RADIUS), color="#B0B0B0",
            linewidth=0.9, linestyle=":", zorder=1)

    if len(table):
        ax.scatter(np.radians(table["bearing_deg"].to_numpy(float)),
                   table["radius_drawn"].to_numpy(float),
                   s=26, color=COLOUR, alpha=0.55, edgecolors="none", zorder=3)

    ax.set_title(TITLE, fontsize=16, loc="left", pad=18)
    ax.tick_params(axis="x", labelsize=10.5, colors="#555555", pad=1.0)
    fig.tight_layout()
    fig.savefig(out_png, dpi=170, facecolor="white",
                bbox_inches="tight", pad_inches=0.25,
                metadata={"Software": "bdb27-minimal"})
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_DIR))
    args = p.parse_args(argv)
    out = Path(args.out)

    turns = load_turns()
    combine = valid_turns(turns, "combine")
    if combine.empty or "drill_name" not in combine.columns:
        print("no Combine turns in the turn table. The Combine tracking file is "
              "needed to draw this dial; nothing was written.")
        return 0
    frame = combine[combine["drill_name"] == DRILL]
    if frame.empty:
        print(f"no valid turns of {DRILL} in the turn table. The Combine tracking "
              "file is needed to draw this dial; nothing was written.")
        return 0

    table = build_table(frame)
    pos, neg = lobe(table, +1), lobe(table, -1)
    n_players = int(table["nfl_id"].nunique())
    near_45 = int(((table["abs_turn_angle_deg"] >= 30)
                   & (table["abs_turn_angle_deg"] < 60)).sum())
    print(f"{DRILL}: {len(table):,} valid turns from {n_players:,} players")
    print(f"  clockwise side n = {pos['n']:,}, median bearing {pos['median']:.1f} "
          f"degrees (middle half {pos['q25']:.1f} to {pos['q75']:.1f})")
    print(f"  counter-clockwise side n = {neg['n']:,}, median bearing "
          f"{neg['median']:.1f} degrees (middle half {neg['q25']:.1f} to "
          f"{neg['q75']:.1f})")
    print(f"  turns between 30 and 60 degrees on the dial: {near_45} of {len(table):,} "
          f"({near_45 / len(table) * 100:.1f}%)")

    out.mkdir(parents=True, exist_ok=True)
    png = out / "fig_backpedal_polar.png"
    draw(table, png)
    table.to_csv(out / "fig_backpedal_polar.csv", index=False)
    print(f"wrote {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
