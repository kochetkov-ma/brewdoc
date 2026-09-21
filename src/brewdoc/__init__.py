"""brewdoc: brew a PDF, a Word document or a spreadsheet into LLM-ready Markdown."""

from brewdoc.reader import (ArtifactRef, BrewdocError, CellFormula, FormulaArtifact,
                            FormulaArtifactRef, OpaqueVbaProject, OpaqueVbaProjectRef,
                            discover_formula_artifacts, discover_vba_artifacts,
                            list_book_artifacts, read_book_artifact, read_formula_artifact,
                            read_formulas, read_vba_artifact, render_book, render_doc, render_pdf,
                            run, self_check)

__version__ = "0.1.0"
__all__ = ["ArtifactRef", "BrewdocError", "CellFormula", "FormulaArtifact",
           "FormulaArtifactRef", "__version__", "OpaqueVbaProject", "OpaqueVbaProjectRef",
           "discover_formula_artifacts", "discover_vba_artifacts", "list_book_artifacts",
           "read_book_artifact", "read_formula_artifact", "read_formulas", "read_vba_artifact",
           "render_book", "render_doc", "render_pdf", "run", "self_check"]
