"""Explicit URL acquisition, bounded host GETs and frozen HTML snapshots."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from brewdoc.common import BrewdocError
from brewdoc import htmltext
from brewdoc.service import EXIT_FAIL, _line, run

_BODY_LIMIT = 8 * 1048576
_AGGREGATE_LIMIT = 32 * 1048576
_REQUEST_LIMIT = 100
_STATIC_TIMEOUT = 30.0
_JS_TIMEOUT = 45.0
_OPERATION_TIMEOUT = 5.0
_USER_AGENT = "brewdoc URL acquisition"
_ACCEPT = {"html": "text/html", "script": "text/javascript, application/javascript",
           "module": "text/javascript, application/javascript", "api": "*/*"}
_SCRIPT_TYPES = frozenset({"text/javascript", "application/javascript",
                           "text/ecmascript", "application/ecmascript"})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_V4_DENY = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/3"))
_V6_ALLOW = ipaddress.ip_network("2000::/3")
_V6_DENY = tuple(ipaddress.ip_network(value) for value in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20"))
_DNS_CODE = """import json,os,socket,sys,threading,time
parent=int(sys.argv[3])
def watch():
    while os.getppid()==parent:
        time.sleep(.05)
    os._exit(1)
threading.Thread(target=watch,daemon=True).start()
answers=socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)
print(json.dumps(list(dict.fromkeys(item[4][0] for item in answers))[:129]))
"""


class URLPolicyError(BrewdocError):
    """Refuse a destination or connection that violates acquisition containment."""


class URLResourceError(BrewdocError):
    """Refuse exhausted acquisition budgets without returning partial data."""


def _error(message: str, stage: str, kind: type[BrewdocError] = BrewdocError) -> BrewdocError:
    """Attach a trusted refusal stage without forwarding page-controlled diagnostics."""
    error = kind(message)
    error.acquisition_stage = stage
    return error


def _remaining(deadline: float) -> float:
    """Return the next blocking-operation budget, refusing an expired watchdog."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _error("URL deadline exceeded", "resource", URLResourceError)
    return min(_OPERATION_TIMEOUT, remaining)


def _validate_url(url: str) -> str:
    """Canonicalize one unambiguous HTTP(S) URL without changing its query values."""
    if (not isinstance(url, str) or not url or len(url) > 8192
            or any(ord(char) <= 32 or ord(char) == 127 or char.isspace()
                   or 0xD800 <= ord(char) <= 0xDFFF for char in url)
            or "\\" in url or re.search(r"%(?![0-9A-Fa-f]{2})", url)):
        raise URLPolicyError("URL syntax refused")
    if len(url.encode("utf-8")) > 8192:
        raise URLPolicyError("URL byte limit exceeded")
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
            raise URLPolicyError("URL scheme, host or credentials refused")
        host = parts.hostname
        port = parts.port
        expected = 443 if parts.scheme == "https" else 80
        if (not host or "%" in host or host.endswith("..") or parts.netloc.endswith(":")
                or (port is not None and port != expected)):
            raise URLPolicyError("URL host or port refused")
        if ":" in host:
            host = "[%s]" % ipaddress.IPv6Address(host).compressed
        else:
            host = host.rstrip(".").encode("idna").decode("ascii").lower()
            if not host or len(host) > 253 or any(not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise URLPolicyError("URL host refused")
            if re.fullmatch(r"[0-9.]+", host) or host.startswith("0x"):
                host = str(ipaddress.IPv4Address(host))
        explicit_port = parts.netloc.rsplit(":", 1)[-1]
        if port is not None and explicit_port != str(expected):
            raise URLPolicyError("URL ambiguous port refused")
        return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, parts.fragment))
    except (ValueError, UnicodeError) as exc:
        raise URLPolicyError("URL host or syntax refused") from exc


def _display_url(url: str) -> str:
    """Omit credentials and redact query values and entire fragments for receipts."""
    if (not isinstance(url, str) or len(url) > 8192
            or any(ord(char) <= 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
                   for char in url)):
        return "[invalid URL]"
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or not parts.hostname:
            return "[invalid URL]"
        host = parts.netloc.rsplit("@", 1)[-1]
        query = "&".join(item.partition("=")[0] + "=[redacted]"
                         if "=" in item else "[redacted]" for item in parts.query.split("&"))
        return urlunsplit((parts.scheme, host, parts.path, query if parts.query else "",
                          "[redacted]" if parts.fragment else ""))
    except ValueError:
        return "[invalid URL]"


def _public_address(address: str) -> bool:
    """Use fixed special-use exclusions, independent of Python's patch classifications."""
    try:
        if "%" in address:
            return False
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if parsed.version == 4:
        return not any(parsed in network for network in _V4_DENY)
    return parsed in _V6_ALLOW and not any(parsed in network for network in _V6_DENY)


def _dns_lookup(host: str, port: int, deadline: float) -> tuple[str, ...]:
    """Resolve in a terminable Python child so libc DNS cannot outlive the deadline."""
    try:
        result = subprocess.run([sys.executable, "-I", "-c", _DNS_CODE, host, str(port), str(os.getpid())],
                                capture_output=True, timeout=_remaining(deadline), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _error("URL DNS deadline or worker failure", "dns", URLResourceError) from exc
    if result.returncode != 0 or len(result.stdout) > 16384:
        raise _error("URL DNS resolution failed", "dns")
    try:
        addresses = json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise _error("URL DNS response invalid", "dns") from exc
    if not isinstance(addresses, list) or len(addresses) > 128:
        raise _error("URL DNS address limit exceeded", "dns", URLResourceError)
    return tuple(addresses)


def _resolve(host: str, port: int, deadline: float) -> tuple[str, ...]:
    """Require every DNS candidate to satisfy the same fixed destination policy."""
    try:
        addresses = (str(ipaddress.ip_address(host)),)
    except ValueError:
        addresses = _dns_lookup(host, port, deadline)
    if not addresses or any(not isinstance(value, str) or not _public_address(value)
                            for value in addresses):
        raise _error("URL private or special-use destination refused", "dns", URLPolicyError)
    return tuple(dict.fromkeys(str(ipaddress.ip_address(value)) for value in addresses))


def _interrupt_socket(stream: socket.socket) -> None:
    """Unblock a deadline-expired connect, handshake or response read."""
    try:
        stream.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _connect(url: str, address: str, deadline: float) -> http.client.HTTPConnection:
    """Connect numerically, verify the peer, and retain original TLS hostname checks."""
    parts = urlsplit(url)
    port = 443 if parts.scheme == "https" else 80
    connection = http.client.HTTPConnection(parts.hostname, port, timeout=_remaining(deadline))
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    stream = socket.socket(family, socket.SOCK_STREAM)

    watchdog = threading.Timer(max(0, deadline - time.monotonic()), lambda: _interrupt_socket(stream))
    watchdog.daemon = True
    watchdog.start()
    try:
        stream.settimeout(_remaining(deadline))
        stream.connect((address, port))
        peer = str(ipaddress.ip_address(stream.getpeername()[0]))
        if peer != str(ipaddress.ip_address(address)) or not _public_address(peer):
            raise _error("URL connection peer refused", "connect", URLPolicyError)
        if parts.scheme == "https":
            stream.settimeout(_remaining(deadline))
            stream = ssl.create_default_context().wrap_socket(
                stream, server_hostname=parts.hostname, do_handshake_on_connect=False)
            stream.settimeout(_remaining(deadline))
            stream.do_handshake()
        _remaining(deadline)
        connection.sock = stream
        return connection
    except (OSError, ValueError, BrewdocError):
        stream.close()
        connection.close()
        raise
    finally:
        watchdog.cancel()
        watchdog.join()


def _header(headers: tuple[tuple[str, str], ...], name: str) -> str | None:
    """Reject ambiguous policy headers instead of selecting an arbitrary value."""
    values = [value.strip() for key, value in headers if key.lower() == name]
    if len(values) > 1:
        stage = "media" if name == "content-type" else "redirect" if name == "location" else "bytes"
        raise _error("URL duplicate %s header refused" % name, stage, URLPolicyError)
    return values[0] if values else None


def _read_response(response, deadline: float) -> tuple[bytes, bytes]:
    """Bound transferred and inflated entity bytes while enforcing response framing."""
    headers = tuple(response.getheaders())
    encoding = (_header(headers, "content-encoding") or "identity").lower()
    length = _header(headers, "content-length")
    transfer = _header(headers, "transfer-encoding")
    if transfer and (transfer.lower() != "chunked" or length is not None):
        raise _error("URL response framing refused", "bytes", URLPolicyError)
    if encoding not in ("identity", "gzip"):
        raise _error("URL content encoding refused", "encoding")
    if length is not None and (not re.fullmatch(r"[0-9]+", length) or int(length) > _BODY_LIMIT):
        raise _error("URL entity byte limit or length invalid", "bytes", URLResourceError)
    entity = bytearray()
    body = bytearray()
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    while True:
        _remaining(deadline)
        chunk = response.read(min(65536, _BODY_LIMIT + 1 - len(entity)))
        if not chunk:
            break
        entity.extend(chunk)
        if len(entity) > _BODY_LIMIT:
            raise _error("URL entity byte limit exceeded", "bytes", URLResourceError)
        try:
            decoded = inflater.decompress(chunk, _BODY_LIMIT + 1 - len(body)) if inflater else chunk
        except zlib.error as exc:
            raise _error("URL gzip framing invalid", "encoding") from exc
        body.extend(decoded)
        if len(body) > _BODY_LIMIT or (inflater and inflater.unconsumed_tail):
            raise _error("URL decompressed byte limit exceeded", "bytes", URLResourceError)
    if length is not None and int(length) != len(entity):
        raise _error("URL response length mismatch", "bytes")
    if inflater and (not inflater.eof or inflater.unused_data):
        raise _error("URL gzip framing invalid", "encoding")
    return bytes(entity), bytes(body)


def _request(url: str, deadline: float, accept: str, origin: str | None = None,
             requested_with: bool = False) -> dict:
    """Perform one pinned GET with a socket watchdog and bounded response buffers."""
    parts = urlsplit(url)
    addresses = _resolve(parts.hostname, 443 if parts.scheme == "https" else 80, deadline)
    connection = None
    watchdog = None
    try:
        connection = _connect(url, addresses[0], deadline)
        stream = connection.sock

        watchdog = threading.Timer(max(0, deadline - time.monotonic()), lambda: _interrupt_socket(stream))
        watchdog.daemon = True
        watchdog.start()
        target = urlunsplit(("", "", quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                            quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"), ""))
        headers = {"Host": parts.netloc, "User-Agent": _USER_AGENT, "Accept": accept,
                   "Accept-Encoding": "identity", "Connection": "close"}
        if origin is not None and origin != "%s://%s" % (parts.scheme, parts.netloc):
            headers["Origin"] = origin
        if requested_with and origin == "%s://%s" % (parts.scheme, parts.netloc):
            headers["X-Requested-With"] = "XMLHttpRequest"
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        headers = tuple(response.getheaders())
        entity, body = _read_response(response, deadline)
        _remaining(deadline)
        return {"status": response.status, "headers": headers, "entity": entity, "body": body}
    except (OSError, http.client.HTTPException, zlib.error) as exc:
        _remaining(deadline)
        raise _error("URL transport failed", "tls" if isinstance(exc, ssl.SSLError) else "connect") from exc
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()
        if connection is not None:
            connection.close()


def _validate_media(body: bytes, headers: tuple[tuple[str, str], ...], kind: str) -> None:
    """Validate document and script media without applying HTML policy to API data."""
    if kind == "api":
        return
    content_type = _header(headers, "content-type")
    if content_type is None:
        raise _error("URL content type missing", "media")
    media, *parameters = content_type.split(";")
    allowed = {"text/html"} if kind == "html" else _SCRIPT_TYPES
    if media.strip().lower() not in allowed:
        raise _error("URL %s content type refused" % kind, "media")
    charsets = [value.partition("=")[2].strip().strip('"').lower()
                for value in parameters if value.partition("=")[0].strip().lower() == "charset"]
    if len(charsets) > 1 or any(value not in ("utf-8", "utf8") for value in charsets):
        raise _error("URL charset must declare UTF-8 or UTF8", "encoding")
    if kind == "html":
        try:
            htmltext._decode_html(body)
        except BrewdocError as exc:
            raise _error("URL HTML encoding refused", "encoding") from exc
    else:
        try:
            body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _error("URL script UTF-8 decoding failed", "encoding") from exc


def _fetch(url: str, *, deadline: float | None = None, kind: str = "html",
           budget: dict | None = None, origin: str | None = None,
           requested_with: bool = False) -> dict:
    """Acquire one bounded resource, revalidating every redirect and shared budget."""
    requested = current = _validate_url(url)
    if kind not in _ACCEPT:
        raise _error("URL request kind refused", "url", URLPolicyError)
    if origin is not None:
        normalized_origin = _validate_url(origin)
        origin_parts = urlsplit(normalized_origin)
        if origin_parts.path != "/" or origin_parts.query or origin_parts.fragment:
            raise _error("URL request Origin refused", "url", URLPolicyError)
        origin = "%s://%s" % (origin_parts.scheme, origin_parts.netloc)
    if type(requested_with) is not bool or (requested_with and (kind != "api" or origin is None)):
        raise _error("URL controlled XHR marker refused", "url", URLPolicyError)
    deadline = deadline if deadline is not None else time.monotonic() + _STATIC_TIMEOUT
    budget = budget if budget is not None else {"accepted_requests": 0, "decoded_bytes": 0}
    redirects = []
    for hop in range(6):
        _remaining(deadline)
        if budget["accepted_requests"] >= _REQUEST_LIMIT:
            raise _error("URL request count limit exceeded", "resource", URLResourceError)
        budget["accepted_requests"] += 1
        if requested_with:
            response = _request(current, deadline, _ACCEPT[kind], origin,
                                requested_with=origin == "%s://%s" % (urlsplit(current).scheme, urlsplit(current).netloc))
        else:
            response = (_request(current, deadline, _ACCEPT[kind], origin) if origin is not None
                        else _request(current, deadline, _ACCEPT[kind]))
        if len(response["entity"]) > _BODY_LIMIT or len(response["body"]) > _BODY_LIMIT:
            raise _error("URL response byte limit exceeded", "bytes", URLResourceError)
        budget["decoded_bytes"] += len(response["body"])
        if budget["decoded_bytes"] > _AGGREGATE_LIMIT:
            raise _error("URL aggregate byte limit exceeded", "resource", URLResourceError)
        if response["status"] in _REDIRECTS:
            location = _header(response["headers"], "location")
            if hop == 5 or not location:
                raise _error("URL redirect limit or target refused", "redirect", URLPolicyError)
            target = _validate_url(urljoin(current, location))
            if urlsplit(current).scheme == "https" and urlsplit(target).scheme != "https":
                raise _error("URL HTTPS downgrade refused", "redirect", URLPolicyError)
            redirects.append(current)
            current = target
            continue
        if kind == "html" and not 200 <= response["status"] < 300:
            raise _error("URL HTTP status refused: %d" % response["status"], "status")
        if 200 <= response["status"] < 300:
            _validate_media(response["body"], response["headers"], kind)
        excluded = {"connection", "transfer-encoding", "content-encoding", "content-length",
                    "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade"}
        response["headers"] = tuple((key, value) for key, value in response["headers"]
                                    if key.lower() not in excluded) + (("Content-Length", str(len(response["body"]))),)
        response.update({"requested_url": requested, "final_url": current,
                         "entity_sha256": hashlib.sha256(response["entity"]).hexdigest(),
                         "body_sha256": hashlib.sha256(response["body"]).hexdigest(),
                         "redirects": tuple(redirects)})
        return response
    raise URLPolicyError("URL redirect limit exceeded")


def _snapshot(html: str, final_url: str) -> tuple[bytes, str]:
    """Normalize URL-only declarations and inert relative targets through the HTML parser."""
    parser = htmltext.LexborHTMLParser(html)
    base = final_url
    bases = parser.css("base[href]")
    if bases:
        base = _validate_url(urljoin(final_url, bases[0].attributes["href"]))
        bases[0].attrs["href"] = base
    for node in parser.css("meta[charset]"):
        node.attrs["charset"] = "utf-8"
    for node in parser.css("meta[http-equiv]"):
        if node.attributes["http-equiv"].lower() == "content-type":
            node.attrs["content"] = "text/html; charset=utf-8"
    for selector, attribute in (("a[href]", "href"), ("img[src]", "src")):
        for node in parser.css(selector):
            value = node.attributes[attribute]
            if htmltext._target(value)[0] == "relative":
                node.attrs[attribute] = urljoin(base, value)
    snapshot = parser.html.encode("utf-8")
    if not snapshot or len(snapshot) > _BODY_LIMIT:
        raise URLResourceError("URL snapshot byte limit exceeded")
    return snapshot, base


def _acquisition(render_js: bool) -> dict:
    """Create the fixed URL receipt envelope before any acquisition can fail."""
    return {"mode": "javascript" if render_js else "static", "requested_url": None,
            "final_url": None, "effective_base_url": None, "url_sha256": None,
            "response": {"entity_bytes": None, "entity_sha256": None,
                         "body_bytes": None, "body_sha256": None},
            "snapshot": {"kind": None, "bytes": None, "sha256": None},
            "engine": ({"name": "quickjs-ng", "version": None,
                        "dom_name": "linkedom", "dom_version": None} if render_js else None),
            "capture_status": "refused", "errors": [], "error_count": 0,
            "pending": {"requests": None, "timers": None, "modules": None, "promises": None, "jobs": None},
            "counts": {"requests": 0, "promise_jobs": 0, "timer_callbacks": 0},
            "unsupported_resources": (["stylesheets", "images", "subframes", "media",
                                       "service_workers", "websockets", "web_rtc", "navigation",
                                       "layout", "canvas", "shadow_roots", "browser_state",
                                       "parser_blocking_order", "async_script_order"] if render_js else
                                      ["scripts", "stylesheets", "images", "subframes", "media", "browser_state"]),
            "refusal_stage": None, "soft_window_seconds": 12 if render_js else None}


def _challenge(html: str) -> bool:
    """Recognize narrow provider challenge and explicit login form evidence."""
    parser = htmltext.LexborHTMLParser(html)
    title = " ".join(node.text().lower() for node in parser.css("head title"))
    cloudflare = ("/cdn-cgi/challenge-platform/" in html or "_cf_chl_opt" in html)
    login = bool(parser.css('form input[type="password"]'))
    return ((cloudflare and any(value in title for value in ("just a moment", "attention required")))
            or (login and title.strip() in ("login", "log in", "sign in")))


def _run_url(url: str, out=None, *, render_js: bool = False, sheets=None,
             artifact_outputs=None) -> tuple[int, dict, str]:
    """Acquire a frozen URL snapshot, then reuse local conversion and output transactions."""
    acquisition = _acquisition(render_js)
    requested = url
    name = "url.html"
    stage = "options"
    budget = {"accepted_requests": 0, "decoded_bytes": 0}
    try:
        if sheets is not None or artifact_outputs:
            raise BrewdocError("--sheet and --artifact are workbook-only options")
        stage = "url"
        requested = _validate_url(url)
        if not render_js:
            requested = urlunsplit(urlsplit(requested)._replace(fragment=""))
        url_hash = hashlib.sha256(requested.encode("utf-8")).hexdigest()
        name = "url-%s.html" % url_hash[:16]
        acquisition.update({"requested_url": _display_url(requested), "url_sha256": url_hash})
        stage = "prerequisites"
        if not htmltext._SUPPORTED_RUNTIME or htmltext.LexborHTMLParser is None:
            raise BrewdocError("URL HTML parser unavailable")
        if render_js:
            from brewdoc import htmljs
            htmljs._check_runtime()
        deadline = time.monotonic() + (_JS_TIMEOUT if render_js else _STATIC_TIMEOUT)
        stage = "connect"
        response = _fetch(requested, deadline=deadline, budget=budget)
        final_url = response["final_url"]
        acquisition["final_url"] = _display_url(final_url)
        acquisition["response"] = {"entity_bytes": len(response["entity"]),
                                   "entity_sha256": response["entity_sha256"],
                                   "body_bytes": len(response["body"]),
                                   "body_sha256": response["body_sha256"]}
        source = htmltext._decode_html(response["body"])
        csp = any(key.lower() == "content-security-policy" for key, _ in response["headers"])
        del response
        stage = "challenge"
        if _challenge(source):
            raise BrewdocError("URL challenge or login page refused")
        if render_js:
            stage = "javascript"

            def fetch(request: dict) -> dict:
                """Validate the finite parent request record before the shared guarded GET."""
                if (not isinstance(request, dict) or request.get("kind") not in ("script", "module", "api")
                        or request.get("method", "GET") != "GET" or request.get("body_present")
                        or request.get("headers")):
                    raise URLPolicyError("URL child request policy refused")
                return _fetch(request["url"], deadline=deadline, kind=request["kind"], budget=budget,
                              origin=request.get("origin"), requested_with=request.get("requested_with", False))

            capture = htmljs.render(source, final_url, fetch, deadline)
            source = capture["html"]
            acquisition["capture_status"] = ("settled" if capture.get("capture_status") == "settled"
                                              else "partial")
            acquisition["engine"].update({"version": "0.17.0.1", "dom_version": "0.18.13"})
            errors = capture.get("errors", [])
            acquisition["error_count"] = capture.get("error_count", len(errors))
            acquisition["errors"] = [{"category": error.get("category", "script"),
                                       "resource_url": _display_url(error["resource_url"])
                                       if error.get("resource_url") else None} for error in errors[:16]]
            acquisition["pending"] = capture["pending"]
            acquisition["counts"].update(capture["counts"])
            if csp:
                acquisition["capture_status"] = "partial"
                acquisition["error_count"] += 1
                if len(acquisition["errors"]) < 16:
                    acquisition["errors"].append({"category": "csp_policy_unsupported", "resource_url": None})
            del capture
        else:
            acquisition["capture_status"] = "static"
        stage = "snapshot"
        snapshot, base = _snapshot(source, final_url)
        del source
        acquisition["effective_base_url"] = _display_url(base)
        acquisition["snapshot"] = {"kind": "javascript_dom" if render_js else "static_html",
                                    "bytes": len(snapshot), "sha256": hashlib.sha256(snapshot).hexdigest()}
        acquisition["counts"]["requests"] = budget["accepted_requests"]
        _remaining(deadline)
        stage = "conversion"
        with tempfile.TemporaryDirectory(prefix="brewdoc-url-") as folder:
            path = Path(folder) / name
            path.write_bytes(snapshot)
            del snapshot
            code, receipt, markdown = run(path, out)
        if code != 0:
            stage = "output" if receipt["reason"].startswith("output write failed:") else "conversion"
            receipt["reason"] = "URL snapshot conversion or output refused"
            acquisition["capture_status"] = "refused"
            acquisition["refusal_stage"] = stage
        receipt.update({"source": _display_url(requested), "receipt_schema": "brewdoc.receipt/2",
                        "acquisition": acquisition})
        return code, receipt, markdown
    except (BrewdocError, OSError, ValueError, ImportError) as exc:
        stage = getattr(exc, "acquisition_stage", stage)
        acquisition["capture_status"] = "refused"
        acquisition["refusal_stage"] = stage
        acquisition["counts"]["requests"] = budget["accepted_requests"]
        acquisition["errors"] = [{"category": stage, "resource_url": None}]
        acquisition["error_count"] = 1
        reason = ("--sheet and --artifact are workbook-only options" if stage == "options"
                  else "URL acquisition refused during %s" % stage)
        receipt = _line(htmltext.ROUTE, False, reason, name)
        receipt.update({"source": _display_url(requested), "receipt_schema": "brewdoc.receipt/2",
                        "acquisition": acquisition})
        return EXIT_FAIL, receipt, ""
