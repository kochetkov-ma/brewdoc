"""Output transaction: validate every target, stage all payloads, then replace or restore."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import unicodedata
from pathlib import Path

from brewdoc.common import BrewdocError


def _output_targets(source: Path, out,
                    pairs: tuple[tuple[str, Path, str], ...]) -> tuple[Path, ...]:
    """Resolve Markdown then artifact targets; refuse source aliases, collisions, bad targets."""
    named = (((None, Path(out), str(out)),) if out is not None else ()) + pairs
    seen = []
    for key, path, shown in named:
        target = path.parent.resolve() / path.name
        if _same_file(source, target):
            raise BrewdocError("Markdown output aliases the source: %s" % shown if key is None
                               else "artifact output aliases the source: %s=%s" % (key, shown))
        # Casefold + NFC on every filesystem: one plan must not depend on where it runs.
        folded = unicodedata.normalize("NFC", str(target)).casefold()
        for earlier_key, earlier, earlier_folded in seen:
            if earlier_folded == folded or _same_file(earlier, target):
                raise BrewdocError("Markdown and artifact outputs collide: %s" % shown
                                   if earlier_key is None else
                                   "artifact outputs collide: %s and %s" % (earlier_key, key))
        if os.path.lexists(target) and not stat.S_ISREG(target.lstat().st_mode):
            raise BrewdocError("output target is not a regular file: %s" % path)
        parent = next((folder for folder in target.parents if folder.exists()),
                      target.parents[-1])
        if not parent.is_dir():
            raise BrewdocError("output parent is not a directory: %s" % parent)
        seen.append((key, target, folded))
    return tuple(target for _key, target, _folded in seen)


def _same_file(first: Path, second: Path) -> bool:
    return first.exists() and second.exists() and os.path.samefile(first, second)


def _stage(target: Path, payload: bytes, stages: list[Path]) -> Path:
    """Write and fsync payload to a new stage beside target with its mode; record it in stages."""
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(".brewdoc-stage-" + secrets.token_hex(8))
    stream = open(stage, "xb")
    stages.append(stage)
    with stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if target.exists():
        shutil.copymode(target, stage)
    return stage


def _restore(targets: list[Path], originals: list[bytes | None], stages: list[Path]) -> list[str]:
    """Put original bytes back or delete new targets; return the ones that failed."""
    failures = []
    for target, original in zip(targets, originals):
        try:
            if original is None:
                target.unlink()
            else:
                os.replace(_stage(target, original, stages), target)
        except OSError as exc:
            failures.append("%s (%s)" % (target, exc))
    return failures


def _write_outputs(outputs: tuple[tuple[Path, bytes], ...]) -> None:
    """Stage every payload, then replace targets in order; a failed replace restores earlier ones."""
    targets = [target for target, _payload in outputs]
    stages: list[Path] = []
    try:
        originals = [target.read_bytes() if target.exists() else None for target in targets]
        staged = [_stage(target, payload, stages) for target, payload in outputs]
        for index, (target, stage) in enumerate(zip(targets, staged)):
            try:
                os.replace(stage, target)
            except OSError as exc:
                reason = "could not replace %s: %s" % (target, exc)
                failures = _restore(targets[:index], originals, stages)
                if failures:
                    reason += "; could not restore %s" % ", ".join(failures)
                raise BrewdocError(reason) from exc
    finally:
        for leftover in stages:
            leftover.unlink(missing_ok=True)
