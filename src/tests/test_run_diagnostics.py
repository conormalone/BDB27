#!/usr/bin/env python3
"""Contract tests for ``src/run_diagnostics.py`` — stale-code guard + honest §8 failure wording.

The notebook must never run a stale checkout silently and must never confuse a crashed stage with
missing data. These tests build throw-away git repositories in ``/tmp`` (no network: local clone and
locally-created remote refs only) and assert the diagnostics module's promises.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
import run_diagnostics as rd  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "BDB27 Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "BDB27 Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

SEC_OK = (
    "def _safe_exp(x):\n"
    "    return x\n\n"
    "def sanitize_covariates(table, cfg, issues=None):\n"
    "    return 0\n\n"
    "def emit(rows):\n"
    "    quality = 'covariate_quality'\n"
    "    return [dict(block_status='OK', covariate_quality=quality) for _ in rows]\n"
)
CONFIG_OK = ("security_models:\n  e:\n    or_exponent_clip: 700.0\n"
             "  primary:\n    covariate_bounds:\n      height_in: [48.0, 90.0]\n")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=check)


def _seed_files(repo: Path, *, include_block_status: bool = True) -> None:
    (repo / "src").mkdir(parents=True, exist_ok=True)
    src = SEC_OK if include_block_status else "def _safe_exp(x):\n    return x\n"
    (repo / "src" / "07_security_models.py").write_text(src, encoding="utf-8")
    (repo / "src" / "model_features.py").write_text(SEC_OK, encoding="utf-8")
    (repo / "config.yaml").write_text(CONFIG_OK, encoding="utf-8")


def _init_repo(path: Path, *, include_block_status: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(path, "config", "user.name", "BDB27 Test")
    _git(path, "config", "user.email", "test@example.invalid")
    _seed_files(path, include_block_status=include_block_status)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _commit(path: Path, name: str, text: str) -> str:
    (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", f"add {name}")
    return _git(path, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------- #
# git_state / evaluate_checkout
# --------------------------------------------------------------------------- #
def test_capability_missing_blocks_with_sha_label_and_action():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_cap_"))
    repo = _init_repo(tmp / "repo", include_block_status=False)
    ev = rd.evaluate_checkout(repo)

    assert ev["blocked"] is True, ev
    assert "per-block labelled failures (D43)" in ev["missing"], ev["missing"]
    assert ev["state"]["short_sha"] and ev["state"]["short_sha"] in ev["stop_message"], ev
    low = ev["stop_message"].lower()
    assert "per-block labelled failures (d43)" in low, ev["stop_message"]
    assert "pull and re-run" in low, ev["stop_message"]
    assert "STOP" in ev["stop_message"], ev["stop_message"]


def test_all_capabilities_present_is_not_blocked():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_ok_"))
    repo = _init_repo(tmp / "repo")
    ev = rd.evaluate_checkout(repo)
    assert ev["blocked"] is False, ev
    assert ev["missing"] == [], ev["missing"]
    assert any("commit " in ln for ln in ev["header_lines"]), ev["header_lines"]


def test_behind_origin_main_blocks_without_network():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_behind_"))
    repo = _init_repo(tmp / "repo")
    first = _git(repo, "rev-parse", "HEAD").stdout.strip()
    second = _commit(repo, "second.txt", "later\n")
    _git(repo, "remote", "add", "origin", str(repo))
    _git(repo, "update-ref", "refs/remotes/origin/main", second)
    _git(repo, "reset", "--hard", first)
    _git(repo, "branch", "--set-upstream-to=origin/main", "main")

    st = rd.git_state(repo)
    assert st["behind"] == 1 and st["ahead"] == 0, st

    ev = rd.evaluate_checkout(repo)
    assert ev["blocked"] is True, ev
    assert "1 commits behind origin/main" in ev["stop_message"] or \
           "1 commit behind origin/main" in ev["stop_message"], ev["stop_message"]
    assert "pull and re-run" in ev["stop_message"].lower(), ev["stop_message"]


def test_dirty_tree_is_a_warning_not_a_block():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_dirty_"))
    repo = _init_repo(tmp / "repo")
    (repo / "config.yaml").write_text(CONFIG_OK + "# dirty\n", encoding="utf-8")
    ev = rd.evaluate_checkout(repo)
    assert ev["state"]["dirty"] is True, ev["state"]
    assert ev["blocked"] is False, ev
    assert any("dirty" in ln.lower() for ln in ev["header_lines"]), ev["header_lines"]


def test_detached_head_is_a_warning_not_a_block():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_detached_"))
    repo = _init_repo(tmp / "repo")
    _git(repo, "checkout", "-q", "--detach")
    ev = rd.evaluate_checkout(repo)
    assert ev["state"]["detached"] is True, ev["state"]
    assert ev["state"]["branch"] is None, ev["state"]
    assert ev["blocked"] is False, ev
    assert any("detached" in ln.lower() for ln in ev["header_lines"]), ev["header_lines"]


def test_plain_directory_without_git_continues():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_nogit_"))
    _seed_files(tmp)
    ev = rd.evaluate_checkout(tmp)
    assert ev["state"]["git_available"] is False, ev["state"]
    assert ev["state"]["sha"] is None, ev["state"]
    assert ev["blocked"] is False, ev
    assert "unknown" in " ".join(ev["header_lines"]).lower(), ev["header_lines"]


def test_shallow_clone_is_reported_not_blocked():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_shallow_"))
    src = _init_repo(tmp / "src")
    _commit(src, "later.txt", "later\n")
    clone = tmp / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{src}", str(clone)],
                   check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    ev = rd.evaluate_checkout(clone)
    assert ev["state"]["git_available"] is True, ev["state"]
    assert ev["state"]["shallow"] is True, ev["state"]
    assert ev["blocked"] is False, ev
    assert any("shallow" in ln.lower() for ln in ev["header_lines"]), ev["header_lines"]


def test_no_upstream_means_behind_unavailable_but_not_blocked():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_noup_"))
    repo = _init_repo(tmp / "repo")
    st = rd.git_state(repo)
    assert st["upstream"] is None, st
    assert st["behind"] is None and st["ahead"] is None, st
    ev = rd.evaluate_checkout(repo)
    assert ev["blocked"] is False, ev
    assert any("no upstream" in ln.lower() for ln in ev["header_lines"]), ev["header_lines"]


# --------------------------------------------------------------------------- #
# classify_stage / failure_lines
# --------------------------------------------------------------------------- #
def test_classify_stage():
    assert rd.classify_stage({"ok": True, "rc": 0, "error": ""}, "WR") is None
    assert rd.classify_stage({"ok": False, "rc": 1, "error": "ValueError: boom"}, "WR") == "FAILED"
    assert rd.classify_stage({"ok": False, "rc": 2, "error": ""}, "WR") == "BLOCKED (exit 2)"
    assert rd.classify_stage({"ok": False, "rc": 0, "error": ""}, "WR") == "PENDING"


def test_failure_lines_surface_crash_and_tail():
    res = {"ok": False, "rc": 1, "error": "OverflowError: math range error",
           "tail": ["log 1", "Traceback (most recent call last):", "  ...", "OverflowError: math range error"]}
    lines = rd.failure_lines(res, max_tail=3)
    assert lines[0] == "crashed: OverflowError: math range error", lines
    assert lines[1] == "traceback tail:", lines
    assert lines[2:] == res["tail"][-3:], lines


def test_failure_lines_for_blocked_stage_without_exception():
    lines = rd.failure_lines({"ok": False, "rc": 2, "error": "", "tail": ["blocker a", "blocker b"]})
    assert lines[0] == "stage exited without an exception; tail:", lines
    assert lines[1:] == ["blocker a", "blocker b"], lines


# --------------------------------------------------------------------------- #
# diagnose_missing_primary — never a data-supply remedy for a crash
# --------------------------------------------------------------------------- #
def test_diagnose_b_primary_failed_row():
    d = rd.diagnose_missing_primary(None, True, b_primary_row={
        "block_status": "FAILED", "reason": "OverflowError: math range error"})
    assert d["status"] == "FAILED", d
    assert "OverflowError" in d["reason"], d
    assert "supplying data again will not help" in d["action"], d


def test_diagnose_crash_is_failed_not_data_absent():
    d = rd.diagnose_missing_primary(
        {"ok": False, "rc": 1, "error": "RuntimeError: primary block B failed",
         "tail": ["Traceback ...", "RuntimeError: primary block B failed"]}, True)
    assert d["status"] == "FAILED", d
    assert "RuntimeError" in d["reason"], d
    assert "fix the exception" in d["action"], d
    assert "game_tracking" not in d["action"], d["action"]
    assert d["tail_lines"], d


def test_diagnose_blocked_stage():
    d = rd.diagnose_missing_primary(
        {"ok": False, "rc": 2, "error": "", "tail": ["freeze gate: prereg not FROZEN"]}, True)
    assert d["status"] == "BLOCKED", d
    assert "resolve the stage blocker" in d["action"], d


def test_diagnose_ok_stage_full_data_pending_full():
    d = rd.diagnose_missing_primary({"ok": True, "rc": 0, "error": "", "tail": []}, True)
    assert d["status"] == "PENDING FULL RUN", d
    assert "no b_primary.csv" in d["reason"], d
    assert "MODE='full'" in d["action"], d


def test_diagnose_ok_stage_missing_game_data_names_files():
    d = rd.diagnose_missing_primary({"ok": True, "rc": 0, "error": "", "tail": []}, False)
    assert d["status"] == "PENDING FULL RUN", d
    assert "game_tracking_2023/24/25.csv" in d["action"], d


def test_diagnose_not_run_is_not_a_data_remedy():
    d = rd.diagnose_missing_primary(None, True)
    assert d["status"] == "NOT RUN", d
    assert "execute §3" in d["action"], d
    assert "game_tracking" not in d["action"], d["action"]


# --------------------------------------------------------------------------- #
# stage_status_on_disk / headline_is_full (D45: RUN_ONLY single-stage re-runs)
# --------------------------------------------------------------------------- #
def test_stage_status_on_disk_reads_checkpoint_and_never_says_failed():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_disk_"))
    (tmp / "models").mkdir()
    (tmp / "models" / "models_summary.csv").write_text("status\nOK\n", encoding="utf-8")
    (tmp / "models" / "_DONE").write_text(
        '{"stage": "04_models", "mode": "full", "provenance": "FULL"}', encoding="utf-8")

    st = rd.stage_status_on_disk("04_models", tmp)
    assert "FULL checkpoint on disk" in st, st
    assert "not re-run in this session" in st, st
    for word in ("FAILED", "PENDING"):
        assert word not in st, st


def test_stage_status_on_disk_reports_outputs_without_marker():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_nomarker_"))
    (tmp / "security_models").mkdir()
    (tmp / "security_models" / "SUMMARY.json").write_text("{}", encoding="utf-8")
    st = rd.stage_status_on_disk("07_security_models", tmp)
    assert "outputs on disk" in st and "no checkpoint marker" in st, st
    assert "FAILED" not in st and "PENDING" not in st, st


def test_stage_status_on_disk_missing_stage_is_not_a_failure():
    tmp = Path(tempfile.mkdtemp(prefix="bdb27_diag_missing_"))
    st = rd.stage_status_on_disk("05_validate", tmp)
    assert "not re-run in this session" in st, st
    assert "FAILED" not in st and "PENDING" not in st, st


def test_headline_is_full_is_provenance_driven_not_session_driven():
    """A RUN_ONLY=["07_security_models"] re-run must still print a live headline block."""
    assert rd.headline_is_full("full", "FULL", "FULL", False) is True
    assert rd.headline_is_full("full", "FULL", "FULL", False, False, "OK") is True
    # every disqualifier is provenance/checkout state — never "did 03/04/05 run in this session"
    assert rd.headline_is_full("sample", "FULL", "FULL", False) is False
    assert rd.headline_is_full("full", "UNVALIDATED SAMPLE OUTPUT", "FULL", False) is False
    assert rd.headline_is_full("full", "FULL", "SYNTHETIC FIXTURE (not a finding)", False) is False
    assert rd.headline_is_full("full", "FULL", "FULL", True) is False
    assert rd.headline_is_full("full", "FULL", "FULL", False, True) is False
    assert rd.headline_is_full("full", "FULL", "FULL", False, False, "FAILED") is False


if __name__ == "__main__":
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", str(Path(__file__)),
                                     "-q"]))
