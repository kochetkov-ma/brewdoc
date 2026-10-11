"""Enter the CLI or one fixed isolated child without importing converters first."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _number(value: str, minimum: int, maximum: int) -> int:
    """Accept only bounded ASCII decimal operands for child process handles."""
    if not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise ValueError("Invalid internal operand")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError("Invalid internal operand")
    return number


def _child_arguments(operation: str, arguments: list[str] | tuple[str, ...]) -> tuple[str | int, ...]:
    """Validate the complete fixed operation before importing or starting a worker."""
    match operation, arguments:
        case "dns", [host, port, parent_pid]:
            if not host or len(host) > 253 or not all(
                    char.isascii() and (char.isalnum() or char in ".-:") for char in host):
                raise ValueError("Invalid internal host")
            return host, _number(port, 1, 65535), _number(parent_pid, 1, 2**32 - 1)
        case "signal", []:
            return ()
        case "js", [descriptor, parent_pid]:
            return _number(descriptor, 3, 2**31 - 1), _number(parent_pid, 1, 2**32 - 1)
    raise ValueError("Invalid internal operation")


def child_command(operation: str, *arguments: str) -> list[str]:
    """Select fixed child dispatch while retaining isolated installed-Python execution."""
    _child_arguments(operation, arguments)
    prefix = [sys.executable]
    if not getattr(sys, "frozen", False):
        prefix.append("-I")
        if sys.platform == "win32" and operation == "dns":
            base = getattr(sys, "_base_executable", None)
            if not isinstance(base, str) or not Path(base).is_absolute() or not Path(base).is_file():
                raise OSError("DNS base interpreter unavailable")
            prefix = [base, "-I", "-S"]
        prefix.append(str(Path(__file__).resolve()))
    return [*prefix, "--brewdoc-internal", operation, *arguments]


def _exit_windows_dns(handle: int | None) -> None:
    """Close the owner handle and exit the DNS child, even if cleanup fails."""
    try:
        if handle is not None:
            import _winapi
            _winapi.CloseHandle(handle)
    finally:
        os._exit(1)


def _dns_worker(host: str, port: int, parent_pid: int) -> None:
    """Resolve bounded address JSON in a child that exits when its parent disappears."""
    import json
    import socket
    import threading
    import time

    owner_handle = None
    if sys.platform == "win32":
        import _winapi
        try:
            owner_handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, parent_pid)
            if _winapi.WaitForSingleObject(owner_handle, 0) != _winapi.WAIT_TIMEOUT:
                raise OSError("DNS owner process exited")
        except OSError:
            _exit_windows_dns(owner_handle)

    def watch() -> None:
        """Stop blocked libc resolution after parent death."""
        if owner_handle is not None:
            try:
                _winapi.WaitForSingleObject(owner_handle, _winapi.INFINITE)
            finally:
                _exit_windows_dns(owner_handle)
        while os.getppid() == parent_pid:
            time.sleep(0.05)
        os._exit(1)

    try:
        threading.Thread(target=watch, daemon=True).start()
    except (OSError, RuntimeError):
        if owner_handle is None:
            raise
        _exit_windows_dns(owner_handle)
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    sys.stdout.write(json.dumps(list(dict.fromkeys(item[4][0] for item in answers))[:129]) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Dispatch three private child operations or delegate ordinary arguments to the CLI."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--brewdoc-internal"]:
        if len(arguments) < 2:
            return 2
        operation = arguments[1]
        try:
            operands = _child_arguments(operation, arguments[2:])
        except ValueError:
            return 2
        if operation == "dns":
            _dns_worker(*operands)
        elif operation == "signal":
            from brewdoc.htmljs import _signal_preflight
            _signal_preflight()
        else:
            from brewdoc.htmljs import _worker_fd
            _worker_fd(*operands)
        return 0
    if arguments[:1] in (["-c"], ["-m"], ["-I"]):
        return 2
    from brewdoc.cli import main as cli_main
    return cli_main(arguments)


if __name__ == "__main__":
    sys.exit(main())
