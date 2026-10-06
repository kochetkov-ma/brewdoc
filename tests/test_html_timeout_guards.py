"""Trusted timeout guards preserve hard failures and truthful recovery causes."""

import os
import signal
import sys
import threading
import time
from itertools import chain, repeat

import pytest

from brewdoc import htmljs, htmlurl
from brewdoc.common import BrewdocError
from brewdoc._urljs.native import Context, NativeError, _UNDEFINED
from test_html_timeout import URL, _chapter_body, _html_response, _replace_worker


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Timeout guard proofs require isolated Linux.")
INITIAL = b"<html><body><p>Initial article</p></body></html>"
FALLBACK = {"category": "initial_html_timeout_fallback", "resource_url": None}
TIMING = {"category": "capture_timeout", "resource_url": None}
ANCILLARY = {"category": "ancillary_transport_failed", "resource_url": None}
WORKER_BOOT = """
import sys,threading,time
from multiprocessing.connection import Connection
from brewdoc.htmljs import _send,_receive
channel=Connection(int(sys.argv[1]))
bootstrap=_receive(channel)
"""


@pytest.mark.parametrize("operation", [
    pytest.param(lambda context: context.eval("effects++", "page"), id="tiny-eval"),
    pytest.param(lambda context: context.jobs(), id="queued-job"),
    pytest.param(lambda context: context.call("mutate"), id="saved-callback"),
])
def test_expired_loading_deadline_stops_native_entry_before_any_page_effect(operation):
    # GIVEN a real pending job and captured functions before the loading cutoff.
    with Context({}.__getitem__) as context:
        context.eval("globalThis.effects=0;globalThis.read=()=>String(effects);globalThis.mutate=()=>String(++effects);Promise.resolve().then(()=>effects++);", "trusted-bootstrap")
        context.save(["read", "mutate"])
        assert (context.call("read"), context.pending_promises) == ("0", 1), "the tiny mutation and job must start unexecuted"
        context.loading_deadline = time.monotonic() - 1
        # WHEN evaluation, pumping or a callback attempts to enter after cutoff.
        with pytest.raises(NativeError) as stopped:
            operation(context)
        # THEN the trusted pre-entry check prevents every page side effect.
        context.recovery_deadline = time.monotonic() + 1
        assert (stopped.value.timing, stopped.value.resource, context.call("read")) == (
            True, False, "0",
        ), "a cutoff must stop tiny operations before an interrupt callback is needed"


@pytest.mark.parametrize("operation", [
    pytest.param(lambda context: context.eval("effects++", "page"), id="eval-under-recovery"),
    pytest.param(lambda context: context.jobs(), id="jobs-under-recovery"),
])
def test_recovery_deadline_allows_saved_inspection_without_resuming_page_eval_or_jobs(operation):
    # GIVEN an expired loading deadline and a future trusted recovery interval.
    with Context({}.__getitem__) as context:
        context.eval("globalThis.effects=0;globalThis.read=()=>String(effects);Promise.resolve().then(()=>effects++);", "trusted-bootstrap")
        context.save(["read"])
        context.loading_deadline = time.monotonic() - 1
        context.recovery_deadline = time.monotonic() + 1
        assert context.call("read") == "0", "saved inspection must remain possible during recovery"
        # WHEN page evaluation or queued jobs try to consume the recovery interval.
        with pytest.raises(NativeError) as stopped:
            operation(context)
        # THEN only saved inspection can use recovery time, never page execution.
        assert (stopped.value.timing, stopped.value.resource, context.call("read")) == (
            True, False, "0",
        ), "recovery must not restart page evaluation or drain queued jobs"
        context.recovery_deadline = time.monotonic() - 1
        with pytest.raises(NativeError) as expired:
            context.call("read")
        assert (expired.value.timing, expired.value.resource) == (True, False), "saved inspection also stops when recovery expires"


def test_later_module_timing_failure_cannot_overwrite_the_first_hard_host_exception():
    # GIVEN a real first loader policy failure and a later trusted timing failure.
    hard = htmlurl.URLPolicyError("first authoritative policy refusal")
    timing = htmlurl.URLTimeoutError("later trusted loading timeout")
    failures = {"https://fixture.test/hard.js": hard, "https://fixture.test/late.js": timing}

    def fail(url):
        """Raise actual typed host failures through the existing loader callback."""
        raise failures[url]

    with Context(fail) as context:
        context._begin()
        assert context._load(context.context, b"https://fixture.test/hard.js", None, None) is None, "the first actual loader callback must fail"
        assert context.host_error is hard, "the authoritative hard object must already be latched"
        # WHEN another loader callback encounters a later timing-only failure.
        assert context._load(context.context, b"https://fixture.test/late.js", None, None) is None, "the later loader callback must also fail"
        with pytest.raises(htmlurl.URLPolicyError) as refused:
            context._check(_UNDEFINED)
        # THEN the original hard object survives without being reclassified as timing.
        assert refused.value is hard, "the first hard host exception must retain exact identity and priority"


def _cancelled_worker_race(monkeypatch, final_status, *, pending_polls=0):
    """Model an owned kill attempt racing a distinct actual final process status."""
    clock = [100.0]
    kills = []

    class Process:
        """Expose final status independently from the successful kill attempt."""
        pid = 987654
        returncode = None

        def poll(self):
            """Keep final exit status independent of the cancellation attempt."""
            return self.returncode

        def wait(self, timeout):
            """Reap the modeled final exit without changing its identity."""
            self.returncode = final_status
            return final_status

    process = Process()
    statuses = chain(repeat(None, pending_polls), repeat(final_status))

    class Channel:
        """Yield the final-status race without native execution or real IPC."""
        def __init__(self, descriptor):
            self.descriptor = descriptor

        def send_bytes(self, payload):
            """Accept bootstrap bytes without producing a worker response."""
            pass

        def poll(self, timeout):
            """Expose the actual final status after the successful kill attempt."""
            process.returncode = next(statuses)
            return False

        def close(self):
            """Close the real transferred parent descriptor exactly once."""
            os.close(self.descriptor)

    class Watchdog:
        """Advance directly to the bounded recovery cutoff before cancellation."""
        def __init__(self, interval, callback):
            self.interval, self.callback = interval, callback

        def start(self):
            """Advance to the real cutoff before invoking cancellation."""
            clock[0] += self.interval
            self.callback()

        def cancel(self):
            """Leave the completed synchronous fixture callback unchanged."""
            pass

        def join(self):
            """Return immediately because the fixture creates no timer thread."""
            pass

    monkeypatch.setattr(htmljs, "_check_runtime", lambda **options: None)
    monkeypatch.setattr(htmljs.subprocess, "Popen", lambda *args, **options: process)
    monkeypatch.setattr(htmljs, "Connection", Channel)
    monkeypatch.setattr(htmljs.threading, "Timer", Watchdog)
    monkeypatch.setattr(htmljs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(htmljs.os, "killpg", lambda pid, number: kills.append((pid, number)))
    return process, kills


def test_watchdog_and_main_loop_signal_the_owned_group_once_before_reaping(monkeypatch):
    # GIVEN a successful watchdog signal whose process status is not yet observable.
    process, kills = _cancelled_worker_race(monkeypatch, -signal.SIGKILL, pending_polls=1)

    def duplicate_signal(pid, number):
        """Refuse a stale second group signal instead of treating it as cancellation."""
        kills.append((pid, number))
        raise PermissionError(1, "duplicate group signal refused")

    signals = iter([lambda pid, number: kills.append((pid, number)), duplicate_signal])
    monkeypatch.setattr(htmljs.os, "killpg", lambda pid, number: next(signals)(pid, number))
    assert (process.returncode, kills) == (None, []), "the owned worker starts alive and unsignalled"
    # WHEN watchdog cancellation precedes the main loop observing its final status.
    captured = htmljs.render(INITIAL.decode(), URL, lambda request: None, 100.5, soft_window=0.5)
    # THEN the parent reaps actual SIGKILL and publishes exact original-source fallback.
    assert (captured, process.returncode, kills) == (
        {"html": INITIAL.decode(), "capture_status": "partial", "errors": [FALLBACK],
         "error_count": 1, "snapshot_kind": "initial_html_timeout_fallback",
         "pending": {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None},
         "counts": {"promise_jobs": None, "timer_callbacks": None},
         "engine": "QuickJS-ng 0.17.0", "dom_bundle": "LinkeDOM 0.18.13", "soft_window_seconds": 0.5,
         "unsupported": ["whole_document_parsed_before_scripts", "no_layout_or_intersection_observer",
                         "no_cookies_or_credentials", "no_browser_csp_enforcement",
                         "no_shadow_root_or_subframe_export", "no_canvas_media_or_service_workers",
                         "coarse_async_defer_and_module_event_order", "document_write_buffered",
                         "protected_host_intrinsic_prototypes", "imported_module_completion_unobservable"]},
        -signal.SIGKILL, [(process.pid, signal.SIGKILL)],
    ), "a successful owned signal must not be repeated while waiting for reap"


def test_first_group_permission_refusal_cannot_claim_owned_timeout_fallback(monkeypatch):
    # GIVEN a live worker whose first cancellation syscall is refused.
    process, kills = _cancelled_worker_race(monkeypatch, 0, pending_polls=1)
    monkeypatch.setattr(htmljs.threading.Timer, "start", lambda timer: None)
    refusal = PermissionError(1, "first group signal refused")

    def refuse(pid, number):
        """Retain the real signal refusal without creating cancellation provenance."""
        kills.append((pid, number))
        raise refusal

    monkeypatch.setattr(htmljs.os, "killpg", refuse)
    assert (process.returncode, kills) == (None, []), "no earlier successful signal may justify fallback"
    # WHEN the main loop reaches an already expired recovery deadline.
    with pytest.raises(PermissionError) as rejected:
        htmljs.render(INITIAL.decode(), URL, lambda request: None, 98.5, soft_window=0.5)
    # THEN the exact syscall error survives and the final zero exit does not prove cancellation.
    assert (rejected.value, process.returncode, kills) == (
        refusal, 0, [(process.pid, signal.SIGKILL)],
    ), "PermissionError must stay observable instead of being masked as eligible fallback"


def test_concurrent_cancellers_signal_one_owned_group_before_status_becomes_visible(monkeypatch):
    # GIVEN two cancellation callers entering before the first signal returns.
    process, kills = _cancelled_worker_race(monkeypatch, -signal.SIGKILL)
    signal_entered = threading.Event()
    contender_entered = threading.Event()
    release_signal = threading.Event()
    failures = []
    threads = []

    def first_signal(pid, number):
        """Hold successful cancellation until another caller enters the real callback."""
        kills.append((pid, number))
        signal_entered.set()
        assert release_signal.wait(2), "the competing cancellation must release the first syscall"

    def duplicate_signal(pid, number):
        """Expose a second syscall on the same unreaped process group."""
        kills.append((pid, number))
        raise PermissionError(1, "concurrent group signal refused")

    signals = iter([first_signal, duplicate_signal])
    monkeypatch.setattr(htmljs.os, "killpg", lambda pid, number: next(signals)(pid, number))

    class Watchdog:
        """Enter two real cancellation calls without native code or real signals."""

        def __init__(self, interval, callback):
            self.callback = callback

        def invoke(self):
            """Capture expected signal errors so thread failure cannot escape pytest."""
            try:
                self.callback()
            except PermissionError as exc:
                failures.append((type(exc).__name__, exc.errno))

        def contender(self):
            """Record actual callback entry while the first syscall is still blocked."""
            previous_trace = sys.gettrace()
            sys.settrace(lambda frame, event, argument: contender_entered.set())
            try:
                self.callback()
            except PermissionError as exc:
                failures.append((type(exc).__name__, exc.errno))
            finally:
                sys.settrace(previous_trace)

        def start(self):
            """Release the owned syscall only after both callers entered cancellation."""
            try:
                threads.append(threading.Thread(target=self.invoke))
                threads[-1].start()
                assert signal_entered.wait(2), "the first caller must be inside the signal syscall"
                threads.append(threading.Thread(target=self.contender))
                threads[-1].start()
                assert contender_entered.wait(2), "the second caller must enter before the first syscall returns"
            finally:
                release_signal.set()
                for thread in threads:
                    thread.join(2)

        def cancel(self):
            """Leave the completed bounded caller pair unchanged."""
            pass

        def join(self):
            """Both fixture threads have already been joined by start."""
            pass

    monkeypatch.setattr(htmljs.threading, "Timer", Watchdog)
    assert (process.returncode, kills, failures, threads) == (None, [], [], []), "both callers start with the same live owned worker"
    # WHEN both cancellation calls compete while final process status is unknown.
    captured = htmljs.render(INITIAL.decode(), URL, lambda request: None, 100.5, soft_window=0.5)
    # THEN only one syscall establishes owned SIGKILL and both callers complete cleanly.
    assert (captured["html"], captured["snapshot_kind"], process.returncode, kills, failures,
            [thread.is_alive() for thread in threads]) == (
        INITIAL.decode(), "initial_html_timeout_fallback", -signal.SIGKILL,
        [(process.pid, signal.SIGKILL)], [], [False, False],
    ), "concurrent cancellation must preserve process identity and suppress a second signal syscall"


@pytest.mark.parametrize("status", [1, -11, 11, 0], ids=["ordinary-exit", "fault-signal", "guarded-fault-exit", "unproved-zero-exit"])
def test_attempted_parent_cancellation_cannot_mask_an_actual_non_sigkill_exit(status, monkeypatch, tmp_path):
    # GIVEN original HTML and a pure race between kill attempt and actual exit.
    process, kills = _cancelled_worker_race(monkeypatch, status)
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(INITIAL, **options))
    assert (process.returncode, kills) == (None, []), "no cancellation or exit may precede the modeled race"
    # WHEN cancellation succeeds as an attempt but the real final status differs.
    code, receipt, markdown = htmlurl._run_url(URL, output, render_js=True, timeout=0.5)
    # THEN an actual failure remains hard even if the cancelled flag was set.
    assert (code, markdown, receipt["acquisition"]["capture_status"], output.read_bytes(), process.returncode, kills) == (
        1, "", "refused", b"previous output\n", status, [(process.pid, signal.SIGKILL)],
    ), "a successful kill attempt must not overwrite the actual fault or exit status"


def test_actual_owned_sigkill_is_the_eligible_cancellation_race_control(monkeypatch):
    # GIVEN the same pure race with the actual final owned SIGKILL status.
    process, kills = _cancelled_worker_race(monkeypatch, -signal.SIGKILL)
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(INITIAL, **options))
    assert (process.returncode, kills) == (None, []), "the cancellation control starts without a status"
    # WHEN the parent alone cancels a still-running worker successfully.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=0.5)
    # THEN truthful original-source fallback remains permitted for owned cancellation.
    assert code == 0, "an actual owned SIGKILL may retain valid original HTML"
    assert (_chapter_body(markdown), receipt["acquisition"]["snapshot"]["kind"], process.returncode, kills) == (
        "Initial article\n", "initial_html_timeout_fallback", -signal.SIGKILL, [(process.pid, signal.SIGKILL)],
    ), "the eligible control must prove actual final SIGKILL rather than just a kill attempt"


@pytest.mark.parametrize("count,expected_errors", [
    pytest.param(1, [ANCILLARY, FALLBACK], id="one-known-parent-diagnostic"),
    pytest.param(21, [ANCILLARY] * 15 + [FALLBACK], id="cause-survives-public-error-bound"),
])
def test_original_source_fallback_retains_known_parent_diagnostics_and_its_cause(count, expected_errors, monkeypatch):
    # GIVEN known ordinary parent failures before an unresponsive trusted worker.
    def initial(**options):
        """Acquire complete original HTML before ordinary resource failures."""
        return _html_response(INITIAL, **options)

    def ancillary(**options):
        """Count an attempted resource with an ordinary transport diagnostic."""
        options["budget"]["accepted_requests"] += 1
        raise BrewdocError("ordinary ancillary transport failure")

    def fetch(url, *, kind="html", **options):
        """Keep source acquisition distinct from known ancillary diagnostics."""
        return {"html": initial, "api": ancillary}[kind](**options)

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    source = WORKER_BOOT + """
for index in range(COUNT):
    _send(channel,{'kind':'request','request':{'kind':'api','url':'https://fixture.test/data-'+str(index),
      'method':'GET','headers':{},'mode':'cors','credentials':'same-origin','redirect':'follow','body_present':False}})
    response=_receive(channel)
    assert response=={'error':'ancillary_transport_failed'},'the parent must return the controlled ancillary transport failure'
threading.Event().wait(60)
""".replace("COUNT", str(count))
    processes = _replace_worker(monkeypatch, source)
    assert processes == [], "the ordinary diagnostics must be observed by the real parent after startup"
    # WHEN parent-owned cancellation follows the recorded ordinary failures.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=1)
    acquisition = receipt["acquisition"]
    # THEN known diagnostics and the fallback cause survive bounded public reporting.
    assert code == 0, "ordinary transport diagnostics must not erase acquired original content"
    assert (_chapter_body(markdown), acquisition["errors"], acquisition["error_count"], acquisition["pending"],
            acquisition["counts"], acquisition["snapshot"]["kind"], [p.returncode for p in processes]) == (
        "Initial article\n", expected_errors, count + 1,
        {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None},
        {"requests": count + 1, "promise_jobs": None, "timer_callbacks": None},
        "initial_html_timeout_fallback", [-signal.SIGKILL],
    ), "fallback must preserve ordinary order, total count and exactly one visible cause"


@pytest.mark.parametrize("script,expected", [
    pytest.param("document.querySelector('p').textContent='Committed article';throw{toString(){while(true){}}}",
                 "Committed article\n", id="exception-stringification-interrupt"),
    pytest.param("""
const original=globalThis;const proxy=new Proxy(original,{getOwnPropertyDescriptor(target,key){
  document.querySelector('p').textContent='Proxy callback';return Reflect.getOwnPropertyDescriptor(target,key);
}});globalThis=proxy;document.querySelector('p').textContent='Committed article';while(true){}
""", "Committed article\n", id="recovery-must-ignore-page-global-proxy"),
])
def test_recovery_preserves_committed_dom_without_page_exception_or_global_proxy_callbacks(script, expected, monkeypatch):
    # GIVEN completed DOM and hostile page coercion or a substituted global object.
    body = ('<html><body><p>Initial article</p><script>' + script + '</script></body></html>').encode()
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(body, **options))
    # WHEN the native interrupt or trusted recovery inspects capture state.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=2)
    # THEN no post-cutoff page callback can change the committed published content.
    assert code == 0, "a genuine timing stop must retain valid committed content"
    assert (_chapter_body(markdown), receipt["acquisition"]["errors"], receipt["acquisition"]["capture_status"]) == (
        expected, [TIMING], "partial",
    ), "trusted recovery must use original capabilities instead of page-controlled coercion or globals"


@pytest.mark.parametrize("script", [
    pytest.param("""
Object.defineProperty(document.body,'toString',{value:()=> 'forged'});
globalThis.Error=function(){while(true){}};while(true){}
""", id="tamper-error-constructor-loops"),
    pytest.param("""
const original=document.body.toString;const parent=Object.create(Object.getPrototypeOf(document.body));
Object.defineProperty(parent,'toString',{get(){document.querySelector('p').textContent='Getter callback';return original;}});
Object.setPrototypeOf(document.body,parent);while(true){}
""", id="inherited-serializer-getter"),
    pytest.param("throw new InternalError('page-created intrinsic error');", id="noninterrupted-intrinsic-internal-error"),
])
def test_recovery_cannot_soften_serializer_tampering_or_intrinsic_internal_error(script, monkeypatch, tmp_path):
    # GIVEN acquired content and actual tampering or a noninterrupted intrinsic error.
    body = ('<html><body><p>Initial article</p><script>' + script + '</script></body></html>').encode()
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(body, **options))
    assert output.read_bytes() == b"previous output\n", "hard recovery checks start with existing output"
    # WHEN trusted recovery encounters the forged capability or native error class.
    code, receipt, markdown = htmlurl._run_url(URL, output, render_js=True, timeout=2)
    # THEN neither a looping page constructor nor inherited getter permits fallback.
    assert (code, markdown, receipt["acquisition"]["capture_status"], output.read_bytes()) == (
        1, "", "refused", b"previous output\n",
    ), "tamper and intrinsic InternalError must retain hard priority over recovery timing"


def test_second_runtime_preflight_timeout_preserves_already_acquired_html_without_starting_a_child(monkeypatch):
    # GIVEN successful first prerequisites and complete HTML before the next cutoff.
    clock = [100.0]
    checks, processes = [], []
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: clock[0])

    def check(*, deadline):
        """Apply the actual shared cutoff without starting native prerequisites."""
        checks.append((deadline, clock[0]))
        htmlurl._remaining(deadline)

    def fetch(url, **options):
        """Complete source acquisition before the second prerequisite cutoff."""
        response = _html_response(INITIAL, **options)
        clock[0] = 111.0
        return response

    monkeypatch.setattr(htmljs, "_check_runtime", check)
    monkeypatch.setattr(htmljs.subprocess, "Popen", lambda *args, **options: processes.append(args))
    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert (checks, processes) == ([], []), "the complete source starts before runtime or child I/O"
    # WHEN the trusted loading cutoff expires between acquisition and second preflight.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=10)
    acquisition = receipt["acquisition"]
    # THEN acquired original HTML survives without starting an expired worker.
    assert code == 0, "a second timing-only preflight must retain complete acquired HTML"
    assert (_chapter_body(markdown), checks, processes, acquisition["errors"], acquisition["snapshot"]["kind"], acquisition["pending"]) == (
        "Initial article\n", [(110.0, 100.0), (110.0, 111.0)], [], [FALLBACK], "initial_html_timeout_fallback",
        {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None},
    ), "the second preflight must share the original deadline and preserve truthful unknown JS state"


@pytest.mark.parametrize("offered", ["True", "0", "-1", "float('nan')", "float('inf')", "10**400", "bootstrap['deadline']+2"],
                         ids=["bool", "zero", "negative", "nan", "infinity", "overflowing-integer", "beyond-recovery-limit"])
def test_malformed_private_timing_notification_refuses_safely_and_reaps_worker(offered, monkeypatch, tmp_path):
    # GIVEN original content and a malformed trusted-fixture timing notification.
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(INITIAL, **options))
    source = WORKER_BOOT + "_send(channel,{'kind':'timing','recovery_deadline':" + offered + "});threading.Event().wait(60)"
    processes = _replace_worker(monkeypatch, source)
    assert processes == [], "the malformed IPC fixture has not started"
    # WHEN the parent validates type, representability and bounded ordering.
    code, receipt, markdown = htmlurl._run_url(URL, output, render_js=True, timeout=2)
    # THEN invalid private metadata stays a sanitized hard refusal, not a raw exception.
    assert (code, markdown, receipt["acquisition"]["capture_status"], output.read_bytes(), [p.returncode for p in processes]) == (
        1, "", "refused", b"previous output\n", [-signal.SIGKILL],
    ), "malformed timing metadata must refuse transactionally and reap its owned child"


def test_first_timing_notification_bounds_recovery_and_duplicates_cannot_extend_it(monkeypatch):
    # GIVEN an early trusted stop followed by a later offered recovery deadline.
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(INITIAL, **options))
    source = WORKER_BOOT + """
_send(channel,{'kind':'timing','recovery_deadline':time.monotonic()+1})
_send(channel,{'kind':'timing','recovery_deadline':time.monotonic()+5})
threading.Event().wait(60)
"""
    processes = _replace_worker(monkeypatch, source)
    assert processes == [], "the bounded recovery control starts without a worker"
    # WHEN a twenty-second loading budget enters recovery much earlier.
    started = time.monotonic()
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=20)
    elapsed = time.monotonic() - started
    # THEN only the first one-second interval controls the owned worker's lifetime.
    assert code == 0, "a valid first timing notification may preserve original content"
    assert (_chapter_body(markdown), receipt["acquisition"]["errors"], [p.returncode for p in processes]) == (
        "Initial article\n", [FALLBACK], [-signal.SIGKILL],
    ), "the early recovery hang must be reaped without accepting a later extension"
    assert elapsed < 2.5, "recovery must use its first bounded interval rather than five or twenty seconds"


def test_current_dom_timeout_cause_survives_child_and_public_error_bounds(monkeypatch):
    # GIVEN twenty-one ordinary failures followed by an actual native timing stop.
    script = "<script>throw new Error('ordinary failure')</script>" * 21
    body = ("<html><body><p>Committed article</p>" + script + "<script>while(true){}</script></body></html>").encode()
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, **options: _html_response(body, **options))
    assert body.count(b"ordinary failure") == 21, "the cause must be beyond both existing error list bounds"
    # WHEN cooperative current-DOM recovery adds its timing cause after ordinary errors.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=2)
    acquisition = receipt["acquisition"]
    # THEN retained ordinary order and total count coexist with one visible timeout cause.
    assert code == 0, "ordinary script errors and timing must preserve available content"
    assert (_chapter_body(markdown), acquisition["errors"], acquisition["error_count"], acquisition["capture_status"]) == (
        "Committed article\n", [{"category": "script_execution_failed", "resource_url": None}] * 15 + [TIMING], 22, "partial",
    ), "the causal marker must survive both bounded error lists without clearing ordinary diagnostics"
