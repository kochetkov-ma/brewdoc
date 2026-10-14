"""Strict UTF-8 text pass-through without a generated Markdown envelope."""

from __future__ import annotations

from pathlib import Path

from brewdoc.common import Rendered, Route, new_tally, reading


def _render_text(path: Path, _sheets=None) -> Rendered:
    """Preserve every UTF-8 byte, including BOM, whitespace and newline variants."""
    with reading(path, "text"):
        markdown = path.read_bytes().decode("utf-8")
    return markdown, new_tally(), (), ()


ROUTE = Route("text", "none", (".md", ".txt"), _render_text, (),
              markdown_schema="brewdoc.markdown/passthrough/1")
