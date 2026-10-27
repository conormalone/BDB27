#!/usr/bin/env python3
"""00_thresholds.py — freeze the effort / leverage / gate thresholds.

Writes ``outputs/thresholds.json`` with real provenance logic:

* sample mode  -> ``PLACEHOLDER_SAMPLE`` provenance, artefact stamped
  ``UNVALIDATED SAMPLE OUTPUT``;
* full mode    -> refuses to emit ``FULL`` unless the full tracking input
  exists *and* its parsed row count exceeds the provided sample row cap.
  Any failure exits non-zero with the actual numbers in the message.

The values are design placeholders from ``config.yaml`` (``bdb27_lc:``); none is
estimated from data.  Scripts 03-05 call
``lc_common.require_full_provenance`` before reporting a number.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import lc_common as C

SECTIONS = ("thresholds", "ceiling", "audit", "leverage", "efforts", "power",
            "gate")


def _row_count(cfg, files: list[Path]) -> int:
    con = C.duckdb_connect(cfg)
    try:
        null = C.lc(cfg)["runtime"].get("csv_null_marker", "NA")
        if len(files) == 1:
            reader = f"read_csv_auto('{files[0].as_posix()}', nullstr=['{null}'])"
        else:
            listing = "[" + ", ".join(f"'{f.as_posix()}'" for f in files) + "]"
            reader = (f"read_csv_auto({listing}, nullstr=['{null}'], "
                      f"union_by_name=true)")
        return int(con.execute(f"SELECT count(*) FROM {reader}").fetchone()[0])
    finally:
        con.close()


def verify_full_input(cfg, mode: str = "full") -> dict:
    """Prove the full tracking input is present and larger than the sample cap.

    Raises ``RuntimeError`` with a specific message on any failure.  This is the
    gate that prevents a ``FULL`` provenance label being attached to sample data.
    """
    files = C.tracking_source(cfg, mode)
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise RuntimeError(
            "FULL MODE REFUSED: full tracking input is missing:\n  " +
            "\n  ".join(missing) +
            f"\n  configured under bdb27_lc.files.{C.lc(cfg)['mode'][mode]['tracking_key']}")
    rows = _row_count(cfg, files)
    cap = int(C.lc(cfg)["mode"]["sample"]["sample_row_cap"])
    if rows <= cap:
        raise RuntimeError(
            "FULL MODE REFUSED: parsed tracking row count is "
            f"{rows:,}, which does not exceed the provided sample row cap of "
            f"{cap:,}. This input is sample-sized, so FULL provenance cannot be "
            "asserted. Point bdb27_lc.files.game_tracking_full at the real "
            "full-season tracking files.")
    return {"files": [str(f) for f in files], "rows": rows, "sample_row_cap": cap}


def build_payload(cfg, mode: str) -> dict:
    """Assemble the threshold document (provenance added by ``write_json``)."""
    block = C.lc(cfg)
    payload = {
        "mode": mode,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": block["seeds"]["base"],
        "config_fingerprint": C.config_fingerprint(cfg, "00_thresholds"),
        "provenance_note": block["provenance_note"],
    }
    for section in SECTIONS:
        payload[section] = block[section]
    return payload


def main(argv=None) -> int:
    args = C.mode_from_argv(argv)
    cfg = C.load_config(args.config)
    block = C.lc(cfg)
    C.seed_everything(int(block["seeds"]["base"]))
    paths = C.resolve_paths(cfg, args.mode)

    try:
        C.assert_disk_space(paths, cfg)
        verified = None
        if args.mode == "full":
            level = C.provenance(cfg, args.mode)
            if level != "FULL":
                raise RuntimeError(
                    f"FULL MODE REFUSED: config provenance for mode=full is "
                    f"{level!r}, not 'FULL'")
            verified = verify_full_input(cfg, args.mode)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    payload = build_payload(cfg, args.mode)
    if verified:
        payload["full_input"] = verified
    out = C.write_json(paths["thresholds"], payload, mode=args.mode, cfg=cfg)
    print(f"wrote {out} (mode={args.mode}, "
          f"provenance={C.provenance(cfg, args.mode)})")
    if verified:
        print(f"  verified full input: {verified['rows']:,} rows "
              f"(sample cap {verified['sample_row_cap']:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
