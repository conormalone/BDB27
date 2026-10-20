"""
_gen_mock_fig01.py — MOCK-UP GENERATOR (throwaway, not pipeline code).

Figure 01 — "The Ceiling Line".
Four route efforts; each game speed trace sits under the same player's
shrunken Combine envelope. Synthetic geometry, invented numbers.
"""

import numpy as np
import matplotlib.pyplot as plt

import _style as S

S.apply_style()

rng = np.random.default_rng(7)

PLAYERS = [
    dict(tag="a", pid="WR-014", group="moved_down", route="post, 18 yd",
         vmax=21.4, tau=0.86, peak=19.62, t_peak=2.55, util=0.917, break_t=2.75),
    dict(tag="b", pid="WR-027", group="moved_down", route="go, 24 yd",
         vmax=22.1, tau=0.92, peak=20.04, t_peak=2.95, util=0.907, break_t=None),
    dict(tag="c", pid="WR-061", group="high_secure", route="dig, 14 yd",
         vmax=20.8, tau=0.80, peak=18.41, t_peak=2.15, util=0.885, break_t=2.35),
    dict(tag="d", pid="WR-089", group="high_secure", route="corner, 16 yd",
         vmax=21.9, tau=0.95, peak=19.35, t_peak=2.70, util=0.884, break_t=None),
]

fig = S.fig_new(9.6, 6.0)
S.title_block(
    fig,
    "Most of the Combine ceiling never reaches the field",
    "One route effort per player, against that player's own shrunken Combine envelope \u2014 a reference sprint, not a game outcome.",    top=0.958,
)

gs = fig.add_gridspec(2, 2, left=0.085, right=0.973, bottom=0.200, top=0.742,
                      wspace=0.175, hspace=0.70)

t = np.linspace(0, 4.2, 260)

for i, p in enumerate(PLAYERS):
    ax = fig.add_subplot(gs[i // 2, i % 2])
    S.clean_axes(ax, grid="y")
    ax.set_xlim(0, 4.2)
    ax.set_ylim(0, 24.5)
    ax.set_xticks([0, 1, 2, 3, 4])
    ax.set_yticks([0, 5, 10, 15, 20])
    if i % 2 == 0:
        ax.set_ylabel("speed (yd/s)")

    gcol = S.GROUP_COLOR[p["group"]]

    # --- Combine envelope (shrunken) -------------------------------------
    ceil = p["vmax"] * (1 - np.exp(-t / p["tau"]))
    ax.fill_between(t, ceil - 0.38, ceil + 0.38, color="#000000", alpha=0.055,
                    lw=0, zorder=2)
    ax.plot(t, ceil, color=S.INK, lw=1.25, ls=(0, (4.2, 2.2)), zorder=3)

    # --- this effort ------------------------------------------------------
    v = p["vmax"] * (0.965 * (1 - np.exp(-t / (p["tau"] * 0.92))))
    v = v + rng.normal(0, 0.055, t.shape)
    if p["break_t"] is not None:
        v = v * (1 - 0.085 / (1 + np.exp(-(t - p["break_t"]) / 0.16)))
    v = np.minimum(v, p["peak"] + 0.10)
    # force the stated peak
    k = np.argmin(np.abs(t - p["t_peak"]))
    v[k] = p["peak"]

    ax.fill_between(t, v, ceil, where=v < ceil, color=gcol, alpha=0.10, lw=0,
                    zorder=1)
    ax.plot(t, v, color=gcol, lw=2.0, zorder=5, solid_capstyle="round")

    # other efforts (context: this is not a cherry-picked trace)
    for j in range(2):
        vg = v * (0.955 + 0.022 * j) + rng.normal(0, 0.09, t.shape)
        ax.plot(t, vg, color=gcol, lw=0.9, alpha=0.28, zorder=4)

    # peak + gap
    ax.plot([p["t_peak"]], [p["peak"]], "o", ms=5.0, mfc=gcol, mec="white",
            mew=1.2, zorder=8)
    gap = float(np.interp(p["t_peak"], t, ceil) - p["peak"])
    xa = p["t_peak"] + 0.10
    ax.annotate("", xy=(xa, p["peak"]), xytext=(xa, np.interp(p["t_peak"], t, ceil)),
                arrowprops=dict(arrowstyle="<->", color=S.MUTED, lw=0.8,
                                shrinkA=1.5, shrinkB=1.5), zorder=7)
    ax.text(xa + 0.09, (p["peak"] + np.interp(p["t_peak"], t, ceil)) / 2,
            f"{gap:.1f}", fontsize=7.6, color=S.MUTED, va="center", ha="left",
            family=S.MONO)
    ax.text(p["t_peak"] - 0.10, p["peak"] - 1.55, f"peak {p['peak']:.1f}",
            fontsize=7.4, color=S.INK_2, va="top", ha="right")

    # panel header
    S.group_tag(ax, p["group"], x=0.0, y=1.10)
    ax.text(0.0, 1.20, f"{p['pid']}  \u00b7  {p['route']}", transform=ax.transAxes,
            fontsize=8.8, color=S.INK, fontweight="bold", va="bottom")
    ax.text(1.0, 1.20, f"top-k / ceiling  {p['util']*100:.0f}%", transform=ax.transAxes,
            fontsize=7.4, color=S.MUTED, va="bottom", ha="right", family=S.MONO)

    if i == 0:
        ax.annotate("shortfall\n(shaded)", xy=(1.75, (p["peak"] + 20.2) / 2 + 0.1),
                    xytext=(0.55, 11.4), fontsize=7.2, color=S.MUTED,
                    ha="left", va="center", linespacing=1.35,
                    arrowprops=dict(arrowstyle="-", color=S.FAINT, lw=0.7,
                                    connectionstyle="arc3,rad=-0.18"))

# shared axis labels
fig.text(0.029, 0.470, "speed (yd/s)", rotation=90, fontsize=8.6,
         color=S.INK_2, va="center", ha="center")
fig.text(0.529, 0.128, "time since snap (s)", fontsize=8.6, color=S.INK_2,
         va="center", ha="center")

# legend, bottom-left
handles = [
    plt.Line2D([], [], color=S.INK, lw=1.25, ls=(0, (4.2, 2.2)),
               label="shrunken Combine envelope (\u00b11 SE)"),
    plt.Line2D([], [], color=S.DOWN, lw=2.0, label="this effort, moved_down"),
    plt.Line2D([], [], color=S.SECURE, lw=2.0, label="this effort, high_secure"),
    plt.Line2D([], [], color=S.MUTED, lw=0.9, alpha=0.5, label="his other efforts"),
]
fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.082, 0.028),
           ncol=4, handlelength=1.9, columnspacing=1.5, handletextpad=0.5,
           fontsize=7.4)

S.caption(fig, "n = 111 WR (placeholder). Each panel is one route effort chosen by a fixed rule. "
               "Colour = tier; line = one player.",
          y=0.012, size=7.2)

S.watermark(fig)
S.save(fig, "fig01_ceiling_line.png")
