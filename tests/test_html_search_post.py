"""Check the finite public-search permission at the real parent and HTTP boundaries."""

import hashlib
import http.client
import io
import json
import socket
import threading
import time
from urllib.parse import urlencode

import pytest

from brewdoc import htmljs, htmlurl
from test_html_timeout import _html_response


DOCUMENT = "https://hn.algolia.com/"
PUBLIC_KEY = "0123456789abcdef0123456789abcdef"
SEARCH = "https://uj5wyc0l7x-dsn.algolia.net/1/indexes/Item_dev/query?" + urlencode({
    "x-algolia-agent": "Synthetic search witness", "x-algolia-application-id": "UJ5WYC0L7X",
    "x-algolia-api-key": PUBLIC_KEY,
})
FORM = "application/x-www-form-urlencoded"
BODY = '''{ "query":"питон 🙂", "analyticsTags":[], "page":0, "hitsPerPage":1,
"minWordSizefor1Typo":4,"minWordSizefor2Typos":8,"advancedSyntax":true,
"ignorePlurals":false,"clickAnalytics":false,"minProximity":7,"numericFilters":[],
"tagFilters":[["story"]],"typoTolerance":true,"queryType":"prefixNone",
"restrictSearchableAttributes":["title"],"getRankingInfo":false }'''


@pytest.fixture
def trusted_search_key(monkeypatch):
    """Pin synthetic public input without replacing any real profile validator."""
    monkeypatch.setattr(htmlurl, "_SEARCH_KEY_SHA256", hashlib.sha256(PUBLIC_KEY.encode()).hexdigest(), raising=False)


def search_record(**changes):
    """Return a synthetic XHR record with original primitive body and explicit types."""
    return {"url": SEARCH, "kind": "api", "method": "POST", "body_present": True,
            "body": BODY, "xhr": True, "with_credentials": False, "headers_conflict": False,
            "headers": {"content-type": FORM}, "mode": "cors", "credentials": "same-origin",
            "redirect": "follow", **changes}


def api_response(url=SEARCH, body=b"synthetic reply", headers=()):
    """Supply unit response bytes unrelated to retained GET content oracles."""
    return {"status": 200, "headers": (("Access-Control-Allow-Origin", "*"), *headers),
            "body": body, "entity": body, "final_url": url}


def search_body(**changes):
    """Create synthetic schema cases separately from retained original sender bodies."""
    return json.dumps({**json.loads(BODY), **changes}, ensure_ascii=False, separators=(",", ":"))


def _serve_http(server, received, reply):
    """Read one real HTTP frame, retaining EOF cleanup when the request is refused."""
    with server, server.makefile("rb") as incoming:
        line = incoming.readline()
        if not line:
            return
        headers = []
        for header in incoming:
            if header == b"\r\n":
                break
            headers.append(header)
        length = int(next(header.split(b":", 1)[1] for header in headers
                          if header.lower().startswith(b"content-length:")))
        received.append((line, headers, incoming.read(length)))
        server.sendall(b"HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: 15\r\n\r\n" + reply)


def test_post_redirect_is_hard_before_reading_a_truncated_entity(monkeypatch):
    # GIVEN real HTTP parsing has accepted redirect headers with one missing body byte.
    from test_html_url import Socket

    stream = io.BytesIO(b"HTTP/1.1 303 See Other\r\nLocation: https://fixture.test/follow\r\nContent-Length: 1\r\n\r\n")
    socket_stub = Socket()
    monkeypatch.setattr(socket_stub, "makefile", lambda mode: stream, raising=False)
    response = http.client.HTTPResponse(socket_stub)
    response.begin()
    requests, reads, timers = [], [], []
    original_read, original_timer = response.read, threading.Timer
    assert (response.status, response.length, stream.read()) == (303, 1, b""), "the real parser must know the redirect before encountering EOF"

    class RedirectConnection:
        """Retain one HTTP emission and socket ownership without real network I/O."""

        sock = socket_stub

        def request(self, method, target, *, body=None, headers=None):
            requests.append((method, target, body))

        def getresponse(self):
            return response

        def close(self):
            response.close()
            self.sock.close()

    def read(size):
        """Record any entity read through the real HTTP response implementation."""
        reads.append(size)
        return original_read(size)

    def timer(*arguments):
        """Retain the actual owned watchdog so cleanup can be checked after refusal."""
        owned = original_timer(*arguments)
        timers.append(owned)
        return owned

    monkeypatch.setattr(response, "read", read)
    monkeypatch.setattr(htmlurl, "_resolve", lambda *arguments: ["93.184.216.34"])
    monkeypatch.setattr(htmlurl, "_connect", lambda *arguments: RedirectConnection())
    monkeypatch.setattr(htmlurl.threading, "Timer", timer)
    # WHEN the guarded HTTP boundary receives a POST redirect with a truncated entity.
    with pytest.raises(htmlurl.URLPolicyError) as refused:
        htmlurl._request("https://fixture.test/search", time.monotonic() + 10,
                         "*/*", search_body=b"original query")
    # THEN known redirect policy wins without body reads, replay or leaked ownership.
    assert type(refused.value) is htmlurl.URLPolicyError, "a body-framing fault must not replace the known hard POST redirect refusal"
    assert (requests, reads, socket_stub.closed, [owned.is_alive() for owned in timers]) == (
        [("POST", "/search", b"original query")], [], True, [False],
    ), "a refused POST redirect must never read its entity or follow its Location and must join its watchdog"


@pytest.mark.parametrize("changes,document", [
    pytest.param({}, "https://elsewhere.test/", id="wrong-document-origin"),
    pytest.param({"url": SEARCH.replace("https:", "http:")}, DOCUMENT, id="plaintext-api"),
    pytest.param({"url": SEARCH.replace("uj5wyc0l7x-dsn", "other-dsn")}, DOCUMENT, id="other-tenant"),
    pytest.param({"url": SEARCH.replace("Item_dev", "Other")}, DOCUMENT, id="other-index"),
    pytest.param({"url": SEARCH.replace("Item_dev", "%49tem_dev")}, DOCUMENT, id="encoded-endpoint-alias"),
    pytest.param({"url": SEARCH.replace("/query?", "/query/?")}, DOCUMENT, id="trailing-path-alias"),
    pytest.param({"url": SEARCH.replace("UJ5WYC0L7X", "OTHER")}, DOCUMENT, id="other-app"),
    pytest.param({"url": SEARCH.replace(PUBLIC_KEY, "wrong-public-key")}, DOCUMENT, id="other-public-key"),
    pytest.param({"url": SEARCH + "&extra=1"}, DOCUMENT, id="extra-query-key"),
    pytest.param({"url": SEARCH + "&x-algolia-api-key=" + PUBLIC_KEY}, DOCUMENT, id="duplicate-query-key"),
    pytest.param({"url": SEARCH.replace("Synthetic+search+witness", "x" * 513)}, DOCUMENT, id="agent-byte-overflow"),
    pytest.param({"url": SEARCH.replace("Synthetic+search+witness", "%0A")}, DOCUMENT, id="nonprintable-agent"),
    pytest.param({"xhr": False}, DOCUMENT, id="fetch-is-not-xhr"),
    pytest.param({"kind": "script"}, DOCUMENT, id="script-is-not-search-api"),
    pytest.param({"with_credentials": True}, DOCUMENT, id="credentialed-xhr"),
    pytest.param({"credentials": "include"}, DOCUMENT, id="credential-mode-include"),
    pytest.param({"mode": "no-cors"}, DOCUMENT, id="opaque-mode"),
    pytest.param({"redirect": "manual"}, DOCUMENT, id="unsupported-input-redirect-mode"),
])
def test_other_search_profiles_and_options_make_zero_acquisition_calls(changes, document, trusted_search_key):
    # GIVEN well-formed inputs outside the one approved public-search permission.
    requested = []
    assert requested == [], "no request may precede trusted profile validation"
    # WHEN the existing parent boundary independently validates the claimed XHR.
    response = htmljs._host_response(search_record(**changes), requested.append, document, DOCUMENT)
    # THEN unsupported options preserve a partial-capable result without transport.
    assert (response, requested) == ({"error": "request_options_unsupported"}, []), "unsupported POST profiles must not acquire anything"


@pytest.mark.parametrize("url", [
    pytest.param(SEARCH.replace(".net/", ".net:444/"), id="globally-forbidden-port"),
    pytest.param(SEARCH.replace("https:", "ftp:"), id="globally-forbidden-scheme"),
    pytest.param(SEARCH.replace("https://", "https://synthetic-user@"), id="globally-forbidden-userinfo"),
])
def test_search_does_not_soften_the_existing_global_url_policy(url, trusted_search_key):
    # GIVEN URL syntax/port/credentials already prohibited for every acquisition mode.
    requested = []
    # WHEN the parent applies the unchanged global URL policy before POST permission.
    with pytest.raises(htmlurl.URLPolicyError):
        htmljs._host_response(search_record(url=url), requested.append, DOCUMENT, DOCUMENT)
    # THEN search permission cannot convert a global security refusal into partial input.
    assert requested == [], "globally forbidden POST destinations must retain hard refusal before acquisition"


@pytest.mark.parametrize("headers,conflict", [
    pytest.param({"content-type": "application/json"}, False, id="wrong-content-type"),
    pytest.param({"content-type": FORM, "Authorization": "synthetic"}, False, id="authorization"),
    pytest.param({"content-type": FORM, "Cookie": "synthetic"}, False, id="cookie"),
    pytest.param({"content-type": FORM, "X-Custom": "synthetic"}, False, id="custom-header"),
    pytest.param({"content-type": FORM, "Content-Type": FORM}, False, id="case-colliding-form-header"),
    pytest.param({"content-type": FORM}, True, id="repeated-same-name-header"),
    pytest.param({"content-type": FORM, "Accept": "text/html"}, False, id="uncontrolled-accept"),
])
def test_search_headers_remain_a_finite_zero_wire_surface(headers, conflict, trusted_search_key):
    # GIVEN a forbidden header or a retained duplicate-header conflict.
    requested = []
    # WHEN the parent sees headers before any normalization can erase duplicates.
    response = htmljs._host_response(search_record(headers=headers, headers_conflict=conflict), requested.append, DOCUMENT, DOCUMENT)
    # THEN caller auth/custom headers never reach transport or consume a request.
    assert (response, requested) == ({"error": "request_headers_unsupported"}, []), "POST header permission must remain finite and credential-free"


@pytest.mark.parametrize("changes", [
    pytest.param({"body": {}}, id="object-body"),
    pytest.param({"body": None}, id="null-body"),
    pytest.param({"body_present": False}, id="body-presence-contradiction"),
    pytest.param({"body_present": 1}, id="integer-body-presence"),
    pytest.param({"xhr": 1}, id="integer-xhr-marker"),
    pytest.param({"xhr": None}, id="null-xhr-marker"),
    pytest.param({"with_credentials": 0}, id="integer-credentials"),
    pytest.param({"with_credentials": None}, id="null-credentials"),
    pytest.param({"with_credentials": "false"}, id="string-credentials"),
    pytest.param({"headers_conflict": 0}, id="integer-header-conflict"),
    pytest.param({"headers": []}, id="list-header-container"),
    pytest.param({"headers": None}, id="null-header-container"),
    pytest.param({"headers": 0}, id="scalar-header-container"),
])
def test_forged_search_ipc_primitive_shapes_refuse_hard_before_acquisition(changes, trusted_search_key):
    # GIVEN a malformed private record rather than a well-formed page option.
    requested = []
    # WHEN the parent receives the forged primitive or inconsistent body shape.
    with pytest.raises(htmlurl.URLPolicyError) as refused:
        htmljs._host_response(search_record(**changes), requested.append, DOCUMENT, DOCUMENT)
    # THEN protocol corruption cannot become a partial unsupported-option success.
    assert (type(refused.value), requested) == (htmlurl.URLPolicyError, []), "private IPC corruption must be exact policy refusal with zero acquisition"


@pytest.mark.parametrize("body", [
    pytest.param(search_body(page=True), id="boolean-is-not-integer"),
    pytest.param(search_body(hitsPerPage=0), id="zero-hit-count"),
    pytest.param(search_body(page=-1), id="negative-page"),
    pytest.param(search_body(page=0.5), id="fractional-page"),
    pytest.param(search_body(advancedSyntax=1), id="integer-is-not-boolean"),
    pytest.param(search_body(query=0), id="query-is-not-string"),
    pytest.param(search_body(queryType="prefixAll"), id="unapproved-query-type"),
    pytest.param(search_body(typoTolerance="anything"), id="unapproved-typo-tolerance"),
    pytest.param(search_body(analyticsTags=[1]), id="nonstring-array-member"),
    pytest.param(search_body(unapproved="field"), id="extra-field"),
    pytest.param(BODY.replace('"query":', '"query":"duplicate","query":', 1), id="duplicate-json-key"),
    pytest.param(BODY.replace('"page":0', '"page":NaN'), id="nonjson-number"),
    pytest.param(BODY.replace('"query":"питон 🙂"', '"query":"\ud800"'), id="strict-utf8-surrogate"),
])
def test_well_formed_unsupported_search_schema_never_rewrites_or_sends_a_body(body, trusted_search_key):
    # GIVEN an original primitive body outside the finite supported search schema.
    requested = []
    # WHEN the parent validates without coercing fields or replacing Unicode.
    response = htmljs._host_response(search_record(body=body), requested.append, DOCUMENT, DOCUMENT)
    # THEN invalid schema/encoding is recoverable without request-body transport.
    assert (response, requested) == ({"error": "request_options_unsupported"}, []), "unsupported original body data must stay unchanged and unsent"


@pytest.mark.parametrize("changes", [
    pytest.param({"tagFilters": [[["story"]]]}, id="inclusive-depth-four"),
    pytest.param({"analyticsTags": ["x"] * 253}, id="inclusive-256-total-array-entries"),
    pytest.param({"typoTolerance": "min", "queryType": "prefixLast"}, id="source-enumerated-values"),
    pytest.param({"typoTolerance": "strict"}, id="source-strict-typo-form"),
])
def test_inclusive_search_schema_edges_preserve_the_original_body(changes, trusted_search_key):
    # GIVEN supported nested depth, aggregate array count or enumerated values.
    body = search_body(**changes)
    requested = []

    def fetch(record):
        """Record admitted primitive body identity without issuing a wire request."""
        requested.append(record)
        return api_response()

    # WHEN exact supported limits pass the real parent validator.
    response = htmljs._host_response(search_record(body=body), fetch, DOCUMENT, DOCUMENT)
    # THEN validation does not reserialize or replace the original string.
    assert (response["body"], requested) == ("synthetic reply", [
        {"url": SEARCH, "kind": "api", "origin": "https://hn.algolia.com", "requested_with": False,
         "method": "POST", "body": body, "content_type": FORM},
    ]), "inclusive schema edges must reach acquisition with byte-identical page input"


@pytest.mark.parametrize("changes", [
    pytest.param({"tagFilters": [[[["story"]]]]}, id="depth-five-overflow"),
    pytest.param({"analyticsTags": ["x"] * 254}, id="257-total-array-entries"),
])
def test_search_container_limits_are_hard_before_transport(changes, trusted_search_key):
    # GIVEN one-unit excess beyond a finite depth or total-array-entry guard.
    requested = []
    # WHEN the parent inspects the original search string.
    with pytest.raises(htmlurl.URLResourceError):
        htmljs._host_response(search_record(body=search_body(**changes)), requested.append, DOCUMENT, DOCUMENT)
    # THEN a resource excess cannot be softened into ordinary unsupported options.
    assert requested == [], "search depth/array overflow must refuse hard before acquisition"


def sized_body(size):
    """Build exact byte witnesses using ASCII padding in a supported query string."""
    empty = search_body(query="")
    return search_body(query="x" * (size - len(empty.encode("utf-8"))))


def test_original_utf8_body_limit_admits_exactly_16384_bytes(trusted_search_key):
    # GIVEN an original primitive body at the exact inclusive UTF-8 byte bound.
    body = sized_body(16384)
    requested = []
    assert len(body.encode("utf-8")) == 16384, "the body-size precondition must measure original UTF-8 bytes"

    def fetch(record):
        """Record inclusive original body admission without a network request."""
        requested.append(record)
        return api_response()

    # WHEN the real parent checks the inclusive search body boundary.
    response = htmljs._host_response(search_record(body=body), fetch, DOCUMENT, DOCUMENT)
    # THEN all original bytes are admitted without body normalization.
    assert (response["body"], [record["body"] for record in requested]) == ("synthetic reply", [body]), "inclusive bytes must pass without normalization"


def test_original_utf8_body_limit_refuses_the_16385th_byte_hard(trusted_search_key):
    # GIVEN one original byte beyond the inclusive public-search body bound.
    body = sized_body(16385)
    requested = []
    assert len(body.encode("utf-8")) == 16385, "overflow must be exactly one original UTF-8 byte"
    # WHEN the parent checks its resource bound before acquisition.
    with pytest.raises(htmlurl.URLResourceError):
        htmljs._host_response(search_record(body=body), requested.append, DOCUMENT, DOCUMENT)
    # THEN a caught body error cannot make the excess an eligible partial request.
    assert requested == [], "one-byte body overflow must remain unsent"


def test_outgoing_body_and_response_share_inclusive_aggregate_and_last_request_budget(trusted_search_key, monkeypatch):
    # GIVEN the last request and enough shared bytes for exactly one body plus reply.
    reply = b"reply"
    original = BODY.encode("utf-8")
    budget = {"accepted_requests": 99, "decoded_bytes": 32 * 1048576 - len(original) - len(reply)}
    calls = []

    def request(url, deadline, accept, origin, **options):
        """Record the actual admitted outgoing payload before a bounded unit reply."""
        calls.append((url, origin, options))
        return {"status": 200, "headers": (), "entity": reply, "body": reply}

    monkeypatch.setattr(htmlurl, "_request", request)
    # WHEN the guarded fetch accounts for the original UTF-8 body and incoming bytes.
    response = htmlurl._fetch(SEARCH, deadline=time.monotonic() + 10, kind="api", origin="https://hn.algolia.com",
                              budget=budget, search_body=BODY)
    # THEN request 100 and 32 MiB are inclusive and outgoing bytes are charged once.
    assert (response["body"], budget, calls) == (
        reply, {"accepted_requests": 100, "decoded_bytes": 32 * 1048576},
        [(SEARCH, "https://hn.algolia.com", {"search_body": original})],
    ), "admitted outgoing and incoming bytes must share the existing aggregate rather than duplicate body charge"


@pytest.mark.parametrize("budget,reply,expected_calls", [
    pytest.param({"accepted_requests": 100, "decoded_bytes": 0}, b"", 0, id="request-101-refused"),
    pytest.param({"accepted_requests": 0, "decoded_bytes": 32 * 1048576 - len(BODY.encode()) + 1}, b"", 0, id="outgoing-aggregate-overflow"),
    pytest.param({"accepted_requests": 0, "decoded_bytes": 32 * 1048576 - len(BODY.encode())}, b"x", 1, id="incoming-aggregate-overflow"),
])
def test_search_shared_budget_overflow_refuses_before_or_after_the_owned_wire_stage(budget, reply, expected_calls, trusted_search_key, monkeypatch):
    # GIVEN a finite shared request/body/response budget with a one-unit excess.
    calls = []

    def request(*arguments, **options):
        """Retain the exact stage reached before shared-quota refusal."""
        calls.append(options)
        return {"status": 200, "headers": (), "entity": reply, "body": reply}

    monkeypatch.setattr(htmlurl, "_request", request)
    # WHEN the guarded POST consumes its corresponding outgoing or incoming stage.
    with pytest.raises(htmlurl.URLResourceError):
        htmlurl._fetch(SEARCH, deadline=time.monotonic() + 10, kind="api", origin="https://hn.algolia.com",
                      budget=dict(budget), search_body=BODY)
    # THEN hard quota failure keeps the exact known number of transport attempts.
    assert len(calls) == expected_calls, "resource overflow must not invent zero-wire status after a received response"


@pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 305, 307, 308])
def test_every_post_redirect_status_is_refused_after_one_send_without_body_replay(status, trusted_search_key, monkeypatch):
    # GIVEN an admitted POST receiving a redirect status and an otherwise public target.
    calls = []

    def request(url, deadline, accept, origin, **options):
        """Return one controlled redirect while counting actual body submissions."""
        calls.append((url, options))
        return {"status": status, "headers": (("Location", "https://example.test/target"),), "entity": b"", "body": b""}

    monkeypatch.setattr(htmlurl, "_request", request)
    # WHEN the existing guarded fetch sees a POST 3xx rather than an eligible GET hop.
    with pytest.raises(htmlurl.URLPolicyError):
        htmlurl._fetch(SEARCH, deadline=time.monotonic() + 10, kind="api", origin="https://hn.algolia.com", search_body=BODY)
    # THEN the payload is neither replayed nor rewritten into a redirected GET.
    assert calls == [(SEARCH, {"search_body": BODY.encode("utf-8")})], "all POST 3xx must stop after one admitted request"


@pytest.mark.parametrize("headers", [
    pytest.param((), id="missing-acao"),
    pytest.param((("Access-Control-Allow-Origin", "https://other.test"),), id="wrong-acao"),
    pytest.param((("Access-Control-Allow-Origin", "*"), ("access-control-allow-origin", "*")), id="ambiguous-acao"),
])
def test_cors_denies_post_response_exposure_after_one_approved_transport(headers, trusted_search_key):
    # GIVEN an admitted POST with a response lacking unambiguous credential-free CORS.
    requested = []

    def fetch(record):
        """Record admission independently from later CORS response denial."""
        requested.append(record)
        return {"status": 200, "headers": headers, "body": b"private response", "final_url": SEARCH}

    # WHEN parent CORS checks the actual admitted response.
    response = htmljs._host_response(search_record(), fetch, DOCUMENT, DOCUMENT)
    # THEN only body exposure is refused, without a false zero-wire claim.
    assert (response, len(requested)) == ({"error": "cors_denied"}, 1), "post-send CORS denial must hide bytes while retaining its admitted request"


def test_bodyless_get_record_and_host_controlled_headers_keep_their_existing_contract():
    # GIVEN an ordinary API GET unrelated to public search permission.
    requested = []

    def fetch(record):
        """Serve the original bodyless GET contract without POST-specific fields."""
        requested.append(record)
        return api_response(record["url"])

    # WHEN the original GET parent boundary runs without any new POST fields.
    response = htmljs._host_response({"url": "/data", "kind": "api"}, fetch, DOCUMENT, DOCUMENT)
    # THEN old bodyless input and sanitized output remain exactly compatible.
    assert (response, requested) == (
        {"body": "synthetic reply", "status": 200, "headers": {"access-control-allow-origin": "*"},
         "url": "https://hn.algolia.com/data", "redirected": False},
        [{"url": "https://hn.algolia.com/data", "kind": "api", "origin": "https://hn.algolia.com", "requested_with": False}],
    ), "adding one public POST profile must preserve existing bodyless GET records"


@pytest.mark.parametrize("budget", [
    pytest.param({"accepted_requests": 100, "decoded_bytes": 0}, id="expired-and-request-overflow"),
    pytest.param({"accepted_requests": 0, "decoded_bytes": 32 * 1048576}, id="expired-and-outgoing-byte-overflow"),
])
def test_search_shared_resource_refusal_has_priority_over_an_expired_loading_deadline(budget, trusted_search_key, monkeypatch):
    # GIVEN an already exceeded shared quota and simultaneous trusted deadline expiry.
    resolved = []
    monkeypatch.setattr(htmlurl, "_resolve", lambda *arguments: resolved.append(arguments))
    # WHEN finite POST preflight checks the original body before any acquisition.
    with pytest.raises(htmlurl.URLResourceError):
        htmlurl._fetch(SEARCH, deadline=time.monotonic() - 1, kind="api", origin="https://hn.algolia.com",
                      budget=dict(budget), search_body=BODY)
    # THEN timing cannot soften an already observable hard quota and no DNS runs.
    assert resolved == [], "an expired hard resource refusal must retain priority before DNS or transport"


def test_cross_origin_same_origin_mode_denies_search_before_transport(trusted_search_key):
    # GIVEN an approved endpoint with a cross-origin mode that forbids exposure.
    requested = []
    # WHEN the existing CORS mode owner validates the POST relative to the document.
    response = htmljs._host_response(search_record(mode="same-origin"), requested.append, DOCUMENT, DOCUMENT)
    # THEN the mode does not permit a wire attempt simply because search is authorized.
    assert (response, requested) == ({"error": "cors_denied"}, []), "same-origin mode must still refuse cross-origin search before acquisition"


def test_search_cors_filters_cookie_headers_and_preserves_api_status_and_bytes(trusted_search_key):
    # GIVEN an admitted search response with ordinary status, bytes and cookie metadata.
    requested = []

    def fetch(record):
        """Provide ordinary status/body plus headers that must remain unexposed."""
        requested.append(record)
        return {"status": 418, "body": b"exact response", "final_url": SEARCH,
                "headers": (("Access-Control-Allow-Origin", "*"), ("Set-Cookie", "synthetic=ignored"),
                            ("Content-Type", "text/plain"), ("X-Extra", "hidden"))}

    # WHEN the real parent filters an otherwise approved CORS response.
    response = htmljs._host_response(search_record(), fetch, DOCUMENT, DOCUMENT)
    # THEN status/body survive and ambient cookie/nonexposed headers never reach XHR.
    assert (response, len(requested)) == (
        {"body": "exact response", "status": 418, "headers": {"content-type": "text/plain"},
         "url": SEARCH, "redirected": False}, 1,
    ), "approved search must retain finite API visibility without exposing cookie state"


def test_admitted_search_reaches_real_http_with_original_utf8_bytes_and_form_header(trusted_search_key, monkeypatch):
    # GIVEN a complete synthetic search and a real http.client socketpair edge.
    client, server = socket.socketpair()
    connection = htmlurl.http.client.HTTPConnection("uj5wyc0l7x-dsn.algolia.net", 443)
    connection.sock = client
    received = []
    reply = b"synthetic reply"

    thread = threading.Thread(target=_serve_http, args=(server, received, reply), daemon=True)
    monkeypatch.setattr(htmlurl, "_resolve", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(htmlurl, "_connect", lambda *args: connection)
    original_fetch = htmlurl._fetch
    main = b"<html><body><p>Initial article</p></body></html>"
    providers = {"html": lambda url, **options: _html_response(main, **options), "api": original_fetch}

    def fetch(url, **options):
        """Keep initial HTML synthetic and send the admitted API through actual transport."""
        response = providers[options.get("kind", "html")](url, **options)
        return {**response, "final_url": url}

    def render(source, url, acquire, deadline, *, soft_window):
        """Use the existing parent callback signature before any new transport keyword."""
        response = htmljs._host_response(search_record(), acquire, DOCUMENT, DOCUMENT)
        assert response.get("body") == "synthetic reply", "an admitted POST must reach guarded HTTP before returning content"
        return {"html": "<html><body><p>synthetic reply</p></body></html>", "capture_status": "settled",
                "errors": [], "pending": {}, "counts": {}}

    monkeypatch.setattr(htmljs, "_check_runtime", lambda **options: None)
    monkeypatch.setattr(htmljs, "render", render)
    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert len(reply) == 15, "the controlled response must have complete framing"
    thread.start()
    # WHEN original request bytes cross orchestration, parent policy and the HTTP edge.
    try:
        code, receipt, markdown = htmlurl._run_url(DOCUMENT, render_js=True)
    finally:
        client.close()
        server.close()
        thread.join(2)
    # THEN method, byte length, original form payload and owned transport are exact.
    assert (code, receipt["acquisition"]["capture_status"], len(received), thread.is_alive(), client.fileno()) == (
        0, "settled", 1, False, -1,
    ), "the admitted original search must emit one HTTP request and close both owned endpoints"
    line, headers, body = received[0]
    assert (line, body, {header.split(b":", 1)[0].lower(): header.split(b":", 1)[1].strip() for header in headers}) == (
        b"POST " + SEARCH.partition(".net")[2].encode() + b" HTTP/1.1\r\n", BODY.encode("utf-8"),
        {b"host": b"uj5wyc0l7x-dsn.algolia.net", b"user-agent": htmlurl._USER_AGENT.encode(),
         b"accept": htmlurl._ACCEPT["api"].encode(), b"accept-encoding": b"identity", b"connection": b"close",
         b"origin": b"https://hn.algolia.com", b"content-type": FORM.encode(),
         b"content-length": str(len(BODY.encode("utf-8"))).encode()},
    ), "guarded POST must preserve original UTF-8/form bytes and host-controlled headers"
