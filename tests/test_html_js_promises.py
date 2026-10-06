"""Check native page promises and sticky observability after public capability changes."""

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
    reason="Promise regressions require the installed native engine in Linux isolation.",
)

UNKNOWN = "promise_completion_unobservable"


def _capture(script, fetch=lambda request: None, *, script_type=""):
    """Run a synthetic strict script through the real installed capture boundary."""
    source = ('<html><body><p id="result">pending</p><script type="' + script_type + '">'
              "'use strict';const emit=value=>document.querySelector('#result').textContent=JSON.stringify(value);"
              + script + '</script></body></html>')
    return render(source, "https://fixture.test/", fetch, time.monotonic() + 10, soft_window=1)


def _assert_capture(captured, expected, *, status="settled", categories=(), promises=0, modules=0):
    """Require exact provenance and native counts before reading the frozen page observation."""
    assert {name: captured[name] for name in ("capture_status", "errors", "error_count", "pending")} == {
        "capture_status": status,
        "errors": [{"category": category, "resource_url": None} for category in categories],
        "error_count": len(categories),
        "pending": {"requests": 0, "timers": 0, "modules": modules, "promises": promises, "jobs": False},
    }, "capture status must retain exact native pending counts and sticky promise provenance"
    matches = re.findall(r'<p id="result">([^<]*)</p>', captured["html"])
    assert len(matches) == 1, "the frozen DOM must retain exactly one promise result element"
    assert json.loads(matches[0]) == expected, "the promise operation must produce its exact observable values and effects"


def test_page_promise_facade_is_mutable_while_native_roots_stay_protected():
    # GIVEN the real page factory and its publicly observable native ancestors.
    script = """
const prototype=Promise.prototype;const native=Object.getPrototypeOf(Promise);
const base=Object.getPrototypeOf(prototype);
const methods=['then','catch','finally'].map(name=>{
  const descriptor=Object.getOwnPropertyDescriptor(prototype,name);
  return [name,typeof descriptor.value,descriptor.writable,descriptor.configurable,descriptor.enumerable,descriptor.value===base[name]];
});
const constructor=Object.getOwnPropertyDescriptor(prototype,'constructor');
const tag=Object.getOwnPropertyDescriptor(prototype,Symbol.toStringTag);
emit({factory_frozen:Object.isFrozen(Promise),native_frozen:Object.isFrozen(native),native_prototype_frozen:Object.isFrozen(base),
  native_chain:native.prototype===base,methods,
  constructor:[constructor.value===Promise,constructor.writable,constructor.configurable,constructor.enumerable],
  tag:[tag.value,tag.writable,tag.configurable,tag.enumerable]});
"""
    # WHEN page code reads ordinary data descriptors without mutating them.
    captured = _capture(script)
    # THEN pristine native methods have writable page-owned descriptors and protected native ancestors.
    _assert_capture(captured, {
        "factory_frozen": True, "native_frozen": True, "native_prototype_frozen": True, "native_chain": True,
        "methods": [["then", "function", True, True, False, True], ["catch", "function", True, True, False, True],
                    ["finally", "function", True, True, False, True]],
        "constructor": [True, True, True, False], "tag": ["Promise", False, True, False],
    })


@pytest.mark.parametrize("script,expected", [
    pytest.param("Promise.resolve(42).then(value=>emit({value}));", {"value": 42}, id="native-static-resolve"),
    pytest.param("new Promise(resolve=>resolve(42)).then(value=>emit({value}));", {"value": 42}, id="native-construction"),
    pytest.param("""
class Derived extends Promise {}
const promise=Derived.resolve(42);const chained=promise.then(value=>value+1);
chained.then(value=>emit({value,original:promise instanceof Derived,chained:chained instanceof Derived,
  new_target:Object.getPrototypeOf(promise)===Derived.prototype,tag:Object.prototype.toString.call(promise)}));
""", {"value": 43, "original": True, "chained": True, "new_target": True, "tag": "[object Promise]"},
        id="derived-new-target-and-default-species"),
    pytest.param("""
class Derived extends Promise {static get [Symbol.species](){return Promise;}}
const promise=new Derived(resolve=>resolve(7));const chained=promise.then(value=>value+1);
chained.then(value=>emit({value,derived:promise instanceof Derived,chained:chained instanceof Derived,
  page:chained instanceof Promise}));
""", {"value": 8, "derived": True, "chained": False, "page": True}, id="native-custom-species"),
    pytest.param("""
let error=null,called=false;try{Promise(()=>{called=true;});}catch(problem){error=problem.name;}
emit({error,called});
""", {"error": "TypeError", "called": False}, id="non-new-call-refuses-before-executor"),
    pytest.param("let error=null;try{new Promise(7);}catch(problem){error=problem.name;}emit({error});",
        {"error": "TypeError"}, id="invalid-executor-native-typeerror"),
    pytest.param("new Promise(()=>{throw new Error('private');}).catch(error=>emit({name:error.name}));",
        {"name": "Error"}, id="executor-error-handled-native-rejection"),
    pytest.param("Promise.reject(new Error('private')).catch(error=>emit({name:error.name}));",
        {"name": "Error"}, id="handled-rejection-retains-settled"),
])
def test_untouched_page_promises_preserve_native_language_and_settled_capture(script, expected):
    # GIVEN finite native promise work without a public capability mutation.
    # WHEN the existing native job pump drains that work.
    captured = _capture(script)
    # THEN native construction, subclass/species behavior and handled failures remain settled.
    _assert_capture(captured, expected)


def test_unresolved_native_subclass_retains_exact_pending_count():
    # GIVEN one unresolved actual native instance created through a page subclass.
    script = "class Derived extends Promise{};const pending=new Derived(()=>{});emit({derived:pending instanceof Derived});"
    # WHEN capture reaches the bounded fixture soft window.
    captured = _capture(script)
    # THEN native hooks report one real pending promise without a false mutation warning.
    _assert_capture(captured, {"derived": True}, status="partial", promises=1)


def test_unhandled_native_rejection_retains_sanitized_partial_provenance():
    # GIVEN a native rejection with useful independent visible content.
    script = "Promise.reject(new Error('private exception'));emit({kept:true});"
    # WHEN the native rejection hook observes the unhandled failure.
    captured = _capture(script)
    # THEN only the existing finite rejection category is public.
    _assert_capture(captured, {"kept": True}, status="partial", categories=("unhandled_promise_rejection",))


@pytest.mark.parametrize("mutation,expected", [
    pytest.param("""
globalThis.Promise=function Replacement(){};const changed=Promise!==original;emit({changed,restored:false});
""", {"changed": True, "restored": False}, id="permanent-global-assignment"),
    pytest.param("""
globalThis.Promise=function Replacement(){};const changed=Promise!==original;
globalThis.Promise=original;emit({changed,restored:Promise===original});
""", {"changed": True, "restored": True}, id="transient-global-assignment"),
])
def test_global_promise_identity_changes_remain_observably_partial(mutation, expected):
    # GIVEN a global constructor replacement that actually takes effect.
    script = "const original=Promise;" + mutation
    # WHEN a permanent or restored replacement leaves native pending zero.
    captured = _capture(script)
    # THEN sticky unknown provenance survives independently of final native counts.
    _assert_capture(captured, expected, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("operation", [
    pytest.param("Object.defineProperty(globalThis,'Promise',{value:replacement,configurable:true,writable:true})", id="object-definition"),
    pytest.param("Reflect.defineProperty(globalThis,'Promise',{value:replacement,configurable:true,writable:true})", id="reflect-definition"),
])
def test_defined_global_promise_replacement_and_exact_descriptor_restoration_stay_partial(operation):
    # GIVEN the original accessor descriptor and a genuinely different constructor value.
    script = "const original=Promise;const descriptor=Object.getOwnPropertyDescriptor(globalThis,'Promise');const replacement=function Replacement(){};" + operation + """;
const changed=Promise===replacement;Object.defineProperty(globalThis,'Promise',descriptor);
emit({changed,restored:Promise===original});
"""
    # WHEN a descriptor replacement is later restored exactly.
    captured = _capture(script)
    # THEN real replacement and restoration cannot clear unknown completion.
    _assert_capture(captured, {"changed": True, "restored": True}, status="partial", categories=(UNKNOWN,))


def test_deleted_global_promise_and_exact_accessor_restoration_stay_partial():
    # GIVEN a configurable global accessor captured before a real deletion.
    script = """
const original=Promise;const descriptor=Object.getOwnPropertyDescriptor(globalThis,'Promise');
const removed=delete globalThis.Promise;const changed=typeof globalThis.Promise==='undefined';
Object.defineProperty(globalThis,'Promise',descriptor);emit({removed,changed,restored:Promise===original});
"""
    # WHEN page code deletes and restores the global property.
    captured = _capture(script)
    # THEN accessor exposure/deletion remains sticky after exact restoration.
    _assert_capture(captured, {"removed": True, "changed": True, "restored": True}, status="partial", categories=(UNKNOWN,))


def test_untracked_work_created_during_constructor_replacement_stays_partial_after_restore():
    # GIVEN an actual replacement constructing a nonnative pending object.
    script = """
const original=Promise;function Replacement(executor){this.then=()=>this;}
globalThis.Promise=Replacement;const pending=new Promise(()=>{});
const changed=pending instanceof Replacement;globalThis.Promise=original;
emit({changed,own_then:Object.hasOwn(pending,'then'),restored:Promise===original});
"""
    # WHEN the original constructor is restored but native hooks never owned that object.
    captured = _capture(script)
    # THEN zero native pending promises cannot produce a settled claim.
    _assert_capture(captured, {"changed": True, "own_then": True, "restored": True}, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("name", ["then", "constructor"])
def test_page_owned_promise_members_really_change_before_restoration(name):
    # GIVEN writable page-owned method/constructor descriptors over a protected native base.
    script = "const prototype=Promise.prototype;const name=" + json.dumps(name) + """;
const original=prototype[name];const replacement=function Replacement(){};
let changed=false,restored=false,error=null;
try{prototype[name]=replacement;changed=prototype[name]===replacement;
  prototype[name]=original;restored=prototype[name]===original;}catch(problem){error=problem.name;}
emit({changed,restored,error,native_frozen:Object.isFrozen(Object.getPrototypeOf(prototype))});
"""
    # WHEN strict assignment really replaces and then restores the page-owned member.
    captured = _capture(script)
    # THEN successful effects remain unknown after restoration while the native base stays frozen.
    _assert_capture(captured, {"changed": True, "restored": True, "error": None, "native_frozen": True},
                    status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("mutation,expected", [
    pytest.param("""
const descriptor=Object.getOwnPropertyDescriptor(prototype,'then');
const replacement=function Replacement(){};let changed=false,restored=false,error=null;
try{Object.defineProperty(prototype,'then',{...descriptor,value:replacement});changed=prototype.then===replacement;
  Object.defineProperty(prototype,'then',descriptor);restored=prototype.then===original;}catch(problem){error=problem.name;}
emit({changed,restored,error});
""", {"changed": True, "restored": True, "error": None}, id="define-then-and-restore-descriptor"),
    pytest.param("""
const descriptor=Object.getOwnPropertyDescriptor(prototype,'then');let changed=false,restored=false,error=null;
try{delete prototype.then;changed=!Object.hasOwn(prototype,'then');
  Object.defineProperty(prototype,'then',descriptor);restored=Object.hasOwn(prototype,'then')&&prototype.then===original;}catch(problem){error=problem.name;}
emit({changed,restored,error});
""", {"changed": True, "restored": True, "error": None}, id="delete-then-and-restore-descriptor"),
    pytest.param("""
const base=Object.getPrototypeOf(prototype);const alternative={};let changed=false,restored=false,error=null;
try{Object.setPrototypeOf(prototype,alternative);changed=Object.getPrototypeOf(prototype)===alternative;
  Object.setPrototypeOf(prototype,base);restored=Object.getPrototypeOf(prototype)===base;}catch(problem){error=problem.name;}
emit({changed,restored,error});
""", {"changed": True, "restored": True, "error": None}, id="prototype-chain-change-and-restoration"),
    pytest.param("""
const was_extensible=Object.isExtensible(prototype);Object.preventExtensions(prototype);
emit({was_extensible,extensible:Object.isExtensible(prototype)});
""", {"was_extensible": True, "extensible": False}, id="actual-prevent-extensions-effect"),
    pytest.param("""
const instance=Promise.resolve(7);const replacement=function Replacement(){};let changed=false,restored=false,error=null;
try{instance.then=replacement;changed=Object.hasOwn(instance,'then')&&instance.then===replacement;
  delete instance.then;restored=instance.then===original;}catch(problem){error=problem.name;}
emit({changed,restored,error});
""", {"changed": True, "restored": True, "error": None}, id="inherited-instance-shadow-and-restoration"),
])
def test_page_promise_prototype_mutations_keep_unknown_after_actual_effects(mutation, expected):
    # GIVEN a page-owned prototype mutation with its original capability retained.
    script = "const prototype=Promise.prototype;const original=prototype.then;" + mutation
    # WHEN the operation actually changes the page surface, including restored changes.
    captured = _capture(script)
    # THEN unknown completion survives without relying on a merely attempted failed write.
    _assert_capture(captured, expected, status="partial", categories=(UNKNOWN,))


def test_promise_prototype_accessors_receive_proxy_or_native_instance_without_raw_target_leak():
    # GIVEN a page-owned getter/setter reporting its actual receiver identity.
    script = """
const prototype=Promise.prototype;let receiver=null;
Object.defineProperty(prototype,'receiver',{configurable:true,get(){return this;},set(value){receiver=this;}});
const proxy_getter=prototype.receiver===prototype;prototype.receiver=1;const proxy_setter=receiver===prototype;
const instance=Promise.resolve(7);const native_getter=instance.receiver===instance;
instance.receiver=1;const native_setter=receiver===instance;delete prototype.receiver;
emit({proxy_getter,proxy_setter,native_getter,native_setter,removed:!Object.hasOwn(prototype,'receiver')});
"""
    # WHEN the prototype Proxy forwards reads and writes to real receivers.
    captured = _capture(script)
    # THEN neither getter nor setter exposes the raw mutable backing target.
    _assert_capture(captured, {
        "proxy_getter": True, "proxy_setter": True, "native_getter": True, "native_setter": True, "removed": True,
    }, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("target", ["globalThis", "new Proxy(globalThis,{})"], ids=["global", "transparent-global-proxy"])
@pytest.mark.parametrize("operation", [
    pytest.param("Object.getOwnPropertyDescriptor(target,'Promise')", id="object-descriptor"),
    pytest.param("Reflect.getOwnPropertyDescriptor(target,'Promise')", id="reflect-descriptor"),
    pytest.param("Object.getOwnPropertyDescriptors(target).Promise", id="descriptor-map"),
])
def test_global_promise_descriptor_capabilities_are_returned_intact_and_mark_unknown(target, operation):
    # GIVEN direct or transparent-proxy access to the global restoration descriptor.
    script = "const target=" + target + ";const descriptor=" + operation + """;
const other=Reflect.getOwnPropertyDescriptor(target,'Promise');
emit({getter:typeof descriptor.get,setter:typeof descriptor.set,configurable:descriptor.configurable,
  enumerable:descriptor.enumerable,same_getter:descriptor.get===other.get,same_setter:descriptor.set===other.set,
  same_value:descriptor.get.call(target)===Promise});
"""
    # WHEN reflection exposes actual getter/setter restoration capabilities.
    captured = _capture(script)
    # THEN original functions/descriptors survive while conservative unknown state is sticky.
    _assert_capture(captured, {
        "getter": "function", "setter": "function", "configurable": True, "enumerable": False,
        "same_getter": True, "same_setter": True, "same_value": True,
    }, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("target", ["globalThis", "new Proxy(globalThis,{})"], ids=["global", "transparent-global-proxy"])
@pytest.mark.parametrize("kind,member", [("Getter", "get"), ("Setter", "set")])
def test_borrowed_legacy_promise_accessor_lookups_keep_identity_and_unknown(target, kind, member):
    # GIVEN a borrowed native legacy lookup against a direct or proxy alias.
    script = "const target=" + target + ";const kind=" + json.dumps(kind) + ";const member=" + json.dumps(member) + """;
const capability=Object.prototype['__lookup'+kind+'__'].call(target,'Promise');
const descriptor=Object.getOwnPropertyDescriptor(target,'Promise');
emit({type:typeof capability,same:capability===descriptor[member]});
"""
    # WHEN the wrapper returns the actual accessor function through a borrowed call.
    captured = _capture(script)
    # THEN returned capability identity is unchanged and exposure marks the capture partial.
    _assert_capture(captured, {"type": "function", "same": True}, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("kind,expected", [
    ("Getter", {"called": True, "read_is_replacement": True, "restored": True}),
    ("Setter", {"called": True, "read_is_replacement": False, "restored": True}),
])
def test_borrowed_legacy_promise_accessor_definition_really_changes_before_restore(kind, expected):
    # GIVEN a transparent global alias and its original exact accessor descriptor.
    script = "const kind=" + json.dumps(kind) + """;const target=new Proxy(globalThis,{});
const original=Promise;const descriptor=Object.getOwnPropertyDescriptor(globalThis,'Promise');
let called=false;const replacement=function Replacement(){};
const getter=function(){called=true;return replacement;};const setter=function(value){called=value===replacement;};
Object.prototype['__define'+kind+'__'].call(target,'Promise',({Getter:getter,Setter:setter})[kind]);
globalThis.Promise=replacement;const read=globalThis.Promise;
Object.defineProperty(globalThis,'Promise',descriptor);globalThis.Promise=original;
emit({called,read_is_replacement:read===replacement,restored:Promise===original});
"""
    # WHEN a legacy definition genuinely installs and invokes a different accessor.
    captured = _capture(script)
    # THEN its actual effect and exact restoration cannot reset unknown completion.
    _assert_capture(captured, expected, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("operation", ["Object.getOwnPropertyDescriptor", "Reflect.getOwnPropertyDescriptor"])
def test_guarded_promise_property_key_is_coerced_once_with_string_hint(operation):
    # GIVEN a coercible key returning Promise on an unrelated valid target.
    script = """
const hints=[];const key={[Symbol.toPrimitive](hint){hints.push(hint);return 'Promise';}};
const target={};const descriptor=""" + operation + """(target,key);
emit({hints,missing:typeof descriptor});
"""
    # WHEN reflection uses the normalized property key without a second conversion.
    captured = _capture(script)
    # THEN native return semantics survive and normalized Promise access conservatively marks unknown.
    _assert_capture(captured, {"hints": ["string"], "missing": "undefined"}, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("operation", [
    pytest.param("Object.getOwnPropertyDescriptor(null,key)", id="object-null-target"),
    pytest.param("Reflect.getOwnPropertyDescriptor(7,key)", id="reflect-primitive-target"),
    pytest.param("Object.defineProperty(7,key,{value:1})", id="define-primitive-target"),
    pytest.param("Object.prototype.__lookupGetter__.call(null,key)", id="borrowed-getter-null-this"),
    pytest.param("Object.prototype.__lookupSetter__.call(undefined,key)", id="borrowed-setter-undefined-this"),
])
def test_guarded_reflection_preserves_target_validation_before_key_coercion(operation):
    # GIVEN an invalid receiver and a coercion side effect that must remain unobserved.
    script = "let calls=0,error=null;const key={[Symbol.toPrimitive](){calls++;return 'Promise';}};try{" + operation + """
}catch(problem){error=problem.name;}emit({error,calls});
"""
    # WHEN the native operation rejects its target before converting the key.
    captured = _capture(script)
    # THEN TypeError occurs without key side effects or a spurious unknown marker.
    _assert_capture(captured, {"error": "TypeError", "calls": 0})


def test_guarded_reflection_preserves_symbol_property_keys_without_unknown():
    # GIVEN a real symbol key returned by exactly one string-hint conversion.
    script = """
const token=Symbol('token');const target={[token]:7};const hints=[];
const key={[Symbol.toPrimitive](hint){hints.push(hint);return token;}};
const descriptor=Object.getOwnPropertyDescriptor(target,key);emit({hints,value:descriptor.value});
"""
    # WHEN reflection accesses a non-Promise symbol property.
    captured = _capture(script)
    # THEN symbol identity and single coercion survive without a monitoring false positive.
    _assert_capture(captured, {"hints": ["string"], "value": 7})


def test_native_promise_roots_and_saved_control_handles_remain_protected():
    # GIVEN the public native ancestors and the actual saved host capability names.
    script = """
const native=Object.getPrototypeOf(Promise);const prototype=Object.getPrototypeOf(Promise.prototype);
let constructor_rejected=false,prototype_rejected=false;
try{native.resolve=()=> 'forged';}catch(error){constructor_rejected=error.name==='TypeError';}
try{prototype.then=()=> 'forged';}catch(error){prototype_rejected=error.name==='TypeError';}
emit({constructor_rejected,prototype_rejected,native_chain:native.prototype===prototype,
  hidden:['__smallBoot','__smallRequests','__smallComplete','__smallSnapshot','__smallParseStart','__smallParseResume','__smallParseEnd'].map(name=>typeof globalThis[name])});
"""
    # WHEN page code attempts to overwrite protected native methods and inspect host handles.
    captured = _capture(script)
    # THEN native methods reject writes and every saved private capability stays absent.
    _assert_capture(captured, {"constructor_rejected": True, "prototype_rejected": True, "native_chain": True,
                               "hidden": ["undefined"] * 7})


def test_private_host_fetch_completes_after_page_promise_replacement_and_host_tampering():
    # GIVEN one guarded same-origin response and attempted page replacements of host capabilities.
    requested = []
    def fetch(request):
        """Return a controlled response through the original host request seam."""
        requested.append(request["url"])
        return {"status": 200, "headers": (), "final_url": request["url"], "body": b'guarded'}
    script = """
globalThis.Promise=function Replacement(){};
let json_rejected=false,map_rejected=false;
try{JSON.stringify=()=> 'forged';}catch(error){json_rejected=true;}
try{Map.prototype.get=()=>null;}catch(error){map_rejected=true;}
fetch('/data').then(response=>response.text()).then(value=>emit({value,json_rejected,map_rejected}));
"""
    assert requested == [], "the host resource must start unrequested"
    # WHEN private native host promises complete independently of the replaced page constructor.
    captured = _capture(script, fetch)
    # THEN real guarded content and protected control serialization survive with truthful unknown status.
    assert requested == ["https://fixture.test/data"], "only the requested guarded resource may reach transport"
    _assert_capture(captured, {"value": "guarded", "json_rejected": True, "map_rejected": True},
                    status="partial", categories=(UNKNOWN,))


def test_inline_native_module_await_keeps_settled_capture_without_unknown():
    # GIVEN finite top-level await using the untouched page native factory.
    script = "const value=await Promise.resolve(42);emit({value});"
    # WHEN the native module evaluator and job pump finish the real module.
    captured = _capture(script, script_type="module")
    # THEN finite native work retains its existing settled semantics.
    _assert_capture(captured, {"value": 42})


def test_dynamic_native_module_retains_existing_unknown_graph_metadata_without_new_warning():
    # GIVEN one real imported module delivered through a controlled source seam.
    requested = []
    def fetch(request):
        """Deliver an unchanged finite module without live network access."""
        requested.append(request["url"])
        return {"status": 200, "headers": (), "final_url": request["url"], "body": b'export const value=42;'}
    script = "import('./value.js').then(module=>emit({value:module.value}));"
    assert requested == [], "the module source must start unrequested"
    # WHEN native module linking and jobs deliver its actual exported value.
    captured = _capture(script, fetch)
    # THEN the existing nullable graph-completion count survives without a new mutation marker.
    assert requested == ["https://fixture.test/value.js"], "the one declared module identity must be fetched once"
    _assert_capture(captured, {"value": 42}, status="partial", modules=None)


def test_page_native_promises_accept_the_exact_cumulative_job_limit():
    # GIVEN exactly 2,000 real native Promise reaction jobs.
    script = "for(let index=0;index<2000;index++)Promise.resolve(index).then(()=>{});emit({queued:2000});"
    # WHEN the unchanged cumulative native job pump drains them.
    captured = _capture(script)
    # THEN the inclusive quota accepts every job with exact accounting and settled provenance.
    _assert_capture(captured, {"queued": 2000})
    assert captured["counts"] == {"promise_jobs": 2000, "timer_callbacks": 0}, "exact native job accounting must survive the page factory"


def test_page_native_promises_refuse_one_over_the_cumulative_job_limit():
    # GIVEN one job over the unchanged 2,000 native-reaction cap.
    script = "for(let index=0;index<2001;index++)Promise.resolve(index).then(()=>{});emit({queued:2001});"
    # WHEN the cumulative native job pump observes the excess.
    with pytest.raises(BrewdocError, match="resource"):
        _capture(script)
    # THEN no Promise facade or partial marker can downgrade hard resource refusal.


@pytest.mark.parametrize("mutation", [
    "Object.defineProperty(document.body,'toString',{value:()=> 'forged'})",
    "Object.defineProperty(document.querySelector('p').firstChild,'toString',{value:()=> 'forged'})",
])
def test_promise_unknown_never_downgrades_dom_serializer_hard_refusal(mutation):
    # GIVEN unknown page promise behavior and a real forged DOM serializer.
    script = "globalThis.Promise=function Replacement(){};" + mutation
    # WHEN the trusted serializer examines the actual tree.
    with pytest.raises(BrewdocError, match="resource"):
        _capture(script)
    # THEN unknown completion cannot permit a forged partial snapshot.


def test_parent_accepts_the_exact_promise_unknown_category_with_existing_metadata_shape():
    from brewdoc.htmljs import _validate_capture
    # GIVEN a real ordinary capture with one declared unknown-promise error category.
    captured = _capture("emit({kept:true});")
    captured.update(capture_status="partial", errors=[{"category": UNKNOWN, "resource_url": None}], error_count=1)
    # WHEN the parent checks the exact new category in its existing shape.
    result = _validate_capture(captured, 1)
    # THEN only the finite approved category is accepted.
    assert result is None, "parent validation must accept exact promise unknown provenance without a schema change"


def test_parent_rejects_forged_promise_unknown_category_variants():
    from brewdoc.htmljs import _validate_capture
    # GIVEN a forged category resembling the finite approved one.
    captured = _capture("emit({kept:true});")
    captured.update(capture_status="partial", errors=[{"category": UNKNOWN + "_forged", "resource_url": None}], error_count=1)
    # WHEN the parent validates its error allow-list.
    with pytest.raises(BrewdocError, match="metadata invalid"):
        _validate_capture(captured, 1)
    # THEN a similar name does not confer permission to claim unknown completion.


TRUSTED_ACCESSOR_GUARD_PROBE = """
import json
from pathlib import Path
import sys
from brewdoc import htmljs
from brewdoc._urljs.native import Context

def inspect_guard():
    '''Seed a trusted alias to isolate returned-accessor identity monitoring.'''
    with Context({}.__getitem__,guarded_stack=True) as context:
        context.eval('''(()=>{
          const read=Object.getOwnPropertyDescriptor,define=Object.defineProperty,create=Object.create;
          let descriptor;
          globalThis.__trustedSeed=()=>{
            descriptor=read(globalThis,'Promise');const target=create(null);
            define(target,'renamed',descriptor);define(globalThis,'guardTarget',{value:target,configurable:true});
            return JSON.stringify({getter:typeof descriptor.get,setter:typeof descriptor.set});
          };
          globalThis.__trustedVerify=()=>JSON.stringify('''+sys.argv[2]+''');
        })();''','trusted-seed-bootstrap')
        context.save(['__trustedSeed','__trustedVerify'])
        package=Path(htmljs.__file__).parent
        bundle=(package/'_vendor/linkedom-worker.js').read_text().rpartition('\\nexport {')[0]
        glue=(package/'_urljs/glue.js').read_text()
        context.eval('(()=>{'+bundle+'\\n'+glue+'\\n})();','installed-bootstrap')
        context.save(['__smallParseStart','__smallParseResume','__smallParseEnd','__smallBoot','__smallErrors'])
        pending=int(context.call('__smallParseStart','<html><body><p>article</p></body></html>'))
        while pending:
            pending=int(context.call('__smallParseResume'))
        context.call('__smallParseEnd')
        context.call('__smallBoot','https://fixture.test/',json.dumps({'href':'https://fixture.test/',
          'protocol':'https:','hostname':'fixture.test','host':'fixture.test','pathname':'/',
          'search':'','hash':'','origin':'https://fixture.test'}))
        before=json.loads(context.call('__smallErrors'))
        seed=json.loads(context.call('__trustedSeed'))
        after_seed=json.loads(context.call('__smallErrors'))
        context.eval('globalThis.__guardOperation=()=>{globalThis.exposed='+sys.argv[1]+';};','public-guard-probe')
        context.save(['__guardOperation'])
        context.call('__guardOperation')
        after=json.loads(context.call('__smallErrors'))
        same=json.loads(context.call('__trustedVerify'))
        return {'before':before,'seed':seed,'after_seed':after_seed,'after':after,'same':same}

htmljs._CHILD_ONLY=True
print(json.dumps(htmljs._guarded_call(inspect_guard,protect=True)))
"""


@pytest.mark.parametrize("target", ["guardTarget", "new Proxy(guardTarget,{})"], ids=["trusted-alias", "transparent-alias-proxy"])
@pytest.mark.parametrize("operation,verification", [
    pytest.param("Object.getOwnPropertyDescriptor(TARGET,'renamed')", "exposed.get===descriptor.get&&exposed.set===descriptor.set",
                 id="object-renamed-descriptor"),
    pytest.param("Reflect.getOwnPropertyDescriptor(TARGET,'renamed')", "exposed.get===descriptor.get&&exposed.set===descriptor.set",
                 id="reflect-renamed-descriptor"),
    pytest.param("Object.getOwnPropertyDescriptors(TARGET)", "exposed.renamed.get===descriptor.get&&exposed.renamed.set===descriptor.set",
                 id="renamed-descriptor-map"),
    pytest.param("Object.prototype.__lookupGetter__.call(TARGET,'renamed')", "exposed===descriptor.get",
                 id="borrowed-renamed-getter"),
    pytest.param("Object.prototype.__lookupSetter__.call(TARGET,'renamed')", "exposed===descriptor.set",
                 id="borrowed-renamed-setter"),
])
def test_trusted_renamed_accessor_exposure_marks_unknown_by_returned_function_identity(target, operation, verification):
    # GIVEN a trusted guard-unit fixture, not a demonstrated reachable page exploit.
    script = operation.replace("TARGET", target)
    # WHEN a public guard exposes a seeded original accessor under a non-Promise name.
    checked = subprocess.run([sys.executable, "-I", "-c", TRUSTED_ACCESSOR_GUARD_PROBE, script, verification],
                             capture_output=True, timeout=10)
    # THEN identity alone marks unknown without fixture seeding or descriptor rewriting causing it first.
    assert (checked.returncode, checked.stderr) == (0, b""), "the real guarded accessor unit must finish normally"
    assert json.loads(checked.stdout) == {
        "before": [], "seed": {"getter": "function", "setter": "function"}, "after_seed": [],
        "after": [{"category": UNKNOWN}], "same": True,
    }, "renamed returned accessor functions must trigger unknown while preserving their exact identities"


@pytest.mark.parametrize("script,expected", [
    pytest.param("""
const original=Promise;const replacement=function Replacement(){};
Object.defineProperties(globalThis,{Promise:{value:replacement,writable:true,configurable:true}});
emit({changed:Promise===replacement,restored:Promise===original});
""", {"changed": True, "restored": False}, id="permanent-native-define-properties"),
    pytest.param("""
const original=Promise;const descriptor=Object.getOwnPropertyDescriptor(globalThis,'Promise');
const replacement=function Replacement(executor){this.then=()=>this;};
Object.defineProperties(globalThis,{Promise:{value:replacement,writable:true,configurable:true}});
const pending=new Promise(()=>{});const changed=pending instanceof replacement;
Object.defineProperties(globalThis,{Promise:descriptor});
emit({changed,own_then:Object.hasOwn(pending,'then'),restored:Promise===original});
""", {"changed": True, "own_then": True, "restored": True}, id="temporary-native-define-properties-with-untracked-work"),
])
def test_native_define_properties_promise_replacements_remain_unknown(script, expected):
    # GIVEN native descriptor definitions that really replace the global constructor.
    # WHEN permanent changes or guarded exact restoration leave native pending zero.
    captured = _capture(script)
    # THEN final descriptor mismatch or prior capability exposure retains unknown completion.
    _assert_capture(captured, expected, status="partial", categories=(UNKNOWN,))


@pytest.mark.parametrize("operation", [
    pytest.param("Reflect.getOwnPropertyDescriptor(7,'field')", id="guard-invalid-target"),
    pytest.param("Object.prototype.__defineGetter__.call({},'field',7)", id="guard-invalid-legacy-callback"),
    pytest.param("Promise(()=>{})", id="factory-non-new-call"),
])
def test_promise_guard_and_factory_errors_use_intrinsic_typeerror_after_global_replacement(operation):
    # GIVEN an observable fake TypeError global and the original intrinsic identity.
    script = "const intrinsic=TypeError;let fake_calls=0;globalThis.TypeError=function FakeTypeError(){fake_calls++;return new Error('forged');};let name=null,original=false;try{" + operation + """
}catch(error){name=error.name;original=error instanceof intrinsic;}
emit({name,original,fake_calls});
"""
    # WHEN an approved guard/factory site rejects an invalid call.
    captured = _capture(script)
    # THEN the saved intrinsic exception is used without invoking page-controlled error code.
    _assert_capture(captured, {"name": "TypeError", "original": True, "fake_calls": 0})
