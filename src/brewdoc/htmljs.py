"""Capture the finite installed JavaScript subset in an isolated child."""

import importlib.metadata
import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from multiprocessing.connection import Connection
from urllib.parse import urljoin, urlsplit, urlunsplit

from .common import BrewdocError

BYTE_LIMIT = 8 * 1048576
IPC_LIMIT = 18 * 1048576
PROCESS_LIMIT = 256 * 1048576
SOFT_WINDOW = 12
STACK_LIMIT = 1048576
_SIGNAL_PREREQUISITE = False
_CHILD_ONLY = False
OMISSIONS = (
    'whole_document_parsed_before_scripts', 'no_layout_or_intersection_observer',
    'no_cookies_or_credentials', 'no_browser_csp_enforcement',
    'no_shadow_root_or_subframe_export', 'no_canvas_media_or_service_workers',
    'coarse_async_defer_and_module_event_order', 'document_write_buffered',
    'protected_host_intrinsic_prototypes',
    'imported_module_completion_unobservable',
)


def _check_runtime():
    """Refuse absent or unsupported installed native prerequisites before I/O."""
    try:
        if importlib.metadata.version('quickjs-ng') != '0.17.0.1':
            raise BrewdocError('JS installed engine version refused')
        from ._urljs.native import NativeError, preflight
    except (ImportError, importlib.metadata.PackageNotFoundError, OSError) as exc:
        raise BrewdocError('JS runtime unavailable; install brewdoc[render-js]') from exc
    try:
        preflight()
    except NativeError as exc:
        raise BrewdocError('JS installed native prerequisites unsupported') from exc
    try:
        _guarded_call(lambda: None)
    except (AttributeError, OSError, RuntimeError, ValueError) as exc:
        raise BrewdocError('JS guarded stack prerequisite unavailable') from exc
    global _SIGNAL_PREREQUISITE
    if not _SIGNAL_PREREQUISITE:
        command = 'from brewdoc.htmljs import _signal_preflight; _signal_preflight()'
        try:
            probe = subprocess.run([sys.executable, '-I', '-c', command], timeout=5,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BrewdocError('JS native fault handler prerequisite unavailable') from exc
        if probe.returncode != int(signal.SIGUSR1):
            raise BrewdocError('JS native fault handler prerequisite unavailable')
        _SIGNAL_PREREQUISITE = True
    for name in ('_vendor/linkedom-worker.js', '_urljs/glue.js'):
        if not Path(__file__).with_name(name.split('/')[0]).joinpath(name.split('/')[1]).is_file():
            raise BrewdocError('JS installed bundle unavailable')


def _send(channel, record):
    """Bound wire bytes before writing a private JSON message."""
    data = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(data) > IPC_LIMIT:
        raise BrewdocError('JS resource IPC byte limit exceeded')
    channel.send_bytes(data)


def _receive(channel):
    """Bound private IPC before decoding child-controlled bytes."""
    try:
        data = channel.recv_bytes(IPC_LIMIT)
        record = json.loads(data)
    except (OSError, EOFError, ValueError, UnicodeError, RecursionError) as exc:
        raise BrewdocError('JS resource IPC invalid or child failed') from exc
    if not isinstance(record, dict):
        raise BrewdocError('JS resource IPC record invalid')
    return record


def _origin(url):
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    suffix = '' if port == (443 if parsed.scheme == 'https' else 80) else ':' + str(port)
    host = str(parsed.hostname)
    return parsed.scheme + '://' + ('[' + host + ']' if ':' in host else host) + suffix


def _verify_stack():
    """Require the dedicated thread's actual stack and inaccessible guard page."""
    library = ctypes.CDLL(None)
    library.pthread_self.restype = ctypes.c_void_p
    thread = library.pthread_self()
    if sys.platform == 'darwin':
        library.pthread_get_stacksize_np.argtypes = [ctypes.c_void_p]
        library.pthread_get_stacksize_np.restype = ctypes.c_size_t
        library.pthread_get_stackaddr_np.argtypes = [ctypes.c_void_p]
        library.pthread_get_stackaddr_np.restype = ctypes.c_void_p
        size = library.pthread_get_stacksize_np(thread)
        address = ctypes.c_uint64(library.pthread_get_stackaddr_np(thread) - size - 1)
        probe_address = address.value
        region_size = ctypes.c_uint64()
        information = (ctypes.c_int * 32)()
        count = ctypes.c_uint(32)
        object_name = ctypes.c_uint()
        task = ctypes.c_uint.in_dll(library, 'mach_task_self_').value
        library.mach_vm_region.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64), ctypes.c_int, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        status = library.mach_vm_region(task, ctypes.byref(address), ctypes.byref(region_size),
                                        9, information, ctypes.byref(count), ctypes.byref(object_name))
        guard = status == 0 and address.value <= probe_address < address.value + region_size.value and information[0] == 0
    elif sys.platform == 'linux':
        attribute = (ctypes.c_long * 16)()
        library.pthread_getattr_np.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.pthread_attr_getstack.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t)]
        library.pthread_attr_getguardsize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        library.pthread_attr_destroy.argtypes = [ctypes.c_void_p]
        address, stack_size, guard_size = ctypes.c_void_p(), ctypes.c_size_t(), ctypes.c_size_t()
        status = library.pthread_getattr_np(thread, attribute)
        try:
            status |= library.pthread_attr_getstack(attribute, ctypes.byref(address), ctypes.byref(stack_size))
            status |= library.pthread_attr_getguardsize(attribute, ctypes.byref(guard_size))
        finally:
            library.pthread_attr_destroy(attribute)
        size, guard = stack_size.value, status == 0 and guard_size.value > 0
    else:
        raise BrewdocError('JS guarded thread platform unsupported')
    if not STACK_LIMIT - 16384 <= size <= STACK_LIMIT or not guard:
        raise BrewdocError('JS effective guarded stack prerequisite unavailable')


def _fault_setup(probe=False):
    """Install child-only native exit handlers on the dedicated alternate stack."""
    if not _CHILD_ONLY:
        raise BrewdocError('JS fault handlers require the isolated child')
    library = ctypes.CDLL(None)
    if sys.platform == 'darwin':
        action_fields = [('handler', ctypes.c_void_p), ('mask', ctypes.c_uint32), ('flags', ctypes.c_int)]
        stack_fields = [('pointer', ctypes.c_void_p), ('size', ctypes.c_size_t), ('flags', ctypes.c_int)]
        on_stack, disabled, action_size = 1, 4, 16
    elif sys.platform == 'linux' and hasattr(library, 'gnu_get_libc_version'):
        action_fields = [('handler', ctypes.c_void_p), ('mask', ctypes.c_ulong * 16),
                         ('flags', ctypes.c_int), ('restorer', ctypes.c_void_p)]
        stack_fields = [('pointer', ctypes.c_void_p), ('flags', ctypes.c_int), ('size', ctypes.c_size_t)]
        on_stack, disabled, action_size = 0x08000000, 2, 152
    else:
        raise BrewdocError('JS fault handler libc ABI unsupported')
    class Action(ctypes.Structure):
        """Public 64-bit libc signal disposition layout."""
        _fields_ = action_fields
    class Stack(ctypes.Structure):
        """Public per-thread alternate signal stack layout."""
        _fields_ = stack_fields
    if ctypes.sizeof(Action) != action_size or ctypes.sizeof(Stack) != 24:
        raise BrewdocError('JS fault handler public ABI layout refused')
    library.sigaction.argtypes = [ctypes.c_int, ctypes.POINTER(Action), ctypes.POINTER(Action)]
    library.sigaction.restype = ctypes.c_int
    library.sigaltstack.argtypes = [ctypes.POINTER(Stack), ctypes.POINTER(Stack)]
    library.sigaltstack.restype = ctypes.c_int
    library.sigfillset.argtypes = [ctypes.c_void_p]
    library.sigfillset.restype = ctypes.c_int
    memory = ctypes.create_string_buffer(131072)
    stack, previous_stack = Stack(), Stack()
    stack.pointer, stack.size, stack.flags = ctypes.addressof(memory), len(memory), 0
    if library.sigaltstack(ctypes.byref(stack), ctypes.byref(previous_stack)) != 0:
        raise BrewdocError('JS alternate signal stack unavailable')
    saved = []
    state = (library, memory, previous_stack, saved)
    try:
        observed = Stack()
        if library.sigaltstack(None, ctypes.byref(observed)) != 0 or observed.pointer != stack.pointer or observed.size != len(memory) or observed.flags & disabled:
            raise BrewdocError('JS alternate signal stack readback refused')
        exit_pointer = ctypes.cast(library._exit, ctypes.c_void_p).value
        signals = [signal.SIGSEGV, signal.SIGBUS, signal.SIGABRT, signal.SIGILL, signal.SIGFPE]
        if probe:
            signals.append(signal.SIGUSR1)
        for number in signals:
            action, previous, readback = Action(), Action(), Action()
            action.handler, action.flags = exit_pointer, on_stack
            if library.sigfillset(ctypes.byref(action, Action.mask.offset)) != 0:
                raise BrewdocError('JS native signal mask unavailable')
            if library.sigaction(number, ctypes.byref(action), ctypes.byref(previous)) != 0:
                raise BrewdocError('JS native fault handler unavailable')
            saved.append((number, previous))
            if library.sigaction(number, None, ctypes.byref(readback)) != 0 or readback.handler != exit_pointer or not readback.flags & on_stack:
                raise BrewdocError('JS native fault handler readback refused')
        return state
    except Exception:
        _fault_cleanup(state)
        raise


def _fault_cleanup(state):
    """Restore child dispositions before releasing the alternate stack memory."""
    library, memory, previous_stack, saved = state
    status = 0
    for number, previous in reversed(saved):
        status |= library.sigaction(number, ctypes.byref(previous), None)
    if sys.platform == 'darwin' and previous_stack.flags & 4:
        previous_stack.pointer, previous_stack.size = ctypes.addressof(memory), len(memory)
    status |= library.sigaltstack(ctypes.byref(previous_stack), None)
    if status:
        raise BrewdocError('JS native fault handler cleanup refused')


def _signal_preflight():
    """Prove native handler setup with a harmless signal in a fresh child only."""
    global _CHILD_ONLY
    _CHILD_ONLY = True
    def harmless_signal():
        """Deliver only SIGUSR1 to the verified native execution thread."""
        library = ctypes.CDLL(None)
        library.pthread_self.restype = ctypes.c_void_p
        library.pthread_kill.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.pthread_kill.restype = ctypes.c_int
        if library.pthread_kill(library.pthread_self(), signal.SIGUSR1) != 0:
            raise BrewdocError('JS harmless signal prerequisite unavailable')
        raise BrewdocError('JS native signal exit did not run')
    try:
        _guarded_call(harmless_signal, protect=True, probe=True)
    except Exception:
        os._exit(1)


def _guarded_call(function, *, protect=False, probe=False):
    """Run native work entirely on one verified bounded operating-system stack."""
    result, failure = [], []
    def invoke():
        """Check the effective thread stack before creating native state."""
        try:
            _verify_stack()
            state = _fault_setup(probe) if protect else None
            try:
                result.append(function())
            finally:
                if state:
                    _fault_cleanup(state)
        except Exception as exc:
            failure.append(exc)
    previous = threading.stack_size(STACK_LIMIT - 16384 if sys.platform == 'darwin' else STACK_LIMIT)
    try:
        thread = threading.Thread(target=invoke)
        thread.start()
    finally:
        threading.stack_size(previous)
    thread.join()
    if failure:
        raise failure[0]
    return result[0]


def _host_response(request, fetch, document_url, base_url):
    """Validate finite GET options and enforce document-origin CORS."""
    from .htmlurl import URLPolicyError, URLResourceError, _header, _validate_url
    url = _validate_url(urljoin(base_url, str(request.get('url', ''))))
    kind = request.get('kind', 'api')
    if kind not in ('script', 'module', 'api'):
        raise URLPolicyError('JS unknown host request kind refused')
    if request.get('method', 'GET') != 'GET' or request.get('body_present'):
        return {'error': 'request_options_unsupported'}
    if request.get('credentials', 'omit') not in ('same-origin', 'omit'):
        return {'error': 'request_options_unsupported'}
    if request.get('mode', 'cors') not in ('cors', 'same-origin') or request.get('redirect', 'follow') != 'follow':
        return {'error': 'request_options_unsupported'}
    headers = request.get('headers', {})
    if not isinstance(headers, dict) or any(not isinstance(key, str) for key in headers):
        return {'error': 'request_headers_unsupported'}
    cross = _origin(url) != _origin(document_url)
    normalized = {key.lower(): value for key, value in headers.items()}
    if (len(normalized) != len(headers) or set(normalized) - {'accept', 'x-requested-with'}
            or ('accept' in normalized and normalized['accept'] not in ('*/*', 'application/json, text/plain, */*'))
            or ('x-requested-with' in normalized and (normalized['x-requested-with'] != 'XMLHttpRequest' or cross or kind != 'api'))
            or (headers and kind != 'api')):
        return {'error': 'request_headers_unsupported'}
    if cross and request.get('mode') == 'same-origin':
        return {'error': 'cors_denied'}
    response = fetch({'url': url, 'kind': kind,
                      'origin': _origin(document_url) if kind != 'script' else None,
                      'requested_with': 'x-requested-with' in normalized})
    final_url = response.get('final_url', url)
    cross = _origin(final_url) != _origin(document_url)
    if cross and request.get('mode') == 'same-origin':
        return {'error': 'cors_denied'}
    original_headers = response.get('headers', ())
    response_headers = dict(original_headers)
    response_headers = {key.lower(): value for key, value in response_headers.items()}
    if cross and kind != 'script':
        try:
            allow_origin = _header(original_headers, 'access-control-allow-origin')
        except URLPolicyError:
            return {'error': 'cors_denied'}
        if allow_origin not in ('*', _origin(document_url)):
            return {'error': 'cors_denied'}
    body = response.get('body', b'')
    if isinstance(body, bytes):
        try:
            body = body.decode('utf-8', 'strict')
        except UnicodeError:
            return {'error': 'resource_encoding_unsupported'}
    if not isinstance(body, str) or len(body.encode('utf-8')) > BYTE_LIMIT:
        raise URLResourceError('JS resource response byte limit exceeded')
    exposed = {'cache-control', 'content-language', 'content-length', 'content-type',
               'expires', 'last-modified', 'pragma'}
    exposed.update(item.strip().lower() for item in response_headers.get('access-control-expose-headers', '').split(','))
    response_headers = {key: value for key, value in response_headers.items()
                        if key not in ('set-cookie', 'set-cookie2') and (not cross or key in exposed)}
    return {'body': body, 'status': response.get('status', 200), 'headers': response_headers,
            'url': final_url, 'redirected': final_url != url}


def render(source, final_url, fetch, deadline, *, soft_window=SOFT_WINDOW):
    """Acquire child-requested resources and return a bounded frozen DOM."""
    _check_runtime()
    if len(source.encode('utf-8')) > BYTE_LIMIT:
        raise BrewdocError('JS resource main source byte limit exceeded')
    parent_socket, child_socket = socket.socketpair()
    parent = Connection(parent_socket.detach())
    descriptor = child_socket.fileno()
    command = 'from brewdoc.htmljs import _worker_fd; _worker_fd(%d,%d)' % (descriptor, os.getpid())
    try:
        process = subprocess.Popen([sys.executable, '-I', '-c', command], pass_fds=(descriptor,),
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        parent.close()
        raise BrewdocError('JS child start failed') from exc
    finally:
        child_socket.close()
    def terminate():
        """Kill only the still-owned isolated child process group."""
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    watchdog = threading.Timer(max(0, deadline - time.monotonic()), terminate)
    watchdog.start()
    try:
        _send(parent, {'source': source, 'url': final_url, 'soft_window': soft_window})
        base_url = final_url
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrewdocError('JS resource hard deadline exceeded')
            if not parent.poll(min(remaining, 0.05)):
                if process.poll() is not None:
                    raise BrewdocError('JS child crash refused')
                continue
            message = _receive(parent)
            if time.monotonic() >= deadline:
                raise BrewdocError('JS resource hard deadline exceeded')
            if message.get('kind') == 'request':
                request = message.get('request')
                if not isinstance(request, dict):
                    raise BrewdocError('JS resource request invalid')
                try:
                    response = _host_response(request, fetch, final_url, base_url)
                except BrewdocError as exc:
                    from .htmlurl import URLPolicyError, URLResourceError
                    if isinstance(exc, (URLPolicyError, URLResourceError)):
                        raise
                    response = {'error': 'ancillary_transport_failed'}
                _send(parent, response)
            elif message.get('kind') == 'base':
                from .htmlurl import _validate_url
                value = message.get('value')
                if not isinstance(value, str) or len(value.encode('utf-8')) > 8192:
                    raise BrewdocError('JS resource base IPC invalid')
                base_url = _validate_url(urljoin(final_url, value))
                _send(parent, {'base': base_url})
            elif message.get('kind') == 'result':
                result = message.get('result')
                if not isinstance(result, dict) or not isinstance(result.get('html'), str):
                    raise BrewdocError('JS resource capture invalid')
                if not result['html'].strip() or len(result['html'].encode('utf-8')) > BYTE_LIMIT:
                    raise BrewdocError('JS resource snapshot invalid or byte limit exceeded')
                _validate_capture(result, soft_window)
                return result
            elif message.get('kind') == 'error':
                raise BrewdocError('JS resource execution refused')
            else:
                raise BrewdocError('JS resource IPC kind invalid')
    finally:
        watchdog.cancel()
        watchdog.join()
        parent.close()
        try:
            process.wait(0.1)
        except subprocess.TimeoutExpired:
            terminate()
            process.wait(1)


def _validate_capture(result, soft_window):
    """Refuse malformed child metadata before the receipt consumes it."""
    required = {'html', 'capture_status', 'errors', 'error_count', 'pending', 'counts',
                'engine', 'dom_bundle', 'soft_window_seconds', 'unsupported'}
    if set(result) != required:
        raise BrewdocError('JS resource capture metadata invalid')
    valid = type(result['capture_status']) is str and result['capture_status'] in ('settled', 'partial')
    valid = valid and result['engine'] == 'QuickJS-ng 0.17.0' and result['dom_bundle'] == 'LinkeDOM 0.18.13'
    valid = valid and result['soft_window_seconds'] == soft_window and result['unsupported'] == list(OMISSIONS)
    categories = {'request_options_unsupported', 'request_headers_unsupported', 'cors_denied',
                  'resource_encoding_unsupported', 'ancillary_transport_failed', 'script_resource_failed',
                  'script_execution_failed', 'javascript_callback_failed', 'unhandled_promise_rejection',
                  'csp_policy_unsupported', 'inline_style_only'}
    errors = result['errors']
    valid = valid and isinstance(errors, list) and len(errors) <= 20
    valid = valid and all(isinstance(error, dict) and set(error) == {'category', 'resource_url'}
                          and type(error['category']) is str and error['category'] in categories
                          and error['resource_url'] is None for error in errors)
    valid = valid and type(result['error_count']) is int and len(errors) <= result['error_count'] <= 2000000
    pending = result['pending']
    valid = valid and isinstance(pending, dict) and set(pending) == {'requests', 'timers', 'modules', 'promises', 'jobs'}
    valid = valid and all(type(pending[name]) is int and 0 <= pending[name] <= 2000000
                          for name in ('requests', 'timers')) and type(pending['jobs']) is bool
    valid = valid and (pending['modules'] is None or type(pending['modules']) is int and 0 <= pending['modules'] <= 2000000)
    valid = valid and type(pending['promises']) is int and 0 <= pending['promises'] <= 2000000
    counts = result['counts']
    valid = valid and isinstance(counts, dict) and set(counts) == {'promise_jobs', 'timer_callbacks'}
    valid = valid and type(counts['promise_jobs']) is int and 0 <= counts['promise_jobs'] <= 2000
    valid = valid and type(counts['timer_callbacks']) is int and 0 <= counts['timer_callbacks'] <= 200
    valid = valid and (result['capture_status'] != 'settled' or result['error_count'] == 0
                      and not errors and all(value == 0 for value in pending.values()))
    if not valid:
        raise BrewdocError('JS resource capture metadata invalid')


def _worker_fd(descriptor, parent_pid):
    """Open the inherited private descriptor without starting helper processes."""
    global _CHILD_ONLY
    _CHILD_ONLY = True
    _worker(Connection(descriptor), parent_pid)


def _worker(channel, parent_pid):
    """Own one native runtime and exit promptly when its parent disappears."""
    def parent_watchdog():
        """Exit even when native module IPC is blocked after parent death."""
        while os.getppid() == parent_pid:
            time.sleep(0.05)
        os._exit(1)
    threading.Thread(target=parent_watchdog, daemon=True).start()
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if sys.platform == 'linux':
        resource.setrlimit(resource.RLIMIT_AS, (PROCESS_LIMIT, PROCESS_LIMIT))
    try:
        record = _receive(channel)
        result = _guarded_call(lambda: _capture(record['source'], record['url'], channel, float(record['soft_window'])), protect=True)
        _send(channel, {'kind': 'result', 'result': result})
    except Exception:
        try:
            _send(channel, {'kind': 'error'})
        except (OSError, BrewdocError):
            pass
    finally:
        channel.close()


def _capture(source, url, channel, soft_window):
    """Pump proved scripts, native modules, host GETs, jobs and finite timers."""
    from ._urljs.native import Context, NativeError
    import resource
    started = time.monotonic()
    errors = []
    unknown_modules = False
    counts = {'promise_jobs': 0, 'timer_callbacks': 0}
    def request(record):
        """Ask the authoritative parent for one bounded resource."""
        _send(channel, {'kind': 'request', 'request': record})
        response = _receive(channel)
        if response.get('error'):
            errors.append({'category': response['error'], 'resource_url': None})
        return response
    def module_source(name):
        """Retain redirected module identity and unknown graph completion."""
        nonlocal unknown_modules
        unknown_modules = True
        response = request({'url': name, 'kind': 'module'})
        if response.get('error') or not 200 <= response['status'] < 300:
            raise BrewdocError('JS module resource failed')
        return response['body'], response['url']
    native = Context(module_source, guarded_stack=True)
    try:
        worker = Path(__file__).with_name('_vendor').joinpath('linkedom-worker.js').read_text()
        bundle, marker, suffix = worker.rpartition('\nexport {')
        if not marker or not suffix.endswith('};\n'):
            raise BrewdocError('JS installed DOM bundle shape refused')
        glue = Path(__file__).with_name('_urljs').joinpath('glue.js').read_text()
        native.eval('(()=>{' + bundle + '\n' + glue + '\n})();', 'installed-bootstrap')
        names = ('Boot', 'Requests', 'Complete', 'Timers', 'RunTimer', 'Clock', 'Errors', 'Ready',
                 'Loaded', 'Snapshot', 'StartScript', 'EndScript', 'TrackModule', 'Modules',
                 'Scripts', 'ScriptDone', 'Base', 'SetBase', 'Pending', 'Policy', 'Violation')
        native.save(['__small' + name for name in names])
        def call(name, *args):
            """Use trusted native handles and enforce sticky host limits."""
            value = native.call('__small' + name, *args)
            if name != 'Violation' and int(native.call('__smallViolation')):
                raise BrewdocError('JS resource scheduler or write limit exceeded')
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if rss * (1 if sys.platform == 'darwin' else 1024) > PROCESS_LIMIT:
                raise BrewdocError('JS resource process memory limit exceeded')
            return value
        parsed = urlsplit(url)
        location = {'href': url, 'protocol': parsed.scheme + ':', 'hostname': parsed.hostname,
                    'host': parsed.netloc, 'pathname': parsed.path, 'search': '?' + parsed.query if parsed.query else '',
                    'hash': '#' + parsed.fragment if parsed.fragment else '', 'origin': _origin(url)}
        call('Boot', source, url, json.dumps(location))
        if int(call('Policy')):
            errors.append({'category': 'csp_policy_unsupported', 'resource_url': None})
        base = call('Base')
        if base:
            _send(channel, {'kind': 'base', 'value': base})
            call('SetBase', _receive(channel)['base'])
        def drain():
            """Drain native jobs under the cumulative per-capture cap."""
            counts['promise_jobs'] += native.jobs()
        def scripts():
            """Execute each discovered accepted script exactly once."""
            found = json.loads(call('Scripts'))
            for script in sorted(found, key=lambda item: bool(item['defer']) or item['type'] == 'module'):
                if script['type'] not in ('', 'text/javascript', 'application/javascript', 'module'):
                    continue
                text = script['text']
                name = urlunsplit(parsed._replace(fragment='')) + '#inline-' + str(script['index'])
                success = False
                if script['src']:
                    response = request({'url': script['src'], 'kind': 'module' if script['type'] == 'module' else 'script'})
                    if response.get('error') or not 200 <= response['status'] < 300:
                        errors.append({'category': 'script_resource_failed', 'resource_url': None})
                        call('ScriptDone', script['index'], 0)
                        continue
                    text, name = response['body'], response['url']
                try:
                    call('StartScript', script['index'])
                    native.eval(text, name, module=script['type'] == 'module',
                                meta_url=url if not script['src'] else name)
                    drain()
                    success = True
                except NativeError as exc:
                    if exc.resource:
                        raise BrewdocError('JS resource native limit exceeded') from exc
                    errors.append({'category': 'script_execution_failed', 'resource_url': None})
                finally:
                    call('EndScript')
                call('ScriptDone', script['index'], int(success))
            return len(found)
        scripts()
        call('Ready')
        drain()
        call('Loaded')
        drain()
        while True:
            now = (time.monotonic() - started) * 1000
            call('Clock', int(now))
            queue = json.loads(call('Requests'))
            for queued in queue:
                response = request({**queued, 'kind': 'api'})
                call('Complete', json.dumps({**response, 'id': queued['id']}))
            drain()
            added = scripts()
            timers = json.loads(call('Timers'))
            for timer in sorted(timers, key=lambda item: (item['due'], item['id'])):
                if timer['due'] > now:
                    continue
                counts['timer_callbacks'] += 1
                if counts['timer_callbacks'] > 200:
                    raise BrewdocError('JS resource timer callback limit exceeded')
                call('RunTimer', timer['id'], int(now))
                drain()
            native_pending = native.pending_modules
            pending = {'requests': int(call('Pending')), 'timers': len(json.loads(call('Timers'))),
                       'modules': native_pending if native_pending else None if unknown_modules else 0,
                       'promises': native.pending_promises, 'jobs': False}
            if not any(pending.values()) and not queue and not added:
                break
            if time.monotonic() - started >= soft_window:
                break
            if not queue and not added:
                time.sleep(0.005)
        observed = json.loads(call('Errors'))
        errors.extend({'category': 'inline_style_only' if item == {'category': 'inline_style_only'}
                       else 'javascript_callback_failed', 'resource_url': None} for item in observed)
        errors.extend({'category': 'unhandled_promise_rejection', 'resource_url': None} for _ in range(native.rejections))
        snapshot = call('Snapshot')
        return {'html': snapshot, 'capture_status': 'partial' if errors or any(pending.values()) or unknown_modules else 'settled',
                'errors': errors[:20], 'error_count': len(errors), 'pending': pending, 'counts': counts,
                'engine': 'QuickJS-ng 0.17.0', 'dom_bundle': 'LinkeDOM 0.18.13',
                'soft_window_seconds': soft_window, 'unsupported': list(OMISSIONS)}
    finally:
        native.close()
