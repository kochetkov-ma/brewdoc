import itertools
import json
import os
import stat
from pathlib import Path

import pytest

import brewdoc
from brewdoc.cli import main

from test_spreadsheets import FORMULA_KEYS, formula_book, refused


def tree_state(root: Path) -> dict:
    """Map each path under root to its bytes and permission bits; directories map to None."""
    return {
        item.relative_to(root).as_posix():
            (item.read_bytes(), stat.S_IMODE(item.lstat().st_mode)) if item.is_file() else None
        for item in sorted(root.rglob("*"))
    }


def new_file_mode(directory: Path) -> int:
    """Measure the mode a fresh 0666 file receives under the current umask."""
    control = directory / "mode-control"
    control.touch(mode=0o666)
    return stat.S_IMODE(control.stat().st_mode)


def observable_mode(posix_mode: int) -> int:
    """Return the permission bits os.stat reports after chmod(posix_mode) on this platform."""
    # Windows chmod only toggles read-only, so stat reports 0o666 or 0o444 there.
    windows_mode = 0o666 if posix_mode & stat.S_IWRITE else 0o444
    return windows_mode if os.name == "nt" else posix_mode


@pytest.mark.parametrize(
    ("artifact_outputs", "reason"),
    [
        (["formula/sheet/000002"], "malformed artifact assignment: formula/sheet/000002"),
        (["missing/key=missing.json"], "unknown artifact key: missing/key"),
        (["formula/sheet/000002=a.json", "formula/sheet/000002=b.json"],
         "duplicate artifact key: formula/sheet/000002"),
    ],
)
def test_invalid_artifact_requests_fail_before_any_write(
        tmp_path, monkeypatch, capsys, artifact_outputs, reason):
    # GIVEN an invalid CLI artifact request resolved inside tmp_path and a valid Markdown destination
    monkeypatch.chdir(tmp_path)
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)
    # WHEN the request is validated
    code = main([str(path), "--out", str(tmp_path / "book.md"), "--sheet", "Calc",
                 *(f"--artifact={item}" for item in artifact_outputs)])
    # THEN stdout is the one exact refusal receipt line and nothing is written
    assert (code, capsys.readouterr().out, tree_state(tmp_path)) == (
        1, json.dumps(refused(reason), ensure_ascii=True, sort_keys=True) + "\n", before,
    ), "invalid artifact requests must be refused before any write"


@pytest.mark.parametrize(
    ("files", "dirs", "links", "out", "artifacts", "reason"),
    [
        pytest.param((), (), (), "book.xlsx", ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-is-source"),
        pytest.param((), ("sub",), (), "sub/../book.xlsx", ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-spelling"),
        pytest.param((), (), (("symlink_to", "alias.md", "book.xlsx"),), "alias.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-symlink"),
        pytest.param((), (), (("hardlink_to", "alias.md", "book.xlsx"),), "alias.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "Markdown output aliases the source: {out}", id="markdown-source-hardlink"),
        pytest.param((), (), (), "book.md", ((FORMULA_KEYS[0], "book.xlsx"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-is-source"),
        pytest.param((), ("sub",), (), "book.md", ((FORMULA_KEYS[0], "sub/../book.xlsx"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-spelling"),
        pytest.param((), (), (("symlink_to", "alias.json", "book.xlsx"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-symlink"),
        pytest.param((), (), (("hardlink_to", "alias.json", "book.xlsx"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "artifact output aliases the source: formula/sheet/000001={artifacts[0]}",
                     id="artifact-source-hardlink"),
        pytest.param((), (), (), "same.out", ((FORMULA_KEYS[0], "same.out"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="markdown-artifact-same-path"),
        pytest.param(("book.md",), (), (("hardlink_to", "alias.json", "book.md"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="markdown-artifact-hardlink"),
        pytest.param(("book.md",), (), (("symlink_to", "alias.json", "book.md"),), "book.md",
                     ((FORMULA_KEYS[0], "alias.json"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="markdown-artifact-symlink"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-same-path"),
        pytest.param((), ("sub",), (), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "sub/../calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-spelling"),
        pytest.param(("calc.json",), (), (("hardlink_to", "alias.json", "calc.json"),),
                     "book.md", ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "alias.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-hardlink"),
        pytest.param(("calc.json",), (), (("symlink_to", "alias.json", "calc.json"),),
                     "book.md", ((FORMULA_KEYS[0], "calc.json"), (FORMULA_KEYS[1], "alias.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="artifact-artifact-symlink"),
        pytest.param((), (), (), "book.md", ((FORMULA_KEYS[0], "BOOK.md"),),
                     "Markdown and artifact outputs collide: {artifacts[0]}",
                     id="casefold-markdown-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "Calc.json"), (FORMULA_KEYS[1], "calc.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="casefold-artifact-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "café.json"), (FORMULA_KEYS[1], "café.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="nfc-artifact-artifact"),
        pytest.param((), (), (), "book.md",
                     ((FORMULA_KEYS[0], "CAFÉ.json"), (FORMULA_KEYS[1], "café.json")),
                     "artifact outputs collide: formula/sheet/000001 and formula/sheet/000002",
                     id="nfc-casefold-artifact-artifact"),
        pytest.param((), ("book.md",), (), "book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {out}", id="markdown-directory"),
        pytest.param((), ("calc.json",), (), "book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {artifacts[0]}",
                     id="artifact-directory"),
        pytest.param(("real.md",), (), (("symlink_to", "book.md", "real.md"),), "book.md",
                     ((FORMULA_KEYS[0], "calc.json"),),
                     "output target is not a regular file: {out}", id="markdown-symlink"),
        pytest.param(("parent",), (), (), "parent/book.md", ((FORMULA_KEYS[0], "calc.json"),),
                     "output parent is not a directory: {out.parent}",
                     id="markdown-parent-is-file"),
    ],
)
def test_invalid_output_plans_are_refused_before_any_write(
        tmp_path, files, dirs, links, out, artifacts, reason):
    # GIVEN a workbook, prepared filesystem state, and an output plan that aliases, collides
    # by spelling, NFC casefold, hardlink or symlink, or names a non-regular target
    path = formula_book(tmp_path / "book.xlsx")
    for name in files:
        (tmp_path / name).write_bytes(b"old " + name.encode("ascii"))
    for name in dirs:
        (tmp_path / name).mkdir()
    for method, link, target in links:
        getattr(tmp_path / link, method)(tmp_path / target)
    markdown_out = tmp_path / out
    outputs = [(key, tmp_path / name) for key, name in artifacts]
    before = tree_state(tmp_path)
    # WHEN the plan is requested through the public run path
    actual = brewdoc.run(path, markdown_out, artifact_outputs=dict(outputs))
    # THEN it is refused with the exact reason and no file or stage changes
    assert (actual, tree_state(tmp_path)) == (
        (1, refused(reason.format(out=markdown_out, artifacts=[item for _key, item in outputs])),
         ""), before,
    ), "invalid output plans must be refused before any byte is written"


def existing_targets(tmp_path: Path) -> list[Path]:
    """Return Markdown and artifact targets: two existing with their own modes, one new."""
    targets = [tmp_path / "book.md", tmp_path / "first.json", tmp_path / "later.json"]
    for target, data, mode in zip(targets, (b"old markdown", b"old artifact"), (0o640, 0o600)):
        target.write_bytes(data)
        target.chmod(mode)
    return targets


@pytest.mark.parametrize("failing_call", [1, 2, 3])
def test_failed_replace_restores_earlier_targets_and_removes_new_ones(
        tmp_path, monkeypatch, failing_call):
    # GIVEN existing and new targets and os.replace failing only on the Nth call
    path = formula_book(tmp_path / "book.xlsx")
    targets = existing_targets(tmp_path)
    before = tree_state(tmp_path)

    def fail(_source, _target):
        raise OSError("injected replace failure")

    steps = itertools.chain(itertools.repeat(os.replace, failing_call - 1), [fail],
                            itertools.repeat(os.replace))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    # WHEN publishing fails part way through
    actual = brewdoc.run(path, targets[0], artifact_outputs=dict(zip(FORMULA_KEYS, targets[1:])))
    # THEN originals keep bytes and modes, the new target is absent, and no stage remains
    assert (actual, tree_state(tmp_path)) == ((1, refused(
        "output write failed: could not replace %s: injected replace failure"
        % targets[failing_call - 1]), ""), before,
    ), "a failed replace must restore every target and name the failed one"


def test_failed_restore_after_failed_replace_names_every_unrestored_target(
        tmp_path, monkeypatch):
    # GIVEN existing and new targets and os.replace failing from the third target onwards
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out, first, later = existing_targets(tmp_path)
    markdown = brewdoc.run(path)[2]
    before = tree_state(tmp_path)

    def fail(_source, _target):
        raise OSError("injected failure")

    steps = itertools.chain(itertools.repeat(os.replace, 2), itertools.repeat(fail))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    # WHEN the third replace and both restores fail
    actual = brewdoc.run(path, markdown_out, artifact_outputs=dict(zip(FORMULA_KEYS, (first, later))))
    # THEN run fails, names every unrestored target, and leaves new bytes but no stage
    assert (actual, tree_state(tmp_path)) == ((1, refused(
        "output write failed: could not replace %s: injected failure; could not restore "
        "%s (injected failure), %s (injected failure)" % (later, markdown_out, first)), ""), {
        **before, "book.md": (markdown.encode("ascii"), observable_mode(0o640)),
        "first.json": (brewdoc.read_book_artifact(path, FORMULA_KEYS[0]), observable_mode(0o600)),
    }), "an unrestored target must be reported, never silently left replaced"


def test_staging_failure_fails_with_a_receipt_and_leaves_no_file(tmp_path, monkeypatch):
    # GIVEN three new outputs and os.fsync failing while the second stage is written
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)

    def fail(_descriptor):
        raise OSError("injected staging failure")

    steps = itertools.chain([os.fsync], [fail], itertools.repeat(os.fsync))
    monkeypatch.setattr(os, "fsync", lambda descriptor: next(steps)(descriptor))
    # WHEN staging fails after one payload was prepared
    actual = brewdoc.run(path, tmp_path / "book.md", artifact_outputs=dict(
        zip(FORMULA_KEYS, (tmp_path / "a.json", tmp_path / "b.json"))))
    # THEN the receipt reports the failure and neither outputs nor stages exist
    assert (actual, tree_state(tmp_path)) == (
        (1, refused("output write failed: injected staging failure"), ""), before,
    ), "a staging failure must leave the directory exactly as it was"


def test_keyboard_interrupt_during_staging_leaves_no_file(tmp_path, monkeypatch):
    # GIVEN three new outputs and an interrupt while the second stage is written
    path = formula_book(tmp_path / "book.xlsx")
    before = tree_state(tmp_path)

    def interrupt(_descriptor):
        raise KeyboardInterrupt

    steps = itertools.chain([os.fsync], [interrupt], itertools.repeat(os.fsync))
    monkeypatch.setattr(os, "fsync", lambda descriptor: next(steps)(descriptor))
    # WHEN the interrupt propagates out of run
    with pytest.raises(KeyboardInterrupt):
        brewdoc.run(path, tmp_path / "book.md", artifact_outputs=dict(
            zip(FORMULA_KEYS, (tmp_path / "a.json", tmp_path / "b.json"))))
    # THEN no output or .brewdoc-* stage file remains
    assert tree_state(tmp_path) == before, "an interrupt must not leak stages or partial outputs"


def test_successful_run_writes_exact_bytes_keeps_modes_and_leaves_no_stage(tmp_path):
    # GIVEN existing Markdown and artifact targets with their own modes and one new target
    path = formula_book(tmp_path / "book.xlsx")
    markdown_out, first, later = existing_targets(tmp_path)
    created_mode = new_file_mode(tmp_path)
    before = tree_state(tmp_path)
    # WHEN all three are published
    code, receipt, markdown = brewdoc.run(
        path, markdown_out, artifact_outputs=dict(zip(FORMULA_KEYS, (first, later))))
    # THEN bytes are exact, existing modes survive, the new file follows 0666 & ~umask
    assert (code, receipt["file_ok"], tree_state(tmp_path)) == (0, True, {
        **before,
        "book.md": (markdown.encode("ascii"), observable_mode(0o640)),
        "first.json": (brewdoc.read_book_artifact(path, FORMULA_KEYS[0]), observable_mode(0o600)),
        "later.json": (brewdoc.read_book_artifact(path, FORMULA_KEYS[1]), created_mode),
    }), "success must publish exact bytes with preserved or umask modes and no stage files"


def test_parent_symlink_retarget_cannot_redirect_publish_or_leak_stage(tmp_path, monkeypatch):
    # GIVEN an output parent symlink to directory A that is retargeted to B before publish
    path = formula_book(tmp_path / "book.xlsx")
    first_parent, second_parent = tmp_path / "dir-a", tmp_path / "dir-b"
    first_parent.mkdir()
    second_parent.mkdir()
    created_mode = new_file_mode(tmp_path)
    link = tmp_path / "out"
    link.symlink_to(first_parent, target_is_directory=True)

    def retarget_then_replace(source, target):
        link.unlink()
        link.symlink_to(second_parent, target_is_directory=True)
        return real_replace(source, target)

    real_replace = os.replace
    steps = itertools.chain([retarget_then_replace], itertools.repeat(real_replace))
    monkeypatch.setattr(os, "replace", lambda source, target: next(steps)(source, target))
    # WHEN the Markdown is published through the lexical symlink path
    code, _receipt, markdown = brewdoc.run(path, link / "book.md")
    # THEN the parent captured before staging receives it and neither directory keeps a stage
    assert (code, tree_state(first_parent), tree_state(second_parent)) == (
        0, {"book.md": (markdown.encode("ascii"), created_mode)}, {},
    ), "publish must use the real parent captured before staging"
