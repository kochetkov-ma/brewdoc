"""Own a bounded QuickJS runtime through its pinned public 64-bit ABI."""

import ctypes
import importlib.metadata
import importlib.util
import platform
import threading
import time
from urllib.parse import urljoin, urlsplit


class NativeError(RuntimeError):
    """Distinguish hard native resource refusal from script exceptions."""

    def __init__(self, message, *, resource=False):
        self.resource = resource
        super().__init__('resource: ' + message if resource else message)


class _Union(ctypes.Union):
    _fields_ = [('integer', ctypes.c_int32), ('number', ctypes.c_double), ('pointer', ctypes.c_void_p)]


class _Value(ctypes.Structure):
    _fields_ = [('u', _Union), ('tag', ctypes.c_int64)]


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
    if ctypes.sizeof(_PTR) != 8 or ctypes.sizeof(_Value) != 16:
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

    def __init__(self, fetch_module, *, guarded_stack=False):
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
        self.interrupted = False
        self.host_error = None
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
        self.interrupted = time.monotonic() >= self.deadline
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
            if getattr(error, 'resource', False) or getattr(error, 'containment', False):
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

    def _begin(self):
        if not self.context:
            raise NativeError('native context is closed')
        if threading.get_ident() != self.thread:
            raise NativeError('native context requires its owning thread')
        self.deadline = time.monotonic() + 0.250
        self.interrupted = False

    def _string(self, value):
        encoded = value.encode('utf-8')
        return self.lib.JS_NewStringLen(self.context, encoded, len(encoded))

    def _text(self, value):
        size = _SIZE()
        pointer = self.lib.JS_ToCStringLen2(self.context, ctypes.byref(size), value, False)
        if not pointer:
            raise NativeError('native string allocation failed', resource=True)
        try:
            if size.value > 8 * 1024 * 1024:
                raise NativeError('native string bytes exceeded', resource=True)
            return ctypes.string_at(pointer, size.value).decode('utf-8')
        finally:
            self.lib.JS_FreeCString(self.context, pointer)

    def _check(self, value):
        """Classify native failures without inspecting page-controlled messages."""
        if self.heap_exhausted:
            raise NativeError('native heap limit exceeded', resource=True)
        if self.host_error is not None:
            error, self.host_error = self.host_error, None
            raise error
        if value.tag != 6:
            return
        exception = self.lib.JS_GetException(self.context)
        try:
            internal = False
            if exception.tag == -1:
                prototype = self.lib.JS_GetPrototype(self.context, exception)
                try:
                    internal = self.lib.JS_IsStrictEqual(self.context, prototype, self.handles['_nativeInternalError'])
                finally:
                    self.lib.JS_FreeValue(self.context, prototype)
            message = self._text(exception)
        finally:
            self.lib.JS_FreeValue(self.context, exception)
        raise NativeError('execution limit exceeded' if self.interrupted else message[:512], resource=self.interrupted or internal)

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

    def call(self, name, *arguments):
        """Invoke a captured handle without consulting page-controlled globals."""
        self._begin()
        values = [self._string(value) if isinstance(value, str) else _Value(_Union(integer=value), 0) for value in arguments]
        try:
            argv = (_Value * len(values))(*values)
            value = self.lib.JS_Call(self.context, self.handles[name], _UNDEFINED, len(values), argv)
            try:
                self._check(value)
                return self._text(value)
            finally:
                self.lib.JS_FreeValue(self.context, value)
        finally:
            for value in values:
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
