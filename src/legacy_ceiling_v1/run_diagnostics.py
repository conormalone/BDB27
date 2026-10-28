#!/usr/bin/env python3
"""run_diagnostics.py — stale-checkout guard and honest failure wording for the BDB27 notebook.

Why this module exists (defect report, 2026-10-18). A laptop ran a checkout that predated D43. The
stage code on that checkout had no ``_safe_exp`` clipping and no per-block labelling, so
``07_security_models`` died with a bare ``OverflowError`` and wrote no ``b_primary.csv``. §8 then
reported the absence as a *data* problem and told the reader to supply the game-tracking files,
which could not help. Three separate failures compounded:

1. a stale checkout ran silently (no commit/capability check);
2. §8 confused a crash with data that were never supplied;
3. the traceback tail was captured but never surfaced.

This module is deliberately dependency-free (stdlib only, no network) so it can run before the
pipeline imports anything heavy.

Numeric audit (D43/D44)
-----------------------
An AST scan of every module reachable from ``07_security_models.run()`` — ``07_security_models.py``,
``common.py``, ``gate.py``, ``model_features.py``, ``security_features.py`` — found exactly one
unguarded exponentiation: ``model_features.bmi`` computed ``float(height_in) ** 2`` outside a
``try``. That line — not the selection logit — is what killed the Windows run: CPython maps ERANGE
to ``OverflowError``, and the message differs by platform (Windows: ``math range error``;
Linux/glibc: ``(34, 'Numerical result out of range')``), which is why a Linux test suite never
reproduced it. The site is now guarded (overflow / division by zero / implausible inputs -> ``nan``)
and no longer uses ``**`` at all; D44 additionally declares plausibility bounds in config and
reports every implausible row by ``nfl_id`` (never a silent ``nan``). ``math.exp`` appears only
inside ``07_security_models._safe_exp`` (the single allowed site); the remaining ``** 2`` is a NumPy
array operation in a VIF variance sum, not a scalar float power.

Invariant (D43/D44, restated): **block B either produces ``b_primary.csv`` or the stage raises
``RuntimeError("primary block B failed (...)")`` after writing a labelled ``block_status=FAILED``
row.** Missing ``b_primary.csv`` is therefore never a reason to ask for more data.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# Capabilities the notebook needs from the checkout. A checkout missing any of these predates D43
# (per-block labelling / safe odds ratios) or D44 (guarded BMI + covariate-quality reporting).
REQUIRED_CAPABILITIES: list[dict[str, str]] = [
    {"path": "src/07_security_models.py", "needle": "def _safe_exp(",
     "label": "safe odds ratios (D43)"},
    {"path": "src/07_security_models.py", "needle": "block_status",
     "label": "per-block labelled failures (D43)"},
    {"path": "config.yaml", "needle": "or_exponent_clip",
     "label": "odds-ratio exponent clip (D43)"},
    {"path": "src/07_security_models.py", "needle": "covariate_quality",
     "label": "covariate-quality reporting (D44)"},
    {"path": "src/model_features.py", "needle": "sanitize_covariates",
     "label": "guarded BMI + plausibility bounds (D44)"},
    {"path": "config.yaml", "needle": "covariate_bounds",
     "label": "declared covariate plausibility bounds (D44)"},
]

_DATA_REMEDY = ("supply game_tracking_2023/24/25.csv under DATA_DIR, run in MODE='full', "
                "then re-run 07_security_models")
_CRASH_REMEDY = ("fix the exception and re-run 07_security_models (the data were read "
                 "successfully; supplying data again will not help)")

# Where each stage keeps its checkpoint + the artefacts that prove it produced output.
STAGE_OUTPUTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "01_audit": ("audit", ("audit_decision.json",)),
    "02_combine_envelope": ("envelope", ("per_player_envelope.csv",)),
    "00_thresholds": ("", ("thresholds.json",)),
    "03_game_demand": ("game", ("game_demand_summary.csv",)),
    "04_models": ("models", ("models_summary.csv",)),
    "05_validate": ("validate", ("validation_summary.csv",)),
    "06_security": ("security", ("SUMMARY.json",)),
    "07_security_models": ("security_models", ("SUMMARY.json",)),
    "08_route_robustness": ("security_models/route_robustness", ("route_robustness.md",)),
    "09_draft_and_target": ("security_models/draft_and_target", ("draft_target.md",)),
    "00b_external_proxies": ("external", ()),
}


# --------------------------------------------------------------------------- #
# git state (local commands only — never fetches, never raises)
# --------------------------------------------------------------------------- #
def _git(repo_root: Path, *args: str) -> tuple[int, str, str]:
    """Run one local git command. Returns ``(returncode, stdout, stderr)``; never raises."""
    try:
        p = subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True, text=True,
                           timeout=15)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as exc:  # noqa: BLE001 - git missing / not executable / timeout
        return 127, "", f"{type(exc).__name__}: {exc}"


_COMMIT_CACHE: dict[tuple[str, str], str] = {}


def file_commit(repo_root: str | Path, rel_path: str, cache: bool = True) -> str:
    """Last commit that touched ``rel_path``, as ``"<short sha> <commit date>"`` (D46).

    Used to print *which* code a loaded module corresponds to: after a pull, a module cached in
    ``sys.modules`` still carries the commit that was current when it was imported. Local only;
    returns ``"unknown"`` when git or the file is unavailable.
    """
    key = (str(repo_root), str(rel_path))
    if cache and key in _COMMIT_CACHE:
        return _COMMIT_CACHE[key]
    rc, out, _ = _git(Path(repo_root), "log", "-1", "--format=%h %cs", "--", str(rel_path))
    value = out if (rc == 0 and out) else "unknown"
    if cache:
        _COMMIT_CACHE[key] = value
    return value


def git_state(repo_root: str | Path) -> dict[str, Any]:
    """Local-only description of the checkout. Missing git/repo is reported, not raised."""
    root = Path(repo_root)
    state: dict[str, Any] = dict(
        git_available=False, sha=None, short_sha=None, branch=None, detached=False,
        dirty=None, shallow=None, upstream=None, ahead=None, behind=None, error="")

    rc, sha, err = _git(root, "rev-parse", "HEAD")
    if rc != 0 or not sha:
        state["error"] = err or f"git rev-parse HEAD failed (exit {rc})"
        return state
    state["git_available"] = True
    state["sha"] = sha

    rc, short, _ = _git(root, "rev-parse", "--short", "HEAD")
    state["short_sha"] = short or sha[:7]

    rc, branch, _ = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    state["branch"] = branch if (rc == 0 and branch) else None
    state["detached"] = state["branch"] is None

    rc, porcelain, _ = _git(root, "status", "--porcelain")
    state["dirty"] = bool(porcelain) if rc == 0 else None

    rc, shallow, _ = _git(root, "rev-parse", "--is-shallow-repository")
    state["shallow"] = (shallow == "true") if rc == 0 else None

    rc, upstream, _ = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if rc == 0 and upstream:
        state["upstream"] = upstream
        # `rev-list --left-right --count @{u}...HEAD` -> "<behind> <ahead>" (left = upstream only)
        rc, counts, err = _git(root, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
        if rc == 0 and counts:
            parts = counts.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                state["behind"], state["ahead"] = int(parts[0]), int(parts[1])
            else:
                state["error"] = f"unexpected rev-list output: {counts!r}"
        else:
            state["error"] = err or "could not compare HEAD with upstream"
    return state


# --------------------------------------------------------------------------- #
# capability check + checkout verdict
# --------------------------------------------------------------------------- #
def check_capabilities(repo_root: str | Path) -> list[dict[str, Any]]:
    """Which D43 capabilities are present in the files on disk."""
    root = Path(repo_root)
    # D63: these modules were parked under src/legacy_ceiling_v1/ (D59). A capability is a property
    # of the code, not of its directory, so look next to this file first and fall back to the
    # historical src/ path. Without this the guard reports a clean checkout as missing D43/D44.
    here = Path(__file__).resolve().parent
    out: list[dict[str, Any]] = []
    for spec in REQUIRED_CAPABILITIES:
        path = root / spec["path"]
        if not path.exists():
            candidate = here / Path(spec["path"]).name
            if candidate.exists():
                path = candidate
        detail = ""
        ok = False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            ok = spec["needle"] in text
            detail = f"found {spec['needle']!r}" if ok else f"missing {spec['needle']!r}"
        except OSError as exc:
            detail = f"unreadable: {type(exc).__name__}: {exc}"
        out.append(dict(name=spec["label"], ok=ok, path=spec["path"], detail=detail))
    return out


def _header_lines(state: dict[str, Any]) -> list[str]:
    if not state.get("git_available"):
        err = state.get("error") or "git not available"
        return [
            f"commit unknown (git unavailable — continuing without a stale-code check)",
            "working tree: unknown (git unavailable)",
            "branch: unknown (git unavailable)",
            "shallow: unknown (git unavailable)",
            "no upstream ref; behind-check unavailable",
            f"git error: {err}",
        ]
    short = state.get("short_sha") or state.get("sha") or "unknown"
    clean = "unknown" if state.get("dirty") is None else ("dirty" if state["dirty"] else "clean")
    lines = [f"commit {short} ({clean})"]
    lines.append("branch: detached HEAD" if state.get("detached") else
                 f"branch {state.get('branch') or 'unknown'}")
    if state.get("shallow"):
        lines.append("shallow clone: yes — behind-check may be incomplete (warning)")
    else:
        lines.append("shallow clone: no")
    if state.get("upstream") and state.get("behind") is not None and state.get("ahead") is not None:
        lines.append(f"ahead {state['ahead']} / behind {state['behind']} vs {state['upstream']}")
    else:
        lines.append("no upstream ref; behind-check unavailable")
    return lines


def _stop_message(state: dict[str, Any], missing: list[str], behind: int | None) -> str:
    sha = state.get("short_sha") or state.get("sha") or "unknown"
    bits: list[str] = []
    if missing:
        bits.append("this run needs " + ", ".join(missing))
    if behind is not None and behind > 0:
        unit = "commit" if behind == 1 else "commits"
        bits.append(f"it is {behind} {unit} behind origin/main")
    detail = "; ".join(bits) if bits else "the checkout is incomplete"
    return f"STOP — your checkout is at {sha}; {detail}. Please pull and re-run."


def evaluate_checkout(repo_root: str | Path) -> dict[str, Any]:
    """Decide whether a pipeline run may start on this checkout.

    Blocked when a required D43 capability is absent or the checkout is behind origin/main. A dirty
    tree, a detached HEAD, a shallow clone or a missing git binary are warnings only: an offline
    laptop must still be able to run the pipeline.
    """
    state = git_state(repo_root)
    capabilities = check_capabilities(repo_root)
    missing = [c["name"] for c in capabilities if not c["ok"]]
    behind = state.get("behind")
    blocked = bool(missing) or (behind is not None and behind > 0)
    header = _header_lines(state)
    stop = _stop_message(state, missing, behind) if blocked else ""
    return dict(ok=not blocked, blocked=blocked, state=state, capabilities=capabilities,
                missing=missing, behind=behind, header_lines=header, stop_message=stop)


# --------------------------------------------------------------------------- #
# stage-result helpers (used by the notebook)
# --------------------------------------------------------------------------- #
def _read_marker(out_dir: Path) -> dict[str, Any] | None:
    """The stage's own checkpoint marker (``_DONE.json`` / legacy ``_DONE``), or None."""
    for name in ("_DONE.json", "_DONE"):
        p = out_dir / name
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not text:
            return {}
        if text.startswith("{"):
            try:
                rec = json.loads(text)
            except ValueError:
                return None
            return rec if isinstance(rec, dict) else None
        return {"mode": text}
    return None


def stage_status_on_disk(stage: str, outputs_dir: str | Path) -> str:
    """Status for a stage that did **not** run in this session, read from its own files.

    Used by the notebook's ``stage_status_text`` so a ``RUN_ONLY`` re-run (e.g. only
    ``07_security_models``) reports the checkpointed stages honestly instead of calling them
    "not run" — a stage that was merely skipped is never labelled FAILED or PENDING.
    """
    sub, artifacts = STAGE_OUTPUTS.get(stage, ("", ()))
    out = Path(outputs_dir) / sub if sub else Path(outputs_dir)
    present = [a for a in artifacts if (out / a).exists()]
    marker = _read_marker(out) if out.is_dir() else None
    if marker:
        mode = marker.get("mode") or "?"
        prov = marker.get("provenance") or "?"
        return (f"{prov} checkpoint on disk (not re-run in this session; marker mode={mode})")
    if present:
        return (f"outputs on disk ({len(present)}/{len(artifacts)} expected files; no checkpoint "
                f"marker; not re-run in this session)")
    if stage == "00b_external_proxies":
        return "not re-run in this session (retired no-op stage, D43)"
    return "not re-run in this session and no outputs on disk"


def headline_is_full(mode: str, thresholds_provenance: Any, security_provenance: Any,
                     synthetic: bool, checkout_blocked: bool = False,
                     b_row_status: Any = "") -> bool:
    """The §8 FULL gate: driven **only** by provenance/checkout state.

    Deliberately takes no ``RUN_RESULTS``: a ``RUN_ONLY=["07_security_models"]`` re-run must be
    able to print a live headline block when the on-disk provenance inputs are FULL.
    """
    return (str(mode) == "full"
            and str(thresholds_provenance) == "FULL"
            and str(security_provenance) == "FULL"
            and not bool(synthetic)
            and not bool(checkout_blocked)
            and str(b_row_status or "").strip().upper() != "FAILED")


def classify_stage(res: dict[str, Any], game_side: bool) -> str | None:
    """None for success; FAILED for an exception; BLOCKED for a non-zero exit; else PENDING."""
    if res.get("ok"):
        return None
    if res.get("error"):
        return "FAILED"
    rc = int(res.get("rc") or 0)
    if rc != 0:
        return f"BLOCKED (exit {rc})"
    return "PENDING"


def failure_lines(res: dict[str, Any], max_tail: int = 10) -> list[str]:
    """A short, honest crash report: the exception line first, then the tail of the captured log.

    The full traceback is written to ``crash_<stamp>.txt`` (D46). If the captured tail is empty the
    line says so *and* names the file, instead of ending on a bare ``traceback tail:``.
    """
    tail = [str(x) for x in (res.get("tail") or [])][-max(0, int(max_tail)):]
    crash_file = res.get("crash_file")
    if res.get("error"):
        lines = [f"crashed: {res['error']}"]
        if tail:
            lines.append("traceback tail:")
        elif crash_file:
            lines.append(f"traceback tail: (empty — see {crash_file})")
        else:
            lines.append("traceback tail: (empty — no crash file was written)")
    else:
        lines = ["stage exited without an exception; tail:"]
    lines += tail
    if crash_file:
        lines.append(f"full traceback: {crash_file}")
    return lines


def diagnose_missing_primary(run_result: dict[str, Any] | None, full_game_ok: bool,
                             b_primary_row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explain *why* there is no quotable primary result — and never blame missing data for a crash."""
    if b_primary_row is not None and str(b_primary_row.get("block_status", "")).upper() == "FAILED":
        reason = str(b_primary_row.get("reason") or b_primary_row.get("note")
                     or "primary block B failed")
        return dict(status="FAILED", reason=f"primary block B failed: {reason}",
                    action=_CRASH_REMEDY,
                    tail_lines=failure_lines(run_result) if run_result is not None else [])

    if run_result is not None:
        if run_result.get("error"):
            return dict(status="FAILED", reason=f"stage crashed: {run_result['error']}",
                        action=_CRASH_REMEDY,
                        tail_lines=failure_lines(run_result))
        rc = int(run_result.get("rc") or 0)
        if not run_result.get("ok") and rc != 0:
            return dict(status="BLOCKED",
                        reason=f"stage exited without an exception (exit {rc})",
                        action="resolve the stage blocker shown above",
                        tail_lines=failure_lines(run_result))
        if run_result.get("ok"):
            if full_game_ok:
                return dict(status="PENDING FULL RUN",
                            reason="stage completed but wrote no b_primary.csv",
                            action="run in MODE='full' with FULL thresholds and inspect §3",
                            tail_lines=[])
            return dict(status="PENDING FULL RUN",
                        reason="stage completed but wrote no b_primary.csv; the game-tracking "
                               "files are absent",
                        action=_DATA_REMEDY, tail_lines=[])

    if not full_game_ok:
        return dict(status="PENDING FULL RUN",
                    reason="the game-tracking files are absent, so 07_security_models cannot run",
                    action=_DATA_REMEDY, tail_lines=[])
    return dict(status="NOT RUN", reason="07_security_models has not run in this session",
                action="execute §3 (Run All) and re-read §8", tail_lines=[])
