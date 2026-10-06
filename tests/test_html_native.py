"""Check public QuickJS module APIs and native resource boundaries."""

import pytest
import platform

from brewdoc._urljs.native import Context, NativeError, preflight


native_supported = pytest.mark.skipif(
    platform.system() == 'Windows' or (platform.system() == 'Darwin' and platform.machine() != 'arm64'),
    reason='Native ABI success is proved only on Linux64 and macOS ARM64.',
)


@pytest.mark.parametrize(
    ('source', 'module', 'expected'),
    [
        ('import {value} from "./value.js"; result=String(value)', True, '42'),
        ('import("./value.js").then(m=>result=String(m.value))', False, '42'),
        ('result=import.meta.url', True, 'https://fixture.test/main.js'),
        ('import("./absent.js").catch(()=>result="denied")', False, 'denied'),
        ('result=String(await Promise.resolve(42))', True, '42'),
    ],
)
@native_supported
def test_native_module_language_preserves_sources(source, module, expected):
    # GIVEN a frozen module graph and captured result function.
    modules = {'https://fixture.test/value.js': 'export const value=42'}
    with Context(modules.__getitem__) as context:
        context.eval('globalThis.result="";globalThis.read=()=>result', 'bootstrap')
        context.save(['read'])
        # WHEN the native loader evaluates the unchanged source.
        context.eval(source, 'https://fixture.test/main.js', module=module)
        context.jobs()
        # THEN relative imports and metadata retain their native semantics.
        assert context.call('read') == expected, 'The unchanged module must produce the exact value.'


@native_supported
def test_native_loader_preserves_cycles_and_fetches_each_identity_once():
    # GIVEN a two-module cycle with lazy references.
    modules = {
        'https://fixture.test/a.js': 'import {b} from "./b.js";export const a=2;export const total=()=>a+b',
        'https://fixture.test/b.js': 'import {a} from "./a.js";export const b=3;export const get=()=>a',
    }
    requested = []

    def fetch(name):
        """Record each requested module identity before returning its source."""
        requested.append(name)
        return modules[name]

    with Context(fetch) as context:
        context.eval('globalThis.result="";globalThis.read=()=>result', 'bootstrap')
        context.save(['read'])
        # WHEN a root imports a cycle and then the same canonical module.
        context.eval('import {total} from "./a.js";result=String(total())', 'https://fixture.test/main.js', module=True)
        context.jobs()
        context.eval('import("./a.js").then(m=>result=String(m.total()))', 'https://fixture.test/main.js')
        context.jobs()
        # THEN the native cache retains identities without duplicate fetching.
        assert context.call('read') == '5', 'The cycle must link successfully.'
        assert requested == list(modules), 'Each canonical module must be fetched once.'


@native_supported
def test_native_rejections_track_later_handlers():
    # GIVEN a rejection without a handler.
    with Context({}.__getitem__) as context:
        context.eval('globalThis.promise=Promise.reject("observed")', 'page')
        # WHEN the native rejection observer runs.
        context.jobs()
        # THEN adding a handler removes the same rejection identity.
        assert context.rejections == 1, 'Exactly one promise is unhandled.'
        context.eval('promise.catch(()=>{})', 'handler')
        context.jobs()
        assert context.rejections == 0, 'The handled promise must leave the rejection set.'


@native_supported
def test_native_interrupt_reports_trusted_timing_without_a_heap_failure():
    # GIVEN the unchanged native invocation guard and an endless actual script.
    with Context({}.__getitem__) as context:
        # WHEN the engine's trusted interrupt stops evaluation.
        with pytest.raises(NativeError) as caught:
            context.eval('while(true){}', 'page')
        # THEN timing provenance is distinct from a hard resource or heap failure.
        assert (caught.value.resource, caught.value.timing, context.interrupted, context.heap_exhausted) == (
            False, True, True, False,
        ), 'Only a trusted timing interrupt can permit available-content recovery.'


@native_supported
def test_native_heap_exhaustion_remains_hard_without_timing_reclassification():
    # GIVEN the unchanged native heap bound and an oversized allocation.
    with Context({}.__getitem__) as context:
        # WHEN page code exceeds the actual allocator limit.
        with pytest.raises(NativeError, match='heap') as caught:
            context.eval('new Uint8Array(80*1024*1024)', 'page')
        # THEN a real heap denial retains hard priority over timing recovery.
        assert (caught.value.resource, caught.value.timing, context.interrupted, context.heap_exhausted) == (
            True, False, False, True,
        ), 'Heap exhaustion must remain a hard refusal with trusted allocator evidence.'


@native_supported
def test_native_job_limit_is_cumulative_across_pumps():
    # GIVEN a promise chain that keeps generating native jobs.
    with Context({}.__getitem__) as context:
        context.eval('const again=()=>Promise.resolve().then(again);again()', 'page')
        # WHEN the job pump consumes its lifetime allowance.
        with pytest.raises(NativeError, match='job') as caught:
            context.jobs()
        # THEN the exhausted context refuses later pumping.
        assert caught.value.resource is True, 'The job limit must be a hard refusal.'
        with pytest.raises(NativeError, match='job'):
            context.jobs()


@native_supported
def test_native_heap_failure_cannot_be_hidden_by_page_catch():
    # GIVEN a native allocator with a finite heap.
    with Context({}.__getitem__) as context:
        # WHEN page code catches its rejected allocation.
        with pytest.raises(NativeError, match='heap') as caught:
            context.eval('try {new Uint8Array(80*1024*1024)} catch(error) {}', 'page')
        # THEN the allocator still reports a hard resource refusal.
        assert caught.value.resource is True, 'A script catch must never conceal allocator exhaustion.'


@native_supported
def test_native_import_attributes_are_explicitly_refused():
    # GIVEN a JavaScript-only source loader.
    with Context(lambda name: 'export const value=42') as context:
        # WHEN an import requests unsupported content attributes.
        with pytest.raises(NativeError, match='attributes') as caught:
            context.eval('import {value} from "./value.js" with {type:"json"}', 'https://fixture.test/main.js', module=True)
        # THEN the unsupported feature remains a script-level failure.
        assert caught.value.resource is False, 'Unsupported attributes must not be classified as resource exhaustion.'


@native_supported
def test_native_inline_module_metadata_preserves_document_url():
    # GIVEN distinct compile identity and inline document metadata.
    with Context({}.__getitem__) as context:
        context.eval('globalThis.result="";globalThis.read=()=>result', 'bootstrap')
        context.save(['read'])
        # WHEN an inline root reads import.meta.url.
        context.eval('result=import.meta.url', 'https://fixture.test/main#inline-1', module=True, meta_url='https://fixture.test/main')
        context.jobs()
        # THEN private compile identifiers never leak into metadata.
        assert context.call('read') == 'https://fixture.test/main', 'Inline module metadata must identify the document.'


@native_supported
def test_native_redirected_module_metadata_and_relative_imports_use_final_url():
    # GIVEN a module redirected to a different resource directory.
    modules = {
        'https://fixture.test/start.js': ('import {value} from "./value.js";export const result=import.meta.url+":"+value', 'https://fixture.test/final/root.js'),
        'https://fixture.test/final/value.js': 'export const value=42',
    }
    with Context(modules.__getitem__) as context:
        context.eval('globalThis.result="";globalThis.read=()=>result', 'bootstrap')
        context.save(['read'])
        # WHEN the redirected module resolves a relative dependency.
        context.eval('import {result as value} from "./start.js";result=value', 'https://fixture.test/main.js', module=True)
        context.jobs()
        # THEN both metadata and dependency resolution use its final URL.
        assert context.call('read') == 'https://fixture.test/final/root.js:42', 'Redirected module resolution must preserve final identity.'


@native_supported
def test_native_trusted_handles_are_removed_before_page_code():
    # GIVEN a captured private function.
    with Context({}.__getitem__) as context:
        context.eval('globalThis.secret=(value)=>"trusted:"+value', 'bootstrap')
        context.save(['secret'])
        # WHEN page code replaces the deleted global.
        context.eval('globalThis.secret=()=>"forged"', 'page')
        # THEN native calls still use the captured function identity.
        assert context.call('secret', 'ok') == 'trusted:ok', 'Page replacement must not affect native handles.'


@native_supported
def test_native_module_can_update_installed_dom():
    from importlib.resources import files

    # GIVEN the installed vendor DOM and a native imported value.
    worker = files('brewdoc').joinpath('_vendor/linkedom-worker.js').read_text(encoding='utf-8')
    body = worker.rpartition('\nexport {')[0]
    modules = {'https://fixture.test/value.js': 'export const value="native DOM 42"'}
    with Context(modules.__getitem__) as context:
        context.eval('(()=>{' + body + ';globalThis.document=parseHTML("<html><body></body></html>").document;globalThis.snapshot=()=>document.toString()})();', 'bootstrap')
        context.save(['snapshot'])
        # WHEN imported module content is inserted into the actual DOM.
        context.eval('import {value} from "./value.js";document.body.innerHTML="<p>"+value+"</p>"', 'https://fixture.test/main.js', module=True)
        context.jobs()
        # THEN serialization contains exactly the generated content.
        assert context.call('snapshot') == '<html><head></head><body><p>native DOM 42</p></body></html>', 'The installed DOM must serialize imported content.'


def test_native_preflight_refuses_unproved_pointer_layout(monkeypatch):
    # GIVEN an ABI layout that has no retained proof.
    monkeypatch.setattr('brewdoc._urljs.native.ctypes.sizeof', lambda value: 4)
    # WHEN prerequisite checking runs before any HTTP work.
    with pytest.raises(NativeError, match='64-bit'):
        preflight()
    # THEN no runtime is allocated under the unsupported ABI.


@native_supported
def test_native_dynamic_import_pending_top_level_await_is_observed():
    # GIVEN an imported module that remains suspended at top-level await.
    with Context(lambda name: 'await new Promise(()=>{});export const value=42') as context:
        # WHEN dynamic import jobs run without fulfilling their await.
        context.eval('import("./hang.js")', 'https://fixture.test/main.js')
        context.jobs()
        # THEN supported queues must remain partial while promises are pending.
        assert context.pending == 6, 'The pinned engine creates six unresolved promises for this imported await.'


@native_supported
def test_native_root_module_pending_state_is_observed():
    # GIVEN one root module suspended at top-level await.
    with Context({}.__getitem__) as context:
        # WHEN its jobs cannot settle the awaited promise.
        context.eval('await new Promise(()=>{})', 'https://fixture.test/main.js', module=True)
        context.jobs()
        # THEN the root count describes the evaluation promise.
        assert context.pending_modules == 1, 'One root module must remain pending.'


@native_supported
def test_native_failed_host_fetch_excludes_host_wait_from_instruction_deadline(monkeypatch):
    # GIVEN a deterministic clock and an ancillary module failure after host wait.
    clock = [0.0]
    monkeypatch.setattr('brewdoc._urljs.native.time.monotonic', lambda: clock[0])

    def fetch(name):
        """Advance only the simulated host wait before an ancillary failure."""
        clock[0] += 1.0
        raise KeyError(name)

    with Context(fetch) as context:
        context.eval('globalThis.result="";globalThis.read=()=>result', 'bootstrap')
        context.save(['read'])
        # WHEN the script handles the failure and continues finite execution.
        context.eval('import("./absent.js").catch(()=>{for(let i=0;i<50000;i++){};result="handled"})', 'https://fixture.test/main.js')
        context.jobs()
        # THEN host wait does not become a false native CPU refusal.
        assert context.call('read') == 'handled', 'Expected ancillary failure must retain its script error semantics.'


@native_supported
@pytest.mark.parametrize(
    ('source', 'pending'),
    [('Promise.resolve().then(()=>{throw "x"}).catch(()=>{})', 0), ('Promise.resolve({then(){}})', 1)],
)
def test_native_pending_observation_uses_actual_promise_state(source, pending):
    # GIVEN a handled rejection or a thenable that never settles.
    with Context({}.__getitem__) as context:
        # WHEN the supported native jobs are drained.
        context.eval(source, 'page')
        context.jobs()
        # THEN settled rejections disappear and pending thenables remain observed.
        assert context.pending_promises == pending, 'Pending observation must match actual native promise state.'


@native_supported
def test_native_page_error_text_cannot_forge_resource_category():
    # GIVEN ordinary script error text resembling an engine diagnostic.
    with Context({}.__getitem__) as context:
        # WHEN page code throws that text as an ordinary Error.
        with pytest.raises(NativeError) as caught:
            context.eval('throw new Error("out of memory; stack overflow")', 'page')
        # THEN structural classification keeps this a script failure.
        assert caught.value.resource is False, 'Page error text must not control the resource category.'


@native_supported
def test_native_host_resource_failure_cannot_be_swallowed_by_dynamic_import_catch():
    # GIVEN a host module loader that encounters a resource violation.
    def fetch(name):
        """Refuse the host request with a structural resource category."""
        raise NativeError('host source limit exceeded', resource=True)

    with Context(fetch) as context:
        # WHEN page code catches the native import rejection.
        context.eval('import("./large.js").catch(()=>{})', 'https://fixture.test/main.js')
        with pytest.raises(NativeError, match='host source limit') as caught:
            context.jobs()
        # THEN host failure remains visible outside the JavaScript handler.
        assert caught.value.resource is True, 'An import catch cannot convert a host violation into partial capture.'


@native_supported
@pytest.mark.parametrize('urls', [('./start.js', './final/root.js'), ('./final/root.js', './start.js')])
def test_native_redirect_does_not_replace_an_existing_requested_module_identity(urls):
    # GIVEN different requested URLs with the same final response location.
    source = 'globalThis.loaded++;export const value=loaded'
    modules = {
        'https://fixture.test/start.js': (source, 'https://fixture.test/final/root.js'),
        'https://fixture.test/final/root.js': source,
    }
    with Context(modules.__getitem__) as context:
        context.eval('globalThis.loaded=0;globalThis.result="";globalThis.read=()=>result', 'bootstrap')
        context.save(['read'])
        # WHEN imports visit both request identities and then repeat the first.
        context.eval(f'const first=await import("{urls[0]}");const second=await import("{urls[1]}");const repeated=await import("{urls[0]}");result=[first.value,second.value,repeated.value].join(",")', 'https://fixture.test/main.js', module=True)
        context.jobs()
        # THEN each requested identity evaluates once and keeps its own cache entry.
        assert context.call('read') == '1,2,1', 'Redirect response identities must not overwrite requested module entries.'
