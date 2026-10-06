"""Check HTML-only innerText assignment without broadening pinned DOM getters."""

import html
import json
from pathlib import Path
import re
import sys
import time

import pytest

from brewdoc import htmljs
from brewdoc._urljs.native import Context
from test_html_js_bootstrap import ENGINE_AVAILABLE, _installed_bundle


pytestmark = pytest.mark.skipif(sys.platform != "linux" or not ENGINE_AVAILABLE,
                               reason="DOM/native proofs require the pinned engine in isolated Linux.")


def observe(script):
    """Return one page-produced JSON observation from actual frozen DOM capture."""
    source = '<html><body><p id="result">pending</p><script>"use strict";' + script + '</script></body></html>'
    captured = htmljs.render(source, "https://fixture.test/", lambda request: None,
                             time.monotonic() + 10, soft_window=0.1)
    assert (captured["capture_status"], captured["errors"]) == ("settled", []), "a bounded DOM consumer must settle without hidden script errors"
    matches = re.findall(r'<p id="result">([^<]*)</p>', captured["html"])
    assert len(matches) == 1, "the actual snapshot must retain exactly one observation node"
    return json.loads(html.unescape(matches[0]))


def test_strict_cleanup_consumer_assigns_inner_text_then_escapes_inner_html():
    # GIVEN the confirmed ordinary-div cleanup pattern in strict page code.
    script = """
const div=document.createElement('div');let result;
try{div.innerText='<em>a & b</em>';result={escaped:div.innerHTML,error:null};}
catch(error){result={escaped:div.innerHTML,error:[error.name,error.message]};}
document.querySelector('#result').textContent=JSON.stringify(result);
"""
    # WHEN the actual HTML element receives original text before serialization.
    result = observe(script)
    # THEN markup characters are escaped and no getter-only assignment error survives.
    assert result == {"escaped": "&lt;em&gt;a &amp; b&lt;/em&gt;", "error": None}, "the original cleanup pattern must produce escaped text rather than TypeError"


@pytest.mark.parametrize("text,children", [
    pytest.param("", [], id="empty"),
    pytest.param("a", [[3, "#text", "a"]], id="plain-text"),
    pytest.param("\n", [[1, "BR", None]], id="lf-only"),
    pytest.param("\r", [[1, "BR", None]], id="cr-only"),
    pytest.param("\r\n", [[1, "BR", None]], id="crlf-once"),
    pytest.param("\na\n", [[1, "BR", None], [3, "#text", "a"], [1, "BR", None]], id="leading-trailing-lf"),
    pytest.param("\n\n", [[1, "BR", None], [1, "BR", None]], id="consecutive-lf"),
    pytest.param("\r\r\n", [[1, "BR", None], [1, "BR", None]], id="cr-followed-by-crlf"),
    pytest.param("\n\r", [[1, "BR", None], [1, "BR", None]], id="lf-followed-by-cr"),
    pytest.param("a\r\nb\rc\nd", [[3, "#text", "a"], [1, "BR", None], [3, "#text", "b"],
                                      [1, "BR", None], [3, "#text", "c"], [1, "BR", None],
                                      [3, "#text", "d"]], id="mixed-breaks"),
    pytest.param(" \t🙂\ud800 ", [[3, "#text", " \t🙂\ud800 "]], id="original-whitespace-and-utf16"),
])
def test_inner_text_builds_exact_text_and_break_children_without_empty_text_nodes(text, children):
    # GIVEN old element and comment children to be fully replaced by original text.
    script = """
const div=document.createElement('div');div.innerHTML='<span>old</span><!--old-->';
const old=Array.from(div.childNodes);
div.innerText=""" + json.dumps(text) + """;
document.querySelector('#result').textContent=JSON.stringify({
  children:Array.from(div.childNodes,node=>[node.nodeType,node.nodeName,node.nodeValue]),
  detached:old.map(node=>node.parentNode===null)
});
"""
    # WHEN actual HTML assignment converts newline runs into a replacement fragment.
    result = observe(script)
    # THEN every break and nonempty original text run is represented exactly once.
    assert result == {"children": children, "detached": [True, True]}, "replacement must preserve UTF-16 and every break while detaching all old children"


@pytest.mark.parametrize("expression,children", [
    pytest.param("null", [], id="null-is-empty"),
    pytest.param("undefined", [[3, "#text", "undefined"]], id="undefined-is-text"),
    pytest.param("false", [[3, "#text", "false"]], id="boolean"),
    pytest.param("42", [[3, "#text", "42"]], id="number"),
])
def test_inner_text_domstring_conversion_retains_null_and_undefined_distinction(expression, children):
    # GIVEN a primitive value assigned to an existing ordinary HTML element.
    script = """
const div=document.createElement('div');div.textContent='old';div.innerText=""" + expression + """;
document.querySelector('#result').textContent=JSON.stringify(
  Array.from(div.childNodes,node=>[node.nodeType,node.nodeName,node.nodeValue]));
"""
    # WHEN the HTML setter performs its DOMString conversion.
    result = observe(script)
    # THEN only null is empty; other primitive values retain their standard string form.
    assert result == children, "DOMString conversion must not normalize undefined or false into empty text"


def test_inner_text_converts_once_before_replacing_children():
    # GIVEN a conversion hook that observes the original child and returns new text.
    script = r"""
const div=document.createElement('div');div.innerHTML='<span>old</span>';
const child=div.firstChild,seen=[];
div.innerText={toString(){seen.push([div.innerHTML,child.parentNode===div]);return 'new\ntext';}};
document.querySelector('#result').textContent=JSON.stringify({seen,html:div.innerHTML,detached:child.parentNode===null});
"""
    # WHEN conversion completes before the actual replacement.
    result = observe(script)
    # THEN the hook runs once with untouched children and the final fragment replaces them.
    assert result == {"seen": [["<span>old</span>", True]], "html": "new<br>text", "detached": True}, "conversion must finish once before any setter-owned child mutation"


@pytest.mark.parametrize("expression,name,same", [
    pytest.param("{toString(){throw original;}}", "RangeError", True, id="throwing-conversion"),
    pytest.param("Symbol('text')", "TypeError", False, id="direct-symbol"),
    pytest.param("{toString(){return Symbol('text');}}", "TypeError", False, id="converted-symbol"),
])
def test_inner_text_conversion_refusal_preserves_original_children_and_exception(expression, name, same):
    # GIVEN an input whose conversion cannot produce a DOMString.
    script = """
const div=document.createElement('div');div.innerHTML='<span>old</span><!--tail-->';
const children=Array.from(div.childNodes),original=new RangeError('synthetic conversion');let caught;
try{div.innerText=""" + expression + """;}catch(error){caught=[error.name,error===original];}
document.querySelector('#result').textContent=JSON.stringify({caught,html:div.innerHTML,
  retained:children.map((child,index)=>child.parentNode===div&&div.childNodes[index]===child)});
"""
    # WHEN the setter attempts strict conversion before constructing replacement children.
    result = observe(script)
    # THEN conversion failures retain the original exception and every old child identity.
    assert result == {"caught": [name, same], "html": "<span>old</span><!--tail-->", "retained": [True, True]}, "failed conversion must not clear, detach or replace any original child"


def test_inner_text_checks_html_receiver_before_calling_conversion():
    # GIVEN the actual HTML accessor borrowed for a non-HTML receiver.
    script = """
const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.textContent='old';
const setter=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'innerText').set;let calls=0,caught;
try{setter.call(svg,{toString(){calls++;return 'new';}});}catch(error){caught=error.name;}
document.querySelector('#result').textContent=JSON.stringify({calls,caught,text:svg.textContent});
"""
    # WHEN the borrowed setter receives an SVG node with an observable conversion hook.
    result = observe(script)
    # THEN receiver refusal precedes conversion and retains the original SVG text.
    assert result == {"calls": 0, "caught": "TypeError", "text": "old"}, "HTML receiver validation must precede conversion and cannot grant SVG assignment"


@pytest.mark.parametrize("tag", ["span", "button"])
def test_html_subclasses_inherit_inner_text_assignment(tag):
    # GIVEN actual HTML subclasses without their own innerText accessor override.
    script = """
const node=document.createElement(""" + json.dumps(tag) + r""");node.innerText='a\r\nb';
document.querySelector('#result').textContent=JSON.stringify({html:node.innerHTML,
  children:Array.from(node.childNodes,child=>[child.nodeType,child.nodeName,child.nodeValue])});
"""
    # WHEN strict assignment follows the ordinary HTML inheritance chain.
    result = observe(script)
    # THEN subclass children have the same original text and one CRLF break.
    assert result == {"html": "a<br>b", "children": [[3, "#text", "a"], [1, "BR", None], [3, "#text", "b"]]}, "ordinary HTML subclasses must inherit the HTML setter without requiring a per-tag shim"


def test_inner_text_preserves_installed_getter_style_override_svg_and_frozen_prototypes():
    # GIVEN the actual installed accessors captured before the real Boot freezes DOM roots.
    instrumentation = """
const own=Object.getOwnPropertyDescriptor.bind(Object);
const originalGet=own(Element$1.prototype,'innerText').get;
const originalStyle=own(HTMLStyleElement.prototype,'innerText');
const controls=document=>{
  const div=document.createElement('div');div.innerHTML=' a <span>b</span><div>c</div> d ';
  const style=document.createElement('style');style.textContent='p{color:red}';const sheet=style.sheet;
  style.innerText='p{color:blue}';
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.textContent='old';let caught;
  try{svg.innerText='new';}catch(error){caught=error.name;}
  return {getter:div.innerText,style:[style.innerText,style.innerHTML,style.sheet!==sheet],svg:[caught,svg.innerHTML]};
};
globalThis.__testOriginal=()=>JSON.stringify(controls(parseHTML('<html></html>').document));
globalThis.__testCurrent=()=>JSON.stringify({controls:controls(document),
  getterSame:own(HTMLElement.prototype,'innerText').get===originalGet,
  styleSame:[own(HTMLStyleElement.prototype,'innerText').get===originalStyle.get,
             own(HTMLStyleElement.prototype,'innerText').set===originalStyle.set],
  elementSetter:typeof own(Element$1.prototype,'innerText').set,
  frozen:[Object.isFrozen(HTMLElement.prototype),Object.isFrozen(Element$1.prototype),Object.isFrozen(Object.prototype)]});
"""
    glue = Path(htmljs.__file__).with_name("_urljs").joinpath("glue.js").read_text()
    with Context({}.__getitem__) as native:
        native.eval("(()=>{'use strict';" + _installed_bundle() + "\n" + instrumentation + "\n" + glue + "})();", "inner-text-counterpart")
        native.save(["__testOriginal", "__testCurrent", "__smallParseStart", "__smallParseEnd", "__smallBoot"])
        original = json.loads(native.call("__testOriginal"))
        assert original["style"] == ["p{color:blue}", "p{color:blue}", True], "the installed style override must invalidate its native cached stylesheet"
        assert original["svg"] == ["TypeError", "old"], "the installed SVG accessor must reject strict assignment"
        # WHEN the original parser and actual Boot install only the HTML setter.
        assert native.call("__smallParseStart", "<html><body></body></html>") == "0", "the bounded original document must parse completely"
        assert native.call("__smallParseEnd") == "1", "original parser finalization must complete before Boot"
        native.call("__smallBoot", "https://fixture.test/", json.dumps({"href": "https://fixture.test/", "origin": "https://fixture.test/"}))
        current = json.loads(native.call("__testCurrent"))
    # THEN native getters/overrides and SVG semantics remain exact while roots stay frozen.
    assert current == {"controls": original, "getterSame": True, "styleSame": [True, True],
                       "elementSetter": "undefined", "frozen": [True, True, True]}, "the HTML setter must preserve native accessor identities, specialized style behavior and frozen roots"
