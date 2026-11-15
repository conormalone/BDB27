#!/usr/bin/env python
"""Geometric attribution end to end — the same pipeline the notebook runs.

Specification: ``notes/geometric-attribution-spec.md``.

    python scripts/geometric_attribution.py \
        --turns data/interim/full_run_20261111/bdb27_full_db_turns.parquet \
        --player-play data/raw/player_play.csv \
        --tracking data/raw/game_tracking_2023.csv data/raw/game_tracking_2024.csv \
        --out-dir outputs/geometric_attribution

Exit codes: 0 = ran to completion (including a legitimate "no estimate" outcome);
2 = a pre-stated stop condition fired; 1 = an unexpected error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bdb27 import geometric_match as gm  # noqa: E402
from bdb27 import outcome_test  # noqa: E402

BAND_LO, BAND_HI = 30.0, 60.0


def _log(msg: str) -> None:
    print(msg, flush=True)


def _progress_printer() -> object:
    state = {"t": time.time()}

    def report(ev: dict) -> None:
        now = time.time()
        if ev.get("reused"):
            _log(f"  reused shard: {Path(ev['path']).name} "
                 f"({ev.get('rows_kept', 0):,} rows kept earlier)")
            return
        # One line per ~5% of the file, plus the first and last chunk.
        if now - state["t"] < 2.0 and ev.get("file_frac", 0) < 1.0:
            return
        state["t"] = now
        frac = ev.get("file_frac") or 0.0
        eta = ev.get("eta_s")
        eta_txt = f" ETA {eta:,.0f}s" if eta else ""
        _log(f"  {Path(ev['path']).name}: {frac:5.1%} of file "
             f"({ev['rows_read']:,} rows read, {ev['rows_kept']:,} kept){eta_txt}")
    return report


def load_band(turns_path: Path) -> pd.DataFrame:
    """The band definition lives in the module; the script does not restate it."""
    return gm.load_band(turns_path, band=(BAND_LO, BAND_HI))


def resolve_tracking(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in args:
        p = Path(raw).expanduser()
        if p.is_dir():
            # Auto-discovery never picks a file whose name says it is a sample;
            # an explicitly named file is always taken as given.
            paths.extend(sorted(q for q in p.glob("*.csv")
                                if "sample" not in q.name.lower()))
        elif p.exists():
            paths.append(p)
        else:
            raise FileNotFoundError(raw)
    # Prefer the single all-season file when both layouts are present.
    all_seasons = [p for p in paths if p.name == "game_tracking.csv"]
    if all_seasons:
        return all_seasons[:1]
    seen, unique = set(), []
    for p in paths:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--turns", required=True)
    ap.add_argument("--player-play", required=True)
    ap.add_argument("--tracking", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--chunk-rows", type=int, default=2_000_000)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--allow-sample-tracking", action="store_true",
                    help="bypass ONLY the pre-flight stop; stamps output "
                         "PLACEHOLDER_SAMPLE and never produces a FULL claim")
    args = ap.parse_args(argv)

    turns_path = Path(args.turns).expanduser().resolve()
    pp_path = Path(args.player_play).expanduser().resolve()
    out = Path(args.out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    tracking = resolve_tracking(args.tracking)
    if not tracking:
        _log("STOP — no tracking files found. Looked for *.csv under the "
             "--tracking paths given.")
        return 2

    stamp = "PLACEHOLDER_SAMPLE" if args.allow_sample_tracking else "FULL"
    _log(f"turns      : {turns_path}")
    _log(f"player_play: {pp_path}")
    _log(f"tracking   : {', '.join(p.name for p in tracking)}")
    _log(f"out        : {out}  [{stamp}]")

    band = load_band(turns_path)
    band_plays = band[["game_id", "play_id"]].drop_duplicates()
    band_turn_count = len(band)
    _log(f"band turns : {band_turn_count:,} in {len(band_plays):,} plays")

    # ---- pre-flight ------------------------------------------------------
    preflight_path = out / "preflight.json"
    cached = {}
    if preflight_path.exists():
        try:
            cached = json.loads(preflight_path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
    counts = {}
    for p in tracking:
        key = str(p.resolve())
        stat = p.stat()
        hit = (cached.get(key) or {})
        if (hit.get("size") == stat.st_size
                and hit.get("mtime_ns") == stat.st_mtime_ns):
            counts[key] = hit
        else:
            _log(f"pre-flight: counting rows in {p.name} (one streaming pass)")
            counts[key] = gm.preflight_counts(p, chunk_rows=args.chunk_rows)
            counts[key]["size"] = stat.st_size
            counts[key]["mtime_ns"] = stat.st_mtime_ns
    preflight_path.write_text(json.dumps(counts, indent=2), encoding="utf-8")

    reports = [gm.preflight_verdict(c, band_plays=len(band_plays))
               for c in counts.values()]
    for report in reports:
        for line in report["findings"]:
            _log("  " + line)
    truncated = any(r["truncated"] for r in reports)
    stamp = ("PLACEHOLDER_SAMPLE" if args.allow_sample_tracking
             else ("TRUNCATED" if truncated else "FULL"))
    if truncated:
        _log("=" * 78)
        _log("FINDING — TRUNCATED EXPORT: the tracking file ends at exactly "
             "1,048,575 rows. The run continues on it; every number is "
             f"conditional on this subset. Stamp: {stamp}.")
        _log("=" * 78)
    else:
        _log(f"provenance stamp: {stamp}")

    # ---- extraction ------------------------------------------------------
    _log("extraction: streaming tracking, keeping the band's plays")
    shards = gm.extract_plays(
        tracking, band_plays, out, chunk_rows=args.chunk_rows,
        resume=not args.no_resume,
        expected_rows={str(p.resolve()): c["rows"] for p, c in zip(tracking, counts.values())},
        progress=_progress_printer())
    for s in shards:
        _log(f"  {Path(s['path']).name}: read {s['rows_read']:,}, kept "
             f"{s['rows_kept']:,}{' (reused)' if s.get('reused') else ''}")

    # ---- break frame, candidates, match ----------------------------------
    con = gm.connect()
    have_tracking = sum(s["rows_kept"] for s in shards) > 0
    if have_tracking:
        t0 = time.time()
        bps = gm.recover_break_frames(con, band, shards)
        _log(f"break frames: {int(bps['break_frame_ok'].sum()):,} of {len(band):,} "
             f"recovered within {gm.ALIGN_GATE_YD} yd "
             f"({time.time() - t0:,.0f}s)")
        frames = gm.candidate_frame(con, bps, shards)
        _log(f"candidate frames: {len(frames):,} player-rows across "
             f"{frames.groupby(list(gm.TURN_KEY), sort=False).ngroups:,} turns")
        match = gm.match_turns(band, frames, bps, pp_path)
    else:
        _log(f"FINDING — no tracking row for any of the {len(band):,} band turns. "
             "Every turn is reported untracked and every count is zero.")
        match = gm.empty_match(band)
    match.to_csv(out / "match_turns.csv", index=False, encoding="utf-8")

    coverage = gm.coverage_table(match, band_turn_count)
    coverage.to_csv(out / "band_coverage.csv", index=False, encoding="utf-8")
    _log("coverage of the band:")
    for _, r in coverage.iterrows():
        _log(f"  {r['radius_yd']:.0f} yd: unique {r['unique']:>5,} "
             f"({r['attributed_fraction']:6.1%}) · ambiguous {r['multiple']:>4,} · "
             f"none {r['none']:>4,} · unobservable {r['unobservable']:>5,} · "
             f"untracked {r['untracked']:>5,} · frame-lost "
             f"{r['break_frame_unrecovered']:>4,}")

    # ---- misattribution --------------------------------------------------
    elim = outcome_test.build_sample(gm.connect(), turns_path, pp_path)
    miss = gm.compare_elimination(match, elim)
    miss["rows"].to_csv(out / "misattribution_turns.csv", index=False,
                        encoding="utf-8")
    _log(f"misattribution: agree {miss['agree']:,} · disagree {miss['disagree']:,} · "
         f"ambiguous {miss['ambiguous']:,} · none {miss['none']:,} · "
         f"unmeasurable {miss['unmeasurable']:,}")
    if miss["misattribution_rate"] is None:
        _log(f"  {miss['label']}: 0 of 0 judgeable turns "
             f"({miss['elimination_turns']:,} elimination-attributed compared)")
    else:
        _log(f"  {miss['label']}: {miss['misattribution_rate']:.1%} "
             f"({miss['disagree']:,} of {miss['judgeable']:,} judgeable turns; "
             f"{miss['elimination_turns']:,} elimination-attributed compared)")

    # ---- legitimate stops before any fit ---------------------------------
    n_band = band_turn_count
    unseen = int(match["match_status"].isin(["unobservable", "untracked"]).sum())
    summary: dict = {
        "provenance": stamp,
        "attribution": gm.PROVENANCE_INFERENCE,
        "rule": gm.RULE_STRING,
        "spec": "notes/geometric-attribution-spec.md",
        "band": {"turns": n_band, "plays": int(len(band_plays))},
        "tracking": [{"file": Path(s["path"]).name, "rows": s["rows_read"],
                      "rows_kept": s["rows_kept"]} for s in shards],
        "coverage": coverage.to_dict(orient="records"),
        "misattribution": {k: v for k, v in miss.items() if k != "rows"},
        "stop": None,
    }

    # Attribution and the gate are measured before any stop check: the verdict
    # must be able to say how many turns were attributed even when the file is
    # too thin to fit.
    unique = match[match["match_status"] == "unique"]
    sample = gm.geometric_sample(match, pp_path, band)
    attributed = int(len(unique))
    attributed_fraction = attributed / n_band if n_band else 0.0
    gate_met = attributed >= 500 and attributed_fraction >= 0.30
    summary["gate"] = {
        "attributed_turns": attributed,
        "attributed_fraction": round(attributed_fraction, 4),
        "min_turns": 500, "min_share": 0.30, "gate_met": bool(gate_met),
    }

    unseen_share = unseen / n_band if n_band else 0.0
    summary["findings"] = {
        "opponent_visibility": {
            "band_turns": n_band,
            "turns_with_an_opponent_in_frame": int(n_band - unseen),
            "unobservable": int(match["match_status"].eq("unobservable").sum()),
            "untracked": int(match["match_status"].eq("untracked").sum()),
            "share_unobservable_or_untracked": round(unseen_share, 4),
        },
        "truncated_export": bool(truncated),
    }
    _log(f"opponent visibility: {n_band - unseen:,} of {n_band:,} band turns carry "
         f"an opposing player in the break frame; {unseen:,} ({unseen_share:.1%}) are "
         f"unobservable or untracked — reported, not a stop.")
    summary["geometric_sample"] = dict(outcome_test.sample_summary(sample)) if len(sample) else {}
    _log(f"attributed: {attributed:,} unique at {gm.PRIMARY_RADIUS_YD:.0f} yd "
         f"({attributed_fraction:.1%} of the band); with the matched receiver's "
         f"separation recorded: {len(sample):,}; gate "
         f"{'MET' if gate_met else 'NOT MET'}")

    if len(sample) == 0:
        summary["estimate_status"] = (
            f"0 of {attributed:,} attributed turns carry the matched receiver's "
            f"separation_at_pass_forward ({attributed} of {n_band:,} band turns)")
        _log("estimate: not fitted — " + summary["estimate_status"] + ".")
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _write_verdict(out, summary)
        gm.write_manifest(out, [turns_path, pp_path, *tracking],
                          [out / "match_turns.csv", out / "band_coverage.csv",
                           out / "misattribution_turns.csv", out / "summary.json",
                           out / "verdict.txt"],
                          config={"chunk_rows": args.chunk_rows,
                                  "resume": not args.no_resume,
                                  "allow_sample_tracking": args.allow_sample_tracking},
                          extra={"stage": "no-attributed-outcome",
                                 "provenance": stamp})
        return 0

    if len(sample):
        fits, boot = outcome_test.run_all(sample)
        verdict = outcome_test.verdict(fits, boot)
        est = {
            "beta": round(float(fits[0].beta), 4),
            "lo": round(float(fits[0].lo), 4),
            "hi": round(float(fits[0].hi), 4),
            "p": round(float(fits[0].p), 5),
            "n": int(fits[0].n),
            "clusters": int(fits[0].n_clusters),
            "wild_cluster_p": boot["p_wild_cluster"],
            "label": verdict["label"] if gate_met else
                     "DESCRIPTIVE ONLY — BELOW PRE-DECLARED GATE",
        }
        est["elimination_beta"] = float(
            pd.read_csv(ROOT / "outputs" / "outcome_test" / "estimates.csv")
            .iloc[0]["beta_yd_per_unit_retention"])
        summary["estimate"] = est

        pd.DataFrame([f.as_dict() for f in fits]).to_csv(
            out / "estimates_geometric.csv", index=False, encoding="utf-8")
        comp = gm.compare_with_d199(
            fits, ROOT / "outputs" / "outcome_test" / "estimates.csv")
        comp["attributed_fraction_of_band"] = round(attributed_fraction, 4)
        comp.to_csv(out / "estimates_comparison.csv", index=False, encoding="utf-8")
        (out / "verdict.json").write_text(json.dumps(verdict, indent=2),
                                          encoding="utf-8")
        _log(f"estimate: beta {est['beta']:+.3f} yd/unit "
             f"[{est['lo']:+.3f}, {est['hi']:+.3f}], n={est['n']:,}, "
             f"clusters={est['clusters']:,}, label {est['label']}")

    # ---- persistence diagnostic -----------------------------------------
    pf = gm.pass_forward_points(con, shards) if have_tracking else pd.DataFrame()
    if len(pf):
        at_pf = gm.players_at_pass_forward(con, pf, shards)
        pers = gm.persistence_check(match, pf, at_pf, pp_path)
        pers.to_csv(out / "persistence.csv", index=False, encoding="utf-8")
        counts_p = pers["persistence"].value_counts().to_dict()
        summary["persistence"] = {str(k): int(v) for k, v in counts_p.items()}
        _log("persistence at pass forward: " + " · ".join(
            f"{k} {v:,}" for k, v in sorted(counts_p.items())))
    else:
        summary["persistence"] = {"status": "PERSISTENCE NOT EVALUABLE"}
        _log("PERSISTENCE NOT EVALUABLE — no pass_forward event in the tracking")

    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_verdict(out, summary)
    gm.write_manifest(out, [turns_path, pp_path, *tracking],
                      [out / "match_turns.csv", out / "band_coverage.csv",
                       out / "misattribution_turns.csv",
                       out / "estimates_geometric.csv",
                       out / "estimates_comparison.csv", out / "summary.json",
                       out / "verdict.txt"],
                      config={"chunk_rows": args.chunk_rows,
                              "resume": not args.no_resume,
                              "allow_sample_tracking": args.allow_sample_tracking},
                      extra={"stage": "complete", "provenance": stamp})
    return 0


def _write_verdict(out: Path, summary: dict) -> None:
    lines = gm.plain_english_verdict(summary)
    (out / "verdict.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log("")
    _log("VERDICT")
    for line in lines:
        _log("  " + line)


if __name__ == "__main__":
    raise SystemExit(main())
