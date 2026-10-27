"""Cache the guarded native HTML parser binding for readable and passive readers."""

import platform
import sys

_SUPPORTED_RUNTIME = platform.python_implementation() == "CPython" and (3, 12) <= sys.version_info < (3, 15)
_PARSER_ERROR = ""
LexborHTMLParser = None
if _SUPPORTED_RUNTIME:
    try:
        from selectolax.lexbor import LexborHTMLParser
    except (ImportError, OSError, RuntimeError, ValueError, SystemError) as exc:
        _PARSER_ERROR = str(exc)
