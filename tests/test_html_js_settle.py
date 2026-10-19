"""Check JS capture settle, per-request soft window and ancillary sub-request failures."""

import hashlib
import importlib.metadata
import platform
import re
import ssl
import sys
import threading
import time

import pytest

from brewdoc import htmljs, htmlurl
from brewdoc.common import BrewdocError
from brewdoc.htmljs import OMISSIONS, render
from test_html_timeout import CHAPTER, URL, _html_response, _replace_worker
from test_html_url import JS_ENVELOPE, RAN_ENGINE, converted, static_page

try:
    importlib.metadata.version('quickjs-ng')
    AVAILABLE = (sys.platform == 'darwin' and platform.machine() == 'arm64'
                 or sys.platform == 'linux' and platform.libc_ver()[0] == 'glibc')
except importlib.metadata.PackageNotFoundError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason='Optional public native API is unavailable.')

SOFT_WINDOW = 6.0
# Child start plus 500 ms settle under parallel load; half the soft window keeps the bound meaningful.
SETTLE_BOUND = 3.0
IDLE = {'requests': 0, 'timers': 0, 'modules': 0, 'promises': 0, 'jobs': False}
FAILED = 'https://fixture.test/data?token=[redacted]'


def _paragraph(html):
    """Return the text of the single synthetic result paragraph."""
    return re.fullmatch(r'.*<p id="result">(.*?)</p>.*', html, re.S).group(1)


def _public_lookup(*args):
    """Resolve every synthetic host to one fixed public address."""
    return ('93.184.216.34',)


def _dns_failure(*args):
    """Fail name resolution the way the DNS worker reports it."""
    raise htmlurl._error('URL DNS resolution failed', 'dns')


def _tls_failure(*args):
    """Fail the handshake with a real certificate verification error."""
    raise ssl.SSLCertVerificationError('synthetic certificate mismatch')


def _connect_failure(*args):
    """Fail the TCP connect with a real refused-connection error."""
    raise ConnectionRefusedError('synthetic refused connection')


def _js_url(tmp_path, body, snapshot, timeout=10.0, kind='javascript_dom', **changes):
    """Return local snapshot conversion code and exact JS URL result; `changes` replace acquisition keys."""
    _, (code, local, markdown) = converted(tmp_path, URL, '.html', snapshot)
    acquisition = static_page(URL, body, snapshot) | JS_ENVELOPE | {
        'engine': RAN_ENGINE, 'soft_window_seconds': timeout / 2, 'timeout_seconds': timeout,
        'snapshot': {'kind': kind, 'bytes': len(snapshot), 'sha256': hashlib.sha256(snapshot).hexdigest()}}
    return code, (0, dict(local, source=URL, receipt_schema='brewdoc.receipt/2', acquisition=acquisition | changes),
                  markdown)


@pytest.mark.parametrize(('script', 'expected'), [
    pytest.param('setInterval(()=>{},0)', ('article', 'partial', [], 0, {**IDLE, 'timers': 1},
                 {'promise_jobs': 0, 'timer_callbacks': 1}), id='zero-delay-interval-does-not-block'),
    pytest.param('setInterval(()=>{document.querySelector("#result").textContent="tick"},1000)',
                 ('article', 'partial', [], 0, {**IDLE, 'timers': 1}, {'promise_jobs': 0, 'timer_callbacks': 0}),
                 id='interval-does-not-block'),
    pytest.param('new Promise(()=>{})', ('article', 'partial', [], 0, {**IDLE, 'promises': 1},
                 {'promise_jobs': 0, 'timer_callbacks': 0}), id='unresolved-promise-does-not-block'),
    pytest.param('setTimeout(()=>{document.querySelector("#result").textContent="late"},2000)',
                 ('article', 'partial', [], 0, {**IDLE, 'timers': 1}, {'promise_jobs': 0, 'timer_callbacks': 0}),
                 id='one-shot-timer-beyond-settle-window-does-not-block'),
    pytest.param('setTimeout(()=>{document.querySelector("#result").textContent="late"},300);setInterval(()=>{},1000)',
                 ('late', 'partial', [], 0, {**IDLE, 'timers': 1}, {'promise_jobs': 0, 'timer_callbacks': 1}),
                 id='one-shot-timer-within-settle-window-blocks-beside-interval'),
    pytest.param('function frame(){requestAnimationFrame(frame)}frame()',
                 ('article', 'partial', [], 0, {**IDLE, 'timers': 1}, {'promise_jobs': 0, 'timer_callbacks': 0}),
                 id='animation-frame-loop-does-not-block'),
])
def test_capture_finishes_on_quiescence_well_before_the_soft_window(script, expected):
    # GIVEN useful content plus work that is pending but not blocking settle.
    source = '<html><body><p id="result">article</p><script>' + script + '</script></body></html>'
    # WHEN capture runs with a soft window far above the settle bound.
    started = time.monotonic()
    captured = render(source, 'https://fixture.test/', lambda request: None, started + 30, soft_window=SOFT_WINDOW)
    elapsed = time.monotonic() - started
    # THEN it stops once quiescent, keeps pending work visible and stays partial.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'], captured['error_count'],
            captured['pending'], captured['counts'], elapsed) == (
        *expected, pytest.approx(0.0, abs=SETTLE_BOUND),
    ), 'intervals, unresolved promises and timers due after 500 ms must not hold capture open'


def test_animation_frame_loop_beside_a_slow_fetch_never_spends_the_timer_cap():
    # GIVEN a page looping on animation frames while its one fetch answers after five seconds.
    script = ('function frame(){requestAnimationFrame(frame)}frame();'
              'fetch("/data").then(r=>r.text()).then(t=>{document.querySelector("#result").textContent=t})')
    source = '<html><body><p id="result">article</p><script>' + script + '</script></body></html>'

    def fetch(request):
        """Answer the single data request after five seconds."""
        threading.Event().wait(5)
        return {'status': 200, 'headers': (), 'final_url': request['url'], 'body': b'loaded'}
    # WHEN capture serves the fetch early in the default 12 s soft window.
    started = time.monotonic()
    captured = render(source, 'https://fixture.test/', fetch, started + 30, soft_window=12.0)
    elapsed = time.monotonic() - started
    # THEN frames neither refuse nor hold capture open after the fetch completes.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'], captured['error_count'],
            captured['pending'], captured['counts'], elapsed) == (
        'loaded', 'partial', [], 0, {**IDLE, 'timers': 1}, {'promise_jobs': 4, 'timer_callbacks': 0},
        pytest.approx(5.0, abs=SETTLE_BOUND),
    ), 'animation frames must not block settle or count toward the timer callback cap'


def test_soft_window_is_checked_before_each_queued_request():
    # GIVEN three queued requests and a slow server answering after one soft window.
    soft_window = 2.0
    script = ''.join('fetch("/data-%d").then(r=>r.text()).catch(()=>{});' % index for index in range(3))
    source = '<html><body><p id="result">article</p><script>' + script + '</script></body></html>'
    requested = []

    def fetch(request):
        """Answer each request only after a full soft window has passed."""
        requested.append(request['url'])
        threading.Event().wait(soft_window)
        return {'status': 200, 'headers': (), 'final_url': request['url'], 'body': b'data'}

    # WHEN the first answer arrives after the soft window has ended.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 30, soft_window=soft_window)
    # THEN no further queued request is served and unserved ones stay pending.
    assert (requested, captured['capture_status'], captured['errors'], captured['pending']['requests']) == (
        ['https://fixture.test/data-0'], 'partial', [], 2,
    ), 'the soft window must stop serving queued requests instead of draining the whole queue first'


def test_parent_records_the_url_of_a_failed_ancillary_request():
    # GIVEN a page whose one sub-request fails in transport and is caught by the page.
    source = ('<html><body><p id="result">article</p><script>fetch("/data").then(r=>r.text())'
              '.catch(()=>document.querySelector("#result").textContent="fallback")</script></body></html>')

    def fetch(request):
        """Represent a sub-request whose transport fails without a policy refusal."""
        raise BrewdocError('URL transport failed')

    # WHEN the parent classifies the failure for the untrusted child.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN the capture succeeds and the parent alone names the failed resource.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'], captured['error_count']) == (
        'fallback', 'partial', [{'category': 'ancillary_transport_failed', 'resource_url': 'https://fixture.test/data'}], 1,
    ), 'the parent must record the validated sub-request URL for its own transport diagnostic'


@pytest.mark.parametrize(('lookup', 'connect'), [
    pytest.param(_dns_failure, _connect_failure, id='dns'),
    pytest.param(_public_lookup, _tls_failure, id='tls'),
    pytest.param(_public_lookup, _connect_failure, id='connect'),
])
def test_failed_sub_request_transport_keeps_the_capture(lookup, connect, monkeypatch, tmp_path):
    # GIVEN acquired main HTML and a real sub-request fetch whose transport fails.
    body = (b"<html><body><p>Initial article</p><script>fetch('/data?token=secret').then(r=>r.text())"
            b".then(t=>document.querySelector('p').textContent=t)"
            b".catch(()=>document.querySelector('p').textContent='Fallback article')</script></body></html>")
    snapshot = body.replace(b'<html><body><p>Initial', b'<html><head></head><body><p>Fallback')
    local_code, expected = _js_url(tmp_path, body, snapshot, capture_status='partial', error_count=1, pending=IDLE,
                                   errors=[{'category': 'ancillary_transport_failed', 'resource_url': FAILED}],
                                   counts={'requests': 2, 'promise_jobs': 3, 'timer_callbacks': 0})
    original = htmlurl._fetch

    def fetch(url, *, kind='html', **options):
        """Serve the main page locally and send the sub-request through real transport."""
        return {'html': lambda: _html_response(body, **options),
                'api': lambda: original(url, kind=kind, **options)}[kind]()

    monkeypatch.setattr(htmlurl, '_fetch', fetch)
    monkeypatch.setattr(htmlurl, '_dns_lookup', lookup)
    monkeypatch.setattr(htmlurl, '_connect', connect)
    assert local_code == 0, 'the expected fallback snapshot must convert as a local file'
    # WHEN JavaScript capture runs with the failing ancillary request.
    result = htmlurl._run_url(URL, render_js=True, timeout=10)
    # THEN the page converts and the receipt names the redacted failed resource.
    assert result == expected, 'a TLS, DNS or connect failure of one sub-request must not refuse the whole capture'


def test_sub_request_policy_refusal_stays_hard_and_names_the_resource(monkeypatch):
    # GIVEN acquired main HTML and a sub-request resolving to a private address.
    body = b"<html><body><p>Initial article</p><script>fetch('/data?token=secret').catch(()=>{})</script></body></html>"
    original = htmlurl._fetch

    def fetch(url, *, kind='html', **options):
        """Serve the main page locally and send the sub-request through real policy checks."""
        return {'html': lambda: _html_response(body, **options),
                'api': lambda: original(url, kind=kind, **options)}[kind]()

    monkeypatch.setattr(htmlurl, '_fetch', fetch)
    monkeypatch.setattr(htmlurl, '_dns_lookup', lambda *args: ('127.0.0.1',))
    # WHEN the parent refuses the private destination even though the page catches it.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=10)
    acquisition = receipt['acquisition']
    # THEN the refusal stays hard and its envelope names the redacted sub-request.
    assert (code, markdown, acquisition['capture_status'], acquisition['refusal_stage'], acquisition['errors']) == (
        1, '', 'refused', 'dns', [{'category': 'dns', 'resource_url': FAILED}],
    ), 'a sub-request policy refusal must stay hard and carry the URL that caused it'


def test_main_document_tls_failure_stays_a_hard_refusal(monkeypatch):
    # GIVEN a main document whose real TLS handshake fails.
    monkeypatch.setattr(htmlurl, '_dns_lookup', _public_lookup)
    monkeypatch.setattr(htmlurl, '_connect', _tls_failure)
    # WHEN JavaScript acquisition requests that document.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=10)
    acquisition = receipt['acquisition']
    # THEN the main-document TLS failure is never softened like a sub-request.
    assert (code, markdown, receipt['reason'], acquisition['capture_status'], acquisition['refusal_stage']) == (
        1, '', 'URL acquisition refused during tls', 'refused', 'tls',
    ), 'only sub-request transport failures may become ancillary; the main document must refuse'


FETCH_FALLBACK = ('fetch("%s").then(r=>r.text())'
                  '.catch(e=>{document.querySelector("#result").textContent="fallback "+e.name})')
XHR_FALLBACK = ('const x=new XMLHttpRequest();x.open("GET","%s");'
                'x.onerror=e=>{document.querySelector("#result").textContent="fallback "+e.type};x.send()')


@pytest.mark.parametrize(('script', 'expected'), [
    pytest.param(FETCH_FALLBACK % 'http://[', 'fallback TypeError', id='fetch-unparseable'),
    pytest.param(XHR_FALLBACK % 'http://[', 'fallback error', id='xhr-unparseable'),
    pytest.param(FETCH_FALLBACK % 'http://a b/', 'fallback TypeError', id='fetch-syntax-invalid'),
    pytest.param(XHR_FALLBACK % 'http://a b/', 'fallback error', id='xhr-syntax-invalid'),
])
def test_invalid_sub_request_url_rejects_in_the_page_and_keeps_the_capture(script, expected):
    # GIVEN a page whose one sub-request URL cannot be parsed and whose failure handler renders fallback text.
    source = '<html><body><p id="result">article</p><script>' + script + '</script></body></html>'
    requested = []
    # WHEN capture handles the unparseable sub-request URL.
    captured = render(source, 'https://fixture.test/', requested.append, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN the page sees a rejection, nothing is fetched and the unnamed failure stays ancillary.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'], captured['error_count'],
            requested) == (
        expected, 'partial', [{'category': 'ancillary_transport_failed', 'resource_url': None}], 1, [],
    ), 'an invalid sub-request URL must reject like a browser TypeError, not escape the capture as ValueError'


@pytest.mark.parametrize('target', [
    pytest.param("'http://['", id='unparseable'),
    pytest.param("'/'+'a'.repeat(9000)", id='ascii-over-length'),
    pytest.param("'/'+'\\u00e9'.repeat(4100)", id='two-byte-over-length'),
    pytest.param("'data:,x'", id='data-scheme'),
])
def test_invalid_sub_request_url_keeps_the_url_capture(monkeypatch, tmp_path, target):
    # GIVEN acquired main HTML whose one sub-request URL is unparseable or over the length limit, caught by the page.
    body = ("<html><body><p>Initial article</p><script>fetch(" + target + ").then(r=>r.text())"
            ".then(t=>document.querySelector('p').textContent=t)"
            ".catch(()=>document.querySelector('p').textContent='Fallback article')</script></body></html>").encode()
    snapshot = body.replace(b'<html><body><p>Initial', b'<html><head></head><body><p>Fallback')
    local_code, expected = _js_url(tmp_path, body, snapshot, capture_status='partial', error_count=1, pending=IDLE,
                                   errors=[{'category': 'ancillary_transport_failed', 'resource_url': None}],
                                   counts={'requests': 1, 'promise_jobs': 3, 'timer_callbacks': 0})
    kinds = []

    def fetch(url, *, kind='html', **options):
        """Serve the main page locally and record every acquisition kind."""
        kinds.append(kind)
        return _html_response(body, **options)

    monkeypatch.setattr(htmlurl, '_fetch', fetch)
    assert local_code == 0, 'the expected fallback snapshot must convert as a local file'
    # WHEN JavaScript capture runs through the URL boundary.
    result = htmlurl._run_url(URL, render_js=True, timeout=10)
    # THEN the page converts with its fallback and the receipt records one unnamed ancillary failure.
    assert (result, kinds) == (expected, ['html']), \
        'an invalid sub-request URL must not refuse the capture at javascript; main-document refusals stay hard'


@pytest.mark.parametrize('base', [pytest.param('http://[', id='unparseable'),
                                  pytest.param('http://a b/', id='syntax-invalid'),
                                  pytest.param('data:,x', id='data-scheme'),
                                  pytest.param('ftp://fixture.test/', id='ftp-scheme')])
def test_invalid_document_base_is_ignored_by_direct_js_render(base):
    # GIVEN a page whose base is unparseable or non-HTTP(S) and whose script resolves a relative anchor.
    source = ('<html><head><base href="' + base + '"></head><body><p id="result">pending</p><script>'
              'const a=document.createElement("a");a.href="guide";document.querySelector("#result").textContent=a.href'
              '</script></body></html>')
    requested = []
    # WHEN capture renders the page.
    captured = render(source, URL, requested.append, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN the document URL stays the base and the capture settles without an error.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'], captured['error_count'],
            requested) == ('https://fixture.test/guide', 'settled', [], 0, []), (
        'an invalid <base href> must be ignored like a browser, never raise an untyped ValueError')


@pytest.mark.parametrize('base', [pytest.param('http://[', id='unparseable'),
                                  pytest.param('http://a b/', id='syntax-invalid')])
def test_invalid_document_base_keeps_the_js_url_capture(monkeypatch, tmp_path, base):
    # GIVEN acquired main HTML with an invalid base, a relative link and a script resolving a relative anchor.
    body = ('<html><head><base href="' + base + '"></head><body><p id="result">pending</p>'
            '<p>See <a href="guide?q=1">guide</a></p><script>const a=document.createElement("a");a.href="guide";'
            'document.querySelector("#result").textContent=a.href</script></body></html>').encode()
    snapshot = body.replace(b'>pending<', b'>https://fixture.test/guide<').replace(
        b'"guide?q=1"', b'"https://fixture.test/guide?q=1"')
    local_code, expected = _js_url(tmp_path, body, snapshot, capture_status='settled', pending=IDLE,
                                   counts={'requests': 1, 'promise_jobs': 0, 'timer_callbacks': 0})
    monkeypatch.setattr(htmlurl, '_fetch', lambda url, *, kind='html', **options: _html_response(body, **options))
    assert (local_code, expected[2].partition(CHAPTER)[2]) == (
        0, 'https://fixture.test/guide\n\nSee [guide](https://fixture.test/guide?q=1)\n'), \
        'the expected snapshot must convert locally with links resolved against the document URL'
    # WHEN JavaScript capture runs through the URL boundary.
    result = htmlurl._run_url(URL, render_js=True, timeout=10)
    # THEN the capture settles and links resolve against the document URL.
    assert result == expected, 'an invalid <base href> must be ignored in JS capture, not refuse it at javascript'


def test_animation_frames_run_in_one_pass_share_the_frame_timestamp():
    # GIVEN a frame requested on one pump pass and another requested after a fetch on the next pass.
    script = ('setTimeout(()=>{let first;requestAnimationFrame(t=>{first=t});'
              'fetch("/data").then(()=>requestAnimationFrame(t=>{'
              'document.querySelector("#result").textContent=t===first?"same frame":"stale"}))},0)')
    source = '<html><body><p id="result">article</p><script>' + script + '</script></body></html>'
    # WHEN both frames run on the pass that served the fetch, after the clock moved on.
    captured = render(source, 'https://fixture.test/',
                      lambda request: {'status': 200, 'headers': (), 'final_url': request['url'], 'body': b''},
                      time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN both callbacks see the one frame timestamp of that pass.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'],
            captured['pending']) == ('same frame', 'settled', [], IDLE), (
        'all rAF callbacks run in one frame pass must share one deterministic timestamp')


@pytest.mark.parametrize(('script', 'expected'), [
    pytest.param(FETCH_FALLBACK % 'data:,x', 'fallback TypeError', id='fetch-data'),
    pytest.param(XHR_FALLBACK % 'data:,x', 'fallback error', id='xhr-data'),
    pytest.param(FETCH_FALLBACK % 'mailto:a@fixture.test', 'fallback TypeError', id='fetch-mailto'),
    pytest.param(FETCH_FALLBACK % 'ftp://fixture.test/x', 'fallback TypeError', id='fetch-ftp'),
    pytest.param(XHR_FALLBACK % 'a://x', 'fallback error', id='xhr-custom-scheme'),
])
def test_non_http_sub_request_rejects_in_the_page_and_keeps_the_capture(script, expected):
    # GIVEN a page whose one sub-request uses a non-HTTP(S) scheme and whose failure handler renders fallback text.
    source = '<html><body><p id="result">article</p><script>' + script + '</script></body></html>'
    requested = []
    # WHEN capture handles the refused sub-request scheme.
    captured = render(source, 'https://fixture.test/', requested.append, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN only that request fails in the page, nothing is fetched and the capture stays partial.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'], captured['error_count'],
            requested) == (
        expected, 'partial', [{'category': 'ancillary_transport_failed', 'resource_url': None}], 1, [],
    ), 'a non-HTTP(S) sub-request must fail alone like a browser network error, never refuse the whole capture'


@pytest.mark.parametrize('markup', [
    pytest.param('<script src="data:,x"></script>', id='script-data'),
    pytest.param('<script src="ftp://fixture.test/a.js"></script>', id='script-ftp'),
    pytest.param('<script type="module" src="a://x"></script>', id='module-custom-scheme'),
])
def test_non_http_script_source_fails_alone_and_later_scripts_run(markup):
    # GIVEN a script element with a non-HTTP(S) source followed by an inline script that writes content.
    source = ('<html><body><p id="result">article</p>' + markup
              + '<script>document.querySelector("#result").textContent="after"</script></body></html>')
    requested = []
    # WHEN capture loads the refused script source.
    captured = render(source, 'https://fixture.test/', requested.append, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN that script fails alone, nothing is fetched and the later script still runs.
    assert (_paragraph(captured['html']), captured['capture_status'], captured['errors'], captured['error_count'],
            captured['pending'], requested) == (
        'after', 'partial', [{'category': 'ancillary_transport_failed', 'resource_url': None},
                             {'category': 'script_resource_failed', 'resource_url': None}], 2, IDLE, [],
    ), 'a non-HTTP(S) script source must fail that script only, never refuse the whole capture'


@pytest.mark.parametrize('media', ['application/x-javascript', 'text/jscript', 'text/javascript1.5'])
def test_legacy_javascript_mime_script_runs_through_the_url_boundary(monkeypatch, tmp_path, media):
    # GIVEN acquired main HTML whose external script is served with a legacy JavaScript MIME essence.
    page = b'<html><body><p id="result">pending</p><script src="/legacy.js"></script></body></html>'
    script = b'document.querySelector("#result").textContent="legacy script"'
    snapshot = page.replace(b'<html><body>', b'<html><head></head><body>').replace(b'pending', b'legacy script')
    local_code, expected = _js_url(tmp_path, page, snapshot, capture_status='settled', pending=IDLE,
                                   counts={'requests': 2, 'promise_jobs': 0, 'timer_callbacks': 0})
    responses = {URL: (('Content-Type', 'text/html'),), 'https://fixture.test/legacy.js': (('Content-Type', media),)}
    bodies = {URL: page, 'https://fixture.test/legacy.js': script}
    requested = []

    def request(url, deadline, accept, origin, requested_with=False):
        """Serve the synthetic page and script below real media validation."""
        requested.append(url)
        return {'status': 200, 'headers': responses[url], 'entity': bodies[url], 'body': bodies[url]}

    monkeypatch.setattr(htmlurl, '_request', request)
    assert local_code == 0, 'the expected rendered snapshot must convert as a local file'
    # WHEN JavaScript capture runs through the URL boundary.
    result = htmlurl._run_url(URL, render_js=True, timeout=10)
    # THEN the script is accepted, runs and the capture settles without errors.
    assert (result, requested) == (expected, [URL, 'https://fixture.test/legacy.js']), \
        'a WHATWG JavaScript MIME essence must be accepted for an external script like a browser'


HEAP = 'initial_html_heap_fallback'
DOM = 'initial_html_dom_fallback'
TIMER = 'initial_html_timer_fallback'
UNKNOWN = dict.fromkeys(('requests', 'timers', 'modules', 'promises', 'jobs'))
# Pre-boot heap exhaustion while LinkeDOM parses a large page (code.claude.com changelog).
LARGE_PAGE = '<html><body><p id="result">article</p>' + '<p>x</p>' * 200000 + '</body></html>'
CAUGHT_HEAP = 'try{new Uint8Array(80*1024*1024)}catch(error){}document.querySelector("p").textContent="Changed article"'
# Consent-manager wrapper; the raw-text serializer must not call page code (yakutia-daily).
WRAPPED_CREATE = ('const create=document.createElement;document.createElement=function(name){return create.call(document,name)};'
                  'document.querySelector("p").textContent="Changed article"')
# 100 self-rescheduling timers spend the 200-callback quota in 3 passes; never 200 at once (scheduler cap).
TIMER_CHAINS = ('document.querySelector("p").textContent="Changed article";'
                'for(let i=0;i<100;i++)(function tick(){setTimeout(tick,0)})()')
SIGNAL_WORKER = ("import os,sys\nfrom multiprocessing.connection import Connection\nfrom brewdoc.htmljs import _receive,_send\n"
                 "channel=Connection(int(sys.argv[1]))\n_receive(channel)\n_send(channel,{'kind':'error','cause':%r})\n"
                 "os._exit(%d)\n")


def _digest(capture):
    """Replace captured HTML by its SHA-256 so a large page mismatch stays readable."""
    return {**capture, 'html': hashlib.sha256(capture['html'].encode()).hexdigest()}


def _initial_fallback(source, cause):
    """Expected parent-labelled original HTML for one named recovery cause."""
    return _digest({'html': source, 'snapshot_kind': cause, 'capture_status': 'partial',
                    'errors': [{'category': cause, 'resource_url': None}], 'error_count': 1, 'pending': UNKNOWN,
                    'counts': {'promise_jobs': None, 'timer_callbacks': None}, 'engine': 'QuickJS-ng 0.17.0',
                    'dom_bundle': 'LinkeDOM 0.18.13', 'soft_window_seconds': SOFT_WINDOW, 'unsupported': list(OMISSIONS)})


@pytest.fixture
def owned_workers(monkeypatch):
    """Track real capture workers; await exit before the 0.1 s reap grace so load cannot force SIGKILL."""
    htmljs._check_runtime()
    processes = []

    class Tracked(htmljs.subprocess.Popen):
        """Record each started worker and wait for its own exit first."""

        def __init__(self, *args, **options):
            super().__init__(*args, **options)
            processes.append(self)

        def wait(self, timeout=None):
            super().wait(10)
            return super().wait(timeout)

    monkeypatch.setattr(htmljs.subprocess, 'Popen', Tracked)
    return processes


@pytest.mark.parametrize('source', [
    pytest.param(LARGE_PAGE, id='during-parse'),
    pytest.param('<html><body><p>article</p><script>' + CAUGHT_HEAP + '</script></body></html>', id='caught-during-script'),
])
def test_native_heap_exhaustion_falls_back_to_labelled_initial_html(owned_workers, source):
    # GIVEN a page that exhausts the 64 MiB native heap before or after any script runs.
    # WHEN capture meets the sticky heap denial.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN original HTML is published under the heap cause and the worker exits cleanly.
    assert (_digest(captured), [process.returncode for process in owned_workers]) == (
        _initial_fallback(source, HEAP), [0],
    ), 'heap exhaustion must fall back to acquired HTML like a timeout, never refuse or publish the JS DOM'


@pytest.mark.parametrize(('wire', 'cause'), [pytest.param('heap', HEAP, id='heap'),
                                             pytest.param('timers', TIMER, id='timers')])
def test_typed_fallback_signal_from_a_cleanly_exited_worker_falls_back(monkeypatch, wire, cause):
    # GIVEN a worker that reports one typed fallback cause and exits with status 0.
    processes = _replace_worker(monkeypatch, SIGNAL_WORKER % (wire, 0))
    source = '<html><body><p>article</p></body></html>'
    # WHEN the parent receives the typed terminal message.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN the wire cause alone selects its labelled initial HTML.
    assert (_digest(captured), [process.returncode for process in processes]) == (
        _initial_fallback(source, cause), [0],
    ), "a typed wire record {'kind': 'error', 'cause': ...} plus exit 0 must select its own fallback"


def test_typed_heap_signal_from_a_crashed_worker_stays_refused(monkeypatch):
    # GIVEN a worker that reports the typed heap cause but exits with status 1.
    _replace_worker(monkeypatch, SIGNAL_WORKER % ('heap', 1))
    source = '<html><body><p>article</p></body></html>'
    # WHEN the parent receives the heap message from a failing child.
    # THEN the crash still refuses; only a clean exit may publish the fallback.
    with pytest.raises(BrewdocError, match='^JS child crash refused$'):
        render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 30, soft_window=SOFT_WINDOW)


@pytest.mark.parametrize('name', [pytest.param('STYLE', id='style-nuipogoda'), 'TEXTAREA', 'TITLE'])
def test_upper_case_raw_text_element_converts_like_its_lower_case_form(name):
    # GIVEN a page that inserts a raw-text element created with an upper-case tag name.
    script = 'const e=document.createElement("' + name + '");e.textContent="note";document.head.appendChild(e)'
    source = '<html><head></head><body><p id="result">article</p><script>' + script + '</script></body></html>'
    # WHEN capture serializes the changed DOM.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN it settles like a browser; HTML tag names are case-insensitive, so compare lower case.
    assert (captured['html'].lower(), captured['capture_status'], captured['errors'], captured['error_count'],
            captured['pending']) == (
        source.replace('<head>', '<head><%s>note</%s>' % (name, name)).lower(), 'settled', [], 0, IDLE,
    ), 'createElement in an HTML document is case-insensitive; the snapshot guard must not refuse STYLE'


def test_replaced_create_element_falls_back_to_labelled_initial_html(owned_workers):
    # GIVEN a page that wraps document.createElement and then changes its text.
    source = '<html><body><p>Initial article</p><script>' + WRAPPED_CREATE + '</script></body></html>'
    # WHEN the snapshot guard refuses to serialize through page-owned createElement.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN original HTML is published under the DOM cause and the worker exits cleanly.
    assert (_digest(captured), [process.returncode for process in owned_workers]) == (
        _initial_fallback(source, DOM), [0],
    ), 'a correct DOM-check refusal must fall back to acquired HTML, never run page code or refuse the URL'


@pytest.mark.parametrize('script', [
    pytest.param(TIMER_CHAINS, id='parallel-chains'),
    pytest.param('document.querySelector("p").textContent="Changed article";function again(){setTimeout(again,0)}again()',
                 id='single-chain'),
])
def test_timer_callback_quota_exhaustion_falls_back_to_labelled_initial_html(owned_workers, script):
    # GIVEN a page that changes its text, then fires more than 200 timer callbacks (acloserlisten, therecoup).
    source = '<html><body><p>Initial article</p><script>' + script + '</script></body></html>'
    # WHEN capture spends the timer callback quota well inside the soft window.
    captured = render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 30, soft_window=SOFT_WINDOW)
    # THEN original HTML is published under the timer cause and the worker exits cleanly.
    assert (_digest(captured), [process.returncode for process in owned_workers]) == (
        _initial_fallback(source, TIMER), [0],
    ), 'timer callback quota exhaustion must fall back to acquired HTML like heap, never refuse or publish the JS DOM'


@pytest.mark.parametrize(('script', 'cause'), [pytest.param(CAUGHT_HEAP, HEAP, id='heap'),
                                               pytest.param(WRAPPED_CREATE, DOM, id='dom-check'),
                                               pytest.param(TIMER_CHAINS, TIMER, id='timer-callbacks')])
def test_url_receipt_names_the_initial_html_fallback_cause(monkeypatch, tmp_path, script, cause):
    # GIVEN acquired main HTML whose script triggers a heap, DOM-check or timer fallback after changing its text.
    body = ('<html><body><p>Initial article</p><script>' + script + '</script></body></html>').encode()
    snapshot = body.replace(b'<html><body>', b'<html><head></head><body>')
    local_code, expected = _js_url(tmp_path, body, snapshot, kind=cause, capture_status='partial', error_count=1,
                                   errors=[{'category': cause, 'resource_url': None}], pending=UNKNOWN,
                                   counts={'requests': 1, 'promise_jobs': None, 'timer_callbacks': None})
    monkeypatch.setattr(htmlurl, '_fetch', lambda url, *, kind='html', **options: _html_response(body, **options))
    assert local_code == 0, 'the canonical initial snapshot must convert as a local file'
    # WHEN JavaScript capture runs through the URL boundary.
    result = htmlurl._run_url(URL, render_js=True, timeout=10)
    # THEN the original article converts and the receipt names one cause for both error and snapshot kind.
    assert result == expected, \
        'a heap, DOM-check or timer fallback must publish acquired HTML with its own cause, not refuse at javascript'


SCRIPTS = ['https://fixture.test/s-%d.js' % index for index in range(3)]
SCRIPT_BODIES = {url: ('document.querySelector("#result").textContent="script %d"' % index).encode()
                 for index, url in enumerate(SCRIPTS)}
# Documented parent recovery after the hard deadline: htmljs.render `recovery_deadline = deadline + 1`.
RECOVERY = 1.0


@pytest.mark.parametrize('markup', [
    pytest.param(''.join('<script src="%s"></script>' % url for url in SCRIPTS), id='parser-inserted'),
    pytest.param('<script>for(const i of [0,1,2]){const s=document.createElement("script");s.src="/s-"+i+".js";'
                 'document.body.appendChild(s)}</script>', id='script-inserted-chunks'),
])
def test_soft_window_stops_initial_script_fetches_before_the_next_script(markup):
    # GIVEN three external scripts and a server answering each only after one full soft window.
    soft_window = 2.0
    source = '<html><body><p id="result">article</p>' + markup + '</body></html>'
    requested = []

    def fetch(request):
        """Answer each script only after a full soft window has passed."""
        requested.append(request['url'])
        threading.Event().wait(soft_window)
        return {'status': 200, 'headers': (), 'final_url': request['url'], 'body': SCRIPT_BODIES[request['url']]}

    # WHEN the first script arrives after the soft window has ended.
    captured = render(source, 'https://fixture.test/', fetch, time.monotonic() + 30, soft_window=soft_window)
    # THEN the fetched script runs, no further script is fetched and the two unfetched ones stay pending.
    assert (requested, _paragraph(captured['html']), captured['capture_status'], captured['errors'],
            captured['error_count'], captured['pending']) == (
        SCRIPTS[:1], 'script 0', 'partial', [], 0, {**IDLE, 'requests': 2},
    ), 'the soft window must stop serial initial script loading, not only the later request queue'


def test_slow_initial_scripts_end_the_url_capture_at_the_soft_window_inside_the_timeout(monkeypatch, tmp_path):
    # GIVEN a page with three external scripts, each served after one full soft window, never past the deadline.
    timeout, soft_window = 6.0, 3.0
    page = ('<html><body><p id="result">article</p>' + ''.join('<script src="%s"></script>' % url for url in SCRIPTS)
            + '</body></html>').encode()
    snapshot = page.replace(b'<html><body><p id="result">article', b'<html><head></head><body><p id="result">script 0')
    local_code, expected = _js_url(tmp_path, page, snapshot, timeout=timeout, capture_status='partial',
                                   pending={**IDLE, 'requests': 2},
                                   counts={'requests': 2, 'promise_jobs': 0, 'timer_callbacks': 0})
    bodies = {URL: page, **SCRIPT_BODIES}
    delays = {URL: 0.0, **dict.fromkeys(SCRIPTS, soft_window)}
    media = {URL: 'text/html', **dict.fromkeys(SCRIPTS, 'text/javascript')}
    requested = []

    def request(url, deadline, accept, origin, requested_with=False):
        """Serve the page at once and each script after its delay, bounded by the real deadline."""
        requested.append(url)
        threading.Event().wait(min(delays[url], max(0.0, deadline - time.monotonic())))
        htmlurl._remaining(deadline)
        return {'status': 200, 'headers': (('Content-Type', media[url]),), 'entity': bodies[url], 'body': bodies[url]}

    monkeypatch.setattr(htmlurl, '_request', request)
    assert local_code == 0, 'the expected partial snapshot must convert as a local file'
    # WHEN JavaScript capture runs through the URL boundary with one request timeout.
    started = time.monotonic()
    result = htmlurl._run_url(URL, render_js=True, timeout=timeout)
    elapsed = time.monotonic() - started
    # THEN capture stops at the soft window from the current DOM; total wall time stays within the deadline bound.
    assert (result, requested, elapsed) == (
        expected, [URL, SCRIPTS[0]], pytest.approx(0.0, abs=timeout + RECOVERY),
    ), 'slow initial scripts must stop at the soft window instead of running into the --timeout deadline'


MODULES = ['https://fixture.test/m-%d.js' % index for index in range(3)]
MODULE_BODIES = {
    **{url: ('import "./m-%d.js";document.querySelector("#result").textContent="module %d"' % (index + 1, index)).encode()
       for index, url in enumerate(MODULES[:2])},
    MODULES[2]: b'document.querySelector("#result").textContent="module 2"',
    'https://fixture.test/first.js': b'export const value="first"',
    'https://fixture.test/late.js': b'export const value="late"',
}
DYNAMIC = ('import("./first.js").then(m=>{document.querySelector("#result").textContent=m.value;return import("./late.js")})'
           '.then(m=>{document.querySelector("#result").textContent=m.value})')
REJECTED = [{'category': 'unhandled_promise_rejection', 'resource_url': None}]


@pytest.mark.parametrize(('markup', 'expected'), [
    pytest.param('<script type="module">import "./m-0.js"</script>', (MODULES[:1], 'article', [], 0),
                 id='static-import-chain'),
    pytest.param('<script>' + DYNAMIC + '.catch(()=>{})</script>', (['https://fixture.test/first.js'], 'first', [], 0),
                 id='dynamic-import-caught'),
    pytest.param('<script>' + DYNAMIC + '</script>', (['https://fixture.test/first.js'], 'first', REJECTED, 1),
                 id='dynamic-import-uncaught'),
])
def test_soft_window_stops_module_import_fetches_before_the_next_module(markup, expected):
    # GIVEN a module import chain and a server answering each module only after one full soft window.
    # 2 s like the script counterpart: worker bootstrap must finish inside the window under parallel load.
    soft_window = 2.0
    source = '<html><body><p id="result">article</p>' + markup + '</body></html>'
    requested = []

    def fetch(request):
        """Answer each module only after a full soft window has passed."""
        requested.append(request['url'])
        threading.Event().wait(soft_window)
        return {'status': 200, 'headers': (), 'final_url': request['url'], 'body': MODULE_BODIES[request['url']]}

    # WHEN the first module arrives after the soft window has ended.
    started = time.monotonic()
    captured = render(source, 'https://fixture.test/', fetch, started + 30, soft_window=soft_window)
    elapsed = time.monotonic() - started
    # THEN the next import is refused unfetched and stays one pending request; only page-visible rejections are errors.
    assert (requested, _paragraph(captured['html']), captured['errors'], captured['error_count'], captured['capture_status'],
            captured['pending'], elapsed) == (
        *expected, 'partial', {**IDLE, 'requests': 1, 'modules': None}, pytest.approx(soft_window, abs=SETTLE_BOUND),
    ), 'the soft window must stop serial module imports like initial scripts, without a timeout or new error category'
