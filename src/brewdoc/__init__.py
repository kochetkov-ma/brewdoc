"""Brew PDFs, Word documents, presentations and spreadsheets into LLM-ready Markdown."""

from brewdoc.common import ArtifactRef, BrewdocError
from brewdoc.docx import render_doc
from brewdoc.pdf import render_pdf
from brewdoc.pptx import render_presentation
from brewdoc.selfcheck import self_check
from brewdoc.service import run
from brewdoc.sheets import (CellFormula, FormulaArtifact, list_book_artifacts, read_book_artifact,
                            read_formulas, render_book)

__version__ = "0.1.0"
__all__ = ["ArtifactRef", "BrewdocError", "CellFormula", "FormulaArtifact", "__version__",
           "list_book_artifacts", "read_book_artifact", "read_formulas", "render_book",
           "render_doc", "render_pdf", "render_presentation", "run", "self_check"]
