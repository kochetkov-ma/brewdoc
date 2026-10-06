"""Check bounded initial parsing without changing the installed DOM semantics."""

import html
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import time

import pytest

from brewdoc import htmljs
from brewdoc.common import BrewdocError
from brewdoc._urljs.native import Context, NativeError

try:
    ENGINE_AVAILABLE = importlib.metadata.version("quickjs-ng") == "0.17.0.1"
except importlib.metadata.PackageNotFoundError:
    ENGINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not ENGINE_AVAILABLE,
    reason="Bootstrap regressions run only with the pinned engine in Linux isolation.",
)

_TREE = """
const tree=node=>[node.nodeType,node.nodeName,node.nodeValue,
  Array.from(node.attributes||[],attribute=>[attribute.name,attribute.value]),
  Array.from(node.childNodes,tree)];
"""
_PROBE = '<script id="probe">' + _TREE + """
document.querySelector('#probe').remove();
const result=document.querySelector('#result');
const observed=[document.toString(),tree(document)];
result.textContent=JSON.stringify(observed);
""" + "</script>"


def _installed_bundle():
    """Read the exact installed DOM implementation without its export statement."""
    worker = Path(htmljs.__file__).with_name("_vendor").joinpath("linkedom-worker.js").read_text()
    bundle, marker, suffix = worker.rpartition("\nexport {")
    assert marker == "\nexport {", "The comparison must load the real installed worker."
    assert suffix.endswith("};\n"), "The installed worker must retain its export boundary."
    return bundle


def _original_observation(source):
    """Describe the exact upstream parse before page bindings change prototypes."""
    probe = """
globalThis.original=source=>{
  const document=parseHTML(source).document;
  document.querySelector('#probe').remove();
""" + _TREE + "return JSON.stringify([document.toString(),tree(document)]);};"
    with Context({}.__getitem__) as native:
        native.eval("(()=>{" + _installed_bundle() + "\n" + probe + "})();", "original-parser")
        native.save(["original"])
        return json.loads(native.call("original", source))


def _page_observation(captured):
    """Read the page's unchanged DOM description from its frozen result node."""
    assert captured["errors"] == [], "The synthetic observation must have no script failures."
    matches = re.findall(r'<p id="result">([^<]*)</p>', captured["html"])
    assert len(matches) == 1, "The complete parsed document must contain one result node."
    return json.loads(html.unescape(matches[0]))


@pytest.mark.parametrize("source", ["...", "<!doctype html>", "<html><body>end</body></html>"])
def test_initial_parser_preserves_ellipsis_and_finalization(source):
    # GIVEN upstream inputs with distinct expansion and finalization behavior.
    probe = "globalThis.original=source=>parseHTML(source).document.toString();"
    with Context({}.__getitem__) as native:
        native.eval("(()=>{" + _installed_bundle() + "\n" + probe + "})();", "original-parser")
        native.save(["original"])
        expected = native.call("original", source)
    # WHEN the real initialization parses the original input under capture limits.
    captured = htmljs.render(source, "https://fixture.test/", lambda request: None,
                            time.monotonic() + 10, soft_window=1)
    # THEN the original exact-input branches and serialized final document survive.
    assert (captured["capture_status"], captured["errors"], captured["html"]) == (
        "settled", [], expected,
    ), "Initialization must retain the upstream ellipsis and finalization semantics."


def test_empty_input_remains_a_hard_capture_refusal():
    # GIVEN empty input that cannot produce a valid capture snapshot.
    source = ""
    # WHEN initialization reaches the existing nonblank snapshot check.
    with pytest.raises(BrewdocError, match="snapshot invalid") as caught:
        htmljs.render(source, "https://fixture.test/", lambda request: None,
                      time.monotonic() + 10, soft_window=1)
    # THEN the capture still refuses empty output under its original policy.
    assert type(caught.value) is BrewdocError, "Empty output must remain a controlled capture refusal."


@pytest.mark.parametrize("prefix,lead,fragment", [
    pytest.param("<section>", 2, "&amp; &#x1F642;</section>", id="entity"),
    pytest.param("<section>", 9, '<article data-q="left&amp;right">text</article></section>', id="quoted-attribute"),
    pytest.param("<section>", 3, "<!-- marker &amp; --></section>", id="comment"),
    pytest.param("<section><p>", 2, "</p></section>", id="closing-tag"),
    pytest.param('<script type="application/json">', 2, 'x<&amp;{"value":1}</script>', id="raw-script"),
    pytest.param("<style>", 2, "a{content:'<&amp;>'}</style>", id="raw-style"),
    pytest.param("<section>", 1, "🙂🌱&amp;</section>", id="astral-text"),
    pytest.param("<ul><li>", 2, "<li>second<ul><li>nested</ul></ul>", id="malformed-list"),
    pytest.param("<table><tbody><tr><td>", 2, "<td>second<tr><td>third</table>", id="malformed-table"),
    pytest.param("<svg><text>", 2, "&amp;🙂</text><foreignObject><p>HTML</p></foreignObject></svg>", id="svg-transition"),
    pytest.param("<section>", 0, "tail</section>", id="exact-checkpoint"),
    pytest.param("<section>", -17, "tail</section>", id="final-partial-checkpoint"),
])
def test_initial_parser_preserves_serialized_dom_and_node_boundaries(prefix, lead, fragment):
    # GIVEN synthetic tokens spanning the 16,384 UTF-16 checkpoint.
    opening = "<!doctype html><html><head></head><body>" + prefix
    source = opening + "x" * (16384 - len(opening) - lead) + fragment
    source += '<p id="result">pending</p>' + _PROBE + "</body></html>"
    expected = _original_observation(source)
    # WHEN the real capture initializes the same installed DOM and runs page code.
    captured = htmljs.render(source, "https://fixture.test/", lambda request: None,
                            time.monotonic() + 10, soft_window=1)
    # THEN serialization and every node, text boundary and attribute match upstream.
    assert (captured["capture_status"], _page_observation(captured)) == (
        "settled", expected,
    ), "Pausing initial parsing must preserve the full upstream DOM structure."


def test_large_initial_dom_finishes_before_page_code_can_observe_it():
    # GIVEN a bounded document containing 8,192 independently identified elements.
    rows = "".join(f'<p class="row" data-n="{number}">row {number}</p>' for number in range(8192))
    script = """
document.querySelector('#result').textContent=JSON.stringify({
  count:document.querySelectorAll('.row').length,
  last:document.querySelector('[data-n="8191"]').textContent,
  handles:['__smallParseStart','__smallParseResume','__smallParseEnd'].map(name=>typeof globalThis[name])
});
"""
    source = '<html><body>' + rows + '<p id="result">pending</p><script>' + script + '</script></body></html>'
    assert len(source.encode("utf-8")) < 8 * 1048576, "The witness must remain within the original input limit."
    # WHEN the original per-call interrupt and parent deadline guard initialization.
    captured = htmljs.render(source, "https://fixture.test/", lambda request: None,
                            time.monotonic() + 10, soft_window=1)
    # THEN page code sees every element and none of the trusted initialization handles.
    assert (captured["capture_status"], _page_observation(captured)) == (
        "settled", {"count": 8192, "last": "row 8191", "handles": ["undefined"] * 3},
    ), "Initial parsing must finish under existing limits before any page code runs."


_PAUSED_SOURCE = "<html><body><p>" + "x" * 20000 + "</p></body></html>"
_LOCATION = json.dumps({"href": "https://fixture.test/", "origin": "https://fixture.test/"})
_INITIALIZATION_HANDLES = ("__smallParseStart", "__smallParseResume", "__smallParseEnd")


@pytest.fixture
def initialization_native():
    """Observe real upstream parser calls and exact prototype restoration privately."""
    instrumentation = """
const own=Object.getOwnPropertyDescriptor.bind(Object);
const names=['write','end','resume'];
let writes=[],ends=0,failure='',observedParser;
for(const name of names){
  const descriptor=own(Parser$1.prototype,name);
  Object.defineProperty(Parser$1.prototype,name,{...descriptor,value:function(...args){
    if(name==='write'){writes.push(args[0]);observedParser=this;}
    if(name==='end')ends++;
    if(failure===name)throw new Error('synthetic parser '+name+' failure');
    return descriptor.value.apply(this,args);
  }});
}
const expected=names.map(name=>own(Parser$1.prototype,name));
globalThis.__testFail=name=>{failure=name;};
globalThis.__testReset=()=>{writes=[];ends=0;failure='';observedParser=undefined;};
globalThis.__testCursor=()=>observedParser.tokenizer.index;
globalThis.__testLockEnd=()=>{
  Object.defineProperty(Parser$1.prototype,'end',{writable:false,configurable:false});
  expected[1]=own(Parser$1.prototype,'end');
};
globalThis.__testState=()=>JSON.stringify({
  writes,ends,
  restored:names.map((name,index)=>{
    const actual=own(Parser$1.prototype,name),before=expected[index];
    return actual.value===before.value&&actual.writable===before.writable&&
      actual.enumerable===before.enumerable&&actual.configurable===before.configurable;
  }),
  globals:[typeof globalThis.document,typeof globalThis.window],
  handles:['__smallParseStart','__smallParseResume','__smallParseEnd'].map(name=>typeof globalThis[name])
});
globalThis.__testOriginal=source=>{
  const document=parseHTML(source).document;
""" + _TREE + "return JSON.stringify([document.toString(),tree(document)]);};" + """
globalThis.__testObserve=()=>{
""" + _TREE + "return JSON.stringify([document.toString(),tree(document)]);};"
    glue = Path(htmljs.__file__).with_name("_urljs").joinpath("glue.js").read_text()
    with Context({}.__getitem__) as native:
        native.eval("(()=>{" + _installed_bundle() + "\n" + instrumentation + "\n" + glue + "})();",
                    "observed-parser-bootstrap")
        native.save([*_INITIALIZATION_HANDLES, "__smallBoot", "__smallSnapshot", "__testFail",
                     "__testReset", "__testState", "__testOriginal", "__testObserve", "__testLockEnd",
                     "__testCursor"])
        yield native


@pytest.mark.parametrize("source,writes,ends", [
    pytest.param("", [], 0, id="exact-empty-upstream-document"),
    pytest.param("...", ["<!doctype html><html><head></head><body></body></html>"], 1,
                 id="exact-ellipsis-upstream-normalization"),
])
def test_private_finalization_preserves_exact_upstream_empty_and_ellipsis(initialization_native, source, writes, ends):
    # GIVEN the upstream document and real write/end instrumentation before initialization.
    native = initialization_native
    expected = json.loads(native.call("__testOriginal", source))
    native.call("__testReset")
    # WHEN the exact drained input is finalized once and then bound to page globals.
    status = native.call("__smallParseStart", source)
    finalized = native.call("__smallParseEnd")
    native.call("__smallBoot", "https://fixture.test/", _LOCATION)
    # THEN exact DOM parity, original normalization and descriptor restoration survive.
    assert (status, finalized, json.loads(native.call("__testObserve")),
            json.loads(native.call("__testState"))) == (
        "0", "1", expected, {"writes": writes, "ends": ends, "restored": [True] * 3,
                               "globals": ["object", "object"], "handles": ["undefined"] * 3},
    ), "Private finalization must preserve upstream branches before publishing the view."


def test_paused_initialization_never_publishes_a_partial_dom(initialization_native):
    # GIVEN one source that crosses a checkpoint before all closing tags are parsed.
    native = initialization_native
    # WHEN the original full write pauses with more input remaining.
    status = native.call("__smallParseStart", _PAUSED_SOURCE)
    # THEN parser state stays private and prototype interception has already been restored.
    assert (status, json.loads(native.call("__testState"))) == (
        "1", {"writes": [_PAUSED_SOURCE], "ends": 0, "restored": [True] * 3,
              "globals": ["undefined"] * 2, "handles": ["undefined"] * 3},
    ), "A checkpoint must never make its incomplete DOM or initialization handles public."


def test_resumed_parser_writes_original_source_once_and_finalizes_once(initialization_native):
    # GIVEN one checkpointed source and its complete original upstream tree.
    native = initialization_native
    expected = json.loads(native.call("__testOriginal", _PAUSED_SOURCE))
    native.call("__testReset")
    assert native.call("__smallParseStart", _PAUSED_SOURCE) == "1", "The source must pause before completion."
    # WHEN the same parser resumes, finalizes and binds the complete view.
    resumed = native.call("__smallParseResume")
    finalized = native.call("__smallParseEnd")
    native.call("__smallBoot", "https://fixture.test/", _LOCATION)
    # THEN no input is replayed, end fires once and text-node boundaries are exact.
    assert (resumed, finalized, json.loads(native.call("__testObserve")),
            json.loads(native.call("__testState"))) == (
        "0", "1", expected, {"writes": [_PAUSED_SOURCE], "ends": 1, "restored": [True] * 3,
                               "globals": ["object"] * 2, "handles": ["undefined"] * 3},
    ), "Resume must retain one parser and one original write rather than reparsing fragments."


@pytest.mark.parametrize("setup,operation,arguments", [
    pytest.param([], "Boot", ("https://fixture.test/", _LOCATION), id="boot-before-start"),
    pytest.param([], "Snapshot", (), id="snapshot-before-start"),
    pytest.param([("ParseStart", (_PAUSED_SOURCE,))], "Boot", ("https://fixture.test/", _LOCATION),
                 id="boot-before-drain"),
    pytest.param([("ParseStart", (_PAUSED_SOURCE,))], "Snapshot", (), id="snapshot-before-drain"),
    pytest.param([("ParseStart", (_PAUSED_SOURCE,))], "ParseEnd", (), id="end-before-drain"),
    pytest.param([("ParseStart", (_PAUSED_SOURCE,))], "ParseStart", (_PAUSED_SOURCE,), id="duplicate-start"),
    pytest.param([("ParseStart", ("<p>complete</p>",))], "ParseResume", (), id="resume-after-drain"),
    pytest.param([("ParseStart", ("<p>complete</p>",)), ("ParseEnd", ())], "ParseEnd", (), id="duplicate-end"),
])
def test_invalid_initialization_transition_never_exposes_a_view(initialization_native, setup, operation, arguments):
    # GIVEN an exact private protocol state that cannot permit the requested operation.
    native = initialization_native
    for name, values in setup:
        native.call("__small" + name, *values)
    # WHEN the illegal initialization or publication step is attempted.
    with pytest.raises(NativeError):
        native.call("__small" + operation, *arguments)
    # THEN no window or document is published by the rejected transition.
    assert json.loads(native.call("__testState"))["globals"] == ["undefined"] * 2, (
        "An invalid private transition must never publish an incomplete view."
    )


@pytest.mark.parametrize("phase,setup,operation,arguments,ends", [
    pytest.param("write", [], "ParseStart", (_PAUSED_SOURCE,), 0, id="start-write-failure"),
    pytest.param("resume", [("ParseStart", (_PAUSED_SOURCE,))], "ParseResume", (), 0, id="resume-failure"),
    pytest.param("end", [("ParseStart", ("<p>complete</p>",))], "ParseEnd", (), 1, id="end-failure"),
])
def test_initialization_failure_restores_descriptors_and_cannot_be_retried(initialization_native, phase, setup,
                                                                       operation, arguments, ends):
    # GIVEN a real upstream parser method that fails during a specific initialization phase.
    native = initialization_native
    for name, values in setup:
        native.call("__small" + name, *values)
    native.call("__testFail", phase)
    # WHEN its original write, resume or end raises and the caller tries to recover a view.
    with pytest.raises(NativeError, match="synthetic parser"):
        native.call("__small" + operation, *arguments)
    native.call("__testFail", "")
    with pytest.raises(NativeError):
        native.call("__smallParseStart", "<p>replacement</p>")
    with pytest.raises(NativeError):
        native.call("__smallParseResume")
    with pytest.raises(NativeError):
        native.call("__smallParseEnd")
    with pytest.raises(NativeError):
        native.call("__smallBoot", "https://fixture.test/", _LOCATION)
    with pytest.raises(NativeError):
        native.call("__smallSnapshot")
    # THEN all descriptors are restored, end is never replayed and no partial DOM escapes.
    state = json.loads(native.call("__testState"))
    assert (state["ends"], state["restored"], state["globals"], state["handles"]) == (
        ends, [True] * 3, ["undefined"] * 2, ["undefined"] * 3,
    ), "A failed initialization must stay unusable after exact descriptor restoration."


def test_failed_parser_interception_restores_the_first_descriptor(initialization_native):
    # GIVEN an immutable end method that prevents the second parser interception.
    native = initialization_native
    native.call("__testLockEnd")
    # WHEN initialization fails before the original write or end can run.
    with pytest.raises(NativeError):
        native.call("__smallParseStart", "<p>complete</p>")
    with pytest.raises(NativeError):
        native.call("__smallBoot", "https://fixture.test/", _LOCATION)
    # THEN the first intercepted descriptor is restored and no parser view escapes.
    assert json.loads(native.call("__testState")) == {
        "writes": [], "ends": 0, "restored": [True] * 3,
        "globals": ["undefined"] * 2, "handles": ["undefined"] * 3,
    }, "Failure during interception must restore every descriptor already changed."


def test_resume_progresses_after_comment_scan_jumps_over_multiple_checkpoints(initialization_native):
    # GIVEN a comment whose upstream fast scan jumps beyond four cursor checkpoints.
    native = initialization_native
    source = "<html><body><!--" + "x" * 65536 + "--><p>tail</p></body></html>"
    expected = json.loads(native.call("__testOriginal", source))
    native.call("__testReset")
    assert native.call("__smallParseStart", source) == "1", "The source must pause after its long comment."
    before = int(native.call("__testCursor"))
    assert before > 65536, "The upstream comment scan must actually cross multiple checkpoints."
    # WHEN one pending resume advances from the actual tokenizer cursor.
    resumed = native.call("__smallParseResume")
    after = int(native.call("__testCursor"))
    # THEN remaining input drains without replaying a stale checkpoint.
    assert (resumed, after) == ("0", len(source)), (
        f"Pending resume must advance from cursor {before}, not repeat that cursor with pending work."
    )
    native.call("__smallParseEnd")
    native.call("__smallBoot", "https://fixture.test/", _LOCATION)
    assert json.loads(native.call("__testObserve")) == expected, (
        "Advancing after a fast scan must preserve exact upstream serialization and node boundaries."
    )
