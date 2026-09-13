"""Exercise capture behavior with synthetic inputs and isolated child commands."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks" / "capture_results.py"


@pytest.fixture
def capture_args(tmp_path):
    """Provide an input and a result location with spaces in both paths."""
    source = tmp_path / "input file.txt"
    source.write_bytes(b"synthetic input\n")
    return [sys.executable, str(SCRIPT), "--input", str(source), "--output-dir",
            str(tmp_path / "captured run"), "--tool", "synthetic", "--tool-version", "1.2.3"]


def read_result(tmp_path):
    """Read the observation written by the separate capture process."""
    return json.loads((tmp_path / "captured run" / "result.json").read_text(encoding="utf-8"))


def test_success_retains_binary_streams_command_and_artifact_hashes(capture_args, tmp_path):
    # GIVEN a command with literal shell syntax and paths containing spaces
    code = ("import pathlib,sys; "
            "pathlib.Path(sys.argv[2], 'actual.txt').write_bytes(pathlib.Path(sys.argv[1]).read_bytes()); "
            "sys.stdout.buffer.write(b'out\\x00\\xff'); sys.stderr.buffer.write(b'err\\xfe')")
    command = [sys.executable, "-c", code, "{input}", "{output_dir}", "$(echo unsafe); *"]
    assert not (tmp_path / "captured run").exists(), "the result location must start absent"
    # WHEN one command runs without a shell
    run = subprocess.run(capture_args + ["--"] + command, cwd=tmp_path,
                         capture_output=True, timeout=10)
    result = read_result(tmp_path)
    # THEN exact bytes and invocation details are retained
    expected_file = {"path": "artifacts/actual.txt", "sha256": hashlib.sha256(b"synthetic input\n").hexdigest(),
                     "size_bytes": 16}
    assert (run.returncode, run.stdout, run.stderr) == (0, b"", b""), "capture must finish silently"
    assert (result["status"], result["returncode"], result["signal"], result["error"]) == (
        "success", 0, None, None), "successful execution must retain its exact outcome"
    assert result["artifacts"] == [expected_file], "only the actual output belongs in the artifact list"
    assert result["input"] == dict(expected_file, path=str(tmp_path / "input file.txt")), "input bytes must be identified"
    assert result["command_template"] == command, "unexpanded options must be retained"
    assert result["command"] == [sys.executable, "-c", code, str(tmp_path / "input file.txt"),
                                  str(tmp_path / "captured run" / "artifacts"), "$(echo unsafe); *"], "only placeholders expand"
    assert (result["schema_version"], result["tool"], result["cwd"], result["timeout_seconds"]) == (
        1, {"name": "synthetic", "version": "1.2.3"}, str(tmp_path), None), "capture metadata must describe this run"
    assert 0 <= result["elapsed_seconds"] < 10, "elapsed time must measure the bounded command"
    assert result["stdout"] == {"path": "stdout.bin", "sha256": hashlib.sha256(b"out\x00\xff").hexdigest(),
                                "size_bytes": 5}, "stdout hashes must cover undecoded bytes"
    assert (tmp_path / "captured run" / "stdout.bin").read_bytes() == b"out\x00\xff", "stdout bytes must survive unchanged"
    assert (tmp_path / "captured run" / "stderr.bin").read_bytes() == b"err\xfe", "stderr bytes must survive unchanged"


def test_nonzero_exit_retains_partial_output(capture_args, tmp_path):
    # GIVEN a failing command that produces an artifact first
    code = "import pathlib,sys; pathlib.Path(sys.argv[1], 'partial').write_bytes(b'partial'); sys.exit(7)"
    # WHEN capture observes the failure
    run = subprocess.run(capture_args + ["--", sys.executable, "-c", code, "{output_dir}"], timeout=10)
    result = read_result(tmp_path)
    # THEN the command status and partial artifact are preserved
    assert (run.returncode, result["status"], result["returncode"], result["signal"]) == (
        1, "nonzero_exit", 7, None), "the helper must distinguish its own exit from the command exit"
    assert result["artifacts"] == [{"path": "artifacts/partial", "size_bytes": 7,
                                   "sha256": hashlib.sha256(b"partial").hexdigest()}], "failure must retain partial output"


def test_missing_executable_is_a_captured_failure(capture_args, tmp_path):
    # GIVEN an executable path that does not exist
    executable = tmp_path / "no executable"
    assert not executable.exists(), "the command must be unavailable"
    # WHEN process creation fails
    run = subprocess.run(capture_args + ["--", str(executable)], timeout=10)
    result = read_result(tmp_path)
    # THEN a complete failure observation remains
    assert (run.returncode, result["status"], result["returncode"], result["signal"], result["artifacts"]) == (
        1, "spawn_error", None, None, []), "spawn failure must not masquerade as tool output"
    assert result["error"].startswith("FileNotFoundError:"), "the spawn failure reason must be retained"
    assert (tmp_path / "captured run" / "stderr.bin").read_bytes() == b"", "capture errors must not alter tool stderr"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group cleanup")
def test_timeout_kills_the_command_and_its_child(capture_args, tmp_path):
    # GIVEN a command whose child would write after the timeout
    marker = tmp_path / "late output"
    child_code = "import pathlib,sys,time; time.sleep(1.5); pathlib.Path(sys.argv[1]).write_text('alive')"
    code = ("import subprocess,sys,time; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
            "sys.stdout.write('started'); sys.stdout.flush(); time.sleep(30)")
    assert not marker.exists(), "no child output may predate the command"
    # WHEN the helper enforces its timeout
    run = subprocess.run(capture_args + ["--timeout", "0.5", "--", sys.executable, "-c", code,
                                         child_code, str(marker)], timeout=10)
    result = read_result(tmp_path)
    time.sleep(1.5)
    # THEN both the command and its descendant stop before further output
    assert (run.returncode, result["status"], result["returncode"], result["signal"], result["timeout_seconds"]) == (
        1, "timeout", -signal.SIGKILL, signal.SIGKILL, 0.5), "timeout must retain the forced termination status"
    assert (tmp_path / "captured run" / "stdout.bin").read_bytes() == b"started", "output preceding timeout must survive"
    assert not marker.exists(), "the command's child must not survive the timeout"


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal return codes")
def test_signal_exit_is_recorded_separately(capture_args, tmp_path):
    # GIVEN a command that terminates itself with SIGTERM
    code = "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"
    # WHEN capture observes the signal
    run = subprocess.run(capture_args + ["--", sys.executable, "-c", code], timeout=10)
    result = read_result(tmp_path)
    # THEN the signal number is explicit
    assert (run.returncode, result["status"], result["returncode"], result["signal"]) == (
        1, "signal", -signal.SIGTERM, signal.SIGTERM), "a signal exit must be distinguishable from a normal nonzero exit"


def test_existing_result_is_never_overwritten(capture_args, tmp_path):
    # GIVEN an existing result with prior bytes
    directory = tmp_path / "captured run"
    directory.mkdir()
    sentinel = directory / "result.json"
    sentinel.write_bytes(b"prior observation")
    assert sentinel.read_bytes() == b"prior observation", "the prior observation must exist"
    # WHEN a new capture targets that directory
    run = subprocess.run(capture_args + ["--", sys.executable, "-c", "raise RuntimeError('must not run')"],
                         capture_output=True, timeout=10)
    # THEN capture refuses before running the command or changing bytes
    assert run.returncode == 2, "reusing a result directory must fail capture setup"
    assert sorted(path.name for path in directory.iterdir()) == ["result.json"], "no new capture files may be created"
    assert sentinel.read_bytes() == b"prior observation", "the prior observation must remain byte-identical"


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "invalid"])
def test_invalid_timeout_fails_before_creating_results(capture_args, tmp_path, timeout):
    # GIVEN a timeout that cannot bound execution
    assert not (tmp_path / "captured run").exists(), "the result location must start absent"
    # WHEN arguments are validated
    run = subprocess.run(capture_args + ["--timeout", timeout, "--", sys.executable, "-c", "pass"],
                         capture_output=True, timeout=10)
    # THEN invalid setup produces no result directory
    assert run.returncode == 2, "invalid timeout must be a usage error"
    assert not (tmp_path / "captured run").exists(), "invalid arguments must not create observations"


@pytest.mark.skipif(os.name != "posix", reason="symlink and FIFO creation")
def test_artifact_inventory_does_not_follow_links_or_read_fifos(capture_args, tmp_path):
    # GIVEN a command that creates a link and a named pipe
    code = ("import os,pathlib,sys; out=pathlib.Path(sys.argv[1]); "
            "(out/'link').symlink_to(sys.argv[2]); os.mkfifo(out/'pipe')")
    # WHEN capture inventories artifacts
    run = subprocess.run(capture_args + ["--", sys.executable, "-c", code, "{output_dir}", "{input}"], timeout=10)
    result = read_result(tmp_path)
    # THEN neither link targets nor special files are read as output bytes
    assert run.returncode == 0, "special output entries must not block capture"
    assert result["artifacts"] == [
        {"path": "artifacts/link", "kind": "symlink", "target": str(tmp_path / "input file.txt")},
        {"path": "artifacts/pipe", "kind": "special"},
    ], "only regular output files receive content hashes"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission failures")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_artifacts_retain_execution_metadata(capture_args, tmp_path):
    # GIVEN a failing command with readable and unreadable artifacts
    code = ("import pathlib,sys; out=pathlib.Path(sys.argv[1]); "
            "(out/'readable').write_bytes(b'kept'); (out/'unreadable').write_bytes(b'locked'); "
            "(out/'unreadable').chmod(0); (out/'locked').mkdir(); (out/'locked').chmod(0); sys.exit(7)")
    # WHEN the command fails and artifact capture cannot finish
    try:
        run = subprocess.run(capture_args + ["--", sys.executable, "-c", code, "{output_dir}"], timeout=10)
        result = read_result(tmp_path)
    finally:
        (tmp_path / "captured run" / "artifacts" / "locked").chmod(0o700)
        (tmp_path / "captured run" / "artifacts" / "unreadable").chmod(0o600)
    # THEN the failed inventory cannot be mistaken for an empty successful result
    assert (run.returncode, result["status"], result["returncode"]) == (
        2, "nonzero_exit", 7), "execution outcome must survive a separate capture failure"
    assert result["artifacts"] == [{"path": "artifacts/readable", "size_bytes": 4,
                                   "sha256": hashlib.sha256(b"kept").hexdigest()}], "accessible artifacts must remain recorded"
    assert len(result["capture_errors"]) == 2, "both unreadable file and directory failures must be explicit"
    assert "artifacts/unreadable: PermissionError:" in result["capture_errors"][0], "the file error must identify its path"
    assert "locked" in result["capture_errors"][1], "the directory error must identify its path"


def test_missing_input_fails_before_creating_results(capture_args, tmp_path):
    # GIVEN an input path that no longer exists
    (tmp_path / "input file.txt").unlink()
    assert not (tmp_path / "input file.txt").exists(), "the input must be unavailable"
    # WHEN input hashing is attempted
    run = subprocess.run(capture_args + ["--", sys.executable, "-c", "pass"], capture_output=True, timeout=10)
    # THEN the helper refuses to start the selected tool
    assert run.returncode == 2, "missing input must fail capture setup"
    assert not (tmp_path / "captured run").exists(), "invalid input must not create an observation"


def test_substituted_paths_are_not_expanded_again(capture_args, tmp_path):
    # GIVEN a literal placeholder spelling inside the input filename
    source = tmp_path / "{output_dir}.txt"
    source.write_bytes(b"literal path")
    args = capture_args.copy()
    args[args.index("--input") + 1] = str(source)
    # WHEN the input placeholder is substituted
    run = subprocess.run(args + ["--", sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", "{input}"],
                         timeout=10)
    # THEN substitution leaves the inserted filename unchanged
    assert run.returncode == 0, "literal placeholder text in a real filename must be supported"
    assert (tmp_path / "captured run" / "stdout.bin").read_text() == str(source), "substitution must happen only once"


@pytest.mark.skipif(os.name != "posix", reason="POSIX named pipe input")
def test_named_pipe_input_is_rejected_without_opening_it(capture_args, tmp_path):
    # GIVEN an input FIFO without a writer
    source = tmp_path / "input file.txt"
    source.unlink()
    os.mkfifo(source)
    assert not source.is_file(), "the input must not be a regular file"
    # WHEN capture validates the input
    run = subprocess.run(capture_args + ["--", sys.executable, "-c", "pass"], capture_output=True, timeout=10)
    # THEN it refuses promptly instead of blocking while hashing the pipe
    assert run.returncode == 2, "special input files must fail capture setup"
    assert b"input must be a regular file" in run.stderr, "the refusal must explain the input restriction"
    assert not (tmp_path / "captured run").exists(), "invalid input must not create an observation"
