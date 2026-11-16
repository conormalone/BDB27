"""Band-reliability figures (D207).

Two figures, both about the same artefact: the 30–60° DB band ICC(1) in
``outputs/band_reliability/reliability.json`` (D206) and the three independent
routes to the same answer in ``outputs/fallback_validation/reliability.json``
(D203). Spec: ``notes/spec-reliability-figures.md``.

Division of labour:

* **loaders** are the only input I/O in this module, and they fail by naming the
  path (``FileNotFoundError``) or the file and every missing key/column
  (``KeyError``). Nothing else raises;
* **builders** (``build_*``) are pure: artefact in, a dict of rows, marks and
  manifest numbers out. Every printed number is computed from the artefact;
  none is hard-coded;
* **drawers** (``draw_*``) return a matplotlib ``Figure`` and touch no file;
* ``render_figure`` writes the PNG, CSV, ``.provenance.json`` and
  ``.drawn_text.json``; ``merge_manifest`` adds the single entry.

House rules honoured here (spec §0): DB band only, no player identity in a CSV
or a drawn string, no per-player ranking, grey context and one accent, counts
and denominators on the marks, no threshold or ``VOID`` line, no outcome
language, and no on-image provenance banner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")  # deterministic, headless

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from bdb27 import common, figures
from bdb27.figure_text_scan import BANNED_TOKENS, figure_texts
from bdb27.geometric_match import file_sha256

STAGE = "06_figures"

FIG_THIRD = "fig_third_of_one_percent"
FIG_ROUTES = "fig_three_routes"
FIGURE_KEYS: tuple[str, ...] = (FIG_THIRD, FIG_ROUTES)

#: One accent, grey context (spec §0.4).
GREY = "#B9BEC4"
ACCENT = "#1F4E79"
ACCENT_ALPHA = 0.85
PANEL = "#EBEBEB"
BORDER = "#B3B3B3"
DPI = 150

#: Drawn words that would break the spec's rules: the retired gate label, and
#: the outcome vocabulary TASK.md bans (spec §0.6, §0.8). Checked on every drawn
#: string at render time, case-insensitively. This is a text rule, not a data
#: gate, so nothing is skipped or clipped on a count.
BANNED_DRAW_WORDS: tuple[str, ...] = (
    "VOID", "stable", "separation", "pressure", "production", "predicts",
)

#: The four scenarios, in the artefact's own order, with the plain-words row
#: labels figure 1 draws (spec §5.1).
SCENARIO_LABELS: dict[str, str] = {
    "all_band": "all band turns",
    "entry_3_5": "3–5 yd/s entry",
    "entry_3_5_pass": "3–5 yd/s on pass plays",
    "pass_only": "pass plays only",
}
ADJUSTED_LABELS: dict[str, str] = {
    "all_band": "all band turns, residualised",
    "entry_3_5": "3–5 yd/s entry, residualised",
}

#: Row status literals. "not estimable" is drawn, never raised (spec §0.7).
STATUS_OK = "ok"
STATUS_NOT_ESTIMABLE = "not estimable"
STATUS_ADJUSTED = "not drawn (adjusted)"


# ------------------------------------------------------------------ loaders
def _load_json(path: str | Path, required: Sequence[str], *,
               what: str, build_with: str) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{what} not found: {path} — build it with {build_with}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # unreadable, and it says so
        raise ValueError(f"{what} at {path} is not valid JSON: {exc}") from exc
    missing = [key for key in required if key not in doc]
    if missing:
        raise KeyError(
            f"{what} at {path} is missing required key(s) {missing}; "
            f"found keys: {sorted(doc)}")
    return doc


def load_band_reliability(path: str | Path) -> dict:
    """The D206 artefact: four scenarios, two adjusted rows, derived block."""
    return _load_json(
        path, ("scenarios", "adjustment", "derived", "sample"),
        what="band-reliability artefact",
        build_with=".venv/bin/python scripts/band_reliability.py --mode full "
                   "(writes outputs/band_reliability/reliability.json)")


def load_fallback_reliability(path: str | Path) -> dict:
    """The D203 artefact: ICC(1) with intervals, split-half, cross-season."""
    return _load_json(
        path,
        ("icc1", "split_half", "season_2024_2025", "season_2024_2025_min10"),
        what="fallback-validation artefact",
        build_with=".venv/bin/python scripts/fallback_validation.py --mode full "
                   "(writes outputs/fallback_validation/reliability.json)")


def load_turns_per_player(path: str | Path) -> pd.DataFrame:
    """Per-player exposure (spec §3.2 artefact); ids live here, never in a figure."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"per-player exposure not found: {path} — build it with "
            ".venv/bin/python scripts/band_turns_per_player.py --mode full")
    frame = pd.read_csv(path)
    required = ("nfl_id", "scenario", "n_turns", "mean_apex_retention",
                "sd_apex_retention")
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise KeyError(
            f"per-player exposure at {path} is missing required column(s) "
            f"{missing}; found columns: {list(frame.columns)}")
    return frame


# --------------------------------------------------------------- formatting
def format_share(share: float | None) -> str:
    """A variance share as the percent printed on the mark (2 dp)."""
    return "not estimable" if share is None else f"{share * 100.0:.2f}%"


def format_count(value: Any) -> str:
    """A count with thousands separators; never rounded to a share."""
    return "n/a" if value is None else f"{int(value):,}"


def format_kbar(value: float | None) -> str:
    return "n/a" if value is None else f"{float(value):.1f}"


def scenario_mark(row: dict) -> str:
    """``0.34%  ·  7,777 T / 113 P  ·  k̄ 68.8`` — share, turns, players, k̄."""
    head = format_share(row["between_share"])
    return (f"{head}  ·  {format_count(row['turns'])} T / "
            f"{format_count(row['players'])} P  ·  k̄ {format_kbar(row['kbar'])}")


# --------------------------------------------------------------- builders
def _scenario_rows(doc: dict) -> list[dict]:
    """The four scenario rows, in the artefact's own order."""
    rows: list[dict] = []
    for scenario in doc["scenarios"]:
        name = scenario["name"]
        s2a = scenario.get("sigma2_a")
        s2e = scenario.get("sigma2_e")
        icc = scenario.get("icc")
        total = (s2a or 0.0) + (s2e or 0.0)
        estimable = (s2a is not None and s2e is not None and icc is not None
                     and total > 0)
        row = {
            "scenario": name,
            "rule_label": SCENARIO_LABELS.get(name, name),
            "players": None if scenario.get("players") is None
            else int(scenario["players"]),
            "turns": None if scenario.get("turns") is None
            else int(scenario["turns"]),
            "kbar": None if scenario.get("kbar") is None
            else float(scenario["kbar"]),
            "icc": icc,
            "sigma2_a": s2a,
            "sigma2_e": s2e,
            "between_share": (s2a / total) if estimable else None,
            "status": STATUS_OK if estimable else STATUS_NOT_ESTIMABLE,
        }
        row["mark"] = scenario_mark(row)
        rows.append(row)
    return rows


def _adjusted_rows(doc: dict) -> list[dict]:
    """The two adjusted ICCs — CSV and manifest text only, never a bar."""
    rows: list[dict] = []
    for name, block in doc["adjustment"].items():
        s2a, s2e, icc = (block.get("sigma2_a"), block.get("sigma2_e"),
                         block.get("icc"))
        total = (s2a or 0.0) + (s2e or 0.0)
        estimable = (s2a is not None and s2e is not None and icc is not None
                     and total > 0)
        rows.append({
            "scenario": f"{name}_adjusted",
            "rule_label": ADJUSTED_LABELS.get(name, f"{name}, residualised"),
            "players": None if block.get("players") is None
            else int(block["players"]),
            "turns": None if block.get("turns") is None else int(block["turns"]),
            "kbar": None,
            "icc": icc,
            "sigma2_a": s2a,
            "sigma2_e": s2e,
            "between_share": (s2a / total) if estimable else None,
            "status": STATUS_ADJUSTED,
            "r_squared": block.get("r_squared"),
            "covariates": list(block.get("covariates", [])),
        })
    return rows


CSV_COLUMNS: tuple[str, ...] = (
    "scenario", "rule_label", "players", "turns", "kbar", "icc",
    "sigma2_a", "sigma2_e", "between_share", "status",
)


def build_third_of_one_percent(doc: dict) -> dict:
    """Figure 1's rows, marks and manifest numbers. Pure: artefact in, dict out."""
    rows = _scenario_rows(doc)
    adjusted = _adjusted_rows(doc)
    missing = [name for name in SCENARIO_LABELS if name not in
               [r["scenario"] for r in rows]]
    if missing:
        raise KeyError(
            "band-reliability artefact is missing scenario(s) "
            f"{missing}; found: {[r['scenario'] for r in rows]}")

    frame = pd.DataFrame(rows + adjusted, columns=list(CSV_COLUMNS))
    whole = next(r for r in rows if r["scenario"] == "all_band")
    return {
        "figure": FIG_THIRD,
        "title": ("A third of one percent is the player: share of total "
                  "variance in the 30–60° DB band"),
        "claim": (
            "Across the whole 30–60° DB band the between-player share of total "
            "variance in apex_retention is "
            f"{format_share(whole['between_share'])} "
            f"(ICC {whole['icc']:.4f} on {format_count(whole['turns'])} turns, "
            f"{format_count(whole['players'])} players): "
            f"{format_share(1.0 - whole['between_share'])} of the variation is "
            "turn to turn, so a player mean carries almost no player-level "
            "information."),
        "rows": rows,
        "adjusted_rows": adjusted,
        "csv_frame": frame,
        "csv_columns": list(CSV_COLUMNS),
        "annotation": "the accent segment is the ICC, drawn to scale.",
        "marks": [r["mark"] for r in rows],
        "numbers": {
            "rule": "between_share = sigma2_a / (sigma2_a + sigma2_e) = icc",
            "scenarios": [{k: r[k] for k in
                           ("scenario", "rule_label", "players", "turns",
                            "kbar", "icc", "sigma2_a", "sigma2_e",
                            "between_share", "status", "mark")}
                          for r in rows],
            "annotation": "the accent segment is the ICC, drawn to scale.",
        },
        "caveats": _THIRD_CAVEATS,
        "adjusted_not_drawn": {
            r["scenario"]: {
                "icc": r["icc"],
                "sigma2_a": r["sigma2_a"],
                "sigma2_e": r["sigma2_e"],
                "between_share": r["between_share"],
                "players": r["players"],
                "turns": r["turns"],
                "r_squared": r["r_squared"],
                "covariates": r["covariates"],
                "note": ("adjusted rows are residualised on entry_speed and "
                         "abs_turn_angle; they are CSV and manifest text only "
                         "and are never a fifth bar"),
            } for r in adjusted},
    }


# ---------------------------------------------------------------- drawers
def _style_axes(ax) -> None:
    ax.set_facecolor(PANEL)
    for spine in ax.spines.values():
        spine.set_color(BORDER)


def draw_third_of_one_percent(built: dict) -> plt.Figure:
    """Four 100%-stacked bars at true scale, marks in a right-hand column."""
    rows = built["rows"]
    n = len(rows)
    fig, ax = plt.subplots(figsize=(11.6, 3.6), dpi=DPI)
    fig.patch.set_facecolor("white")
    _style_axes(ax)

    ys = np.arange(n)[::-1].astype(float)  # artefact order runs top to bottom
    for y, row in zip(ys, rows):
        total = (row["sigma2_a"] or 0.0) + (row["sigma2_e"] or 0.0)
        if row["status"] == STATUS_OK and total > 0:
            grey_w = 100.0 * row["sigma2_e"] / total
            accent_w = 100.0 * row["sigma2_a"] / total
        else:  # not estimable: full-width grey bar, and the mark says so
            grey_w, accent_w = 100.0, 0.0
        ax.barh(y, grey_w, height=0.52, color=GREY, edgecolor=BORDER,
                linewidth=0.6, zorder=2)
        if accent_w > 0:
            ax.barh(y, accent_w, left=grey_w, height=0.52, color=ACCENT,
                    alpha=ACCENT_ALPHA, linewidth=0.0, zorder=3)
        ax.text(1.012, y, row["mark"], transform=ax.get_yaxis_transform(),
                va="center", ha="left", fontsize=9.0, family="monospace",
                color="#222222")

    ax.set_yticks(ys)
    ax.set_yticklabels([r["rule_label"] for r in rows], fontsize=10.5)
    ax.set_xlim(0.0, 100.0)
    ax.set_ylim(-0.65, n - 0.05)
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("share of total variance", fontsize=10)
    # The heading is a figure-level text so the drawn-text dump can see it
    # (``ax.get_title()`` exposes only the centred title).
    fig.text(0.012, 0.955, built["title"], fontsize=12.5, fontweight="bold",
             ha="left", va="top")
    ax.text(0.4, n - 0.55, built["annotation"], ha="left", va="bottom",
            fontsize=9.0, style="italic", color="#333333")
    fig.subplots_adjust(left=0.235, right=0.985, top=0.78, bottom=0.19)
    return fig


DRAWERS: dict[str, Callable[[dict], plt.Figure]] = {
    FIG_THIRD: draw_third_of_one_percent,
}

#: Figure 1's honesty clauses — the failure modes the menu (its §2 item 1) names.
_THIRD_CAVEATS: tuple[str, ...] = (
    "At true scale the accent segment is about two pixels on a 600 px bar. "
    "That invisibility is the finding, and it is the weaker of the two honest "
    "readings: the share is tiny and its interval (D203 whole-band player "
    "bootstrap, [-0.0012, 0.008]) includes zero. Both readings support the "
    "same conclusion.",
    "No zoom inset: a zoom would turn a hairline into a feature and invert the "
    "message.",
    "The four rows sit at different turns per player (k̄ is printed on every "
    "row: 68.8, 30.0, 20.7, 46.8), so no ratio between rows is computed or "
    "drawn; only the share within a row is read.",
    "The two adjusted ICCs are CSV and manifest text only, never a fifth bar: "
    "they residualise retention on entry speed and turn angle and are a "
    "different quantity from the four scenario rows.",
    "sigma2 is in retention² units; the axis is a share of total variance, "
    "never a value.",
    "No threshold line and no retired gate label is drawn anywhere: the "
    "figure carries no pass mark.",
    "Exploratory: not one of the TASK.md report figures; in_report is false.",
)


# ----------------------------------------------------------------- text dump
def visible_strings(fig) -> list[str]:
    """Visible drawn strings only, by the figure-1 rule (D185)."""
    from bdb27.full_run import _visible_strings

    return _visible_strings(fig)


def check_drawn_text(texts: Iterable[str]) -> None:
    """Refuse a canvas carrying a hedging token or a banned word."""
    hits = [f"{token!r} in {text!r}" for text in texts
            for token in (*BANNED_TOKENS, *BANNED_DRAW_WORDS)
            if token.lower() in text.lower()]
    if hits:
        raise common.StageRefused(
            f"{STAGE}: drawn text breaks the figure rules: {hits}")


# ------------------------------------------------------------------- render
def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).absolute().relative_to(root))
    except ValueError:  # pragma: no cover - only for paths outside the repo
        return str(Path(path).absolute())


def source_record(path: str | Path, role: str, root: Path) -> dict:
    """One ``source_artefacts`` record: path, sha256, role."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"source artefact not found: {path} — {role}")
    return {"path": _rel(path, root), "sha256": file_sha256(path), "role": role}


def manifest_entry(built: dict, *, png: Path, csv: Path, prov: Path,
                   drawn_text: Path, root: Path, texts: Sequence[str]) -> dict:
    """The single manifest entry for one figure (spec §4)."""
    entry: dict[str, Any] = {
        "stage": STAGE,
        "artefact_version": figures.ARTEFACT_VERSION,
        "figure": built["figure"],
        "exploratory": True,
        "in_report": False,
        "title": built["title"],
        "claim": built["claim"],
        "png": _rel(png, root),
        "csv": _rel(csv, root),
        "provenance": common.PROVENANCE_FULL,
        "stamp": figures.stamp_text("full"),
        "sources": [r["path"] for r in built.get("source_artefacts", [])],
        "source_artefacts": list(built.get("source_artefacts", [])),
        "caveats": list(built.get("caveats", [])),
        "rendered_text": _rel(drawn_text, root),
        "provenance_json": _rel(prov, root),
        # the two rules a reader can check without opening the image (spec §6.9)
        "gates_drawn": False,
        "ranked_players": False,
        "numbers": built.get("numbers", {}),
        "drawn_marks": list(built.get("marks", [])),
    }
    if "adjusted_not_drawn" in built:
        entry["adjusted_not_drawn"] = built["adjusted_not_drawn"]
    entry["drawn_strings"] = len(texts)
    return entry


def render_figure(key: str, built: dict, out_dir: str | Path, cfg,
                  *, root: str | Path | None = None) -> dict:
    """Write the four artefacts for one figure. No manifest merge here."""
    root = Path(root or Path(__file__).resolve().parents[2])
    outdir = Path(out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    png = outdir / f"{key}.png"
    csv = outdir / f"{key}.csv"
    prov = outdir / f"{key}.provenance.json"
    drawn = outdir / f"{key}.drawn_text.json"

    common.write_csv(built["csv_frame"], csv, "full", cfg,
                     extra={"figure": key,
                            "stamp": figures.stamp_text("full")})

    fig = DRAWERS[key](built)
    try:
        texts = figure_texts(fig)
        scan = visible_strings(fig)
        check_drawn_text(scan)
        if built["title"] not in texts:
            raise common.StageRefused(
                f"{STAGE}: the rendered heading of {key} is not in the drawn "
                "text dump")
        fig.savefig(png, dpi=DPI, facecolor="white",
                    metadata={"Software": "bdb27-figures"})
    finally:
        plt.close(fig)

    entry = manifest_entry(built, png=png, csv=csv, prov=prov,
                           drawn_text=drawn, root=root, texts=scan)

    record = {
        "figure": key,
        "title": built["title"],
        "claim": built["claim"],
        "source_artefacts": entry["source_artefacts"],
        "numbers": entry["numbers"],
        "adjusted_not_drawn": entry.get("adjusted_not_drawn"),
        "manifest_provenance": common.PROVENANCE_FULL,
        "outputs": {"png": _rel(png, root), "csv": _rel(csv, root),
                    "drawn_text": _rel(drawn, root),
                    "manifest": _rel(outdir / "figures_manifest.json", root)},
        "git_rev": figures.git_rev() if hasattr(figures, "git_rev") else None,
        "config_sha": common.config_fingerprint(cfg),
    }
    common.write_json(record, prov, "full", cfg)

    # No timestamp here on purpose: this dump must be byte-identical across two
    # runs of the same inputs (spec §6 pin 10), so ``common.write_json`` (which
    # stamps ``generated_utc``) is not used.
    drawn.parent.mkdir(parents=True, exist_ok=True)
    drawn.write_text(json.dumps({
        "provenance": common.PROVENANCE_FULL,
        "label": common.stamp("full"),
        "config_sha": common.config_fingerprint(cfg),
        "figure": key,
        "stamp": figures.stamp_text("full"),
        "strings": scan,
        "scan_strings": texts,
        "banned_tokens": list(BANNED_TOKENS),
        "banned_words": list(BANNED_DRAW_WORDS),
        "banned_hits": [],
    }, indent=2, default=str) + "\n", encoding="utf-8")

    return {"png": png, "csv": csv, "provenance": prov, "drawn_text": drawn,
            "entry": entry, "texts": texts}


def merge_manifest(entry: dict, path: str | Path, cfg) -> dict:
    """Add exactly one entry, leaving every other entry untouched."""
    path = Path(path)
    existing: dict = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    doc = dict(existing)
    figs = dict(doc.get("figures", {}))
    figs[entry["figure"]] = entry
    doc["figures"] = figs
    doc["stage"] = STAGE
    doc["artefact_version"] = figures.ARTEFACT_VERSION
    n_full = sum(1 for v in figs.values()
                 if str(v.get("stamp", "")).startswith("FULL"))
    n_schem = sum(1 for v in figs.values()
                  if str(v.get("stamp", "")).startswith("SCHEMATIC"))
    n_sample = len(figs) - n_full - n_schem
    label = (f"MIXED PROVENANCE — {n_full} FULL, {n_sample} sample, "
             f"{n_schem} schematic")
    doc["stamp"] = label
    doc["label"] = label
    doc["config_sha"] = common.config_fingerprint(cfg)
    doc.pop("generated_utc", None)
    common.write_json(doc, path, "full", cfg)
    return doc
