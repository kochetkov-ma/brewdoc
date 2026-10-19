"""Service: suffix routes, `run` and its receipt line, output writes through `output`."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from brewdoc.common import (MARKDOWN_SCHEMA, RECEIPT_SCHEMA, BrewdocError, Route, _body_only,
                            _ZipBudget, _markdown_encoding, new_tally)
from brewdoc.docx import ROUTE as DOC_ROUTE
from brewdoc.eml import ROUTE as EML_ROUTE
from brewdoc.epub import ROUTE as EPUB_ROUTE
from brewdoc.htmltext import ARTICLE_OMISSION, LINKS_OMISSION, ROUTE as HTML_ROUTE, _html_content
from brewdoc.odf import ODP_ROUTE, ODT_ROUTE
from brewdoc.output import _output_targets, _write_outputs
from brewdoc.pdf import ROUTE as PDF_ROUTE
from brewdoc.pptx import ROUTE as PRESENTATION_ROUTE
from brewdoc.sheets import ROUTE as SHEET_ROUTE
from brewdoc.sheets import _artifact_payloads, _sheet_selection
from brewdoc.text import ROUTE as TEXT_ROUTE

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def _route_map(*routes: Route) -> dict[str, Route]:
    """Suffix -> route; two adapters claiming one suffix is a defect, not a last-one-wins race."""
    table: dict[str, Route] = {}
    for route in routes:
        for suffix in route.suffixes:
            if suffix in table:
                raise BrewdocError("suffix '%s' is claimed by both the %s and %s routes"
                                   % (suffix, table[suffix].name, route.name))
            table[suffix] = route
    return table


ROUTES = _route_map(PDF_ROUTE, DOC_ROUTE, PRESENTATION_ROUTE, SHEET_ROUTE, HTML_ROUTE,
                    EPUB_ROUTE, TEXT_ROUTE, EML_ROUTE, ODT_ROUTE, ODP_ROUTE)
SUFFIXES = " ".join(sorted(ROUTES))
NO_ROUTE = Route("none", "none", (), None, ())
HTML_ONLY = "--article is an HTML-only option"
LINKS_HTML_ONLY = "--no-links is an HTML-only option"
WORKBOOK_ONLY = "--sheet and --artifact are workbook-only options"


def _route(path: Path) -> Route:
    return ROUTES.get(path.suffix.lower(), NO_ROUTE)


def _html_receipt_route(article: bool, links: bool = True) -> Route:
    """Keep article, then links, disclosure on an unregistered copy of the HTML receipt route."""
    omissions = ((ARTICLE_OMISSION,) if article else ()) + (() if links else (LINKS_OMISSION,))
    return replace(HTML_ROUTE, not_carried=HTML_ROUTE.not_carried + omissions) if omissions else HTML_ROUTE


def _line(route: Route, file_ok: bool, reason: str, path, tally: dict | None = None,
          out=None, unit_keys: tuple[str, ...] = (), artifacts: tuple[dict, ...] = (),
          selected_sheets=None) -> dict:
    """Copy loss counters into a receipt; success adds Markdown schema, unit keys and artifacts.
    Selected sheets and the article and charset fallbacks appear only on success when present."""
    tally = tally or new_tally()
    line = {"file_ok": file_ok, "route": route.name, "reason": reason,
            "source": Path(path).name, "out": str(out) if out else None,
            "receipt_schema": RECEIPT_SCHEMA, "unit_kind": route.unit_kind,
            "units": len(unit_keys), "tables": tally["tables"],
            "text_regions": tally["text_regions"], "columns_split": tally["columns_split"],
            "dropped": dict(tally["dropped"]),
            "broken_ligature_words": tally["broken_ligature_words"],
            "not_carried": list(route.not_carried)}
    if file_ok:
        line.update({"artifacts": list(artifacts), "markdown_schema": route.markdown_schema,
                     "unit_keys": list(unit_keys)})
        if selected_sheets is not None:
            line["selected_sheets"] = list(selected_sheets)
        if "article_fallback" in tally:
            line["article_fallback"] = True
        if "charset_fallback" in tally:
            line["charset_fallback"] = tally["charset_fallback"]
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


def run(path, out=None, *, sheets=None, artifact_outputs=None, article: bool = False,
        body_only: bool = False, links: bool = True) -> tuple[int, dict, str]:
    """(exit code, receipt, Markdown); every path returns a receipt, a refusal names what and where.
    Body-only derives its Markdown from the full rendering, so limits and receipts stay the same."""
    path = Path(path)
    route = _route(path)
    if route.name == "html":
        route = _html_receipt_route(article, links)
    if route.render is None:
        return EXIT_FAIL, _line(route, False, "unsupported suffix '%s' in %s: brewdoc reads %s"
                                % (path.suffix.lower(), path, SUFFIXES), path), ""
    if not path.is_file():
        return EXIT_FAIL, _line(route, False, "no such file: %s" % path, path), ""
    try:
        if route.name != "html" and (article or not links):
            raise BrewdocError(HTML_ONLY if article else LINKS_HTML_ONLY)
        sheets = _sheet_selection(sheets)
        pairs = _artifact_output_pairs(artifact_outputs)
        if route.name != "sheet" and (sheets is not None or pairs):
            raise BrewdocError(WORKBOOK_ONLY)
        targets = _output_targets(path, out, pairs)
        zip_budget = _ZipBudget() if route.name == "sheet" else None
        if route.name == "sheet":
            rendered = route.render(path, sheets, _zip_budget=zip_budget)
        elif route.name == "eml":
            rendered = route.render(path, sheets, _html_content=_html_content)
        elif route.name == "html":
            rendered = route.render(path, sheets, article=article, links=links)
        else:
            rendered = route.render(path, sheets)
        markdown, tally, unit_keys, references = rendered
        if "article_fallback" in tally:
            route = _html_receipt_route(False, links)
        if body_only and route.markdown_schema == MARKDOWN_SCHEMA:
            markdown = _body_only(markdown, unit_keys)
        keys = tuple(key for key, _target, _shown in pairs)
        listed = {item.key for item in references}
        unknown = [key for key in keys if key not in listed]
        if unknown:
            raise BrewdocError("unknown artifact key: %s" % ", ".join(unknown))
        payloads = ((markdown.encode(_markdown_encoding(route.name)),)
                    if out is not None else ()) + _artifact_payloads(
            path, sheets, references, keys, zip_budget)
    except BrewdocError as exc:
        return EXIT_FAIL, _line(route, False, str(exc), path), ""
    except Exception as exc:                      # a third-party parser raises its own types
        return EXIT_FAIL, _line(route, False, "%s unreadable: %s: %s: %s"
                                % (route.name, path, type(exc).__name__, exc), path), ""
    try:
        _write_outputs(tuple(zip(targets, payloads)))
    except (BrewdocError, OSError) as exc:
        return EXIT_FAIL, _line(route, False, "output write failed: %s" % exc, path), ""
    reason = ("text passed through without normalization" if route.name == "text" else
              "%s rendered: %d %ss, %d tables, %d text regions, %d column splits" % (
                  route.name, len(unit_keys), route.unit_kind, tally["tables"],
                  tally["text_regions"], tally["columns_split"]))
    shown = {key: text for key, _target, text in pairs}
    receipt_artifacts = tuple(item.to_dict(shown.get(item.key)) for item in references)
    return EXIT_OK, _line(route, True, reason, path, tally, out, unit_keys,
                          receipt_artifacts, sheets), markdown
