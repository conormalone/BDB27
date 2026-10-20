"""
_gen_mock_fig02.py — MOCK-UP GENERATOR (throwaway, not pipeline code).

Figure 02 — "Route Replay, Speed-Painted".
Half-field views; colour is instantaneous chip speed along one route.
Group is carried by panel position and typography, never by colour.
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

import _style as S

S.apply_style()
rng = np.random.default_rng(11)

PANELS = [
    ("moved_down", "go",   "vertical stem, no break",      21.6, 20.9),
    ("moved_down", "dig",  "12 yd stem, break across",     21.2, 20.1),
    ("high_secure", "go",  "vertical stem, no break",      21.9, 20.4),
    ("high_secure", "dig", "12 yd stem, break across",     21.4, 19.6),
]

FIGW, FIGH = 9.6, 7.4
fig = S.fig_new(FIGW, FIGH)
S.title_block(
    fig,
    "The ceiling is approached in one place, and only for a moment",
    "Colour is chip speed along a single route. The peak sits at the top of the stem or just before the break \u2014 never for long.",
    top=0.962, x0=0.075, x1=0.925,
)

PW, PH = 0.375, 0.375 * FIGW / 1.615 / FIGH
X0, Y0 = 0.0975, 0.128
GAPX, GAPY = 0.055, 0.052
cmap = S.speed_cmap()
norm = Normalize(9, 22)

for i, (grp, kind, desc, vpeak, peak_lab) in enumerate(PANELS):
    col, row = i % 2, i // 2
    ax = fig.add_axes([X0 + col * (PW + GAPX),
                       Y0 + (1 - row) * (PH + GAPY), PW, PH])
    xs, ys, depth = S.route_path(kind, seed=i + 3)
    v = S.speed_along(ys, vpeak, tau=0.52,
                      break_frac=(0.42 if kind == "dig" else None),
                      decay=0.13, rng=rng, noise=0.18)
    S.field_axes(ax, y0=-2.0, y1=31.0)

    S.route_ribbon(ax, xs, ys, v, cmap, lw=3.4, vmin=9, vmax=22)
    ax.plot([xs[0]], [ys[0]], "o", ms=5, mfc="white", mec=S.INK, mew=1.0, zorder=9)

    k = int(np.argmax(v))
    ax.plot([xs[k]], [ys[k]], "o", ms=8.2, mfc="none", mec=S.INK, mew=1.25,
            zorder=10)
    ax.annotate(f"peak {peak_lab:.1f}", xy=(xs[k], ys[k]),
                xytext=((xs[k] - 8.5, ys[k] - 5.0) if kind == "dig"
                        else (xs[k] + 7.5, ys[k] + 3.0)),
                fontsize=7.3, color=S.INK,
                ha=("right" if kind == "dig" else "left"), va="bottom", zorder=11,
                arrowprops=dict(arrowstyle="-", color=S.INK_2, lw=0.7,
                                shrinkA=2, shrinkB=4))

    if kind == "dig":
        ib = int(np.argmin(np.abs(ys - 12.0)))
        ax.plot([xs[ib] - 1.4, xs[ib] + 1.4], [12.0, 12.0], color="white",
                lw=2.2, zorder=8)
        ax.text(xs[ib] + 1.9, 12.0, "break", fontsize=6.4, color="#7C8574",
                va="center", ha="left", zorder=8)

    fy = Y0 + (1 - row) * (PH + GAPY) + PH
    fig.text(X0 + col * (PW + GAPX), fy + 0.030, f"{kind}  \u00b7  {desc}",
             fontsize=8.9, color=S.INK, fontweight="bold", va="bottom")
    fig.text(X0 + col * (PW + GAPX), fy + 0.006,
             (" ".join(S.GROUP_LABEL[grp])), fontsize=7.2,
             color=S.GROUP_COLOR[grp], fontweight="bold", va="bottom")

# row labels, rotated in the left margin
fig.text(0.052, Y0 + PH + GAPY + PH / 2, "MOVED DOWN", rotation=90, fontsize=8.0,
         color=S.DOWN, fontweight="bold", va="center", ha="center")
fig.text(0.052, Y0 + PH / 2, "HIGH / SECURE", rotation=90, fontsize=8.0,
         color=S.SECURE, fontweight="bold", va="center", ha="center")

# colourbar
cax = fig.add_axes([0.355, 0.096, 0.29, 0.013])
cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=cax,
                  orientation="horizontal")
cb.set_label("instantaneous speed (yd/s)", fontsize=7.6, color=S.INK_2,
             labelpad=4)
cb.outline.set_visible(False)
cb.ax.tick_params(labelsize=7.2, length=2.5, width=0.6, pad=2.5, colors=S.MUTED)

S.caption(fig, "Each panel = one route effort (placeholder geometry). Colour = speed; tier = panel "
               "position. Scale 9\u201322 yd/s.",
          x=0.075, y=0.012, size=7.2)

S.watermark(fig)
S.save(fig, "fig02_route_replay.png")
