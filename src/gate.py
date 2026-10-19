#!/usr/bin/env python3
"""gate.py — runtime enforcement of the pre-registration freeze and the audit go/no-go gate.

Per `TASK.md`, a `--mode full` run may **not** start until `notes/prereg.md` is frozen (values filled,
not the skeleton) **and** the audit go/no-go gate passes; otherwise the run stops and points at
`notes/blockers.md`. This module turns that rule into code so an unfrozen/missing pre-registration is a
hard stop rather than a silent one.

Two requirement levels:

* **freeze-only** (Combine-side stages, e.g. `02_combine_envelope`): `notes/prereg.md` must read
  `FROZEN` and every pre-registered `config.yaml` key must be non-null. The Combine file is complete, so
  these stages legitimately run in `--mode full` once the freeze holds.
* **freeze + audit go/no-go** (game-side stages, `03/04/05`): additionally requires the Phase-1 audit
  decision (`outputs/audit/audit_decision.json`) to be a pass (not `NO-GO`, not the unfrozen
  `CONDITIONAL`), mirroring the matched-WR / route-snap / missing-field / ICC minima.
* **+ external proxy** (Phase-4 models, `04_models`): when `external.require_for_phase4` is set, a full run
  additionally requires the Phase 0.5 deliverable `outputs/<external.proxy_file>` to **exist** with
  **exactly** the configured schema (`external.schema`) — this gates Phase 4 on the pre-Combine
  expectation proxy (existence + schema). Never affects `--mode sample`.

Usage (CLI):
    python src/gate.py --mode full [--stage 02_combine_envelope] [--requires-game-side]
        [--requires-external-proxy]
    # exit 0 = PASS, exit 2 = BLOCKED (writes the reason; points at notes/blockers.md)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

# Every pre-registered key that must be fixed (non-null, non-empty) before a full run.
# (notes/prereg.md freeze checklist items 1-16.)
REQUIRED_KEYS: list[str] = [
    "models.quantiles",
    "game.top_k_efforts",
    "game.route_snap_count",
    "game.scope.primary",
    "game.scope.sensitivity",
    "game.scope.active",
    "envelope.max_features",
    "envelope.selection_rule",
    "envelope.icc_min",
    "audit.min_icc",
    "audit.min_matched_wr",
    "audit.min_route_snaps_per_player",
    "audit.exceedance_margin_rel",
    "audit.exceedance_reframe_rate",
    "envelope.vif_max",
    "envelope.split_distances_yd",
    "envelope.speed_at_times_s",
    "envelope.cut_metrics",
    "envelope.min_attempts_per_player",
    "game.thresholds.high_speed_percentile",
    "game.thresholds.accel_percentile",
    "models.bootstrap_reps",
    "models.controls_without_draft",
    "models.fallbacks",
    "validate.cv_folds",
    "validate.perm_reps",
    "validate.measurement_error_correction",
    "validate.sensitivity",
]

UNFROZEN_HINTS = ("skeleton", "tbd", "tick as each is fixed")


def _dig(cfg: dict[str, Any], dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return "__MISSING__"
        node = node[part]
    return node


def _is_unset(value: Any) -> bool:
    if value == "__MISSING__":
        return True
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, str)) and len(value) == 0:
        return True
    return False


# --------------------------------------------------------------------------- #
# pre-registration freeze
# --------------------------------------------------------------------------- #
def prereg_frozen(cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    """True iff notes/prereg.md reads FROZEN with no residual TBD / unticked checklist item."""
    reasons: list[str] = []
    path = common.resolve(cfg, "notes") / "prereg.md"
    if not path.exists():
        return False, [f"notes/prereg.md missing at {path}"]
    text = path.read_text(encoding="utf-8")

    header = "\n".join(text.splitlines()[:6]).lower()
    if not re.search(r"\*\*status:\s*frozen", header):
        reasons.append("notes/prereg.md does not declare '**Status: FROZEN**' in its header")
    if not any(h in header for h in ("frozen",)):
        reasons.append("notes/prereg.md header has no FROZEN marker")
    # residual placeholders
    for token in ("TBD",):
        if re.search(rf"\b{token}\b", text):
            reasons.append(f"notes/prereg.md still contains '{token}' (unfrozen value)")
    if "☐" in text:
        reasons.append("notes/prereg.md has unticked checklist items ('☐')")

    return (not reasons), reasons


def config_keys_ok(cfg: dict[str, Any], keys: list[str] | None = None) -> tuple[bool, list[str]]:
    """True iff every required pre-registered config key is set (non-null, non-empty)."""
    missing: list[str] = []
    for dotted in (keys or REQUIRED_KEYS):
        if _is_unset(_dig(cfg, dotted)):
            missing.append(dotted)
    return (not missing), missing


# --------------------------------------------------------------------------- #
# audit go/no-go gate (game-side stages only)
# --------------------------------------------------------------------------- #
def audit_gate_ok(cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    """True iff the Phase-1 audit decision is a pass (not NO-GO / not the unfrozen CONDITIONAL)."""
    dec_path = common.resolve(cfg, "outputs", cfg["audit"]["out_subdir"]) / "audit_decision.json"
    if not dec_path.exists():
        return False, [f"audit decision missing at {dec_path}; run 01_audit first"]
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    decision = str(dec.get("decision", ""))
    reasons: list[str] = []
    if decision.upper().startswith("NO-GO"):
        reasons.append(f"audit decision is NO-GO: {decision}")
    if "CONDITIONAL" in decision.upper() or "not frozen" in decision.lower():
        reasons.append(f"audit decision not a pass (thresholds unfrozen): {decision}")
    return (not reasons), reasons


# --------------------------------------------------------------------------- #
# external proxy gate (Phase 0.5 deliverable -> gates Phase 4)
# --------------------------------------------------------------------------- #
def external_proxy_gate_ok(cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    """True iff the Phase 0.5 proxy parquet exists with EXACTLY the configured schema.

    No-op (passes) when ``external.enabled`` or ``external.require_for_phase4`` is false, so the gate is
    config-driven and never blocks stages that do not need the proxy.
    """
    ext = cfg.get("external") or {}
    if not ext.get("enabled") or not ext.get("require_for_phase4"):
        return True, []
    path = common.resolve(cfg, "outputs", ext["proxy_file"])
    if not path.exists():
        return False, [f"Phase-0.5 external proxy missing at {path}; run "
                       f"`python src/00b_external_proxies.py --mode full` first"]
    expected = list(ext.get("schema") or [])
    try:
        import pyarrow.parquet as pq  # lazy: only needed when the gate actually checks
        names = list(pq.read_schema(path).names)
    except Exception as exc:  # noqa: BLE001 - a corrupt/unreadable parquet blocks the gate
        return False, [f"external proxy unreadable at {path}: {type(exc).__name__}: {exc}"]
    if names != expected:
        return False, [f"external proxy schema mismatch at {path}: got {names} expected {expected}"]
    return True, []


# --------------------------------------------------------------------------- #
# enforce
# --------------------------------------------------------------------------- #
def enforce(cfg: dict[str, Any], mode: str, stage: str = "", requires_game_side: bool = False,
            requires_external_proxy: bool = False, logger: Any = None) -> None:
    """Stop a full run that is not cleared by the freeze (/ audit / external) gate. No-op in sample."""
    if mode != "full":
        return
    ok, reasons = prereg_frozen(cfg)
    kok, missing = config_keys_ok(cfg)
    if not kok:
        reasons = reasons + [f"config key not fixed: {k}" for k in missing]
    if requires_game_side:
        aok, areasons = audit_gate_ok(cfg)
        if not aok:
            reasons = reasons + areasons
    if requires_external_proxy:
        eok, ereasons = external_proxy_gate_ok(cfg)
        if not eok:
            reasons = reasons + ereasons
    if reasons:
        msg = (f"FULL-RUN GATE BLOCKED for stage '{stage or '?'}': " + "; ".join(reasons) +
               " -> see notes/blockers.md")
        if logger:
            logger.error(msg)
        raise SystemExit(2)


def evaluate(cfg: dict[str, Any], mode: str, stage: str = "",
             requires_game_side: bool = False, requires_external_proxy: bool = False) -> dict[str, Any]:
    """Return a structured pass/fail report (used by the CLI and tests)."""
    frozen, freasons = prereg_frozen(cfg)
    kok, missing = config_keys_ok(cfg)
    rep: dict[str, Any] = {
        "mode": mode, "stage": stage, "requires_game_side": requires_game_side,
        "requires_external_proxy": requires_external_proxy,
        "prereg_frozen": frozen, "prereg_reasons": freasons,
        "config_keys_ok": kok, "missing_keys": missing,
        "reasons": list(freasons) + [f"config key not fixed: {k}" for k in missing],
    }
    rep["audit_gate_ok"] = None
    if requires_game_side:
        aok, areasons = audit_gate_ok(cfg)
        rep["audit_gate_ok"] = aok
        rep["reasons"] += areasons
    rep["external_proxy_gate_ok"] = None
    if requires_external_proxy:
        eok, ereasons = external_proxy_gate_ok(cfg)
        rep["external_proxy_gate_ok"] = eok
        rep["reasons"] += ereasons
    rep["passed"] = (not rep["reasons"]) and (mode == "full")
    if mode != "full":
        rep["passed"] = True  # sample mode is never gated
    return rep


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="BDB27 pre-registration / go-no-go gate check")
    ap.add_argument("--mode", choices=["sample", "full"], default="full")
    ap.add_argument("--stage", default="", help="stage name for the message (e.g. 02_combine_envelope)")
    ap.add_argument("--requires-game-side", action="store_true",
                    help="also require the audit go/no-go gate (game-side stages)")
    ap.add_argument("--requires-external-proxy", action="store_true",
                    help="also require the Phase-0.5 external proxy parquet (existence + schema)")
    ap.add_argument("--config", default=None)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = common.load_config(args.config)
    rep = evaluate(cfg, args.mode, args.stage, args.requires_game_side, args.requires_external_proxy)
    print(json.dumps(rep, indent=2))
    if not rep["passed"]:
        print(f"\nBLOCKED: {'; '.join(rep['reasons'])} -> see notes/blockers.md", file=sys.stderr)
        raise SystemExit(2)
    print("\nGATE PASS")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
