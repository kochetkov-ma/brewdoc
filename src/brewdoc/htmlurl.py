"""Explicit URL acquisition, bounded host requests and frozen HTML snapshots."""

from __future__ import annotations

import codecs
import email.message
import functools
import hashlib
import http.client
import ipaddress
import itertools
import json
import math
import os
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, quote, urljoin, urlsplit, urlunsplit

from brewdoc.common import BrewdocError
from brewdoc._standalone import child_command
from brewdoc import htmltext
from brewdoc.service import (EXIT_FAIL, HTML_ONLY, LINKS_HTML_ONLY, ROUTES, WORKBOOK_ONLY, _html_receipt_route,
                             _line, run)

_BODY_LIMIT = 8 * 1048576
_AGGREGATE_LIMIT = 32 * 1048576
_REQUEST_LIMIT = 100
_DEFAULT_TIMEOUT = 30.0
_SEARCH_KEY_SHA256 = "3f95312b20bf7c1216841a364f20f791c979bf845e00ccb7ba516d45d616ad47"
_SEARCH_CONTENT_TYPE = "application/x-www-form-urlencoded"
_MAX_TIMEOUT = min(threading.TIMEOUT_MAX, (2**31 - 1) / 1000)
_TIMEOUT_RANGE_REASON = "URL timeout exceeds supported clock range (maximum %s seconds)" % _MAX_TIMEOUT
_SOFT_WINDOW = 12.0
_USER_AGENT = "brewdoc URL acquisition"
_ACCEPT = {"html": "text/html,application/xhtml+xml,*/*;q=0.8", "script": "text/javascript, application/javascript",
           "module": "text/javascript, application/javascript", "api": "*/*"}
# WHATWG MIME Sniffing JavaScript MIME type essences.
_SCRIPT_TYPES = frozenset({"application/ecmascript", "application/javascript", "application/x-ecmascript",
                           "application/x-javascript", "text/ecmascript", "text/javascript", "text/javascript1.0",
                           "text/javascript1.1", "text/javascript1.2", "text/javascript1.3", "text/javascript1.4",
                           "text/javascript1.5", "text/jscript", "text/livescript", "text/x-ecmascript",
                           "text/x-javascript"})
# Weak challenge markers and shell evidence count only below this visible text length.
_PAGE_TEXT_LIMIT = 1000
_WEAK_CHALLENGES = {"ddos-guard": ("/.well-known/ddos-guard/",), "hcaptcha": ("hcaptcha.com",),
                    "recaptcha": ("/recaptcha/",), "cloudflare": ("challenges.cloudflare.com/turnstile",),
                    "generic": ("checking your browser", "проверка браузера", "проверяем браузер",
                                "проверка вашего браузера", "please enable cookies", "вы не робот",
                                "are you a robot")}
_CAPTCHA_HOSTS = frozenset({"hcaptcha", "recaptcha", "cloudflare"})  # Script hosts that lose to shell evidence.
# An empty app root is shell evidence only below this visible text length.
_SHELL_TEXT_LIMIT = 200
_NO_JAVASCRIPT = ("enable javascript", "javascript enabled", "включите javascript", "отключен javascript")
_APP_ROOTS = "#root, #app, #__next, #__nuxt"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CONNECT_ATTEMPTS = 3
_CONNECT_SLICE = 3.0
_V4_DENY = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/3"))
_V6_ALLOW = ipaddress.ip_network("2000::/3")
_V6_DENY = tuple(ipaddress.ip_network(value) for value in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20"))
_OFFICE = "application/vnd.openxmlformats-officedocument."
_OPEN = "application/vnd.oasis.opendocument."
_MEDIA = {"text/html": ".html", "application/pdf": ".pdf", _OFFICE + "wordprocessingml.document": ".docx",
          _OFFICE + "presentationml.presentation": ".pptx", _OFFICE + "spreadsheetml.sheet": ".xlsx",
          "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
          "application/vnd.ms-excel.sheet.binary.macroenabled.12": ".xlsb",
          "application/vnd.ms-excel": ".xls", _OPEN + "spreadsheet": ".ods", _OPEN + "text": ".odt",
          _OPEN + "presentation": ".odp", "application/epub+zip": ".epub", "message/rfc822": ".eml",
          "text/plain": ".txt", "text/markdown": ".md", "application/octet-stream": None}
_ZIP = b"PK\x03\x04"
_MAGIC = {".pdf": lambda body: body.startswith(b"%PDF-"),
          ".xls": lambda body: body.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
          **dict.fromkeys((".docx", ".pptx", ".xlsx", ".xlsm", ".xlsb"),
                          lambda body: body.startswith(_ZIP) and b"[Content_Types].xml" in body),
          **dict.fromkeys((".ods", ".odt", ".odp", ".epub"),
                          lambda body: body.startswith(_ZIP) and body[30:38] == b"mimetype")}


class URLPolicyError(BrewdocError):
    """Refuse a destination or connection that violates acquisition containment."""


class URLSyntaxError(URLPolicyError):
    """Refuse a URL that cannot be parsed or fails syntax validation."""


class URLResourceError(BrewdocError):
    """Refuse exhausted acquisition budgets without returning partial data."""


class URLTimeoutError(BrewdocError):
    """Stop acquisition on a trusted loading deadline without accepting half bodies."""


def _timeout_seconds(value: int | float) -> float:
    """Validate a finite positive duration within the supported clock range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrewdocError("URL timeout must be a finite positive number")
    try:
        seconds = float(value)
    except OverflowError as exc:
        reason = _TIMEOUT_RANGE_REASON if value > 0 else "URL timeout must be a finite positive number"
        raise BrewdocError(reason) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise BrewdocError("URL timeout must be a finite positive number")
    if seconds > _MAX_TIMEOUT:
        raise BrewdocError(_TIMEOUT_RANGE_REASON)
    return seconds


def _error(message: str, stage: str, kind: type[BrewdocError] = BrewdocError) -> BrewdocError:
    """Attach a trusted refusal stage without forwarding page-controlled diagnostics."""
    error = kind(message)
    error.acquisition_stage = stage
    return error


def _remaining(deadline: float) -> float:
    """Return the next blocking-operation budget, refusing an expired watchdog."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _error("URL deadline exceeded", "timeout", URLTimeoutError)
    return remaining


def _validate_url(url: str) -> str:
    """Canonicalize one unambiguous HTTP(S) URL without changing its query values."""
    if (not isinstance(url, str) or not url or len(url.encode("utf-8", "surrogatepass")) > 8192
            or any(ord(char) <= 32 or ord(char) == 127 or char.isspace()
                   or 0xD800 <= ord(char) <= 0xDFFF for char in url)
            or "\\" in url or re.search(r"%(?![0-9A-Fa-f]{2})", url)):
        raise URLSyntaxError("URL syntax refused")
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
        raise URLSyntaxError("URL host or syntax refused") from exc


def _location(value: str) -> str:
    """Percent-encode Location whitespace and raw header bytes like a browser before validation."""
    try:
        value = value.encode("latin-1").decode("utf-8")
    except UnicodeEncodeError:
        pass
    except UnicodeDecodeError:
        value = "".join(chr(byte) if byte < 0x80 else "%%%02X" % byte for byte in value.encode("latin-1"))
    value = value.translate(dict.fromkeys(map(ord, "\t\n\r"))).strip("".join(map(chr, range(33))))
    return "".join(quote(char, safe="") if ord(char) <= 32 or ord(char) == 127 or char.isspace() else char
                   for char in value)


def _display_url(url: str) -> str:
    """Omit credentials and redact query values and entire fragments for receipts."""
    if (not isinstance(url, str) or len(url.encode("utf-8", "surrogatepass")) > 8192
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
    """Resolve in a terminable child so libc DNS cannot outlive the deadline."""
    try:
        result = subprocess.run(child_command("dns", host, str(port), str(os.getpid())),
                                capture_output=True, timeout=_remaining(deadline), check=False)
    except subprocess.TimeoutExpired as exc:
        raise _error("URL DNS deadline exceeded", "dns", URLTimeoutError) from exc
    except OSError as exc:
        raise _error("URL DNS worker failed", "dns") from exc
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


def _connect_failed(deadline: float) -> BrewdocError:
    """Return the retryable TCP-phase error unless the overall deadline already expired."""
    _remaining(deadline)
    return _error("URL connect failed", "connect")


def _connect(url: str, address: str, deadline: float,
             connect_deadline: float | None = None) -> http.client.HTTPConnection:
    """Connect numerically, verify the peer, and retain original TLS hostname checks.
    `connect_deadline` bounds only TCP connect; TLS keeps the overall deadline."""
    parts = urlsplit(url)
    port = 443 if parts.scheme == "https" else 80
    connection = http.client.HTTPConnection(parts.hostname, port, timeout=_remaining(deadline))
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    try:
        stream = socket.socket(family, socket.SOCK_STREAM)
    except OSError as exc:
        raise _connect_failed(deadline) from exc

    watchdog = threading.Timer(max(0, deadline - time.monotonic()), lambda: _interrupt_socket(stream))
    watchdog.daemon = True
    watchdog.start()
    try:
        budget = (connect_deadline or deadline) - time.monotonic()
        if budget <= 0:
            raise _connect_failed(deadline)
        stream.settimeout(budget)
        try:
            stream.connect((address, port))
        except OSError as exc:
            raise _connect_failed(deadline) from exc
        peer = str(ipaddress.ip_address(stream.getpeername()[0]))
        if peer != str(ipaddress.ip_address(address)) or not _public_address(peer):
            raise _error("URL connection peer refused", "connect", URLPolicyError)
        if parts.scheme == "https":
            stream.settimeout(_remaining(deadline))
            context = ssl.create_default_context()
            context.set_alpn_protocols(["http/1.1"])  # WordPress.com edge answers 403 to a ClientHello without ALPN
            stream = context.wrap_socket(
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
        stage = ("media" if name in ("content-type", "content-disposition")
                 else "redirect" if name == "location" else "bytes")
        raise _error("URL duplicate %s header refused" % name, stage, URLPolicyError)
    return values[0] if values else None


def _read_response(response, deadline: float, limit: int | None = None) -> tuple[bytes, bytes]:
    """Bound transferred and inflated entity bytes while enforcing response framing."""
    limit = _BODY_LIMIT if limit is None else limit
    headers = tuple(response.getheaders())
    encoding = (_header(headers, "content-encoding") or "identity").lower()
    length = _header(headers, "content-length")
    transfer = _header(headers, "transfer-encoding")
    if transfer and (transfer.lower() != "chunked" or length is not None):
        raise _error("URL response framing refused", "bytes", URLPolicyError)
    if encoding not in ("identity", "gzip"):
        raise _error("URL content encoding refused", "encoding")
    if length is not None and (not re.fullmatch(r"[0-9]+", length) or int(length) > limit):
        raise _error("URL entity byte limit or length invalid", "bytes", URLResourceError)
    entity = bytearray()
    body = bytearray()
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    while True:
        _remaining(deadline)
        chunk = response.read(min(65536, limit + 1 - len(entity)))
        if not chunk:
            break
        entity.extend(chunk)
        if len(entity) > limit:
            raise _error("URL entity byte limit exceeded", "bytes", URLResourceError)
        try:
            decoded = inflater.decompress(chunk, limit + 1 - len(body)) if inflater else chunk
        except zlib.error as exc:
            raise _error("URL gzip framing invalid", "encoding") from exc
        body.extend(decoded)
        if len(body) > limit or (inflater and inflater.unconsumed_tail):
            raise _error("URL decompressed byte limit exceeded", "bytes", URLResourceError)
    if length is not None and int(length) != len(entity):
        raise _error("URL response length mismatch", "bytes")
    if inflater and (not inflater.eof or inflater.unused_data):
        raise _error("URL gzip framing invalid", "encoding")
    return bytes(entity), bytes(body)


def _search_body(url: str, origin: str | None, body: str, content_type: str) -> bytes | None:
    """Validate the public search profile without rewriting its original body."""
    parts = urlsplit(url)
    if (origin != "https://hn.algolia.com" or parts.scheme != "https"
            or parts.hostname != "uj5wyc0l7x-dsn.algolia.net" or parts.port not in (None, 443)
            or parts.path != "/1/indexes/Item_dev/query" or parts.fragment
            or parts.username is not None or parts.password is not None
            or content_type != _SEARCH_CONTENT_TYPE):
        return None
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True, errors="strict")
    except (ValueError, UnicodeError):
        return None
    parameters = dict(pairs)
    if (len(pairs) != 3 or set(parameters) != {
            "x-algolia-agent", "x-algolia-application-id", "x-algolia-api-key"}
            or parameters["x-algolia-application-id"] != "UJ5WYC0L7X"
            or hashlib.sha256(parameters["x-algolia-api-key"].encode("utf-8")).hexdigest() != _SEARCH_KEY_SHA256):
        return None
    agent = parameters["x-algolia-agent"]
    if not agent or len(agent) > 512 or any(not 32 <= ord(character) <= 126 for character in agent):
        return None
    try:
        encoded = body.encode("utf-8", "strict")
    except UnicodeError:
        return None
    if len(encoded) > 16384:
        raise _error("URL search body byte limit exceeded", "resource", URLResourceError)

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        """Reject duplicate JSON keys before schema validation."""
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate search field")
        return value

    try:
        fields = json.loads(body, object_pairs_hook=unique_object)
    except RecursionError as exc:
        raise _error("URL search body depth limit exceeded", "resource", URLResourceError) from exc
    except ValueError:
        return None
    entries = 0
    pending = [(fields, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 4:
            raise _error("URL search body depth limit exceeded", "resource", URLResourceError)
        if isinstance(value, list):
            entries += len(value)
            if entries > 256:
                raise _error("URL search array entry limit exceeded", "resource", URLResourceError)
            pending.extend((child, depth + 1) for child in value)
        elif isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
    integers = ("page", "hitsPerPage", "minWordSizefor1Typo", "minWordSizefor2Typos", "minProximity")
    booleans = ("advancedSyntax", "ignorePlurals", "clickAnalytics", "getRankingInfo")
    arrays = ("analyticsTags", "numericFilters", "tagFilters", "restrictSearchableAttributes")
    if (type(fields) is not dict
            or set(fields) != {*integers, *booleans, *arrays, "query", "queryType", "typoTolerance"}
            or type(fields["query"]) is not str
            or any(type(fields[name]) is not int or fields[name] < 0 for name in integers)
            or fields["hitsPerPage"] == 0
            or any(type(fields[name]) is not bool for name in booleans)
            or fields["queryType"] not in ("prefixLast", "prefixNone")
            or not (type(fields["typoTolerance"]) is bool or fields["typoTolerance"] in ("min", "strict"))):
        return None
    for name in arrays:
        if type(fields[name]) is not list:
            return None
        pending = list(fields[name])
        while pending:
            value = pending.pop()
            if type(value) is str:
                continue
            if type(value) is list and name in ("numericFilters", "tagFilters"):
                pending.extend(value)
            else:
                return None
    return encoded


def _candidates(addresses: tuple[str, ...]) -> list[str]:
    """Alternate address families, starting with the first answer's, up to the attempt cap."""
    ipv6 = ":" in addresses[0]
    same = [value for value in addresses if (":" in value) == ipv6]
    other = [value for value in addresses if (":" in value) != ipv6]
    return [value for pair in itertools.zip_longest(same, other) for value in pair if value][:_CONNECT_ATTEMPTS]


def _request(url: str, deadline: float, accept: str, origin: str | None = None,
             requested_with: bool = False, *, search_body: bytes | None = None) -> dict:
    """Perform one pinned request with bounded response buffers and owned cleanup."""
    parts = urlsplit(url)
    candidates = _candidates(_resolve(parts.hostname, 443 if parts.scheme == "https" else 80, deadline))
    connection = None
    watchdog = None
    stage = "connect"
    try:
        for index, address in enumerate(candidates):
            last = index == len(candidates) - 1
            now = time.monotonic()
            # Only a TCP-phase failure moves on, so request bytes are never sent twice.
            try:
                connection = _connect(url, address, deadline, deadline if last else
                                      now + min((deadline - now) / (len(candidates) - index), _CONNECT_SLICE))
                break
            except BrewdocError as exc:
                if last or type(exc) is not BrewdocError or getattr(exc, "acquisition_stage", None) != "connect":
                    raise
        stream = connection.sock

        watchdog = threading.Timer(max(0, deadline - time.monotonic()), lambda: _interrupt_socket(stream))
        watchdog.daemon = True
        watchdog.start()
        target = urlunsplit(("", "", quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                            quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"), ""))
        headers = {"Host": parts.netloc, "User-Agent": _USER_AGENT, "Accept": accept,
                   "Accept-Encoding": "gzip", "Connection": "close"}
        if origin is not None and origin != "%s://%s" % (parts.scheme, parts.netloc):
            headers["Origin"] = origin
        if requested_with and origin == "%s://%s" % (parts.scheme, parts.netloc):
            headers["X-Requested-With"] = "XMLHttpRequest"
        if search_body is None:
            connection.request("GET", target, headers=headers)
        else:
            headers["Content-Type"] = _SEARCH_CONTENT_TYPE
            connection.request("POST", target, body=search_body, headers=headers)
        response = connection.getresponse()
        stage = "bytes"
        if search_body is not None and 300 <= response.status < 400:
            raise _error("URL search redirect refused", "redirect", URLPolicyError)
        headers = tuple(response.getheaders())
        types = [value.partition(";")[0].strip().lower() for key, value in headers if key.lower() == "content-type"]
        document = accept == _ACCEPT["html"] and len(types) == 1 and _MEDIA.get(types[0], ".html") != ".html"
        entity, body = _read_response(response, deadline, _AGGREGATE_LIMIT if document else None)
        _remaining(deadline)
        return {"status": response.status, "headers": headers, "entity": entity, "body": body}
    except (OSError, http.client.HTTPException, zlib.error) as exc:
        if isinstance(exc, ssl.SSLError):
            raise _error("URL TLS failed", "tls") from exc
        _remaining(deadline)
        raise _error("URL transport failed", stage) from exc
    finally:
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()
        if connection is not None:
            connection.close()


def _media_suffix(body: bytes, headers: tuple[tuple[str, str], ...], url: str) -> tuple[str, str | None]:
    """Return the snapshot suffix confirmed by magic bytes and the text charset label."""
    content_type = _header(headers, "content-type")
    if content_type is None:
        raise _error("URL content type missing", "media")
    media = content_type.partition(";")[0].strip().lower()
    if media not in _MEDIA:
        raise _error("URL content type refused", "media")
    suffix = _MEDIA[media]
    if suffix is None:
        message = email.message.Message()
        message["content-disposition"] = _header(headers, "content-disposition") or ""
        try:
            name = message.get_filename() or urlsplit(url).path.rpartition("/")[2]
        except (TypeError, ValueError) as exc:
            raise _error("URL content disposition refused", "media") from exc
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in _MAGIC:
            raise _error("URL octet-stream document suffix refused", "media")
    if suffix in _MAGIC and not _MAGIC[suffix](body):
        error = _error("URL %s content does not match its media type" % suffix, "media")
        error.document_suffix = suffix
        raise error
    return suffix, _charset(headers) if suffix in (".html", ".txt", ".md") else None


def _validate_media(body: bytes, headers: tuple[tuple[str, str], ...], kind: str, url: str) -> None:
    """Validate document and script media without applying HTML policy to API data."""
    if kind == "api":
        return
    if kind == "html":
        suffix, label = _media_suffix(body, headers, url)
    else:
        content_type = _header(headers, "content-type")
        if content_type is None:
            raise _error("URL content type missing", "media")
        if content_type.partition(";")[0].strip().lower() not in _SCRIPT_TYPES:
            raise _error("URL %s content type refused" % kind, "media")
        suffix, label = None, _charset(headers)
    try:
        if suffix == ".html":
            htmltext._decode_html(body, label)
        elif suffix in (".txt", ".md"):
            htmltext._decode_bytes(body, label)
        elif suffix is None and label is not None and htmltext._codec(label)[0] != "UTF-8":
            raise BrewdocError("URL script charset must resolve to UTF-8")
        elif suffix is None:
            body.decode("utf-8")
    except (BrewdocError, UnicodeDecodeError) as exc:
        raise _error("URL %s encoding refused" % kind, "encoding") from exc


def _charset(headers: tuple[tuple[str, str], ...]) -> str | None:
    """Return the one raw Content-Type charset label, or None; a repeated label refuses."""
    parameters = (_header(headers, "content-type") or "").split(";")[1:]
    labels = [value.partition("=")[2] for value in parameters
              if value.partition("=")[0].strip().lower() == "charset"]
    if len(labels) > 1:
        raise _error("URL charset declared more than once", "encoding")
    return labels[0] if labels else None


def _text_snapshot(body: bytes, label: str | None) -> bytes:
    """Freeze a text body as UTF-8; UTF-8 bodies stay byte-identical, others are transcoded."""
    snapshot = htmltext._decode_bytes(body, label).encode("utf-8")
    if len(snapshot) > _AGGREGATE_LIMIT:
        raise _error("URL text snapshot byte limit exceeded", "resource", URLResourceError)
    return body if body.removeprefix(codecs.BOM_UTF8) == snapshot else snapshot


def _fetch(url: str, *, deadline: float | None = None, kind: str = "html",
           budget: dict | None = None, origin: str | None = None,
           requested_with: bool = False, search_body: str | None = None) -> dict:
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
    outgoing = None
    if search_body is not None:
        if type(search_body) is not str or kind != "api" or requested_with:
            raise _error("URL search request policy refused", "url", URLPolicyError)
        outgoing = _search_body(current, origin, search_body, _SEARCH_CONTENT_TYPE)
        if outgoing is None:
            raise _error("URL search request policy refused", "url", URLPolicyError)
    deadline = deadline if deadline is not None else time.monotonic() + _DEFAULT_TIMEOUT
    budget = budget if budget is not None else {"accepted_requests": 0, "decoded_bytes": 0}
    redirects = []
    response = None
    try:
        for hop in range(6):
            if outgoing is None:
                _remaining(deadline)
            if budget["accepted_requests"] >= _REQUEST_LIMIT:
                raise _error("URL request count limit exceeded", "resource", URLResourceError)
            if outgoing is not None:
                if budget["decoded_bytes"] + len(outgoing) > _AGGREGATE_LIMIT:
                    raise _error("URL aggregate byte limit exceeded", "resource", URLResourceError)
                _remaining(deadline)
                budget["decoded_bytes"] += len(outgoing)
            budget["accepted_requests"] += 1
            if outgoing is not None:
                response = _request(current, deadline, _ACCEPT[kind], origin, search_body=outgoing)
            else:
                response = _request(current, deadline, _ACCEPT[kind], origin,
                                    requested_with and origin == "%s://%s" % urlsplit(current)[:2])
            cap = _AGGREGATE_LIMIT if kind == "html" else _BODY_LIMIT
            if len(response["entity"]) > cap or len(response["body"]) > cap:
                raise _error("URL response byte limit exceeded", "bytes", URLResourceError)
            budget["decoded_bytes"] += len(response["body"])
            if budget["decoded_bytes"] > _AGGREGATE_LIMIT:
                raise _error("URL aggregate byte limit exceeded", "resource", URLResourceError)
            if outgoing is not None and 300 <= response["status"] < 400:
                raise _error("URL search redirect refused", "redirect", URLPolicyError)
            if response["status"] in _REDIRECTS:
                location = _header(response["headers"], "location")
                if not location:
                    raise _error("URL redirect target refused", "redirect", URLPolicyError)
                if hop == 5:
                    raise _error("URL redirect limit exceeded after 5 hops (last status %d)" % response["status"],
                                 "redirect", URLPolicyError)
                try:
                    target = _validate_url(urljoin(current, _location(location)))
                except (BrewdocError, ValueError) as exc:
                    raise _error("URL redirect target refused", "redirect", URLPolicyError) from exc
                if urlsplit(current).scheme == "https" and urlsplit(target).scheme != "https":
                    raise _error("URL HTTPS downgrade refused", "redirect", URLPolicyError)
                redirects.append(current)
                current = target
                continue
            if kind == "html" and not 200 <= response["status"] < 300:
                check = None
                if htmltext._SUPPORTED_RUNTIME and htmltext.LexborHTMLParser is not None:
                    try:
                        source = htmltext._decode_html(response["body"], _charset(response["headers"]))[0]
                        check = _page_check(source, current, response["headers"],
                                            blocked=response["status"])
                    except BrewdocError:
                        pass  # An undecodable error page keeps the plain status refusal.
                if check in (None, "shell"):
                    raise _error("URL HTTP status refused: %d" % response["status"], "status")
                raise _page_refusal(check, response["status"])
            if 200 <= response["status"] < 300:
                _validate_media(response["body"], response["headers"], kind, current)
            excluded = {"connection", "transfer-encoding", "content-encoding", "content-length",
                        "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade"}
            response["headers"] = (tuple((key, value) for key, value in response["headers"]
                                         if key.lower() not in excluded)
                                   + (("Content-Length", str(len(response["body"]))),))
            response.update({"requested_url": requested, "final_url": current,
                             "entity_sha256": hashlib.sha256(response["entity"]).hexdigest(),
                             "body_sha256": hashlib.sha256(response["body"]).hexdigest(),
                             "redirects": tuple(redirects)})
            return response
    except BrewdocError as exc:
        exc.resource_url = current
        exc.http_status = None if response is None else response["status"]
        exc.redirect_count = len(redirects)
        raise


def _snapshot(html: str, final_url: str) -> tuple[bytes, str]:
    """Normalize URL-only declarations and inert relative targets through the HTML parser."""
    parser = htmltext.LexborHTMLParser(html)
    base = final_url
    bases = parser.css("base[href]")
    if bases:
        try:
            base = _validate_url(urljoin(final_url, bases[0].attributes["href"]))
            bases[0].attrs["href"] = base
        except (ValueError, URLSyntaxError):
            pass
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
            "refusal_stage": None, "soft_window_seconds": None,
            "soft_window_origin": None, "timeout_seconds": None, "http_status": None, "redirect_count": 0,
            "challenge": None, "charset": None, "charset_source": None, "render_js": None}


def _page_check(html: str, url: str, headers: tuple[tuple[str, str], ...], *, blocked: int = 0) -> str | None:
    """Name a captcha, bot challenge or login page, or return "shell" for a JavaScript-only page.
    `blocked` is a non-2xx status, where DataDome or QRATOR evidence is definitive."""
    parser = htmltext.LexborHTMLParser(html)
    lowered = functools.cache(html.lower)  # Content pages return before needing it; it costs about half a parse.
    fields = {(key.lower(), value.strip().lower()) for key, value in headers}
    title = " ".join(node.text().lower() for node in parser.css("head title"))
    if ("cf-mitigated", "challenge") in fields or (
            any(value in title for value in ("just a moment", "attention required"))
            and ("/cdn-cgi/challenge-platform/" in lowered() or "_cf_chl_opt" in lowered())):
        return "cloudflare"
    if urlsplit(url).path == "/showcaptcha" or parser.css('form[action*="/checkcaptcha"]'):
        return "yandex-smartcaptcha"
    if blocked and ("captcha-delivery.com" in lowered() or any(key == "x-datadome" for key, _ in fields)):
        return "datadome"
    if blocked == 439 or (blocked and ("server", "qrator") in fields):
        return "qrator"
    if parser.css('form input[type="password"]') and title.strip() in ("login", "log in", "sign in"):
        return "login"
    # Collect shell evidence before stripping: Lexbor keeps noscript content as one text node.
    scripts = parser.css("script")
    empty_root = any(not node.text(strip=True) for node in parser.css(_APP_ROOTS))
    parser.strip_tags(["script", "style", "noscript", "template"])
    visible = len(" ".join((parser.body.text(separator=" ") if parser.body else "").split()))
    if visible >= _PAGE_TEXT_LIMIT:
        return None
    if ("server", "ddos-guard") in fields:
        return "ddos-guard"
    shell = scripts and ((empty_root and visible < _SHELL_TEXT_LIMIT)
                         or any(message in lowered() for message in _NO_JAVASCRIPT))
    return next((name for name, markers in _WEAK_CHALLENGES.items() if not (shell and name in _CAPTCHA_HOSTS)
                 and any(marker in lowered() for marker in markers)), "shell" if shell else None)


def _page_refusal(check: str, status: int, *, rendered: bool = False) -> BrewdocError:
    """Refuse one page-check result with a plain reason at challenge or javascript_required."""
    if check == "shell":
        return _error("URL page content was not produced by the bounded JavaScript engine" if rendered
                      else "URL page needs JavaScript; retry with --render-js auto", "javascript_required")
    error = _error("URL login page refused" if check == "login"
                   else "URL captcha or bot challenge refused: %s (HTTP %d)" % (check, status), "challenge")
    error.challenge = check
    return error


def _run_url(url: str, out=None, *, render_js: bool | str | None = False, sheets=None,
             artifact_outputs=None, timeout: int | float = _DEFAULT_TIMEOUT,
             article: bool = False, body_only: bool = False, links: bool = True) -> tuple[int, dict, str]:
    """Acquire a frozen URL snapshot, then reuse local conversion and output transactions.
    `render_js` "auto" runs JS only on a static shell; True means "always"."""
    known = type(render_js) is bool or render_js in (None, "auto", "always")
    render_js = {True: "always", False: None}.get(render_js, render_js) if known else None
    acquisition = _acquisition(render_js == "always") | {"render_js": render_js}
    requested = url
    suffix = ".html"
    stage = "timeout"
    final_url = None
    budget = {"accepted_requests": 0, "decoded_bytes": 0}
    try:
        acquisition["timeout_seconds"] = _timeout_seconds(timeout)
        javascript = {key: value for key, value in _acquisition(True).items()
                      if key in ("mode", "engine", "unsupported_resources")} | {
            "soft_window_seconds": min(_SOFT_WINDOW, acquisition["timeout_seconds"] / 2),
            "soft_window_origin": "request"}
        if render_js == "always":
            acquisition.update(javascript)
        started = time.monotonic()
        deadline = started + acquisition["timeout_seconds"]
        stage = "options"
        if not known:
            raise BrewdocError('render_js must be None, False, True, "auto" or "always"')
        if sheets is not None or artifact_outputs:
            raise BrewdocError(WORKBOOK_ONLY)
        stage = "url"
        requested = _validate_url(url)
        if not render_js:
            requested = urlunsplit(urlsplit(requested)._replace(fragment=""))
        url_hash = hashlib.sha256(requested.encode("utf-8")).hexdigest()
        acquisition.update({"requested_url": _display_url(requested), "url_sha256": url_hash})
        stage = "prerequisites"
        if render_js:
            from brewdoc import htmljs
            htmljs._check_runtime(deadline=deadline)
        stage = "connect"
        response = _fetch(requested, deadline=deadline, budget=budget)
        final_url = response["final_url"]
        acquisition.update({"final_url": _display_url(final_url), "http_status": response["status"],
                            "redirect_count": len(response["redirects"])})
        acquisition["response"] = {"entity_bytes": len(response["entity"]),
                                   "entity_sha256": response["entity_sha256"],
                                   "body_bytes": len(response["body"]),
                                   "body_sha256": response["body_sha256"]}
        suffix, label = _media_suffix(response["body"], response["headers"], final_url)
        if suffix != ".html":
            if article or not links:
                stage = "options"
                raise BrewdocError(HTML_ONLY if article else LINKS_HTML_ONLY)
            stage = "encoding"
            snapshot = _text_snapshot(response["body"], label) if suffix in (".txt", ".md") else response["body"]
            del response
            acquisition["capture_status"] = "not_applied" if render_js == "always" else "static"
            snapshot_kind = "document"
        else:
            stage = "prerequisites"
            if not htmltext._SUPPORTED_RUNTIME or htmltext.LexborHTMLParser is None:
                raise BrewdocError("URL HTML parser unavailable")
            stage = "encoding"
            source, acquisition["charset"], acquisition["charset_source"] = htmltext._decode_html(
                response["body"], label)
            stage = "challenge"
            check = _page_check(source, final_url, response["headers"])
            csp = any(key.lower() == "content-security-policy" for key, _ in response["headers"])
            del response
            if check and (check != "shell" or not render_js):
                raise _page_refusal(check, acquisition["http_status"])
            if render_js == "always" or check:
                stage = "javascript"
                acquisition.update(javascript)

                def fetch(request: dict) -> dict:
                    """Revalidate the finite parent request before guarded acquisition."""
                    if isinstance(request, dict) and request.get("method") == "POST":
                        if (request.get("kind") != "api" or type(request.get("body")) is not str
                                or request.get("origin") != "https://hn.algolia.com"
                                or htmljs._origin(final_url) != "https://hn.algolia.com"
                                or request.get("requested_with") is not False
                                or request.get("headers") or request.get("body_present")
                                or request.get("content_type") != _SEARCH_CONTENT_TYPE):
                            raise URLPolicyError("URL search request policy refused")
                        return _fetch(request["url"], deadline=deadline, kind="api", budget=budget,
                                      origin=request["origin"], search_body=request["body"])
                    if (not isinstance(request, dict) or request.get("kind") not in ("script", "module", "api")
                            or request.get("method", "GET") != "GET" or request.get("body_present")
                            or request.get("headers")):
                        raise URLPolicyError("URL child request policy refused")
                    return _fetch(request["url"], deadline=deadline, kind=request["kind"], budget=budget,
                                  origin=request.get("origin"), requested_with=request.get("requested_with", False))

                soft_window = max(0.0, started + acquisition["soft_window_seconds"] - time.monotonic())
                try:
                    capture = htmljs.render(source, final_url, fetch, deadline, soft_window=soft_window)
                except URLTimeoutError:
                    capture = htmljs._initial_capture(source, soft_window)
                snapshot_kind = capture.get("snapshot_kind", "javascript_dom")
                source = capture["html"]
                acquisition["capture_status"] = ("settled" if capture.get("capture_status") == "settled"
                                                  else "partial")
                acquisition["engine"].update({"version": "0.17.0.1", "dom_version": "0.18.13"})
                errors = capture.get("errors", [])
                acquisition["error_count"] = capture.get("error_count", len(errors))
                acquisition["errors"] = [{"category": error.get("category", "script"),
                                           "resource_url": _display_url(error["resource_url"])
                                           if error.get("resource_url") else None}
                                          for error in htmljs._capture_errors(errors, 16)]
                acquisition["pending"] = capture["pending"]
                acquisition["counts"].update(capture["counts"])
                if csp:
                    acquisition["capture_status"] = "partial"
                    acquisition["error_count"] += 1
                    if len(acquisition["errors"]) < 16:
                        acquisition["errors"].append({"category": "csp_policy_unsupported", "resource_url": None})
                del capture
                if check := _page_check(source, final_url, ()):
                    raise _page_refusal(check, acquisition["http_status"], rendered=True)
            else:
                acquisition["capture_status"] = "static"
                snapshot_kind = "static_html"
            stage = "snapshot"
            snapshot, base = _snapshot(source, final_url)
            del source
            acquisition["effective_base_url"] = _display_url(base)
        acquisition["snapshot"] = {"kind": snapshot_kind,
                                    "bytes": len(snapshot), "sha256": hashlib.sha256(snapshot).hexdigest()}
        acquisition["counts"]["requests"] = budget["accepted_requests"]
        stage = "conversion"
        with tempfile.TemporaryDirectory(prefix="brewdoc-url-") as folder:
            path = Path(folder) / ("url-%s%s" % (url_hash[:16], suffix))
            path.write_bytes(snapshot)
            del snapshot
            code, receipt, markdown = run(path, out, article=article, body_only=body_only, links=links)
        if code != 0:
            stage = "output" if receipt["reason"].startswith("output write failed:") else "conversion"
            receipt["reason"] = "URL snapshot conversion or output refused"
            acquisition["capture_status"] = "refused"
            acquisition["refusal_stage"] = stage
    except (BrewdocError, OSError, ValueError, ImportError) as exc:
        stage = getattr(exc, "acquisition_stage", stage)
        acquisition["capture_status"] = "refused"
        acquisition["refusal_stage"] = stage
        acquisition["counts"]["requests"] = budget["accepted_requests"]
        if final_url is None:
            acquisition.update({"http_status": getattr(exc, "http_status", None),
                                "redirect_count": getattr(exc, "redirect_count", 0)})
        resource_url = getattr(exc, "resource_url", None) or (final_url if stage != "options" else None)
        acquisition["errors"] = [{"category": stage, "resource_url": resource_url and _display_url(resource_url)}]
        acquisition["error_count"] = 1
        acquisition["challenge"] = getattr(exc, "challenge", None)
        shown = stage in ("options", "status", "redirect", "challenge", "javascript_required") or (
            stage == "timeout" and acquisition["timeout_seconds"] is None)
        reason = str(exc) if shown else "URL acquisition refused during %s" % stage
        suffix = getattr(exc, "document_suffix", suffix)
        route = _html_receipt_route(article, links) if suffix == ".html" else ROUTES[suffix]
        code, receipt, markdown = EXIT_FAIL, _line(route, False, reason, "url" + suffix), ""
    receipt.update({"source": _display_url(requested), "receipt_schema": "brewdoc.receipt/2",
                    "acquisition": acquisition})
    return code, receipt, markdown
