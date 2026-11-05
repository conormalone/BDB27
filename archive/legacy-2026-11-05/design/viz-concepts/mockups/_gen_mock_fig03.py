"""
_gen_mock_fig03.py — MOCK-UP GENERATOR (throwaway, not pipeline code).

Figure 03 — "Every Player Loses Speed".
Sorted dumbbell: Combine ceiling -> in-game top-k mean, one line per player.
The gap is the visual object, not a correlation.

The hero panel is zero-based (0 -> 23.2 yd/s) so the connecting line length is
the gap at true scale. A separate, explicitly labelled zoom panel
("zoom: 18-23 yd/s") exists because 111 rows at full scale are hard to read;
the zoom is never silently applied to the hero panel.
"""

import numpy as np
import matplotlib.pyplot as plt

import _style as S

S.apply_style()
rng = np.random.default_rng(23)

N = 111
n_down = 38
groups = np.array(["moved_down"] * n_down + ["high_secure"] * (N - n_down))

ceiling = rng.normal(21.35, 0.52, N)
gap = rng.normal(1.72, 0.46, N)
# deliberately flat, obviously-placeholder group difference
gap[groups == "moved_down"] += 0.02
game = ceiling - gap

order = np.argsort(-gap)
ceiling, game, gap, groups = ceiling[order], game[order], gap[order], groups[order]
ypos = np.arange(N)[::-1]

fig = S.fig_new(9.6, 6.6)
S.title_block(
    fig,
    "Every receiver gives up speed between the Combine and the field",
    "The question is not whether a gap exists \u2014 it is who gives up less. Sorted by gap; group difference deliberately near zero in this mock.",
    top=0.958,
)

gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 0.48, 0.24], left=0.088, right=0.965,
                      bottom=0.190, top=0.735, wspace=0.10)
ax = fig.add_subplot(gs[0, 0])          # hero, zero-based
axz = fig.add_subplot(gs[0, 1], sharey=ax)   # labelled zoom
ax2 = fig.add_subplot(gs[0, 2])         # group means

ax.set_ylim(-1.5, N + 0.5)


def dumbbells(a):
    for i in range(N):
        c = S.GROUP_COLOR[groups[i]]
        a.plot([game[i], ceiling[i]], [ypos[i], ypos[i]], color=c, lw=1.0,
               alpha=0.42, zorder=2, solid_capstyle="butt")
    a.scatter(game, ypos, s=9, c=[S.GROUP_COLOR[g] for g in groups], lw=0, zorder=4)
    a.scatter(ceiling, ypos, s=11, facecolors="white", edgecolors="#4A4A4A",
              linewidths=0.75, zorder=5)


# ------------------------------------------------------------ hero: zero-based
S.clean_axes(ax, grid="x", spines=("bottom",))
ax.set_xlim(0, 23.2)
ax.set_yticks([])
ax.set_xticks([0, 5, 10, 15, 20])
ax.set_xlabel("speed (yd/s)")
dumbbells(ax)

ax.text(0.0, 1.035, "zero-based \u2014 line length is the gap at true scale",
        transform=ax.transAxes, fontsize=7.3, color=S.MUTED, va="bottom")
ax.text(0.0, -0.105, "players, sorted by gap (largest at top)", transform=ax.transAxes,
        fontsize=7.6, color=S.INK_2, va="top")
ax.text(0.0, -0.152, "hollow = Combine ceiling   \u00b7   filled = game top-k mean",
        transform=ax.transAxes, fontsize=7.2, color=S.MUTED, va="top")

# ------------------------------------------------------------ zoom (labelled)
S.clean_axes(axz, grid="x", spines=("bottom",))
axz.set_xlim(18.0, 23.0)
axz.set_yticks([])
axz.set_xticks([18, 19, 20, 21, 22, 23])
axz.set_xlabel("speed (yd/s)")
dumbbells(axz)

axz.text(0.0, 1.035, "zoom: 18\u201323 yd/s (not the hero scale)",
         transform=axz.transAxes, fontsize=7.3, color=S.MUTED, va="bottom")

# median-gap bracket, drawn on the zoom where the gap is legible
med = np.argsort(gap)[N // 2]
axz.annotate("", xy=(game[med], ypos[med]), xytext=(ceiling[med], ypos[med]),
             arrowprops=dict(arrowstyle="<->", color=S.INK, lw=1.0, shrinkA=0,
                             shrinkB=0), zorder=8)
axz.annotate("median gap\n1.7 yd/s", xy=(ceiling[med], ypos[med] + 1.0),
             xytext=(22.92, ypos[med] + 20), fontsize=7.4, color=S.INK,
             ha="right", va="bottom", linespacing=1.35, zorder=9,
             arrowprops=dict(arrowstyle="-", color=S.MUTED, lw=0.7,
                             connectionstyle="arc3,rad=0.2"))

# ------------------------------------------------------------- side panel
S.clean_axes(ax2, grid="x", spines=("bottom",))
ax2.set_xlim(0, 3.4)
ax2.set_ylim(-1.5, N + 0.5)
ax2.set_yticks([])
ax2.set_xticks([0, 1, 2, 3])
ax2.set_xlabel("gap (yd/s)")

means = {"moved_down": gap[groups == "moved_down"].mean(),
         "high_secure": gap[groups == "high_secure"].mean()}
ses = {"moved_down": gap[groups == "moved_down"].std(ddof=1) / np.sqrt(n_down),
       "high_secure": gap[groups == "high_secure"].std(ddof=1) / np.sqrt(N - n_down)}

for j, g in enumerate(["moved_down", "high_secure"]):
    yy = N - 12 - j * 22
    ax2.plot([0, 3.4], [yy, yy], color=S.GRID, lw=0.8, zorder=1)
    ax2.errorbar(means[g], yy, xerr=1.96 * ses[g], fmt="o", ms=6.5,
                 color=S.GROUP_COLOR[g], ecolor=S.GROUP_COLOR[g], elinewidth=1.6,
                 capsize=2.5, capthick=1.0, zorder=5)
    ax2.text(means[g], yy + 5.0, f"{means[g]:.2f}", fontsize=7.2, family=S.MONO,
             color=S.GROUP_COLOR[g], ha="center", va="bottom")
ax2.text(0.0, 1.035, "group mean \u00b1 95% CI", transform=ax2.transAxes,
         fontsize=7.3, color=S.MUTED, va="bottom")
ax2.text(0.0, -0.105, "difference\n+0.02 yd/s", transform=ax2.transAxes,
         fontsize=6.8, family=S.MONO, color=S.INK_2, va="top", linespacing=1.3)

S.caption(fig, "n = 111 WR placeholder (38 moved_down / 73 high_secure). Ceiling = shrunken Combine envelope;\n"
               "game = top-k mean over a fixed number of route snaps. Values synthetic.",
          y=0.030, size=7.2)

S.watermark(fig)
S.save(fig, "fig03_speed_dumbbell.png")
