"""Console entry for local documents, explicit URL acquisition and self-check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brewdoc.common import BrewdocError
from brewdoc.selfcheck import self_check
from brewdoc.service import EXIT_FAIL, EXIT_OK, EXIT_USAGE, SUFFIXES, _line, _route, run

RENDERED_BY = "brewdoc"


def _timeout_seconds(value: str) -> float:
    """Validate an explicit URL wait budget as an argparse value."""
    from brewdoc.htmlurl import _timeout_seconds as validate_timeout

    try:
        return validate_timeout(float(value))
    except (ValueError, BrewdocError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


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
        description="Render a PDF, a Word document, a presentation, a spreadsheet or static HTML as "
                    "deterministic, sanitised, LLM-readable "
                    "Markdown. PDF regions route by their own ruling edges: a lined table becomes "
                    "a Markdown table, an unlined captioned table is cropped then read, other "
                    "regions become fixed-width or plain text, and two-column prose is cropped at "
                    "the gutter. A spreadsheet becomes one anchored '## Sheet N: \"<name>\"' "
                    "section per sheet, using only the --sheet names when given. A .docx becomes "
                    "one anchored '## Chapter N: \"<heading>\"' section per Word heading and "
                    "keeps its tables. A .pptx becomes one anchored "
                    "'## Slide N: \"<title>\"' section per declared slide. Static .html becomes one "
                    "chapter and requires CPython 3.12-3.14 with selectolax 0.4.13. Other runtimes "
                    "explicitly refuse HTML; a missing or broken parser on supported CPython "
                    "fails HTML conversion and self-check.",
        epilog="Both paths may be absolute or relative; a relative one is resolved against the "
               "current working directory, never against this script's location, and --out "
               "creates its parent directories. "
               "Reads %s. A document with no text layer is REFUSED by name - there is no OCR here. "
               "One JSON route line always goes to stdout: it names the route, what was rendered, "
               "every sanitised item dropped, and what this route structurally cannot carry. "
               "Without --out the Markdown follows that line on stdout."
               % SUFFIXES)
    parser.add_argument("document", nargs="?",
                        help="the .pdf, .docx, .pptx, .html or spreadsheet to render")
    parser.add_argument("--url", help="acquire one public HTTP(S) HTML page instead of a local document")
    parser.add_argument("--timeout", type=_timeout_seconds, metavar="SECONDS",
                        help="maximum waiting for one --url call and JS; positive finite seconds "
                             "within supported clock range, default 10")
    parser.add_argument("--render-js", action="store_true",
                        help="execute the optional bounded JS subset for --url; requires brewdoc[render-js]")
    parser.add_argument("--out", help="write Markdown here (HTML UTF-8, other formats ASCII); default stdout")
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
    if args.url is not None and (args.document is not None or args.self_check):
        parser.error("--url cannot be combined with a local document or --self-check")
    if args.render_js and args.url is None:
        parser.error("--render-js requires --url")
    if args.timeout is not None and args.url is None:
        parser.error("--timeout requires --url")
    if args.self_check:
        return self_check()
    if args.url is not None:
        from brewdoc.htmlurl import _DEFAULT_TIMEOUT, _run_url

        rc, line, markdown = _run_url(
            args.url, args.out, render_js=args.render_js,
            timeout=_DEFAULT_TIMEOUT if args.timeout is None else args.timeout,
            sheets=args.sheet, artifact_outputs=args.artifact)
    elif not args.document:
        parser.print_usage()
        return EXIT_USAGE
    else:
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
        if line["route"] == "html" and hasattr(sys.stdout, "buffer"):
            sys.stdout.flush()
            sys.stdout.buffer.write(markdown.encode("utf-8"))
        else:
            sys.stdout.write(markdown)
    return rc


if __name__ == "__main__":
    sys.exit(main())
