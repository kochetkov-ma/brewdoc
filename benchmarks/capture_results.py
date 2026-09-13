"""Capture one foreground command and its artifacts without judging output quality."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time


def file_record(path: Path, label: str) -> dict:
    """Hash file bytes in bounded memory."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": label, "sha256": digest.hexdigest(), "size_bytes": size}


def artifact_records(directory: Path) -> tuple[list[dict], list[str]]:
    """Describe accessible outputs and retain failures without following symlinks."""
    records, errors = [], []
    for root, directories, files in os.walk(directory / "artifacts", onerror=lambda exc: errors.append(str(exc))):
        for name in sorted(directories + files):
            path = Path(root, name)
            label = path.relative_to(directory).as_posix()
            try:
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    records.append({"path": label, "kind": "symlink", "target": os.readlink(path)})
                elif stat.S_ISREG(mode):
                    records.append(file_record(path, label))
                elif not stat.S_ISDIR(mode):
                    records.append({"path": label, "kind": "special"})
            except OSError as exc:
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
    return sorted(records, key=lambda item: item["path"]), errors


def stop_process(process: subprocess.Popen) -> None:
    """Kill and reap the command; POSIX also kills its process group."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def capture(args: argparse.Namespace) -> int:
    """Create a fresh result directory and retain completed or failed observations."""
    source = args.input.resolve(strict=True)
    if not source.is_file():
        raise OSError(f"input must be a regular file: {source}")
    source_record = file_record(source, str(source))
    directory = args.output_dir.absolute()
    directory.mkdir(parents=True, exist_ok=False)
    directory = directory.resolve()
    artifacts = directory / "artifacts"
    artifacts.mkdir()
    replacements = {"{input}": str(source), "{output_dir}": str(artifacts)}
    command = [re.sub(r"\{(?:input|output_dir)\}", lambda match: replacements[match[0]], part)
               for part in args.command]
    result = {
        "schema_version": 1,
        "tool": {"name": args.tool, "version": args.tool_version},
        "input": source_record,
        "command_template": args.command,
        "command": command,
        "cwd": str(Path.cwd()),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": args.timeout,
        "returncode": None,
        "signal": None,
        "error": None,
        "capture_errors": [],
    }
    with (directory / "stdout.bin").open("xb") as stdout, \
            (directory / "stderr.bin").open("xb") as stderr:
        started = time.perf_counter()
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout,
                                       stderr=stderr, start_new_session=os.name == "posix")
        except OSError as exc:
            result.update(status="spawn_error", error=f"{type(exc).__name__}: {exc}")
        else:
            try:
                process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                stop_process(process)
                result["status"] = "timeout"
            except KeyboardInterrupt:
                stop_process(process)
                result["status"] = "interrupted"
            else:
                result["status"] = ("success" if process.returncode == 0 else
                                    "signal" if process.returncode < 0 else "nonzero_exit")
            result["returncode"] = process.returncode
            result["signal"] = -process.returncode if process.returncode < 0 else None
        result["elapsed_seconds"] = time.perf_counter() - started
    for name in ("stdout", "stderr"):
        result[name] = None
        try:
            result[name] = file_record(directory / f"{name}.bin", f"{name}.bin")
        except OSError as exc:
            result["capture_errors"].append(f"{name}: {type(exc).__name__}: {exc}")
    result["artifacts"], errors = artifact_records(directory)
    result["capture_errors"].extend(errors)
    (directory / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    if result["capture_errors"]:
        return 2
    return 0 if result["status"] == "success" else 1


def positive_seconds(value: str) -> float:
    """Reject timeouts that cannot bound a run."""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return seconds


def main(argv=None) -> int:
    """Return 0 for success, 1 for a command failure, or 2 for a setup or capture error."""
    parser = argparse.ArgumentParser(
        description=__doc__, allow_abbrev=False,
        epilog="Use -- before COMMAND. {input} is the absolute input path; {output_dir} "
               "is the new result directory's artifacts/ subdirectory. The command inherits "
               "the current working directory. Existing result directories are refused.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tool", required=True, help="user-declared tool name")
    parser.add_argument("--tool-version", required=True, help="user-declared version; not probed")
    parser.add_argument("--timeout", type=positive_seconds, help="seconds; default has no timeout")
    parser.add_argument("command", nargs=argparse.REMAINDER, metavar="COMMAND")
    args = parser.parse_args(argv)
    if not args.command or args.command[0] != "--" or len(args.command) == 1:
        parser.error("provide one command after --")
    args.command = args.command[1:]
    try:
        return capture(args)
    except OSError as exc:
        parser.exit(2, f"capture error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
