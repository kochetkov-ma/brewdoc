"""Console entry point: `brewdoc <document> [--out FILE]` or `brewdoc --self-check`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brewdoc.common import BrewdocError
from brewdoc.selfcheck import self_check
from brewdoc.service import EXIT_FAIL, EXIT_OK, EXIT_USAGE, SUFFIXES, _line, _route, run

RENDERED_BY = "brewdoc"


def _artifact_assignments(assignments) -> dict[str, str] | None:
    """Turn repeated CLI KEY=PATH values into the `run` mapping; refuse bad or repeated keys."""
    if assignments is None:
        return None
    mapping = {}
    for assignment in assignments:
        key, separator, path = assignment.partition("=")
        if not (separator and key and path):
            raise BrewdocError("malformed artifact assignment: %s" % assignment)
        if key in mapping:
            raise BrewdocError("duplicate artifact key: %s" % key)
        mapping[key] = path
    return mapping


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for document, sheet, artifact, and self-check requests."""
    parser = argparse.ArgumentParser(
        prog=RENDERED_BY,
        description="Render a PDF, a Word document or a spreadsheet as deterministic, "
                    "sanitised, LLM-readable "
                    "Markdown. PDF regions route by their own ruling edges: a lined table becomes "
                    "a Markdown table, an unlined captioned table is cropped then read, other "
                    "regions become fixed-width or plain text, and two-column prose is cropped at "
                    "the gutter. A spreadsheet becomes one anchored '## Sheet N: \"<name>\"' "
                    "section per sheet, only the --sheet ones when given, and a .docx one "
                    "anchored '## Chapter N: \"<heading>\"' section per Word heading, its "
                    "tables kept as tables.",
        epilog="Both paths may be absolute or relative; a relative one is resolved against the "
               "current working directory, never against this script's location, and --out "
               "creates its parent directories. "
               "Reads %s. A document with no text layer is REFUSED by name - there is no OCR here. "
               "One JSON route line always goes to stdout: it names the route, what was rendered, "
               "every sanitised item dropped, and what this route structurally cannot carry. "
               "Without --out the Markdown follows that line on stdout."
               % SUFFIXES)
    parser.add_argument("document", nargs="?",
                        help="the .pdf, .docx or spreadsheet to render")
    parser.add_argument("--out", help="write the Markdown here (ASCII); default stdout")
    parser.add_argument("--sheet", action="append",
                        help="render only this exact workbook sheet; repeat for caller order")
    parser.add_argument("--artifact", action="append",
                        help="write one listed workbook artifact as KEY=PATH; repeat as needed")
    parser.add_argument("--self-check", action="store_true", dest="self_check",
                        help="render synthetic fixtures twice and compare; exits 0 when green")
    return parser


def main(argv=None) -> int:
    """Command-line entry: prints the JSON receipt, then the Markdown unless `--out` was given."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.document:
        parser.print_usage()
        return EXIT_USAGE
    try:
        artifact_outputs = _artifact_assignments(args.artifact)
    except BrewdocError as exc:
        rc, line, markdown = EXIT_FAIL, _line(
            _route(Path(args.document)), False, str(exc), args.document), ""
    else:
        rc, line, markdown = run(
            args.document, args.out, sheets=args.sheet, artifact_outputs=artifact_outputs)
    print(json.dumps(line, ensure_ascii=True, sort_keys=True))
    if rc == EXIT_OK and not args.out:
        sys.stdout.write(markdown)
    return rc


if __name__ == "__main__":
    sys.exit(main())
