"""Check the finite compatibility surfaces required by frozen content cases."""

import hashlib
import importlib.util
import platform
import time

import pytest

from brewdoc import htmljs, htmlurl
from brewdoc.common import BrewdocError


native_supported = pytest.mark.skipif(
    importlib.util.find_spec('_quickjs') is None or platform.system() == 'Windows'
    or (platform.system() == 'Darwin' and platform.machine() != 'arm64'),
    reason='Optional native capture requires a proved platform ABI.',
)
IDLE = {'requests': 0, 'timers': 0, 'modules': 0, 'promises': 0, 'jobs': False}


@native_supported
def test_inline_style_access_retains_content_with_explicit_partial_status():
    # GIVEN content created after reading an inline style value.
    source = '<html><body><p id="result" style="display:block;color:red">pending</p><script>const s=getComputedStyle(document.querySelector("p"));document.querySelector("p").textContent=s.getPropertyValue("color")+":"+s.display</script></body></html>'
    # WHEN the finite accessor reads declarations without CSS or layout.
    captured = htmljs.render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN content survives and inline-only style behavior is explicit.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source.replace('>pending<', '>red:block<'), 'partial', [{'category': 'inline_style_only', 'resource_url': None}], IDLE,
    ), 'The accessor must read exact inline declarations and name the finite style surface as partial.'


@native_supported
def test_history_read_surface_has_one_ephemeral_entry_and_no_navigation():
    # GIVEN content depending on the initial history state.
    source = '<html><body><p>pending</p><script>document.querySelector("p").textContent=[history.length,history.state,typeof history.pushState,typeof history.go].map(String).join(":")</script></body></html>'
    # WHEN initial history is read without any navigation.
    captured = htmljs.render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN only the finite initial read surface exists.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source.replace('>pending<', '>1:null:undefined:undefined<'), 'settled', [], IDLE,
    ), 'History must expose one initial entry and no navigation methods.'


@native_supported
@pytest.mark.parametrize(
    ('target', 'expected'),
    [
        ('https://example.test:443/products?q=1#top', 'https:|example.test|example.test||/products|?q=1|#top'),
        ('../products?q=1#top', 'https:|fixture.test|fixture.test||/products|?q=1|#top'),
        ('https://[2606:4700:4700::1111]/products', 'https:|[2606:4700:4700::1111]|[2606:4700:4700::1111]||/products||'),
    ],
)
def test_anchor_url_components_are_data_only_and_resolve_against_document_base(target, expected):
    # GIVEN an anchor whose components are needed by an HTTP library.
    source = '<html><body><p>pending</p><script>const a=document.createElement("a");a.href=' + repr(target) + ';document.querySelector("p").textContent=[a.protocol,a.host,a.hostname,a.port,a.pathname,a.search,a.hash].join("|")</script></body></html>'
    requests = []
    # WHEN the URL components are read without following the anchor.
    # Soft window spans the deadline: a stray request is served and recorded, never hidden by start-up time.
    captured = htmljs.render(source, 'https://fixture.test/path/page', lambda request: requests.append(request), time.monotonic() + 10, soft_window=10)
    # THEN the finite adapter resolves components and performs no acquisition.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending'], requests) == (
        source.replace('>pending<', '>' + expected + '<'), 'settled', [], IDLE, [],
    ), 'Anchor components must describe the exact resolved HTTP(S) URL without requesting the target.'


@pytest.mark.parametrize('accept', ['*/*', 'application/json, text/plain, */*'])
def test_observed_accept_header_is_normalized_to_host_controlled_request(accept):
    # GIVEN one exact Accept constant observed in the frozen content libraries.
    requested = []

    def fetch(record):
        """Capture the sanitized request without performing network I/O."""
        requested.append(record)
        return {'status': 200, 'headers': (), 'body': b'content', 'final_url': record['url']}

    # WHEN the API request passes through the host option boundary.
    response = htmljs._host_response({'url': '/data', 'kind': 'api', 'headers': {'Accept': accept}}, fetch, 'https://fixture.test/', 'https://fixture.test/')
    # THEN arbitrary caller header data is absent from the acquisition request.
    assert response['body'] == 'content', 'The observed finite Accept value must permit a normal GET.'
    assert requested == [{'url': 'https://fixture.test/data', 'kind': 'api', 'origin': 'https://fixture.test', 'requested_with': False}], 'Only the host-controlled request surface may reach transport.'


def test_same_origin_xhr_marker_is_host_controlled():
    # GIVEN the exact same-origin jQuery request marker.
    requested = []

    def fetch(record):
        """Record the finite marker consumed by the host transport."""
        requested.append(record)
        return {'status': 200, 'headers': (), 'body': b'content', 'final_url': record['url']}

    # WHEN the host validates the marker on a same-origin API GET.
    response = htmljs._host_response({'url': '/data', 'kind': 'api', 'headers': {'Accept': '*/*', 'X-Requested-With': 'XMLHttpRequest'}}, fetch, 'https://fixture.test/', 'https://fixture.test/')
    # THEN the marker becomes a boolean rather than a caller header dictionary.
    assert response['body'] == 'content', 'The exact same-origin marker must permit this GET.'
    assert requested == [{'url': 'https://fixture.test/data', 'kind': 'api', 'origin': 'https://fixture.test', 'requested_with': True}], 'The host must supply the documented marker itself.'


@pytest.mark.parametrize('headers', [
    {'Authorization': 'secret'}, {'Cookie': 'secret'}, {'Accept': 'text/html'},
    {'X-Requested-With': 'forged'}, {'Accept': '*/*', 'accept': '*/*'},
])
def test_other_or_duplicate_headers_remain_unsupported(headers):
    # GIVEN caller headers outside the two observed constants and one marker.
    requested = []
    # WHEN finite request validation runs.
    response = htmljs._host_response({'url': '/data', 'kind': 'api', 'headers': headers}, lambda record: requested.append(record), 'https://fixture.test/', 'https://fixture.test/')
    # THEN unsupported headers are observable and no GET is attempted.
    assert response == {'error': 'request_headers_unsupported'}, 'Only exact reviewed header values may be normalized.'
    assert requested == [], 'Rejected header options must not reach transport.'


@pytest.mark.parametrize('kind', ['api', 'script', 'module'])
def test_cross_origin_or_non_api_xhr_marker_is_refused(kind):
    # GIVEN an XHR marker outside its same-origin API scope.
    requested = []
    # WHEN finite request validation runs.
    response = htmljs._host_response({'url': 'https://elsewhere.test/data', 'kind': kind, 'headers': {'X-Requested-With': 'XMLHttpRequest'}}, lambda record: requested.append(record), 'https://fixture.test/', 'https://fixture.test/')
    # THEN a custom cross-origin header cannot bypass preflight restrictions.
    assert response == {'error': 'request_headers_unsupported'}, 'The marker is unsupported outside same-origin API GET.'
    assert requested == [], 'Unsupported cross-origin custom headers must not request anything.'


def test_controlled_xhr_marker_is_stripped_on_every_cross_origin_redirect(monkeypatch):
    # GIVEN a same-origin API redirecting across another public origin and back.
    requested = []
    responses = {
        'https://fixture.test/start': {'status': 302, 'headers': (('Location', 'https://elsewhere.test/data'),), 'entity': b'', 'body': b''},
        'https://elsewhere.test/data': {'status': 302, 'headers': (('Location', 'https://fixture.test/final'),), 'entity': b'', 'body': b''},
        'https://fixture.test/final': {'status': 200, 'headers': (('Content-Type', 'application/json'),), 'entity': b'{}', 'body': b'{}'},
    }

    def request(url, deadline, accept, origin, requested_with=False):
        """Observe each controlled transport option without opening a socket."""
        requested.append((url, requested_with, origin, accept))
        return dict(responses[url])

    monkeypatch.setattr(htmlurl, '_request', request)
    # WHEN the shared redirect loop processes the finite XHR marker.
    response = htmlurl._fetch('https://fixture.test/start', kind='api', origin='https://fixture.test', requested_with=True)
    # THEN the marker is absent on every cross-origin hop.
    assert requested == [
        ('https://fixture.test/start', True, 'https://fixture.test', htmlurl._ACCEPT['api']),
        ('https://elsewhere.test/data', False, 'https://fixture.test', htmlurl._ACCEPT['api']),
        ('https://fixture.test/final', True, 'https://fixture.test', htmlurl._ACCEPT['api']),
    ], 'Only actual same-origin hops may receive the controlled XHR marker.'
    assert response['final_url'] == 'https://fixture.test/final', 'The existing redirect acquisition contract must remain intact.'


@pytest.mark.parametrize('origin,expected', [('https://fixture.test', {'X-Requested-With': 'XMLHttpRequest'}), ('https://elsewhere.test', {'Origin': 'https://elsewhere.test'})])
def test_transport_emits_only_exact_host_controlled_xhr_marker(monkeypatch, origin, expected):
    from test_html_url import Connection, Response

    # GIVEN synthetic response/socket edges and the controlled marker option.
    response = Response(b'{}', (('Content-Type', 'application/json'),))
    response.status = 200
    connection = Connection(response)
    monkeypatch.setattr(htmlurl, '_resolve', lambda *args: ['93.184.216.34'])
    monkeypatch.setattr(htmlurl, '_connect', lambda *args: connection)
    # WHEN the real request builder produces one guarded GET.
    captured = htmlurl._request('https://fixture.test/data', time.monotonic() + 10, '*/*', origin, requested_with=True)
    # THEN the marker exists only for the actual matching origin.
    assert connection.requests == [('GET', '/data', {
        'Host': 'fixture.test', 'User-Agent': 'brewdoc URL acquisition', 'Accept': '*/*',
        'Accept-Encoding': 'gzip', 'Connection': 'close', **expected,
    })], 'The transport must emit only fixed controlled header values.'
    assert captured['body'] == b'{}', 'The existing bounded response body contract must remain intact.'


@native_supported
@pytest.mark.parametrize('target', ['javascript:alert(1)', 'https://user:secret@example.test/', 'https://éxample.test/'])
def test_anchor_adapter_reports_unsupported_url_forms_without_acquisition(target):
    # GIVEN forms outside the bounded ASCII HTTP(S) adapter.
    source = '<html><body><p>pending</p><script>const a=document.createElement("a");a.setAttribute("href",' + repr(target) + ');document.querySelector("p").textContent=a.pathname</script></body></html>'
    requested = []
    # WHEN the unsupported component getter is used.
    # Soft window spans the deadline: a stray request is served and recorded, never hidden by start-up time.
    captured = htmljs.render(source, 'https://fixture.test/', lambda record: requested.append(record), time.monotonic() + 10, soft_window=10)
    # THEN the failure is partial and never becomes a network request.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending'], requested) == (
        source, 'partial', [{'category': 'script_execution_failed', 'resource_url': None}], IDLE, [],
    ), 'Unsupported URL forms must fail observably without inventing browser URL behavior or acquisition.'


@native_supported
def test_xhr_response_header_callback_observes_only_host_exposed_fields():
    # GIVEN an XHR whose completion reads headers before inserting content.
    source = '<html><body><p>pending</p><script>const xhr=new XMLHttpRequest();xhr.onload=()=>{document.querySelector("p").textContent=xhr.responseText+":"+xhr.getAllResponseHeaders()};xhr.open("GET","/data");xhr.send()</script></body></html>'

    def fetch(record):
        """Serve body and mixed response headers at the bounded host edge."""
        return {'status': 200, 'final_url': record['url'], 'body': b'article',
                'headers': (('Content-Type', 'text/plain'), ('Set-Cookie', 'secret=1'), ('Set-Cookie2', 'secret=2'))}

    # WHEN the native callback reads the already filtered response record.
    # Soft window spans the whole deadline: only engine settle ends capture, never child start-up time.
    captured = htmljs.render(source, 'https://fixture.test/', fetch, time.monotonic() + 10, soft_window=10)
    # THEN the missing-header-method failure is fixed without exposing cookies.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source.replace('<p>pending</p>', '<p>article:content-type: text/plain\r\n</p>'), 'settled', [],
        IDLE,
    ), 'XHR completion must drain and expose only the finite filtered header record.'


def test_cookie_response_headers_remain_forbidden_even_when_exposure_is_requested():
    # GIVEN a cross-origin response explicitly naming forbidden cookie fields.
    def fetch(record):
        """Supply CORS-granted response fields without real network I/O."""
        return {'status': 200, 'final_url': record['url'], 'body': b'article', 'headers': (
            ('Access-Control-Allow-Origin', '*'), ('Access-Control-Expose-Headers', 'set-cookie, set-cookie2'),
            ('Content-Type', 'text/plain'), ('Set-Cookie', 'secret=1'), ('Set-Cookie2', 'secret=2'))}

    # WHEN the parent computes the child-visible response fields.
    response = htmljs._host_response({'url': 'https://elsewhere.test/data'}, fetch, 'https://fixture.test/', 'https://fixture.test/')
    # THEN no cookie-setting header crosses the host boundary.
    assert response['headers'] == {'content-type': 'text/plain'}, 'CORS exposure must never disclose cookie-setting fields.'


JAVASCRIPT_ESSENCES = [
    'application/ecmascript', 'application/javascript', 'application/x-ecmascript', 'application/x-javascript',
    'text/ecmascript', 'text/javascript', 'text/javascript1.0', 'text/javascript1.1', 'text/javascript1.2',
    'text/javascript1.3', 'text/javascript1.4', 'text/javascript1.5', 'text/jscript', 'text/livescript',
    'text/x-ecmascript', 'text/x-javascript',
]
SCRIPT_URL = 'https://fixture.test/app.js'
SCRIPT_BODY = b'window.loaded=1'


def _serve_script(monkeypatch, media):
    """Answer one script request with the given Content-Type below real media validation."""
    monkeypatch.setattr(htmlurl, '_request', lambda *args: {
        'status': 200, 'headers': (('Content-Type', media),), 'entity': SCRIPT_BODY, 'body': SCRIPT_BODY})


@pytest.mark.parametrize('kind', ['script', 'module'])
@pytest.mark.parametrize('media', JAVASCRIPT_ESSENCES)
def test_script_fetch_accepts_every_javascript_mime_essence(monkeypatch, media, kind):
    # GIVEN a script response labelled with one WHATWG JavaScript MIME type essence.
    _serve_script(monkeypatch, media + '; charset=utf-8')
    # WHEN the shared acquisition validates script media.
    response = htmlurl._fetch(SCRIPT_URL, kind=kind)
    # THEN the script bytes are accepted unchanged with the bounded header record.
    assert response == {
        'status': 200, 'headers': (('Content-Type', media + '; charset=utf-8'), ('Content-Length', '15')),
        'entity': SCRIPT_BODY, 'body': SCRIPT_BODY, 'requested_url': SCRIPT_URL, 'final_url': SCRIPT_URL,
        'entity_sha256': hashlib.sha256(SCRIPT_BODY).hexdigest(), 'body_sha256': hashlib.sha256(SCRIPT_BODY).hexdigest(),
        'redirects': (),
    }, 'every JavaScript MIME essence must be accepted for classic and module scripts like a browser'


@pytest.mark.parametrize('kind', ['script', 'module'])
@pytest.mark.parametrize('media', ['text/plain', 'application/json', 'text/html', 'application/octet-stream',
                                   'text/javascript1.6', 'text/vbscript', 'application/x-javascript-module'])
def test_script_fetch_refuses_media_outside_the_javascript_essences(monkeypatch, media, kind):
    # GIVEN a script response labelled with a non-JavaScript media type.
    _serve_script(monkeypatch, media)
    # WHEN the shared acquisition validates script media.
    with pytest.raises(BrewdocError) as refused:
        htmlurl._fetch(SCRIPT_URL, kind=kind)
    # THEN the script is refused at the media stage with its URL.
    assert (type(refused.value), str(refused.value), refused.value.acquisition_stage, refused.value.resource_url) == (
        BrewdocError, 'URL %s content type refused' % kind, 'media', SCRIPT_URL,
    ), 'media outside the JavaScript essences must stay refused for scripts'


@native_supported
@pytest.mark.parametrize('media', JAVASCRIPT_ESSENCES + ['TEXT/JavaScript'])
def test_script_element_with_javascript_mime_type_runs(media):
    # GIVEN an inline script whose type attribute is a JavaScript MIME type essence.
    source = ('<html><body><p id="result">pending</p><script type="' + media + '">'
              'document.querySelector("#result").textContent="ran"</script></body></html>')
    # WHEN capture discovers and classifies the script element.
    captured = htmljs.render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=10)
    # THEN the script runs and the capture settles.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source.replace('>pending<', '>ran<'), 'settled', [],
        IDLE,
    ), 'a script type matching a JavaScript MIME essence case-insensitively must run like a browser'


@native_supported
@pytest.mark.parametrize('media', ['text/plain', 'application/json', 'text/template', 'text/javascript1.6',
                                   'text/vbscript'])
def test_script_element_with_non_javascript_type_stays_inert(media):
    # GIVEN an inline script whose type attribute names a non-JavaScript data block.
    source = ('<html><body><p id="result">pending</p><script type="' + media + '">'
              'document.querySelector("#result").textContent="ran"</script></body></html>')
    # WHEN capture discovers and classifies the script element.
    captured = htmljs.render(source, 'https://fixture.test/', lambda request: None, time.monotonic() + 10, soft_window=10)
    # THEN the data block never runs and the capture settles unchanged.
    assert (captured['html'], captured['capture_status'], captured['errors'], captured['pending']) == (
        source, 'settled', [], IDLE,
    ), 'a non-JavaScript script type must stay an inert data block'
