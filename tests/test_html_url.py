"""Synthetic URL acquisition tests keep network policy separate from local HTML."""

import importlib
import gzip
import hashlib
import io
import ssl
import time
from html.parser import HTMLParser

import pytest

from brewdoc.common import BrewdocError
from test_contract import zero_tally
from test_html import HTML_OMISSIONS


@pytest.fixture
def htmlurl():
    """Load acquisition only when a test needs its optional entry boundary."""
    return importlib.import_module("brewdoc.htmlurl")


class Response:
    """Expose bounded response reads without real sockets or ambient credentials."""

    def __init__(self, body, headers=()):
        self.stream = io.BytesIO(body)
        self.headers = tuple(headers)
        self.read_sizes = []

    def getheaders(self):
        return self.headers

    def read(self, size):
        self.read_sizes.append(size)
        return self.stream.read(size)


class Targets(HTMLParser):
    """Record inert anchor/image/base targets from the independently parsed snapshot."""

    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        self.targets.extend((tag, name, value) for name, value in attrs
                            if tag in ("a", "img", "base") and name in ("href", "src"))


class Socket:
    """Record numeric connect, timeout and cleanup without operating-system I/O."""

    def __init__(self, peer="93.184.216.34"):
        self.peer = peer
        self.connections = []
        self.timeouts = []
        self.handshakes = []
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connections.append(address)

    def getpeername(self):
        return self.peer, 443

    def close(self):
        self.closed = True

    def do_handshake(self):
        self.handshakes.append(self.timeouts[-1])


class Connection:
    """Capture one actual http.client request call while serving synthetic response bytes."""

    def __init__(self, response):
        self.response = response
        self.sock = Socket()
        self.requests = []

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.sock.close()


def test_explicit_url_canonicalizes_host_and_default_port_without_changing_resource():
    # GIVEN a mixed-case HTTPS host with the default port, query and local fragment
    source = "https://ExAmPle.COM:443/docs?token=secret#part"
    assert source.endswith("?token=secret#part"), "query and fragment identify the requested resource"
    # WHEN explicit URL acquisition validates the source
    htmlurl = importlib.import_module("brewdoc.htmlurl")
    normalized = htmlurl._validate_url(source)
    # THEN only the host and redundant default port are normalized
    assert normalized == "https://example.com/docs?token=secret#part", "URL normalization must preserve resource semantics"


@pytest.mark.parametrize("source,expected", [
    ("http://example.com:80", "http://example.com/"),
    ("https://example.com/a%2Fb?q=a%26b", "https://example.com/a%2Fb?q=a%26b"),
    ("https://bücher.example/東京", "https://xn--bcher-kva.example/東京"),
    ("https://[2606:4700:4700::1111]:443/", "https://[2606:4700:4700::1111]/"),
])
def test_valid_url_preserves_encoded_path_and_query(htmlurl, source, expected):
    # GIVEN absolute HTTP URLs with explicit stable normalization expectations
    assert source.startswith(("http://", "https://")), "valid cases must use an HTTP scheme"
    # WHEN validating without DNS or requests
    normalized = htmlurl._validate_url(source)
    # THEN Unicode hosts and default ports normalize while path and query stay exact
    assert normalized == expected, "normalization must not reinterpret escaped path or query delimiters"


@pytest.mark.parametrize("source", [
    "file:///etc/passwd", "ftp://example.com/a", "//example.com/a", "https:///a",
    "https://user:password@example.com/", "https://example.com:444/",
    "http://example.com:443/", "https://example.com/%GG", "https://example.com/%",
    "https://example.com/\nsecret", "https://example.com/\x00secret",
    "https://example.com\\@evil.example/", "https://127.1/", "https://2130706433/",
    "https://0177.0.0.1/", "https://0x7f000001/", "https://[fe80::1%25en0]/",
    "https://example.com:invalid/", "https://example.com:/",
    "https://example.com/" + "é" * 4096,
], ids=["file", "ftp", "relative", "missing-host", "userinfo", "nondefault-tls-port",
        "wrong-default-port", "malformed-escape", "unfinished-escape", "newline", "nul",
        "backslash-host", "short-ipv4", "decimal-ipv4", "octal-ipv4", "hex-ipv4",
        "scoped-ipv6", "invalid-port", "empty-port", "utf8-byte-limit"])
def test_unsafe_or_ambiguous_url_refuses_before_dns(htmlurl, monkeypatch, source):
    # GIVEN unsupported, ambiguous, malformed or over-budget destination text
    dns = []
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: dns.append(args))
    assert dns == [], "URL grammar checks must start before resolution"
    # WHEN acquisition validates the destination
    with pytest.raises(BrewdocError):
        htmlurl._validate_url(source)
    # THEN no rejected destination reaches a resolver
    assert dns == [], "syntax refusal must not resolve or connect"


@pytest.mark.parametrize("address,expected", [
    ("1.1.1.1", True), ("8.8.8.8", True), ("93.184.216.34", True),
    ("2606:4700:4700::1111", True), ("2001:4860:4860::8888", True),
    ("0.0.0.0", False), ("0.1.2.3", False), ("10.0.0.1", False),
    ("100.64.0.1", False), ("127.0.0.1", False), ("169.254.169.254", False),
    ("172.16.0.1", False), ("192.168.0.1", False), ("192.0.0.9", False),
    ("192.0.2.1", False), ("198.18.0.1", False), ("198.51.100.1", False),
    ("203.0.113.1", False), ("224.0.0.1", False), ("240.0.0.1", False),
    ("255.255.255.255", False), ("::", False), ("::1", False),
    ("fc00::1", False), ("fe80::1", False), ("ff02::1", False),
    ("2001:db8::1", False), ("::ffff:127.0.0.1", False),
    ("::ffff:8.8.8.8", False), ("2002:7f00:1::", False),
    ("2001:0000:4136:e378:8000:63bf:3fff:fdd2", False),
    ("64:ff9b::7f00:1", False), ("fe80::1%en0", False), ("not-an-address", False),
])
def test_address_policy_has_stable_routable_and_transition_table(htmlurl, address, expected):
    # GIVEN explicit addresses independent of Python patch-level classification
    assert type(address) is str, "address policy input is numeric address text"
    # WHEN the fixed production policy classifies the address
    accepted = htmlurl._public_address(address)
    # THEN mapped, transition, special and private ranges cannot become public by interpreter drift
    assert accepted is expected, "the reviewed routability table must hold across Python patches"


@pytest.mark.parametrize("answers", [
    ("93.184.216.34", "127.0.0.1"), ("::1", "2606:4700:4700::1111"),
    ("93.184.216.34", "::ffff:169.254.169.254"), (),
])
def test_dns_mixed_or_empty_answers_refuse_the_whole_destination(htmlurl, monkeypatch, answers):
    # GIVEN one DNS result containing a private candidate or no candidate
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: answers)
    assert type(answers) is tuple, "resolution supplies one frozen candidate set"
    # WHEN resolving the public hostname
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._resolve("example.com", 443, time.monotonic() + 30)
    # THEN refusing the whole answer set prevents selecting only its public member
    assert isinstance(refusal.value, htmlurl.URLPolicyError), "unsafe DNS candidates must be a containment refusal"


def test_dns_public_answers_are_canonicalized_without_a_second_lookup(htmlurl, monkeypatch):
    # GIVEN a single all-public dual-stack DNS answer
    calls = []
    answers = ("2606:4700:4700:0:0:0:0:1111", "93.184.216.34")
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: (calls.append(args), answers)[1])
    deadline = time.monotonic() + 30
    assert calls == [], "the resolver must start without ambient lookups"
    # WHEN the destination is resolved for a bounded connection
    resolved = htmlurl._resolve("example.com", 443, deadline)
    # THEN the approved frozen numeric answers are canonical and resolution occurs once
    assert (resolved, calls) == (
        ("2606:4700:4700::1111", "93.184.216.34"), [("example.com", 443, deadline)]
    ), "connection candidates must come from one validated DNS result"


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/a?token=secret&token=two&empty=#local", "https://example.com/a?token=[redacted]&token=[redacted]&empty=[redacted]#[redacted]"),
    ("https://example.com/a?opaque-secret", "https://example.com/a?[redacted]"),
    ("https://example.com/a?=secret", "https://example.com/a?=[redacted]"),
])
def test_display_url_redacts_every_query_value_and_opaque_query_segment(htmlurl, url, expected):
    # GIVEN secret query values, including duplicates and unnamed segments
    assert "?" in url, "redaction cases must contain query data"
    # WHEN a URL is prepared for a receipt or refusal
    shown = htmlurl._display_url(url)
    # THEN no query value is published while named query structure remains visible
    assert shown == expected, "display URLs must not leak query credentials"


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/#access_token=synthetic-secret", "https://example.com/#[redacted]"),
    ("https://user:password@example.com/a?q=secret#token=secret", "https://example.com/a?q=[redacted]#[redacted]"),
    ("https://example.com/#/account/secret", "https://example.com/#[redacted]"),
    ("https://example.com/#", "https://example.com/"),
])
def test_display_url_redacts_entire_nonempty_fragment_and_userinfo(htmlurl, url, expected):
    # GIVEN fragments that can carry credentials or application state.
    assert "#" in url, "Each case must exercise an explicit fragment delimiter."
    # WHEN the receipt displays a URL.
    shown = htmlurl._display_url(url)
    # THEN only the entire nonempty fragment is replaced, with userinfo omitted.
    assert shown == expected, "Display provenance must not reveal fragment tokens or credentials."


@pytest.mark.parametrize("render_js,runtime,shown,expected_render", [
    (True, "https://example.com/page?q=secret#access_token=synthetic-secret",
     "https://example.com/page?q=[redacted]#[redacted]",
     ["https://example.com/page?q=secret#access_token=synthetic-secret"]),
    (False, "https://example.com/page?q=secret", "https://example.com/page?q=[redacted]", []),
])
def test_run_url_redacts_receipt_fragments_without_changing_runtime_or_identity(
        htmlurl, monkeypatch, tmp_path, render_js, runtime, shown, expected_render):
    # GIVEN synthetic HTML and a requested URL carrying query and fragment secrets.
    from brewdoc import htmljs, run

    requested = "https://example.com/page?q=secret#access_token=synthetic-secret"
    body = b"<html><body><p>article</p></body></html>"
    local = tmp_path / "local.html"
    local.write_bytes(body)
    baseline = run(local)
    seen = []
    rendered_urls = []
    monkeypatch.setattr(htmljs, "_check_runtime", lambda: None)

    def fetch(url, **kwargs):
        seen.append(url)
        return {"final_url": url, "body": body, "entity": body,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "entity_sha256": hashlib.sha256(body).hexdigest(), "headers": ()}

    monkeypatch.setattr(htmlurl, "_fetch", fetch)

    def render(source, final_url, *args):
        rendered_urls.append(final_url)
        return {
            "html": body.decode(), "capture_status": "partial", "error_count": 1,
            "errors": [{"category": "script", "resource_url": requested}],
            "pending": {"requests": 0, "timers": 0, "modules": 0, "promises": 0, "jobs": False},
            "counts": {"promise_jobs": 0, "timer_callbacks": 0},
        }

    monkeypatch.setattr(htmljs, "render", render)
    assert baseline[0] == 0, "The local HTML precondition must convert successfully."
    # WHEN the actual URL orchestration produces provenance and Markdown.
    code, receipt, markdown = htmlurl._run_url(requested, render_js=render_js)
    provenance = {key: receipt["acquisition"][key] for key in (
        "requested_url", "final_url", "effective_base_url", "url_sha256")}
    # THEN runtime/hash preserve JS state while every displayed URL hides it.
    assert (code, receipt["source"], provenance, seen, rendered_urls,
            markdown.splitlines()[-1], run(local)) == (
        0, shown, {"requested_url": shown, "final_url": shown, "effective_base_url": shown,
                   "url_sha256": hashlib.sha256(runtime.encode()).hexdigest()},
        [runtime], expected_render, "article", baseline,
    ), "Only display fields may redact the fragment; existing local conversion must remain exact."
    assert "synthetic-secret" not in str(receipt), "Nested resource error provenance must also redact the fragment."


def test_duplicate_cors_headers_reach_the_js_policy_owner_unchanged(htmlurl, monkeypatch):
    # GIVEN an API response with ambiguous CORS grants.
    headers = (("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Origin", "https://other.test"))
    response = {"status": 200, "headers": headers, "entity": b"data", "body": b"data"}
    monkeypatch.setattr(htmlurl, "_request", lambda *args: dict(response))
    assert len(headers) == 2, "The ambiguity must contain two original header fields."
    # WHEN guarded acquisition completes its transport and byte policy.
    captured = htmlurl._fetch("https://example.com/data", kind="api", origin="https://other.test")
    # THEN the JS policy can decide same/cross-origin CORS without transport erasure.
    assert (captured["headers"], captured["body"]) == (
        headers + (("Content-Length", "4"),), b"data",
    ), "Transport must preserve duplicate CORS fields for the owning JS policy."


@pytest.mark.parametrize("policy", ["default-src 'none'", ""])
def test_js_main_response_csp_presence_marks_valid_dom_partial(htmlurl, monkeypatch, policy):
    # GIVEN valid captured HTML and a main-response CSP field, including empty policy.
    from brewdoc import htmljs

    body = b"<html><body><p>article</p></body></html>"
    monkeypatch.setattr(htmljs, "_check_runtime", lambda: None)
    monkeypatch.setattr(htmlurl, "_fetch", lambda *args, **kwargs: {
        "final_url": "https://example.com/", "body": body, "entity": body,
        "body_sha256": hashlib.sha256(body).hexdigest(), "entity_sha256": hashlib.sha256(body).hexdigest(),
        "headers": (("Content-Security-Policy", policy),),
    })
    monkeypatch.setattr(htmljs, "render", lambda *args: {
        "html": body.decode(), "capture_status": "settled", "errors": [], "error_count": 0,
        "pending": {"requests": 0, "timers": 0, "modules": 0, "promises": 0, "jobs": False},
        "counts": {"promise_jobs": 0, "timer_callbacks": 0},
    })
    assert len(body) == 40, "The synthetic captured document must stay fixed."
    # WHEN actual URL orchestration converts the valid DOM.
    code, receipt, markdown = htmlurl._run_url("https://example.com/", render_js=True)
    # THEN unsupported CSP is visible instead of claiming settled browser policy.
    assert (code, receipt["acquisition"]["capture_status"], receipt["acquisition"]["errors"],
            receipt["acquisition"]["error_count"], markdown.splitlines()[-1]) == (
        0, "partial", [{"category": "csp_policy_unsupported", "resource_url": None}], 1, "article",
    ), "Main-response CSP must mark useful DOM partial while preserving successful conversion."


@pytest.mark.parametrize("render_js", [False, True])
def test_url_refusal_pending_envelope_retains_unknown_promise_state(htmlurl, render_js):
    # GIVEN a URL rejected before any script execution.
    assert htmlurl._acquisition(render_js)["capture_status"] == "refused", "The initial envelope must start refused."
    # WHEN actual orchestration rejects a non-HTTP scheme.
    code, receipt, markdown = htmlurl._run_url("file:///private", render_js=render_js)
    # THEN unobserved promise state is null rather than implicitly complete.
    assert (code, receipt["acquisition"]["pending"], markdown) == (
        1, {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None}, "",
    ), "Pre-execution refusals must retain the complete unknown-work envelope."


@pytest.mark.parametrize("encoding", ["identity", "gzip"])
def test_transferred_and_decoded_body_accept_the_exact_inclusive_limit(htmlurl, monkeypatch, encoding):
    # GIVEN a response whose decoded content reaches the allowed byte boundary
    body = b"a" * 64
    entities = {"identity": body, "gzip": gzip.compress(body, mtime=0)}
    entity = entities[encoding]
    response = Response(entity, (("Content-Encoding", encoding), ("Content-Length", str(len(entity)))))
    monkeypatch.setattr(htmlurl, "_BODY_LIMIT", 64)
    assert len(body) == 64, "the decoded body must sit exactly on the inclusive boundary"
    # WHEN the bounded response reader consumes the entity
    captured = htmlurl._read_response(response, time.monotonic() + 30)
    # THEN entity identity and decoded bytes both survive without truncation
    assert captured == (entity, body), "exact-limit bodies must retain original and decompressed bytes"


@pytest.mark.parametrize("entity,headers", [
    (b"a" * 65, (("Content-Encoding", "identity"),)),
    (gzip.compress(b"a" * 65, mtime=0), (("Content-Encoding", "gzip"),)),
    (b"x", (("Content-Encoding", "br"),)),
    (b"not-gzip", (("Content-Encoding", "gzip"),)),
    (b"a", (("Content-Length", "65"),)),
    (b"a", (("Content-Length", "1"), ("Content-Length", "2"))),
], ids=["entity-one-over", "decompressed-one-over", "unsupported-encoding", "corrupt-gzip",
        "declared-length-one-over", "conflicting-lengths"])
def test_response_entity_decompression_and_header_bounds_refuse(htmlurl, monkeypatch, entity, headers):
    # GIVEN an over-budget body, corrupt encoding or contradictory entity metadata
    response = Response(entity, headers)
    monkeypatch.setattr(htmlurl, "_BODY_LIMIT", 64)
    assert response.stream.tell() == 0, "the response must start unread"
    # WHEN the bounded response reader acquires bytes
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._read_response(response, time.monotonic() + 30)
    # THEN the caller receives a hard failure rather than a truncated accepted document
    assert isinstance(refusal.value, BrewdocError), "invalid transport bytes must be a hard acquisition refusal"


def test_expired_body_deadline_refuses_before_another_read(htmlurl):
    # GIVEN unread entity bytes after the shared acquisition deadline
    response = Response(b"<p>late</p>")
    assert response.read_sizes == [], "deadline test must start with no response read"
    # WHEN attempting to read after the total deadline
    with pytest.raises(BrewdocError):
        htmlurl._read_response(response, time.monotonic() - 1)
    # THEN no late blocking body operation starts
    assert response.read_sizes == [], "an expired total budget must refuse before socket reads"


def test_https_redirect_downgrade_refuses_before_following_destination(htmlurl, monkeypatch):
    # GIVEN a public HTTPS response redirecting to cleartext HTTP
    calls = []
    hop = {"status": 302, "headers": (("Location", "http://example.com/plain"),),
           "entity": b"", "body": b""}
    monkeypatch.setattr(htmlurl, "_request", lambda url, *args: (calls.append(url), hop)[1])
    assert calls == [], "redirect acquisition must start without an outgoing hop"
    # WHEN the static fetch follows redirect policy
    with pytest.raises(BrewdocError):
        htmlurl._fetch("https://example.com/start")
    # THEN the insecure destination is never requested
    assert calls == ["https://example.com/start"], "HTTPS downgrade must stop before a second request"


@pytest.mark.parametrize("status", [300, 304, 400, 401, 403, 407, 429, 500])
def test_non_success_and_unapproved_redirect_statuses_refuse_without_retry(htmlurl, monkeypatch, status):
    # GIVEN an unsuccessful or unsupported status and a tempting Location header
    calls = []
    hop = {"status": status, "headers": (("Location", "https://example.com/other"),),
           "entity": b"<p>blocked</p>", "body": b"<p>blocked</p>"}
    monkeypatch.setattr(htmlurl, "_request", lambda url, *args: (calls.append(url), hop)[1])
    assert calls == [], "status refusal must start before acquisition"
    # WHEN static acquisition receives the status
    with pytest.raises(BrewdocError):
        htmlurl._fetch("https://example.com/start")
    # THEN there is one attempted hop and no retry or unsupported redirect
    assert calls == ["https://example.com/start"], "failed final statuses must refuse without retry"


def test_redirect_hops_are_bounded_instead_of_retrying_forever(htmlurl, monkeypatch):
    # GIVEN a redirect chain that never returns a document
    calls = []
    def redirect(url, *args):
        calls.append(url)
        return {"status": 302, "headers": (("Location", f"/hop{len(calls)}"),),
                "entity": b"", "body": b""}

    monkeypatch.setattr(htmlurl, "_request", redirect)
    assert calls == [], "redirect loop must start with no attempted requests"
    # WHEN static acquisition tries to follow the chain
    with pytest.raises(BrewdocError):
        htmlurl._fetch("https://example.com/start")
    # THEN at most five redirect destinations are requested after the original page
    assert calls == ["https://example.com/start"] + [f"https://example.com/hop{index}" for index in range(1, 6)], "five redirect hops are inclusive"


@pytest.mark.parametrize("headers,body", [
    ((("Content-Type", "application/xhtml+xml"),), b"<p>x</p>"),
    ((("Content-Type", "text/plain"),), b"<p>x</p>"),
    ((), b"<p>x</p>"),
    ((("Content-Type", "text/html; charset=windows-1252"),), b"<p>x</p>"),
    ((("Content-Type", "text/html"), ("Content-Type", "text/plain")), b"<p>x</p>"),
    ((("Content-Type", "text/html; charset=utf-8"),), b"<p>\xff</p>"),
    ((("Content-Type", "text/html"),), b'<meta charset="windows-1252"><p>x</p>'),
], ids=["xhtml", "plain", "missing-type", "legacy-header", "duplicate-type",
        "invalid-utf8", "legacy-declaration"])
def test_main_document_requires_unambiguous_utf8_html(htmlurl, monkeypatch, headers, body):
    # GIVEN successful transport carrying unsupported or ambiguous HTML metadata
    hop = {"status": 200, "headers": headers, "entity": body, "body": body}
    monkeypatch.setattr(htmlurl, "_request", lambda *args: hop)
    assert type(body) is bytes, "transport must supply raw bytes before document decoding"
    # WHEN the main-document policy accepts or refuses the resource
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._fetch("https://example.com/page")
    # THEN invalid encodings and non-HTML resources are hard acquisition failures
    assert isinstance(refusal.value, BrewdocError), "UTF-8 HTML policy must not silently transcode or accept other media"


def test_snapshot_resolves_inert_targets_against_final_page_and_source_base(htmlurl):
    # GIVEN a redirected page with a relative base, Unicode content and inert targets
    source = '<html><head><base href="../assets/"></head><body><a href="guide?q=1">café</a><img src="img.png"></body></html>'
    assert source.count("<base ") == 1, "this source must declare one effective base"
    # WHEN URL-only snapshot preparation freezes resolved inert references
    snapshot, effective_base = htmlurl._snapshot(source, "https://example.com/docs/final.html")
    targets = Targets()
    targets.feed(snapshot.decode("utf-8"))
    # THEN relative targets use the declared final-page base without any resource acquisition
    assert (effective_base, targets.targets) == (
        "https://example.com/assets/",
        [("base", "href", "https://example.com/assets/"),
         ("a", "href", "https://example.com/assets/guide?q=1"),
         ("img", "src", "https://example.com/assets/img.png")]
    ), "the frozen snapshot must retain an explicit base and resolved inert targets"


@pytest.mark.parametrize("base", ["file:///etc/", "javascript:alert(1)", "https://user:secret@example.com/", "https://example.com:444/"])
def test_snapshot_refuses_unsupported_or_unsafe_source_base(htmlurl, base):
    # GIVEN a source base that changes relative references into an unsupported destination
    source = f'<base href="{base}"><p>content</p>'
    assert source.startswith("<base "), "base refusal requires a source declaration"
    # WHEN snapshot preparation validates relative-reference resolution
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._snapshot(source, "https://example.com/docs/page")
    # THEN unsupported bases do not become silently accepted snapshot provenance
    assert isinstance(refusal.value, htmlurl.URLPolicyError), "unsafe source bases must refuse snapshot preparation"


def test_pinned_https_connection_verifies_hostname_and_keeps_tls_required(htmlurl, monkeypatch):
    # GIVEN one approved address and a different original HTTPS hostname
    stream = Socket()
    tls = []
    monkeypatch.setattr(htmlurl.socket, "socket", lambda *args: stream)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", lambda context, sock, *, server_hostname, **options:
                        (tls.append((server_hostname, context.check_hostname, context.verify_mode)), sock)[1])
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    assert stream.connections == [], "pinning test must start without a socket connection"
    # WHEN the transport opens the approved numeric endpoint
    connection = htmlurl._connect("https://example.com/a", "93.184.216.34", 130.0)
    connection.close()
    # THEN the peer is pinned while original-host SNI and certificate verification remain enabled
    assert (stream.connections, stream.timeouts, tls, stream.closed) == (
        [("93.184.216.34", 443)], [5.0, 5.0, 5.0], [("example.com", True, ssl.CERT_REQUIRED)], True
    ), "numeric connection must retain verified original-host TLS and close its socket"


@pytest.mark.parametrize("peer", ["127.0.0.1", "8.8.8.8", "::ffff:93.184.216.34"])
def test_actual_peer_must_match_the_approved_numeric_destination(htmlurl, monkeypatch, peer):
    # GIVEN a socket reporting a private or different public peer after connect
    stream = Socket(peer)
    tls = []
    monkeypatch.setattr(htmlurl.socket, "socket", lambda *args: stream)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", lambda *args, **kwargs: tls.append(kwargs))
    assert stream.closed is False, "peer test must begin with an open fake socket"
    # WHEN production transport verifies the actual endpoint
    with pytest.raises(htmlurl.URLPolicyError):
        htmlurl._connect("https://example.com/", "93.184.216.34", time.monotonic() + 30)
    # THEN mismatch is a containment failure before TLS and the socket is closed
    assert (stream.connections, tls, stream.closed) == (
        [("93.184.216.34", 443)], [], True
    ), "a changed actual peer must never be accepted or sent to TLS"


def test_certificate_failure_closes_the_pinned_socket_without_plaintext_retry(htmlurl, monkeypatch):
    # GIVEN an approved peer whose TLS certificate verification fails
    stream = Socket()
    monkeypatch.setattr(htmlurl.socket, "socket", lambda *args: stream)

    def reject_certificate(*args, **kwargs):
        raise ssl.SSLCertVerificationError("synthetic certificate mismatch")

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", reject_certificate)
    assert stream.connections == [], "TLS refusal must start before connecting"
    # WHEN the pinned HTTPS connection validates its certificate
    with pytest.raises(ssl.SSLCertVerificationError):
        htmlurl._connect("https://example.com/", "93.184.216.34", time.monotonic() + 30)
    # THEN no retry or plaintext fallback occurs and the failed socket is closed
    assert (stream.connections, stream.closed) == (
        [("93.184.216.34", 443)], True
    ), "TLS verification failure must retain the failure and release the numeric connection"


def test_static_body_hashes_keep_transferred_gzip_separate_from_decoded_html(htmlurl, monkeypatch):
    # GIVEN a compressed entity with independently known decoded bytes
    body = b"<p>frozen</p>"
    entity = gzip.compress(body, mtime=0)
    hop = {"status": 200, "headers": (("Content-Type", "text/html"),
           ("Content-Encoding", "gzip")), "entity": entity, "body": body}
    monkeypatch.setattr(htmlurl, "_request", lambda *args: dict(hop))
    assert len(entity) == 33, "the synthetic gzip entity identity must remain stable"
    # WHEN acquisition records frozen response provenance
    captured = htmlurl._fetch("https://example.com/frozen")
    # THEN response and decoded hashes identify their respective bytes without relabeling the snapshot
    assert captured == dict(hop, headers=(("Content-Type", "text/html"), ("Content-Length", "13")),
        requested_url="https://example.com/frozen",
        final_url="https://example.com/frozen", entity_sha256=hashlib.sha256(entity).hexdigest(),
        body_sha256="097e091696d8a165038f7d132954453433b8f76f562f09fad5fade79adb0e395", redirects=()
    ), "gzip entity and decoded HTML hashes must describe distinct original byte streams"


def test_get_uses_one_dns_result_original_host_and_no_fragment_or_ambient_credentials(htmlurl, monkeypatch):
    # GIVEN a public DNS answer plus ambient proxy and credentials that URL mode must ignore
    response = Response(b"<p>public</p>", (("Content-Type", "text/html"),))
    response.status = 200
    connection = Connection(response)
    resolutions, connects = [], []
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@127.0.0.1:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://user:secret@127.0.0.1:3128")
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args:
                        (resolutions.append(args[:2]), ("93.184.216.34", "8.8.8.8"))[1])
    monkeypatch.setattr(htmlurl, "_connect", lambda url, address, deadline:
                        (connects.append((url, address)), connection)[1])
    assert connection.requests == [], "no request may precede destination validation"
    # WHEN the true single-hop transport constructs and executes its GET
    captured = htmlurl._request("https://example.com/a?token=secret#local", time.monotonic() + 30, "text/html")
    # THEN numeric pinning and request identity exclude the fragment, proxies, cookies and authorization
    assert (resolutions, connects, connection.requests, captured, connection.sock.closed) == (
        [("example.com", 443)], [("https://example.com/a?token=secret#local", "93.184.216.34")],
        [("GET", "/a?token=secret", {"Host": "example.com", "User-Agent": htmlurl._USER_AGENT,
          "Accept": "text/html", "Accept-Encoding": "identity", "Connection": "close"})],
        {"status": 200, "headers": (("Content-Type", "text/html"),),
         "entity": b"<p>public</p>", "body": b"<p>public</p>"}, True
    ), "the guarded GET must preserve Host while ignoring ambient proxy credentials and fragments"


def test_private_dns_hard_failure_preserves_existing_output_and_exact_safe_receipt(htmlurl, monkeypatch, tmp_path):
    # GIVEN an existing destination and a hostname that resolves to a private address
    out = tmp_path / "existing.md"
    out.write_bytes(b"previous output\n")
    requested = "https://example.com/page?token=secret"
    shown = "https://example.com/page?token=[redacted]"
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("127.0.0.1",))
    assert out.read_bytes() == b"previous output\n", "hard refusal must begin with retained prior output"
    # WHEN the URL orchestration applies its real DNS containment policy
    result = htmlurl._run_url(requested, out)
    expected = {
        "file_ok": False, "route": "html", "reason": "URL acquisition refused during dns",
        "source": shown, "out": None, "receipt_schema": "brewdoc.receipt/2",
        "unit_kind": "chapter", "units": 0, "tables": 0, "text_regions": 0,
        "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": HTML_OMISSIONS,
        "acquisition": {
            "mode": "static", "requested_url": shown, "final_url": None,
            "effective_base_url": None, "url_sha256": hashlib.sha256(requested.encode()).hexdigest(),
            "response": {"entity_bytes": None, "entity_sha256": None, "body_bytes": None, "body_sha256": None},
            "snapshot": {"kind": None, "bytes": None, "sha256": None}, "engine": None,
            "capture_status": "refused", "errors": [{"category": "dns", "resource_url": None}],
            "error_count": 1, "pending": {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None},
            "counts": {"requests": 1, "promise_jobs": 0, "timer_callbacks": 0},
            "unsupported_resources": ["scripts", "stylesheets", "images", "subframes", "media", "browser_state"],
            "refusal_stage": "dns", "soft_window_seconds": None,
        },
    }
    # THEN the whole refusal is sanitized and no snapshot or output is published
    assert (result, out.read_bytes(), sorted(path.name for path in tmp_path.iterdir())) == (
        (1, expected, ""), b"previous output\n", ["existing.md"]
    ), "private-DNS refusal must preserve prior bytes and publish only the safe exact URL receipt"


def test_tls_handshake_receives_only_the_deadline_remaining_after_connect(htmlurl, monkeypatch):
    # GIVEN a connect that consumes 4.9 seconds of a five-second overall deadline
    clock = {"now": 100.0}
    stream = Socket()
    tls_options = []

    def slow_connect(address):
        stream.connections.append(address)
        clock["now"] = 104.9

    monkeypatch.setattr(stream, "connect", slow_connect)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(htmlurl.socket, "socket", lambda *args: stream)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", lambda context, sock, **options:
                        (tls_options.append(options), sock)[1])
    assert clock["now"] == 100.0, "deadline regression must start with the complete five-second budget"
    # WHEN connect succeeds and production transport starts the TLS handshake
    connection = htmlurl._connect("https://example.com/", "93.184.216.34", 105.0)
    connection.close()
    # THEN explicit handshake receives only the remaining 100 ms and cannot reuse the connect timeout
    assert (tls_options, [round(value, 6) for value in stream.handshakes], stream.closed) == (
        [{"server_hostname": "example.com", "do_handshake_on_connect": False}], [0.1], True
    ), "TLS must recompute the overall deadline after numeric connect and before the blocking handshake"


def test_malformed_url_refusal_never_echoes_userinfo_like_path_secrets(htmlurl, tmp_path):
    # GIVEN invalid URL grammar that hides apparent credentials in a path
    out = tmp_path / "retained.md"
    out.write_bytes(b"prior output")
    assert out.read_bytes() == b"prior output", "redaction refusal must preserve a preexisting output"
    # WHEN the explicit URL boundary rejects the malformed destination
    code, receipt, markdown = htmlurl._run_url("https:///user:secret@example.com/?token=secret", out)
    # THEN malformed source text is represented by a placeholder rather than leaked in provenance
    assert (code, receipt["source"], receipt["acquisition"]["requested_url"],
            receipt["acquisition"]["url_sha256"], markdown, out.read_bytes()) == (
        1, "[invalid URL]", None, None, "", b"prior output"
    ), "invalid URL grammar must never publish path-embedded credentials or secret query values"
