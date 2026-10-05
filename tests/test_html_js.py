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

import pytest

from brewdoc.htmljs import render
from brewdoc.common import BrewdocError

try:
    importlib.metadata.version('quickjs-ng')
    AVAILABLE = (sys.platform == 'darwin' and platform.machine() == 'arm64'
                 or sys.platform == 'linux' and platform.libc_ver()[0] == 'glibc')
except importlib.metadata.PackageNotFoundError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason='Optional public native API is unavailable.')


def test_inline_mutation_reaches_frozen_snapshot():
    # GIVEN a page whose primary content is created by JavaScript.
    source = '<html><body><p id="result">pending</p><script>document.querySelector("#result").textContent="article"</script></body></html>'
    # WHEN the installed worker executes the finite script.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN the frozen DOM contains the created text with truthful status.
    assert '<p id="result">article</p>' in captured['html'], 'The inline script must update the exported DOM.'
    assert captured['capture_status'] == 'settled', 'No pending work should remain.'


def test_later_timer_returns_partial_snapshot():
    # GIVEN useful initial content and a timer outside the soft window.
    source = '<html><body><p>article</p><script>setTimeout(()=>document.body.append("late"),10000)</script></body></html>'
    # WHEN capture reaches its soft window.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.05)
    # THEN useful DOM survives and later work is explicitly pending.
    assert captured['capture_status'] == 'partial', 'A pending timer must prevent a complete label.'
    assert captured['pending']['timers'] == 1, 'Exactly one later timer remains.'


def test_runaway_script_refuses_capture():
    # GIVEN an unbounded CPU loop.
    source = '<html><body><p>article</p><script>while(true){}</script></body></html>'
    # WHEN capture executes the script under its native interrupt.
    with pytest.raises(BrewdocError, match='resource'):
        render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN the exception prevents a snapshot from reaching conversion.


@pytest.mark.parametrize('script', [
    pytest.param('try{function f(){f()}f()}catch(e){}', marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
    pytest.param('try{const code="function f(){let "+Array.from({length:2000},(_,i)=>"v"+i+"=0").join(",")+";f()}f()";eval(code)}catch(e){}', marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
    pytest.param('try{function f(){f.apply(null,Array(10000).fill(1))}f()}catch(e){}', marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
    pytest.param('try{eval("(".repeat(10000)+"0"+")".repeat(10000))}catch(e){}', marks=pytest.mark.skipif(sys.platform == 'darwin', reason='Native fault probes run in Linux isolation.')),
    'try{new Uint8Array(80*1024*1024)}catch(e){}',
    'try{for(let i=0;i<201;i++)setTimeout(()=>{},10000)}catch(e){}',
    'try{document.write("x".repeat(8*1024*1024+1))}catch(e){}',
    'function again(){Promise.resolve().then(again)}again()',
    'setInterval(()=>{},0)',
    'document.body.textContent="x".repeat(8*1024*1024+1)',
])
def test_caught_or_runaway_resource_exhaustion_still_refuses(script):
    # GIVEN script work that exhausts one hard structural or execution bound.
    source = '<html><body><p>article</p><script>' + script + '</script></body></html>'
    # WHEN the page tries to catch errors or retain useful initial text.
    # Keep soft capture beyond the hard deadline so slow timers cannot return partial.
    with pytest.raises(BrewdocError, match='resource|crash'):
        render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=12)
    # THEN exhaustion still prevents publication of a partial snapshot.


@pytest.mark.parametrize('script', [
    'Object.defineProperty(document.body,"toString",{value:()=>"forged"})',
    'Object.defineProperty(document.querySelector("p").firstChild,"toString",{value:()=>"forged"})',
    'Object.defineProperty(document,"childNodes",{value:["forged"]})',
    'Object.defineProperty(document.querySelector("p"),Symbol.toPrimitive,{value:()=>"forged"})',
    'customElements.define("x-article",class extends HTMLElement{toString(){return "forged"}});document.body.append(document.createElement("x-article"))',
])
def test_serializer_tampering_refuses_forged_snapshot(script):
    # GIVEN page-controlled overrides that do not change actual document text.
    source = '<html><body><p>article</p><script>' + script + '</script></body></html>'
    # WHEN the trusted serializer observes the overridden node capability.
    with pytest.raises(BrewdocError, match='resource'):
        render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN forged Markdown source is refused.


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
    assert captured['capture_status'] == 'partial', 'Ordinary listener failure must retain useful DOM.'
    assert captured['errors'] == [{'category': 'javascript_callback_failed', 'resource_url': None}], 'Listener diagnostics must be sanitized.'


def test_dynamic_module_unresolved_await_is_observable_partial():
    # GIVEN an imported graph that never resolves top-level await.
    source = '<html><body><p>article</p><script>import("./module.js")</script></body></html>'
    def fetch(request):
        return {'status': 200, 'headers': (), 'final_url': request['url'],
                'body': b'await new Promise(()=>{});export const value=42'}
    # WHEN the public native Promise hook observes the unresolved graph.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=0.05)
    # THEN pending async state prevents a settled claim.
    assert captured['capture_status'] == 'partial', 'Unknown imported completion must remain partial.'
    assert captured['pending']['promises'] > 0, 'Unresolved imported promises must be counted.'


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
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=0.1)
    # THEN the response body remains hidden with ordinary partial provenance.
    assert captured['capture_status'] == 'partial', 'CORS denial must retain useful initial DOM.'
    assert captured['errors'] == [{'category': 'cors_denied', 'resource_url': None}], 'CORS ambiguity must deny exposure.'
    assert '<p id="result">article</p>' in captured['html'], 'Denied cross-origin bytes must never replace content.'


@pytest.mark.parametrize(('script', 'resources', 'expected', 'status'), [
    ('fetch("/data").then(r=>r.json()).then(d=>document.querySelector("#result").textContent=d.value)',
     {'https://fixture.test/data': {'body': b'{"value":"fetch content"}'}}, 'fetch content', 'settled'),
    ('const x=new XMLHttpRequest();x.onload=()=>document.querySelector("#result").textContent=x.responseText;x.open("GET","/data");x.send()',
     {'https://fixture.test/data': {'body': b'xhr content'}}, 'xhr content', 'settled'),
    ('window.addEventListener("load",()=>setTimeout(()=>document.querySelector("#result").textContent="loaded",1))', {}, 'loaded', 'settled'),
    ('const s=document.createElement("script");s.src="/chunk.js";document.body.appendChild(s)',
     {'https://fixture.test/chunk.js': {'body': b'document.querySelector("#result").textContent="chunk"'}}, 'chunk', 'settled'),
    ('import("./module.js").then(m=>document.querySelector("#result").textContent=m.value)',
     {'https://fixture.test/module.js': {'body': b'export const value="imported"'}}, 'imported', 'partial'),
    ('JSON.stringify=()=>"[]";Map.prototype.get=()=>null;fetch("/data").then(r=>r.text()).then(t=>document.querySelector("#result").textContent=t)',
     {'https://fixture.test/data': {'body': b'protected'}}, 'protected', 'settled'),
])
def test_host_apis_dynamic_scripts_and_imports_preserve_created_content(script, resources, expected, status):
    # GIVEN bounded sources with an independent expected content value.
    source = '<html><body><p id="result">pending</p><script>' + script + '</script></body></html>'
    def fetch(request):
        return {'status': 200, 'headers': (), 'final_url': request['url'], **resources[request['url']]}
    # WHEN the worker requests only those parent-owned resources.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=0.1)
    # THEN the created content reaches the frozen DOM and queues settle.
    assert '<p id="result">' + expected + '</p>' in captured['html'], 'The proved host API must preserve its useful content.'
    assert captured['capture_status'] == status, 'Import-graph completion must remain unknown.'


def test_unhandled_rejection_keeps_useful_content_partial():
    # GIVEN useful content and an unhandled analytics rejection.
    source = '<html><body><p>article</p><script>Promise.reject(new Error("secret"))</script></body></html>'
    # WHEN the native rejection tracker observes the asynchronous failure.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN partial provenance reports only a stable category.
    assert captured['capture_status'] == 'partial', 'An unhandled rejection must be visible.'
    assert captured['errors'] == [{'category': 'unhandled_promise_rejection', 'resource_url': None}], 'Raw page exception text must not escape.'


def test_private_destination_refuses_even_when_page_catches_rejection():
    from brewdoc.htmlurl import URLPolicyError
    # GIVEN a page that attempts to hide a containment violation.
    source = '<html><body><p>article</p><script>fetch("http://127.0.0.1/").catch(()=>{})</script></body></html>'
    def refuse(request):
        raise URLPolicyError('private destination refused')
    # WHEN the authoritative parent transport refuses the target.
    with pytest.raises(URLPolicyError, match='private destination'):
        render(source, 'https://fixture.test/', refuse, time.monotonic() + 10, soft_window=0.1)
    # THEN no caught JS rejection can downgrade the host refusal to partial.


def test_success_and_soft_capture_reap_owned_worker(monkeypatch):
    import brewdoc.htmljs as module
    # GIVEN a tracked child process for a useful partial capture.
    processes = []
    original = module.subprocess.Popen
    def start(*args, **options):
        process = original(*args, **options)
        processes.append(process)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', start)
    # WHEN a later timer remains after the short soft window.
    captured = render('<html><body><p>article</p><script>setTimeout(()=>{},10000)</script></body></html>',
                      'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.05)
    # THEN the child exits normally and is already reaped by its owner.
    assert captured['capture_status'] == 'partial', 'The later timer must remain partial.'
    assert tuple(process.returncode for process in processes) in ((0,), (int(signal.SIGUSR1), 0)), 'Only the optional prerequisite child and the finished capture may run.'
    with pytest.raises(ProcessLookupError):
        os.kill(processes[-1].pid, 0)


def test_hard_deadline_kills_and_reaps_worker(monkeypatch):
    import brewdoc.htmljs as module
    # GIVEN a tracked page whose timer extends beyond the hard deadline.
    processes = []
    original = module.subprocess.Popen
    def start(*args, **options):
        process = original(*args, **options)
        processes.append(process)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', start)
    # WHEN the authoritative watchdog reaches its deadline.
    with pytest.raises(BrewdocError, match='resource|crash'):
        render('<html><body><p>article</p><script>setTimeout(()=>{},10000)</script></body></html>',
               'https://fixture.test/', lambda request: None, time.monotonic() + 0.15, soft_window=12)
    # THEN the one owned child is killed and reaped before returning.
    assert tuple(process.returncode for process in processes) in ((-signal.SIGKILL,), (int(signal.SIGUSR1), -signal.SIGKILL)), 'The prerequisite must exit and the watchdog must kill the capture.'
    with pytest.raises(ProcessLookupError):
        os.kill(processes[-1].pid, 0)


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
              'https://fixture.test/',fetch,time.monotonic()+45)
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
    assert captured['counts']['promise_jobs'] == 2000, 'Exactly 2000 jobs must remain within the inclusive cap.'
    assert captured['pending']['timers'] == 200, 'Exactly 200 later timers must remain accepted.'
    assert captured['capture_status'] == 'partial', 'Pending later timers must prevent settled status.'


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
@pytest.mark.parametrize('number', [signal.SIGSEGV, signal.SIGBUS, signal.SIGABRT, signal.SIGILL, signal.SIGFPE])
def test_native_fault_handler_exits_child_normally_without_unhandled_signal(number):
    # GIVEN the verified child-only alternate-stack native handler.
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
