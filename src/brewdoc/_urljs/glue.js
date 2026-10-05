(function () {
  let nextId = 1;
  let clock = 0;
  let baseURL = '';
  const requests = [];
  const pending = new Map();
  const timers = new Map();
  const errors = [];
  const parse = JSON.parse.bind(JSON);
  const stringify = JSON.stringify.bind(JSON);
  const NativePromise = Promise;
  const promiseResolve = Promise.resolve.bind(Promise);
  const encodeURL = encodeURIComponent;
  const hasOwn = Function.call.bind(Object.prototype.hasOwnProperty);
  const primitive = Symbol.toPrimitive;
  const serializers = new Set([Node$1, Attr$1, Text$1, Comment$2, CDATASection$1,
    ParentNode, DocumentFragment$1, DocumentType$1, Element$1, HTMLTemplateElement, TextElement, Document$1]
    .map(constructor => constructor.prototype.toString));
  const create = Object.create.bind(Object);
  const setPrototype = Object.setPrototypeOf.bind(Object);
  const entries = Object.entries.bind(Object);
  const isArray = Array.isArray.bind(Array);
  const controlJSON = value => {
    const copy = item => {
      if (!item || typeof item !== 'object') return item;
      const cloned = isArray(item) ? [] : create(null);
      for (const [key, child] of entries(item)) cloned[key] = copy(child);
      if (isArray(cloned)) setPrototype(cloned, null);
      return cloned;
    };
    return stringify(copy(value));
  };
  let pageDocument;
  let pageWindow;
  let snapshot;
  let queryAll;
  let queryOne;
  let pageEvent;
  let firstChild;
  let nextSibling;
  let getAttributes;
  let dispatchLoad;
  let currentScript;
  let writeBuffer = '';
  let pendingModules = 0;
  let violation = false;
  let inlineStyleUsed = false;
  const seenScripts = new WeakSet();
  const scriptNodes = [];
  const error = (name, message) => Object.assign(new Error(message), { name });

  function anchorURL(value) {
    const raw = String(value);
    if (raw.length > 8192 || encodeURL(raw).replace(/%[0-9a-f]{2}/gi, '_').length > 8192 ||
        /[\u0000-\u0020\u007f\\]/.test(raw) || /%(?![0-9a-f]{2})/i.test(raw))
      throw new TypeError('anchor URL form unsupported');
    const absolute = text => {
      const match = /^(https?):\/\/(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(?::([0-9]{1,5}))?(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i.exec(text);
      if (!match || Number(match[3] || 0) > 65535) throw new TypeError('anchor URL authority unsupported');
      const protocol = match[1].toLowerCase() + ':';
      const hostname = match[2].toLowerCase();
      const port = match[3] && !((protocol === 'https:' && Number(match[3]) === 443) ||
        (protocol === 'http:' && Number(match[3]) === 80)) ? String(Number(match[3])) : '';
      return { protocol, hostname, port, host: hostname + (port ? ':' + port : ''),
        pathname: match[4] || '/', search: match[5] || '', hash: match[6] || '' };
    };
    let parsed;
    if (/^https?:\/\//i.test(raw)) parsed = absolute(raw);
    else {
      const base = absolute(pageDocument.baseURI);
      if (raw.startsWith('//')) parsed = absolute(base.protocol + raw);
      else {
        if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) throw new TypeError('anchor URL scheme unsupported');
        const relative = /^([^?#]*)(\?[^#]*)?(#.*)?$/.exec(raw);
        const pathname = relative[1] ? (relative[1].startsWith('/') ? relative[1] :
          base.pathname.slice(0, base.pathname.lastIndexOf('/') + 1) + relative[1]) : base.pathname;
        parsed = { ...base, pathname, search: relative[2] || (relative[1] ? '' : base.search), hash: relative[3] || '' };
      }
    }
    if (/(?:^|\/)(?:%2e|\.%2e|%2e\.|%2e%2e)(?:\/|$)/i.test(parsed.pathname))
      throw new TypeError('encoded anchor path segments unsupported');
    const parts = [];
    for (const part of parsed.pathname.split('/')) {
      if (part === '..') { if (parts.length > 1) parts.pop(); }
      else if (part !== '.') parts.push(part);
    }
    if (/\/(?:\.|\.\.)$/.test(parsed.pathname)) parts.push('');
    parsed.pathname = parts.join('/') || '/';
    parsed.href = parsed.protocol + '//' + parsed.host + parsed.pathname + parsed.search + parsed.hash;
    return parsed;
  }

  class Headers {
    constructor(source = {}) {
      this.values = new Map(Object.entries(source).map(([key, value]) => [key.toLowerCase(), String(value)]));
    }
    get(name) { return this.values.get(String(name).toLowerCase()) ?? null; }
    set(name, value) { this.values.set(String(name).toLowerCase(), String(value)); }
    entries() { return this.values.entries(); }
    [Symbol.iterator]() { return this.entries(); }
  }

  class Response {
    constructor(record) {
      this.status = record.status;
      this.ok = this.status >= 200 && this.status < 300;
      this.url = record.url;
      this.redirected = record.redirected;
      this.headers = new Headers(record.headers);
      this.bodyUsed = false;
      this.payload = record.body;
    }
    text() {
      if (this.bodyUsed) return NativePromise.reject(new TypeError('body already consumed'));
      this.bodyUsed = true;
      return promiseResolve(this.payload);
    }
    json() { return this.text().then(text => parse(text)); }
    clone() {
      if (this.bodyUsed) throw new TypeError('body already consumed');
      return new Response({ status: this.status, url: this.url, headers: Object.fromEntries(this.headers),
        body: this.payload, redirected: this.redirected });
    }
  }

  function fetch(input, options = {}) {
    const id = nextId++;
    return new NativePromise((resolve, reject) => {
      if (options.signal?.aborted) { reject(error('AbortError', 'request aborted')); return; }
      const request = { id, url: String(input), method: String(options.method || 'GET').toUpperCase(),
        headers: Object.fromEntries(new Headers(options.headers)), mode: options.mode || 'cors',
        credentials: options.credentials || 'same-origin', redirect: options.redirect || 'follow' };
      request.body_present = options.body !== undefined && options.body !== null;
      pending.set(id, { resolve, reject, aborted: false });
      options.signal?.addEventListener('abort', () => {
        const state = pending.get(id);
        if (state) { state.aborted = true; state.reject(error('AbortError', 'request aborted')); pending.delete(id); }
      }, { once: true });
      requests.push(request);
    });
  }

  function setTimer(callback, delay = 0, repeat = false, args = []) {
    if (typeof callback !== 'function') throw new TypeError('string timers unsupported');
    if (timers.size >= 200) { violation = true; throw new RangeError('timer count exceeded'); }
    const id = nextId++;
    const milliseconds = Math.max(0, Math.min(2147483647, Number(delay) || 0));
    timers.set(id, { callback, args, delay: milliseconds, due: clock + milliseconds, repeat });
    return id;
  }

  globalThis.__smallBoot = (html, url, locationJSON) => {
    const view = parseHTML(html);
    baseURL = url;
    globalThis.window = view;
    globalThis.document = view.document;
    pageDocument = view.document;
    pageWindow = view;
    snapshot = pageDocument.toString.bind(pageDocument);
    queryAll = pageDocument.querySelectorAll.bind(pageDocument);
    queryOne = pageDocument.querySelector.bind(pageDocument);
    pageEvent = view.Event;
    firstChild = Function.call.bind(Object.getOwnPropertyDescriptor(ParentNode.prototype, 'firstChild').get);
    nextSibling = Function.call.bind(Object.getOwnPropertyDescriptor(view.Element.prototype, 'nextSibling').get);
    getAttributes = Function.call.bind(Object.getOwnPropertyDescriptor(view.Element.prototype, 'attributes').get);
    dispatchLoad = view.dispatchEvent.bind(view);
    for (const key of ['HTMLElement', 'customElements', 'Event', 'CustomEvent', 'EventTarget', 'Node'])
      globalThis[key] = view[key];
    Object.defineProperty(document, 'readyState', { value: 'loading', writable: true });
    Object.defineProperty(document, 'baseURI', { value: url, writable: true });
    globalThis.location = parse(locationJSON);
    Object.defineProperty(globalThis, 'history', { value: Object.freeze({ length: 1, state: null }) });
    const anchorPrototype = view.HTMLAnchorElement.prototype;
    const href = Object.getOwnPropertyDescriptor(anchorPrototype, 'href');
    const readHref = element => stringAttribute.get(element, 'href').trim();
    Object.defineProperty(anchorPrototype, 'href', {
      configurable: true, get() { const raw = readHref(this); return raw ? anchorURL(raw).href : ''; }, set: href.set
    });
    for (const component of ['protocol', 'host', 'hostname', 'port', 'pathname', 'search', 'hash'])
      Object.defineProperty(anchorPrototype, component, { configurable: true, get() { return anchorURL(readHref(this))[component]; } });
    globalThis.getComputedStyle = element => {
      if (!element || element.nodeType !== 1) throw new TypeError('inline style requires an element');
      if (!inlineStyleUsed) { inlineStyleUsed = true; errors.push({ category: 'inline_style_only' }); }
      const read = element.style.getPropertyValue.bind(element.style);
      return new Proxy(Object.freeze({ getPropertyValue: name => read(String(name)) || '' }), {
        get(target, name) {
          if (name === 'getPropertyValue') return target.getPropertyValue;
          if (typeof name !== 'string') return undefined;
          return read(name === 'cssFloat' ? 'float' : name.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase())) || '';
        }, set() { return false; }
      });
    };
    globalThis.navigator = { userAgent: 'brewdoc QuickJS-ng', language: 'en-US', cookieEnabled: false };
    pageDocument.write = (...parts) => {
      if (!currentScript) throw new Error('document.write outside initial classic script unsupported');
      writeBuffer += parts.join('');
      if (writeBuffer.length > 8 * 1048576) { violation = true; throw new RangeError('document.write byte bound'); }
    };
    globalThis.fetch = fetch;
    globalThis.Headers = Headers;
    globalThis.Response = Response;
    globalThis.queueMicrotask = callback => promiseResolve().then(callback);
    globalThis.setTimeout = (callback, delay, ...args) => setTimer(callback, delay, false, args);
    globalThis.setInterval = (callback, delay, ...args) => setTimer(callback, delay, true, args);
    globalThis.clearTimeout = id => timers.delete(id);
    globalThis.clearInterval = globalThis.clearTimeout;
    globalThis.AbortController = class {
      constructor() { this.signal = new EventTarget(); this.signal.aborted = false; }
      abort() { this.signal.aborted = true; this.signal.dispatchEvent(new Event('abort')); }
    };
    globalThis.XMLHttpRequest = class extends EventTarget {
      constructor() {
        super(); this.readyState = 0; this.status = 0; this.responseType = ''; this.headers = {};
        this.responseText = ''; this.response = ''; this.responseURL = ''; this.timeout = 0;
      }
      emit(type) {
        const event = new pageEvent(type);
        try {
          this.dispatchEvent(event);
          if (typeof this['on' + type] === 'function') this['on' + type](event);
        } catch (reason) { errors.push(String(reason)); }
      }
      change(state) { this.readyState = state; this.emit('readystatechange'); }
      open(method, url, asynchronous = true) {
        if (!asynchronous) throw new TypeError('synchronous XHR unsupported');
        this.method = method; this.url = url; this.change(1);
      }
      setRequestHeader(name, value) {
        if (this.readyState !== 1) throw new Error('XHR not opened');
        this.headers[name] = value;
      }
      getResponseHeader(name) { return this.responseHeaders?.get(name) ?? null; }
      getAllResponseHeaders() {
        return this.responseHeaders ? [...this.responseHeaders.entries()].map(([name, value]) => name + ': ' + value + '\r\n').join('') : '';
      }
      send(body = null) {
        if (this.readyState !== 1 || body !== null) throw new TypeError('only opened bodyless XHR supported');
        if (!['', 'text', 'json'].includes(this.responseType)) throw new TypeError('XHR responseType unsupported');
        this.controller = new AbortController();
        const timeout = this.timeout > 0 ? setTimeout(() => { this.timedOut = true; this.abort(); }, this.timeout) : null;
        fetch(this.url, { method: this.method, headers: this.headers, signal: this.controller.signal })
          .then(response => {
            this.status = response.status; this.responseURL = response.url;
            this.responseHeaders = response.headers; this.change(2);
            return response.text();
          }).then(text => {
            this.change(3); this.responseText = text;
            this.response = this.responseType === 'json' ? parse(text) : text;
            this.change(4); this.emit('load'); this.emit('loadend');
          }).catch(reason => {
            this.status = 0; this.change(4);
            this.emit(this.timedOut ? 'timeout' : reason.name === 'AbortError' ? 'abort' : 'error');
            this.emit('loadend');
          }).finally(() => clearTimeout(timeout));
      }
      abort() { this.controller?.abort(); }
    };
    for (const prototype of [Object.prototype, Array.prototype, Map.prototype,
      Set.prototype, WeakSet.prototype, Promise.prototype, Function.prototype])
      Object.freeze(prototype);
    for (const constructor of [Object, Array, Map, Set, WeakSet, Promise, Function, JSON])
      Object.freeze(constructor);
    for (const name of ['Node', 'Element', 'Document', 'HTMLElement', 'HTMLScriptElement', 'HTMLAnchorElement',
      'Text', 'Comment', 'DocumentType']) {
      let prototype = view[name]?.prototype;
      while (prototype && prototype !== Object.prototype) {
        Object.freeze(prototype);
        prototype = Object.getPrototypeOf(prototype);
      }
    }
  };

  globalThis.__smallBase = () => queryOne('base[href]')?.getAttribute('href') || '';
  globalThis.__smallPolicy = () => {
    for (const node of queryAll('meta[http-equiv]'))
      if (node.getAttribute('http-equiv').toLowerCase() === 'content-security-policy') return 1;
    return 0;
  };
  globalThis.__smallSetBase = url => { pageDocument.baseURI = url; };
  globalThis.__smallPending = () => pending.size;
  globalThis.__smallViolation = () => violation ? 1 : 0;
  globalThis.__smallScripts = () => {
    const found = [];
    for (const node of queryAll('script')) {
      if (seenScripts.has(node)) continue;
      seenScripts.add(node);
      const index = scriptNodes.push(node) - 1;
      found.push({index, type: node.type || '', src: node.getAttribute('src'),
        text: node.textContent, async: node.hasAttribute('async'), defer: node.hasAttribute('defer')});
    }
    return controlJSON(found);
  };
  globalThis.__smallScriptDone = (index, success) => {
    const node = scriptNodes[index];
    if (node) {
      try { node.dispatchEvent(new pageEvent(success ? 'load' : 'error')); }
      catch (reason) { errors.push(String(reason)); }
    }
  };
  globalThis.__smallRequests = () => {
    const active = requests.splice(0).filter(request => !pending.get(request.id)?.aborted);
    return controlJSON(active);
  };
  globalThis.__smallComplete = serialized => {
    const record = parse(serialized);
    const state = pending.get(record.id);
    pending.delete(record.id);
    if (!state || state.aborted) return;
    if (record.error) state.reject(error(record.name || 'TypeError', record.error));
    else state.resolve(new Response(record));
  };
  globalThis.__smallTimers = () => controlJSON([...timers].map(([id, timer]) => ({ id, due: timer.due })));
  globalThis.__smallRunTimer = (id, now) => {
    clock = now;
    const timer = timers.get(id);
    if (!timer) return;
    if (timer.repeat) timer.due = now + Math.max(1, timer.delay);
    else timers.delete(id);
    try { timer.callback(...timer.args); } catch (reason) { errors.push(String(reason)); }
  };
  globalThis.__smallClock = now => { clock = now; };
  globalThis.__smallErrors = () => controlJSON(errors);
  globalThis.__smallSnapshot = () => {
    const visit = node => {
      for (const key of ['childNodes', 'firstChild', 'nextSibling', 'cloneNode',
        'textContent', 'attributes', 'valueOf', primitive])
        if (hasOwn(node, key)) throw new Error('DOM serializer tampering');
      if (node !== pageDocument && hasOwn(node, 'toString')) throw new Error('DOM serializer tampering');
      if (node !== pageDocument && !serializers.has(node.toString)) throw new Error('DOM serializer tampering');
      if (hasOwn(node, END))
        for (const attribute of getAttributes(node))
          if (hasOwn(attribute, 'toString') || hasOwn(attribute, primitive) ||
            !serializers.has(attribute.toString)) throw new Error('DOM serializer tampering');
      if (hasOwn(node, END))
        for (let child = firstChild(node); child; child = nextSibling(child)) visit(child);
    };
    visit(pageDocument);
    return snapshot();
  };
  globalThis.__smallStartScript = index => { currentScript = scriptNodes[index]; };
  globalThis.__smallEndScript = () => {
    if (writeBuffer) currentScript.insertAdjacentHTML('afterend', writeBuffer);
    writeBuffer = ''; currentScript = null;
  };
  globalThis.__smallTrackModule = value => {
    pendingModules++;
    promiseResolve(value).then(() => { pendingModules--; }, reason => {
      pendingModules--; errors.push(String(reason));
    });
  };
  globalThis.__smallModules = () => pendingModules;
  globalThis.__smallReady = () => {
    pageDocument.readyState = 'interactive';
    try { pageDocument.dispatchEvent(new pageEvent('DOMContentLoaded')); }
    catch (reason) { errors.push(String(reason)); }
  };
  globalThis.__smallLoaded = () => {
    pageDocument.readyState = 'complete';
    try { dispatchLoad(new pageEvent('load')); } catch (reason) { errors.push(String(reason)); }
  };
})();
