"""Install one exact public PyPI consumer without changing artifact selection."""

import argparse
import subprocess
import sys
import time

# Issue #53: absence at 59s, success about 5.5min after upload; allow 10min per route.
WAIT_SECONDS = 600
POLL_SECONDS = 15


def install(version, python, route, *, run=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    """Retry only the pinned uv diagnostic for an absent exact brewdoc version."""
    command = ["uv", "pip", "install", "--python", str(python), "--index-url", "https://pypi.org/simple",
               "--only-binary" if route == "wheel" else "--no-binary", "brewdoc",
               "--refresh-package", "brewdoc", f"brewdoc=={version}"]
    missing = ("error: No solution found when resolving dependencies\n"
               f"  cause: Because there is no version of brewdoc=={version} and you require brewdoc=={version}, "
               "we can conclude that your requirements are unsatisfiable.")
    deadline = clock() + WAIT_SECONDS
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, WAIT_SECONDS)
        result = run(command, capture_output=True, timeout=remaining)
        if clock() >= deadline:
            raise subprocess.TimeoutExpired(command, WAIT_SECONDS)
        detail = result.stderr.decode("utf-8", errors="replace")
        if detail.startswith("Using Python "):
            detail = detail.partition("\n")[2]
        if result.returncode != 1 or detail.strip() != missing:
            return result
        remaining = deadline - clock()
        if remaining <= 0:
            return result
        sleep(min(POLL_SECONDS, remaining))
        if clock() >= deadline:
            return result


def main(argv=None):
    """Preserve the final uv exit code and output for the public consumer step."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--route", choices=("wheel", "sdist"), required=True)
    args = parser.parse_args(argv)
    try:
        result = install(args.version, args.python, args.route)
    except subprocess.TimeoutExpired:
        sys.stderr.write("Exact PyPI consumer install exceeded its 600-second deadline\n")
        return 1
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
