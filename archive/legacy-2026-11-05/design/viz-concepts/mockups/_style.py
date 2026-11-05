"""
_style.py — MOCK-UP GENERATOR SUPPORT. NOT PIPELINE CODE.

Shared house style for the BDB27 "Visualisation Concept Pack".
Every mock-up imports this so the pack reads as one entry.

No data access, no network, no pipeline imports. Synthetic values only.
"""

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path
from matplotlib.patches import PathPatch, Rectangle
import matplotlib.patheffects as pe

# ----------------------------------------------------------------------------
# Palette — semantic, colourblind-safe, redundant with shape/label everywhere.
# ----------------------------------------------------------------------------
INK       = "#141414"   # primary text, reference lines
INK_2     = "#3D3D3D"
MUTED     = "#6B6B6B"   # secondary text
FAINT     = "#9A9A9A"
RULE      = "#D6D6D0"   # hairlines
GRID      = "#E6E6E1"
PAPER     = "#FFFFFF"
PANEL_BG  = "#F7F7F4"
TURF      = "#EDF1E6"
TURF_LINE = "#FFFFFF"

DOWN      = "#C4553B"   # moved_down  (stock slipping >= +3 EDP spots)
SECURE    = "#2F5D7C"   # high_secure (stable or rising)
AMBER     = "#D9922B"   # single accent: exceedance / "outside the noise"

GROUP_COLOR = {"moved_down": DOWN, "high_secure": SECURE}
GROUP_LABEL = {"moved_down": "MOVED DOWN", "high_secure": "HIGH / SECURE"}
GROUP_NOTE  = {"moved_down": "EDP trend \u2265 +3.0 spots",
               "high_secure": "EDP trend < +3.0 spots"}

MONO = "DejaVu Sans Mono"
SANS = "DejaVu Sans"

WATERMARK = "MOCK \u2014 synthetic placeholder, not results"

FIGW, FIGH = 9.6, 5.4      # 1920 x 1080 px at 200 dpi
DPI = 200


def apply_style():
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [SANS],
        "font.monospace": [MONO],
        "font.size": 8.2,
        "axes.titlesize": 9.0,
        "axes.labelsize": 8.6,
        "axes.labelcolor": INK_2,
        "axes.edgecolor": "#B5B5AF",
        "axes.linewidth": 0.8,
        "axes.facecolor": PAPER,
        "figure.facecolor": PAPER,
        "savefig.facecolor": PAPER,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 8.0,
        "ytick.labelsize": 8.0,
        "xtick.major.width": 0.7,
        "ytick.major.width": 0.7,
        "xtick.major.size": 3.0,
        "ytick.major.size": 3.0,
        "legend.frameon": False,
        "legend.fontsize": 8.0,
        "lines.solid_capstyle": "round",
        "lines.dash_capstyle": "round",
        "text.color": INK,
        "axes.unicode_minus": True,
    })


def fig_new(w=FIGW, h=FIGH):
    fig = plt.figure(figsize=(w, h), dpi=DPI)
    fig.patch.set_facecolor(PAPER)
    return fig


def title_block(fig, title, subtitle=None, kicker=None, top=0.955,
                x0=0.072, x1=0.972, rule=True):
    """Editorial header: kicker, title, deck, hairline."""
    if kicker is None:
        kicker = "BDB27  \u00b7  VISUALISATION CONCEPT  \u00b7  MOCK"
    fig.text(x0, top, kicker, fontsize=7.2, color=MUTED, va="top",
             fontweight="normal")
    fig.text(x0, top - 0.042, title, fontsize=15.6, color=INK, va="top",
             fontweight="bold")
    if subtitle:
        fig.text(x0, top - 0.107, subtitle, fontsize=9.3, color=INK_2,
                 va="top", linespacing=1.45)
    if rule:
        y = top - (0.155 if subtitle and "\n" in subtitle else
                   (0.132 if subtitle else 0.085))
        fig.add_artist(plt.Line2D([x0, x1], [y, y], color=RULE, lw=0.9,
                                  transform=fig.transFigure))
        return y - 0.012
    return top - 0.06


def watermark(fig, dense=True):
    """Diagonal faint bands + corner stamp. Survives a screenshot crop."""
    spots = [(0.16, 0.28), (0.52, 0.20), (0.34, 0.55), (0.72, 0.46),
             (0.13, 0.72), (0.60, 0.74), (0.88, 0.30)]
    if not dense:
        spots = spots[:3]
    for (x, y) in spots:
        fig.text(x, y, WATERMARK, rotation=21, fontsize=17.5, color="#000000",
                 alpha=0.062, ha="center", va="center", zorder=900)
    # Corner stamp: the exact warning string, full opacity, bordered, bottom-right.
    # This is the first thing a screenshot reviewer reads; the diagonal bands stay
    # faint (alpha 0.062) so they do not compete with the data.
    fig.text(0.972, 0.014, WATERMARK,
             fontsize=8.0, family=MONO, color=INK, alpha=1.0,
             ha="right", va="bottom", zorder=950,
             bbox=dict(boxstyle="round,pad=0.34", fc="#FFFFFF", ec=INK_2,
                       lw=0.9, alpha=1.0))


def clean_axes(ax, grid="y", spines=("left", "bottom")):
    for s in ("top", "right", "left", "bottom"):
        ax.spines[s].set_visible(False)
    for s in spines:
        ax.spines[s].set_visible(True)
        ax.spines[s].set_color("#B5B5AF")
        ax.spines[s].set_linewidth(0.8)
    ax.tick_params(length=3, width=0.7, pad=3, colors=MUTED)
    if grid == "y":
        ax.yaxis.grid(True, color=GRID, lw=0.7)
    elif grid == "x":
        ax.xaxis.grid(True, color=GRID, lw=0.7)
    elif grid == "both":
        ax.grid(True, color=GRID, lw=0.7)
    ax.set_axisbelow(True)


def panel_label(ax, s, x=-0.005, y=1.055):
    ax.text(x, y, s, transform=ax.transAxes, fontsize=8.2,
            fontweight="bold", color=FAINT, va="bottom", ha="left")


def group_tag(ax, group, x=0.0, y=1.045, size=7.4):
    """Small-caps-ish group label in the group colour (redundant encoding)."""
    ax.text(x, y, " ".join(GROUP_LABEL[group]), transform=ax.transAxes,
            fontsize=size, color=GROUP_COLOR[group], fontweight="bold",
            va="bottom", ha="left")


def stat(fig_or_ax, x, y, label, value, transform=None, size=8.6,
         value_size=11.0, color=INK, ha="left"):
    """Tabular stat card: tiny label above a mono number."""
    t = transform if transform is not None else fig_or_ax.transAxes
    fig_or_ax.text(x, y, label, transform=t, fontsize=6.9, color=MUTED,
                   va="bottom", ha=ha)
    fig_or_ax.text(x, y - 0.008, value, transform=t, fontsize=value_size,
                   family=MONO, color=color, va="top", ha=ha, fontweight="bold")


def caption(fig, text, x=0.072, y=0.042, size=7.6, color=MUTED, width=None):
    fig.text(x, y, text, fontsize=size, color=color, va="bottom", ha="left",
             linespacing=1.5)


def _overlap_report(fig):
    """Dev-only: flag text boxes that collide (layout QA, not pipeline)."""
    import itertools
    import os
    if not os.environ.get("VIZ_CHECK"):
        return
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    items = []
    for t in fig.findobj(matplotlib.text.Text):
        txt = t.get_text().strip()
        if not txt or "MOCK" in txt or not t.get_visible():
            continue
        a = t.get_alpha()
        if a is not None and a < 0.45:
            continue
        try:
            bb = matplotlib.text.Text.get_window_extent(t, renderer=r)
        except Exception:
            try:
                bb = t.get_window_extent(renderer=r)
            except Exception:
                continue
        if bb.width <= 0 or bb.height <= 0:
            continue
        items.append((txt, bb))
    bad = 0
    for (s1, b1), (s2, b2) in itertools.combinations(items, 2):
        if not b1.overlaps(b2):
            continue
        ox = min(b1.x1, b2.x1) - max(b1.x0, b2.x0)
        oy = min(b1.y1, b2.y1) - max(b1.y0, b2.y0)
        inter = max(ox, 0) * max(oy, 0)
        small = min(b1.width * b1.height, b2.width * b2.height)
        if small > 0 and inter / small > 0.22:
            bad += 1
            print(f"  OVERLAP {inter/small:.0%}: {s1[:38]!r} <-> {s2[:38]!r}")
    print(f"  text-collision check: {bad} flagged of {len(items)} items")


def save(fig, path):
    _overlap_report(fig)
    fig.savefig(path, dpi=DPI, facecolor=PAPER, edgecolor="none")
    plt.close(fig)
    print("wrote", path)


# ----------------------------------------------------------------------------
# Synthetic football geometry helpers (plausible, invented, never results)
# ----------------------------------------------------------------------------

FIELD_W = 53.3          # yards, sideline to sideline
HASH_L = 23.58
HASH_R = 29.72


def field_axes(ax, y0=-3.0, y1=30.0, lw_los=1.4, ticks=True):
    """Half-field view looking downfield. x = across, y = yards from LOS."""
    ax.set_facecolor(TURF)
    ax.set_xlim(0, FIELD_W)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(True)
        s.set_color("#DCE2D2")
        s.set_linewidth(0.8)
    # yard lines
    yy = 0.0
    while yy <= y1:
        ax.plot([0, FIELD_W], [yy, yy], color=TURF_LINE, lw=1.0, zorder=0)
        if ticks and yy > 0:
            ax.text(1.6, yy + 0.5, str(int(yy)), fontsize=5.6, color="#B9C2AE",
                    ha="left", va="bottom", zorder=0)
            ax.text(FIELD_W - 1.6, yy + 0.5, str(int(yy)), fontsize=5.6,
                    color="#B9C2AE", ha="right", va="bottom", zorder=0)
        yy += 5.0
    # hash marks
    for hx in (HASH_L, HASH_R):
        yy = 1.0
        while yy < y1:
            ax.plot([hx - 0.28, hx + 0.28], [yy, yy], color=TURF_LINE, lw=0.9,
                    zorder=0)
            yy += 1.0
    ax.plot([0, FIELD_W], [0, 0], color="#FFFFFF", lw=lw_los, zorder=1)
    ax.text(0.7, 0.35, "LOS", fontsize=5.4, color="#B9C2AE", ha="left",
            va="bottom", zorder=1)


def route_path(kind, x0=26.6, seed=0):
    """Return (xs, ys) polyline for a route family, with a soft break."""
    rng = np.random.default_rng(seed)
    if kind == "go":
        depth, lat = 22.0, rng.choice([-1.0, 0.6, 1.4])
        t = np.linspace(0, 1, 90)
        ys = depth * t
        xs = x0 + lat * t + 0.7 * np.sin(np.pi * t) * rng.choice([-1, 1])
        return xs, ys, depth
    if kind == "post":
        stem, cut = 11.0, 15.0
        t = np.linspace(0, 1, 40)
        xs1, ys1 = np.full_like(t, x0), stem * t
        u = np.linspace(0, 1, 55)
        xs2 = x0 - cut * 0.55 * u
        ys2 = stem + cut * 0.72 * u
        return np.r_[xs1, xs2[1:]], np.r_[ys1, ys2[1:]], stem + cut * 0.72
    if kind == "corner":
        stem, cut = 12.0, 14.0
        t = np.linspace(0, 1, 40)
        xs1, ys1 = np.full_like(t, x0), stem * t
        u = np.linspace(0, 1, 55)
        xs2 = x0 + cut * 0.45 * u
        ys2 = stem + cut * 0.70 * u
        return np.r_[xs1, xs2[1:]], np.r_[ys1, ys2[1:]], stem + cut * 0.70
    if kind == "dig":
        stem, cut = 12.0, 13.0
        t = np.linspace(0, 1, 40)
        xs1, ys1 = np.full_like(t, x0 + 4.0), stem * t
        u = np.linspace(0, 1, 55)
        xs2 = (x0 + 4.0) - cut * u
        ys2 = np.full_like(u, stem)
        return np.r_[xs1, xs2[1:]], np.r_[ys1, ys2[1:]], stem
    if kind == "out":
        stem, cut = 9.0, 7.0
        t = np.linspace(0, 1, 35)
        xs1, ys1 = np.full_like(t, x0 - 6.0), stem * t
        u = np.linspace(0, 1, 35)
        xs2 = (x0 - 6.0) + cut * u
        ys2 = np.full_like(u, stem)
        return np.r_[xs1, xs2[1:]], np.r_[ys1, ys2[1:]], stem
    if kind == "cross":
        stem, cut = 6.0, 16.0
        t = np.linspace(0, 1, 30)
        xs1, ys1 = np.full_like(t, x0 + 9.0), stem * t
        u = np.linspace(0, 1, 60)
        xs2 = (x0 + 9.0) - cut * u
        ys2 = stem + 2.0 * u
        return np.r_[xs1, xs2[1:]], np.r_[ys1, ys2[1:]], stem + 2.0
    if kind == "slant":
        t = np.linspace(0, 1, 60)
        xs = x0 + 1.0 - 5.0 * t
        ys = 7.0 * t
        return xs, ys, 7.0
    if kind == "screen":
        t = np.linspace(0, 1, 45)
        xs = x0 + 2.0 + 6.0 * t
        ys = 0.6 * np.sin(np.pi * t) + 1.2 * t
        return xs, ys, 1.2
    raise ValueError(kind)


def speed_along(ys_or_t, v_peak, tau=0.55, decay=0.10, break_frac=None,
                rng=None, noise=0.0):
    """Speed profile along a route: rise, optional post-break decay."""
    t = np.asarray(ys_or_t, dtype=float)
    tnorm = (t - t.min()) / max(t.max() - t.min(), 1e-9)
    v = v_peak * (0.55 + 0.45 * (1 - np.exp(-tnorm / tau)))
    if break_frac is not None:
        v = v * (1 - decay / (1 + np.exp(-(tnorm - break_frac) / 0.06)))
    if rng is not None and noise:
        v = v + rng.normal(0, noise, v.shape)
    return v


def speed_cmap():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list(
        "chip_grey", ["#DCDCD4", "#A8A8A0", "#6E6E67", "#3A3A35", "#171714"])


def route_ribbon(ax, xs, ys, speeds, cmap, lw=3.0, vmin=0, vmax=24,
                 halo=True, zorder=4):
    from matplotlib.collections import LineCollection
    pts = np.array([xs, ys]).T.reshape(-1, 1, 2)
    segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
    lc = LineCollection(segs, cmap=cmap, array=(speeds[:-1] + speeds[1:]) / 2,
                        norm=plt.Normalize(vmin, vmax), linewidth=lw,
                        capstyle="round", joinstyle="round", zorder=zorder)
    if halo:
        ax.plot(xs, ys, color="#FFFFFF", lw=lw + 2.4, alpha=0.85,
                solid_capstyle="round", zorder=zorder - 1)
    ax.add_collection(lc)
    return lc
