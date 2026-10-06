"""Pinned serializer inputs cannot run page traps during trusted inspection."""

import json
import ctypes
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Snapshot guard proofs require isolated Linux.")

PROBE = r"""
import json,sys,time
from brewdoc import htmljs
from brewdoc.common import BrewdocError
from brewdoc._urljs.native import Context,NativeError

def inspect():
    '''Read only authored numeric counters after the real capture path finishes.'''
    observed={}
    original_close=Context.close
    def close(context):
        global_value=context.lib.JS_GetGlobalObject(context.context)
        try:
            for name in ('__guard_ready','__guard_calls'):
                value=context.lib.JS_GetPropertyStr(context.context,global_value,name.encode())
                try:
                    observed[name]={'tag':value.tag,'value':value.u.integer}
                finally:
                    context.lib.JS_FreeValue(context.context,value)
        finally:
            context.lib.JS_FreeValue(context.context,global_value)
            original_close(context)
    class Channel:
        def send_bytes(self,payload):
            pass
        def recv_bytes(self,maximum):
            raise AssertionError('synthetic snapshot probes never acquire resources')
    Context.close=close
    try:
        try:
            captured=htmljs._capture(sys.argv[1],'https://fixture.test/',Channel(),1,time.monotonic()+10)
            result={'refused':False,'html':captured['html']}
        except (BrewdocError,NativeError):
            result={'refused':True,'html':None}
    finally:
        Context.close=original_close
    return {**result,'observed':observed}

htmljs._CHILD_ONLY=True
print(json.dumps(htmljs._guarded_call(inspect,protect=True)))
"""

SETUP = """
globalThis.__guard_calls=0;globalThis.__guard_ready=0;
document.doctype.name='html';
if(document.doctype.name!=='html')throw new Error('fixture doctype name absent');
const key=(node,name)=>Object.getOwnPropertySymbols(node).find(symbol=>symbol.description===name);
const body=document.body,p=body.querySelector('p');
const next=key(body,'next'),end=key(body,'end'),start=key(body[end],'start');
if(typeof next!=='symbol'||typeof end!=='symbol'||typeof start!=='symbol')throw new Error('fixture symbols absent');
const traps={
  get(target,name,receiver){__guard_calls++;return Reflect.get(target,name,receiver);},
  getOwnPropertyDescriptor(target,name){__guard_calls++;return Reflect.getOwnPropertyDescriptor(target,name);},
  getPrototypeOf(target){__guard_calls++;return Reflect.getPrototypeOf(target);},
  has(target,name){__guard_calls++;return Reflect.has(target,name);}
};
"""


def _probe(script):
    """Use one protected Linux child and observe primitive counters without callbacks."""
    source = '<!DOCTYPE html><html><head></head><body><p>Initial article</p><script>' + script + '</script></body></html>'
    result = subprocess.run([sys.executable, "-I", "-c", PROBE, source], capture_output=True, timeout=15)
    assert (result.returncode, result.stderr) == (0, b""), "the protected real capture probe must finish without native faults"
    return json.loads(result.stdout)


@pytest.mark.parametrize("setup", [
    pytest.param("body[next]=new Proxy(p,traps);", id="node-proxy"),
    pytest.param("Object.setPrototypeOf(p,new Proxy(Object.getPrototypeOf(p),traps));", id="prototype-proxy"),
    pytest.param("""
p.setAttribute('safe','yes');const attribute=p[key(p,'next')];
if(attribute.nodeType!==2)throw new Error('fixture attribute absent');
p[key(p,'next')]=new Proxy(attribute,traps);
""", id="attribute-proxy"),
    pytest.param("""
const finish=p[key(p,'end')],text=p.firstChild;
text[key(text,'next')]=new Proxy(finish,traps);
""", id="end-proxy"),
    pytest.param("const pair=Proxy.revocable(p,traps);pair.revoke();body[next]=pair.proxy;", id="revoked-node-proxy"),
    pytest.param("const pair=Proxy.revocable(Object.getPrototypeOf(p),traps);pair.revoke();Object.setPrototypeOf(p,pair.proxy);", id="revoked-prototype-proxy"),
    pytest.param("p.setAttribute('safe','yes');const k=key(p,'next'),pair=Proxy.revocable(p[k],traps);pair.revoke();p[k]=pair.proxy;", id="revoked-attribute-proxy"),
    pytest.param("const text=p.firstChild,pair=Proxy.revocable(p[key(p,'end')],traps);pair.revoke();text[key(text,'next')]=pair.proxy;", id="revoked-end-proxy"),
])
def test_snapshot_proxy_inputs_refuse_without_running_any_page_trap(setup):
    # GIVEN an actual flat serializer input Proxy, reset after active page setup.
    script = SETUP + setup + "__guard_calls=0;__guard_ready=1;while(true){}"
    # WHEN the real timing recovery inspects and serializes the current document.
    result = _probe(script)
    # THEN hard refusal precedes every get, descriptor, prototype and has trap.
    assert result == {"refused": True, "html": None, "observed": {
        "__guard_ready": {"tag": 0, "value": 1}, "__guard_calls": {"tag": 0, "value": 0},
    }}, "a Proxy serializer input must be refused before any page-controlled inspection"


@pytest.mark.parametrize("setup", [
    pytest.param("const value=body[next];Object.defineProperty(body,next,{get(){__guard_calls++;return value;}});", id="own-next-accessor"),
    pytest.param("const k=key(p,'end'),value=p[k];Object.defineProperty(p,k,{get(){__guard_calls++;return value;}});", id="own-end-accessor"),
    pytest.param("const finish=p[key(p,'end')],k=key(finish,'start'),value=finish[k];Object.defineProperty(finish,k,{get(){__guard_calls++;return value;}});", id="own-start-accessor"),
    pytest.param("const text=p.firstChild,k=key(text,'value'),value=text[k];Object.defineProperty(text,k,{get(){__guard_calls++;return value;}});", id="own-value-accessor"),
    pytest.param("const value=p.nodeType;Object.defineProperty(p,'nodeType',{get(){__guard_calls++;return value;}});", id="own-node-type-accessor"),
    pytest.param("""
const name=p.localName,parent=Object.create(Object.getPrototypeOf(p));delete p.localName;
Object.defineProperty(parent,'localName',{get(){__guard_calls++;return name;}});Object.setPrototypeOf(p,parent);
""", id="inherited-local-name-getter"),
    pytest.param("""
p.setAttribute('safe','yes');const attribute=p[key(p,'next')],parent=Object.create(Object.getPrototypeOf(attribute));
const original=attribute.toString;
Object.defineProperty(parent,Symbol.toPrimitive,{get(){__guard_calls++;return function(){return original.call(this);}}});
Object.setPrototypeOf(attribute,parent);
""", id="inherited-attribute-coercion-getter"),
    pytest.param("""
const list=Object.getPrototypeOf(document.childNodes),original=list.push;
const replacement=function(...values){__guard_calls++;return original.apply(this,values);};
Object.defineProperty(list,'push',{value:replacement});
if(list.push!==replacement)throw new Error('fixture push replacement absent');
""", id="node-list-push-shadow"),
    pytest.param("const original=RegExp.prototype.exec;RegExp.prototype.exec=function(input){__guard_calls++;return original.call(this,input);};", id="regexp-exec-shadow"),
    pytest.param("""
const style=document.createElement('style');style.textContent='p{color:red}';body.appendChild(style);
const original=style.cloneNode,replacement=function(...args){__guard_calls++;return original.apply(this,args);};
Object.defineProperty(style,'cloneNode',{value:replacement});
if(style.cloneNode!==replacement)throw new Error('fixture clone replacement absent');
""", id="text-element-clone-shadow"),
    pytest.param("""
const parent=Object.create(Object.getPrototypeOf(p)),original=p.toString;
Object.defineProperty(parent,'toString',{get(){__guard_calls++;return original;}});Object.setPrototypeOf(p,parent);
""", id="inherited-serializer-getter"),
    pytest.param("""
const parent=Object.create(Object.getPrototypeOf(p)),original=p.valueOf;
Object.defineProperty(parent,'valueOf',{get(){__guard_calls++;return original;}});Object.setPrototypeOf(p,parent);
""", id="inherited-value-of-getter"),
    pytest.param("""
const list=Object.getPrototypeOf(document.childNodes),original=list.join;
const replacement=function(...values){__guard_calls++;return original.apply(this,values);};
Object.defineProperty(list,'join',{value:replacement});
if(list.join!==replacement)throw new Error('fixture join replacement absent');
""", id="node-list-join-shadow"),
    pytest.param("""
const style=document.createElement('style');style.textContent='p{color:red}';body.appendChild(style);
const original=HTMLElement.prototype.toString,replacement=function(...args){__guard_calls++;return Reflect.apply(Function.prototype.call,original,args);};
Object.defineProperty(original,'call',{value:replacement});
if(original.call!==replacement)throw new Error('fixture function call replacement absent');
""", id="serializer-function-call-shadow"),
    pytest.param("""
const fragment=document.createDocumentFragment(),serializer=fragment.toString,parent=Object.create(Object.getPrototypeOf(fragment));
const getter=function(){__guard_calls++;return [];};Object.defineProperty(parent,'childNodes',{get:getter});Object.setPrototypeOf(p,parent);
if(p.toString!==serializer||Object.getOwnPropertyDescriptor(parent,'childNodes').get!==getter)throw new Error('fixture fragment serializer or getter absent');
if(!Object.hasOwn(p,'nodeType')||!Object.hasOwn(p,'localName')||p.nodeType!==1||p.localName!=='p'||p[key(p,'end')][start]!==p)throw new Error('fixture own element inputs differ');
""", id="non-element-parent-serializer-child-nodes-getter"),
    pytest.param("""
p.setAttribute('safe','yes');const attribute=p[key(p,'next')],fragment=document.createDocumentFragment();
const parent=Object.create(Object.getPrototypeOf(fragment)),getter=function(){__guard_calls++;return attribute[key(attribute,'next')];};
Object.setPrototypeOf(attribute,parent);Object.defineProperty(attribute,end,{get:getter});
if(attribute.nodeType!==2||attribute.toString!==fragment.toString||Object.getOwnPropertyDescriptor(attribute,end).get!==getter)throw new Error('fixture borrowed attribute serializer or END getter absent');
if(!Object.hasOwn(attribute,'name')||attribute.name!=='safe'||!Object.hasOwn(attribute,'localName')||typeof attribute.localName!=='string'||Object.getOwnPropertyDescriptor(attribute,key(attribute,'value')).value!=='yes')throw new Error('fixture own attribute inputs differ');
""", id="attribute-parent-serializer-end-getter"),
])
def test_snapshot_accessor_and_serializer_dependencies_refuse_without_page_callbacks(setup):
    # GIVEN a changed actual serializer input or dependency after active setup.
    script = SETUP + setup + "__guard_calls=0;__guard_ready=1;while(true){}"
    # WHEN timing recovery reaches the unchanged pinned serializer boundary.
    result = _probe(script)
    # THEN descriptor validation refuses before any accessor or replacement method.
    assert result == {"refused": True, "html": None, "observed": {
        "__guard_ready": {"tag": 0, "value": 1}, "__guard_calls": {"tag": 0, "value": 0},
    }}, "changed flat inputs and native dependencies must refuse without executing page code"


def test_ordinary_custom_element_inheriting_native_serializer_keeps_unrelated_members(tmp_path):
    # GIVEN an ordinary custom HTMLElement with an unrelated getter and method.
    script = SETUP + """
class Card extends HTMLElement {
  get unrelated(){__guard_calls++;throw new Error('unrelated getter ran');}
  greeting(){return 'ordinary method';}
}
customElements.define('x-card',Card);
const card=document.createElement('x-card');card.setAttribute('safe','yes');card.textContent='Custom article';
document.body.replaceChildren(card);document.doctype.name='html';
if(document.doctype.name!=='html')throw new Error('fixture doctype name absent');
__guard_calls=0;__guard_ready=1;
"""
    # WHEN the unchanged pinned serializer snapshots the healthy custom node.
    result = _probe(script)
    # THEN native serialization accepts unrelated custom members without invoking them.
    assert result == {"refused": False,
        "html": '<!DOCTYPE html><html><head></head><body><x-card safe="yes">Custom article</x-card></body></html>',
        "observed": {"__guard_ready": {"tag": 0, "value": 1}, "__guard_calls": {"tag": 0, "value": 0}},
    }, "ordinary custom HTMLElement prototypes must remain compatible with the original serializer"
    from brewdoc.htmltext import render_html
    from test_html import expected_document
    path = tmp_path / 'sample.html'
    path.write_text(result['html'], encoding='utf-8')
    markdown, _ = render_html(path)
    assert markdown == expected_document(path, 'Custom article\n', 1), "the ordinary custom element must retain its exact content through Markdown conversion"


def test_healthy_element_borrowing_native_fragment_serializer_keeps_exact_content(tmp_path):
    # GIVEN a healthy p borrowing the unchanged native fragment serializer with valid own endpoints.
    script = SETUP + """
const fragment=document.createDocumentFragment(),parent=Object.create(Object.getPrototypeOf(fragment));
document.body.replaceChildren(p);Object.setPrototypeOf(p,parent);
const finish=p[end];
if(p.toString!==fragment.toString||!Object.hasOwn(p,'localName')||p.localName!=='p'||Object.getOwnPropertyDescriptor(p,end).value!==finish||finish[start]!==p)throw new Error('fixture borrowed serializer endpoints differ');
__guard_calls=0;__guard_ready=1;
"""
    # WHEN trusted inspection reaches the actual borrowed native parent serializer.
    result = _probe(script)
    # THEN healthy borrowing preserves the unchanged upstream HTML without page callbacks.
    assert result == {"refused": False,
        "html": '<!DOCTYPE html><html><head></head><body><p>Initial article</p></body></html>',
        "observed": {"__guard_ready": {"tag": 0, "value": 1}, "__guard_calls": {"tag": 0, "value": 0}},
    }, "valid native parent serializer borrowing must remain supported without a class restriction"
    from brewdoc.htmltext import render_html
    from test_html import expected_document
    path = tmp_path / 'sample.html'
    path.write_text(result['html'], encoding='utf-8')
    markdown, _ = render_html(path)
    assert markdown == expected_document(path, 'Initial article\n', 1), "borrowed native serialization must retain exact article Markdown"


@pytest.mark.parametrize("setup", [
    pytest.param("""
const parent=Object.getPrototypeOf(style),original=style.cloneNode;
const replacement=function(...args){__guard_calls++;return original.apply(this,args);};
Object.defineProperty(parent,'cloneNode',{value:replacement});
if(style.cloneNode!==replacement)throw new Error('fixture inherited clone replacement absent');
""", id="inherited-style-clone"),
    pytest.param("""
const parent=Object.getPrototypeOf(style);
Object.defineProperty(parent,'ownerDocument',{set(value){__guard_calls++;Object.defineProperty(this,'ownerDocument',{value,writable:true,configurable:true});}});
if(typeof Object.getOwnPropertyDescriptor(parent,'ownerDocument').set!=='function')throw new Error('fixture constructor setter absent');
""", id="fresh-style-owner-document-setter"),
    pytest.param("""
const attribute=style[key(style,'next')],parent=Object.getPrototypeOf(attribute);
if(attribute.nodeType!==2||!Object.hasOwn(attribute,'name'))throw new Error('fixture safe original attribute absent');
Object.defineProperty(parent,'name',{set(value){__guard_calls++;Object.defineProperty(this,'name',{value,writable:true,configurable:true});}});
if(typeof Object.getOwnPropertyDescriptor(parent,'name').set!=='function')throw new Error('fixture attribute setter absent');
""", id="fresh-attribute-name-setter"),
    pytest.param("""
const upgrade=key(document,'upgrade'),target={};
if(typeof upgrade!=='symbol')throw new Error('fixture upgrade symbol absent');
Object.defineProperty(target,'changed',{set(value){__guard_calls++;}});
document[upgrade]={element:target,values:[['changed','yes']]};
if(document[upgrade].element!==target)throw new Error('fixture upgrade state absent');
""", id="constructor-upgrade-values"),
    pytest.param("""
const original=WeakMap.prototype.set,replacement=function(...args){__guard_calls++;return original.apply(this,args);};
Object.defineProperty(WeakMap.prototype,'set',{value:replacement});
if(WeakMap.prototype.set!==replacement)throw new Error('fixture weak map replacement absent');
""", id="constructor-weak-map-set"),
    pytest.param("""
const original=Map,replacement=function(...args){__guard_calls++;return new original(...args);};
globalThis.Map=replacement;if(Map!==replacement)throw new Error('fixture Map replacement absent');
""", id="constructor-global-map"),
    pytest.param("""
const parent=Object.create(Object.getPrototypeOf(style));
Object.defineProperty(parent,'ownerSVGElement',{get(){__guard_calls++;return null;}});Object.setPrototypeOf(style,parent);
if(typeof Object.getOwnPropertyDescriptor(parent,'ownerSVGElement').get!=='function')throw new Error('fixture SVG getter absent');
""", id="clone-svg-owner-getter"),
    pytest.param("""
const original=document.createDocumentFragment,replacement=function(...args){__guard_calls++;return original.apply(this,args);};
const script=body.querySelector('script');Object.defineProperty(document,'createDocumentFragment',{value:replacement});script.localName='template';
if(document.createDocumentFragment!==replacement||script.localName!=='template')throw new Error('fixture clone target replacement absent');
""", id="text-element-retargeted-template-constructor"),
])
def test_text_element_fresh_clone_dependencies_refuse_before_constructors_and_setters(setup):
    # GIVEN a native style with safe own inputs followed by a changed fresh-clone dependency.
    script = SETUP + "const style=document.createElement('style');style.setAttribute('safe','yes');style.textContent='p{color:red}';body.appendChild(style);" + setup
    script += "__guard_calls=0;__guard_ready=1;while(true){}"
    # WHEN the pinned TextElement serializer would clone the existing safe instance.
    result = _probe(script)
    # THEN no constructor, fresh inherited setter or upgrade callback executes.
    assert result == {"refused": True, "html": None, "observed": {
        "__guard_ready": {"tag": 0, "value": 1}, "__guard_calls": {"tag": 0, "value": 0},
    }}, "fresh clone dependencies must be validated before creating any new native node"


@pytest.mark.parametrize(("markup", "expected"), [
    ('<p safe="&quot;&amp;">Text &amp; words<!--note--></p>', '<p safe="&quot;&">Text &amp; words<!--note--></p>'),
    ('<template><p>Template words</p></template>', '<template><p>Template words</p></template>'),
    ('<style>p{color:red}</style><textarea>A & B</textarea><script type="application/json">{"value":1}</script>',
     '<style>p{color:red}</style><textarea>A & B</textarea><script type="application/json">{"value":1}</script>'),
])
def test_native_serializer_keeps_text_template_and_text_element_markup_exact(markup, expected):
    # GIVEN native nodes whose unchanged serializers use the accepted descriptor paths.
    script = SETUP + "body.innerHTML=" + json.dumps(markup).replace('<', r'\u003c') + ";"
    script += "if(body.innerHTML!==" + json.dumps(expected).replace('<', r'\u003c') + ")throw new Error('fixture native markup differs');document.doctype.name='html';"
    script += "if(document.doctype.name!=='html')throw new Error('fixture doctype name absent');__guard_calls=0;__guard_ready=1;"
    # WHEN a healthy page is captured without executing embedded replacement markup.
    result = _probe(script)
    # THEN the original serializer retains exact text, escaping and constructor behavior.
    assert result == {"refused": False, "html": '<!DOCTYPE html><html><head></head><body>' + expected + '</body></html>',
        "observed": {"__guard_ready": {"tag": 0, "value": 1}, "__guard_calls": {"tag": 0, "value": 0}},
    }, "the guard must preserve accepted native markup rather than rejecting healthy clone paths"


def test_untouched_parsed_doctype_keeps_the_unchanged_vendor_serialization():
    # GIVEN the actual parser-produced own undefined doctype name without normalization.
    script = """
globalThis.__guard_calls=0;globalThis.__guard_ready=0;
const descriptor=Object.getOwnPropertyDescriptor(document.doctype,'name');
if(!descriptor||!Object.hasOwn(descriptor,'value')||descriptor.value!==undefined)throw new Error('fixture parsed doctype differs');
document.body.innerHTML='<p>Untouched doctype article</p>';__guard_ready=1;
"""
    # WHEN trusted inspection reaches the unchanged native DocumentType serializer.
    result = _probe(script)
    # THEN the source-backed existing serialization remains available and exact.
    assert result == {"refused": False,
        "html": '<!DOCTYPE ><html><head></head><body><p>Untouched doctype article</p></body></html>',
        "observed": {"__guard_ready": {"tag": 0, "value": 1}, "__guard_calls": {"tag": 0, "value": 0}},
    }, "an untouched native parser result must retain upstream serialization without introducing page coercion"


def test_public_descriptor_abi_layout_matches_the_pinned_64_bit_header():
    # GIVEN the exact public descriptor and value declarations used by native calls.
    from brewdoc._urljs.native import _PropertyDescriptor, _Value
    # WHEN ctypes computes structure size, alignment and member offsets.
    actual = (ctypes.sizeof(_Value), ctypes.sizeof(_PropertyDescriptor), ctypes.alignment(_PropertyDescriptor),
              tuple(getattr(_PropertyDescriptor, name).offset for name in ('flags', 'value', 'getter', 'setter')))
    # THEN real descriptor marshalling must use the reviewed pinned public layout.
    assert actual == (16, 56, 8, (0, 8, 24, 40)), "the public 64-bit descriptor ABI must match every owned value field"


@pytest.mark.parametrize(("expression", "expected"), [
    pytest.param('({value:42})', False, id='ordinary'),
    pytest.param('new Proxy({value:42},traps)', True, id='transparent'),
    pytest.param('(()=>{const pair=Proxy.revocable({value:42},traps);pair.revoke();return pair.proxy;})()', True, id='revoked'),
])
def test_public_proxy_brand_returns_a_bool_without_any_trap(expression, expected):
    # GIVEN a real ordinary, transparent or revoked object and captured native handles.
    from brewdoc._urljs.native import Context
    with Context({}.__getitem__) as context:
        context.eval('globalThis.calls=0;const traps={get(){calls++;},getOwnPropertyDescriptor(){calls++;},getPrototypeOf(){calls++;},has(){calls++;}};'
                     'const object=' + expression + ';globalThis.read=()=>object;globalThis.count=()=>String(calls)', 'fixture')
        context.save(['read', 'count'])
        value = context.call_raw('read')
        try:
            # WHEN the public bool-returning C API inspects the owned raw value.
            actual = context.is_proxy(value)
            # THEN no trap runs, including on an already revoked Proxy.
            assert (type(actual), actual, context.call('count')) == (bool, expected, '0'), "native branding must never reflect through a Proxy"
        finally:
            context.lib.JS_FreeValue(context.context, value)


def test_raw_descriptor_results_preserve_data_accessors_symbols_and_owned_lifetimes():
    # GIVEN a native object with data, accessor and symbol keys plus captured handles.
    from brewdoc._urljs.native import Context
    with Context({}.__getitem__) as context:
        context.eval('globalThis.calls=0;const key=Symbol("owned"),object={data:42,[key]:7};'
                     'Object.defineProperty(object,"accessor",{configurable:true,enumerable:true,get(){calls++;return 99;},set(value){calls++;}});'
                     'globalThis.read=()=>object;globalThis.key=()=>key;globalThis.count=()=>String(calls)', 'fixture')
        context.save(['read', 'key', 'count'])
        observed = []
        # WHEN bounded repeated raw calls query real absent, data and accessor descriptors.
        for _ in range(12):
            value = context.call_raw('read')
            symbol = context.call_raw('key')
            try:
                assert context.own_descriptor(value, 'absent') is None, "absent keys must return no owned descriptor"
                data = context.own_descriptor(value, 'data')
                accessor = context.own_descriptor(value, 'accessor')
                symbolic = context.own_descriptor(value, symbol)
                assert (data is None, accessor is None, symbolic is None) == (False, False, False), "present data, accessor and symbol properties must each return an owned descriptor"
                try:
                    observed.append((data.flags & 7, data.value.tag, data.value.u.integer,
                                     accessor.flags & 7, accessor.value.tag, accessor.getter.tag, accessor.setter.tag,
                                     symbolic.value.tag, symbolic.value.u.integer))
                finally:
                    context.free_descriptor(data)
                    context.free_descriptor(accessor)
                    context.free_descriptor(symbolic)
                assert (data.value.tag, accessor.getter.tag, accessor.setter.tag, symbolic.value.tag) == (3, 3, 3, 3), "freed descriptor handles must be cleared to undefined"
            finally:
                context.lib.JS_FreeValue(context.context, symbol)
                context.lib.JS_FreeValue(context.context, value)
        # THEN native field values and ownership are exact without any getter or setter call.
        assert (observed, context.call('count')) == ([(7, 0, 42, 5, 3, -1, -1, 0, 7)] * 12, '0'), "raw descriptors must preserve native values and never coerce or invoke accessors"


@pytest.mark.parametrize(("body", "flags"), [
    pytest.param('throw {toString(){calls++;return "page stringifier";}};', (True, False), id='raw-exception'),
    pytest.param('while(true){}', (False, True), id='raw-timing-interrupt'),
])
def test_raw_call_failure_preserves_borrowed_values_without_stringification_and_frees_runtime(body, flags):
    # GIVEN retained native data and a raw call that throws or actually interrupts.
    from brewdoc._urljs.native import Context, NativeError
    with Context({}.__getitem__) as context:
        context.eval('globalThis.calls=0;const object={retained:73};globalThis.read=()=>object;'
                     'globalThis.count=()=>String(calls);globalThis.fail=()=>{' + body + '}', 'fixture')
        context.save(['read', 'count', 'fail'])
        retained = context.call_raw('read')
        try:
            # WHEN the raw native call owns a string temporary and borrows the retained object.
            with pytest.raises(NativeError) as caught:
                context.call_raw('fail', 'owned argument' * 512, 17, retained)
            assert (caught.value.resource, caught.value.timing) == flags, "raw failure classification must use native provenance without inspecting the exception"
            descriptor = context.own_descriptor(retained, 'retained')
            assert descriptor is not None, "the retained property's owned descriptor must survive raw failure cleanup"
            try:
                observed = (descriptor.value.tag, descriptor.value.u.integer, context.call('count'))
            finally:
                context.free_descriptor(descriptor)
            # THEN the borrowed reference survives while exception conversion remains uncalled.
            assert observed == (0, 73, '0'), "failure cleanup must preserve borrowed values and never run a page stringifier"
        finally:
            context.lib.JS_FreeValue(context.context, retained)
    assert (context.context, context.runtime, context.allocated_bytes, context.allocations, context.handles) == (
        None, None, 0, {}, {},
    ), "all owned values and allocator blocks must be released after both raw failure paths"
