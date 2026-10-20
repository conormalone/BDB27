"""
_gen_contact_sheet.py — MOCK-UP GENERATOR (throwaway, not pipeline code).

Tiles the six mock-ups with their titles so the pack can be judged at a glance.
"""

import matplotlib.pyplot as plt
import matplotlib.image as mpimg

import _style as S

S.apply_style()

ITEMS = [
    ("fig01_ceiling_line.png",  "01  The Ceiling Line",
     "Four efforts against their own Combine envelope; the shortfall is the object."),
    ("fig02_route_replay.png",  "02  Route Replay, Speed-Painted",
     "Chip speed along real route geometry: where the ceiling is nearly reached."),
    ("fig03_speed_dumbbell.png", "03  Every Player Loses Speed",
     "Sorted ceiling-to-game gap, one line per player; group difference near zero."),
    ("fig04_ceiling_fan.png",   "04  Where the Gap Lives",
     "Utilisation by within-player effort percentile, and how rarely the ceiling is beaten."),
    ("fig05_route_tree.png",    "05  Route Tree, Gap by Branch",
     "Eight route families tested separately, so role mix cannot fake the contrast."),
    ("fig06_honest_null.png",   "06  The Honest Null",
     "An equivalence-aware difference plot plus the smallest detectable effect."),
]

FIGW, FIGH = 9.6, 11.9
fig = S.fig_new(FIGW, FIGH)
fig.patch.set_facecolor(S.PAPER)

MARGIN = 0.36
GAP = 0.42
COLS = 2
CELL_W = (FIGW - 2 * MARGIN - GAP) / COLS

# header
fig.text(MARGIN / FIGW, 1 - 0.30 / FIGH, "BDB27  \u00b7  VISUALISATION CONCEPT PACK  \u00b7  MOCK",
         fontsize=7.2, color=S.MUTED, va="top")
fig.text(MARGIN / FIGW, 1 - 0.46 / FIGH, "Six mock-ups, one entry",
         fontsize=17.0, color=S.INK, va="top", fontweight="bold")
fig.text(MARGIN / FIGW, 1 - 0.74 / FIGH,
         "Same palette, same type scale, same margins. Placeholder data throughout \u2014 nothing here is a result.",
         fontsize=9.0, color=S.INK_2, va="top")
fig.add_artist(plt.Line2D([MARGIN / FIGW, 1 - MARGIN / FIGW],
                          [1 - 0.92 / FIGH, 1 - 0.92 / FIGH],
                          color=S.RULE, lw=0.9, transform=fig.transFigure))

y_in = 1.02  # inches from top, after header

for row in range(3):
    batch = ITEMS[row * COLS:(row + 1) * COLS]
    heights = []
    for fn, _, _ in batch:
        im = mpimg.imread(fn)
        heights.append(CELL_W / (im.shape[1] / im.shape[0]))
    row_h = max(heights)

    for col, (fn, title, claim) in enumerate(batch):
        im = mpimg.imread(fn)
        h = heights[col]
        x_in = MARGIN + col * (CELL_W + GAP)
        top_in = y_in + 0.30
        fig.text(x_in / FIGW, 1 - top_in / FIGH + 0.14 / FIGH, title,
                 fontsize=9.8, color=S.INK, fontweight="bold", va="bottom")
        ax = fig.add_axes([x_in / FIGW, 1 - (top_in + h) / FIGH,
                           CELL_W / FIGW, h / FIGH])
        ax.imshow(im, interpolation="lanczos")
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(True)
            s.set_color("#E4E4DE")
            s.set_linewidth(0.8)
        fig.text(x_in / FIGW, 1 - (top_in + h + 0.06) / FIGH, claim,
                 fontsize=7.6, color=S.MUTED, va="top")

    y_in += 0.30 + row_h + 0.34

fig.text(MARGIN / FIGW, 0.030, "Full-size files: mockups/*.png  \u00b7  generators: mockups/_gen_mock_fig0*.py  \u00b7  "
                               "all values synthetic",
         fontsize=7.2, color=S.MUTED, va="bottom")

S.watermark(fig, dense=True)
S.save(fig, "00_contact_sheet.png")
