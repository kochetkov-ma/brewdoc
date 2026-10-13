"""Fixed standalone child operations preserve isolated launch and refusal behavior."""

import importlib
import importlib.util
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import socket
import threading
import queue
import struct
import zipfile
from types import SimpleNamespace

import pytest

from brewdoc import htmlurl

BUILDER_SPEC = importlib.util.spec_from_file_location(
    'standalone_builder', Path(__file__).resolve().parents[1] / '.github/workflows/build_standalone.py')
BUILDER = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(BUILDER)


def test_native_zip_member_probe_requires_the_real_cli_refusal_and_preserves_output(tmp_path):
    # GIVEN a valid DOCX with one entry beyond the approved shipping limit
    source, output, expected = BUILDER.zip_xml_refusal_case(tmp_path)
    with zipfile.ZipFile(source) as package:
        assert len(package.infolist()) == 4097, 'the probe crosses the actual shipping member limit by one'
    assert output.read_bytes() == b'preserve existing output\n', 'the existing destination starts with the sentinel'
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    # WHEN the same probe used for producer and public archives invokes the source CLI
    actual = BUILDER.zip_xml_refusal_probe([sys.executable, '-m', 'brewdoc.cli'], tmp_path, environment)
    # THEN the exact full refusal survives and no Markdown replaces the prior output
    assert (actual, output.read_bytes()) == (expected, b'preserve existing output\n'), 'CLI refusal preserves the complete receipt and destination'


@pytest.mark.parametrize('code,receipt_change,suffix,stderr,output_bytes', [
    (0, {}, b'', b'', b'preserve existing output\n'),
    (1, {'reason': 'different refusal'}, b'', b'', b'preserve existing output\n'),
    (1, {}, b'additional stdout\n', b'', b'preserve existing output\n'),
    (1, {}, b'', b'parser traceback', b'preserve existing output\n'),
    (1, {}, b'', b'', b'replaced output\n'),
], ids=['accepted-input', 'wrong-receipt', 'extra-stdout', 'stderr', 'changed-output'])
def test_native_zip_member_probe_rejects_incomplete_consumer_evidence(
        tmp_path, monkeypatch, code, receipt_change, suffix, stderr, output_bytes):
    # GIVEN a consumer response that changes one required refusal observation
    source, output, expected = BUILDER.zip_xml_refusal_case(tmp_path)
    assert source.is_file() is True, 'the probe fixture exists before the consumer runs'
    stdout = (json.dumps({**expected, **receipt_change}, ensure_ascii=True, sort_keys=True) + os.linesep).encode('ascii') + suffix

    def run(arguments, **kwargs):
        """Return the selected consumer observation with its actual output side effect."""
        output.write_bytes(output_bytes)
        return subprocess.CompletedProcess(arguments, code, stdout, stderr)

    monkeypatch.setattr(BUILDER.subprocess, 'run', run)
    # WHEN the verifier compares the complete CLI observation to literal expectations
    with pytest.raises(ValueError, match='^ZIP/XML member refusal changed its receipt or existing output$'):
        BUILDER.zip_xml_refusal_probe(['brewdoc'], tmp_path, {})
    # THEN failed admission retains the observation instead of rewriting output to pass
    assert output.read_bytes() == output_bytes, 'the verifier does not repair a failed consumer observation'


def test_native_verify_logs_tls_probe_stage_before_preserving_refusal(tmp_path, monkeypatch, caplog):
    # GIVEN admitted synthetic packaging edges and a frozen executable refusing at DNS
    output, bundle, root = (tmp_path / name for name in ('output', 'bundle', 'frozen/brewdoc'))
    for directory in (output, bundle / 'ci-package', root):
        directory.mkdir(parents=True)
    archive = output / 'archive.tar.gz'
    archive.write_bytes(b'x')
    identity = {'version': '0.3.3', 'source_sha': 'a' * 40, 'python': '3.12.14', 'target': 'macos-arm64'}
    manifest = {'schema': 1, **identity, 'oracle': [], 'probes': [], 'inventory': {}, 'executable': 'brewdoc/brewdoc',
                'archive': {'filename': archive.name, 'bytes': 1, 'sha256': BUILDER.digest(archive)}}
    BUILDER.write_json(output / 'manifest.json', manifest)
    BUILDER.write_json(bundle / 'ci-package/manifest.json', {'checkout_sha': identity['source_sha'], 'version': identity['version']})
    BUILDER.write_json(root / 'version.json', identity)
    monkeypatch.setattr(BUILDER, 'facts', lambda *args: {'python': identity['python'], 'targets': {'macos-arm64': {'render_js': True}}})
    monkeypatch.setattr(BUILDER, 'validate_manifest', lambda value: None)
    monkeypatch.setattr(BUILDER, 'verification_scope', lambda value: 'smoke')
    monkeypatch.setattr(BUILDER, 'extract_archive', lambda *args: root)
    monkeypatch.setattr(BUILDER, 'consumer_environment', lambda work: {})
    monkeypatch.setattr(BUILDER, 'child_checks', lambda *args: {})
    monkeypatch.setattr(BUILDER, 'zip_xml_refusal_probe', lambda *args: {})
    commands = {('--self-check',): f'self-check: ok (38 checks){os.linesep}'.encode(),
                ('--url', 'https://example.org/', '--timeout', '30'): json.dumps({
                    'file_ok': True, 'acquisition': {'capture_status': 'static'}}).encode()}
    monkeypatch.setattr(BUILDER, 'command', lambda arguments, **kwargs:
                        subprocess.CompletedProcess(arguments, 0, commands[tuple(arguments[1:])], b''))
    receipts = iter([{'file_ok': False, 'units': 0}, {
        'acquisition': {'refusal_stage': 'dns'}, 'reason': 'private remote diagnostic'}])
    calls = []

    def run(arguments, **kwargs):
        """Return the malformed-file refusal followed by the external DNS refusal."""
        calls.append(arguments[1:])
        return subprocess.CompletedProcess(arguments, 1, json.dumps(next(receipts)).encode(), b'')

    monkeypatch.setattr(BUILDER.subprocess, 'run', run)
    assert calls == [], 'the native probe starts without subprocess or network activity'
    # WHEN the real native verifier reaches an external refusal before the TLS handshake
    with pytest.raises(ValueError, match='^Frozen invalid TLS certificate was not refused at TLS$'):
        BUILDER.verify(tmp_path, bundle, output, 'macos-arm64', smoke=True)
    # THEN the existing error remains and the real call path reports only the safe stage
    assert (calls[-1], caplog.messages) == (
        ['--url', 'https://self-signed.badssl.com/', '--timeout', '30'],
        ['Frozen TLS probe failed: exit=1 refusal_stage=dns']), 'native verification exposes the refusal stage without remote diagnostics'


def test_tls_probe_reports_observed_stage_without_remote_diagnostics(caplog):
    # GIVEN a frozen probe refused by DNS before reaching its TLS handshake
    result = subprocess.CompletedProcess(['brewdoc'], 1, json.dumps({
        'acquisition': {'refusal_stage': 'dns'}, 'reason': 'private remote diagnostic',
    }).encode(), b'private process diagnostic')
    assert result.returncode == 1, 'the probe already refused the external request'
    # WHEN the verifier requires refusal at the TLS boundary
    with pytest.raises(ValueError, match='^Frozen invalid TLS certificate was not refused at TLS$'):
        BUILDER.tls_receipt(result)
    # THEN the actual stage is visible without forwarding uncontrolled response fields
    assert caplog.messages == ['Frozen TLS probe failed: exit=1 refusal_stage=dns'], 'only the observed exit and trusted stage are logged'


@pytest.mark.parametrize('stage,code,observed', [
    ('dns', 1, 'dns'), ('connect', 1, 'connect'), ('timeout', 1, 'timeout'),
    ('tls', 0, 'tls'), (None, 1, 'invalid'), ('token-value\nInjected diagnostic', 1, 'invalid'),
])
def test_tls_probe_preserves_exact_refusal_and_redacts_unknown_stage(stage, code, observed, caplog):
    # GIVEN a probe receipt whose stage or exit differs from the exact TLS refusal
    receipt = {'acquisition': {'refusal_stage': stage}}
    result = subprocess.CompletedProcess(['brewdoc'], code, json.dumps(receipt).encode(), b'private stderr')
    assert receipt['acquisition']['refusal_stage'] == stage, 'the synthetic receipt carries the observed stage'
    # WHEN the verifier applies its existing exact exit and stage requirement
    with pytest.raises(ValueError, match='^Frozen invalid TLS certificate was not refused at TLS$'):
        BUILDER.tls_receipt(result)
    # THEN only trusted stage data reaches the diagnostic and the refusal remains exact
    assert caplog.messages == [f'Frozen TLS probe failed: exit={code} refusal_stage={observed}'], 'unknown stage data is replaced before logging'


def test_tls_probe_returns_exact_accepted_receipt_without_logging(caplog):
    # GIVEN the existing admitted exit and TLS refusal stage
    receipt = {'file_ok': False, 'acquisition': {'refusal_stage': 'tls'}}
    result = subprocess.CompletedProcess(['brewdoc'], 1, json.dumps(receipt).encode(), b'')
    assert result.returncode == 1, 'the successful TLS test still requires the refusal exit'
    # WHEN the native verifier admits the exact TLS outcome
    actual = BUILDER.tls_receipt(result)
    # THEN later native evidence retains the whole original receipt without logging it
    assert (actual, caplog.messages) == (receipt, []), 'the accepted receipt remains byte-field equivalent and silent'


@pytest.mark.parametrize("frozen", [True, False], ids=["frozen", "isolated-source"])
def test_dns_lookup_uses_fixed_child_dispatch_without_interpreter_code(tmp_path, monkeypatch, frozen):
    # GIVEN a fixed deadline and an executable whose child launch is observable
    executable = str(tmp_path / "brewdoc")
    calls = []
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
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


@pytest.mark.parametrize("operation,operands,runtime,flags", [
    ("dns", ("localhost", "443", "43210"), "base", ["-I", "-S"]),
    ("signal", (), "venv", ["-I"]),
    ("js", ("17", "43210"), "venv", ["-I"]),
])
def test_windows_source_selects_base_python_only_for_stdlib_dns(tmp_path, monkeypatch, operation, operands, runtime, flags):
    # GIVEN distinct trusted runtime paths and an existing base executable
    standalone = importlib.import_module("brewdoc._standalone")
    base = tmp_path / "base python.exe"
    base.write_bytes(b"runtime")
    executables = {"base": str(base), "venv": str(tmp_path / "venv python.exe")}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", executables["venv"])
    monkeypatch.setattr(sys, "_base_executable", executables["base"])
    assert (Path(sys._base_executable), base.read_bytes()) == (base, b"runtime"), (
        "the trusted base path must identify the prepared executable file")
    # WHEN the fixed source child operation selects its interpreter
    command = standalone.child_command(operation, *operands)
    # THEN only DNS bypasses the venv relay and site initialization
    assert command == [executables[runtime], *flags, str(Path(standalone.__file__).resolve()),
                       "--brewdoc-internal", operation, *operands], (
        "Windows DNS alone must directly own the same runtime's stdlib-only child")


@pytest.mark.parametrize("base_kind", ["missing", "relative", "absent", "directory", "wrong-type"])
def test_invalid_windows_base_runtime_retains_named_dns_refusal(tmp_path, monkeypatch, base_kind):
    # GIVEN an unavailable or invalid trusted base runtime and an observable launch edge
    bases = {"missing": None, "relative": "python.exe", "absent": str(tmp_path / "missing.exe"),
             "directory": str(tmp_path), "wrong-type": 17}
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "_base_executable", bases[base_kind])

    def launch(argv, **options):
        """Observe accidental fallback to an executable outside the trusted base path."""
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b'["127.0.0.1"]\n', b"")

    monkeypatch.setattr(htmlurl.subprocess, "run", launch)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    assert calls == [], "invalid base selection must begin without a child launch"
    # WHEN DNS cannot select the running installation's base interpreter
    with pytest.raises(htmlurl.BrewdocError) as raised:
        htmlurl._dns_lookup("localhost", 443, 100.5)
    # THEN the existing parent boundary refuses without any interpreter fallback
    assert (str(raised.value), raised.value.acquisition_stage, calls) == (
        "URL DNS worker failed", "dns", [],
    ), "invalid base paths must preserve named DNS refusal before process creation"


@pytest.fixture
def windows_dns_edges(monkeypatch):
    """Expose Windows owner-handle lifecycle without starting native processes."""
    standalone = importlib.import_module("brewdoc._standalone")
    calls, watchers = [], []

    def open_owner(access, inherit, owner):
        """Record the exact non-inheritable owner handle request."""
        calls.append(("open", access, inherit, owner))
        return 123

    def wait_owner(handle, timeout):
        """Model an alive owner probe followed by signaled owner death."""
        calls.append(("wait", handle, timeout))
        return {0: 258, 4294967295: 0}[timeout]

    def close_owner(handle):
        """Observe explicit handle cleanup after its wait completes."""
        calls.append(("close", handle))

    def start_thread(*, target, daemon):
        """Retain the daemon's target so the test can deliver owner death deterministically."""
        def start():
            """Record startup without executing the retained watcher."""
            calls.append(("start", daemon))
            watchers.append(target)
        return SimpleNamespace(start=start)

    def resolve(host, port, **options):
        """Record resolution after synchronous owner admission."""
        calls.append(("resolve", host, port, options))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    def stop(code):
        """Capture process termination without exiting the test interpreter."""
        calls.append(("exit", code))
        raise SystemExit(code)

    api = SimpleNamespace(SYNCHRONIZE=1048576, INFINITE=4294967295, WAIT_TIMEOUT=258,
                          OpenProcess=open_owner, WaitForSingleObject=wait_owner, CloseHandle=close_owner)
    monkeypatch.setitem(sys.modules, "_winapi", api)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "getppid", lambda: 76543)
    monkeypatch.setattr(os, "_exit", stop)
    monkeypatch.setattr(threading, "Thread", start_thread)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    return standalone, api, calls, watchers


def test_windows_dns_admits_logical_owner_before_resolution_and_stops_after_owner_death(windows_dns_edges, capsys):
    # GIVEN an alive logical owner whose PID differs from the immediate redirector parent
    standalone, _api, calls, watchers = windows_dns_edges
    assert os.getppid() == 76543, "the DNS child must see the intermediate launcher's PID"
    # WHEN DNS acquires its owner's handle and resolves while that owner remains alive
    standalone._dns_worker("localhost", 443, 43210)
    # THEN admission precedes resolution without trusting immediate parent identity
    assert (calls, capsys.readouterr().out) == ([
        ("open", 1048576, False, 43210), ("wait", 123, 0), ("start", True),
        ("resolve", "localhost", 443, {"type": socket.SOCK_STREAM}),
    ], '["127.0.0.1"]\n'), "an alive logical owner must permit exact DNS output before its death"
    # WHEN the actual owner signals the already acquired process handle
    with pytest.raises(SystemExit) as stopped:
        watchers[0]()
    # THEN the watcher closes that handle and terminates despite the unchanged redirector PID
    assert (stopped.value.code, calls[-3:]) == (
        1, [("wait", 123, 4294967295), ("close", 123), ("exit", 1)],
    ), "owner death must close the waited handle and stop the resolver child"


def _failed_after(action):
    """Preserve an observable API call before raising its operating-system failure."""
    def fail(*args, **options):
        """Raise after the mocked API edge records its arguments."""
        action(*args, **options)
        raise OSError("test API failure")
    return fail


@pytest.mark.parametrize("phase,expected", [
    ("open", [("open", 1048576, False, 43210), ("exit", 1)]),
    ("probe", [("open", 1048576, False, 43210), ("wait", 123, 0), ("close", 123), ("exit", 1)]),
    ("exited", [("open", 1048576, False, 43210), ("wait", 123, 0), ("close", 123), ("exit", 1)]),
    ("unexpected", [("open", 1048576, False, 43210), ("wait", 123, 0), ("close", 123), ("exit", 1)]),
    ("start", [("open", 1048576, False, 43210), ("wait", 123, 0), ("start", True), ("close", 123), ("exit", 1)]),
])
def test_windows_dns_refuses_owner_admission_failures_before_resolution(windows_dns_edges, monkeypatch, capsys, phase, expected):
    # GIVEN a failing owner admission edge and a resolver that records any premature I/O
    standalone, api, calls, _watchers = windows_dns_edges
    original_wait, original_thread = api.WaitForSingleObject, threading.Thread

    def exited(handle, timeout):
        """Return an already signaled owner after recording the zero-time probe."""
        original_wait(handle, timeout)
        return 0

    def unexpected(handle, timeout):
        """Return an inadmissible probe verdict after recording its handle."""
        original_wait(handle, timeout)
        return 128

    def failed_start(**options):
        """Fail daemon startup before its retained target can wait on the owner."""
        thread = original_thread(**options)
        thread.start = _failed_after(thread.start)
        return thread

    changes = {"open": (api, "OpenProcess", _failed_after(api.OpenProcess)),
               "probe": (api, "WaitForSingleObject", _failed_after(original_wait)),
               "exited": (api, "WaitForSingleObject", exited),
               "unexpected": (api, "WaitForSingleObject", unexpected),
               "start": (threading, "Thread", failed_start)}
    monkeypatch.setattr(*changes[phase])
    assert calls == [], "owner admission must fail before resolver or watcher actions"
    # WHEN the worker reaches the selected owner lifetime failure
    with pytest.raises(SystemExit) as stopped:
        standalone._dns_worker("localhost", 443, 43210)
    # THEN acquired non-waiting handles close and every failure refuses before DNS
    output = capsys.readouterr()
    assert (stopped.value.code, calls, output.out, output.err) == (1, expected, "", ""), (
        "owner admission failures must close their handle and terminate without DNS I/O")


@pytest.mark.parametrize("phase", ["wait", "close"])
def test_windows_dns_watcher_always_exits_after_wait_or_close_failure(windows_dns_edges, monkeypatch, capsys, phase):
    # GIVEN an admitted DNS owner and a daemon that has not yet consumed owner death
    standalone, api, calls, watchers = windows_dns_edges
    standalone._dns_worker("localhost", 443, 43210)
    assert capsys.readouterr().out == '["127.0.0.1"]\n', "the admitted owner must first permit exact DNS output"
    changes = {"wait": (api, "WaitForSingleObject", _failed_after(api.WaitForSingleObject)),
               "close": (api, "CloseHandle", _failed_after(api.CloseHandle))}
    monkeypatch.setattr(*changes[phase])
    # WHEN the owner's waiting or cleanup API fails inside its watcher
    with pytest.raises(SystemExit) as stopped:
        watchers[0]()
    # THEN cleanup failure cannot leave a live resolver without an owner watchdog
    assert (stopped.value.code, calls[-3:]) == (
        1, [("wait", 123, 4294967295), ("close", 123), ("exit", 1)],
    ), "the DNS child must terminate unconditionally after wait or handle cleanup failure"


def test_frozen_windows_dns_keeps_its_bundle_executable_without_a_base_runtime(tmp_path, monkeypatch):
    # GIVEN a frozen bundle whose DNS operation needs no external Python interpreter
    standalone = importlib.import_module("brewdoc._standalone")
    executable = str(tmp_path / "brewdoc.exe")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(sys, "_base_executable", None)
    assert sys._base_executable is None, "frozen DNS must work without selecting base Python"
    # WHEN the admitted bundle dispatches its fixed DNS child
    command = standalone.child_command("dns", "localhost", "443", "43210")
    # THEN the embedded runtime is selected with no source-interpreter flags
    assert command == [executable, "--brewdoc-internal", "dns", "localhost", "443", "43210"], (
        "frozen Windows DNS must retain exact bundle dispatch without base-runtime fallback")


def _blocked_dns_probe(tmp_path):
    """Publish native runtime identity only after owner admission, then block the resolver."""
    standalone = importlib.import_module("brewdoc._standalone")
    probe = tmp_path / "blocked DNS probe.py"
    probe.write_text(
        "import json,os,runpy,socket,struct,sys,threading\n"
        "worker=runpy.run_path(%r)\n"
        "def blocked(*args,**options):\n"
        "    print(json.dumps({'pid':os.getpid(),'parent':os.getppid(),"
        "'base':sys._base_executable,'version':list(sys.version_info[:3]),"
        "'bits':struct.calcsize('P')*8,'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,"
        "'site_loaded':'site' in sys.modules,'brewdoc_loaded':'brewdoc' in sys.modules}),flush=True)\n"
        "    threading.Event().wait(20)\n"
        "    raise OSError('test resolver remained blocked')\n"
        "socket.getaddrinfo=blocked\n"
        "raise SystemExit(worker['main'](sys.argv[1:]))\n" % str(Path(standalone.__file__).resolve()),
        encoding="utf-8")
    return probe


def _read_probe_record(process):
    """Bound one readiness read before controlling the recorded process."""
    records = queue.Queue()

    def read():
        """Deliver the next readiness line."""
        records.put(process.stdout.readline())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    record = json.loads(records.get(timeout=5))
    reader.join(5)
    assert reader.is_alive() is False, "the bounded readiness reader must finish before process control"
    return record


def _close_windows_probe(handle):
    """Reap the recorded test-owned process even when an earlier assertion fails."""
    import _winapi
    try:
        with contextlib.suppress(PermissionError):
            _winapi.TerminateProcess(handle, 1)
        assert _winapi.WaitForSingleObject(handle, 5000) == 0, "the owned DNS probe must be terminated before handle cleanup"
    finally:
        _winapi.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Direct Windows base-runtime selection requires a Windows runner.")
def test_windows_production_dns_succeeds_outside_checkout_with_hostile_python_paths(tmp_path):
    # GIVEN a copied entry and poisoned startup paths without Python on PATH
    standalone = importlib.import_module("brewdoc._standalone")
    script = tmp_path / "direct DNS with spaces.py"
    shutil.copyfile(standalone.__file__, script)
    poison = tmp_path / "poison"
    poison.mkdir()
    marker = tmp_path / "site-imported"
    (poison / "sitecustomize.py").write_text(
        "from pathlib import Path\nPath(%r).write_text('unexpected startup')\n" % str(marker), encoding="utf-8")
    environment = {"PATH": str(tmp_path / "empty-path"), "PYTHONPATH": str(poison),
                   "PYTHONHOME": str(poison), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
                   "SystemRoot": os.environ["SystemRoot"], "WINDIR": os.environ["WINDIR"],
                   "__PYVENV_LAUNCHER__": sys.executable}
    command = standalone.child_command("dns", "127.0.0.1", "443", str(os.getpid()))
    command[3] = str(script)
    assert command[:3] == [sys._base_executable, "-I", "-S"], (
        "production Windows DNS must directly select the running installation's isolated base interpreter")
    # WHEN the actual selected runtime resolves through the relocated entry
    completed = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, timeout=5, check=False)
    # THEN ordinary and internal Python path overrides cannot import startup hooks or change DNS output
    assert (completed.returncode, completed.stdout, completed.stderr, marker.exists()) == (
        0, ('["127.0.0.1"]' + os.linesep).encode("ascii"), b"", False,
    ), "direct production DNS must preserve exact success under hostile cwd and Python environment overrides"


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows process handles require a Windows runner.")
def test_windows_direct_dns_timeout_reaps_the_same_runtime_worker(tmp_path, monkeypatch):
    # GIVEN an observable direct DNS child that blocks after native owner admission
    import _winapi
    probe = _blocked_dns_probe(tmp_path)
    processes, handles, identities = [], [], []
    original_popen = subprocess.Popen

    def launch(command, **options):
        """Use production interpreter selection while replacing only the resolver I/O edge."""
        command = list(command)
        command[3] = str(probe)
        process = original_popen(command, **options)
        processes.append(process)
        handles.append(_winapi.OpenProcess(1048577, False, process.pid))
        identities.append(_read_probe_record(process))
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    assert processes == [], "the native timeout probe must start without any owned child"
    try:
        # WHEN the real parent-owned DNS subprocess reaches its deadline after readiness
        with pytest.raises(htmlurl.URLTimeoutError) as raised:
            htmlurl._dns_lookup("localhost", 443, 100.25)
        # THEN the physical worker is the chosen base runtime and timeout killed/reaped it directly
        assert (str(raised.value), raised.value.acquisition_stage, processes[0].returncode,
                identities, _winapi.WaitForSingleObject(handles[0], 0)) == (
            "URL DNS deadline exceeded", "dns", 1, [{
                "pid": processes[0].pid, "parent": os.getpid(), "base": sys._base_executable,
                "version": list(sys.version_info[:3]), "bits": struct.calcsize("P") * 8,
                "isolated": 1, "no_site": 1, "site_loaded": False, "brewdoc_loaded": False,
            }], 0,
        ), "deadline cancellation must directly own the same isolated runtime without a relay or orphan"
    finally:
        for process in processes:
            process.kill()
            process.communicate(timeout=5)
        for handle in handles:
            _close_windows_probe(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows owner death requires a Windows runner.")
def test_windows_direct_dns_stops_when_its_logical_owner_exits(tmp_path):
    # GIVEN a real owner that uses production DNS selection without an enclosing venv relay
    import _winapi
    standalone = importlib.import_module("brewdoc._standalone")
    probe = _blocked_dns_probe(tmp_path)
    owner_script = tmp_path / "DNS owner.py"
    owner_script.write_text(
        "import json,os,runpy,subprocess,sys\n"
        "worker=runpy.run_path(%r)\n"
        "command=worker['child_command']('dns','localhost','443',str(os.getpid()))\n"
        "command[3]=%r\n"
        "child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
        "print(json.dumps({'owner':os.getpid(),'child':child.pid}),flush=True)\n"
        "sys.stdout.buffer.write(child.stdout.readline());sys.stdout.flush()\n"
        "sys.stdin.buffer.read(1)\n"
        "os._exit(0)\n" % (str(Path(standalone.__file__).resolve()), str(probe)), encoding="utf-8")
    handles = []
    owner = subprocess.Popen([sys._base_executable, "-I", "-S", str(owner_script)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        owner_record = _read_probe_record(owner)
        # PROCESS_QUERY_LIMITED_INFORMATION permits reading the worker's exit status.
        handles.append(_winapi.OpenProcess(_winapi.SYNCHRONIZE | 0x0001 | 0x1000, False, owner_record["child"]))
        identity = _read_probe_record(owner)
        assert (owner.poll(), owner_record["owner"], identity["pid"], identity["parent"]) == (
            None, owner.pid, owner_record["child"], owner.pid,
        ), "an alive owner must admit its actual direct resolver before the death handshake"
        # WHEN the owner exits while the resolver remains blocked
        stdout, stderr = owner.communicate(input=b"x", timeout=5)
        verdict = _winapi.WaitForSingleObject(handles[0], 5000)
        # THEN the owner handle stops the physical worker without relying on unchanged getppid
        assert (owner.returncode, stdout, stderr, verdict, _winapi.GetExitCodeProcess(handles[0])) == (
            0, b"", b"", 0, 1,
        ), "owner death must promptly terminate the direct resolver and leave no orphan"
    finally:
        owner.kill()
        owner.communicate(timeout=5)
        for handle in handles:
            _close_windows_probe(handle)
