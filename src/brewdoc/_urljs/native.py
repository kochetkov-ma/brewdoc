"""Own a bounded QuickJS runtime through its pinned public 64-bit ABI."""

import ctypes
import importlib.metadata
import importlib.util
import platform
import threading
import time
from urllib.parse import urljoin, urlsplit


class NativeError(RuntimeError):
    """Distinguish trusted timing stops, resource refusal and script exceptions."""

    def __init__(self, message, *, resource=False, timing=False):
        self.resource = resource
        self.timing = timing and not resource
        super().__init__('resource: ' + message if resource else message)


class _Union(ctypes.Union):
    _fields_ = [('integer', ctypes.c_int32), ('number', ctypes.c_double), ('pointer', ctypes.c_void_p)]


class _Value(ctypes.Structure):
    _fields_ = [('u', _Union), ('tag', ctypes.c_int64)]


class _PropertyDescriptor(ctypes.Structure):
    """Mirror the pinned public 64-bit descriptor layout."""
    _fields_ = [('flags', ctypes.c_int), ('value', _Value), ('getter', _Value), ('setter', _Value)]


_PTR = ctypes.c_void_p
_TEXT = ctypes.c_char_p
_SIZE = ctypes.c_size_t
_INT = ctypes.c_int
_BOOL = ctypes.c_bool
_UNDEFINED = _Value(_Union(integer=0), 3)
_INTERRUPT = ctypes.CFUNCTYPE(_INT, _PTR, _PTR)
_REJECTION = ctypes.CFUNCTYPE(None, _PTR, _Value, _Value, _BOOL, _PTR)
_PROMISE = ctypes.CFUNCTYPE(None, _PTR, _INT, _Value, _Value, _PTR)
_NORMALIZE = ctypes.CFUNCTYPE(_PTR, _PTR, _TEXT, _TEXT, _PTR)
_LOAD = ctypes.CFUNCTYPE(_PTR, _PTR, _TEXT, _PTR, _Value)
_ATTRIBUTES = ctypes.CFUNCTYPE(_INT, _PTR, _PTR, _Value)
_CALLOC = ctypes.CFUNCTYPE(_PTR, _PTR, _SIZE, _SIZE)
_MALLOC = ctypes.CFUNCTYPE(_PTR, _PTR, _SIZE)
_FREE = ctypes.CFUNCTYPE(None, _PTR, _PTR)
_REALLOC = ctypes.CFUNCTYPE(_PTR, _PTR, _PTR, _SIZE)
_USABLE = ctypes.CFUNCTYPE(_SIZE, _PTR)


class _Allocators(ctypes.Structure):
    _fields_ = [('calloc', _CALLOC), ('malloc', _MALLOC), ('free', _FREE), ('realloc', _REALLOC), ('usable', _USABLE)]


def preflight():
    """Validate installed version, platform, exports and JSValue ABI before HTTP."""
    if (ctypes.sizeof(_PTR) != 8 or ctypes.sizeof(_Value) != 16
            or ctypes.sizeof(_PropertyDescriptor) != 56 or ctypes.alignment(_PropertyDescriptor) != 8
            or tuple(getattr(_PropertyDescriptor, name).offset for name in ('flags', 'value', 'getter', 'setter')) != (0, 8, 24, 40)):
        raise NativeError('JavaScript requires a proved 64-bit ABI')
    system, machine = platform.system(), platform.machine().lower()
    if system not in ('Darwin', 'Linux') or machine not in ('arm64', 'aarch64', 'x86_64', 'amd64') or (system == 'Darwin' and machine != 'arm64'):
        raise NativeError('JavaScript native ABI is unavailable on this platform')
    try:
        if importlib.metadata.version('quickjs-ng') != '0.17.0.1':
            raise NativeError('JavaScript requires quickjs-ng==0.17.0.1')
        spec = importlib.util.find_spec('_quickjs')
        if spec is None or spec.origin is None:
            raise NativeError('JavaScript requires the installed render-js extra')
        library = ctypes.CDLL(spec.origin)
        declarations = {
            'JS_GetVersion': (_TEXT, []),
            'JS_NewRuntime': (_PTR, []),
            'JS_NewRuntime2': (_PTR, [ctypes.POINTER(_Allocators), _PTR]),
            'JS_FreeRuntime': (None, [_PTR]),
            'JS_NewContext': (_PTR, [_PTR]),
            'JS_FreeContext': (None, [_PTR]),
            'JS_SetMemoryLimit': (None, [_PTR, _SIZE]),
            'JS_SetMaxStackSize': (None, [_PTR, _SIZE]),
            'JS_SetCanBlock': (None, [_PTR, _BOOL]),
            'JS_SetInterruptHandler': (None, [_PTR, _INTERRUPT, _PTR]),
            'JS_SetHostPromiseRejectionTracker': (None, [_PTR, _REJECTION, _PTR]),
            'JS_SetPromiseHook': (None, [_PTR, _PROMISE, _PTR]),
            'JS_SetModuleLoaderFunc2': (None, [_PTR, _NORMALIZE, _LOAD, _ATTRIBUTES, _PTR]),
            'JS_Eval': (_Value, [_PTR, _TEXT, _SIZE, _TEXT, _INT]),
            'JS_EvalFunction': (_Value, [_PTR, _Value]),
            'JS_GetException': (_Value, [_PTR]),
            'JS_GetPrototype': (_Value, [_PTR, _Value]),
            'JS_IsProxy': (_BOOL, [_Value]),
            'JS_GetOwnProperty': (_INT, [_PTR, ctypes.POINTER(_PropertyDescriptor), _Value, ctypes.c_uint32]),
            'JS_ValueToAtom': (ctypes.c_uint32, [_PTR, _Value]),
            'JS_IsStrictEqual': (_BOOL, [_PTR, _Value, _Value]),
            'JS_Throw': (_Value, [_PTR, _Value]),
            'JS_FreeValue': (None, [_PTR, _Value]),
            'JS_DupValue': (_Value, [_PTR, _Value]),
            'JS_NewStringLen': (_Value, [_PTR, _TEXT, _SIZE]),
            'JS_ToCStringLen2': (_PTR, [_PTR, ctypes.POINTER(_SIZE), _Value, _BOOL]),
            'JS_FreeCString': (None, [_PTR, _PTR]),
            'JS_GetImportMeta': (_Value, [_PTR, _PTR]),
            'JS_SetPropertyStr': (_INT, [_PTR, _Value, _TEXT, _Value]),
            'JS_GetPropertyStr': (_Value, [_PTR, _Value, _TEXT]),
            'JS_GetGlobalObject': (_Value, [_PTR]),
            'JS_IsFunction': (_BOOL, [_PTR, _Value]),
            'JS_GetOwnPropertyNames': (_INT, [_PTR, ctypes.POINTER(_PTR), ctypes.POINTER(ctypes.c_uint32), _Value, _INT]),
            'JS_FreePropertyEnum': (None, [_PTR, _PTR, ctypes.c_uint32]),
            'JS_NewAtom': (ctypes.c_uint32, [_PTR, _TEXT]),
            'JS_FreeAtom': (None, [_PTR, ctypes.c_uint32]),
            'JS_DeleteProperty': (_INT, [_PTR, _Value, ctypes.c_uint32, _INT]),
            'JS_Call': (_Value, [_PTR, _Value, _Value, _INT, ctypes.POINTER(_Value)]),
            'JS_ExecutePendingJob': (_INT, [_PTR, ctypes.POINTER(_PTR)]),
            'JS_IsJobPending': (_BOOL, [_PTR]),
            'JS_PromiseState': (_INT, [_PTR, _Value]),
            'js_malloc': (_PTR, [_PTR, _SIZE]),
        }
        for name, (result, arguments) in declarations.items():
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments
        if library.JS_GetVersion() != b'0.17.0':
            raise NativeError('JavaScript embedded engine ABI version differs')
        return library
    except (importlib.metadata.PackageNotFoundError, OSError, AttributeError) as error:
        raise NativeError('JavaScript native ABI unavailable; install the render-js extra') from error


class Context:
    """Own native memory, trusted handles and a bounded host-fed module graph."""

    def __init__(self, fetch_module, *, guarded_stack=False, loading_deadline=None):
        if guarded_stack and threading.current_thread() is threading.main_thread():
            raise NativeError('guarded native runtime requires its verified worker thread')
        self.lib = preflight()
        self.thread = threading.get_ident()
        self.allocations = {}
        self.allocated_bytes = 0
        self.heap_exhausted = False
        self.allocator = ctypes.CDLL(None)
        for name, result, arguments in [('malloc', _PTR, [_SIZE]), ('calloc', _PTR, [_SIZE, _SIZE]), ('realloc', _PTR, [_PTR, _SIZE]), ('free', None, [_PTR])]:
            function = getattr(self.allocator, name)
            function.restype, function.argtypes = result, arguments
        self.allocators = _Allocators(_CALLOC(self._calloc), _MALLOC(self._malloc), _FREE(self._free), _REALLOC(self._realloc), _USABLE(self._usable))
        self.runtime = self.lib.JS_NewRuntime2(ctypes.byref(self.allocators), None)
        self.context = None
        self.handles = {}
        self.root_modules = []
        self.promises = {}
        self.unhandled = {}
        self.fetch_module = fetch_module
        self.job_count = 0
        self.deadline = 0
        self.loading_deadline = loading_deadline
        self.recovery_deadline = None
        self._active_deadline = None
        self.interrupted = False
        self.host_error = None
        self.snapshot_refused = False
        self.module_bases = {}
        if not self.runtime:
            raise NativeError('native runtime allocation failed', resource=True)
        self.lib.JS_SetMemoryLimit(self.runtime, ctypes.c_size_t(-1).value)
        self.lib.JS_SetMaxStackSize(self.runtime, 0 if guarded_stack else 1024 * 1024)
        self.lib.JS_SetCanBlock(self.runtime, False)
        self.context = self.lib.JS_NewContext(self.runtime)
        if not self.context:
            self.close()
            raise NativeError('native context allocation failed', resource=True)
        self.callbacks = (_INTERRUPT(self._interrupt), _REJECTION(self._rejection), _NORMALIZE(self._normalize), _LOAD(self._load), _ATTRIBUTES(self._attributes), _PROMISE(self._promise))
        self.lib.JS_SetInterruptHandler(self.runtime, self.callbacks[0], None)
        self.lib.JS_SetHostPromiseRejectionTracker(self.runtime, self.callbacks[1], None)
        self.lib.JS_SetPromiseHook(self.runtime, self.callbacks[5], None)
        self.lib.JS_SetModuleLoaderFunc2(self.runtime, self.callbacks[2], self.callbacks[3], self.callbacks[4], None)
        self.deadline = time.monotonic() + 0.250
        prototype = b'InternalError.prototype'
        self.handles['_nativeInternalError'] = self.lib.JS_Eval(self.context, prototype, len(prototype), b'bootstrap', 0)

    def _allocate(self, size, pointer=None, clear=False):
        """Enforce requested native bytes and remember every allocation denial."""
        previous = self.allocations.get(pointer, 0)
        if self.allocated_bytes - previous + size > 64 * 1024 * 1024:
            self.heap_exhausted = True
            return None
        if pointer:
            allocated = self.allocator.realloc(pointer, size)
        elif clear:
            allocated = self.allocator.calloc(1, size)
        else:
            allocated = self.allocator.malloc(size)
        if not allocated:
            self.heap_exhausted = True
            return None
        self.allocations.pop(pointer, None)
        self.allocations[allocated] = size
        self.allocated_bytes += size - previous
        return allocated

    def _calloc(self, opaque, count, size):
        return self._allocate(count * size, clear=True)

    def _malloc(self, opaque, size):
        return self._allocate(size)

    def _free(self, opaque, pointer):
        self.allocated_bytes -= self.allocations.pop(pointer, 0)
        self.allocator.free(pointer)

    def _realloc(self, opaque, pointer, size):
        if size == 0:
            self._free(opaque, pointer)
            return None
        return self._allocate(size, pointer)

    def _usable(self, pointer):
        return self.allocations.get(pointer, 0)

    def _attributes(self, context, opaque, attributes):
        """Refuse import attributes rather than treating them as JavaScript."""
        if attributes.tag in (2, 3):
            return 0
        table, length = _PTR(), ctypes.c_uint32()
        status = self.lib.JS_GetOwnPropertyNames(context, ctypes.byref(table), ctypes.byref(length), attributes, 3)
        if status < 0:
            return -1
        self.lib.JS_FreePropertyEnum(context, table, length.value)
        if length.value:
            self._throw('module import attributes are unsupported')
            return -1
        return 0

    def _interrupt(self, runtime, opaque):
        """Interrupt an invocation or its active loading/recovery phase budget."""
        now = time.monotonic()
        self.interrupted = now >= self.deadline or self._active_deadline is not None and now >= self._active_deadline
        return int(self.interrupted)

    def _rejection(self, context, promise, reason, handled, opaque):
        identity = promise.u.pointer
        if handled:
            retained = self.unhandled.pop(identity, None)
            if retained is not None:
                self.lib.JS_FreeValue(context, retained)
        else:
            retained = self.promises.pop(identity, None)
            if identity not in self.unhandled:
                self.unhandled[identity] = retained if retained is not None else self.lib.JS_DupValue(context, promise)

    def _promise(self, context, event, promise, parent, opaque):
        identity = promise.u.pointer
        if event == 0:
            self.promises[identity] = self.lib.JS_DupValue(context, promise)
        elif event == 3 and self.lib.JS_PromiseState(context, promise) != 0:
            retained = self.promises.pop(identity, None)
            if retained is not None:
                self.lib.JS_FreeValue(context, retained)

    def _throw(self, message):
        value = self._string(message)
        self.lib.JS_Throw(self.context, value)

    def _normalize(self, context, base, name, opaque):
        """Resolve requested URL identities against final resource bases."""
        try:
            specifier = name.decode('utf-8')
            if not specifier.startswith(('./', '../', '/', 'http://', 'https://')):
                raise ValueError('bare module specifiers are unsupported')
            root = base.decode('utf-8')
            url = urljoin(self.module_bases.get(root, root), specifier)
            if urlsplit(url).scheme not in ('http', 'https'):
                raise ValueError('module URL requires HTTP(S)')
            encoded = url.encode('utf-8')
            if len(encoded) > 8192:
                raise ValueError('module URL exceeds its limit')
            pointer = self.lib.js_malloc(context, len(encoded) + 1)
            if pointer:
                ctypes.memmove(pointer, encoded + b'\0', len(encoded) + 1)
            return pointer
        except (ValueError, UnicodeError) as error:
            self._throw(str(error))
            return None

    def _load(self, context, name, opaque, attributes):
        """Compile host-supplied sources while excluding host wait from CPU time."""
        try:
            url = name.decode('utf-8')
            started = time.monotonic()
            try:
                source = self.fetch_module(url)
            finally:
                self.deadline += time.monotonic() - started
            final_url = url
            if isinstance(source, tuple):
                source, final_url = source
            if not isinstance(source, str):
                raise ValueError('module source must be UTF-8 text')
            self.module_bases[url] = final_url
            encoded = source.encode('utf-8')
            if len(encoded) > 8 * 1024 * 1024:
                raise NativeError('module source bytes exceeded', resource=True)
            value = self.lib.JS_Eval(context, encoded, len(encoded), name, 1 | 32)
            if value.tag == 6:
                return None
            try:
                self._meta(value.u.pointer, final_url)
                return value.u.pointer
            finally:
                self.lib.JS_FreeValue(context, value)
        except Exception as error:
            from ..htmlurl import URLPolicyError, URLResourceError, URLTimeoutError
            if (isinstance(error, (URLPolicyError, URLResourceError, URLTimeoutError))
                    or isinstance(error, NativeError) and (error.resource or error.timing)):
                if (self.host_error is None or isinstance(self.host_error, URLTimeoutError)
                        or isinstance(self.host_error, NativeError) and self.host_error.timing):
                    self.host_error = error
            self._throw('module acquisition failed')
            return None

    def _meta(self, module, name):
        value = self.lib.JS_GetImportMeta(self.context, module)
        try:
            self._check(value)
            self.lib.JS_SetPropertyStr(self.context, value, b'url', self._string(name))
        finally:
            self.lib.JS_FreeValue(self.context, value)

    def _check_limits(self):
        """Keep sticky heap and authoritative host failures ahead of timing."""
        if self.heap_exhausted:
            raise NativeError('native heap limit exceeded', resource=True)
        if self.host_error is not None:
            error, self.host_error = self.host_error, None
            raise error
        if self.snapshot_refused:
            raise NativeError('native snapshot inspection refused', resource=True)

    def _begin(self, *, recovery=False):
        """Enforce trusted cutoffs before any page effect and retain hard precedence."""
        if not self.context:
            raise NativeError('native context is closed')
        if threading.get_ident() != self.thread:
            raise NativeError('native context requires its owning thread')
        self._check_limits()
        now = time.monotonic()
        self._active_deadline = self.recovery_deadline if recovery else self.loading_deadline
        if self._active_deadline is not None and now >= self._active_deadline:
            self.interrupted = True
            raise NativeError('execution deadline exceeded', timing=True)
        self.deadline = now + 0.250
        self.interrupted = False

    def _string(self, value):
        encoded = value.encode('utf-8')
        return self.lib.JS_NewStringLen(self.context, encoded, len(encoded))

    def _text(self, value):
        size = _SIZE()
        pointer = self.lib.JS_ToCStringLen2(self.context, ctypes.byref(size), value, False)
        if not pointer:
            exception = self.lib.JS_GetException(self.context)
            self.lib.JS_FreeValue(self.context, exception)
            self._check_limits()
            raise NativeError('native string conversion failed', timing=self.interrupted)
        try:
            if size.value > 8 * 1024 * 1024:
                raise NativeError('native string bytes exceeded', resource=True)
            return ctypes.string_at(pointer, size.value).decode('utf-8')
        finally:
            self.lib.JS_FreeCString(self.context, pointer)

    def _check(self, value, *, raw=False):
        """Classify native failures without inspecting page-controlled messages."""
        exception = self.lib.JS_GetException(self.context) if value.tag == 6 else None
        try:
            self._check_limits()
            if self.interrupted:
                raise NativeError('execution limit exceeded', timing=True)
            if exception is None:
                return
            if raw:
                raise NativeError('native raw inspection failed', resource=True)
            if exception.tag == -1:
                prototype = self.lib.JS_GetPrototype(self.context, exception)
                try:
                    internal = self.lib.JS_IsStrictEqual(self.context, prototype, self.handles['_nativeInternalError'])
                finally:
                    self.lib.JS_FreeValue(self.context, prototype)
                if internal:
                    raise NativeError('native InternalError refused', resource=True)
            message = self._text(exception)
        finally:
            if exception is not None:
                self.lib.JS_FreeValue(self.context, exception)
        raise NativeError(message[:512])

    def eval(self, source, name, module=False, *, meta_url=None):
        """Evaluate unchanged source; retain module promises for pending status."""
        self._begin()
        encoded = source.encode('utf-8')
        value = self.lib.JS_Eval(self.context, encoded, len(encoded), name.encode('utf-8'), 33 if module else 0)
        try:
            self._check(value)
        except Exception:
            self.lib.JS_FreeValue(self.context, value)
            raise
        if module:
            self.module_bases[name] = meta_url or name
            try:
                self._meta(value.u.pointer, meta_url or name)
            except Exception:
                self.lib.JS_FreeValue(self.context, value)
                raise
            value = self.lib.JS_EvalFunction(self.context, value)
            try:
                self._check(value)
            except Exception:
                self.lib.JS_FreeValue(self.context, value)
                raise
            self.root_modules.append(self.lib.JS_DupValue(self.context, value))
        self.lib.JS_FreeValue(self.context, value)

    def save(self, names):
        """Capture trusted native function handles and remove their globals."""
        self._begin()
        global_object = self.lib.JS_GetGlobalObject(self.context)
        try:
            for name in names:
                encoded = name.encode('utf-8')
                value = self.lib.JS_GetPropertyStr(self.context, global_object, encoded)
                self._check(value)
                if not self.lib.JS_IsFunction(self.context, value):
                    self.lib.JS_FreeValue(self.context, value)
                    raise NativeError('trusted function is unavailable: ' + name)
                self.handles[name] = value
                atom = self.lib.JS_NewAtom(self.context, encoded)
                try:
                    if self.lib.JS_DeleteProperty(self.context, global_object, atom, 0) != 1:
                        raise NativeError('trusted function global cannot be removed')
                finally:
                    self.lib.JS_FreeAtom(self.context, atom)
        finally:
            self.lib.JS_FreeValue(self.context, global_object)

    def _invoke(self, name, arguments, *, raw=False):
        """Return an owned call result while releasing only marshalled temporaries."""
        self._begin(recovery=self.recovery_deadline is not None)
        values, owned = [], []
        try:
            for argument in arguments:
                value = argument if isinstance(argument, _Value) else self._string(argument) if isinstance(argument, str) else _Value(_Union(integer=argument), 0)
                values.append(value)
                if not isinstance(argument, _Value):
                    owned.append(value)
                self._check(value, raw=raw)
            argv = (_Value * len(values))(*values)
            value = self.lib.JS_Call(self.context, self.handles[name], _UNDEFINED, len(values), argv)
            try:
                self._check(value, raw=raw)
                return value
            except Exception:
                self.lib.JS_FreeValue(self.context, value)
                raise
        finally:
            for value in owned:
                self.lib.JS_FreeValue(self.context, value)

    def call(self, name, *arguments):
        """Invoke a captured handle without consulting page-controlled globals."""
        value = self._invoke(name, arguments)
        try:
            return self._text(value)
        finally:
            self.lib.JS_FreeValue(self.context, value)

    def call_raw(self, name, *arguments):
        """Return an owned raw value without page-controlled string conversion."""
        return self._invoke(name, arguments, raw=True)

    def is_proxy(self, value):
        """Inspect the public native Proxy brand without invoking any trap."""
        self._begin(recovery=self.recovery_deadline is not None)
        return bool(self.lib.JS_IsProxy(value))

    def _refuse_snapshot(self):
        """Latch a source-guard refusal before a later timing stop can mask it."""
        self.snapshot_refused = True
        raise NativeError('native snapshot inspection refused', resource=True)

    def own_descriptor(self, value, key):
        """Return an owned descriptor or None, rejecting Proxy and key coercion."""
        self._begin(recovery=self.recovery_deadline is not None)
        if value.tag != -1 or self.lib.JS_IsProxy(value):
            self._refuse_snapshot()
        owned_key = isinstance(key, str)
        key_value = self._string(key) if owned_key else key
        atom = 0
        try:
            if not isinstance(key_value, _Value) or key_value.tag not in (-8, -7, -6):
                self._refuse_snapshot()
            atom = self.lib.JS_ValueToAtom(self.context, key_value)
            if not atom:
                self._check(_Value(_Union(integer=0), 6), raw=True)
            descriptor = _PropertyDescriptor(0, _UNDEFINED, _UNDEFINED, _UNDEFINED)
            status = self.lib.JS_GetOwnProperty(self.context, ctypes.byref(descriptor), value, atom)
            if status < 0:
                self._check(_Value(_Union(integer=0), 6), raw=True)
            return descriptor if status else None
        finally:
            if atom:
                self.lib.JS_FreeAtom(self.context, atom)
            if owned_key:
                self.lib.JS_FreeValue(self.context, key_value)

    def free_descriptor(self, descriptor):
        """Release all owned descriptor fields and clear their handles."""
        for name in ('value', 'getter', 'setter'):
            self.lib.JS_FreeValue(self.context, getattr(descriptor, name))
            setattr(descriptor, name, _UNDEFINED)

    def _retain_descriptor(self, descriptor, records):
        """Transfer owned fields to a compact record or release them on failure."""
        try:
            records.append(bytes(descriptor))
        except Exception:
            self.free_descriptor(descriptor)
            raise

    def snapshot(self):
        """Validate the source-used flat graph without traps, then serialize once."""
        import resource
        values, descriptors = [], []
        cleanup = _PropertyDescriptor(0, _UNDEFINED, _UNDEFINED, _UNDEFINED)
        cleanup_address = ctypes.addressof(cleanup)
        cache, prototypes, cells, methods = {}, {}, {}, {}
        keys = ('NEXT', 'END', 'START', 'VALUE', 'MIME', 'primitive', 'PREV', 'PRIVATE',
                'CLASS_LIST', 'DATASET', 'STYLE', 'SHEET', 'CHANGED', 'UPGRADE', 'replaceSymbol',
                'nodeType', 'ownerDocument', 'localName', 'name', 'publicId', 'systemId',
                'toString', 'valueOf', 'childNodes', 'firstChild', 'nextSibling', 'cloneNode',
                'textContent', 'data', 'attributes', 'ownerSVGElement', 'createElement',
                'createElementNS', 'ignoreCase', 'voidElements', 'ownerElement', 'length',
                'push', 'join', 'test', 'exec', 'flags', 'source', 'lastIndex', 'call',
                'prototype', 'set', 'Map', 'global', 'multiline', 'dotAll', 'unicode',
                'unicodeSets', 'sticky', 'hasIndices')

        def raw(name, *arguments):
            """Retain the owned return until serialization and guard cleanup finish."""
            value = self.call_raw('__small' + name, *arguments)
            values.append(value)
            return value

        def identity(value):
            """Key a retained value by tag and native identity."""
            return value.tag, value.u.pointer

        def safe(value):
            """Require a non-Proxy object before native reflection."""
            if value.tag != -1 or self.is_proxy(value):
                self._refuse_snapshot()

        def own(value, key):
            """Retain owned fields while temporary descriptor wrappers may expire."""
            descriptor = self.own_descriptor(value, key)
            if descriptor is not None:
                self._retain_descriptor(descriptor, descriptors)
            return descriptor

        def prototype(value):
            """Cache a Proxy-checked owned prototype reference."""
            safe(value)
            token = identity(value)
            if token not in prototypes:
                result = self.lib.JS_GetPrototype(self.context, value)
                try:
                    self._check(result, raw=True)
                except Exception:
                    self.lib.JS_FreeValue(self.context, result)
                    raise
                values.append(result)
                prototypes[token] = result
            return prototypes[token]

        def resolve(value, key):
            """Resolve descriptors through checked prototype hops without getters."""
            path = []
            first = True
            while value.tag == -1:
                safe(value)
                token = identity(value), key if isinstance(key, str) else identity(key)
                if token in cache:
                    descriptor = cache[token]
                    break
                if not first:
                    path.append(token)
                descriptor = own(value, key)
                if descriptor is not None:
                    break
                value = prototype(value)
                first = False
            else:
                if value.tag != 2:
                    self._refuse_snapshot()
                descriptor = None
            for token in path:
                cache[token] = descriptor
            return descriptor

        def data(value, key, *, inherited=False, optional=False):
            """Read a data field without invoking its accessor."""
            if inherited:
                descriptor = resolve(value, key)
            elif isinstance(key, str) and (key == 'length' or key.isdecimal()):
                # These keys address only the immutable private capture tables.
                token = identity(value), key
                if token not in cells:
                    cells[token] = own(value, key)
                descriptor = cells[token]
            else:
                descriptor = own(value, key)
            if descriptor is None and optional:
                return None
            if descriptor is None or descriptor.flags & 16:
                self._refuse_snapshot()
            return descriptor.value

        def number(value):
            """Read primitive numeric tags without coercion."""
            if value.tag == 0:
                return value.u.integer
            if value.tag == 8:
                return value.u.number
            self._refuse_snapshot()

        def same(left, right):
            """Compare native values without conversion."""
            return bool(self.lib.JS_IsStrictEqual(self.context, left, right))

        def array(value):
            """Read captured private table rows through data descriptors."""
            length = data(value, 'length')
            if length.tag != 0 or length.u.integer < 0:
                self._refuse_snapshot()
            return [data(value, str(index)) for index in range(length.u.integer)]

        def match(descriptor, record):
            """Compare captured flags and native handles without coercion."""
            flags = number(data(record, '2'))
            if flags == -1:
                return descriptor is None
            return (descriptor is not None and descriptor.flags & 23 == flags
                    and all(same(getattr(descriptor, field), data(record, str(index)))
                            for index, field in ((3, 'value'), (4, 'getter'), (5, 'setter'))))

        def verify(record, target=None):
            """Refuse a changed source dependency before executing it."""
            owner, key = data(record, '0'), data(record, '1')
            descriptor = resolve(owner if target is None else target, key)
            if not match(descriptor, record):
                self._refuse_snapshot()
            return descriptor

        def assignable(target, names):
            """Reject inherited setters or readonly destinations on fresh instances."""
            for name in names:
                descriptor = resolve(target, key_map.get(name, name))
                if descriptor is not None and (descriptor.flags & 16 or not descriptor.flags & 2):
                    self._refuse_snapshot()

        def selected(name):
            """Select a captured native constructor without consulting page registries."""
            owned = isinstance(name, str)
            candidate = self._string(name) if owned else name
            try:
                self._check(candidate, raw=True)
                for row in constructor_rows:
                    if same(data(row, '0'), candidate):
                        return row
                self._refuse_snapshot()
            finally:
                if owned:
                    self.lib.JS_FreeValue(self.context, candidate)

        def constructor(row, fields):
            """Validate constructor edges and source-used fresh assignments."""
            token = identity(row)
            if token not in checked_constructors:
                for entry in array(data(row, '2')):
                    owner = data(entry, '0')
                    if not same(prototype(owner), data(entry, '1')):
                        self._refuse_snapshot()
                    descriptor = verify(data(entry, '2'))
                    if descriptor is not None and descriptor.value.tag == -1:
                        if not same(prototype(descriptor.value), data(entry, '3')):
                            self._refuse_snapshot()
                checked_constructors.add(token)
            assignable(data(row, '1'), fields)

        def string(value):
            """Require a native string tag without converting the value."""
            if value.tag not in (-7, -6):
                self._refuse_snapshot()

        try:
            key_map = {name: raw('RawKey', index) for index, name in enumerate(keys)}
            groups = [raw('RawDependency', index) for index in range(14)]
            serializer_records, dom_records = array(groups[0]), array(groups[1])
            stable_records, regexp_records = array(groups[2]), array(groups[3])
            clone_records, constructor_rows = array(groups[4]), array(groups[5])
            mime, node_list, regexps = groups[6], groups[7], groups[8:11]
            checked_constructors = set()
            for record in stable_records:
                verify(record)
            for record in regexp_records:
                for regexp in regexps:
                    verify(record, regexp)
            for regexp in regexps:
                number(data(regexp, key_map['lastIndex']))
            for record in array(groups[13]):
                verify(record)
            function_call = stable_records[1]
            verify(function_call, data(stable_records[0], '3'))
            verify(function_call, data(serializer_records[8], '3'))
            constructor(selected('@nodelist'), ())
            root = raw('RawRoot')
            safe(root)
            if number(data(root, key_map['nodeType'])) != 9:
                self._refuse_snapshot()
            stack, seen, children = [], set(), {}
            current = raw('RawCurrent')
            clone_fields = ('ownerDocument', 'localName', 'nodeType', 'parentNode',
                            'NEXT', 'PREV', 'PRIVATE', 'END', 'CLASS_LIST', 'DATASET', 'STYLE')
            attr_fields = ('ownerDocument', 'localName', 'nodeType', 'parentNode',
                           'NEXT', 'PREV', 'ownerElement', 'name', 'VALUE', 'CHANGED')
            cloning = False
            while current.tag == -1:
                safe(current)
                token = identity(current)
                if token in seen:
                    self._refuse_snapshot()
                seen.add(token)
                if len(seen) % 128 == 0:
                    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    if rss * (1 if platform.system() == 'Darwin' else 1024) > 256 * 1048576:
                        self._refuse_snapshot()
                kind = number(data(current, key_map['nodeType']))
                next_value = data(current, key_map['NEXT'])
                if next_value.tag not in (-1, 2):
                    self._refuse_snapshot()
                if kind == -1:
                    start = data(current, key_map['START'])
                    if not stack or not same(start, stack[-1][0]) or not same(current, stack[-1][1]):
                        self._refuse_snapshot()
                    stack.pop()
                    if not stack:
                        if next_value.tag != 2:
                            self._refuse_snapshot()
                        break
                else:
                    if kind not in (1, 2, 3, 4, 8, 9, 10, 11) or kind == 9 and not same(current, root):
                        self._refuse_snapshot()
                    if stack and kind != 2:
                        children[identity(stack[-1][0])] = children.get(identity(stack[-1][0]), 0) + 1
                    if kind in (1, 9, 11):
                        end = data(current, key_map['END'])
                        safe(end)
                        stack.append((current, end))
                    for name in ('childNodes', 'firstChild', 'nextSibling', 'cloneNode',
                                 'textContent', 'attributes', 'valueOf', 'primitive'):
                        if own(current, key_map[name]) is not None:
                            self._refuse_snapshot()
                    if not same(current, root) and own(current, key_map['toString']) is not None:
                        self._refuse_snapshot()
                    conversion = resolve(current, key_map['valueOf'])
                    if not match(conversion, groups[12]) or resolve(current, key_map['primitive']) is not None:
                        self._refuse_snapshot()
                    method = 11
                    if not same(current, root):
                        serializer = resolve(current, key_map['toString'])
                        token = id(serializer)
                        if token not in methods:
                            # Retain the descriptor so its Python identity cannot be reused.
                            methods[token] = (serializer, next((index for index, record in
                                              enumerate(serializer_records) if match(serializer, record)), None))
                        method = methods[token][1]
                        if method is None:
                            self._refuse_snapshot()
                    if kind == 1 or method in (6, 8, 9, 10):
                        string(data(current, key_map['localName']))
                    if kind in (2, 3, 4, 8) or method in (1, 2, 3, 4):
                        string(data(current, key_map['VALUE']))
                    if kind == 2 or method == 1:
                        string(data(current, key_map['name']))
                    if kind == 10 or method == 7:
                        name = data(current, key_map['name'])
                        if name.tag not in (2, 3):
                            string(name)
                        for name in ('publicId', 'systemId'):
                            string(data(current, key_map[name]))
                    if method in (1, 8, 9, 10):
                        document = data(current, key_map['ownerDocument'])
                        safe(document)
                        if not same(data(document, key_map['MIME']), mime):
                            self._refuse_snapshot()
                        resolve(current, key_map['ownerSVGElement'])
                    if method in (6, 11):
                        if method == 6:
                            end = data(current, key_map['END'])
                            safe(end)
                            if (not stack or not same(current, stack[-1][0])
                                    or not same(end, stack[-1][1])):
                                self._refuse_snapshot()
                        verify(dom_records[0], current)
                        verify(dom_records[1], current)
                    if method == 10:
                        cloning = True
                        verify(dom_records[2], current)
                        content = resolve(current, key_map['textContent'])
                        if not match(content, dom_records[3]) and not match(content, dom_records[9]):
                            self._refuse_snapshot()
                        if match(content, dom_records[9]):
                            constructor(selected('style'), clone_fields + ('SHEET',))
                        verify(dom_records[7], document)
                        verify(dom_records[8], document)
                        upgrade = data(document, key_map['UPGRADE'])
                        if upgrade.tag != 2:
                            self._refuse_snapshot()
                        svg = data(current, key_map['ownerSVGElement'], inherited=True, optional=True)
                        if svg is not None:
                            if svg.tag not in (-1, 2):
                                self._refuse_snapshot()
                            if svg.tag == -1:
                                safe(svg)
                            constructor(selected('@svg'), clone_fields + ('ownerSVGElement',))
                        else:
                            name = data(current, key_map['localName'])
                            if not any(same(name, data(selected(tag), '0'))
                                       for tag in ('script', 'style', 'title', 'textarea')):
                                self._refuse_snapshot()
                            row = selected(name)
                            constructor(row, clone_fields + ('SHEET',))
                    if kind in (3, 4):
                        verify(dom_records[4], current)
                        verify(dom_records[5], current)
                    if cloning and kind == 2:
                        verify(dom_records[6], current)
                raw('RawAdvance', next_value)
                current = raw('RawCurrent')
            else:
                self._refuse_snapshot()
            if stack or not seen:
                self._refuse_snapshot()
            if cloning:
                verify(clone_records[0], groups[11])
                verify(clone_records[1])
                constructor(selected('@attr'), attr_fields)
            for count in children.values():
                assignable(node_list, tuple(str(index) for index in range(count)))
            return self.call('__smallSnapshot')
        finally:
            methods.clear()
            while descriptors:
                record = descriptors.pop()
                ctypes.memmove(cleanup_address, record, 56)
                self.free_descriptor(cleanup)
            for value in reversed(values):
                self.lib.JS_FreeValue(self.context, value)

    def jobs(self):
        """Drain jobs within a cumulative 2,000-job lifetime allowance."""
        self._begin()
        count = 0
        while self.lib.JS_IsJobPending(self.runtime):
            if self.job_count >= 2000:
                raise NativeError('native job count exceeded', resource=True)
            context = _PTR()
            state = self.lib.JS_ExecutePendingJob(self.runtime, ctypes.byref(context))
            self._check(_Value(_Union(integer=0), 6 if state < 0 else 3))
            self.job_count += 1
            count += 1
        return count

    @property
    def rejections(self):
        """Count retained promises whose rejections still have no handler."""
        return len(self.unhandled)

    @property
    def pending(self):
        """Return the conservative unresolved script and module promise count."""
        return self.pending_promises

    @property
    def pending_promises(self):
        """Observe actual promise states, including imported and adopted work."""
        for identity, value in list(self.promises.items()):
            if self.lib.JS_PromiseState(self.context, value) != 0:
                self.promises.pop(identity)
                self.lib.JS_FreeValue(self.context, value)
        return len(self.promises)

    @property
    def pending_modules(self):
        """Count pending root module evaluation promises, excluding import graphs."""
        return sum(self.lib.JS_PromiseState(self.context, value) == 0 for value in self.root_modules)

    def close(self):
        """Release owned native values before their context and runtime."""
        if self.context:
            for value in [*self.handles.values(), *self.promises.values(), *self.unhandled.values(), *self.root_modules]:
                self.lib.JS_FreeValue(self.context, value)
            self.handles.clear()
            self.promises.clear()
            self.unhandled.clear()
            self.root_modules.clear()
            self.lib.JS_FreeContext(self.context)
            self.context = None
        if self.runtime:
            self.lib.JS_FreeRuntime(self.runtime)
            self.runtime = None

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.close()
