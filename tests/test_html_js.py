"""Check finite JavaScript capture and hard resource refusal."""

import time
import importlib.metadata
import platform
import sys
import os
import json
import select
import signal
import subprocess
from types import SimpleNamespace

import pytest

from brewdoc.htmljs import OMISSIONS, render
from brewdoc.common import BrewdocError

try:
    importlib.metadata.version('quickjs-ng')
    AVAILABLE = (sys.platform == 'darwin' and platform.machine() == 'arm64'
                 or sys.platform == 'linux' and platform.libc_ver()[0] == 'glibc')
except importlib.metadata.PackageNotFoundError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason='Optional public native API is unavailable.')
IDLE = {'requests': 0, 'timers': 0, 'modules': 0, 'promises': 0, 'jobs': False}


def test_inline_mutation_reaches_frozen_snapshot():
    # GIVEN a page whose primary content is created by JavaScript.
    source = '<html><body><p id="result">pending</p><script>document.querySelector("#result").textContent="article"</script></body></html>'
    # WHEN the installed worker executes the finite script.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN the frozen DOM contains the created text with truthful status.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source.replace('>pending<', '>article<'), 'settled', [], IDLE,
    ), 'The inline script must update the exported DOM with no pending work left.'


def test_later_timer_returns_partial_snapshot():
    # GIVEN useful initial content and a timer outside the soft window.
    source = '<html><body><p>article</p><script>setTimeout(()=>document.body.append("late"),10000)</script></body></html>'
    # WHEN capture reaches its soft window.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.05)
    # THEN useful DOM survives and later work is explicitly pending.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source, 'partial', [], {**IDLE, 'timers': 1},
    ), 'Useful DOM must survive while exactly one later timer prevents a complete label.'


def test_runaway_script_timing_preserves_valid_available_dom():
    # GIVEN useful acquired content followed by an unbounded CPU loop.
    source = '<html><body><p>article</p><script>while(true){}</script></body></html>'
    # WHEN capture executes the script under its native interrupt.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN a trusted timing stop preserves current content as explicitly partial.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source, 'partial', [{'category': 'capture_timeout', 'resource_url': None}], IDLE,
    ), 'Timing alone must preserve available DOM without claiming completed JS.'


@pytest.mark.parametrize('script', [
    pytest.param('try{function f(){f()}f()}catch(e){}', marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
    pytest.param('try{const code="function f(){let "+Array.from({length:2000},(_,i)=>"v"+i+"=0").join(",")+";f()}f()";eval(code)}catch(e){}', marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
    pytest.param('try{eval("(".repeat(10000)+"0"+")".repeat(10000))}catch(e){}', marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
    'try{for(let i=0;i<201;i++)setTimeout(()=>{},10000)}catch(e){}',
    'try{document.write("x".repeat(8*1024*1024+1))}catch(e){}',
    'function again(){Promise.resolve().then(again)}again()',
    'document.body.textContent="x".repeat(8*1024*1024+1)',
])
def test_caught_or_runaway_resource_exhaustion_still_refuses(script):
    # GIVEN script work that exhausts one hard structural or execution bound.
    source = '<html><body><p>article</p><script>' + script + '</script></body></html>'
    # WHEN the page tries to catch errors or retain useful initial text.
    with pytest.raises(BrewdocError, match='resource|crash'):
        render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=12)
    # THEN exhaustion still prevents publication of a partial snapshot.


@pytest.mark.parametrize(('script', 'cause'), [
    ('Object.defineProperty(document.body,"toString",{value:()=>"forged"})', 'initial_html_dom_fallback'),
    ('Object.defineProperty(document.querySelector("p").firstChild,"toString",{value:()=>"forged"})', 'initial_html_dom_fallback'),
    ('Object.defineProperty(document,"childNodes",{value:["forged"]})', 'initial_html_dom_fallback'),
    ('Object.defineProperty(document.querySelector("p"),Symbol.toPrimitive,{value:()=>"forged"})', 'initial_html_dom_fallback'),
    ('customElements.define("x-article",class extends HTMLElement{toString(){return "forged"}});document.body.append(document.createElement("x-article"))',
     'initial_html_dom_fallback'),
    ('try{new Uint8Array(80*1024*1024)}catch(e){}', 'initial_html_heap_fallback'),
    pytest.param('try{function f(){f.apply(null,Array(10000).fill(1))}f()}catch(e){}', 'initial_html_heap_fallback',
                 marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
])
def test_serializer_tampering_or_caught_heap_denial_publishes_labelled_initial_html(script, cause):
    # GIVEN a page that tampers with serializer capabilities or catches its own heap denial.
    source = '<html><body><p>article</p><script>' + script + '</script></body></html>'
    # WHEN the snapshot guard or sticky heap limit stops the current DOM.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN only the acquired page is published, labelled with its cause and unknown JS state.
    assert captured == {
        'html': source, 'snapshot_kind': cause, 'capture_status': 'partial',
        'errors': [{'category': cause, 'resource_url': None}], 'error_count': 1,
        'pending': dict.fromkeys(('requests', 'timers', 'modules', 'promises', 'jobs')),
        'counts': {'promise_jobs': None, 'timer_callbacks': None}, 'engine': 'QuickJS-ng 0.17.0',
        'dom_bundle': 'LinkeDOM 0.18.13', 'soft_window_seconds': 0.1, 'unsupported': list(OMISSIONS),
    }, 'Forged or heap-denied capture must publish the initial page, never the forged or partial JS DOM.'


@pytest.mark.parametrize('script', [
    'document.addEventListener("DOMContentLoaded",()=>{throw new Error("private")})',
    'window.addEventListener("load",()=>{throw new Error("private")})',
])
def test_ordinary_listener_error_retains_useful_partial_dom(script):
    # GIVEN useful content and an ordinary failing listener.
    source = '<html><body><p>article</p><script>' + script + '</script></body></html>'
    # WHEN the finite event pump observes the listener failure.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN script failure is sanitized while actual content remains convertible.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source, 'partial', [{'category': 'javascript_callback_failed', 'resource_url': None}], IDLE,
    ), 'Ordinary listener failure must retain useful DOM with sanitized diagnostics.'


def test_dynamic_module_unresolved_await_is_observable_partial():
    # GIVEN an imported graph that never resolves top-level await.
    source = '<html><body><p>article</p><script>import("./module.js")</script></body></html>'
    def fetch(request):
        return {'status': 200, 'headers': (), 'final_url': request['url'],
                'body': b'await new Promise(()=>{});export const value=42'}
    # WHEN the public native Promise hook observes the unresolved graph.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=0.05)
    # THEN pending async state prevents a settled claim.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source, 'partial', [], {'requests': 0, 'timers': 0, 'modules': None, 'promises': 8, 'jobs': False},
    ), 'Unknown imported completion must stay partial with its unresolved promises counted.'


@pytest.mark.parametrize(('mode', 'headers'), [
    ('cors', (('Access-Control-Allow-Origin', 'https://wrong.test'), ('Access-Control-Allow-Origin', '*'))),
    ('same-origin', (('Access-Control-Allow-Origin', '*'),)),
])
def test_cross_redirect_or_ambiguous_cors_never_exposes_body(mode, headers):
    # GIVEN a same-origin request whose final destination is cross-origin.
    source = '<html><body><p id="result">article</p><script>fetch("/jump",{mode:"' + mode + '"}).then(r=>r.text()).then(t=>document.querySelector("#result").textContent=t).catch(()=>{})</script></body></html>'
    def fetch(request):
        return {'status': 200, 'headers': headers, 'final_url': 'https://other.test/data', 'body': b'secret'}
    # WHEN the parent applies final-origin CORS rules to ordered headers.
    # Soft window spans the deadline: only engine settle ends capture, never child start-up time.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=10)
    # THEN the response body remains hidden with ordinary partial provenance.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source, 'partial', [{'category': 'cors_denied', 'resource_url': 'https://fixture.test/jump'}], IDLE,
    ), 'CORS ambiguity must deny exposure, name the request and never replace content.'


@pytest.mark.parametrize(('script', 'resources', 'expected'), [
    ('fetch("/data").then(r=>r.json()).then(d=>document.querySelector("#result").textContent=d.value)',
     {'https://fixture.test/data': {'body': b'{"value":"fetch content"}'}}, 'fetch content'),
    ('const x=new XMLHttpRequest();x.onload=()=>document.querySelector("#result").textContent=x.responseText;x.open("GET","/data");x.send()',
     {'https://fixture.test/data': {'body': b'xhr content'}}, 'xhr content'),
    ('window.addEventListener("load",()=>setTimeout(()=>document.querySelector("#result").textContent="loaded",1))', {}, 'loaded'),
    ('JSON.stringify=()=>"[]";Map.prototype.get=()=>null;fetch("/data").then(r=>r.text()).then(t=>document.querySelector("#result").textContent=t)',
     {'https://fixture.test/data': {'body': b'protected'}}, 'protected'),
    ('document.currentScript.parentElement.querySelector("#result").textContent=document.currentScript.localName',
     {}, 'script'),
    ('self.value="self alias";document.querySelector("#result").textContent=self===window?window.value:"other"',
     {}, 'self alias'),
    ('for(const c of [console,window.console])c.log(1),c.info(1),c.warn(1),c.error(1),c.debug(1);document.querySelector("#result").textContent="quiet"',
     {}, 'quiet'),
    ('document.cookie="a=b";document.querySelector("#result").textContent="["+document.cookie+"]"', {}, '[]'),
    ('requestAnimationFrame(t=>document.querySelector("#result").textContent="frame "+t);'
     'cancelAnimationFrame(requestAnimationFrame(()=>document.body.append("cancelled")))', {}, 'frame 0'),
    ('const u=new window.URL("../b/c?x=1&y=a+b#h","https://fixture.test/a/d/e");u.searchParams.set("x","2 3");'
     'u.searchParams.append("z","!");u.hash="";let bare;try{new URL("/x")}catch(e){bare=e instanceof TypeError}'
     'document.querySelector("#result").textContent=[u.href,u.origin,u.host,u.port,u.pathname,u.search,'
     'u.searchParams.get("y"),u.searchParams.has("q"),new URL("/p","https://h.test:8443/x").host,'
     'new URLSearchParams({k:"v w"}),bare].join("|")',
     {}, 'https://fixture.test/a/b/c?x=2+3&amp;y=a+b&amp;z=%21|https://fixture.test|fixture.test||/a/b/c|'
     '?x=2+3&amp;y=a+b&amp;z=%21|a b|false|h.test:8443|k=v+w|true'),
    ('document.querySelector("#result").textContent=new URL("p",location)+" "+location', {},
     'https://fixture.test/p https://fixture.test/'),
])
def test_host_apis_preserve_created_content_and_settle(script, resources, expected):
    # GIVEN bounded sources with an independent expected content value.
    source = '<html><body><p id="result">pending</p><script>' + script + '</script></body></html>'
    def fetch(request):
        return {'status': 200, 'headers': (), 'final_url': request['url'], **resources[request['url']]}
    # WHEN the worker requests only those parent-owned resources.
    # Soft window spans the deadline: only engine settle ends capture, never child start-up time.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=10)
    # THEN the created content reaches the frozen DOM and queues settle.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source.replace('>pending<', '>' + expected + '<'), 'settled', [], IDLE,
    ), 'The proved host API must preserve its useful content and settle.'


def test_dynamic_script_element_runs_and_stays_in_the_snapshot():
    # GIVEN a page that appends one parent-served script element.
    source = '<html><body><p id="result">pending</p><script>const s=document.createElement("script");s.src="/chunk.js";document.body.appendChild(s)</script></body></html>'
    def fetch(request):
        return {'status': 200, 'headers': (), 'final_url': request['url'], 'body': b'document.querySelector("#result").textContent="chunk"'}
    # WHEN the worker requests and runs the appended script.
    # Soft window spans the deadline: only engine settle ends capture, never child start-up time.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=10)
    # THEN the created content and the appended element reach the settled DOM.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        '<html><head></head><body><p id="result">chunk</p><script>const s=document.createElement("script");s.src="/chunk.js";'
        'document.body.appendChild(s)</script><script src="/chunk.js"></script></body></html>', 'settled', [], IDLE,
    ), 'A dynamic script element must run once and remain in the frozen DOM.'


def test_resolved_dynamic_import_keeps_content_with_unknown_module_completion():
    # GIVEN a page whose content comes from one parent-served module.
    source = '<html><body><p id="result">pending</p><script>import("./module.js").then(m=>document.querySelector("#result").textContent=m.value)</script></body></html>'
    def fetch(request):
        return {'status': 200, 'headers': (), 'final_url': request['url'], 'body': b'export const value="imported"'}
    # WHEN the worker resolves the import graph.
    # Soft window spans the deadline: only engine settle ends capture, never child start-up time.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=10)
    # THEN content arrives while import-graph completion stays unknown.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source.replace('>pending<', '>imported<'), 'partial', [], {**IDLE, 'modules': None},
    ), 'Import-graph completion must remain unknown after the module supplies content.'


def test_unhandled_rejection_keeps_useful_content_partial():
    # GIVEN useful content and an unhandled analytics rejection.
    source = '<html><body><p>article</p><script>Promise.reject(new Error("secret"))</script></body></html>'
    # WHEN the native rejection tracker observes the asynchronous failure.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN partial provenance reports only a stable category.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source, 'partial', [{'category': 'unhandled_promise_rejection', 'resource_url': None}], IDLE,
    ), 'An unhandled rejection must be visible without raw page exception text.'


def test_private_destination_refuses_even_when_page_catches_rejection():
    from brewdoc.htmlurl import URLPolicyError
    # GIVEN a page that attempts to hide a containment violation.
    source = '<html><body><p>article</p><script>fetch("http://127.0.0.1/").catch(()=>{})</script></body></html>'
    def refuse(request):
        raise URLPolicyError('private destination refused')
    # WHEN the authoritative parent transport refuses the target.
    # Soft window spans the deadline: only engine settle ends capture, never child start-up time.
    with pytest.raises(URLPolicyError, match='private destination'):
        render(source, 'https://fixture.test/', refuse, time.monotonic() + 10, soft_window=10)
    # THEN no caught JS rejection can downgrade the host refusal to partial.


def test_success_and_soft_capture_reap_owned_worker(monkeypatch):
    import brewdoc.htmljs as module
    # GIVEN cached prerequisites, so the capture worker is the only tracked child.
    module._check_runtime()
    processes = []
    class Tracked(module.subprocess.Popen):
        def __init__(self, *args, **options):
            super().__init__(*args, **options)
            processes.append(self)
        def wait(self, timeout=None):
            # Await worker exit before the owner's 0.1 s reap grace; load cannot turn it into SIGKILL.
            super().wait(10)
            return super().wait(timeout)
    monkeypatch.setattr(module.subprocess, 'Popen', Tracked)
    # WHEN a later timer remains after the short soft window.
    captured = render('<html><body><p>article</p><script>setTimeout(()=>{},10000)</script></body></html>',
                      'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.05)
    # THEN the child exits normally and is already reaped by its owner.
    assert (captured['capture_status'], [process.returncode for process in processes]) == ('partial', [0]), \
        'The later timer must remain partial and the one worker must exit cleanly.'
    with pytest.raises(ProcessLookupError):
        os.kill(processes[-1].pid, 0)


def test_loading_deadline_fallback_kills_and_reaps_unresponsive_worker(monkeypatch):
    import brewdoc.htmljs as module
    # GIVEN acquired HTML and a tracked worker that cannot cooperatively return.
    processes = []
    original = module.subprocess.Popen
    def start(*args, **options):
        process = original([sys.executable, '-I', '-c', 'import threading;threading.Event().wait(60)'], **options)
        processes.append(process)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', start)
    monkeypatch.setattr(module, '_check_runtime', lambda **options: None)
    # WHEN the authoritative watchdog reaches its deadline.
    source = '<html><body><p>article</p><script>setTimeout(()=>{},10000)</script></body></html>'
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 0.15, soft_window=12)
    # THEN the one owned child is killed and reaped before returning.
    assert (captured['html'], captured['capture_status'], captured['errors'],
            tuple(process.returncode for process in processes)) == (
        source, 'partial', [{'category': 'initial_html_timeout_fallback', 'resource_url': None}], (-signal.SIGKILL,),
    ), 'Only parent-owned timing cancellation may publish original HTML after killing its child.'
    with pytest.raises(ProcessLookupError):
        os.kill(processes[-1].pid, 0)


@pytest.fixture
def exiting_worker(monkeypatch):
    """Hold the real worker alive past its reap grace; killpg then meets it as an unreaped zombie."""
    import brewdoc.htmljs as module
    module._check_runtime()
    held = SimpleNamespace(processes=[], kills=[])
    class Held(module.subprocess.Popen):
        def __init__(self, arguments, **options):
            hold = 'import sys;from brewdoc.htmljs import _worker_fd;_worker_fd(int(sys.argv[1]),int(sys.argv[2]));sys.stdin.read()'
            super().__init__([sys.executable, '-I', '-c', hold, *arguments[-2:]],
                             **{**options, 'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE})
            held.processes.append(self)
    def killpg(pid, sig):
        # Release the worker and await its exit (stdout EOF) unreaped; macOS answers EPERM for that group.
        held.kills.append((pid, sig))
        held.processes[0].stdin.close()
        held.processes[0].stdout.read()
        held.processes[0].stdout.close()
        raise PermissionError(1, 'Operation not permitted')
    monkeypatch.setattr(module.subprocess, 'Popen', Held)
    monkeypatch.setattr(module.os, 'killpg', killpg)
    return held


def test_capture_survives_eperm_from_killing_an_already_exited_worker(exiting_worker):
    # GIVEN a worker that has returned its capture and exits only once the owner tries to kill it.
    source = '<html><body><p>article</p><script>setTimeout(()=>{},10000)</script></body></html>'
    # WHEN the reap grace expires and killpg meets the exited, unreaped worker.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.05)
    # THEN the capture is returned and the cleanly exited worker is reaped.
    assert (captured['capture_status'], [process.returncode for process in exiting_worker.processes], exiting_worker.kills) == (
        'partial', [0], [(exiting_worker.processes[0].pid, signal.SIGKILL)],
    ), 'EPERM from an already exited worker must not replace the capture.'


def test_refusal_survives_eperm_from_killing_an_already_exited_worker(exiting_worker):
    # GIVEN a concurrent timer-count quota page whose worker exits only once the owner tries to kill it.
    source = '<html><body><p>article</p><script>try{for(let i=0;i<201;i++)setTimeout(()=>{},10000)}catch(e){}</script></body></html>'
    # WHEN the refusal unwinds through the expired reap grace.
    with pytest.raises(BrewdocError, match='JS resource execution refused'):
        render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN the expected refusal survives and the cleanly exited worker is reaped.
    assert ([process.returncode for process in exiting_worker.processes], exiting_worker.kills) == (
        [0], [(exiting_worker.processes[0].pid, signal.SIGKILL)],
    ), 'EPERM from an already exited worker must not replace the refusal.'


@pytest.fixture
def budget_context(monkeypatch):
    """Yield a native context whose wall clock is test-driven while CPU clocks stay real."""
    from brewdoc._urljs import native
    wall = SimpleNamespace(now=0.0, step=0.0)
    def monotonic():
        wall.now += wall.step
        return wall.now
    monkeypatch.setattr(native, 'time', SimpleNamespace(
        monotonic=monotonic, thread_time=time.thread_time, process_time=time.process_time))
    with native.Context({}.__getitem__) as context:
        context.eval('globalThis.result="";globalThis.read=()=>result', 'bootstrap')
        context.save(['read'])
        yield SimpleNamespace(context=context, wall=wall)


def test_cheap_script_ignores_wall_clock_jumps_from_machine_load(budget_context):
    # GIVEN a wall clock that jumps 1 s on every read, as under heavy load.
    budget_context.wall.step = 1.0
    # WHEN one call runs a loop of about 10 ms CPU.
    budget_context.context.eval('let n=0;for(let i=0;i<1000000;i++)n++;result=String(n)', 'page')
    # THEN the 250 ms per-call budget counts CPU time and lets the loop finish.
    assert budget_context.context.call('read') == '1000000', 'Wall-clock load must not interrupt cheap script work.'


def test_cpu_runaway_script_interrupts_while_wall_clock_stands_still(budget_context):
    from brewdoc._urljs.native import NativeError
    # GIVEN a wall clock that never moves.
    assert budget_context.wall.step == 0.0, 'The wall clock must stay frozen.'
    # WHEN one call runs a loop of about 10 s CPU.
    with pytest.raises(NativeError) as caught:
        budget_context.context.eval('let n=0;for(let i=0;i<1000000000;i++)n++;result=String(n)', 'page')
    # THEN the CPU budget alone stops it as a trusted timing interrupt.
    assert (caught.value.resource, caught.value.timing) == (False, True), 'Runaway CPU work must interrupt by CPU time.'


def test_parent_death_stops_worker_waiting_for_module(tmp_path):
    # GIVEN a separate controlling process blocked on host module delivery.
    script = tmp_path / 'parent.py'
    script.write_text('''import json, threading, time
import brewdoc.htmljs as module
module._check_runtime()
original=module.subprocess.Popen
def start(*args,**options):
    child=original(*args,**options)
    print(json.dumps({"pid":child.pid}),flush=True)
    return child
module.subprocess.Popen=start
def fetch(request):
    print("module_wait",flush=True)
    threading.Event().wait(60)
module.render('<html><body><p>article</p><script>import("./module.js")</script></body></html>',
              'https://fixture.test/',fetch,time.monotonic()+45,soft_window=12.0)
''')
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([parent.stdout], [], [], 5)
        assert ready == [parent.stdout], 'The controller must publish the owned worker PID.'
        child_pid = json.loads(parent.stdout.readline())['pid']
        ready, _, _ = select.select([parent.stdout], [], [], 5)
        assert ready == [parent.stdout], 'The child must reach the native module wait.'
        assert parent.stdout.readline().strip() == 'module_wait', 'The test must kill the parent during module IPC.'
        # WHEN the controlling process disappears during the native loader wait.
        parent.kill()
        parent.wait(5)
        deadline = time.monotonic() + 5
        status = 'running'
        while status and time.monotonic() < deadline:
            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(child_pid)], capture_output=True, text=True).stdout.strip().strip('Z')
            time.sleep(0.01)
        # THEN no executable child remains after the parent-death watchdog.
        assert status == '', 'The worker must exit after its controlling parent dies.'
    finally:
        parent.kill()
        parent.wait(5)
        parent.stdout.close()
        parent.stderr.close()


def test_exact_promise_job_limit_and_outstanding_timer_limit_are_allowed():
    # GIVEN exactly the inclusive job and outstanding timer budgets.
    script = 'for(let i=0;i<2000;i++)Promise.resolve().then(()=>{});for(let i=0;i<200;i++)setTimeout(()=>{},10000)'
    source = '<html><body><p>article</p><script>' + script + '</script></body></html>'
    # WHEN capture drains every accepted job and stops before the later timers.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.05)
    # THEN exact inclusive limits are accepted with truthful pending state.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending'], captured['counts']) == (
        source, 'partial', [], {**IDLE, 'timers': 200}, {'promise_jobs': 2000, 'timer_callbacks': 0},
    ), 'Exactly 2000 jobs and 200 later timers must stay accepted, with the timers preventing settled status.'


@pytest.mark.parametrize('change', [
    {'pending': []},
    {'errors': [{'category': [], 'resource_url': None}]},
    {'capture_status': 'settled', 'error_count': 1},
    {'capture_status': 'settled', 'pending': {'requests': 0, 'timers': 1, 'modules': 0, 'promises': 0, 'jobs': False}},
])
def test_malformed_child_capture_metadata_is_always_a_hard_refusal(change):
    from brewdoc.htmljs import OMISSIONS, _validate_capture
    # GIVEN child-controlled metadata that contradicts its declared shape.
    record = {'html': '<html><body>article</body></html>', 'capture_status': 'partial',
              'errors': [], 'error_count': 0, 'pending': {'requests': 0, 'timers': 0, 'modules': 0, 'promises': 0, 'jobs': False},
              'counts': {'promise_jobs': 0, 'timer_callbacks': 0}, 'engine': 'QuickJS-ng 0.17.0',
              'dom_bundle': 'LinkeDOM 0.18.13', 'soft_window_seconds': 12, 'unsupported': list(OMISSIONS), **change}
    # WHEN the parent validates the private result before receipt assembly.
    with pytest.raises(BrewdocError, match='metadata invalid'):
        _validate_capture(record, 12)
    # THEN malformed metadata cannot cause a raw type error or settled claim.


def test_deep_private_json_is_refused_without_raw_recursion_error():
    from brewdoc.htmljs import _receive
    # GIVEN a bounded private message that exceeds the JSON parser's depth.
    class Channel:
        def recv_bytes(self, limit):
            return b'[' * 2000 + b'0' + b']' * 2000
    # WHEN the parent decodes the private result.
    with pytest.raises(BrewdocError, match='IPC (record )?invalid'):
        _receive(Channel())
    # THEN deep malformed data is a hard IPC refusal.


@pytest.mark.skipif(sys.platform != 'linux', reason='Intentional native fault delivery runs in Linux isolation.')
@pytest.mark.parametrize('signal_name', ['SIGSEGV', 'SIGBUS', 'SIGABRT', 'SIGILL', 'SIGFPE'])
def test_native_fault_handler_exits_child_normally_without_unhandled_signal(signal_name):
    # GIVEN the verified child-only alternate-stack native handler.
    number = getattr(signal, signal_name)
    script = '''import ctypes
from brewdoc import htmljs
htmljs._CHILD_ONLY=True
def deliver():
    library=ctypes.CDLL(None)
    library.pthread_self.restype=ctypes.c_void_p
    library.pthread_kill.argtypes=[ctypes.c_void_p,ctypes.c_int]
    library.pthread_kill(library.pthread_self(),%d)
htmljs._guarded_call(deliver,protect=True)
''' % int(number)
    # WHEN Linux delivers a fatal signal to the guarded native thread.
    child = subprocess.run([sys.executable, '-I', '-c', script], capture_output=True, timeout=5)
    # THEN native _exit produces a normal positive status with no diagnostics.
    assert (child.returncode, child.stdout, child.stderr) == (int(number), b'', b''), 'The native handler must exit without an unhandled signal or Python callback.'
