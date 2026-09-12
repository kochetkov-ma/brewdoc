"""Console entry point: `brewdoc <document> [--out FILE]` or `brewdoc --self-check`."""

import sys

from brewdoc.reader import main as _main


def main(argv=None) -> int:
    """Parse `argv` (default `sys.argv[1:]`), render, return the exit code."""
    return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
