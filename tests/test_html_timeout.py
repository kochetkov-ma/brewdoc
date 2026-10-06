"""Per-request timeout behavior keeps URL waiting separate from hard refusals."""

import contextlib
import hashlib
import json
import os
import signal
import socket
import sys
import threading
import time

import pytest

from brewdoc import cli, htmlurl
from brewdoc.common import BrewdocError


URL = "https://fixture.test/article"
MARKDOWN = "# Ready article\n"
RECEIPT = {"receipt_schema": "brewdoc.receipt/2", "route": "html", "file_ok": True}
MAX_TIMEOUT_SECONDS = min(threading.TIMEOUT_MAX, 2147483.647)
CHAPTER = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Untitled"\n\n'


@pytest.mark.parametrize("render_js", [False, True], ids=["static", "javascript"])
@pytest.mark.parametrize("arguments,expected_timeout", [
    pytest.param([], 10.0, id="default-ten-seconds"),
    pytest.param(["--timeout", "0.125"], 0.125, id="fractional-override"),
    pytest.param(["--timeout", "20"], 20.0, id="larger-override"),
])
def test_url_cli_forwards_each_requests_effective_timeout_once(render_js, arguments, expected_timeout, monkeypatch, capsys):
    # GIVEN an observable URL acquisition boundary with an exact ready result.
    calls = []

    def acquire(url, out=None, **options):
        """Observe per-call options without network or native execution."""
        calls.append((url, out, options))
        return 0, RECEIPT, MARKDOWN

    monkeypatch.setattr(htmlurl, "_run_url", acquire)
    assert calls == [], "no acquisition may precede CLI validation"
    # WHEN this request selects a default or explicit timeout.
    code = cli.main(["--url", URL] + ["--render-js"] * render_js + arguments)
    # THEN both acquisition modes receive exactly one effective per-call value.
    assert (code, calls, capsys.readouterr().out) == (
        0, [(URL, None, {"render_js": render_js, "timeout": expected_timeout,
                        "sheets": None, "artifact_outputs": None})],
        json.dumps(RECEIPT, sort_keys=True) + "\n" + MARKDOWN,
    ), "the CLI must pass the request's effective timeout and preserve output"


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "invalid"],
                         ids=["zero", "negative", "nan", "positive-infinity", "negative-infinity", "malformed"])
def test_invalid_timeout_cli_values_refuse_before_io_and_preserve_existing_output(value, tmp_path, monkeypatch, capsys):
    # GIVEN invalid timeout input, an existing output and observable I/O boundaries.
    output = tmp_path / "article.md"
    output.write_bytes(b"existing article\n")
    calls = []
    monkeypatch.setattr(htmlurl, "_run_url", lambda *args, **options: calls.append("url"))
    monkeypatch.setattr(cli, "run", lambda *args, **options: calls.append("local"))
    assert (calls, output.read_bytes()) == ([], b"existing article\n"), "validation starts without I/O"
    # WHEN the CLI parses a nonpositive, nonfinite or malformed URL timeout.
    with pytest.raises(SystemExit) as refused:
        cli.main(["--url", URL, "--timeout=" + value, "--out", str(output)])
    # THEN usage failure cannot start acquisition, conversion or output mutation.
    assert (refused.value.code, calls, capsys.readouterr().out, output.read_bytes()) == (
        2, [], "", b"existing article\n",
    ), "invalid timeout values must fail before any I/O or receipt publication"


@pytest.mark.parametrize("arguments", [
    pytest.param(["article.html"], id="local-document"),
    pytest.param(["--self-check"], id="self-check"),
    pytest.param([], id="missing-url"),
])
def test_explicit_timeout_requires_url_before_local_work(arguments, monkeypatch, capsys):
    # GIVEN local, self-check or empty requests with observable work boundaries.
    calls = []
    monkeypatch.setattr(htmlurl, "_run_url", lambda *args, **options: calls.append("url"))
    monkeypatch.setattr(cli, "run", lambda *args, **options: calls.append("local"))
    monkeypatch.setattr(cli, "self_check", lambda: calls.append("self-check"))
    assert calls == [], "a timeout option must be validated before work starts"
    # WHEN a valid explicit timeout is supplied without a URL.
    with pytest.raises(SystemExit) as refused:
        cli.main(arguments + ["--timeout", "10"])
    # THEN the option is a usage error with no local work or acquisition.
    assert (refused.value.code, calls, capsys.readouterr().out) == (
        2, [], "",
    ), "an explicit URL timeout must never change local or self-check behavior"


def test_local_cli_without_timeout_keeps_its_original_route_and_receipt(monkeypatch, capsys):
    # GIVEN a ready local result and an acquisition boundary that must stay unused.
    calls = []
    receipt = {"receipt_schema": "brewdoc.receipt/1", "route": "html", "file_ok": True}

    def local(path, out=None, **options):
        """Record the unchanged local route without opening an input document."""
        calls.append((path, out, options))
        return 0, receipt, MARKDOWN

    monkeypatch.setattr(cli, "run", local)
    monkeypatch.setattr(htmlurl, "_run_url", lambda *args, **options: calls.append("url"))
    assert calls == [], "the local route starts without URL acquisition"
    # WHEN converting a local document without the URL-only timeout option.
    code = cli.main(["article.html"])
    # THEN no timeout keyword or acquisition envelope changes the local contract.
    assert (code, calls, capsys.readouterr().out) == (
        0, [("article.html", None, {"sheets": None, "artifact_outputs": None})],
        json.dumps(receipt, sort_keys=True) + "\n" + MARKDOWN,
    ), "local conversion must retain its original arguments and receipt"


def test_timeout_validator_accepts_the_last_representable_clock_millisecond():
    # GIVEN the explicit int32 millisecond clock and supported threading boundary.
    assert MAX_TIMEOUT_SECONDS * 1000 == 2147483647, "the supported platform must admit the last int32 clock millisecond"
    # WHEN validating the inclusive numeric boundary without starting a wait.
    seconds = htmlurl._timeout_seconds(MAX_TIMEOUT_SECONDS)
    # THEN the final valid millisecond is retained exactly.
    assert seconds == MAX_TIMEOUT_SECONDS, "the maximum representable clock duration is inclusive"


@pytest.mark.parametrize("value", [
    pytest.param(True, id="true-is-not-one-second"),
    pytest.param(False, id="false-is-not-zero-seconds"),
    pytest.param(None, id="none"),
    pytest.param("10", id="private-string-is-not-numeric"),
    pytest.param([], id="list"),
    pytest.param(0, id="zero"),
    pytest.param(-1, id="negative"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(MAX_TIMEOUT_SECONDS + 0.001, id="one-clock-millisecond-over"),
])
def test_timeout_validator_rejects_invalid_private_values_and_unrepresentable_clock(value):
    # GIVEN a value that cannot be a valid private numeric waiting budget.
    # WHEN validating before acquisition or native-process setup.
    with pytest.raises(BrewdocError):
        htmlurl._timeout_seconds(value)
    # THEN invalid values cannot become socket or native clock durations.


def test_url_cli_accepts_the_representable_maximum_without_a_hidden_one_hour_cap(monkeypatch, capsys):
    # GIVEN a boundary duration and an observable acquisition result.
    calls = []

    def acquire(url, out=None, **options):
        """Record an accepted duration without actually waiting for it."""
        calls.append(options["timeout"])
        return 0, RECEIPT, MARKDOWN

    monkeypatch.setattr(htmlurl, "_run_url", acquire)
    assert calls == [], "maximum-value validation must precede acquisition"
    # WHEN the CLI receives the inclusive last representable duration.
    code = cli.main(["--url", URL, "--timeout", str(MAX_TIMEOUT_SECONDS)])
    # THEN it forwards the exact finite value while preserving output.
    assert (code, calls, capsys.readouterr().out) == (
        0, [MAX_TIMEOUT_SECONDS], json.dumps(RECEIPT, sort_keys=True) + "\n" + MARKDOWN,
    ), "a concrete clock limit must not introduce an arbitrary site waiting cap"


def test_url_cli_refuses_one_millisecond_beyond_the_representable_clock_before_io(monkeypatch, capsys):
    # GIVEN a duration beyond the clock ABI and an observable acquisition boundary.
    calls = []

    def acquire(url, out=None, **options):
        """Expose accidental acquisition without network or native work."""
        calls.append(options["timeout"])
        return 0, RECEIPT, MARKDOWN

    monkeypatch.setattr(htmlurl, "_run_url", acquire)
    assert calls == [], "an unrepresentable clock starts without acquisition"
    # WHEN the CLI parses one millisecond beyond its representable boundary.
    with pytest.raises(SystemExit) as refused:
        cli.main(["--url", URL, "--timeout", f"{MAX_TIMEOUT_SECONDS + 0.001:.3f}"])
    # THEN usage refusal prevents acquisition and stdout mutation.
    assert (refused.value.code, calls, capsys.readouterr().out) == (
        2, [], "",
    ), "invalid clock durations must refuse before I/O instead of wrapping"


@pytest.mark.parametrize("value", [True, False, None, "10", 0, -1, float("nan"), float("inf")])
def test_invalid_private_url_timeout_reports_its_own_refusal_before_io(value, tmp_path, monkeypatch):
    # GIVEN invalid private options, existing output and observable I/O boundaries.
    output = tmp_path / "article.md"
    output.write_bytes(b"existing article\n")
    calls = []
    monkeypatch.setattr(htmlurl, "_fetch", lambda *args, **options: calls.append("network"))
    monkeypatch.setattr(htmlurl, "run", lambda *args, **options: calls.append("conversion"))
    assert (calls, output.read_bytes()) == ([], b"existing article\n"), "private validation precedes I/O"
    # WHEN the private URL edge validates its own timeout argument.
    code, receipt, markdown = htmlurl._run_url(URL, output, timeout=value)
    acquisition = receipt["acquisition"]
    # THEN finite refusal metadata identifies timeout instead of unrelated workbook options.
    assert (code, receipt["reason"], markdown, calls, output.read_bytes(),
            acquisition["timeout_seconds"], acquisition["counts"]["requests"], acquisition["capture_status"]) == (
        1, "URL timeout must be a finite positive number", "", [], b"existing article\n",
        None, 0, "refused",
    ), "invalid timeout must retain its own safe reason and leave all I/O untouched"


@pytest.mark.parametrize("value", [MAX_TIMEOUT_SECONDS + 0.001, 10 ** 400], ids=["next-clock-millisecond", "huge-positive-integer"])
def test_oversized_positive_timeout_reports_the_supported_maximum_before_io(value, monkeypatch, tmp_path):
    # GIVEN a positive numeric value beyond the concrete clock representation.
    maximum = f"URL timeout exceeds supported clock range (maximum {MAX_TIMEOUT_SECONDS} seconds)"
    output = tmp_path / "article.md"
    output.write_bytes(b"existing article\n")
    calls = []
    monkeypatch.setattr(htmlurl, "_fetch", lambda *args, **options: calls.append("network"))
    assert (calls, output.read_bytes()) == ([], b"existing article\n"), "range validation must precede acquisition"
    # WHEN the pure validator and private receipt edge reject the same oversized value.
    with pytest.raises(BrewdocError) as refused:
        htmlurl._timeout_seconds(value)
    code, receipt, markdown = htmlurl._run_url(URL, output, timeout=value)
    # THEN both diagnostics state the usable maximum without forwarding arbitrary text.
    assert (str(refused.value), code, receipt["reason"], markdown, calls, output.read_bytes(),
            receipt["acquisition"]["timeout_seconds"], receipt["acquisition"]["counts"]["requests"]) == (
        maximum, 1, maximum, "", [], b"existing article\n", None, 0,
    ), "positive oversized values need an actionable range error rather than a finite-positive claim"


def test_oversized_timeout_cli_usage_identifies_the_supported_clock_range(monkeypatch, capsys):
    # GIVEN a finite positive CLI value beyond the maximum and no started acquisition.
    calls = []
    monkeypatch.setattr(htmlurl, "_run_url", lambda *args, **options: calls.append("acquisition"))
    assert calls == [], "invalid range input starts without acquisition"
    # WHEN argparse validates one clock millisecond above the supported range.
    with pytest.raises(SystemExit) as refused:
        cli.main(["--url", URL, "--timeout", f"{MAX_TIMEOUT_SECONDS + 0.001:.3f}"])
    captured = capsys.readouterr()
    # THEN usage stderr gives the exact maximum while stdout and I/O remain untouched.
    assert (refused.value.code, calls, captured.out, captured.err.splitlines()[-1]) == (
        2, [], "", f"brewdoc: error: argument --timeout: URL timeout exceeds supported clock range (maximum {MAX_TIMEOUT_SECONDS} seconds)",
    ), "CLI range refusal must explain the supported maximum before any I/O"


def test_huge_negative_integer_timeout_keeps_the_generic_positive_value_refusal():
    # GIVEN a nonpositive integer that also cannot be converted to a float.
    value = -(10 ** 400)
    # WHEN the numeric validator rejects it without a waiting operation.
    with pytest.raises(BrewdocError) as refused:
        htmlurl._timeout_seconds(value)
    # THEN the sign failure stays distinct from a positive oversized duration.
    assert str(refused.value) == "URL timeout must be a finite positive number", "negative overflow must not claim a usable positive range"


@pytest.mark.parametrize("render_js,timeout,expected", [
    pytest.param(True, 20, (20.0, 20.0, "request"), id="configured-js-refusal"),
    pytest.param(False, 20, (20.0, None, None), id="static-has-no-js-interval"),
    pytest.param(True, 0, (None, None, None), id="invalid-timeout-has-no-configuration"),
])
def test_early_url_refusal_retains_effective_timeout_configuration_before_io(render_js, timeout, expected, monkeypatch, tmp_path):
    # GIVEN a refused URL before prerequisites, network or output mutation.
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    calls = []
    from brewdoc import htmljs
    monkeypatch.setattr(htmlurl, "_fetch", lambda *args, **options: calls.append("network"))
    monkeypatch.setattr(htmljs, "_check_runtime", lambda **options: calls.append("runtime"))
    assert (calls, output.read_bytes()) == ([], b"previous output\n"), "configuration must be recorded before any I/O"
    # WHEN URL grammar refuses a valid configured budget or timeout validation fails.
    code, receipt, markdown = htmlurl._run_url("file:///not-http", output, render_js=render_js, timeout=timeout)
    acquisition = receipt["acquisition"]
    # THEN valid JS configuration remains visible even without acquired HTML.
    assert (code, markdown, calls, output.read_bytes(),
            (acquisition["timeout_seconds"], acquisition["soft_window_seconds"], acquisition["soft_window_origin"])) == (
        1, "", [], b"previous output\n", expected,
    ), "early refusal must retain configured T/request while static and invalid values stay null"


def _html_response(body, *, deadline, budget, **options):
    """Serve complete synthetic HTML at the controlled acquisition boundary."""
    budget["accepted_requests"] += 1
    budget["decoded_bytes"] += len(body)
    digest = hashlib.sha256(body).hexdigest()
    return {"status": 200, "headers": (("Content-Type", "text/html"),),
            "entity": body, "body": body, "entity_sha256": digest,
            "body_sha256": digest, "final_url": URL}


def _chapter_body(markdown):
    """Observe exact content after the existing document metadata envelope."""
    parts = markdown.split(CHAPTER)
    assert len(parts) == 2, "successful fixture output must contain exactly one published chapter"
    return parts[1]


@pytest.mark.skipif(sys.platform != "linux", reason="Native timeout regressions require isolated Linux.")
@pytest.mark.parametrize("script", [
    pytest.param("document.querySelector('p').textContent='Updated article';while(true){}",
                 id="committed-dom-before-loop"),
    pytest.param("document.querySelector('p').textContent='Updated article';document.write('<p>Uncommitted buffer</p>');while(true){}",
                 id="buffered-write-is-not-flushed"),
])
def test_native_timing_stop_preserves_committed_dom_and_discards_script_write_buffer(script, monkeypatch):
    # GIVEN completed HTML and an actual DOM mutation before an endless script.
    body = ('<html><body><p>Initial article</p><script>' + script + '</script></body></html>').encode()
    requests = []

    def fetch(url, **options):
        """Supply original HTML without introducing a second network acquisition."""
        requests.append(url)
        return _html_response(body, **options)

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert requests == [], "the HTML source must be acquired exactly once"
    # WHEN the existing guarded invocation interrupts the actual running script.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True)
    acquisition = receipt["acquisition"]
    # THEN current completed content survives without flushing uncommitted writes.
    assert code == 0, "a trusted timing stop must publish its acquired content"
    assert (_chapter_body(markdown), requests, acquisition["capture_status"], acquisition["errors"],
            acquisition["snapshot"]["kind"], acquisition.get("timeout_seconds")) == (
        "Updated article\n", [URL], "partial",
        [{"category": "capture_timeout", "resource_url": None}], "javascript_dom", 10.0,
    ), "a trusted timing stop must retain actual DOM changes and discard the script's write buffer"


class _PinnedSocket(socket.socket):
    """Keep real local HTTP bytes while presenting an already validated public peer."""

    def connect(self, address):
        """Record the production numeric connect without external network access."""
        self.address = address

    def getpeername(self):
        """Return the public address pinned by the controlled DNS answer."""
        return "93.184.216.34", 80


def test_response_headers_after_five_seconds_use_the_remaining_request_budget(monkeypatch):
    # GIVEN real HTTP bytes whose headers arrive after the obsolete socket ceiling.
    client_stream, server = socket.socketpair()
    client = _PinnedSocket(fileno=client_stream.detach())
    release = threading.Event()
    received = []
    body = b"<p>Late article</p>"

    def respond():
        """Release complete synthetic headers through the actual HTTP reader."""
        with server, server.makefile("rb") as incoming:
            received.append(incoming.readline())
            release.wait(8)
            with contextlib.suppress(OSError):
                server.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 19\r\n\r\n" + body)

    thread = threading.Thread(target=respond, daemon=True)
    timer = threading.Timer(6.1, release.set)
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(htmlurl.socket, "socket", lambda *args: client)
    assert len(body) == 19, "the delayed response must have a complete known body"
    thread.start()
    timer.start()
    # WHEN real connect/request/body logic has a twenty-second request deadline.
    try:
        started = time.monotonic()
        captured = htmlurl._request("http://fixture.test/article", started + 20, "text/html")
        elapsed = time.monotonic() - started
    finally:
        release.set()
        timer.cancel()
        timer.join()
        client.close()
        thread.join(1)
    # THEN actual late headers are admitted and every owned transport object closes.
    assert (captured, received, client.address, client.fileno(), thread.is_alive()) == (
        {"status": 200, "headers": (("Content-Type", "text/html"), ("Content-Length", "19")),
         "entity": body, "body": body}, [b"GET /article HTTP/1.1\r\n"],
        ("93.184.216.34", 80), -1, False,
    ), "remaining request time must admit real late headers and reap controlled transport"
    assert 6 <= elapsed < 10, "headers must really arrive beyond five seconds within the bounded fixture delay"


@pytest.mark.skipif(sys.platform != "linux", reason="Native timeout regressions require isolated Linux.")
@pytest.mark.parametrize("timeout,expected_markdown,status,errors", [
    pytest.param(1.0, "Initial article\n", "partial", [{"category": "capture_timeout", "resource_url": None}],
                 id="short-budget-preserves-initial-content"),
    pytest.param(20.0, "Late article\n", "settled", [], id="larger-budget-admits-content-after-twelve-seconds"),
])
def test_requested_timeout_controls_actual_late_timer_content(timeout, expected_markdown, status, errors, monkeypatch):
    # GIVEN useful initial HTML and real page content arriving after twelve seconds.
    body = b"<html><body><p>Initial article</p><script>setTimeout(()=>document.querySelector('p').textContent='Late article',12500)</script></body></html>"
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(body, **options))
    assert b"12500" in body, "the real page timer must exceed the historical twelve-second window"
    # WHEN this individual request supplies its shorter or longer waiting budget.
    started = time.monotonic()
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=timeout)
    elapsed = time.monotonic() - started
    acquisition = receipt["acquisition"]
    # THEN the configured budget governs content and provenance without a fresh window.
    assert code == 0, "a waiting timeout must preserve acquired content for conversion"
    assert (_chapter_body(markdown), acquisition["capture_status"], acquisition["errors"],
            acquisition["timeout_seconds"], acquisition["soft_window_seconds"], acquisition.get("soft_window_origin")) == (
        expected_markdown, status, errors, timeout, timeout, "request",
    ), "actual timer content must follow each request's budget instead of the fixed twelve-second window"
    assert elapsed < timeout + 2.5, "loading must stop within its budget plus the explicit recovery/reap allowance"


@pytest.mark.skipif(sys.platform != "linux", reason="Native timeout regressions require isolated Linux.")
def test_late_xhr_timeout_keeps_committed_dom_without_running_network_callbacks(monkeypatch):
    # GIVEN committed DOM and an XHR whose host response cannot finish in time.
    body = b"""<html><body><p>Initial article</p><script>
document.querySelector('p').textContent='Committed article';
const xhr=new XMLHttpRequest();
xhr.onload=()=>document.querySelector('p').textContent='Late completion';
xhr.onerror=()=>document.querySelector('p').textContent='Error callback';
xhr.open('GET','/data');xhr.send();
</script></body></html>"""
    requested = []
    expired = threading.Event()

    def initial(**options):
        """Return the complete main body before the stalled API request."""
        return _html_response(body, **options)

    def late(**options):
        """Reach the real trusted deadline without exposing an incomplete response."""
        options["budget"]["accepted_requests"] += 1
        expired.wait(max(0, options["deadline"] - time.monotonic()) + 0.02)
        return htmlurl._remaining(options["deadline"])

    def fetch(url, *, kind="html", **options):
        """Dispatch controlled source and deadline expiry through the parent seam."""
        requested.append((url, kind))
        return {"html": initial, "api": late}[kind](**options)

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert requested == [], "initial HTML and the one XHR start unacquired"
    # WHEN the shared request budget expires during the parent-owned XHR.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=1)
    acquisition = receipt["acquisition"]
    # THEN completed mutations survive while the timed-out XHR remains pending.
    assert code == 0, "late ancillary timing must preserve the acquired page"
    assert (_chapter_body(markdown), requested, acquisition["capture_status"], acquisition["errors"],
            acquisition["pending"], acquisition["counts"], acquisition["snapshot"]["kind"]) == (
        "Committed article\n", [(URL, "html"), ("https://fixture.test/data", "api")], "partial",
        [{"category": "capture_timeout", "resource_url": None}],
        {"requests": 1, "timers": 0, "modules": 0, "promises": 5, "jobs": False},
        {"requests": 2, "promise_jobs": 0, "timer_callbacks": 0}, "javascript_dom",
    ), "timeout recovery must neither complete the XHR nor dispatch page load/error callbacks"


def _replace_worker(monkeypatch, source):
    """Track one real owned Python worker with a trusted failure-state fixture."""
    from brewdoc import htmljs
    original = htmljs.subprocess.Popen
    processes = []

    def start(arguments, **options):
        """Preserve real isolation/reaping while selecting trusted worker behavior."""
        descriptor = options["pass_fds"][0]
        process = original([sys.executable, "-I", "-c", source, str(descriptor)], **options)
        processes.append(process)
        return process

    monkeypatch.setattr(htmljs, "_check_runtime", lambda *args, **options: None)
    monkeypatch.setattr(htmljs.subprocess, "Popen", start)
    return processes


@pytest.mark.skipif(sys.platform != "linux", reason="Owned worker cleanup requires isolated Linux.")
def test_parent_owned_timeout_fallback_labels_original_html_and_unknown_worker_state(monkeypatch):
    # GIVEN acquired original HTML and a genuinely unresponsive owned worker.
    body = b"<html><body><p>Initial article</p><script>document.querySelector('p').textContent='Unobserved change'</script></body></html>"
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(body, **options))
    processes = _replace_worker(monkeypatch, "import threading;threading.Event().wait(60)")
    assert processes == [], "fallback starts with no worker or observed JS state"
    # WHEN the parent alone cancels its still-running worker at the configured cutoff.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=0.5)
    acquisition = receipt["acquisition"]
    # THEN only original content is published with explicitly unknown completion.
    assert code == 0, "a parent-owned timing stop may retain validated original HTML"
    assert (_chapter_body(markdown), acquisition["capture_status"], acquisition["errors"],
            acquisition["snapshot"]["kind"], acquisition["pending"], acquisition["counts"],
            [process.returncode for process in processes]) == (
        "Initial article\n", "partial", [{"category": "initial_html_timeout_fallback", "resource_url": None}],
        "initial_html_timeout_fallback", {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None},
        {"requests": 1, "promise_jobs": None, "timer_callbacks": None}, [-signal.SIGKILL],
    ), "original HTML fallback must never claim a current DOM, zero pending work or settled JS"
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)


@pytest.mark.skipif(sys.platform != "linux", reason="Owned worker cleanup requires isolated Linux.")
@pytest.mark.parametrize("source,expected_returncode", [
    pytest.param("raise SystemExit(1)", lambda: 1, id="spontaneous-worker-exit"),
    pytest.param("""
import sys,threading
from multiprocessing.connection import Connection
from brewdoc.htmljs import _send
channel=Connection(int(sys.argv[1]))
_send(channel,{'kind':'error'})
threading.Event().wait(60)
""", lambda: -signal.SIGKILL, id="buffered-unclassified-hard-error"),
])
def test_worker_failure_or_buffered_hard_error_never_becomes_timeout_fallback(source, expected_returncode, monkeypatch, tmp_path):
    # GIVEN valid original HTML, prior output and a trusted failing worker fixture.
    body = b"<html><body><p>Initial article</p></body></html>"
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(body, **options))
    processes = _replace_worker(monkeypatch, source)
    assert (processes, output.read_bytes()) == ([], b"previous output\n"), "hard refusal starts without an owned child"
    # WHEN the parent observes spontaneous exit or an explicit hard-error IPC record.
    code, receipt, markdown = htmlurl._run_url(URL, output, render_js=True, timeout=0.5)
    # THEN the hard failure wins over retained original HTML and timing cancellation.
    assert (code, markdown, receipt["acquisition"]["capture_status"], output.read_bytes(),
            [process.returncode for process in processes]) == (
        1, "", "refused", b"previous output\n", [expected_returncode()],
    ), "a worker failure or buffered hard error must never be disguised as timing fallback"
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)


@pytest.mark.skipif(sys.platform != "linux", reason="Native safety regressions require isolated Linux.")
@pytest.mark.parametrize("violation", [
    pytest.param("Object.defineProperty(document.body,'toString',{value:()=> 'forged'});", id="serializer-tamper-before-loop"),
    pytest.param("try{new Uint8Array(80*1024*1024)}catch(error){};", id="caught-heap-denial-before-loop"),
    pytest.param("try{for(let n=0;n<201;n++)setTimeout(()=>{},10000)}catch(error){};", id="caught-timer-quota-before-loop"),
])
def test_hard_violation_keeps_priority_over_actual_script_timing_stop(violation, monkeypatch, tmp_path):
    # GIVEN acquired content and an actual safety violation before a timing loop.
    body = ('<html><body><p>Initial article</p><script>' + violation + 'while(true){}</script></body></html>').encode()
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(body, **options))
    assert output.read_bytes() == b"previous output\n", "hard safety failure must preserve prior output"
    # WHEN native timing and the sticky hard violation coexist.
    code, receipt, markdown = htmlurl._run_url(URL, output, render_js=True, timeout=1)
    # THEN timeout recovery cannot erase hard safety evidence or publish a fallback.
    assert (code, markdown, receipt["acquisition"]["capture_status"], output.read_bytes()) == (
        1, "", "refused", b"previous output\n",
    ), "heap, tamper and quota failure must dominate a trusted timing interruption"


@pytest.mark.skipif(sys.platform != "linux", reason="Native policy regressions require isolated Linux.")
def test_caught_forbidden_request_remains_hard_with_a_short_timeout(monkeypatch, tmp_path):
    # GIVEN a page hiding a forbidden request and a previous output transaction.
    body = b"<html><body><p>Initial article</p><script>fetch('http://127.0.0.1/').catch(()=>{});setTimeout(()=>{},10000)</script></body></html>"
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")

    def initial(**options):
        """Return acquired initial HTML before the real page request."""
        return _html_response(body, **options)

    def forbidden(**options):
        """Represent the authoritative parent's actual containment refusal."""
        raise htmlurl.URLPolicyError("private destination refused")

    def fetch(url, *, kind="html", **options):
        """Keep a policy error distinct from the page's later timing state."""
        return {"html": initial, "api": forbidden}[kind](**options)

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert output.read_bytes() == b"previous output\n", "policy refusal must preserve prior output"
    # WHEN the parent refuses the request even though page code catches rejection.
    code, receipt, markdown = htmlurl._run_url(URL, output, render_js=True, timeout=1)
    # THEN no timing recovery or page catch can soften containment policy.
    assert (code, markdown, receipt["acquisition"]["capture_status"], output.read_bytes()) == (
        1, "", "refused", b"previous output\n",
    ), "a forbidden destination must remain a hard authoritative refusal"
