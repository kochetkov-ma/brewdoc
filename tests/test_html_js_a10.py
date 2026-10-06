"""Check finite page compatibility without weakening protected host capabilities."""

import importlib.metadata
import json
import re
import subprocess
import sys
import time

import pytest

from brewdoc.common import BrewdocError
from brewdoc.htmljs import render

try:
    ENGINE_AVAILABLE = importlib.metadata.version("quickjs-ng") == "0.17.0.1"
except importlib.metadata.PackageNotFoundError:
    ENGINE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not ENGINE_AVAILABLE,
    reason="A10 native regressions require the installed engine in Linux isolation.",
)


def _observation(captured):
    """Read page-produced JSON from the real frozen result element."""
    assert captured["errors"] == [], "ordinary fixture scripts must execute without capture errors"
    matches = re.findall(r'<p id="result">([^<]*)</p>', captured["html"])
    assert len(matches) == 1, "the frozen DOM must contain exactly one result element"
    return captured["capture_status"], json.loads(matches[0])


@pytest.mark.parametrize("parent,expected", [
    pytest.param("document", {
        "direct": ["html", "head", "body", "root", "first", "nested", "last", "result", "probe"],
        "wildcard": ["html", "head", "body", "root", "first", "nested", "last", "result", "probe"],
        "named": ["first", "last"], "uppercase": ["first", "last"], "missing": [],
    }, id="document-descendants"),
    pytest.param("document.querySelector('#root')", {
        "direct": ["first", "nested", "last"], "wildcard": ["first", "nested", "last"],
        "named": ["first", "last"], "uppercase": ["first", "last"], "missing": [],
    }, id="element-excludes-receiver-and-non-elements"),
    pytest.param("fragment", {
        "direct": ["fragment-parent", "fragment-child", "fragment-last"],
        "wildcard": ["fragment-parent", "fragment-child", "fragment-last"],
        "named": ["fragment-child", "fragment-last"],
        "uppercase": ["fragment-child", "fragment-last"], "missing": [],
    }, id="fragment-descendants"),
])
def test_wildcard_descendants_keep_dom_order_and_named_tag_behavior(parent, expected):
    # GIVEN nested element descendants mixed with text and a comment.
    script = """
const fragment=document.createDocumentFragment();
const branch=document.createElement('article'); branch.id='fragment-parent';
const child=document.createElement('span'); child.id='fragment-child'; branch.appendChild(child);
fragment.appendChild(branch); fragment.appendChild(document.createTextNode('ignored'));
fragment.appendChild(document.createComment('ignored'));
const last=document.createElement('span'); last.id='fragment-last'; fragment.appendChild(last);
const parent=""" + parent + """;
const ids=nodes=>Array.from(nodes,node=>node.id);
document.querySelector('#result').textContent=JSON.stringify({
  direct:ids(parent.querySelectorAll('*')), wildcard:ids(parent.getElementsByTagName('*')),
  named:ids(parent.getElementsByTagName('span')), uppercase:ids(parent.getElementsByTagName('SPAN')),
  missing:ids(parent.getElementsByTagName('missing'))
});
"""
    source = ('<html id="html"><head id="head"></head><body id="body"><section id="root">'
              '<span id="first"></span>ignored<!--ignored--><div id="nested"><span id="last"></span>'
              '</div></section><p id="result">pending</p><script id="probe">' + script + '</script></body></html>')
    # WHEN the installed page API looks up literal wildcard and named descendants.
    captured = render(source, "https://fixture.test/", lambda request: None, time.monotonic() + 10, soft_window=1)
    # THEN ordered element results exclude the receiver, text and comment nodes.
    assert _observation(captured) == ("settled", expected), "wildcard lookup must match real descendants without changing named tags"


def test_wildcard_lookup_stays_outside_inert_template_contents():
    # GIVEN a template containing an inert descendant and an ordinary sibling.
    source = '''<html><body><section id="root"><template id="template"><span id="inert"></span></template>
<span id="active"></span></section><p id="result">pending</p><script>
const root=document.querySelector('#root');
document.querySelector('#result').textContent=JSON.stringify({
  direct:Array.from(root.querySelectorAll('*'),node=>node.id),
  wildcard:Array.from(root.getElementsByTagName('*'),node=>node.id)
});
</script></body></html>'''
    # WHEN the installed wildcard API traverses the template boundary.
    captured = render(source, "https://fixture.test/", lambda request: None, time.monotonic() + 10, soft_window=1)
    # THEN inert template content does not become an active wildcard descendant.
    assert _observation(captured) == (
        "settled", {"direct": ["template", "active"], "wildcard": ["template", "active"]},
    ), "wildcard selection must retain the existing selector's inert template boundary"


def test_wildcard_class_fallback_appends_guarded_json_content():
    # GIVEN one real destination and a synthetic same-origin JSON resource.
    requested = []
    def fetch(request):
        """Return one controlled JSON response through the existing host seam."""
        requested.append(request["url"])
        return {"status": 200, "headers": (("Content-Type", "application/json"),),
                "final_url": request["url"], "body": b'{"quote":"Orion"}'}
    source = '''<html><body><div class="quotes"></div><p id="result">pending</p><script>
fetch('/quotes').then(response=>response.json()).then(data=>{
  const targets=Array.from(document.getElementsByTagName('*')).filter(node=>node.className==='quotes');
  targets.forEach(node=>node.insertAdjacentHTML('beforeend','<span>'+data.quote+'</span>'));
  document.querySelector('#result').textContent=JSON.stringify({
    direct_found:document.querySelector('.quotes')!==null, selected:targets.length,
    text:document.querySelector('.quotes').textContent
  });
});
</script></body></html>'''
    assert requested == [], "the synthetic resource must start unrequested"
    # WHEN the class fallback selects descendants before appending fetched content.
    captured = render(source, "https://fixture.test/", fetch, time.monotonic() + 10, soft_window=1)
    # THEN actual destination content survives in the frozen DOM.
    assert (requested, _observation(captured)) == (
        ["https://fixture.test/quotes"], ("settled", {"direct_found": True, "selected": 1, "text": "Orion"}),
    ), "wildcard fallback must select the real destination and append the acquired quote"


def test_strict_application_to_string_shadow_preserves_frozen_root():
    # GIVEN a styled-component-shaped assignment on an application-owned prototype.
    source = '''<html><body><p id="result">pending</p><script>
'use strict';
const original=Object.prototype.toString;
class Catalog {}
Catalog.prototype.toString=function(){return 'catalog';};
const descriptor=Object.getOwnPropertyDescriptor(Catalog.prototype,'toString');
document.querySelector('#result').textContent=JSON.stringify({
  value:String(new Catalog()), own:Object.hasOwn(Catalog.prototype,'toString'),
  descriptor:{writable:descriptor.writable,enumerable:descriptor.enumerable,configurable:descriptor.configurable},
  root_frozen:Object.isFrozen(Object.prototype), root_unchanged:Object.prototype.toString===original,
  root_value:Object.prototype.toString.call({})
});
</script></body></html>'''
    # WHEN ordinary strict assignment shadows the inherited protected method.
    captured = render(source, "https://fixture.test/", lambda request: None, time.monotonic() + 10, soft_window=1)
    # THEN the own method works while the frozen root retains its original behavior.
    assert _observation(captured) == ("settled", {
        "value": "catalog", "own": True,
        "descriptor": {"writable": True, "enumerable": True, "configurable": True},
        "root_frozen": True, "root_unchanged": True, "root_value": "[object Object]",
    }), "application shadowing must preserve the protected root and normal own-property semantics"


@pytest.mark.parametrize("mutation", [
    pytest.param("Object.prototype.toString=()=> 'forged'", id="protected-root-assignment"),
    pytest.param("Object.defineProperty(Object.prototype,'toString',{value:()=> 'forged'})", id="protected-root-definition"),
    pytest.param("Element.prototype.toString=()=> 'forged'", id="frozen-dom-prototype-assignment"),
])
def test_protected_to_string_mutations_are_rejected_without_changing_root(mutation):
    # GIVEN a direct root or frozen DOM prototype mutation attempted in strict mode.
    script = "'use strict'; const original=Object.prototype.toString; let rejected=false; try{" + mutation + """
}catch(error){rejected=true;}
document.querySelector('#result').textContent=JSON.stringify({
  rejected, root_frozen:Object.isFrozen(Object.prototype), root_unchanged:Object.prototype.toString===original,
  root_value:Object.prototype.toString.call({})
});
"""
    source = '<html><body><p id="result">pending</p><script>' + script + '</script></body></html>'
    # WHEN page code tries to replace a protected serializer capability.
    captured = render(source, "https://fixture.test/", lambda request: None, time.monotonic() + 10, soft_window=1)
    # THEN caught mutation failure retains both useful DOM and the original root.
    assert _observation(captured) == ("settled", {
        "rejected": True, "root_frozen": True, "root_unchanged": True, "root_value": "[object Object]",
    }), "own application overrides must not permit writes to protected root or DOM prototypes"


@pytest.mark.parametrize("mutation", [
    pytest.param("Object.defineProperty(document.body,'toString',{value:()=> 'forged'})", id="element-own-method"),
    pytest.param("Object.defineProperty(document.querySelector('p').firstChild,'toString',{value:()=> 'forged'})", id="text-own-method"),
    pytest.param("Object.defineProperty(document.querySelector('p').getAttributeNode('id'),'toString',{value:()=> 'forged'})", id="attribute-own-method"),
    pytest.param("Object.defineProperty(document.body,Symbol.toPrimitive,{value:()=> 'forged'})", id="element-coercion"),
    pytest.param("Object.defineProperty(document,'childNodes',{value:['forged']})", id="document-child-list"),
])
def test_dom_serializer_tampering_still_refuses_capture(mutation):
    # GIVEN a page-owned forged serialization capability on a real node or attribute.
    source = '<html><body><p id="result">pending</p><script>' + mutation + '</script></body></html>'
    # WHEN the trusted snapshot examines the actual DOM tree.
    with pytest.raises(BrewdocError, match="resource"):
        render(source, "https://fixture.test/", lambda request: None, time.monotonic() + 10, soft_window=1)
    # THEN no forged snapshot reaches the static converter.


def _capture_page(script):
    """Capture one synthetic script through the installed isolated render boundary."""
    source = '<html><body><p id="result">pending</p><script>' + script + '</script></body></html>'
    return render(source, "https://fixture.test/", lambda request: None, time.monotonic() + 10, soft_window=1)


@pytest.mark.parametrize("setup,expected", [
    pytest.param("""
const target=null;const source={get first(){source_reads++;return 1;}};
""", {"name": "TypeError", "original": True, "fake_calls": 0,
        "source_reads": 0, "entries": []}, id="null-target"),
    pytest.param("""
const target={first:0};Object.defineProperty(target,'blocked',{value:9,enumerable:true,writable:false});
const source={get first(){source_reads++;return 1;},get blocked(){source_reads++;return 2;},last:3};
""", {"name": "TypeError", "original": True, "fake_calls": 0,
        "source_reads": 2, "entries": [["first", 1], ["blocked", 9]]}, id="nonwritable-target-write"),
])
def test_object_assign_uses_intrinsic_typeerror_after_global_replacement(setup, expected):
    # GIVEN an observable fake TypeError and a target that must reject assignment.
    script = """
const intrinsic=TypeError;let fake_calls=0,source_reads=0;
globalThis.TypeError=function FakeTypeError(){fake_calls++;return new Error('forged');};
""" + setup + """
let name=null,original=false;try{Object.assign(target,source);}
catch(error){name=error.name;original=error instanceof intrinsic;}
document.querySelector('#result').textContent=JSON.stringify({
  name,original,fake_calls,source_reads,entries:Object.entries(Object(target))
});
"""
    # WHEN the protected assign operation rejects a null target or failed write.
    captured = _capture_page(script)
    # THEN intrinsic exception identity and partial writes cannot invoke page error code.
    assert _observation(captured) == ("settled", expected), "assign must use intrinsic TypeError and preserve failure ordering"


@pytest.mark.parametrize("script,expected", [
    pytest.param("""
const value=Object.assign({b:1},Object.assign(Object.defineProperty({},'a',{
  enumerable:true,get(){Object.defineProperty(this,'b',{value:3,enumerable:false});}
}),{b:2})).b;
const observation={value};
""", {"value": 1}, id="core-js-later-enumerability-witness"),
    pytest.param("""
const source={get first(){delete this.second;this.added=3;return 1;},second:2};
const target=Object.assign({},source);
const observation={keys:Object.keys(target),target};
""", {"keys": ["first"], "target": {"first": 1}}, id="deleted-key-skipped-new-key-not-visited"),
    pytest.param("""
const source={get first(){Object.defineProperty(this,'hidden',{enumerable:true});return 1;}};
Object.defineProperty(source,'hidden',{value:2,enumerable:false,configurable:true});
const target=Object.assign({},source);
const observation={keys:Object.keys(target),target};
""", {"keys": ["first", "hidden"], "target": {"first": 1, "hidden": 2}}, id="later-key-becomes-enumerable"),
    pytest.param("""
const token=Symbol('token'); const source=Object.create({inherited:4}); source.visible=1;
Object.defineProperty(source,'hidden',{value:99,enumerable:false}); source[token]=7;
const target=Object.assign({},source);
const observation={keys:Reflect.ownKeys(target).map(String),visible:target.visible,symbol:target[token],
  inherited:Object.hasOwn(target,'inherited'),hidden:Object.hasOwn(target,'hidden')};
""", {"keys": ["visible", "Symbol(token)"], "visible": 1, "symbol": 7,
        "inherited": False, "hidden": False}, id="own-enumerable-strings-and-symbols-only"),
    pytest.param("""
const one=Symbol('one'); const two=Symbol('two'); const source={};
source.b=1;source[10]=2;source[2]=3;source.a=4;source[one]=5;source[two]=6;
const target=Object.assign({},source);
const observation={keys:Reflect.ownKeys(target).map(String),values:Reflect.ownKeys(target).map(key=>target[key])};
""", {"keys": ["2", "10", "b", "a", "Symbol(one)", "Symbol(two)"],
        "values": [3, 2, 1, 4, 5, 6]}, id="integer-string-symbol-order"),
    pytest.param("""
const trace=[]; const source=new Proxy({a:1,b:2},{
  ownKeys(target){trace.push('keys');return Reflect.ownKeys(target);},
  getOwnPropertyDescriptor(target,key){trace.push('descriptor:'+key);return Reflect.getOwnPropertyDescriptor(target,key);},
  get(target,key){trace.push('value:'+key);return Reflect.get(target,key);}
});
const target=Object.assign({},source); const observation={trace,target};
""", {"trace": ["keys", "descriptor:a", "value:a", "descriptor:b", "value:b"],
        "target": {"a": 1, "b": 2}}, id="one-key-snapshot-descriptor-immediately-before-read"),
    pytest.param("""
const target=Object.assign(42,'ab',7,true,Symbol('unused'),null,undefined);
const observation={type:typeof target,value:target.valueOf(),entries:Object.entries(target)};
""", {"type": "object", "value": 42, "entries": [["0", "a"], ["1", "b"]]}, id="primitive-target-and-source-conversion"),
    pytest.param("""
const target={first:0}; Object.defineProperty(target,'blocked',{value:9,enumerable:true,writable:false});
let error=null;try{Object.assign(target,{first:1,blocked:2,last:3});}catch(problem){error=problem.name;}
const observation={error,entries:Object.entries(target)};
""", {"error": "TypeError", "entries": [["first", 1], ["blocked", 9]]}, id="failed-write-keeps-prior-success"),
    pytest.param("""
const target={};let error=null;
try{Object.assign(target,{first:1,get blocked(){throw new Error('stop');},last:3});}catch(problem){error=problem.name;}
const observation={error,entries:Object.entries(target)};
""", {"error": "Error", "entries": [["first", 1]]}, id="throwing-getter-keeps-prior-success"),
    pytest.param("""
let called=false;const target={first:0,set blocked(value){called=true;throw new Error('stop');}};
let error=null;try{Object.assign(target,{first:1,blocked:2,last:3});}catch(problem){error=problem.name;}
const observation={error,called,keys:Object.keys(target),first:target.first,last:Object.hasOwn(target,'last')};
""", {"error": "Error", "called": True, "keys": ["first", "blocked"], "first": 1, "last": False},
        id="throwing-setter-keeps-prior-success"),
    pytest.param("""
const target={};Object.assign(target,{first:1},{first:2,last:3});
const observation={entries:Object.entries(target)};
""", {"entries": [["first", 2], ["last", 3]]}, id="later-source-overwrites-in-source-order"),
    pytest.param("""
'use strict';const original=Object.assign;let rejected=false;
try{Object.assign=()=>({forged:true});}catch(error){rejected=true;}
const observation={rejected,frozen:Object.isFrozen(Object),unchanged:Object.assign===original};
""", {"rejected": True, "frozen": True, "unchanged": True}, id="corrected-constructor-stays-protected"),
])
def test_object_assign_preserves_live_descriptors_key_order_and_partial_writes(script, expected):
    # GIVEN an independent assign behavior witness with an exact observable target or trace.
    source = script + "document.querySelector('#result').textContent=JSON.stringify(observation);"
    # WHEN page code uses the generic protected Object.assign operation.
    captured = _capture_page(source)
    # THEN descriptors are checked at read time and abrupt completion preserves prior writes.
    assert _observation(captured) == ("settled", expected), "Object.assign must follow source order and current enumerable descriptors"


@pytest.mark.parametrize("target", ["null", "undefined"])
def test_object_assign_rejects_nullish_target_before_reading_sources(target):
    # GIVEN an invalid target and a source getter that must remain unread.
    script = "let reads=0,error=null;try{Object.assign(" + target + """,{
  get value(){reads++;return 1;}
});}catch(problem){error=problem.name;}
document.querySelector('#result').textContent=JSON.stringify({error,reads});
"""
    # WHEN target conversion rejects the nullish value.
    captured = _capture_page(script)
    # THEN the exact TypeError precedes every source value read.
    assert _observation(captured) == ("settled", {"error": "TypeError", "reads": 0}), "nullish assign targets must fail before source reads"


def _storage_observation(captured):
    """Require exact ephemeral-storage provenance before reading page-produced state."""
    metadata = {name: captured[name] for name in ("capture_status", "errors", "error_count", "pending", "counts")}
    assert metadata == {
        "capture_status": "partial", "errors": [{"category": "ephemeral_storage_only", "resource_url": None}],
        "error_count": 1, "pending": {"requests": 0, "timers": 0, "modules": 0, "promises": 0, "jobs": False},
        "counts": {"promise_jobs": 0, "timer_callbacks": 0},
    }, "real storage access must retain one exact partial marker without pending work or arbitrary errors"
    matches = re.findall(r'<p id="result">([^<]*)</p>', captured["html"])
    assert len(matches) == 1, "storage observations must come from exactly one frozen result element"
    return json.loads(matches[0])


def _require_storage_api():
    """Prove the actual storage API exists before testing a quota refusal."""
    captured = _capture_page("""
const storage=localStorage;
document.querySelector('#result').textContent=JSON.stringify({length:storage.length,
  methods:['getItem','setItem','removeItem','clear','key'].map(name=>typeof storage[name]),
  keys:Object.keys(storage),missing:storage.getItem('missing'),named_missing:typeof storage.missing});
""")
    assert _storage_observation(captured) == {
        "length": 0, "methods": ["function", "function", "function", "function", "function"],
        "keys": [], "missing": None, "named_missing": "undefined",
    }, "quota checks require real fresh storage, not a missing global or dummy methods"


@pytest.mark.parametrize("script,expected", [
    pytest.param("""
const storage=localStorage;const returns=[];
returns.push(typeof storage.setItem('count',42));returns.push(typeof storage.setItem('empty',''));
const before={length:storage.length,keys:Object.keys(storage),count:storage.getItem('count'),empty:storage.empty,
  missing:storage.getItem('missing'),named_missing:typeof storage.missing,first:storage.key(0),outside:storage.key(2)};
returns.push(typeof storage.removeItem('count'));returns.push(typeof storage.clear());
const observation={returns,before,after:{length:storage.length,keys:Object.keys(storage),missing:storage.getItem('empty')}};
""", {"returns": ["undefined", "undefined", "undefined", "undefined"],
        "before": {"length": 2, "keys": ["count", "empty"], "count": "42", "empty": "",
                   "missing": None, "named_missing": "undefined", "first": "count", "outside": None},
        "after": {"length": 0, "keys": [], "missing": None}}, id="real-round-trip-and-method-returns"),
    pytest.param("""
const storage=localStorage;storage.setItem('10','ten');storage.setItem('2','two');storage.setItem('a','first');
storage.setItem('10','updated');const overwritten=Object.keys(storage);
storage.removeItem('2');storage.setItem('2','again');
const observation={overwritten,keys:Object.keys(storage),indexed:[storage.key(0),storage.key(1),storage.key(2),storage.key(3)],
  entries:Object.entries(storage),own:Reflect.ownKeys(storage),negative:storage.key(-1)};
""", {"overwritten": ["10", "2", "a"], "keys": ["10", "a", "2"],
        "indexed": ["10", "a", "2", None], "entries": [["10", "updated"], ["a", "first"], ["2", "again"]],
        "own": ["10", "a", "2"], "negative": None}, id="insertion-order-including-numeric-names"),
    pytest.param("""
const storage=localStorage;storage.answer=42;const before=storage.getItem('answer');
const deleted=delete storage.answer;const absent=delete storage.absent;
const observation={before,deleted,absent,missing:storage.getItem('answer'),named_missing:typeof storage.answer,
  keys:Object.keys(storage),length:storage.length};
""", {"before": "42", "deleted": True, "absent": True, "missing": None, "named_missing": "undefined",
        "keys": [], "length": 0}, id="direct-assignment-and-deletion"),
    pytest.param("""
const storage=localStorage;const trace=[];
const key={toString(){trace.push('key');return 'name';}};
const value={toString(){trace.push('value');return 'catalog';}};
storage.setItem(key,value);storage.setItem(null,undefined);
const observation={trace,entries:Object.entries(storage)};
""", {"trace": ["key", "value"], "entries": [["name", "catalog"], ["null", "undefined"]]},
        id="string-coercion-key-before-value-once"),
    pytest.param("""
const storage=localStorage;storage.setItem('__proto__','first');storage.constructor='second';storage.prototype='third';
const descriptor=Object.getOwnPropertyDescriptor(storage,'__proto__');
const observation={null_prototype:Object.getPrototypeOf(storage)===null,keys:Object.keys(storage),
  values:[storage.__proto__,storage.constructor,storage.prototype],
  descriptor:{value:descriptor.value,writable:descriptor.writable,enumerable:descriptor.enumerable,configurable:descriptor.configurable}};
""", {"null_prototype": True, "keys": ["__proto__", "constructor", "prototype"],
        "values": ["first", "second", "third"],
        "descriptor": {"value": "first", "writable": True, "enumerable": True, "configurable": True}},
        id="null-prototype-poison-names-are-data"),
    pytest.param("""
const initial=Object.keys(localStorage);localStorage.length;localStorage.getItem('absent');Object.keys(localStorage);
const observation={initial,keys:Object.keys(localStorage),length:localStorage.length};
""", {"initial": [], "keys": [], "length": 0}, id="first-enumeration-marks-partial-once"),
    pytest.param("""
const observation={settings:JSON.parse(localStorage.getItem('settings')||'{}'),legacy:Object.keys(localStorage)};
localStorage.setItem('settings',JSON.stringify({theme:'dark'}));
observation.saved=JSON.parse(localStorage.getItem('settings'));
""", {"settings": {}, "legacy": [], "saved": {"theme": "dark"}}, id="retained-read-settings-pattern"),
])
def test_ephemeral_storage_preserves_real_values_order_and_exact_partial_provenance(script, expected):
    # GIVEN synthetic storage operations with independent exact state expectations.
    source = script + "document.querySelector('#result').textContent=JSON.stringify(observation);"
    # WHEN the installed storage surface executes real data operations in one capture.
    captured = _capture_page(source)
    # THEN only stored keys enumerate and one truthful partial marker survives.
    assert _storage_observation(captured) == expected, "ephemeral storage must preserve real strings, methods, order and fresh empty semantics"


def test_ephemeral_storage_starts_empty_in_each_independent_capture():
    # GIVEN one capture that writes a private setting.
    written = _capture_page("""
localStorage.setItem('private','first');
document.querySelector('#result').textContent=JSON.stringify({keys:Object.keys(localStorage),value:localStorage.getItem('private')});
""")
    assert _storage_observation(written) == {"keys": ["private"], "value": "first"}, "the first capture must really store its setting"
    # WHEN a second capture reads storage without writing that setting.
    fresh = _capture_page("""
document.querySelector('#result').textContent=JSON.stringify({keys:Object.keys(localStorage),value:localStorage.getItem('private')});
""")
    # THEN no stored value or key crosses the capture boundary.
    assert _storage_observation(fresh) == {"keys": [], "value": None}, "storage must be fresh for every capture"


@pytest.mark.parametrize("name", ["getItem", "setItem", "removeItem", "clear", "key", "length"])
@pytest.mark.parametrize("operation", [
    "storage.setItem(name,'forged')", "storage.getItem(name)", "storage.removeItem(name)",
    "storage[name]='forged'", "delete storage[name]",
])
def test_ephemeral_storage_rejects_reserved_api_names_as_data_keys(name, operation):
    # GIVEN a reserved member name and an attempted data-key operation.
    script = "const storage=localStorage;const name=" + json.dumps(name) + ";let error=null;try{" + operation + """
}catch(problem){error=problem.name;}
document.querySelector('#result').textContent=JSON.stringify({error,keys:Object.keys(storage),length:storage.length});
"""
    # WHEN page code tries to treat an API member as stored data.
    captured = _capture_page(script)
    # THEN TypeError retains all API members and leaves the data map empty.
    assert _storage_observation(captured) == {"error": "TypeError", "keys": [], "length": 0}, "reserved storage names must reject without changing stored state"


@pytest.mark.parametrize("operation", [
    pytest.param("storage.setItem(Symbol('key'),'value')", id="symbol-method-key"),
    pytest.param("storage[Symbol('key')]='value'", id="symbol-property-write"),
    pytest.param("storage[Symbol('key')]", id="symbol-property-read"),
    pytest.param("delete storage[Symbol('key')]", id="symbol-property-delete"),
    pytest.param("Object.defineProperty(storage,'forged',{value:'value'})", id="define-property-bypass"),
    pytest.param("Object.setPrototypeOf(storage,{forged:'value'})", id="prototype-bypass"),
    pytest.param("Object.preventExtensions(storage)", id="prevent-extensions-bypass"),
    pytest.param("Object.seal(storage)", id="seal-bypass"),
    pytest.param("Object.freeze(storage)", id="freeze-bypass"),
])
def test_ephemeral_storage_rejects_symbols_and_proxy_mutation_bypasses(operation):
    # GIVEN a symbolic key or a mutation that could bypass the private quota owner.
    script = "const storage=localStorage;let error=null;try{" + operation + """
}catch(problem){error=problem.name;}
document.querySelector('#result').textContent=JSON.stringify({error,keys:Object.keys(storage),length:storage.length,
  null_prototype:Object.getPrototypeOf(storage)===null,extensible:Object.isExtensible(storage)});
"""
    # WHEN an unsupported mutation is attempted against the real storage surface.
    captured = _capture_page(script)
    # THEN it rejects atomically and preserves valid proxy enumeration invariants.
    assert _storage_observation(captured) == {
        "error": "TypeError", "keys": [], "length": 0, "null_prototype": True, "extensible": True,
    }, "unsupported storage mutations must reject while preserving the null prototype and stored-key-only enumeration"


@pytest.mark.parametrize("count", [199, 200], ids=["below-key-limit", "exact-key-limit"])
def test_ephemeral_storage_accepts_inclusive_key_quota(count):
    # GIVEN distinct short keys below or exactly at the 200-key quota.
    script = "const storage=localStorage;for(let index=0;index<" + str(count) + """;index++)storage.setItem('k'+index,'');
document.querySelector('#result').textContent=JSON.stringify({length:storage.length,keys:Object.keys(storage),last:storage.key(storage.length-1)});
"""
    expected_keys = ["k" + str(index) for index in range(count)]
    # WHEN all accepted keys are written to one fresh capture.
    captured = _capture_page(script)
    # THEN the entire inclusive map remains available in insertion order.
    assert _storage_observation(captured) == {
        "length": count, "keys": expected_keys, "last": "k" + str(count - 1),
    }, "the key quota must accept its exact inclusive boundary without losing data"


@pytest.mark.parametrize("key,value,units", [
    pytest.param("", "'x'.repeat(65535)", 65535, id="ascii-below-byte-limit"),
    pytest.param("", "'x'.repeat(65536)", 65536, id="ascii-exact-byte-limit"),
    pytest.param("k", "'x'.repeat(65535)", 65535, id="key-and-value-share-byte-limit"),
    pytest.param("", "'é'.repeat(32768)", 32768, id="two-byte-exact-limit"),
    pytest.param("", "'\\uD83D\\uDE00'.repeat(16384)", 32768, id="surrogate-pairs-four-byte-exact-limit"),
    pytest.param("", "'\\uD800'.repeat(21845)+'x'", 21846, id="lone-surrogates-three-byte-replacement-count"),
])
def test_ephemeral_storage_accepts_utf8_quota_without_changing_utf16_values(key, value, units):
    # GIVEN strings whose declared UTF-8 budget is at or below 65,536 bytes.
    script = "const storage=localStorage;const key=" + json.dumps(key) + ";const value=" + value + """;
storage.setItem(key,value);const stored=storage.getItem(key);
document.querySelector('#result').textContent=JSON.stringify({length:storage.length,units:stored.length,
  unchanged:stored===value,key:storage.key(0)});
"""
    # WHEN the storage quota counts bytes while retaining the original JavaScript string.
    captured = _capture_page(script)
    # THEN exact byte boundaries accept and UTF-16 code units remain unchanged.
    assert _storage_observation(captured) == {"length": 1, "units": units, "unchanged": True, "key": key}, "UTF-8 quota accounting must preserve original UTF-16 strings and inclusive limits"


@pytest.mark.parametrize("mutation", [
    pytest.param("for(let index=0;index<201;index++)storage.setItem('k'+index,'')", id="key-count-one-over"),
    pytest.param("storage.setItem('', 'x'.repeat(65537))", id="ascii-byte-one-over"),
    pytest.param("storage.setItem('k', 'x'.repeat(65536))", id="key-bytes-count-toward-quota"),
    pytest.param("storage.setItem('', 'é'.repeat(32768)+'x')", id="multibyte-one-over"),
    pytest.param("storage.setItem('', '\\uD83D\\uDE00'.repeat(16384)+'x')", id="surrogate-pair-one-over"),
    pytest.param("storage.setItem('', '\\uD800'.repeat(21845)+'xx')", id="lone-surrogate-one-over"),
    pytest.param("storage.too_large='x'.repeat(65537)", id="direct-property-quota-owner"),
    pytest.param("""
storage.setItem('','x'.repeat(65533));
storage.setItem('o',{toString(){storage.setItem('i','x');return 'y';}});
""", id="reentrant-value-coercion-cannot-bypass-quota"),
    pytest.param("""
storage.setItem('','x'.repeat(65533));
storage.setItem({toString(){storage.setItem('i','x');return 'o';}},'y');
""", id="reentrant-key-coercion-cannot-bypass-quota"),
])
def test_caught_ephemeral_storage_overflow_remains_a_hard_refusal_after_clear(mutation):
    # GIVEN a real available storage API and one over a key or UTF-8 byte bound.
    _require_storage_api()
    script = "const storage=localStorage;try{" + mutation + """}catch(error){}
storage.clear();document.querySelector('#result').textContent=JSON.stringify({length:storage.length});
"""
    # WHEN page code catches overflow and clears every stored value.
    with pytest.raises(BrewdocError, match="resource"):
        _capture_page(script)
    # THEN the sticky resource refusal prevents publication despite the empty map.


STORAGE_STATE_PROBE = """
import json
from pathlib import Path
import sys
from brewdoc import htmljs
from brewdoc._urljs.native import Context

def inspect_storage():
    '''Inspect real quota state under the checked native thread before host refusal.'''
    with Context({}.__getitem__, guarded_stack=True) as context:
        package=Path(htmljs.__file__).parent
        worker=(package/'_vendor/linkedom-worker.js').read_text()
        bundle=worker.rpartition('\\nexport {')[0]
        glue=(package/'_urljs/glue.js').read_text()
        context.eval('(()=>{'+bundle+'\\n'+glue+'\\n})();','installed-bootstrap')
        context.save(['__smallParseStart','__smallParseResume','__smallParseEnd','__smallBoot','__smallViolation'])
        pending=int(context.call('__smallParseStart','<html><body><p>article</p></body></html>'))
        while pending:
            pending=int(context.call('__smallParseResume'))
        context.call('__smallParseEnd')
        context.call('__smallBoot','https://fixture.test/',
          json.dumps({'href':'https://fixture.test/','protocol':'https:','hostname':'fixture.test',
                      'host':'fixture.test','pathname':'/','search':'','hash':'','origin':'https://fixture.test'}))
        context.eval('globalThis.__storageProbe=()=>{'+sys.argv[1]+';return JSON.stringify(observation);};','storage-unit')
        context.save(['__storageProbe'])
        state=json.loads(context.call('__storageProbe'))
        return {'state':state,'violation':int(context.call('__smallViolation'))}

htmljs._CHILD_ONLY=True
result=htmljs._guarded_call(inspect_storage,protect=True)
print(json.dumps(result,ensure_ascii=True))
"""


@pytest.mark.parametrize("setup,mutation,expected", [
    pytest.param("storage.setItem('keep','old');", "storage.setItem('keep','x'.repeat(65533));", {
        "rejected": True, "length": 1, "keys": ["keep"], "values": ["old"],
    }, id="overflow-replacement-preserves-old-value"),
    pytest.param("storage.setItem('keep','x');", "storage.setItem('extra','x'.repeat(65532));", {
        "rejected": True, "length": 1, "keys": ["keep"], "values": ["x"],
    }, id="overflow-insertion-preserves-map-and-order"),
    pytest.param("for(let index=0;index<200;index++)storage.setItem('k'+index,'');", "storage.extra='x';", {
        "rejected": True, "length": 200, "keys": ["k" + str(index) for index in range(200)],
        "values": [""] * 200,
    }, id="one-over-key-limit-does-not-insert"),
    pytest.param("storage.setItem('','x'.repeat(65533));",
                 "storage.setItem('o',{toString(){storage.setItem('i','x');return 'y';}});", {
                     "rejected": True, "length": 2, "keys": ["", "i"], "values": ["x" * 65533, "x"],
                 }, id="reentrant-value-side-effect-counted-before-write"),
    pytest.param("storage.setItem('','x'.repeat(65533));",
                 "storage.setItem({toString(){storage.setItem('i','x');return 'o';}},'y');", {
                     "rejected": True, "length": 2, "keys": ["", "i"], "values": ["x" * 65533, "x"],
                 }, id="reentrant-key-side-effect-counted-before-write"),
])
def test_ephemeral_storage_overflow_is_atomic_and_keeps_coercion_side_effects(setup, mutation, expected):
    # GIVEN real storage and a guarded unit boundary that can inspect state after a caught overflow.
    _require_storage_api()
    script = "const storage=localStorage;" + setup + "let rejected=false;try{" + mutation + """}catch(error){rejected=true;}
const entries=Object.entries(storage);
const observation={rejected,length:storage.length,keys:Object.keys(storage),values:entries.map(entry=>entry[1])};
"""
    # WHEN the unchanged native context executes the real installed glue under the guarded thread.
    checked = subprocess.run([sys.executable, "-I", "-c", STORAGE_STATE_PROBE, script], capture_output=True, timeout=10)
    # THEN failed writes leave the prior map intact while coercion side effects and sticky violation survive.
    assert (checked.returncode, checked.stderr) == (0, b""), "the guarded storage state probe must finish normally"
    assert json.loads(checked.stdout) == {"state": expected, "violation": 1}, "quota overflow must be atomic after coercion and retain the sticky violation"


def test_ephemeral_storage_overwrite_and_remove_release_existing_byte_usage():
    # GIVEN an exact-full first value followed by a smaller replacement and another key.
    script = """
const storage=localStorage;storage.setItem('a','x'.repeat(65535));storage.setItem('a','x');
storage.setItem('b','y'.repeat(65533));
const replaced={keys:Object.keys(storage),units:[storage.getItem('a').length,storage.getItem('b').length]};
storage.removeItem('a');storage.removeItem('b');storage.setItem('c','z'.repeat(65535));
document.querySelector('#result').textContent=JSON.stringify({replaced,keys:Object.keys(storage),units:storage.getItem('c').length});
"""
    # WHEN real mutations replace and remove previously counted key/value bytes.
    captured = _capture_page(script)
    # THEN current aggregate usage permits exact-boundary writes without stale accounting.
    assert _storage_observation(captured) == {
        "replaced": {"keys": ["a", "b"], "units": [1, 65533]}, "keys": ["c"], "units": 65535,
    }, "overwrite and deletion must release prior byte usage while retaining insertion order"


def test_parent_accepts_only_the_exact_ephemeral_storage_category():
    from brewdoc.htmljs import _validate_capture
    # GIVEN a real metadata shape with the new exact partial-storage category.
    captured = _capture_page("document.querySelector('#result').textContent='[]';")
    captured.update(capture_status="partial", errors=[{"category": "ephemeral_storage_only", "resource_url": None}], error_count=1)
    # WHEN the parent validates the category without adding or removing metadata fields.
    result = _validate_capture(captured, 1)
    # THEN the declared category is accepted with the unchanged shape.
    assert result is None, "the exact ephemeral storage category must be accepted by parent metadata validation"


def test_parent_rejects_arbitrary_ephemeral_storage_category_variants():
    from brewdoc.htmljs import _validate_capture
    # GIVEN a real metadata shape with a forged category resembling the accepted one.
    captured = _capture_page("document.querySelector('#result').textContent='[]';")
    captured.update(capture_status="partial", errors=[{"category": "ephemeral_storage_only_forged", "resource_url": None}], error_count=1)
    # WHEN the parent checks its finite error-category allow-list.
    with pytest.raises(BrewdocError, match="metadata invalid"):
        _validate_capture(captured, 1)
    # THEN a similar string cannot bypass the exact category contract.
