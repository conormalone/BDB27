#!/usr/bin/env python3
"""00b_external_proxies.py — Phase 0.5: construct pre-Combine expectation proxies (Grinding The Mocks).

Maps Kaggle ``nflId`` to a **Pre-Combine Expected Draft Position** (EDP). Per the Phase 0.5 spec
(``notes/phase-0.5-external-proxies.md``):

1. **GTM EDP** — ingest historical Grinding-The-Mocks mock-draft data for the in-scope draft classes
   (``external.years``); keep **February snapshots only** (``external.snapshot.month``) so the EDP is
   uncontaminated by Combine performance; aggregate **median** (config) EDP per player.
2. **ID resolution** — fuzzy match on Name + Draft Year + Position + College (stdlib ``difflib``; no new
   dependency); write sub-threshold cases to ``unmatched_external_ids.csv``; **assert a match rate >
   ``external.min_match_rate``** (0.85) for drafted WRs/TEs, else halt + write ``notes/blockers.md``.
3. **Checkpoint & deliverable** — join to Kaggle ``nflId``; write ``outputs/pre_combine_expectation.parquet``
   with **exactly** ``nflId, Pre_Combine_EDP, match_confidence_score`` (schema asserted). This file **gates
   Phase 4** (``04_models.py``, existence + schema; see ``src/gate.py``).

Data availability. The live GTM export is currently **not machine-obtainable** (Shiny websocket app;
``robots.txt`` Disallow; no static export / mirror / usable capture). We therefore **never fabricate data**:
``--mode full`` first looks for a cached raw export under ``data/raw/external/gtm/``; if none exists it
appends a data-availability **blocker** and stops. ``--mode sample`` and ``--synthetic`` exercise the
*identical* pipeline end to end against a **schema-compliant synthetic GTM fixture**
(``src/synthetic_gtm_fixture.py``), labelled ``UNVALIDATED SAMPLE OUTPUT`` / ``SYNTHETIC FIXTURE``.

Discipline (TASK.md): config-driven (no magic numbers); never load full CSVs into pandas (DuckDB +
explicit columns); strict 0.5 s API pacing; raw responses cached to ``data/raw/external/``; wall time +
peak RAM to ``outputs/run_log.csv``; checkpoint + skip unless ``--force``.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import gate

PROV_SAMPLE = "UNVALIDATED SAMPLE OUTPUT"
PROV_SYNTH = "SYNTHETIC FIXTURE (not a finding)"
PROXY_COLS = ["nflId", "Pre_Combine_EDP", "match_confidence_score"]  # EXACT spec schema (asserted)

MANIFEST_PREAMBLE = """# External Data Manifest — BDB27 Phase 0.5 (Grinding The Mocks)

Compliance record for the Kaggle **External Data** forum post: exact URLs/endpoints, access dates and
outcomes of every route attempted to obtain the external pre-Combine expectation proxy.

**Source in scope:** Grinding The Mocks (GTM) — the SOLE external source for Phase 0.5 (CFBD references
were cut by Conor and are out of scope). **Status: UNAVAILABLE** (see `notes/blockers.md`); the pipeline
is built and exercised against a schema-compliant synthetic fixture pending data access.

## Bounded acquisition attempt (manual, 2026-10-16)

| Route | Target | Result |
|---|---|---|
| Direct (DNS) | `grindingthemocks.com` → 216.92.3.120 | DNS resolves |
| Direct (HTTP :80) | `http://grindingthemocks.com/` | **301** → `https://grindingthemocks.shinyapps.io/Dashboard` |
| Direct (HTTPS :443) | `https://grindingthemocks.com/`, `/api`, `www.` | **TCP connect timeout** (unreachable) |
| Redirect target (found new) | `grindingthemocks.shinyapps.io/Dashboard/` | **202/302, reachable** — but it is a Shiny **websocket** app (interactive, no static export) |
| Third-party renderer | `r.jina.ai/https://grindingthemocks.shinyapps.io/Dashboard/` | renders the landing shell only; the data table is loaded lazily over the Shiny websocket — **no data in response** |
| `robots.txt` (redirect target) | `grindingthemocks.shinyapps.io/robots.txt` | `User-agent: * / Disallow: /` → **not crawled** (site policy) |
| GitHub repo search (no token) | `grinding+themocks`, `grindingthemocks`, `grinding-the-mocks`, `mock+draft+expected+draft+position` | 0 relevant repos (1 unrelated) |
| GitHub code search | `api.github.com/search/code?q=grindingthemocks` | **401** — requires authentication (none available; none invented) |
| Wayback availability | `archive.org/wayback/available?url=grindingthemocks.com` | closest **2024-05-28** snapshot; captured page is the same app shell (no data) |
| Wayback CDX (csv/json) | `web.archive.org/cdx/...filter=original:.*(csv|json)` | empty; CDX endpoint times out/429 (rate-limited) |
| Static export probe | `.../Dashboard/{data.csv,www/data.csv}`, `.../data.csv`, `.../mockdata.csv` | **404** (no static download) |

**Conclusion:** no bounded, policy-compliant machine route yields a raw GTM export. Per the spec we do
**not** substitute another source and do **not** fabricate data.

## Machine acquisition log (this run)

"""


# --------------------------------------------------------------------------- #
# paths / labels
# --------------------------------------------------------------------------- #
def _labelled_dir(cfg: dict, mode: str, synthetic: bool) -> Path:
    """Where non-full (sample/synthetic) outputs live (kept apart from the canonical full-run path)."""
    base = common.resolve(cfg, "outputs", cfg["external"]["out_subdir"])
    return base / ("synthetic" if synthetic else "sample")


def proxy_path(cfg: dict, mode: str, synthetic: bool) -> Path:
    """The proxy parquet path: canonical ``outputs/<proxy_file>`` for a full run, else a labelled dir."""
    name = cfg["external"]["proxy_file"]
    if mode == "full" and not synthetic:
        return common.resolve(cfg, "outputs", name)
    return _labelled_dir(cfg, mode, synthetic) / name


def unmatched_path(cfg: dict, mode: str, synthetic: bool) -> Path:
    name = cfg["external"]["unmatched_file"]
    if mode == "full" and not synthetic:
        return common.resolve(cfg, "outputs", name)
    return _labelled_dir(cfg, mode, synthetic) / name


# --------------------------------------------------------------------------- #
# provenance / blocker helpers (house convention)
# --------------------------------------------------------------------------- #
def record_blocker(cfg: dict, msg: str, log=None) -> None:
    path = common.resolve(cfg, "notes") / "blockers.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n- **BDB27 00b_external_proxies BLOCKER** ({stamp}): {msg}\n")
    if log:
        log.error("BLOCKER: %s", msg)


def update_manifest(cfg: dict, entries: list[dict], log=None) -> Path:
    """Write/maintain notes/external_data_manifest.md (preamble preserved; auto block spliced)."""
    path = common.resolve(cfg, "notes") / cfg["external"]["manifest_file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    block = ["<!-- AUTO-MANIFEST:START -->", "| source | url | access_date | result |",
             "|---|---|---|---|"]
    for e in entries:
        block.append(f"| {e['source']} | {e['url']} | {e['access_date']} | {e['result']} |")
    block.append("<!-- AUTO-MANIFEST:END -->")
    auto = "\n".join(block)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if "<!-- AUTO-MANIFEST:START -->" in text and "<!-- AUTO-MANIFEST:END -->" in text:
            text = re.sub(r"<!-- AUTO-MANIFEST:START -->.*?<!-- AUTO-MANIFEST:END -->", auto, text,
                          flags=re.S)
        else:
            text = text.rstrip("\n") + "\n\n" + auto + "\n"
    else:
        text = MANIFEST_PREAMBLE + auto + "\n"
    path.write_text(text, encoding="utf-8")
    if log:
        log.info("manifest -> %s", path)
    return path


# --------------------------------------------------------------------------- #
# acquisition (bounded, paced, cached)
# --------------------------------------------------------------------------- #
def acquire_gtm(cfg: dict, mode: str, synthetic: bool, log) -> tuple[list[Path], list[dict], bool]:
    """Return (raw GTM file paths, manifest entries, using_fixture). Never fabricates data.

    * synthetic     -> the committed fixture raw file (using_fixture=True).
    * full / sample -> a cached raw export under data/raw/external/gtm/ if present (using_fixture=False);
      otherwise, for full, bounded live attempts; for sample, fall back to the fixture (using_fixture=True)
      so the pipeline is still exercised end to end and labelled ``UNVALIDATED SAMPLE OUTPUT``.
    """
    ext = cfg["external"]
    today = time.strftime("%Y-%m-%d")
    entries: list[dict] = []

    if synthetic:
        syn = ext["synthetic"]
        raw = common.resolve(cfg, "fixtures", syn["fixture_dir"]) / syn["gtm_file"]
        if not raw.exists():
            record_blocker(cfg, f"synthetic GTM fixture missing at {raw}; run "
                                f"`python src/synthetic_gtm_fixture.py` first", log)
            return [], entries, True
        entries.append({"source": "fixture", "url": str(raw), "access_date": today,
                        "result": PROV_SYNTH})
        return [raw], entries, True

    # full / sample: prefer a cached real export; else (full) bounded live attempts, (sample) fixture
    cache_dir = common.resolve(cfg, "raw", ext["raw_cache_dir"]) / "gtm"
    cached = [cache_dir / fn for fn in ext["gtm"]["raw_files"]]
    found = [p for p in cached if p.exists()]
    if found:
        entries.append({"source": "cache", "url": str(cache_dir), "access_date": today,
                        "result": f"{len(found)} cached raw file(s)"})
        return found, entries, False

    if mode == "sample":
        syn = ext["synthetic"]
        raw = common.resolve(cfg, "fixtures", syn["fixture_dir"]) / syn["gtm_file"]
        if raw.exists():
            entries.append({"source": "fixture-fallback", "url": str(raw), "access_date": today,
                            "result": f"no cached GTM export; {PROV_SAMPLE}"})
            return [raw], entries, True
        record_blocker(cfg, f"sample fallback fixture missing at {raw}; run "
                            f"`python src/synthetic_gtm_fixture.py` first", log)
        return [], entries, True

    endpoints = list(ext["gtm"].get("endpoints") or [])
    for ep in endpoints:
        result = _attempt_fetch(ep, ext, log)
        entries.append({"source": "gtm-endpoint", "url": ep, "access_date": today, "result": result})
        time.sleep(float(ext["request_sleep_s"]))  # strict rate limit
    return [], entries, False


def _attempt_fetch(url: str, ext: dict, log) -> str:
    """Bounded GET (retries from config); return a short result string. Never raises."""
    attempts = 1 + int(ext.get("max_retries", 0))
    last = "unavailable"
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bdb27-phase0.5/1.0"})
            with urllib.request.urlopen(req, timeout=float(ext["read_timeout_s"])) as resp:
                body = resp.read(4096)
                last = f"HTTP {resp.status} ({len(body)}B sampled)"
                if resp.status == 200 and b"," in body:
                    last += " — no machine-readable export"
                break
        except Exception as exc:  # noqa: BLE001 - acquisition is best-effort + bounded
            last = f"{type(exc).__name__}: {exc}"
            if i + 1 < attempts:
                time.sleep(float(ext["request_sleep_s"]))
    if log:
        log.info("acquisition %s -> %s", url, last)
    return last


# --------------------------------------------------------------------------- #
# ingest + February filter + EDP aggregation (DuckDB; no pandas)
# --------------------------------------------------------------------------- #
def load_players(con, cfg: dict, synthetic: bool) -> list[dict]:
    """Load in-scope prospects (drafted WR/TE in the config years) from Kaggle or the fixture."""
    ext = cfg["external"]
    if synthetic:
        src = common.resolve(cfg, "fixtures", ext["synthetic"]["fixture_dir"]) / ext["synthetic"]["players_file"]
        nullstr = ""
    else:
        src = common.csv_path(cfg, "players")
        nullstr = ", nullstr=['NA', '']"
    years = ", ".join(str(int(y)) for y in ext["years"])
    poss = ", ".join(f"'{p}'" for p in ext["positions"])
    sql = (
        f"SELECT nfl_id, display_name, draft_year, nfl_position, college_name, draft_overall_pick "
        f"FROM read_csv_auto('{src}', header=true{nullstr}) "
        f"WHERE draft_year IN ({years}) AND nfl_position IN ({poss})"
    )
    rows = con.execute(sql).fetchall()
    out = []
    for r in rows:
        out.append(dict(nfl_id=int(r[0]), name=str(r[1] or ""), draft_year=int(r[2]),
                        position=str(r[3] or ""), college=str(r[4] or ""),
                        drafted=(r[5] not in (None, "", "NA"))))
    return out


def ingest_gtm_feb(con, gtm_paths: list[Path], cfg: dict) -> list[dict]:
    """Filter to February snapshots for the in-scope years; aggregate median/mean EDP per prospect."""
    ext = cfg["external"]
    month = int(ext["snapshot"]["month"])
    years = ", ".join(str(int(y)) for y in ext["years"])
    file_list = ", ".join(f"'{p}'" for p in gtm_paths)
    agg = str(ext["aggregation"]).lower()
    if agg not in ("median", "mean"):
        raise ValueError(f"unsupported external.aggregation: {agg!r}")
    period = "month(snapshot_date::DATE)"
    sql = f"""
        SELECT draft_year,
               prospect_name,
               position,
               college,
               {agg}(projected_pick) AS edp,
               count(*) AS n_snaps
        FROM read_csv_auto([{file_list}], header=true, union_by_name=true)
        WHERE {period} = {month} AND draft_year IN ({years})
        GROUP BY 1, 2, 3, 4
    """
    rows = con.execute(sql).fetchall()
    return [dict(draft_year=int(r[0]), name=str(r[1] or ""), position=str(r[2] or ""),
                 college=str(r[3] or ""), edp=float(r[4]), n_snaps=int(r[5])) for r in rows]


# --------------------------------------------------------------------------- #
# fuzzy ID resolution (Name + Draft Year + Position + College)
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    """Lowercase, strip non-letters, collapse whitespace — the fuzzy-comparison key."""
    return " ".join(re.sub(r"[^a-z ]", " ", str(s or "").lower()).split())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def fuzzy_match(players: list[dict], gtm_rows: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """One-to-one greedy match. Returns (matched proxy rows, unmatched GTM rows). Deterministic."""
    fz = cfg["external"]["fuzzy"]
    w_name, w_col, w_pos = float(fz["name_weight"]), float(fz["college_weight"]), float(fz["position_weight"])
    min_score, min_name = float(fz["min_score"]), float(fz["min_name_score"])

    pairs: list[tuple[float, float, int, int]] = []
    for gi, g in enumerate(gtm_rows):
        for pi, p in enumerate(players):
            if p["draft_year"] != g["draft_year"]:          # hard filter (draft year)
                continue
            name_sim = _ratio(g["name"], p["name"])
            col_sim = _ratio(g["college"], p["college"])
            pos_eq = 1.0 if _norm(g["position"]) == _norm(p["position"]) else 0.0
            score = w_name * name_sim + w_col * col_sim + w_pos * pos_eq
            if score >= min_score and name_sim >= min_name:
                # sort key: (-score, -name_sim, gtm_idx, player_idx) -> deterministic best-first
                pairs.append((-score, -name_sim, gi, pi))

    pairs.sort()
    taken_p, taken_g = set(), set()
    matched: list[dict] = []
    for neg_score, neg_name, gi, pi in pairs:
        if gi in taken_g or pi in taken_p:
            continue
        taken_g.add(gi)
        taken_p.add(pi)
        matched.append(dict(nflId=int(players[pi]["nfl_id"]),
                            Pre_Combine_EDP=float(gtm_rows[gi]["edp"]),
                            match_confidence_score=round(float(-neg_score), 6)))
    matched.sort(key=lambda r: r["nflId"])
    unmatched = [g for gi, g in enumerate(gtm_rows) if gi not in taken_g]
    return matched, unmatched


def match_rate(players: list[dict], matched: list[dict]) -> tuple[float, int, int]:
    """Share of DRAFTED in-scope players (WR/TE) that received a proxy. Returns (rate, hit, denom)."""
    drafted = [p for p in players if p["drafted"]]
    hit_ids = {m["nflId"] for m in matched}
    denom = len(drafted)
    hits = sum(1 for p in drafted if p["nfl_id"] in hit_ids)
    return ((hits / denom) if denom else 0.0), hits, denom


# --------------------------------------------------------------------------- #
# strict schema writer (EXACTLY nflId, Pre_Combine_EDP, match_confidence_score)
# --------------------------------------------------------------------------- #
def assert_proxy_schema(cols: list[str]) -> None:
    if list(cols) != PROXY_COLS:
        raise AssertionError(f"proxy schema mismatch: got {list(cols)} expected {PROXY_COLS}")


def write_proxy_parquet(path: Path, rows: list[dict]) -> None:
    """Write the proxy with the EXACT spec schema (+ dtypes) and assert it by read-back."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([("nflId", pa.int64()),
                        ("Pre_Combine_EDP", pa.float64()),
                        ("match_confidence_score", pa.float64())])
    tbl = pa.Table.from_pylist(
        [{"nflId": int(r["nflId"]), "Pre_Combine_EDP": float(r["Pre_Combine_EDP"]),
          "match_confidence_score": float(r["match_confidence_score"])} for r in rows],
        schema=schema)
    pq.write_table(tbl, path, compression="zstd")
    back = pq.read_table(path).schema.names
    assert_proxy_schema(back)
    if len(back) != 3:
        raise AssertionError("proxy parquet must have exactly 3 columns")


def write_unmatched_csv(path: Path, rows: list[dict], prov: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["provenance", "draft_year", "prospect_name", "position", "college", "n_snaps",
              "best_score", "reason"]
    norm = [{"provenance": prov, **{k: r.get(k, "") for k in fields[1:]}} for r in rows]
    common.write_csv(path, norm, fieldnames=fields)


def write_label(path: Path, prov: str) -> None:
    """Sidecar label (the strict parquet schema forbids a provenance column)."""
    path.with_suffix(path.suffix + ".provenance.txt").write_text(prov + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(cfg: dict, mode: str, synthetic: bool, force: bool, log) -> None:
    ext = cfg["external"]
    con = common.connect(cfg["runtime"]["duckdb_threads"])

    raw_paths, entries, using_fixture = acquire_gtm(cfg, mode, synthetic, log)
    update_manifest(cfg, entries, log)

    if not raw_paths:
        record_blocker(cfg, "GTM raw export UNAVAILABLE via every bounded route (direct DNS/HTTP/HTTPS, "
                            "shinyapps redirect target = interactive Shiny websocket app, GitHub "
                            "repos/code, Wayback, static probes); no cached export under "
                            "data/raw/external/gtm/. Refusing to fabricate or substitute a source "
                            "(CFBD out of scope). See notes/external_data_manifest.md.", log)
        raise SystemExit(2)

    players = load_players(con, cfg, synthetic=using_fixture)
    gtm_rows = ingest_gtm_feb(con, raw_paths, cfg)
    log.info("in-scope players=%d, February GTM prospect-rows=%d", len(players), len(gtm_rows))
    if not gtm_rows:
        record_blocker(cfg, "no February GTM snapshots ingested for the in-scope draft classes", log)
        raise SystemExit(2)

    matched, unmatched = fuzzy_match(players, gtm_rows, cfg)
    rate, hits, denom = match_rate(players, matched)
    log.info("matched=%d unmatched=%d | drafted WR/TE match rate=%.3f (%d/%d)",
             len(matched), len(unmatched), rate, hits, denom)

    prov = PROV_SYNTH if synthetic else common.provenance(cfg, mode)
    unmatched_rows = []
    for g in unmatched:
        unmatched_rows.append(dict(draft_year=g["draft_year"], prospect_name=g["name"],
                                   position=g["position"], college=g["college"], n_snaps=g["n_snaps"],
                                   best_score="", reason="below_threshold"))
    up = unmatched_path(cfg, mode, synthetic)
    write_unmatched_csv(up, unmatched_rows, prov)
    log.info("wrote %s (%d rows)", up.name, len(unmatched_rows))

    if rate <= float(ext["min_match_rate"]):
        record_blocker(cfg, f"drafted WR/TE match rate {rate:.3f} ({hits}/{denom}) is NOT above "
                            f"external.min_match_rate={ext['min_match_rate']} -> halting (spec Step 2)", log)
        raise SystemExit(2)

    pp = proxy_path(cfg, mode, synthetic)
    write_proxy_parquet(pp, matched)
    write_label(pp, prov)
    log.info("wrote %s (%d rows; schema=%s)", pp.name, len(matched), ",".join(PROXY_COLS))

    summary = dict(stage="00b_external_proxies", mode=mode, provenance=prov, synthetic=synthetic,
                   dataset=("synthetic fixture (not a finding)" if using_fixture else "real"),
                   source=ext["source"], aggregation=ext["aggregation"],
                   years=[int(y) for y in ext["years"]], positions=list(ext["positions"]),
                   n_players_in_scope=len(players), n_gtm_prospects=len(gtm_rows),
                   n_matched=len(matched), n_unmatched=len(unmatched),
                   drafted_wr_te_match_rate=round(rate, 6), matched_hits=hits, matched_denom=denom,
                   proxy_file=str(pp.name), schema=PROXY_COLS,
                   raw_sources=[str(p) for p in raw_paths])
    (pp.parent / "_DONE.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("Phase 0.5 summary: %s", summary)


def main() -> None:
    ap = argparse.ArgumentParser(description="BDB27 Phase 0.5 — pre-Combine expectation proxies (GTM)")
    ap.add_argument("--mode", choices=["sample", "full"], default=None)
    ap.add_argument("--force", action="store_true", help="ignore the checkpoint / rebuild")
    ap.add_argument("--synthetic", action="store_true",
                    help="exercise the pipeline on the committed synthetic GTM fixture (not a finding)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    mode = args.mode or cfg["mode"]["default"]
    common.seed_everything(cfg["seed"])
    log = common.get_logger()

    gate.enforce(cfg, mode, "00b_external_proxies", requires_game_side=True, logger=log)

    pp = proxy_path(cfg, mode, args.synthetic)
    prov = PROV_SYNTH if args.synthetic else common.provenance(cfg, mode)
    stage = f"00b_external_proxies[{mode}{'-synthetic' if args.synthetic else ''}]"

    done = pp.parent / "_DONE.json"
    if done.exists() and not args.force:
        rec = common.read_done_marker(done)
        if common.checkpoint_match(rec, mode=mode, provenance=prov,
                                   synthetic=args.synthetic, path=done):
            log.info("checkpoint hit (%s) — skipping; use --force to rebuild", done)
            common.append_run_log(cfg, stage, mode, 0.0, common.peak_ram_mb(), "", {"skipped": "checkpoint"})
            return
        log.info("checkpoint marker %s does not match the requested run (%s) — rebuilding",
                 done.name, common.checkpoint_mismatch_note(
                     rec, mode=mode, provenance=prov, synthetic=args.synthetic))

    with common.timed_stage(cfg, stage, mode, synthetic=args.synthetic):
        run(cfg, mode, args.synthetic, args.force, log)


if __name__ == "__main__":
    main()
