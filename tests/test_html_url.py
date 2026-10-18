"""Synthetic URL acquisition tests keep network policy separate from local HTML."""

import importlib
import gzip
import hashlib
import io
import socket
import ssl
import time
import zipfile
from html.parser import HTMLParser

import pytest

from brewdoc import run, selfcheck
from brewdoc.common import BrewdocError
from brewdoc.service import ROUTES
from test_contract import docx, text_op, zero_tally, zones_book
from test_html import HTML_OMISSIONS
from test_html_timeout import CHAPTER, URL, _html_response


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


@pytest.mark.parametrize(("source", "size"), [
    pytest.param("https://example.com/" + "a" * 9000, 9020, id="ascii-over-length"),
    pytest.param("https://example.com/" + "é" * 4100, 8220, id="two-byte-over-length"),
])
def test_over_length_main_url_refuses_with_a_hidden_source(htmlurl, source, size):
    # GIVEN a main URL over the 8192 encoded-byte limit
    assert len(source.encode("utf-8")) == size, "the URL must exceed the 8192-byte limit by a known size"
    # WHEN the URL boundary rejects it
    code, receipt, markdown = htmlurl._run_url(source)
    acquisition = receipt["acquisition"]
    # THEN both character classes refuse at url and never echo the source
    assert (code, receipt["source"], acquisition["requested_url"], acquisition["refusal_stage"],
            acquisition["capture_status"], acquisition["errors"], markdown) == (
        1, "[invalid URL]", None, "url", "refused", [{"category": "url", "resource_url": None}], "",
    ), "an over-long main URL must be hidden by the same encoded-byte limit that refuses it"


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
    monkeypatch.setattr(htmljs, "_check_runtime", lambda **options: None)

    def fetch(url, **kwargs):
        seen.append(url)
        return {"final_url": url, "body": body, "entity": body,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "entity_sha256": hashlib.sha256(body).hexdigest(),
                "headers": (("Content-Type", "text/html"),)}

    monkeypatch.setattr(htmlurl, "_fetch", fetch)

    def render(source, final_url, *args, **options):
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
    monkeypatch.setattr(htmljs, "_check_runtime", lambda **options: None)
    monkeypatch.setattr(htmlurl, "_fetch", lambda *args, **kwargs: {
        "final_url": "https://example.com/", "body": body, "entity": body,
        "body_sha256": hashlib.sha256(body).hexdigest(), "entity_sha256": hashlib.sha256(body).hexdigest(),
        "headers": (("Content-Type", "text/html"), ("Content-Security-Policy", policy)),
    })
    monkeypatch.setattr(htmljs, "render", lambda *args, **options: {
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
    with pytest.raises(htmlurl.URLTimeoutError):
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


@pytest.mark.parametrize("headers,body,stage", [
    ((("Content-Type", "application/xhtml+xml"),), b"<p>x</p>", "media"),
    ((), b"<p>x</p>", "media"),
    ((("Content-Type", "text/html"), ("Content-Type", "text/plain")), b"<p>x</p>", "media"),
    ((("Content-Type", "text/html; charset=utf-8"),), b"<p>\xff</p>", "encoding"),
], ids=["xhtml", "missing-type", "duplicate-type", "invalid-utf8"])
def test_main_document_refuses_unsupported_ambiguous_or_undecodable_html(htmlurl, monkeypatch, headers, body, stage):
    # GIVEN successful transport carrying unsupported, ambiguous or undecodable HTML
    hop = {"status": 200, "headers": headers, "entity": body, "body": body}
    monkeypatch.setattr(htmlurl, "_request", lambda *args: hop)
    assert type(body) is bytes, "transport must supply raw bytes before document decoding"
    # WHEN the main-document policy accepts or refuses the resource
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._fetch("https://example.com/page")
    # THEN each refusal names the media or encoding stage that rejected it
    assert getattr(refusal.value, "acquisition_stage", None) == stage, \
        "HTML media and decoding refusals must keep their stage"


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


@pytest.mark.parametrize("base", [pytest.param("http://[", id="unparseable"),
                                  pytest.param("http://a b/", id="syntax-invalid")])
def test_invalid_source_base_is_ignored_by_static_url_capture(htmlurl, monkeypatch, base):
    # GIVEN acquired main HTML whose base cannot be parsed and whose link is relative
    body = f'<html><head><base href="{base}"></head><body><p>See <a href="guide?q=1">guide</a></p></body></html>'.encode()
    monkeypatch.setattr(htmlurl, "_fetch", lambda url, *, kind="html", **options: _html_response(body, **options))
    # WHEN static acquisition snapshots and converts the page
    code, receipt, markdown = htmlurl._run_url(URL, timeout=10)
    acquisition = receipt["acquisition"]
    # THEN the document URL stays the base, as browsers ignore an invalid base
    assert (code, markdown.partition(CHAPTER)[2], acquisition["capture_status"], acquisition["refusal_stage"],
            acquisition["errors"], acquisition["effective_base_url"]) == (
        0, "See [guide](https://fixture.test/guide?q=1)\n", "static", None, [], URL,
    ), "an invalid <base href> must be ignored, not refuse the capture at snapshot"


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
        [("93.184.216.34", 443)], [30.0, 30.0, 30.0], [("example.com", True, ssl.CERT_REQUIRED)], True
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
    monkeypatch.setattr(htmlurl, "_connect", lambda url, address, deadline, connect_deadline=None:
                        (connects.append((url, address)), connection)[1])
    assert connection.requests == [], "no request may precede destination validation"
    # WHEN the true single-hop transport constructs and executes its GET
    captured = htmlurl._request("https://example.com/a?token=secret#local", time.monotonic() + 30, "text/html")
    # THEN numeric pinning and request identity exclude the fragment, proxies, cookies and authorization
    assert (resolutions, connects, connection.requests, captured, connection.sock.closed) == (
        [("example.com", 443)], [("https://example.com/a?token=secret#local", "93.184.216.34")],
        [("GET", "/a?token=secret", {"Host": "example.com", "User-Agent": htmlurl._USER_AGENT,
          "Accept": "text/html", "Accept-Encoding": "gzip", "Connection": "close"})],
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
            "capture_status": "refused", "errors": [{"category": "dns", "resource_url": shown}],
            "error_count": 1, "pending": {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None},
            "counts": {"requests": 1, "promise_jobs": 0, "timer_callbacks": 0},
            "unsupported_resources": ["scripts", "stylesheets", "images", "subframes", "media", "browser_state"],
            "refusal_stage": "dns", "soft_window_seconds": None,
            "soft_window_origin": None, "timeout_seconds": 30.0,
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


CONTENT_TYPES = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HTML_TYPE = ("Content-Type", "text/html")
OCTET = ("Content-Type", "application/octet-stream")
PENDING = {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None}
STATIC_RESOURCES = ["scripts", "stylesheets", "images", "subframes", "media", "browser_state"]
SECRET_URL = "https://example.com/start?token=secret"
SECRET_SHOWN = "https://example.com/start?token=[redacted]"
PRIVET_HTML = "<html><head></head><body><p>Привет</p></body></html>".encode()
MALFORMED_DISPOSITION = "attachment; ;filename*;filename*0*"
NUL_DISPOSITION = "attachment; filename*0*=utf-8\x00'';x%)"
REAL_SOCKET = socket.socket
PRIVET_META_HTML = '<html><head><meta charset="utf-8"></head><body><p>Привет</p></body></html>'.encode()


def sha256(data: bytes) -> str:
    """Return the hex SHA-256 digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def pdf_bytes(path):
    """Return a one-page synthetic PDF."""
    return selfcheck.synthetic_pdf([text_op(72, 700, "Quarterly report")])


def packaged(path, build):
    """Add the content-types part that OOXML magic detection requires."""
    build(path)
    with zipfile.ZipFile(path, "a") as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
    return path.read_bytes()


def docx_bytes(path):
    """Return a one-paragraph DOCX package."""
    return packaged(path, lambda target: docx(target, "<w:p><w:r><w:t>Quarterly report</w:t></w:r></w:p>"))


def xlsx_bytes(path):
    """Return the one-sheet zones workbook package."""
    return packaged(path, zones_book)


def text_bytes(path):
    """Return one UTF-8 text line."""
    return "Quarterly report\n".encode()


def ok(headers, body):
    """Return one successful guarded response."""
    return {"status": 200, "headers": headers, "entity": body, "body": body}


def moved(location):
    """Return one 302 redirect response."""
    return {"status": 302, "headers": (("Location", location),), "entity": b"", "body": b""}


def serve(htmlurl, monkeypatch, hops):
    """Answer each guarded request from a URL -> response table and record request order."""
    calls = []
    monkeypatch.setattr(htmlurl, "_request", lambda url, *args, **options: (calls.append(url), dict(hops[url]))[1])
    return calls


def record_snapshots(htmlurl, monkeypatch):
    """Record the frozen snapshot name and bytes handed to local conversion."""
    snapshots = []
    convert = htmlurl.run

    def spy(path, out=None, **options):
        snapshots.append((path.name, path.read_bytes()))
        return convert(path, out, **options)

    monkeypatch.setattr(htmlurl, "run", spy)
    return snapshots


def converted(tmp_path, url, suffix, snapshot):
    """Convert `snapshot` locally under the `url-<sha16><suffix>` name URL mode must use."""
    folder = tmp_path / "expected"
    folder.mkdir()
    local = folder / ("url-%s%s" % (sha256(url.encode())[:16], suffix))
    local.write_bytes(snapshot)
    return local.name, run(local)


def document_acquisition(url, final_url, body, snapshot, requests=1):
    """Return the exact static acquisition envelope of one converted URL document."""
    return {"mode": "static", "requested_url": url, "final_url": final_url, "effective_base_url": None,
            "url_sha256": sha256(url.encode()),
            "response": {"entity_bytes": len(body), "entity_sha256": sha256(body),
                         "body_bytes": len(body), "body_sha256": sha256(body)},
            "snapshot": {"kind": "document", "bytes": len(snapshot), "sha256": sha256(snapshot)},
            "engine": None, "capture_status": "static", "errors": [], "error_count": 0,
            "pending": PENDING, "counts": {"requests": requests, "promise_jobs": 0, "timer_callbacks": 0},
            "unsupported_resources": STATIC_RESOURCES, "refusal_stage": None,
            "soft_window_seconds": None, "soft_window_origin": None, "timeout_seconds": 30.0}


def refused(route, shown, url, stage, resource_url, **changes):
    """Return the exact static refusal receipt for `route`; `changes` replace acquisition keys."""
    acquisition = {
        "mode": "static", "requested_url": shown, "final_url": None, "effective_base_url": None,
        "url_sha256": sha256(url.encode()),
        "response": {"entity_bytes": None, "entity_sha256": None, "body_bytes": None, "body_sha256": None},
        "snapshot": {"kind": None, "bytes": None, "sha256": None}, "engine": None,
        "capture_status": "refused", "errors": [{"category": stage, "resource_url": resource_url}],
        "error_count": 1, "pending": PENDING, "counts": {"requests": 1, "promise_jobs": 0, "timer_callbacks": 0},
        "unsupported_resources": STATIC_RESOURCES, "refusal_stage": stage,
        "soft_window_seconds": None, "soft_window_origin": None, "timeout_seconds": 30.0}
    return {"file_ok": False, "route": route.name, "reason": "URL acquisition refused during %s" % stage,
            "source": shown, "out": None, "receipt_schema": "brewdoc.receipt/2",
            "unit_kind": route.unit_kind, "units": 0, "tables": 0, "text_regions": 0,
            "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
            "not_carried": list(route.not_carried), "acquisition": acquisition | changes}


def failing(make):
    """Return a double that raises a fresh exception from `make` on every call."""
    def fail(*args, **options):
        raise make()
    return fail


def dial(attempts, *outcomes):
    """Record each connect address and slice, then run the next outcome in order."""
    pending = list(outcomes)

    def connect(url, address, deadline, connect_deadline=None):
        attempts.append((address, connect_deadline))
        return pending.pop(0)()

    return connect


def connect_failure(htmlurl):
    """Return a double for a connect-phase failure as the fallback loop receives it."""
    return failing(lambda: htmlurl._error("URL connect failed", "connect"))


def served(body, headers=(HTML_TYPE,)):
    """Return a fresh connection double answering 200 with `body`."""
    response = Response(body, headers)
    response.status = 200
    return Connection(response)


class Unsent(Connection):
    """Fail while sending request bytes, before any response exists."""

    def request(self, *args, **options):
        raise BrokenPipeError("synthetic send failure")


class Reset(Response):
    """Fail the first body read like a peer reset after response headers."""

    status = 200

    def read(self, size):
        raise ConnectionResetError("synthetic reset")


class Upload(Connection):
    """Record POST bytes so a re-sent search body is visible."""

    def request(self, method, target, body, headers):
        self.requests.append((method, target, body))


@pytest.mark.parametrize("url,headers,build,suffix", [
    ("https://example.com/files/report", (("Content-Type", "application/pdf"),), pdf_bytes, ".pdf"),
    ("https://example.com/files/report", (("Content-Type", DOCX_TYPE),), docx_bytes, ".docx"),
    ("https://example.com/files/zones", (("Content-Type", XLSX_TYPE),), xlsx_bytes, ".xlsx"),
    ("https://example.com/download", (OCTET, ("Content-Disposition", 'attachment; filename="Report.PDF"')),
     pdf_bytes, ".pdf"),
    ("https://example.com/download", (OCTET, ("Content-Disposition",
     "attachment; filename*=UTF-8''%D0%BE%D1%82%D1%87%D0%B5%D1%82.docx")), docx_bytes, ".docx"),
    ("https://example.com/files/zones.XLSX", (OCTET,), xlsx_bytes, ".xlsx"),
    ("https://example.com/files/data.xlsx", (OCTET, ("Content-Disposition", 'attachment; filename="report.pdf"')),
     pdf_bytes, ".pdf"),
    ("https://example.com/notes", (("Content-Type", "text/plain; charset=utf-8"),), text_bytes, ".txt"),
    ("https://example.com/notes", (("Content-Type", "Text/Markdown"),), text_bytes, ".md"),
], ids=["pdf", "docx", "xlsx", "octet-disposition", "octet-rfc2231", "octet-url-suffix",
        "disposition-before-url", "text-plain", "text-markdown"])
def test_url_document_converts_through_its_detected_route_from_a_frozen_document_snapshot(
        htmlurl, monkeypatch, tmp_path, url, headers, build, suffix):
    # GIVEN one document response and its local conversion under the expected snapshot name
    body = build(tmp_path / ("source" + suffix))
    name, (code, receipt, markdown) = converted(tmp_path, url, suffix, body)
    calls = serve(htmlurl, monkeypatch, {url: ok(headers, body)})
    snapshots = record_snapshots(htmlurl, monkeypatch)
    assert code == 0, "the synthetic document must convert as a local file"
    # WHEN static URL acquisition receives the document
    result = htmlurl._run_url(url)
    # THEN the unchanged bytes convert through the detected route with a document receipt
    assert (result, calls, snapshots) == (
        (0, dict(receipt, source=url, receipt_schema="brewdoc.receipt/2",
                 acquisition=document_acquisition(url, url, body, body)), markdown),
        [url], [(name, body)],
    ), "URL documents must route by media type and convert their frozen bytes like a local file"


def test_url_document_with_render_js_records_javascript_as_not_applied(htmlurl, monkeypatch, tmp_path):
    # GIVEN a PDF response, an available JS runtime and an observable JS capture
    from brewdoc import htmljs

    url = "https://example.com/files/report"
    body = pdf_bytes(tmp_path)
    name, (code, receipt, markdown) = converted(tmp_path, url, ".pdf", body)
    serve(htmlurl, monkeypatch, {url: ok((("Content-Type", "application/pdf"),), body)})
    renders = []
    monkeypatch.setattr(htmljs, "_check_runtime", lambda **options: None)
    monkeypatch.setattr(htmljs, "render", lambda *args, **options: renders.append(args))
    acquisition = document_acquisition(url, url, body, body) | {
        "mode": "javascript", "capture_status": "not_applied", "soft_window_seconds": 12.0,
        "soft_window_origin": "request",
        "engine": {"name": "quickjs-ng", "version": None, "dom_name": "linkedom", "dom_version": None},
        "unsupported_resources": ["stylesheets", "images", "subframes", "media", "service_workers",
                                  "websockets", "web_rtc", "navigation", "layout", "canvas",
                                  "shadow_roots", "browser_state", "parser_blocking_order",
                                  "async_script_order"]}
    assert code == 0, "the synthetic PDF must convert as a local file"
    # WHEN JS acquisition receives a document
    result = htmlurl._run_url(url, render_js=True)
    # THEN no script runs and the receipt records JS as not applied
    assert (result, renders) == (
        (0, dict(receipt, source=url, receipt_schema="brewdoc.receipt/2", acquisition=acquisition), markdown), [],
    ), "documents must skip JS capture and say so in the receipt"


@pytest.mark.parametrize("headers,build,suffix", [
    ((("Content-Type", "application/pdf"),), docx_bytes, ".pdf"),
    ((("Content-Type", DOCX_TYPE),), pdf_bytes, ".docx"),
    ((("Content-Type", XLSX_TYPE),), lambda path: zones_book(path).read_bytes(), ".xlsx"),
    ((OCTET, ("Content-Disposition", 'attachment; filename="zones.xlsx"')), pdf_bytes, ".xlsx"),
], ids=["pdf-claims-zip", "docx-claims-pdf", "xlsx-without-content-types", "octet-xlsx-claims-pdf"])
def test_url_document_magic_mismatch_refuses_at_media_with_the_claimed_route(
        htmlurl, monkeypatch, tmp_path, headers, build, suffix):
    # GIVEN a response whose bytes contradict its claimed document type
    url = "https://example.com/files/report"
    body = build(tmp_path / ("source" + suffix))
    calls = serve(htmlurl, monkeypatch, {url: ok(headers, body)})
    assert calls == [], "acquisition starts without a request"
    # WHEN static URL acquisition classifies the response
    result = htmlurl._run_url(url)
    # THEN it refuses at media and reports the claimed document route and the failing URL
    assert (result, calls) == ((1, refused(ROUTES[suffix], url, url, "media", url), ""), [url]), \
        "magic mismatch must refuse at media under the detected route"


def test_url_document_with_article_refuses_at_options_after_classification(htmlurl, monkeypatch, tmp_path):
    # GIVEN a PDF response requested in article mode
    url = "https://example.com/files/report"
    body = pdf_bytes(tmp_path)
    calls = serve(htmlurl, monkeypatch, {url: ok((("Content-Type", "application/pdf"),), body)})
    response = {"entity_bytes": len(body), "entity_sha256": sha256(body),
                "body_bytes": len(body), "body_sha256": sha256(body)}
    assert calls == [], "acquisition starts without a request"
    # WHEN URL acquisition classifies the document
    result = htmlurl._run_url(url, article=True)
    # THEN article mode refuses with the trusted option message under the PDF route
    assert (result, calls) == ((1, dict(
        refused(ROUTES[".pdf"], url, url, "options", None, final_url=url, response=response),
        reason="--article is an HTML-only option"), ""), [url]), \
        "--article with a document URL must refuse at options with the detected route"


def test_url_text_transcoded_past_the_document_cap_refuses_at_resource(htmlurl, monkeypatch):
    # GIVEN a 50-byte windows-1252 text body that transcodes to 100 UTF-8 bytes over a 64-byte cap
    url = "https://example.com/notes"
    body = b"\xe9" * 50
    calls = serve(htmlurl, monkeypatch, {url: ok((("Content-Type", "text/plain; charset=windows-1252"),), body)})
    monkeypatch.setattr(htmlurl, "_AGGREGATE_LIMIT", 64)
    response = {"entity_bytes": len(body), "entity_sha256": sha256(body),
                "body_bytes": len(body), "body_sha256": sha256(body)}
    assert len(body.decode("cp1252").encode("utf-8")) == 100, "the transcoded snapshot must exceed the cap"
    # WHEN static URL acquisition freezes the text snapshot
    result = htmlurl._run_url(url)
    # THEN the oversized UTF-8 snapshot refuses at resource under the text route
    assert (result, calls) == (
        (1, refused(ROUTES[".txt"], url, url, "resource", url, final_url=url, response=response), ""), [url],
    ), "a transcoded text snapshot over the document cap must refuse at resource"


def test_main_document_response_reads_up_to_the_document_cap_chosen_from_headers(htmlurl, monkeypatch):
    # GIVEN a 100-byte PDF over the 64-byte page cap but within the 128-byte document cap
    body = b"%PDF-" + b"a" * 95
    connection = served(body, (("Content-Type", "application/pdf"), ("Content-Length", "100")))
    monkeypatch.setattr(htmlurl, "_BODY_LIMIT", 64)
    monkeypatch.setattr(htmlurl, "_AGGREGATE_LIMIT", 128)
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(htmlurl, "_connect", lambda *args: connection)
    assert len(body) == 100, "the body must sit between the two caps"
    # WHEN the main document request reads the response
    captured = htmlurl._request("https://example.com/report", time.monotonic() + 30, htmlurl._ACCEPT["html"])
    # THEN the document cap applies before the first read and the body is complete
    assert captured == {"status": 200, "headers": (("Content-Type", "application/pdf"), ("Content-Length", "100")),
                        "entity": body, "body": body}, "document media must read under the 32 MiB document cap"


def test_main_document_fetch_accepts_a_document_body_over_the_page_cap(htmlurl, monkeypatch):
    # GIVEN a 100-byte PDF response over the 64-byte page cap
    body = b"%PDF-" + b"a" * 95
    hop = ok((("Content-Type", "application/pdf"),), body)
    monkeypatch.setattr(htmlurl, "_request", lambda *args: dict(hop))
    monkeypatch.setattr(htmlurl, "_BODY_LIMIT", 64)
    assert len(body) == 100, "the body must exceed the page cap"
    # WHEN the main document fetch validates the response size
    captured = htmlurl._fetch("https://example.com/report")
    # THEN the post-read check uses the document cap for the main document
    assert captured == dict(hop, headers=(("Content-Type", "application/pdf"), ("Content-Length", "100")),
                            requested_url="https://example.com/report", final_url="https://example.com/report",
                            entity_sha256=sha256(body), body_sha256=sha256(body), redirects=()), \
        "the main document must not be refused by the 8 MiB page cap"


def test_redirect_to_an_octet_stream_document_routes_by_the_final_url_suffix(htmlurl, monkeypatch, tmp_path):
    # GIVEN a download URL redirecting to an octet-stream PDF path
    url = "https://example.com/download"
    final = "https://example.com/files/report.pdf"
    body = pdf_bytes(tmp_path)
    name, (code, receipt, markdown) = converted(tmp_path, url, ".pdf", body)
    calls = serve(htmlurl, monkeypatch, {url: moved("/files/report.pdf"), final: ok((OCTET,), body)})
    assert code == 0, "the synthetic PDF must convert as a local file"
    # WHEN static URL acquisition follows the redirect
    result = htmlurl._run_url(url)
    # THEN the final URL suffix routes the document and the snapshot keeps the requested URL hash
    assert (result, calls) == (
        (0, dict(receipt, source=url, receipt_schema="brewdoc.receipt/2",
                 acquisition=document_acquisition(url, final, body, body, requests=2)), markdown),
        [url, final],
    ), "a redirect to a document must convert it through the PDF route"


@pytest.mark.parametrize("location,target", [
    ("/a b", "https://example.com/a%20b"),
    ("\t /a b\r\n", "https://example.com/a%20b"),
    ("/caf\xc3\xa9 menu", "https://example.com/café%20menu"),
    ("/a\xa0b", "https://example.com/a%A0b"),
    ("/x　y", "https://example.com/x%E3%80%80y"),
], ids=["raw-space", "outer-whitespace", "utf8-bytes", "invalid-utf8-byte", "ideographic-space"])
def test_redirect_location_whitespace_is_percent_encoded_before_validation(htmlurl, monkeypatch, location, target):
    # GIVEN a redirect whose Location carries raw whitespace or header-decoded bytes
    start = "https://example.com/start"
    calls = serve(htmlurl, monkeypatch, {start: moved(location), target: ok((HTML_TYPE,), b"<p>x</p>")})
    assert calls == [], "acquisition starts without a request"
    # WHEN static acquisition follows the redirect
    captured = htmlurl._fetch(start)
    # THEN the target is normalized like a browser and then fetched
    assert (calls, captured["final_url"], captured["redirects"]) == ([start, target], target, (start,)), \
        "Location whitespace must be percent-encoded instead of refusing the redirect"


@pytest.mark.parametrize("hops,stage,resource_url,requests", [
    ({SECRET_URL: moved("/a%")}, "redirect", SECRET_SHOWN, 1),
    ({SECRET_URL: moved("/a\\b")}, "redirect", SECRET_SHOWN, 1),
    ({SECRET_URL: moved("https://user:pw@example.com/")}, "redirect", SECRET_SHOWN, 1),
    ({SECRET_URL: moved("http://example.com/plain")}, "redirect", SECRET_SHOWN, 1),
    ({SECRET_URL: moved("/next"), "https://example.com/next": dict(ok((HTML_TYPE,), b""), status=404)},
     "status", "https://example.com/next", 2),
    ({SECRET_URL: ok((("Content-Type", "application/zip"),), b"PK\x03\x04")}, "media", SECRET_SHOWN, 1),
    ({SECRET_URL: ok((OCTET, ("Content-Disposition", 'attachment; filename="a.pdf"'),
                      ("Content-Disposition", 'attachment; filename="b.pdf"')), b"%PDF-")},
     "media", SECRET_SHOWN, 1),
    ({SECRET_URL: ok((("Content-Type", "text/html; charset=x-bogus"),), b"<p>x</p>")}, "encoding", SECRET_SHOWN, 1),
    ({SECRET_URL: ok((("Content-Type", "text/html; charset=iso-2022-kr"),), b"<p>x</p>")},
     "encoding", SECRET_SHOWN, 1),
    ({SECRET_URL: ok((("Content-Type", "text/html; charset=utf-8; charset=koi8-r"),), b"<p>x</p>")},
     "encoding", SECRET_SHOWN, 1),
    ({SECRET_URL: ok((OCTET, ("Content-Disposition", MALFORMED_DISPOSITION)), b"%PDF-")}, "media", SECRET_SHOWN, 1),
    ({SECRET_URL: ok((OCTET, ("Content-Disposition", NUL_DISPOSITION)), b"%PDF-")}, "media", SECRET_SHOWN, 1),
], ids=["lone-percent", "backslash", "userinfo", "downgrade", "status-after-redirect", "unsupported-media",
        "duplicate-disposition", "unknown-charset", "replacement-charset", "two-charsets",
        "malformed-disposition", "nul-disposition"])
def test_url_refusal_reports_its_real_stage_and_redacted_failing_url(
        htmlurl, monkeypatch, hops, stage, resource_url, requests):
    # GIVEN a static URL whose response chain fails one acquisition policy
    calls = serve(htmlurl, monkeypatch, hops)
    assert calls == [], "acquisition starts without a request"
    # WHEN URL acquisition runs
    result = htmlurl._run_url(SECRET_URL)
    # THEN the refusal names its own stage and the redacted URL that failed
    assert result == (1, refused(ROUTES[".html"], SECRET_SHOWN, SECRET_URL, stage, resource_url, counts={
        "requests": requests, "promise_jobs": 0, "timer_callbacks": 0}), ""), \
        "every refusal must carry its real stage and redacted resource URL"


@pytest.mark.parametrize("connect,stage", [
    (failing(lambda: ConnectionRefusedError("synthetic refusal")), "connect"),
    (lambda *args: Unsent(None), "connect"),
    (lambda *args: Connection(Reset(b"", (HTML_TYPE,))), "bytes"),
    (failing(lambda: ssl.SSLCertVerificationError("synthetic certificate mismatch")), "tls"),
], ids=["tcp-refused", "request-send", "body-read", "certificate"])
def test_transport_refusal_reports_its_phase_and_redacted_failing_url(htmlurl, monkeypatch, connect, stage):
    # GIVEN one public address and a transport failing in one phase
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(htmlurl, "_connect", connect)
    assert htmlurl._public_address("93.184.216.34") is True, "the address must pass destination policy"
    # WHEN URL acquisition runs
    result = htmlurl._run_url(SECRET_URL)
    # THEN connect, body-read and TLS failures keep distinct stages and name the URL
    assert result == (1, refused(ROUTES[".html"], SECRET_SHOWN, SECRET_URL, stage, SECRET_SHOWN), ""), \
        "transport refusals must carry their phase and redacted resource URL"


def test_timeout_inside_fetch_names_the_redacted_url_being_fetched(htmlurl, monkeypatch):
    # GIVEN a redirect whose target request runs out of the overall deadline
    target = "https://example.com/next?token=secret"
    calls = []
    hops = {SECRET_URL: lambda: moved(target),
            target: failing(lambda: htmlurl._error("URL deadline exceeded", "timeout", htmlurl.URLTimeoutError))}
    monkeypatch.setattr(htmlurl, "_request", lambda url, *args, **options: (calls.append(url), hops[url]())[1])
    assert calls == [], "acquisition starts without a request"
    # WHEN URL acquisition runs
    result = htmlurl._run_url(SECRET_URL)
    # THEN the timeout refusal names the redacted hop that was being fetched
    assert (result, calls) == ((1, refused(ROUTES[".html"], SECRET_SHOWN, SECRET_URL, "timeout",
                                           "https://example.com/next?token=[redacted]", counts={
                                               "requests": 2, "promise_jobs": 0, "timer_callbacks": 0}), ""),
                               [SECRET_URL, target]), "a fetch timeout must carry the URL it was fetching"


def test_search_request_policy_refusal_carries_the_url_stage(htmlurl, monkeypatch):
    # GIVEN a search body outside the approved search profile
    calls = []
    monkeypatch.setattr(htmlurl, "_request", lambda *args, **options: calls.append(args))
    assert calls == [], "acquisition starts without a request"
    # WHEN guarded acquisition validates the search request
    with pytest.raises(htmlurl.URLPolicyError) as refusal:
        htmlurl._fetch("https://example.com/query", kind="api", search_body="{}")
    # THEN the policy refusal carries the url stage before any request
    assert (getattr(refusal.value, "acquisition_stage", None), calls) == ("url", []), \
        "search policy refusals must not fall back to the caller's stage"


def test_connect_phase_failure_moves_to_the_next_address_with_a_sliced_deadline(htmlurl, monkeypatch):
    # GIVEN two public addresses whose first refuses TCP connect
    attempts = []
    connection = served(b"<p>public</p>")
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("93.184.216.34", "8.8.8.8"))
    monkeypatch.setattr(htmlurl, "_connect", dial(attempts, connect_failure(htmlurl), lambda: connection))
    assert attempts == [], "no connect precedes the request"
    # WHEN one request runs with 30 seconds left
    captured = htmlurl._request("https://example.com/a", 130.0, "text/html")
    # THEN the first attempt gets a 3 s slice and the last gets the rest
    assert (attempts, [method for method, *_ in connection.requests], captured) == (
        [("93.184.216.34", 103.0), ("8.8.8.8", 130.0)], ["GET"],
        {"status": 200, "headers": (HTML_TYPE,), "entity": b"<p>public</p>", "body": b"<p>public</p>"},
    ), "a connect-phase failure must try the next validated address once"


def test_address_fallback_interleaves_families_and_stops_after_three_attempts(htmlurl, monkeypatch):
    # GIVEN three IPv6 and one IPv4 public address that all refuse TCP connect
    attempts = []
    refuse = connect_failure(htmlurl)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: (
        "2606:4700:4700::1111", "2001:4860:4860::8888", "2606:4700:4700::1001", "93.184.216.34"))
    monkeypatch.setattr(htmlurl, "_connect", dial(attempts, refuse, refuse, refuse, refuse))
    assert attempts == [], "no connect precedes the request"
    # WHEN one request runs with 30 seconds left
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._request("https://example.com/a", 130.0, "text/html")
    # THEN families interleave from the first answer, three attempts run and the stage stays connect
    assert (attempts, getattr(refusal.value, "acquisition_stage", None)) == (
        [("2606:4700:4700::1111", 103.0), ("93.184.216.34", 103.0), ("2001:4860:4860::8888", 130.0)], "connect",
    ), "address fallback must interleave families and cap attempts at three"


@pytest.mark.parametrize("make,stage", [
    (lambda htmlurl: ssl.SSLCertVerificationError("synthetic certificate mismatch"), "tls"),
    (lambda htmlurl: htmlurl._error("URL connection peer refused", "connect", htmlurl.URLPolicyError), "connect"),
    (lambda htmlurl: htmlurl._error("URL deadline exceeded", "timeout", htmlurl.URLTimeoutError), "timeout"),
], ids=["tls", "peer-mismatch", "overall-deadline"])
def test_failure_after_the_connect_phase_never_tries_another_address(htmlurl, monkeypatch, make, stage):
    # GIVEN two public addresses whose first fails after TCP connect or by policy
    attempts = []
    connection = served(b"<p>public</p>")
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("93.184.216.34", "8.8.8.8"))
    monkeypatch.setattr(htmlurl, "_connect", dial(attempts, failing(lambda: make(htmlurl)), lambda: connection))
    assert attempts == [], "no connect precedes the request"
    # WHEN one request runs with 30 seconds left
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._request("https://example.com/a", 130.0, "text/html")
    # THEN only the first sliced attempt runs and no request bytes are sent
    assert (attempts, getattr(refusal.value, "acquisition_stage", None), connection.requests) == (
        [("93.184.216.34", 103.0)], stage, [],
    ), "TLS, peer and deadline failures must never move to another address"


def test_search_post_is_sent_once_after_a_connect_phase_fallback(htmlurl, monkeypatch):
    # GIVEN two public addresses whose first refuses TCP connect and a search body
    attempts = []
    response = Response(b'{"hits":[]}', (("Content-Type", "application/json"),))
    response.status = 200
    connection = Upload(response)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("93.184.216.34", "8.8.8.8"))
    monkeypatch.setattr(htmlurl, "_connect", dial(attempts, connect_failure(htmlurl), lambda: connection))
    assert connection.requests == [], "no POST precedes the request"
    # WHEN the guarded POST runs
    captured = htmlurl._request("https://example.com/query", 130.0, "*/*", search_body=b'{"query":""}')
    # THEN the fallback happens before the POST, which is sent exactly once
    assert (attempts, connection.requests, captured["body"]) == (
        [("93.184.216.34", 103.0), ("8.8.8.8", 130.0)], [("POST", "/query", b'{"query":""}')], b'{"hits":[]}',
    ), "address fallback must end before request bytes so a POST is never re-sent"


def test_tcp_connect_failure_is_a_connect_stage_error_bounded_by_the_connect_slice(htmlurl, monkeypatch):
    # GIVEN a socket refusing TCP connect and a 3 s slice of a 30 s budget
    stream = Socket()

    def refuse(address):
        stream.connections.append(address)
        raise ConnectionRefusedError("synthetic refusal")

    monkeypatch.setattr(stream, "connect", refuse)
    monkeypatch.setattr(htmlurl.socket, "socket", lambda *args: stream)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    assert stream.connections == [], "connect starts without a socket connection"
    # WHEN the transport opens the numeric endpoint
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._connect("https://example.com/", "93.184.216.34", 130.0, 103.0)
    # THEN the failure is a plain connect-stage error and the socket timeout is the slice
    assert (type(refusal.value), getattr(refusal.value, "acquisition_stage", None),
            stream.connections, stream.timeouts, stream.closed) == (
        BrewdocError, "connect", [("93.184.216.34", 443)], [3.0], True,
    ), "TCP connect failures must be retryable connect errors bounded by the slice"


class Checked(Socket):
    """Reject socket timeouts exactly like a real socket and accept a watchdog shutdown."""

    def settimeout(self, value):
        with REAL_SOCKET() as probe:
            probe.settimeout(value)
        super().settimeout(value)

    def shutdown(self, how):
        pass


@pytest.mark.parametrize("now,error,stage", [
    (103.5, "BrewdocError", "connect"),
    (103.0, "BrewdocError", "connect"),
    (130.5, "URLTimeoutError", "timeout"),
], ids=["slice-expired", "slice-exhausted-exactly", "deadline-expired"])
def test_exhausted_connect_budget_refuses_before_the_socket_gets_a_timeout(htmlurl, monkeypatch, now, error, stage):
    # GIVEN a 3 s connect slice of a 30 s budget and a clock that reaches `now` at socket creation
    stream = Checked()
    clock = [100.0]

    def opened(*args):
        clock[0] = now
        return stream

    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(htmlurl.socket, "socket", opened)
    assert (stream.connections, stream.timeouts) == ([], []), "connect starts without socket activity"
    # WHEN the transport opens the numeric endpoint with no connect budget left
    with pytest.raises(BrewdocError) as refusal:
        htmlurl._connect("http://example.com/", "93.184.216.34", 130.0, 103.0)
    # THEN the slice gives a retryable connect error, the deadline a timeout; the socket is never used
    assert (type(refusal.value), getattr(refusal.value, "acquisition_stage", None),
            stream.connections, stream.timeouts, stream.closed) == (
        getattr(htmlurl, error), stage, [], [], True,
    ), "an exhausted connect budget must refuse at its stage, never reach socket.settimeout"


def test_connect_slice_bounds_only_tcp_connect_while_tls_keeps_the_remaining_deadline(htmlurl, monkeypatch):
    # GIVEN an approved address, a 3 s connect slice and a 30 s overall budget
    stream = Socket()
    monkeypatch.setattr(htmlurl.socket, "socket", lambda *args: stream)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", lambda context, sock, **options: sock)
    monkeypatch.setattr(htmlurl.time, "monotonic", lambda: 100.0)
    assert stream.timeouts == [], "connect starts without a socket timeout"
    # WHEN the transport connects and starts TLS
    connection = htmlurl._connect("https://example.com/", "93.184.216.34", 130.0, 103.0)
    connection.close()
    # THEN only TCP connect uses the slice; TLS keeps the full remaining deadline
    assert (stream.timeouts, stream.handshakes, stream.closed) == ([3.0, 30.0, 30.0], [30.0], True), \
        "the connect slice must not shorten the TLS handshake"


def test_main_document_request_offers_browser_accept_and_gzip_then_decodes_the_body(htmlurl, monkeypatch):
    # GIVEN a gzip-compressed HTML response behind one public address
    body = b"<p>public</p>"
    entity = gzip.compress(body, mtime=0)
    connection = served(entity, (HTML_TYPE, ("Content-Encoding", "gzip"), ("Content-Length", str(len(entity)))))
    monkeypatch.setattr(htmlurl, "_dns_lookup", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(htmlurl, "_connect", lambda *args: connection)
    assert len(entity) == 33, "the synthetic gzip entity must stay fixed"
    # WHEN static acquisition fetches the main document
    captured = htmlurl._fetch("https://example.com/page")
    # THEN the GET offers a browser-like Accept and gzip, and the body is decoded
    assert (connection.requests, captured) == (
        [("GET", "/page", {"Host": "example.com", "User-Agent": htmlurl._USER_AGENT,
                           "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                           "Accept-Encoding": "gzip", "Connection": "close"})],
        {"status": 200, "headers": (HTML_TYPE, ("Content-Length", "13")), "entity": entity, "body": body,
         "requested_url": "https://example.com/page", "final_url": "https://example.com/page",
         "entity_sha256": sha256(entity), "body_sha256": sha256(body), "redirects": ()},
    ), "the main request must negotiate documents and gzip like a browser"


@pytest.mark.parametrize("content_type,body", [
    ("text/plain", b"plain words"),
    ("text/html; charset=windows-1252", b"<p>caf\xe9</p>"),
    ("text/html", b'<meta charset="windows-1252"><p>caf\xe9</p>'),
], ids=["plain", "legacy-header", "legacy-declaration"])
def test_main_document_accepts_text_and_legacy_charset_html(htmlurl, monkeypatch, content_type, body):
    # GIVEN a text or legacy-charset HTML response
    hop = ok((("Content-Type", content_type),), body)
    monkeypatch.setattr(htmlurl, "_request", lambda *args: dict(hop))
    assert type(body) is bytes, "transport supplies raw bytes"
    # WHEN the main-document policy validates the response
    captured = htmlurl._fetch("https://example.com/page")
    # THEN the response is accepted with its original bytes
    assert captured == dict(hop, headers=(("Content-Type", content_type), ("Content-Length", str(len(body)))),
                            requested_url="https://example.com/page", final_url="https://example.com/page",
                            entity_sha256=sha256(body), body_sha256=sha256(body), redirects=()), \
        "text routes and declared legacy charsets must be accepted"


@pytest.mark.parametrize("content_type,body,suffix,snapshot", [
    ("text/html; charset=koi8-r", "<p>Привет</p>".encode("koi8_r"), ".html", PRIVET_HTML),
    ('text/html; charset="Windows-1251"', "<p>Привет</p>".encode("cp1251"), ".html", PRIVET_HTML),
    ("text/html; charset=windows-1251", '<meta charset="koi8-r"><p>Привет</p>'.encode("cp1251"),
     ".html", PRIVET_META_HTML),
    ("text/plain; charset=koi8-r", "Привет\n".encode("koi8_r"), ".txt", "Привет\n".encode()),
], ids=["koi8-r", "quoted-windows-1251", "header-over-meta", "plain-koi8-r"])
def test_url_header_charset_decodes_legacy_cyrillic_into_a_utf8_snapshot(
        htmlurl, monkeypatch, content_type, body, suffix, snapshot):
    # GIVEN a Cyrillic response declared by its HTTP charset label
    url = "https://example.com/news"
    serve(htmlurl, monkeypatch, {url: ok((("Content-Type", content_type),), body)})
    snapshots = record_snapshots(htmlurl, monkeypatch)
    assert (body.isascii(), body == snapshot) == (False, False), \
        "the non-ASCII response must differ from its UTF-8 snapshot"
    # WHEN static URL acquisition converts the page
    code, receipt, markdown = htmlurl._run_url(url)
    # THEN the header label decodes the page into a UTF-8 snapshot
    assert (code, receipt["acquisition"]["refusal_stage"], snapshots, markdown.splitlines()[-1:]) == (
        0, None, [("url-%s%s" % (sha256(url.encode())[:16], suffix), snapshot)], ["Привет"],
    ), "header charsets must decode legacy pages instead of refusing them"
