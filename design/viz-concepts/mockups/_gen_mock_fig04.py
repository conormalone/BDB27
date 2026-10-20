"""
_gen_mock_fig04.py — MOCK-UP GENERATOR (throwaway, not pipeline code).

Figure 04 — "Where the Gap Lives".
Left: how close a player's nth-percentile effort gets to his own ceiling.
Right: how often any effort beats the ceiling at all.
"""

import numpy as np
import matplotlib.pyplot as plt

import _style as S

S.apply_style()
rng = np.random.default_rng(31)

pct = np.array([50, 60, 70, 80, 85, 90, 95, 97, 99])
x = np.arange(len(pct))

def curve(base, top, k=2.6):
    u = (pct - pct[0]) / (pct[-1] - pct[0])
    return base + (top - base) * (1 - np.exp(-k * u)) / (1 - np.exp(-k))

med = {"moved_down": curve(79.0, 94.6), "high_secure": curve(78.4, 94.1)}
iqr_half = np.linspace(7.6, 3.4, len(pct))

fig = S.fig_new(9.6, 5.6)
S.title_block(
    fig,
    "The gap is a distribution, not a number",
    "Even a player's best efforts sit below his ceiling; the tier contrast sits inside the spread between players.",
    top=0.955,
)

gs = fig.add_gridspec(1, 2, width_ratios=[1.25, 1], left=0.080, right=0.972,
                      bottom=0.190, top=0.720, wspace=0.26)

# ---------------------------------------------------------------- left: fan
ax = fig.add_subplot(gs[0, 0])
S.clean_axes(ax, grid="y")
ax.set_xlim(-0.4, len(pct) - 0.6)
ax.set_ylim(66, 104)
ax.set_xticks(x)
ax.set_xticklabels([f"{p}" for p in pct])
ax.set_yticks([70, 80, 90, 100])
ax.set_xlabel("effort percentile within player")
ax.set_ylabel("speed as % of own ceiling")

ax.axhline(100, color=S.INK, lw=1.0, ls=(0, (4, 2.5)), zorder=2)
ax.text(len(pct) - 0.75, 100.9, "Combine ceiling", fontsize=7.3, color=S.INK,
        ha="right", va="bottom")

# The truncated axis must state its range and its reason in the figure itself.
ax.text(0.0, 1.040, "axis starts at 66% \u2014 the ceiling line at 100% is the reference",
        transform=ax.transAxes, fontsize=7.2, color=S.MUTED, va="bottom")

for g in ["high_secure", "moved_down"]:
    c = S.GROUP_COLOR[g]
    m = med[g]
    ax.fill_between(x, m - iqr_half, m + iqr_half, color=c, alpha=0.10, lw=0)
    ax.fill_between(x, m - 1.8, m + 1.8, color=c, alpha=0.18, lw=0)
    ax.plot(x, m, color=c, lw=2.2, zorder=5, marker="o", ms=3.4,
            mec="white", mew=0.8)
    ax.text(x[-1] + 0.12, m[-1] + (1.15 if g == "moved_down" else -1.15),
            f"{m[-1]:.1f}%", color=c, fontsize=7.6,
            family=S.MONO, va="center", ha="left", fontweight="bold")

ax.set_xlim(-0.4, len(pct) - 0.05)
ax.annotate("between-player spread\n(IQR)", xy=(4.9, med["moved_down"][4] + 5.4),
            xytext=(6.35, 69.2), fontsize=7.0, color=S.MUTED, linespacing=1.35,
            ha="left", va="bottom",
            arrowprops=dict(arrowstyle="-", color=S.FAINT, lw=0.7,
                            connectionstyle="arc3,rad=0.22"))

S.stat(ax, 0.035, 0.925, "median at 99th pct", "94.3%", transform=ax.transAxes)
S.stat(ax, 0.035, 0.775, "in yd/s, at a 21.4 ceiling", "\u22121.2", transform=ax.transAxes)

# ------------------------------------------------------------ right: exceed
ax2 = fig.add_subplot(gs[0, 1])
S.clean_axes(ax2, grid="y")
ax2.set_xlim(-0.4, len(pct) - 0.6)
ax2.set_ylim(0, 9)
ax2.set_xticks(x)
ax2.set_xticklabels([f"{p}" for p in pct])
ax2.set_yticks([0, 2, 4, 6, 8])
ax2.set_xlabel("effort percentile within player")
ax2.set_ylabel("P(effort > ceiling)  (%)")

ex = {"moved_down": np.array([0.3, 0.5, 0.9, 1.6, 2.3, 3.4, 5.1, 6.2, 7.4]),
      "high_secure": np.array([0.3, 0.5, 0.8, 1.5, 2.2, 3.2, 4.8, 5.9, 7.0])}
for g in ["high_secure", "moved_down"]:
    c = S.GROUP_COLOR[g]
    yv = ex[g]
    ax2.fill_between(x, np.maximum(yv - 0.55, 0), yv + 0.55, color=c, alpha=0.14, lw=0)
    ax2.plot(x, yv, color=c, lw=2.2, marker="o", ms=3.4, mec="white", mew=0.8,
             zorder=5)

ax2.axhline(0, color="#B5B5AF", lw=0.8)
ax2.text(0.0, 1.04, "Ceiling is beaten, but rarely", transform=ax2.transAxes,
         fontsize=8.2, color=S.INK, fontweight="bold", va="bottom")

# legend
handles = [plt.Line2D([], [], color=S.DOWN, lw=2.2, label="moved_down"),
           plt.Line2D([], [], color=S.SECURE, lw=2.2, label="high_secure")]
fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.080, 0.020),
           ncol=2, handlelength=1.8, columnspacing=1.6, handletextpad=0.5,
           fontsize=7.6)

S.caption(fig, "Synthetic placeholder. n = 111 WR. Percentiles are within-player, so players are compared fairly.\n"
               "Left: y = % of own ceiling; axis starts at 66% so the 100% reference stays in view. "
               "Bands: 95% Wilson intervals.",
          x=0.40, y=0.048, size=7.0)

S.watermark(fig)
S.save(fig, "fig04_ceiling_fan.png")
