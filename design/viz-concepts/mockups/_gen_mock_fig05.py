"""
_gen_mock_fig05.py — MOCK-UP GENERATOR (throwaway, not pipeline code).

Figure 05 — "Route Tree, Gap by Branch".
Eight route families as small field multiples; beneath each, the tier
difference in ceiling utilisation with a 95% interval.
"""

import numpy as np
import matplotlib.pyplot as plt

import _style as S

S.apply_style()
rng = np.random.default_rng(41)

FAMS = [
    ("go", 24), ("post", 31), ("corner", 18), ("dig", 27),
    ("out", 22), ("cross", 19), ("slant", 26), ("screen", 14),
]
# placeholder deltas in percentage points; only "go" shows the positive encoding
DELTA = {"go": 3.9, "post": 0.3, "corner": -0.4, "dig": 0.1,
         "out": -0.2, "cross": 0.4, "slant": 0.0, "screen": -0.1}
CI = {"go": (0.4, 7.4), "post": (-2.6, 3.2), "corner": (-3.4, 2.6),
      "dig": (-2.9, 3.1), "out": (-3.1, 2.7), "cross": (-2.4, 3.2),
      "slant": (-2.8, 2.8), "screen": (-4.1, 3.9)}

fig = S.fig_new(9.6, 6.4)
S.title_block(
    fig,
    "If the tier contrast is real, it should live where top speed is reachable",
    "Tested within route family, so role mix cannot fake the contrast. Glyph = one family; bar = moved_down minus high_secure.",
    top=0.958,
)

gs = fig.add_gridspec(2, 4, left=0.045, right=0.978, bottom=0.155, top=0.700,
                      wspace=0.10, hspace=0.34)

for idx, (fam, n) in enumerate(FAMS):
    i, j = divmod(idx, 4)
    sub = gs[i, j].subgridspec(2, 1, height_ratios=[3.1, 1.0], hspace=0.10)

    ax = fig.add_subplot(sub[0])
    xs, ys, depth = S.route_path(fam, seed=idx + 5)
    S.field_axes(ax, y0=-2.0, y1=30.0, lw_los=1.1, ticks=False)
    ax.plot(xs, ys, color="#FFFFFF", lw=5.0, alpha=0.9, zorder=3,
            solid_capstyle="round")
    ax.plot(xs, ys, color=S.INK_2, lw=2.0, zorder=4, solid_capstyle="round")
    ax.plot([xs[0]], [ys[0]], "o", ms=3.4, mfc="white", mec=S.INK, mew=0.8,
            zorder=6)
    # break marker
    k = int(np.argmax(np.abs(np.diff(xs, prepend=xs[0]))))
    if fam not in ("go", "slant", "screen"):
        ax.plot([xs[k]], [ys[k]], "o", ms=4.6, mfc="white", mec=S.INK, mew=0.9,
                zorder=6)

    ax.text(0.0, 1.08, fam, transform=ax.transAxes, fontsize=9.0, color=S.INK,
            fontweight="bold", ha="left", va="bottom")
    ax.text(1.0, 1.08, f"n = {n}", transform=ax.transAxes, fontsize=7.0,
            color=S.MUTED, ha="right", va="bottom", family=S.MONO)

    # ------------------------------------------------------------- delta bar
    bx = fig.add_subplot(sub[1])
    bx.set_xlim(-8.5, 8.5)
    bx.set_ylim(-1.05, 1.05)
    bx.axvline(0, color="#B5B5AF", lw=0.8, zorder=1)
    d, (lo, hi) = DELTA[fam], CI[fam]
    excl = (lo > 0) or (hi < 0)
    c = S.AMBER if excl else "#9C9C94"
    bx.plot([lo, hi], [0, 0], color=c, lw=1.4, alpha=0.9, zorder=3,
            solid_capstyle="butt")
    bx.plot([lo, lo], [-0.22, 0.22], color=c, lw=1.0, zorder=3)
    bx.plot([hi, hi], [-0.22, 0.22], color=c, lw=1.0, zorder=3)
    bx.plot([0, d], [0, 0], color=c, lw=3.4, zorder=4, solid_capstyle="butt")
    bx.plot([d], [0], "o", ms=3.4, color=c, zorder=5)
    bx.set_yticks([])
    bx.set_xticks([-6, 0, 6])
    bx.set_xticklabels(["\u22126", "0", "+6"], fontsize=6.6, color=S.MUTED)
    for s in ("top", "right", "left"):
        bx.spines[s].set_visible(False)
    bx.spines["bottom"].set_color("#D6D6D0")
    bx.spines["bottom"].set_linewidth(0.7)
    bx.tick_params(length=2.0, width=0.6, pad=2, colors=S.MUTED)
    if j == 0:
        bx.set_ylabel("\u0394 utilisation\n(pp)", fontsize=6.8, color=S.MUTED,
                      labelpad=2)
    if i == 1:
        bx.set_xlabel("\u0394 ceiling utilisation (pp)", fontsize=6.9,
                      color=S.MUTED, labelpad=3)

    if fam == "go":
        pass

handles = [plt.Line2D([], [], color="#9C9C94", lw=3.4,
                      label="95% CI spans zero \u2014 no detectable difference"),
           plt.Line2D([], [], color=S.AMBER, lw=3.4,
                      label="95% CI excludes zero (illustrative)")]
fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.045, 0.030),
           ncol=2, handlelength=1.8, columnspacing=1.8, handletextpad=0.5,
           fontsize=7.4)

S.caption(fig, "Placeholder geometry and intervals. \u0394 = mean share of ceiling reached, moved_down minus "
               "high_secure. Invented, not findings.",
          x=0.045, y=0.010, size=7.0)

S.watermark(fig)
S.save(fig, "fig05_route_tree.png")
