"""Console entry for local documents, explicit URL acquisition and self-check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from brewdoc.common import BrewdocError, _markdown_encoding
from brewdoc.selfcheck import self_check
from brewdoc.service import (EXIT_FAIL, EXIT_OK, EXIT_USAGE, SUFFIXES, _html_receipt_route,
                             _line, _route, run)

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
        description="Fast, deterministic document-to-Markdown conversion for agent systems. Easy to install. "
                    "No model weights, OCR or external services.",
        epilog="Both paths may be absolute or relative; a relative one is resolved against the "
               "current working directory, never against this script's location, and --out "
               "creates its parent directories. "
               "Reads %s. A document with no text layer is REFUSED by name - there is no OCR here. "
               "One JSON route line always goes to stdout: it names the route, what was rendered, "
               "every sanitised item dropped, and what this route structurally cannot carry. "
               "Without --out the Markdown follows that line on stdout."
               % SUFFIXES)
    parser.add_argument("document", nargs="?",
                        help="the PDF, DOCX, PPTX, EPUB, HTML, ODT, ODP, EML or spreadsheet to render; "
                             "UTF-8 .md/.txt pass through unchanged")
    parser.add_argument("--url", help="acquire one public HTTP(S) HTML page or supported document "
                                     "instead of a local document")
    parser.add_argument("--timeout", type=_timeout_seconds, metavar="SECONDS",
                        help="maximum waiting for one --url call and JS; positive finite seconds "
                             "within supported clock range, default 30")
    parser.add_argument("--render-js", action="store_true",
                        help="execute the optional bounded JS subset for --url; requires brewdoc[render-js]")
    parser.add_argument("--article", action="store_true",
                        help="keep main or article content; with none, render the whole clean page "
                             "and set receipt article_fallback: true")
    parser.add_argument("--body-only", action="store_true",
                        help="omit the title, metadata, omissions, contents and unit anchors; "
                             "keep unit headings only for several units")
    parser.add_argument("--out", help="write Markdown here (PDF/DOCX/PPTX ASCII; other formats UTF-8); "
                                     "default stdout")
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
    if args.article and args.self_check:
        parser.error("--article cannot be combined with --self-check")
    if args.body_only and args.self_check:
        parser.error("--body-only cannot be combined with --self-check")
    if args.self_check:
        return self_check()
    if args.url is not None:
        from brewdoc.htmlurl import _DEFAULT_TIMEOUT, _run_url

        rc, line, markdown = _run_url(
            args.url, args.out, render_js=args.render_js,
            timeout=_DEFAULT_TIMEOUT if args.timeout is None else args.timeout,
            sheets=args.sheet, artifact_outputs=args.artifact, article=args.article,
            body_only=args.body_only)
    elif not args.document:
        parser.print_usage()
        return EXIT_USAGE
    else:
        try:
            artifact_outputs = _artifact_assignments(args.artifact)
        except BrewdocError as exc:
            route = _route(Path(args.document))
            if args.article and route.name == "html":
                route = _html_receipt_route(args.article)
            rc, line, markdown = EXIT_FAIL, _line(route, False, str(exc), args.document), ""
        else:
            rc, line, markdown = run(
                args.document, args.out, sheets=args.sheet, artifact_outputs=artifact_outputs,
                article=args.article, body_only=args.body_only)
    print(json.dumps(line, ensure_ascii=True, sort_keys=True))
    if rc == EXIT_OK and not args.out:
        encoding = _markdown_encoding(line["route"])
        if encoding == "utf-8" and hasattr(sys.stdout, "buffer"):
            sys.stdout.flush()
            sys.stdout.buffer.write(markdown.encode(encoding))
        else:
            sys.stdout.write(markdown)
    return rc


if __name__ == "__main__":
    sys.exit(main())
