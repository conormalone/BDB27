"""
_gen_mock_fig06.py — MOCK-UP GENERATOR (throwaway, not pipeline code).

Figure 06 — "The Honest Null".
An equivalence-aware difference plot plus the smallest effect this design
could have detected. Built to be the figure when the result is null.
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

import _style as S

S.apply_style()

EST, LO, HI = 0.0, -2.4, 2.4
MARGIN = 3.0
SE = (HI - LO) / (2 * 1.96)

fig = S.fig_new(9.6, 5.6)
S.title_block(
    fig,
    "A null here is a result, not a shrug",
    "Inside the equivalence margin, the claim is \u201cno detectable difference\u201d; the right panel shows what this design could see.",
    top=0.955,
)

gs = fig.add_gridspec(1, 2, width_ratios=[1.45, 1.0], left=0.070, right=0.972,
                      bottom=0.245, top=0.700, wspace=0.24)

# ------------------------------------------------------- left: difference ruler
ax = fig.add_subplot(gs[0, 0])
S.clean_axes(ax, grid=None, spines=("bottom",))
ax.set_xlim(-8.4, 8.4)
ax.set_ylim(0, 1)
ax.set_yticks([])
ax.set_xticks([-8, -6, -4, -2, 0, 2, 4, 6, 8])
ax.set_xlabel("\u0394 ceiling utilisation, moved_down \u2212 high_secure (percentage points)")

# equivalence band
ax.add_patch(plt.Rectangle((-MARGIN, 0), 2 * MARGIN, 1.0, facecolor="#F0F0EB",
                           edgecolor="none", zorder=0))
for xv in (-MARGIN, MARGIN):
    ax.plot([xv, xv], [0, 1.0], color="#C9C9C1", lw=0.9, ls=(0, (3, 2.4)),
            zorder=1)
ax.text(0, 0.955, "pre-declared equivalence margin  \u00b13 pp", fontsize=7.0,
        color=S.MUTED, ha="center", va="top")

# posterior density
xx = np.linspace(-3.2, 3.2, 300)
dens = np.exp(-0.5 * ((xx - EST) / SE) ** 2)
ax.fill_between(xx, 0.30, 0.30 + dens * 0.52, color=S.AMBER, alpha=0.16, lw=0)
ax.plot(xx, 0.30 + dens * 0.52, color=S.AMBER, lw=1.3, zorder=4)

# CI bar
ax.plot([LO, HI], [0.155, 0.155], color=S.INK, lw=5.0, alpha=0.20,
        solid_capstyle="butt", zorder=5)
for xv in (LO, HI):
    ax.plot([xv, xv], [0.115, 0.195], color=S.INK, lw=1.1, zorder=6)
ax.plot([EST], [0.155], "o", ms=7.5, color=S.AMBER, mec=S.INK, mew=0.9, zorder=7)

ax.annotate(f"\u0394 = {EST:+.1f} pp\n95% CI  {LO:+.1f} to {HI:+.1f}",
            xy=(EST + 0.18, 0.155), xytext=(3.9, 0.52), fontsize=8.0,
            color=S.INK, ha="left", va="center", linespacing=1.6,
            family=S.MONO, zorder=9,
            arrowprops=dict(arrowstyle="-", color=S.MUTED, lw=0.7,
                            connectionstyle="arc3,rad=0.15"))

ax.text(8.2, 0.045, "\u2192 moved_down closer to its ceiling", fontsize=6.9,
        color=S.MUTED, ha="right", va="bottom")
ax.text(-8.2, 0.045, "high_secure closer \u2190", fontsize=6.9, color=S.MUTED,
        ha="left", va="bottom")

# stat cards
S.stat(ax, 0.0, -0.28, "n WR (placeholder)", "111", transform=ax.transAxes)
S.stat(ax, 0.30, -0.28, "n moved_down / secure", "38 / 73", transform=ax.transAxes)
S.stat(ax, 0.68, -0.28, "equivalence verdict", "inside", transform=ax.transAxes,
       value_size=10.0)

# ------------------------------------------------------------- right: MDE curve
ax2 = fig.add_subplot(gs[0, 1])
S.clean_axes(ax2, grid="y")
ax2.set_xlim(0, 9)
ax2.set_ylim(0, 1.02)
ax2.set_xticks([0, 3, 6, 9])
ax2.set_yticks([0, 0.5, 0.8, 1.0])
ax2.set_yticklabels(["0", "50%", "80%", "100%"])
ax2.set_xlabel("true difference (percentage points)")
ax2.set_ylabel("chance the 95% CI excludes zero")

d = np.linspace(0, 9, 400)
from math import erf, sqrt
Phi = np.vectorize(lambda z: 0.5 * (1 + erf(z / sqrt(2))))
power = Phi(d / SE - 1.96)
MDE = 2.802 * SE

ax2.fill_between(d, 0, power, where=d <= MDE, color="#EFEFEA", lw=0)
ax2.plot(d, power, color=S.INK, lw=1.8, zorder=4)
ax2.axhline(0.8, color=S.FAINT, lw=0.8, ls=(0, (3, 2.4)), zorder=2)
ax2.axvline(MDE, color=S.AMBER, lw=1.2, ls=(0, (3, 2.2)), zorder=3)
ax2.plot([MDE], [0.8], "o", ms=5.0, color=S.AMBER, mec="white", mew=1.0, zorder=6)

ax2.text(MDE + 0.22, 0.845, f"smallest detectable\neffect {MDE:.1f} pp",
         fontsize=7.4, color=S.INK, ha="left", va="bottom", linespacing=1.4)
ax2.text(0.25, 0.10, "below this line the design\ncannot separate the tiers",
         fontsize=6.9, color=S.MUTED, ha="left", va="bottom", linespacing=1.4)
ax2.text(0.0, 1.04, "What this null rules out", transform=ax2.transAxes,
         fontsize=8.4, color=S.INK, fontweight="bold", va="bottom")

S.caption(fig, "Synthetic placeholders for the null case. Equivalence margin and MDE are pre-declared, "
               "not chosen post hoc.",
          x=0.070, y=0.022, size=7.2)

S.watermark(fig)
S.save(fig, "fig06_honest_null.png")
