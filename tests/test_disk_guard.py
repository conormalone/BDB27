"""Disk guard: fails loudly with actual MB, and measures volumes separately."""

from __future__ import annotations

import copy
import os

import pytest

import lc_common as C


def test_guard_passes_with_real_thresholds(cfg):
    paths = C.resolve_paths(cfg, "sample")
    measured = C.assert_disk_space(paths, cfg)
    assert measured["root_free_mb"] > 0
    assert measured["interim_free_mb"] > 0
    assert set(measured) == {"root_free_mb", "interim_free_mb"}


def test_guard_reports_actual_numbers_when_root_is_impossible(cfg):
    bad = copy.deepcopy(cfg)
    bad["bdb27_lc"]["runtime"]["min_free_mb_root"] = 10 ** 9
    with pytest.raises(RuntimeError) as err:
        C.assert_disk_space(C.resolve_paths(cfg, "sample"), bad)
    message = str(err.value)
    assert "DISK GUARD FAILED" in message
    assert "root filesystem" in message
    assert "MB free" in message
    assert "1000000000 MB" in message


def test_guard_reports_actual_numbers_when_interim_is_impossible(cfg):
    bad = copy.deepcopy(cfg)
    bad["bdb27_lc"]["runtime"]["min_free_mb_interim"] = 10 ** 9
    with pytest.raises(RuntimeError) as err:
        C.assert_disk_space(C.resolve_paths(cfg, "sample"), bad)
    message = str(err.value)
    assert "interim volume" in message
    assert "MB free" in message


def test_guard_lists_both_failures(cfg):
    bad = copy.deepcopy(cfg)
    bad["bdb27_lc"]["runtime"]["min_free_mb_root"] = 10 ** 9
    bad["bdb27_lc"]["runtime"]["min_free_mb_interim"] = 10 ** 9
    with pytest.raises(RuntimeError) as err:
        C.assert_disk_space(C.resolve_paths(cfg, "sample"), bad)
    message = str(err.value)
    assert "root filesystem" in message and "interim volume" in message


def test_root_and_interim_are_different_volumes_on_this_box(cfg):
    paths = C.resolve_paths(cfg, "sample")
    if not paths["interim"].exists():
        pytest.skip("interim volume not mounted")
    root_dev = os.stat(C.REPO_ROOT).st_dev
    interim_dev = os.stat(paths["interim"]).st_dev
    if root_dev == interim_dev:
        pytest.skip("interim is not a separate device in this environment")
    measured = C.assert_disk_space(paths, cfg)
    # Different volumes must not be measured through the same mount.
    assert measured["interim_free_mb"] != measured["root_free_mb"]
