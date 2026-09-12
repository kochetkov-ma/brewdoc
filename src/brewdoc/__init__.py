"""brewdoc: brew a PDF, a Word document or a spreadsheet into LLM-ready Markdown."""

from brewdoc.reader import (BrewdocError, render_book, render_doc, render_pdf, run,
                            self_check)

__version__ = "0.1.0"
__all__ = ["BrewdocError", "__version__", "render_book", "render_doc", "render_pdf", "run",
           "self_check"]
