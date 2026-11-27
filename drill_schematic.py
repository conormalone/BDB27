"""The tempo break drill, drawn: approach line, break cone, two 45 degree exits.

A plan view in yards, equal aspect, so the 5-yard scale bar is true. A 9-yard
approach to a break cone, a single plant, and two exit gates 5 yards from the
cone at 45 degrees either side of the approach line; six reps, three each side,
alternating; about 90 seconds per player. Same cones and footprint as the
3-cone drill.

This is a drawing, not a measurement: nothing on it is data, and no number in it
comes from a tracking file. The angles and distances are the drill's stated
geometry.

Run ``python drill_schematic.py``. It needs no input files.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Arc

from turns import OUT_DIR

APPROACH_YD = 9.0
BREAK_DEG = 45.0
GATE_YD = 5.0
REPS_TOTAL = 6
REPS_PER_SIDE = 3
DURATION_S = 90
SCALE_BAR_YD = 5.0

COLOURS = {"approach": "#333333", "left": "#0072B2", "right": "#D55E00",
           "cone": "#E69F00", "ink": "#1A1A1A", "arc": "#666666"}
TITLE = "The tempo break: a 45\u00b0 cut at speed, both sides"


def draw(out_png: Path) -> None:
    approach = APPROACH_YD
    gate = GATE_YD
    rad = np.deg2rad(BREAK_DEG)
    gl = (gate * np.cos(rad), gate * np.sin(rad))
    gr = (gate * np.cos(rad), -gate * np.sin(rad))

    scale_y = -4.6
    x_lo, x_hi = -approach - 1.6, gl[0] + 2.0
    y_lo, y_hi = min(-(gate * np.sin(rad) + 1.2), scale_y - 0.35), gl[1] + 0.9
    ratio = (x_hi - x_lo) / (y_hi - y_lo)

    fig_w, fig_h = 11.5, 7.0
    ax_h = 0.86
    ax_w = (ax_h * fig_h * ratio) / fig_w
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=150, facecolor="white")
    ax = fig.add_axes([(1.0 - ax_w) / 2.0, 0.065, ax_w, ax_h])
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.set_aspect("equal")
    ax.axis("off")

    # exits and the approach
    for end, colour in ((gl, COLOURS["left"]), (gr, COLOURS["right"])):
        ax.annotate("", xy=end, xytext=(0.0, 0.0),
                    arrowprops=dict(arrowstyle="-|>", lw=2.6, color=colour,
                                    shrinkA=4, shrinkB=2))
    ax.annotate("", xy=(-0.32, 0.0), xytext=(-approach, 0.0),
                arrowprops=dict(arrowstyle="-|>", lw=3.0,
                                color=COLOURS["approach"], shrinkA=8, shrinkB=0))

    # the two 45 degree arcs, labelled with the drill's break angle only
    for theta1, theta2, r in ((0.0, BREAK_DEG, 1.25), (-BREAK_DEG, 0.0, 2.0)):
        ax.add_patch(Arc((0.0, 0.0), 2 * r, 2 * r, theta1=theta1, theta2=theta2,
                         color=COLOURS["arc"], lw=1.3))
    for sign, r in ((1.0, 1.25), (-1.0, 2.0)):
        mid = np.deg2rad(sign * BREAK_DEG / 2.0)
        ax.text((r + 0.38) * np.cos(mid), (r + 0.38) * np.sin(mid),
                f"{BREAK_DEG:.0f}\u00b0", fontsize=11, fontweight="bold",
                color="#333333", ha="center", va="center")

    # gate ticks, perpendicular to each exit, both labelled with the distance
    half, off = 0.45, 0.45
    for px, py, nx, ny, colour in (
            (gl[0], gl[1], -np.sin(rad), np.cos(rad), COLOURS["left"]),
            (gr[0], gr[1], np.sin(rad), -np.cos(rad), COLOURS["right"])):
        ax.plot([px - half * nx, px + half * nx], [py - half * ny, py + half * ny],
                color=colour, lw=3.2, solid_capstyle="butt")
        ax.text(px + off * nx, py + off * ny, f"{gate:.0f} yd", fontsize=9.2,
                color=colour, ha="center", va="center", fontweight="bold")

    # what the exit is for
    d = np.array([np.cos(rad), np.sin(rad)])
    p = np.array([-np.sin(rad), np.cos(rad)])
    ax.text(*(0.72 * gate * d - 0.42 * p), "re-accelerate 5 yd\nthrough the gate",
            fontsize=7.8, color=COLOURS["left"], ha="center", va="center",
            rotation=BREAK_DEG, rotation_mode="anchor", linespacing=1.15)

    # cones and the single plant
    ax.plot([-approach], [0.0], marker="^", markersize=15, color=COLOURS["cone"],
            markeredgecolor=COLOURS["ink"], markeredgewidth=1.0, linestyle="none",
            zorder=5)
    ax.plot([0.0], [0.0], marker="^", markersize=19, color=COLOURS["cone"],
            markeredgecolor=COLOURS["ink"], markeredgewidth=1.2, linestyle="none",
            zorder=5)
    ax.plot([0.0], [0.0], marker="o", markersize=7, color=COLOURS["ink"],
            linestyle="none", zorder=6)

    ax.text(-0.55, 0.55, "break cone\nsingle plant", fontsize=10.5,
            color=COLOURS["ink"], fontweight="bold", ha="right", va="bottom",
            linespacing=1.25)
    ax.text(-approach, 0.65, "cone", fontsize=9.5, color="#5A4600",
            ha="center", va="bottom")
    ax.text(-approach / 2.0, 0.30, "walk-in  \u2192  2-yd run-up  \u2192  "
            "accelerate over the approach", fontsize=9.4, color="#333333",
            ha="center", va="bottom")

    # reps
    sequence = "  ".join(["L", "R"] * REPS_PER_SIDE)
    ax.text(-approach + 0.25, 3.55,
            f"{REPS_TOTAL} reps total\n{REPS_PER_SIDE} left / {REPS_PER_SIDE} right, "
            f"alternating\n{sequence}\n~{DURATION_S} s per player",
            fontsize=9.8, color=COLOURS["ink"], ha="left", va="top",
            linespacing=1.4,
            bbox=dict(boxstyle="round,pad=0.42", facecolor="#F2F2F2",
                      edgecolor="#8C8C8C", linewidth=0.8))

    # a true scale bar: equal aspect, yards on both axes
    x0 = -approach - 0.6
    ax.plot([x0, x0 + SCALE_BAR_YD], [scale_y, scale_y], color=COLOURS["ink"],
            lw=2.6, solid_capstyle="butt")
    for edge in (x0, x0 + SCALE_BAR_YD):
        ax.plot([edge, edge], [scale_y - 0.14, scale_y + 0.14],
                color=COLOURS["ink"], lw=1.2)
    ax.text(x0 + SCALE_BAR_YD / 2.0, scale_y + 0.22, f"{SCALE_BAR_YD:.0f} yd",
            fontsize=9.2, color=COLOURS["ink"], ha="center", va="bottom")

    fig.text(0.022, 0.975, TITLE, fontsize=16.5, fontweight="bold",
             color=COLOURS["ink"], va="top")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor="white", metadata={"Software": "bdb27-minimal"})
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_DIR))
    args = p.parse_args(argv)
    out = Path(args.out) / "fig_drill_schematic.png"
    draw(out)
    print(f"the drill: {APPROACH_YD:.0f} yd approach, one plant, two {BREAK_DEG:.0f}\u00b0 "
          f"exits to gates {GATE_YD:.0f} yd from the cone, {REPS_TOTAL} reps "
          f"({REPS_PER_SIDE} each side), ~{DURATION_S} s per player")
    print("this drawing carries no measurement: every length and angle is the "
          "drill's stated geometry, not data")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
