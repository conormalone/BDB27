# Spec — the notebook reloads the package on every Run All (D209)

**Status.** Binding for this cycle. Written before the notebook was touched.
**Surface.** `notebooks/bdb27_multi_candidate.ipynb`, second code cell.
**Constraint from Conor (2026-11-18).** He does not use a terminal. The notebook is
his only interface; no new CLI step, no "restart the kernel", no diagnostics to run
by hand.

## 0. The defect

On 2026-11-18 Conor pulled commit `a1cdb66` (which added `RUN_STATUS_NAME` to
`src/bdb27/ambiguity_attribution.py`), re-ran the notebook in the **same Jupyter
kernel** he had used before the pull, and got:

```
AttributeError: module 'bdb27.ambiguity_attribution' has no attribute 'RUN_STATUS_NAME'
```

The file on disk was correct. Python caches imports in `sys.modules`; the
notebook's `sys.path` guard only chooses where a *first* import looks. In a live
kernel, `from bdb27 import ambiguity_attribution as aa` returns the module object
built before the pull. A `sys.path` insert cannot fix that, and Conor cannot be
asked to restart the kernel.

This is the second pull-then-rerun failure of this kind. The notebook must be
self-healing against it.

## 1. Decision

The notebook's boot cell will **discard every cached `bdb27` module and import the
files on disk on every Run All**, print the file and checkout it loaded, and — if
a symbol the notebook needs is still absent — stop with a plain-English message
that names the file and says the checkout is older than the notebook.

The reload logic lives in a new stdlib-only module,
`src/bdb27/notebook_boot.py`, so it is testable outside Jupyter. The boot cell
loads that file **by path** (`importlib.util.spec_from_file_location`) before it
imports the package, so the helper itself cannot be the stale article it is meant
to fix.

## 2. Method

### 2.1 `src/bdb27/notebook_boot.py` (new, stdlib only)

*No imports from `bdb27`.* Exposes:

1. `checkout_label(root)` — branch and short HEAD read from `.git/HEAD`, the ref
   file, or `packed-refs` (worktree `gitdir:` files handled), with **no
   subprocess**. Returns `"unknown (no readable .git/HEAD)"` rather than raising
   when git metadata is absent.
2. `fresh_import(src_dir, names, root=None, package="bdb27")`:
   - resolves `src_dir`, removes any existing identical entry from `sys.path`,
     and inserts it at position 0 (the checkout the notebook located wins);
   - calls `importlib.invalidate_caches()`;
   - deletes **every** entry in `sys.modules` named `bdb27` or starting
     `bdb27.`;
   - imports each requested submodule in the order given and returns
     `{name: module}`;
   - prints, to stdout, one line per module with its resolved `__file__` and file
     mtime, one line with the checkout label and root, and one line saying how
     many cached modules were dropped (or that nothing was cached).
3. `require_symbols(modules, required, src_dir, root=None, package="bdb27")` —
   `required` is `{submodule: [attribute, ...]}`. Prints the check and, if any
   attribute is missing (or a module loaded from outside `src_dir`), prints
   `IMPORT CHECK FAILED` followed by the loaded file paths, the missing names,
   `"This checkout is OLDER THAN THE NOTEBOOK"`, and the instruction to pull and
   press Run All. Then raises `RuntimeError` carrying that same text. It never
   raises `AttributeError` and never falls back silently.

Import order: `outcome_test`, `geometric_match`, `ambiguity_attribution`.
`ambiguity_attribution` imports `outcome_test` at module load and imports
`geometric_match` lazily inside functions; importing both first keeps one
consistent set.

### 2.2 The boot cell

Replace the two `from bdb27 import ...` lines with:

1. the helper loaded by path from `SRC / "bdb27" / "notebook_boot.py"`
   (`SRC = ROOT / "src"`), with a plain-English `RuntimeError` if that file is
   absent (a copy of the notebook without the package is named as such);
2. `fresh_import(SRC, ["outcome_test", "geometric_match", "ambiguity_attribution"], root=ROOT)`;
3. bindings `aa`, `ot`, `gm` from the returned dict;
4. `require_symbols(...)` against the API the notebook actually calls: every
   `aa.<name>` the notebook uses, `ot.TURN_KEY`, and `gm.candidate_frame` /
   `gm.recover_break_frames`.

No other cell changes. The config cell is untouched.

### 2.3 What is *not* changed

No analysis call, no threshold, no printed number, no table, no artefact. The
no-tracking path still gives `N = 2,295` and the same fifteen tables. `TASK.md`,
`notes/report-draft.md`, the committed figures and the committed
`outputs/ambiguity_attribution/` set are untouched.

## 3. Acceptance criteria

1. **Reload, not reuse.** In a kernel where `sys.modules["bdb27.ambiguity_attribution"]`
   is an object without `RUN_STATUS_NAME`, one execution of the boot cell yields an
   `aa` whose `RUN_STATUS_NAME` exists and whose `__file__` resolves under
   `ROOT/src/bdb27`.
2. **Visible provenance.** The boot cell's output contains, before any analysis
   output, the resolved path of each of the three modules and a checkout line
   (branch @ short HEAD). A wrong-folder run is readable on screen.
3. **Plain-English staleness failure.** If a required symbol is missing after the
   reload, the raised error's message contains the loaded file path, the missing
   symbol name(s), and the phrase `OLDER THAN THE NOTEBOOK`. The exception is not
   an `AttributeError`.
4. **Numbers unchanged.** The full no-tracking Run All still reports `N = 2,295`
   with frozen 1,731 / geo 0 / 0 / Route A 564, writes the same fifteen tables,
   `manifest.json` and `run_status.txt`, and every existing notebook test still
   passes unmodified in behaviour.
5. **No new interface.** Nothing to run outside Run All; no shell magic, no
   `subprocess`, no `%` magic, no new config knob, committed notebook unexecuted.
6. **A test that would have caught this.** A test seeds `sys.modules` with a
   mutated `bdb27.ambiguity_attribution` (a bare `ModuleType`), runs the reload,
   and asserts the module now comes from the file on disk and carries
   `RUN_STATUS_NAME`; the same file also asserts the staleness message.

## 4. Tests

`tests/test_notebook_fresh_import.py`:

* `test_a_stale_module_in_sys_modules_is_replaced_by_the_file_on_disk` — the
  defect, simulated.
* `test_a_missing_symbol_fails_with_the_file_and_an_older_checkout_message`.
* `test_the_boot_cell_reports_file_and_checkout_and_binds_a_fresh_module` — runs
  the config + boot cells in a throwaway namespace with a poisoned
  `sys.modules`, asserts stdout carries the paths/checkout and `ns["aa"]` is the
  on-disk module.
* `test_the_boot_cell_does_not_import_the_package_the_old_way` — static guard:
  no `from bdb27 import ambiguity_attribution as aa` anywhere in the notebook;
  the boot cell calls `fresh_import` with all three modules before its first
  `aa.` use.

The existing `tests/test_multi_candidate_notebook.py` execution suite is the
regression proof for AC4.
