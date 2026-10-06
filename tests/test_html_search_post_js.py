"""Check original XHR arguments across the isolated JavaScript and parent boundary."""

import json
import sys
import time

import pytest

from brewdoc import htmljs
from test_html_search_post import BODY, DOCUMENT, FORM, SEARCH, api_response, search_body, sized_body, trusted_search_key
from test_html_timeout import _replace_worker
from brewdoc.common import BrewdocError


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="XHR/native proofs require isolated Linux.")


def test_fresh_xhr_has_standard_boolean_false_credentials_default(trusted_search_key):
    # GIVEN a fresh XHR without any page assignment to its credential flag.
    script = "const x=new XMLHttpRequest();document.querySelector('p').textContent=JSON.stringify([typeof x.withCredentials,x.withCredentials]);"
    source = "<html><body><p>pending</p><script>" + script + "</script></body></html>"
    # WHEN the real constructor publishes its standard default.
    captured = htmljs.render(source, DOCUMENT, lambda request: None, time.monotonic() + 10, soft_window=0.1)
    # THEN absent or non-Boolean credentials cannot masquerade as false.
    assert (captured["html"], captured["capture_status"], captured["errors"]) == (
        '<html><body><p>["boolean",false]</p><script>' + script + '</script></body></html>', "settled", [],
    ), "every new XHR must expose a genuine false Boolean before send"


def test_search_xhr_preserves_the_original_string_argument_through_ipc_and_parent(trusted_search_key):
    # GIVEN an original primitive body with Unicode and noncanonical JSON whitespace.
    requests = []
    script = "const x=new XMLHttpRequest();x.onload=()=>document.querySelector('p').textContent=x.responseText;"
    script += "x.open('POST'," + json.dumps(SEARCH) + ");x.setRequestHeader('content-type'," + json.dumps(FORM) + ");x.send(" + json.dumps(BODY) + ");"
    source = "<html><body><p>pending</p><script>" + script + "</script></body></html>"

    def fetch(request):
        """Observe the real sanitized request without bypassing parent validation."""
        requests.append(request)
        return api_response()

    assert requests == [], "no request may precede original XHR execution"
    # WHEN the real XHR sends through native scheduling, IPC and parent validation.
    captured = htmljs.render(source, DOCUMENT, fetch, time.monotonic() + 10, soft_window=0.1)
    # THEN page input bytes and supported credential/form fields survive unchanged.
    assert (captured["html"], captured["capture_status"], captured["errors"], requests) == (
        '<html><body><p>synthetic reply</p><script>' + script + '</script></body></html>', "settled", [],
        [{"url": SEARCH, "kind": "api", "origin": "https://hn.algolia.com", "requested_with": False,
          "method": "POST", "body": BODY, "content_type": FORM}],
    ), "the original XHR string must reach host acquisition without JSON normalization or coercion"


@pytest.mark.parametrize("assignment", [
    "x.withCredentials=true", "x.withCredentials=null", "x.withCredentials=0",
    "x.withCredentials='false'", "delete x.withCredentials",
], ids=["true", "null", "zero", "string-false", "deleted"])
def test_page_credentials_outside_boolean_false_preserve_dom_without_transport(assignment, trusted_search_key):
    # GIVEN useful committed content and a page credential value outside Boolean false.
    requests = []
    script = "const x=new XMLHttpRequest();x.open('POST'," + json.dumps(SEARCH) + ");"
    script += "x.setRequestHeader('content-type'," + json.dumps(FORM) + ");" + assignment + ";x.send(" + json.dumps(BODY) + ");"
    source = "<html><body><p>Committed article</p><script>" + script + "</script></body></html>"
    # WHEN the original XHR reads that value without truthiness normalization.
    captured = htmljs.render(source, DOCUMENT, requests.append, time.monotonic() + 10, soft_window=0.1)
    # THEN unsupported page credentials cannot acquire data or erase available DOM.
    assert (captured["html"], captured["capture_status"], requests) == (
        source, "partial", [],
    ), "page true/non-Boolean credentials must preserve committed DOM with zero host acquisition"


@pytest.mark.parametrize("expression", [
    "fetch(url,{method:'POST',body:body,xhr:true,with_credentials:false})",
    "fetch(url,{method:'POST',body:body},{xhr:true,with_credentials:false})",
])
def test_public_fetch_cannot_forge_the_private_xhr_search_permission(expression, trusted_search_key):
    # GIVEN a page-created marker in fetch options or its otherwise ignored third argument.
    requests = []
    script = "const url=" + json.dumps(SEARCH) + ";const body=" + json.dumps(BODY) + ";" + expression + ".catch(()=>{});"
    source = "<html><body><p>Committed article</p><script>" + script + "</script></body></html>"
    # WHEN the public fetch wrapper receives an attempted XHR impersonation.
    captured = htmljs.render(source, DOCUMENT, requests.append, time.monotonic() + 10, soft_window=0.1)
    # THEN only internal original XHR creation can claim the profile consumer.
    assert (captured["html"], captured["capture_status"], requests) == (source, "partial", []), "public fetch markers must not reach the approved POST transport"


@pytest.mark.parametrize("name", ["content-type", "Content-Type"], ids=["same-case-repeat", "case-collision"])
def test_xhr_header_repetition_cannot_disappear_before_parent_validation(name, trusted_search_key):
    # GIVEN two original setRequestHeader calls targeting the same normalized name.
    requests = []
    script = "const x=new XMLHttpRequest();x.open('POST'," + json.dumps(SEARCH) + ");"
    script += "x.setRequestHeader('content-type'," + json.dumps(FORM) + ");x.setRequestHeader(" + json.dumps(name) + "," + json.dumps(FORM) + ");x.send(" + json.dumps(BODY) + ");"
    source = "<html><body><p>Committed article</p><script>" + script + "</script></body></html>"
    # WHEN the original XHR path reaches finite header admission.
    captured = htmljs.render(source, DOCUMENT, requests.append, time.monotonic() + 10, soft_window=0.1)
    # THEN duplicate evidence survives normalization and forbids any acquisition.
    assert (captured["html"], captured["capture_status"], requests) == (source, "partial", []), "duplicate XHR headers must not collapse into one admitted form header"


@pytest.mark.parametrize("value", ["0", "None", "'false'"], ids=["integer", "null", "string"])
def test_forged_worker_credentials_are_hard_and_owned_child_is_reaped(value, trusted_search_key, monkeypatch):
    # GIVEN a synthetic trusted worker emitting a malformed private credential type.
    from test_html_search_post import search_record
    request = search_record()
    encoded = repr(request)
    source = """
import sys,threading
from multiprocessing.connection import Connection
from brewdoc.htmljs import _send,_receive
channel=Connection(int(sys.argv[1]));_receive(channel)
request=REQUEST
request['with_credentials']=VALUE
_send(channel,{'kind':'request','request':request})
threading.Event().wait(60)
""".replace("REQUEST", encoded).replace("VALUE", value)
    processes = _replace_worker(monkeypatch, source)
    acquired = []
    assert (processes, acquired) == ([], []), "forged IPC starts without an owned worker or acquisition"
    # WHEN parent validation rejects an actual primitive-type protocol violation.
    with pytest.raises(BrewdocError):
        htmljs.render("<html><body><p>Committed article</p></body></html>", DOCUMENT, acquired.append,
                      time.monotonic() + 10, soft_window=0.1)
    # THEN hard refusal closes IPC and reaps the owned still-running worker.
    assert (acquired, [process.returncode for process in processes]) == ([], [-9]), "forged search IPC must refuse hard and reap its own child"


@pytest.mark.parametrize("body", [
    pytest.param(sized_body(16385), id="body-overflow"),
    pytest.param(search_body(tagFilters=[[[["story"]]]]), id="depth-overflow"),
    pytest.param(search_body(analyticsTags=["x"] * 254), id="array-entry-overflow"),
])
def test_caught_search_resource_excess_cannot_publish_committed_dom_or_timeout_fallback(body, trusted_search_key, monkeypatch, tmp_path):
    # GIVEN committed source, prior output and page code catching a resource failure.
    from brewdoc import htmlurl
    from test_html_timeout import _html_response
    script = "const x=new XMLHttpRequest();x.open('POST'," + json.dumps(SEARCH) + ");x.setRequestHeader('content-type'," + json.dumps(FORM) + ");"
    script += "try{x.send(" + json.dumps(body) + ")}catch(error){};while(true){}"
    source = ("<html><body><p>Committed article</p><script>" + script + "</script></body></html>").encode()
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    requests = []

    def fetch(url, **options):
        """Acquire only original HTML with the trusted document origin intact."""
        requests.append(url)
        return {**_html_response(source, **options), "final_url": url}

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert output.read_bytes() == b"previous output\n", "hard resource checks start with exact prior output"
    # WHEN the real worker queues a caught excess then reaches native timing stop.
    code, receipt, markdown = htmlurl._run_url(DOCUMENT, output, render_js=True, timeout=1)
    # THEN sticky resource refusal takes precedence and preserves the output transaction.
    assert (code, markdown, receipt["acquisition"]["capture_status"], output.read_bytes(), requests) == (
        1, "", "refused", b"previous output\n", [DOCUMENT],
    ), "caught search resource violations must prohibit DOM or original-source timing fallback"


@pytest.mark.parametrize("argument", [
    pytest.param("null", id="null-body"),
    pytest.param("0", id="number-body"),
    pytest.param("({toString(){document.querySelector('p').textContent='coerced';return 'forged'}})", id="object-with-coercion"),
])
def test_nonprimitive_page_body_is_not_coerced_or_sent(argument, trusted_search_key):
    # GIVEN a page value whose conversion would mutate committed content.
    requests = []
    script = "const x=new XMLHttpRequest();x.open('POST'," + json.dumps(SEARCH) + ");x.setRequestHeader('content-type'," + json.dumps(FORM) + ");x.send(" + argument + ");"
    source = "<html><body><p>Committed article</p><script>" + script + "</script></body></html>"
    # WHEN original XHR attempts to send a non-string body.
    captured = htmljs.render(source, DOCUMENT, requests.append, time.monotonic() + 10, soft_window=0.1)
    # THEN no toString callback can supply a replacement request or alter the DOM.
    assert (captured["html"], captured["capture_status"], requests) == (source, "partial", []), "XHR must refuse nonprimitive bodies without coercion or transport"


def test_page_lone_surrogate_body_is_partial_and_zero_wire_instead_of_ipc_corruption(trusted_search_key):
    # GIVEN a valid page string with no strict UTF-8 byte representation.
    body = BODY.replace("питон 🙂", "\ud800")
    requests = []
    script = "const x=new XMLHttpRequest();x.open('POST'," + json.dumps(SEARCH) + ");x.setRequestHeader('content-type'," + json.dumps(FORM) + ");x.send(" + json.dumps(body) + ");"
    source = "<html><body><p>Committed article</p><script>" + script + "</script></body></html>"
    assert body.count("\ud800") == 1, "the original body must contain exactly one unpaired surrogate"
    # WHEN strict UTF-8 admission refuses that page string before IPC encoding.
    captured = htmljs.render(source, DOCUMENT, requests.append, time.monotonic() + 10, soft_window=0.1)
    # THEN original content remains partial with no replacement bytes or acquisition.
    assert (captured["html"], captured["capture_status"], captured["errors"], requests) == (
        source, "partial", [{"category": "request_options_unsupported", "resource_url": None}], [],
    ), "a lone-surrogate page body must be unsupported partial rather than hard IPC encoding failure"


def test_timing_stop_blocks_never_admitted_search_without_transport_or_quota_charge(trusted_search_key, monkeypatch):
    # GIVEN observed timing stop before a new search reaches shared-budget admission.
    from brewdoc import htmlurl
    from test_html_search_post import search_record
    from test_html_timeout import _html_response
    raw = repr(search_record())
    worker = """
import sys,time
from multiprocessing.connection import Connection
from brewdoc.htmljs import _send,_receive
channel=Connection(int(sys.argv[1]));bootstrap=_receive(channel)
_send(channel,{'kind':'timing','recovery_deadline':time.monotonic()+0.5})
_send(channel,{'kind':'request','request':REQUEST})
assert _receive(channel)=={'timing':True}, 'timing stop must refuse new acquisition'
_send(channel,{'kind':'error','cause':'timing'})
""".replace("REQUEST", raw)
    processes = _replace_worker(monkeypatch, worker)
    calls = []
    budgets = []

    def fetch(url, **options):
        """Record only source acquisition and expose a known exhausted shared budget."""
        calls.append(url)
        budget = options["budget"]
        response = _html_response(b"<html><body><p>Initial article</p></body></html>", **options)
        response["final_url"] = url
        budget.update(accepted_requests=100, decoded_bytes=32 * 1048576)
        budgets.append(budget)
        assert (response["final_url"], budget) == (
            DOCUMENT, {"accepted_requests": 100, "decoded_bytes": 32 * 1048576},
        ), "source acquisition must establish the trusted document origin before exhausted-budget recovery"
        return response

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert (processes, calls, budgets) == ([], [], []), "the recovery-blocked search starts without admission"
    preflight = []

    def admissible(record):
        """Verify real profile admission without consuming the shared recovery budget."""
        preflight.append(record)
        return api_response()

    response = htmljs._host_response(search_record(), admissible, DOCUMENT, DOCUMENT)
    assert (response["body"], [record["origin"] for record in preflight]) == (
        "synthetic reply", ["https://hn.algolia.com"],
    ), "the exact original record must be admissible under the trusted document origin before timing stop"
    # WHEN the real parent blocks acquisition before calling the shared-budget owner.
    code, receipt, markdown = htmlurl._run_url(DOCUMENT, render_js=True)
    # THEN a never-admitted search remains timing partial with unchanged counters.
    assert (code, receipt["acquisition"]["capture_status"], receipt["acquisition"]["snapshot"]["kind"],
            calls, budgets, [process.returncode for process in processes]) == (
        0, "partial", "initial_html_timeout_fallback", [DOCUMENT],
        [{"accepted_requests": 100, "decoded_bytes": 32 * 1048576}], [0],
    ), "recovery-blocked POST must make zero new acquisitions or quota charges rather than invent a resource admission"


def test_deep_valid_search_json_under_the_byte_limit_remains_hard_when_caught(trusted_search_key, monkeypatch, tmp_path):
    # GIVEN syntactically valid array nesting below the byte cap and prior output.
    from brewdoc import htmlurl
    from test_html_timeout import _html_response
    body = search_body(tagFilters=[]).replace('"tagFilters":[]', '"tagFilters":' + '[' * 2000 + '"story"' + ']' * 2000)
    output = tmp_path / "article.md"
    output.write_bytes(b"previous output\n")
    script = "const x=new XMLHttpRequest();x.open('POST'," + json.dumps(SEARCH) + ");x.setRequestHeader('content-type'," + json.dumps(FORM) + ");"
    script += "try{x.send(" + json.dumps(body) + ")}catch(error){};while(true){}"
    source = ("<html><body><p>Committed article</p><script>" + script + "</script></body></html>").encode()
    requests = []

    def fetch(url, **options):
        """Supply trusted original HTML while exposing any unwanted API acquisition."""
        requests.append(url)
        return {**_html_response(source, **options), "final_url": DOCUMENT}

    monkeypatch.setattr(htmlurl, "_fetch", fetch)
    assert (body.count("["), body.count("]"), len(body.encode("utf-8")) < 16384) == (
        2003, 2003, True,
    ), "balanced two-thousand-deep arrays must remain valid syntax below the original body byte cap"
    # WHEN page code catches send failure then loops within the same callback.
    code, receipt, markdown = htmlurl._run_url(DOCUMENT, output, render_js=True, timeout=1)
    # THEN native/depth/resource refusal remains hard with no API or output mutation.
    assert (code, markdown, receipt["acquisition"]["capture_status"], requests, output.read_bytes()) == (
        1, "", "refused", [DOCUMENT], b"previous output\n",
    ), "deep valid search JSON must not become partial content after caught parser failure or timing"


def test_ordinary_malformed_page_json_stays_partial_and_zero_wire(trusted_search_key):
    # GIVEN a primitive page string with ordinary malformed JSON rather than overflow.
    requests = []
    script = "const x=new XMLHttpRequest();x.open('POST'," + json.dumps(SEARCH) + ");x.setRequestHeader('content-type'," + json.dumps(FORM) + ");x.send('{invalid');"
    source = "<html><body><p>Committed article</p><script>" + script + "</script></body></html>"
    # WHEN finite body validation rejects its malformed syntax.
    captured = htmljs.render(source, DOCUMENT, requests.append, time.monotonic() + 10, soft_window=0.1)
    # THEN ordinary unsupported input does not become a resource or protocol failure.
    assert (captured["html"], captured["capture_status"], requests) == (source, "partial", []), "ordinary malformed page JSON must preserve useful content without acquisition"
