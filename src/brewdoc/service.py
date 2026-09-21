"""Service: suffix routes, `run` and its receipt line, output writes through `output`."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from brewdoc.common import MARKDOWN_SCHEMA, BrewdocError, new_tally
from brewdoc.docx import DOC_NOT_CARRIED, DOC_SUFFIX, _render_doc
from brewdoc.output import _output_targets, _write_outputs
from brewdoc.pdf import PDF_NOT_CARRIED, PDF_SUFFIX, _render_pdf
from brewdoc.sheets import (SHEET_NOT_CARRIED, SHEET_SUFFIXES, _artifact_payloads, _render_book,
                            _sheet_selection)

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

SUFFIXES = " ".join(sorted((PDF_SUFFIX, DOC_SUFFIX) + SHEET_SUFFIXES))


# Suffix -> (route, unit kind, private renderer, what the route structurally cannot carry).
ROUTES = {PDF_SUFFIX: ("pdf", "page", _render_pdf, PDF_NOT_CARRIED),
          DOC_SUFFIX: ("doc", "chapter", _render_doc, DOC_NOT_CARRIED),
          **dict.fromkeys(SHEET_SUFFIXES, ("sheet", "sheet", _render_book, SHEET_NOT_CARRIED))}
NO_ROUTE = ("none", "none", None, ())


def _route(path: Path) -> tuple:
    return ROUTES.get(path.suffix.lower(), NO_ROUTE)


def _line(route: tuple, file_ok: bool, reason: str, path, tally: dict | None = None,
          out=None, unit_keys: tuple[str, ...] = (), artifacts: tuple[dict, ...] = (),
          selected_sheets=None) -> dict:
    name, _unit_kind, _render, not_carried = route
    tally = tally or new_tally()
    line = {"file_ok": file_ok, "route": name, "reason": reason,
            "source": Path(path).name, "out": str(out) if out else None,
            "pages": tally["pages"], "sheets": tally["sheets"], "tables": tally["tables"],
            "text_regions": tally["text_regions"], "columns_split": tally["columns_split"],
            "dropped": dict(tally["dropped"]),
            "broken_ligature_words": tally["broken_ligature_words"],
            "not_carried": list(not_carried)}
    if file_ok:
        line.update({"artifacts": list(artifacts), "markdown_schema": MARKDOWN_SCHEMA,
                     "unit_keys": list(unit_keys)})
        if selected_sheets is not None:
            line["selected_sheets"] = list(selected_sheets)
    return line


def _artifact_output_pairs(artifact_outputs) -> tuple[tuple[str, Path, str], ...]:
    """Validate a key -> path mapping; each pair keeps the caller's path text for the receipt."""
    if artifact_outputs is None:
        return ()
    if not isinstance(artifact_outputs, Mapping):
        raise BrewdocError("artifact outputs must map keys to paths")
    pairs = []
    for key, raw_path in artifact_outputs.items():
        if not (isinstance(key, str) and key and isinstance(raw_path, (str, os.PathLike))
                and str(raw_path)):
            raise BrewdocError("malformed artifact assignment: %r" % ((key, raw_path),))
        pairs.append((key, Path(raw_path), str(raw_path)))
    return tuple(pairs)


def run(path, out=None, *, sheets=None, artifact_outputs=None) -> tuple[int, dict, str]:
    """(exit code, receipt, Markdown); every path returns a receipt, a refusal names what and where."""
    path = Path(path)
    route = _route(path)
    name, unit_kind, render, _not_carried = route
    if render is None:
        return EXIT_FAIL, _line(route, False, "unsupported suffix '%s' in %s: brewdoc reads %s"
                                % (path.suffix.lower(), path, SUFFIXES), path), ""
    if not path.is_file():
        return EXIT_FAIL, _line(route, False, "no such file: %s" % path, path), ""
    try:
        sheets = _sheet_selection(sheets)
        pairs = _artifact_output_pairs(artifact_outputs)
        if name != "sheet" and (sheets is not None or pairs):
            raise BrewdocError("--sheet and --artifact are workbook-only options")
        targets = _output_targets(path, out, pairs)
        markdown, tally, unit_keys, references = render(path, sheets)
        keys = tuple(key for key, _target, _shown in pairs)
        listed = {item.key for item in references}
        unknown = [key for key in keys if key not in listed]
        if unknown:
            raise BrewdocError("unknown artifact key: %s" % ", ".join(unknown))
        payloads = ((markdown.encode("ascii"),) if out is not None else ()) + _artifact_payloads(
            path, sheets, references, keys)
    except BrewdocError as exc:
        return EXIT_FAIL, _line(route, False, str(exc), path), ""
    except Exception as exc:                      # a third-party parser raises its own types
        return EXIT_FAIL, _line(route, False, "%s unreadable: %s: %s: %s"
                                % (name, path, type(exc).__name__, exc), path), ""
    try:
        _write_outputs(tuple(zip(targets, payloads)))
    except (BrewdocError, OSError) as exc:
        return EXIT_FAIL, _line(route, False, "output write failed: %s" % exc, path), ""
    reason = "%s rendered: %d %ss, %d tables, %d text regions, %d column splits" % (
        name, len(unit_keys), unit_kind, tally["tables"], tally["text_regions"],
        tally["columns_split"])
    shown = {key: text for key, _target, text in pairs}
    receipt_artifacts = tuple(item.to_dict(shown.get(item.key)) for item in references)
    return EXIT_OK, _line(route, True, reason, path, tally, out, unit_keys,
                          receipt_artifacts, sheets), markdown
