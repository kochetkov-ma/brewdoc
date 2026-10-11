"""Fixed standalone child operations preserve isolated launch and refusal behavior."""

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from brewdoc import htmlurl


@pytest.mark.parametrize("frozen", [True, False], ids=["frozen", "isolated-source"])
def test_dns_lookup_uses_fixed_child_dispatch_without_interpreter_code(tmp_path, monkeypatch, frozen):
    # GIVEN a fixed deadline and an executable whose child launch is observable
    executable = str(tmp_path / "brewdoc")
    calls = []
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(htmlurl.os, "getpid", lambda: 43210)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)

    def child(argv, **options):
        """Observe launch options without spawning another process."""
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 0, b'["127.0.0.1"]\n', b"")

    monkeypatch.setattr(htmlurl.subprocess, "run", child)
    assert calls == [], "DNS must not launch before the consuming lookup"
    # WHEN the existing URL DNS boundary requests its isolated child
    addresses = htmlurl._dns_lookup("localhost", 443, 100.5)
    # THEN source and frozen executables use the same fixed operation with original process bounds
    suffix = ["--brewdoc-internal", "dns", "localhost", "443", "43210"]
    commands = {
        True: [executable, *suffix],
        False: [executable, "-I", str(Path(htmlurl.__file__).with_name("_standalone.py")), *suffix],
    }
    assert (addresses, calls) == (("127.0.0.1",), [(commands[frozen], {
        "capture_output": True, "timeout": 0.5, "check": False,
    })]), "DNS child launch must never ask a frozen CLI to evaluate arbitrary interpreter code"


@pytest.mark.parametrize("arguments", [
    ["--brewdoc-internal", "python", "-c", "print('untrusted')"],
    ["--brewdoc-internal", "signal", "extra"],
    ["--brewdoc-internal", "dns", "localhost", "٤٤٣", "43210"],
    ["--brewdoc-internal", "js", "-1", "43210"],
    ["--brewdoc-internal", "js", "17", "4294967296"],
], ids=["arbitrary-code", "signal-extra-argument", "non-ascii-port", "negative-fd", "oversized-pid"])
def test_invalid_private_child_requests_refuse_before_any_operation(arguments, monkeypatch, capsys):
    # GIVEN fixed child handlers whose invocation would expose a validation bypass
    standalone = importlib.import_module("brewdoc._standalone")
    htmljs = importlib.import_module("brewdoc.htmljs")
    calls = []
    monkeypatch.setattr(standalone, "_dns_worker", lambda *args: calls.append(("dns", args)))
    monkeypatch.setattr(htmljs, "_signal_preflight", lambda: calls.append(("signal", ())))
    monkeypatch.setattr(htmljs, "_worker_fd", lambda *args: calls.append(("js", args)))
    assert calls == [], "private request validation must start before worker actions"
    # WHEN arbitrary operations, extra operands or malformed process handles reach dispatch
    code = standalone.main(arguments)
    output = capsys.readouterr()
    # THEN refusal is silent exit two and none of the fixed operations started
    assert (code, output.out, output.err, calls) == (2, "", "", []), (
        "malformed private requests must refuse without interpreter execution or worker side effects")


def test_isolated_source_dns_script_works_without_the_package_or_python_environment(tmp_path):
    # GIVEN a relocated script and synthetic environment overrides pointing at a poisoned package
    standalone = importlib.import_module("brewdoc._standalone")
    script = tmp_path / "isolated worker with spaces.py"
    shutil.copyfile(standalone.__file__, script)
    poison = tmp_path / "poison"
    package = poison / "brewdoc"
    package.mkdir(parents=True)
    marker = tmp_path / "package-was-imported"
    (package / "__init__.py").write_text(
        "from pathlib import Path\nPath(%r).write_text('unexpected import')\n" % str(marker),
        encoding="utf-8")
    environment = {"PATH": str(tmp_path / "empty-path"), "PYTHONPATH": str(poison),
                   "PYTHONHOME": str(poison), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
                   "SystemRoot": os.environ.get("SystemRoot", ""), "WINDIR": os.environ.get("WINDIR", "")}
    assert marker.exists() is False, "the poisoned package import sentinel must start absent"
    # WHEN a fresh isolated Python launches the fixed DNS operation outside the checkout
    completed = subprocess.run([sys.executable, "-I", str(script), "--brewdoc-internal", "dns",
                                "127.0.0.1", "443", str(os.getpid())],
                               cwd=tmp_path, env=environment, capture_output=True, timeout=5, check=False)
    # THEN stdlib-only DNS succeeds without package imports or caller-selected Python paths
    assert (completed.returncode, completed.stdout, completed.stderr, marker.exists()) == (
        0, ('["127.0.0.1"]' + os.linesep).encode("ascii"), b"", False,
    ), "isolated DNS dispatch must not depend on package initialization or Python environment overrides"


@pytest.mark.parametrize("arguments", [["--", "-c"], ["--", "--brewdoc-internal-note.html"]],
                         ids=["interpreter-looking-filename", "private-marker-looking-filename"])
def test_normal_cli_arguments_and_exit_code_are_forwarded_unchanged(monkeypatch, arguments):
    # GIVEN an ordinary filename resembling a private or interpreter option after the separator
    standalone = importlib.import_module("brewdoc._standalone")
    cli = importlib.import_module("brewdoc.cli")
    calls = []

    def ordinary_cli(argv):
        """Return an existing CLI failure without parsing public operands."""
        calls.append(argv)
        return 1

    monkeypatch.setattr(cli, "main", ordinary_cli)
    assert calls == [], "ordinary CLI delegation must start without converter calls"
    # WHEN the standalone entry sees public arguments rather than a private child operation
    code = standalone.main(arguments)
    # THEN public argument parsing and its conversion-failure code belong to the existing CLI
    assert (code, calls) == (1, [arguments]), (
        "standalone dispatch must preserve normal CLI operands and the delegated exit code")


def test_dns_child_timeout_retains_the_named_acquisition_failure(monkeypatch):
    # GIVEN a resolver child that reaches its bounded parent deadline
    calls = []

    def expired(argv, **options):
        """Raise the subprocess deadline failure after recording launch options."""
        calls.append(options)
        raise subprocess.TimeoutExpired(argv, options["timeout"])

    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(htmlurl.subprocess, "run", expired)
    assert calls == [], "the DNS timeout must arise only after a bounded child launch"
    # WHEN lookup receives the exhausted child result
    with pytest.raises(htmlurl.URLTimeoutError) as raised:
        htmlurl._dns_lookup("localhost", 443, 100.5)
    # THEN the fixed dispatch keeps the public named timeout and original subprocess deadline
    assert (str(raised.value), raised.value.acquisition_stage, calls) == (
        "URL DNS deadline exceeded", "dns", [{"capture_output": True, "timeout": 0.5, "check": False}],
    ), "fixed DNS launch must preserve acquisition timeout classification and child bounds"


def test_js_dispatch_preserves_private_descriptor_parent_and_child_guard(monkeypatch):
    # GIVEN the existing worker wrapper with an observable private channel and no native execution
    standalone = importlib.import_module("brewdoc._standalone")
    htmljs = importlib.import_module("brewdoc.htmljs")
    channel = object()
    calls = []
    monkeypatch.setattr(htmljs, "_CHILD_ONLY", False)
    monkeypatch.setattr(htmljs, "Connection", lambda descriptor: (calls.append(descriptor), channel)[1])

    def worker(received, parent_pid):
        """Observe the private guard at the native execution boundary."""
        calls.append((received, parent_pid, htmljs._CHILD_ONLY))

    monkeypatch.setattr(htmljs, "_worker", worker)
    assert htmljs._CHILD_ONLY is False, "the dispatch probe must begin in the parent context"
    # WHEN the validated JS operation enters the existing descriptor wrapper
    code = standalone.main(["--brewdoc-internal", "js", "17", "43210"])
    # THEN the original channel ownership and protected child context remain intact
    assert (code, calls) == (0, [17, (channel, 43210, True)]), (
        "JS dispatch must enter the private-FD child wrapper without bypassing its guarded context")


def test_signal_dispatch_enters_only_the_existing_fresh_child_probe(monkeypatch):
    # GIVEN a signal prerequisite action that can be observed without delivering a native fault
    standalone = importlib.import_module("brewdoc._standalone")
    htmljs = importlib.import_module("brewdoc.htmljs")
    calls = []
    monkeypatch.setattr(htmljs, "_signal_preflight", lambda: calls.append("signal"))
    assert calls == [], "the signal prerequisite must not run before private dispatch"
    # WHEN the zero-operand signal operation is requested
    code = standalone.main(["--brewdoc-internal", "signal"])
    # THEN the existing guarded signal proof is called exactly once
    assert (code, calls) == (0, ["signal"]), (
        "signal dispatch must use the existing fixed native prerequisite without arbitrary code")
