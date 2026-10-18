"""Check JS capture settle, per-request soft window and ancillary sub-request failures."""

import importlib.metadata
import platform
import re
import ssl
import sys
import threading
import time

import pytest

from brewdoc import htmlurl
from brewdoc.common import BrewdocError
from brewdoc.htmljs import render
from test_html_timeout import CHAPTER, URL, _html_response

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
def test_failed_sub_request_transport_keeps_the_capture(lookup, connect, monkeypatch):
    # GIVEN acquired main HTML and a real sub-request fetch whose transport fails.
    body = (b"<html><body><p>Initial article</p><script>fetch('/data?token=secret').then(r=>r.text())"
            b".then(t=>document.querySelector('p').textContent=t)"
            b".catch(()=>document.querySelector('p').textContent='Fallback article')</script></body></html>")
    original = htmlurl._fetch

    def fetch(url, *, kind='html', **options):
        """Serve the main page locally and send the sub-request through real transport."""
        return {'html': lambda: _html_response(body, **options),
                'api': lambda: original(url, kind=kind, **options)}[kind]()

    monkeypatch.setattr(htmlurl, '_fetch', fetch)
    monkeypatch.setattr(htmlurl, '_dns_lookup', lookup)
    monkeypatch.setattr(htmlurl, '_connect', connect)
    # WHEN JavaScript capture runs with the failing ancillary request.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=10)
    acquisition = receipt['acquisition']
    # THEN the page converts and the receipt names the redacted failed resource.
    assert (code, markdown.partition(CHAPTER)[2], acquisition['capture_status'], acquisition['refusal_stage'],
            acquisition['errors'], acquisition['error_count'], acquisition['pending'], acquisition['counts']) == (
        0, 'Fallback article\n', 'partial', None,
        [{'category': 'ancillary_transport_failed', 'resource_url': FAILED}], 1, IDLE,
        {'requests': 2, 'promise_jobs': 3, 'timer_callbacks': 0},
    ), 'a TLS, DNS or connect failure of one sub-request must not refuse the whole capture'


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
])
def test_invalid_sub_request_url_keeps_the_url_capture(monkeypatch, target):
    # GIVEN acquired main HTML whose one sub-request URL is unparseable or over the length limit, caught by the page.
    body = ("<html><body><p>Initial article</p><script>fetch(" + target + ").then(r=>r.text())"
            ".then(t=>document.querySelector('p').textContent=t)"
            ".catch(()=>document.querySelector('p').textContent='Fallback article')</script></body></html>").encode()
    kinds = []

    def fetch(url, *, kind='html', **options):
        """Serve the main page locally and record every acquisition kind."""
        kinds.append(kind)
        return _html_response(body, **options)

    monkeypatch.setattr(htmlurl, '_fetch', fetch)
    # WHEN JavaScript capture runs through the URL boundary.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=10)
    acquisition = receipt['acquisition']
    # THEN the page converts with its fallback and the receipt records one unnamed ancillary failure.
    assert (code, kinds, markdown.partition(CHAPTER)[2], acquisition['capture_status'], acquisition['refusal_stage'],
            acquisition['errors'], acquisition['error_count'], acquisition['pending'], acquisition['counts']) == (
        0, ['html'], 'Fallback article\n', 'partial', None,
        [{'category': 'ancillary_transport_failed', 'resource_url': None}], 1, IDLE,
        {'requests': 1, 'promise_jobs': 3, 'timer_callbacks': 0},
    ), 'an invalid sub-request URL must not refuse the capture at javascript; main-document refusals stay hard'


@pytest.mark.parametrize('base', [pytest.param('http://[', id='unparseable'),
                                  pytest.param('http://a b/', id='syntax-invalid')])
def test_invalid_document_base_is_ignored_by_direct_js_render(base):
    # GIVEN a page whose base cannot be parsed and whose script resolves a relative anchor.
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
def test_invalid_document_base_keeps_the_js_url_capture(monkeypatch, base):
    # GIVEN acquired main HTML with an invalid base, a relative link and a script resolving a relative anchor.
    body = ('<html><head><base href="' + base + '"></head><body><p id="result">pending</p>'
            '<p>See <a href="guide?q=1">guide</a></p><script>const a=document.createElement("a");a.href="guide";'
            'document.querySelector("#result").textContent=a.href</script></body></html>').encode()
    monkeypatch.setattr(htmlurl, '_fetch', lambda url, *, kind='html', **options: _html_response(body, **options))
    # WHEN JavaScript capture runs through the URL boundary.
    code, receipt, markdown = htmlurl._run_url(URL, render_js=True, timeout=10)
    acquisition = receipt['acquisition']
    # THEN the capture settles and links resolve against the document URL.
    assert (code, markdown.partition(CHAPTER)[2], acquisition['capture_status'], acquisition['refusal_stage'],
            acquisition['errors'], acquisition['effective_base_url']) == (
        0, 'https://fixture.test/guide\n\nSee [guide](https://fixture.test/guide?q=1)\n', 'settled', None, [], URL,
    ), 'an invalid <base href> must be ignored in JS capture, not refuse it at javascript'


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
