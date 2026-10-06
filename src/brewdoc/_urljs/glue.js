(function () {
  const hostGlobal = globalThis;
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
  const NativeTypeError = TypeError;
  const promiseResolve = Promise.resolve.bind(Promise);
  const promiseReject = NativePromise.reject.bind(NativePromise);
  const promiseThen = Function.call.bind(NativePromise.prototype.then);
  const promiseCatch = Function.call.bind(NativePromise.prototype.catch);
  const promiseFinally = Function.call.bind(NativePromise.prototype.finally);
  const encodeURL = encodeURIComponent;
  const hasOwn = Function.call.bind(Object.prototype.hasOwnProperty);
  const primitive = Symbol.toPrimitive;
  const create = Object.create.bind(Object);
  const setPrototype = Object.setPrototypeOf.bind(Object);
  const entries = Object.entries.bind(Object);
  const isArray = Array.isArray.bind(Array);
  const asObject = Object;
  const asString = String;
  const asNumber = Number;
  const finiteNumber = Number.isFinite.bind(Number);
  const truncate = Math.trunc.bind(Math);
  const ownKeys = Reflect.ownKeys.bind(Reflect);
  const ownDescriptor = Object.getOwnPropertyDescriptor.bind(Object);
  const prototypeOf = Object.getPrototypeOf.bind(Object);
  const rawKeys = [NEXT, END, START, VALUE, MIME, primitive, PREV, PRIVATE, CLASS_LIST,
    DATASET, STYLE, SHEET, CHANGED, UPGRADE, Symbol.replace, 'nodeType', 'ownerDocument',
    'localName', 'name', 'publicId', 'systemId', 'toString', 'valueOf', 'childNodes',
    'firstChild', 'nextSibling', 'cloneNode', 'textContent', 'data', 'attributes',
    'ownerSVGElement', 'createElement', 'createElementNS', 'ignoreCase', 'voidElements',
    'ownerElement', 'length', 'push', 'join', 'test', 'exec', 'flags', 'source',
    'lastIndex', 'call', 'prototype', 'set', 'Map', 'global', 'multiline', 'dotAll',
    'unicode', 'unicodeSets', 'sticky', 'hasIndices'];
  const setValue = Reflect.set.bind(Reflect);
  const defineProperty = Object.defineProperty.bind(Object);
  const charCodeAt = Function.call.bind(String.prototype.charCodeAt);
  const applyFunction = Reflect.apply.bind(Reflect);
  const construct = Reflect.construct.bind(Reflect);
  const defineValue = Reflect.defineProperty.bind(Reflect);
  const deleteValue = Reflect.deleteProperty.bind(Reflect);
  const changePrototype = Reflect.setPrototypeOf.bind(Reflect);
  const preventExtensions = Reflect.preventExtensions.bind(Reflect);
  const descriptors = Object.getOwnPropertyDescriptors.bind(Object);
  const legacyGetter = Object.prototype.__lookupGetter__;
  const legacySetter = Object.prototype.__lookupSetter__;
  const defineGetter = Object.prototype.__defineGetter__;
  const defineSetter = Object.prototype.__defineSetter__;
  Object.assign = function (target, source, ...sources) {
    if (target === null || target === undefined) throw new NativeTypeError('assign target is null');
    const result = asObject(target);
    for (const current of [source, ...sources]) {
      if (current === null || current === undefined) continue;
      const from = asObject(current);
      for (const key of ownKeys(from)) {
        if (ownDescriptor(from, key)?.enumerable && !setValue(result, key, from[key]))
          throw new NativeTypeError('assign target write failed');
      }
    }
    return result;
  };
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
  let rawCursor;
  let rawDependencies;
  let pageWindow;
  let snapshot;
  let queryAll;
  let queryOne;
  let pageEvent;
  let dispatchLoad;
  let currentScript;
  let writeBuffer = '';
  let pendingModules = 0;
  let violation = false;
  let inlineStyleUsed = false;
  let parseState = 'idle';
  let parsedView;
  let initialParser;
  let originalWrite;
  let originalEnd;
  let originalResume;
  let originalPause;
  let tokenizerDescriptor;
  let tokenizerHook = false;
  let checkpoint = 16384;
  let pagePromiseUnknown = false;
  let pagePromise;
  let currentPromise;
  let promiseGetter;
  let promiseSetter;
  const seenScripts = new WeakSet();
  const scriptNodes = [];
  const error = (name, message) => Object.assign(new Error(message), { name });

  function markPromiseUnknown() {
    if (!pagePromiseUnknown) {
      pagePromiseUnknown = true;
      errors.push({ category: 'promise_completion_unobservable' });
    }
  }

  function captureSnapshotDependencies() {
    const record = (owner, key) => {
      let descriptor;
      for (let target = owner; target && !descriptor; target = prototypeOf(target))
        descriptor = ownDescriptor(target, key);
      const flags = descriptor ? (descriptor.configurable ? 1 : 0) |
        (descriptor.writable ? 2 : 0) | (descriptor.enumerable ? 4 : 0) |
        (hasOwn(descriptor, 'get') ? 16 : 0) : -1;
      return [owner, key, flags, descriptor?.value, descriptor?.get, descriptor?.set];
    };
    const chain = constructor => {
      const result = [];
      for (let owner = constructor; owner; owner = prototypeOf(owner)) {
        const instance = owner.prototype;
        result.push([owner, prototypeOf(owner), record(owner, 'prototype'),
          instance && typeof instance === 'object' ? prototypeOf(instance) : undefined]);
      }
      return result;
    };
    const serializerRecords = [Node$1, Attr$1, Text$1, Comment$2, CDATASection$1,
      ParentNode, DocumentFragment$1, DocumentType$1, Element$1, HTMLTemplateElement,
      TextElement, Document$1].map(constructor => record(constructor.prototype, 'toString'));
    const domRecords = [record(ParentNode.prototype, 'childNodes'),
      record(ParentNode.prototype, 'firstChild'), record(Element$1.prototype, 'cloneNode'),
      record(Element$1.prototype, 'textContent'), record(CharacterData$1.prototype, 'textContent'),
      record(CharacterData$1.prototype, 'data'), record(Attr$1.prototype, 'cloneNode'),
      record(pageDocument, 'createElement'), record(pageDocument, 'createElementNS'),
      record(HTMLStyleElement.prototype, 'textContent')];
    const stableRecords = [record(String.prototype, 'replace'), record(Function.prototype, 'call'),
      record(NodeList.prototype, 'push'), record(NodeList.prototype, 'join')];
    const regexpRecords = ['test', 'exec', 'flags', 'source', 'global', 'ignoreCase',
      'multiline', 'dotAll', 'unicode', 'unicodeSets', 'sticky', 'hasIndices', Symbol.replace]
      .map(key => record(RegExp.prototype, key));
    const cloneRecords = [record(WeakMap.prototype, 'set'), record(hostGlobal, 'Map')];
    const constructors = [...htmlClasses].map(([name, constructor]) =>
      [name, constructor.prototype, chain(constructor)]);
    constructors.push(['@attr', Attr$1.prototype, chain(Attr$1)],
      ['@svg', SVGElement$1.prototype, chain(SVGElement$1)],
      ['@nodelist', NodeList.prototype, chain(NodeList)]);
    const mime = pageDocument[MIME];
    rawDependencies = [serializerRecords, domRecords, stableRecords, regexpRecords,
      cloneRecords, constructors, mime, NodeList.prototype, ca, QUOTE, mime.voidElements,
      wm, record(Object.prototype, 'valueOf'),
      [record(mime, 'ignoreCase'), record(mime, 'voidElements')]];
  }

  function propertyKey(value) {
    if (typeof value === 'string' || typeof value === 'symbol') return value;
    const probe = create(null);
    defineProperty(probe, value, { value: 0 });
    return ownKeys(probe)[0];
  }

  function objectTarget(value, boxed = false) {
    if (value === null || value === undefined ||
        (!boxed && typeof value !== 'object' && typeof value !== 'function'))
      throw new NativeTypeError('reflection target is not an object');
    return boxed ? asObject(value) : value;
  }

  function inspectAccessor(descriptor) {
    if (descriptor && (descriptor.get === promiseGetter || descriptor.get === promiseSetter ||
                       descriptor.set === promiseGetter || descriptor.set === promiseSetter))
      markPromiseUnknown();
    return descriptor;
  }

  function installPromises() {
    pagePromise = function Promise(executor) {
      if (!new.target) throw new NativeTypeError('Promise requires new');
      return construct(NativePromise, [executor], new.target);
    };
    const target = create(NativePromise.prototype);
    for (const name of ownKeys(NativePromise.prototype)) {
      const descriptor = ownDescriptor(NativePromise.prototype, name);
      if (name === 'constructor') descriptor.value = pagePromise;
      defineProperty(target, name, descriptor);
    }
    const prototype = new Proxy(target, {
      set(object, name, value, receiver) { markPromiseUnknown(); return setValue(object, name, value, receiver); },
      defineProperty(object, name, descriptor) { markPromiseUnknown(); return defineValue(object, name, descriptor); },
      deleteProperty(object, name) { markPromiseUnknown(); return deleteValue(object, name); },
      setPrototypeOf(object, parent) { markPromiseUnknown(); return changePrototype(object, parent); },
      preventExtensions(object) { markPromiseUnknown(); return preventExtensions(object); }
    });
    defineProperty(pagePromise, 'prototype', { value: prototype });
    setPrototype(pagePromise, NativePromise);
    currentPromise = pagePromise;
    promiseGetter = () => currentPromise;
    promiseSetter = value => {
      if (value !== currentPromise) markPromiseUnknown();
      currentPromise = value;
    };
    defineProperty(globalThis, 'Promise', { configurable: true, enumerable: false,
      get: promiseGetter, set: promiseSetter });

    const readDescriptor = (target, key, boxed) => {
      const object = objectTarget(target, boxed);
      const name = propertyKey(key);
      if (name === 'Promise') markPromiseUnknown();
      return inspectAccessor(ownDescriptor(object, name));
    };
    const defineDescriptor = (target, key, descriptor, reflective) => {
      const object = objectTarget(target);
      const name = propertyKey(key);
      if (name === 'Promise') markPromiseUnknown();
      return reflective ? defineValue(object, name, descriptor) : defineProperty(object, name, descriptor);
    };
    const lookup = (target, key, method) => {
      const object = objectTarget(target, true);
      const name = propertyKey(key);
      if (name === 'Promise') markPromiseUnknown();
      const capability = applyFunction(method, object, [name]);
      if (capability === promiseGetter || capability === promiseSetter) markPromiseUnknown();
      return capability;
    };
    const defineLegacy = (target, key, callback, method) => {
      const object = objectTarget(target, true);
      if (typeof callback !== 'function') throw new NativeTypeError('accessor is not callable');
      const name = propertyKey(key);
      if (name === 'Promise') markPromiseUnknown();
      return applyFunction(method, object, [name, callback]);
    };
    const guards = [
      [Object, 'getOwnPropertyDescriptor', function (target, key) { return readDescriptor(target, key, true); }],
      [Reflect, 'getOwnPropertyDescriptor', function (target, key) { return readDescriptor(target, key, false); }],
      [Object, 'getOwnPropertyDescriptors', function (target) {
        const result = descriptors(objectTarget(target, true));
        for (const name of ownKeys(result)) {
          if (name === 'Promise') markPromiseUnknown();
          inspectAccessor(result[name]);
        }
        return result;
      }],
      [Object, 'defineProperty', function (target, key, descriptor) { return defineDescriptor(target, key, descriptor, false); }],
      [Reflect, 'defineProperty', function (target, key, descriptor) { return defineDescriptor(target, key, descriptor, true); }],
      [Object.prototype, '__lookupGetter__', function (key) { 'use strict'; return lookup(this, key, legacyGetter); }],
      [Object.prototype, '__lookupSetter__', function (key) { 'use strict'; return lookup(this, key, legacySetter); }],
      [Object.prototype, '__defineGetter__', function (key, callback) { 'use strict'; return defineLegacy(this, key, callback, defineGetter); }],
      [Object.prototype, '__defineSetter__', function (key, callback) { 'use strict'; return defineLegacy(this, key, callback, defineSetter); }]
    ];
    for (const [object, name, value] of guards)
      defineProperty(object, name, { ...ownDescriptor(object, name), value, writable: false, configurable: false });
  }

  function releaseParser() {
    if (tokenizerHook) {
      if (tokenizerDescriptor)
        defineProperty(initialParser.tokenizer, 'shouldContinue', tokenizerDescriptor);
      else delete initialParser.tokenizer.shouldContinue;
    }
    tokenizerHook = false;
    initialParser = originalWrite = originalEnd = originalResume = originalPause = tokenizerDescriptor = undefined;
  }

  function failParsing(reason) {
    parseState = 'failed';
    parsedView = undefined;
    releaseParser();
    throw reason;
  }

  globalThis.__smallParseStart = source => {
    if (parseState !== 'idle') failParsing(new Error('initial parser already started'));
    parseState = 'failed';
    const prototype = Parser$1.prototype;
    const writeDescriptor = ownDescriptor(prototype, 'write');
    const endDescriptor = ownDescriptor(prototype, 'end');
    originalWrite = writeDescriptor.value;
    originalEnd = endDescriptor.value;
    originalResume = prototype.resume;
    originalPause = prototype.pause;
    let input;
    let writeChanged = false;
    let endChanged = false;
    try {
      try {
        defineProperty(prototype, 'write', { ...writeDescriptor, value: function (markup) {
          if (initialParser) throw new Error('initial parser duplicate write');
          initialParser = this;
          input = markup;
        } });
        writeChanged = true;
        defineProperty(prototype, 'end', { ...endDescriptor, value: function () {
          if (this !== initialParser) throw new Error('initial parser mismatch');
        } });
        endChanged = true;
        parsedView = parseHTML(source);
      } finally {
        try {
          if (endChanged) defineProperty(prototype, 'end', endDescriptor);
        } finally {
          if (writeChanged) defineProperty(prototype, 'write', writeDescriptor);
        }
      }
      if (initialParser) {
        const tokenizer = initialParser.tokenizer;
        const shouldContinue = tokenizer.shouldContinue.bind(tokenizer);
        tokenizerDescriptor = ownDescriptor(tokenizer, 'shouldContinue');
        defineProperty(tokenizer, 'shouldContinue', { configurable: true, value() {
          if (!shouldContinue()) return false;
          if (tokenizer.index >= checkpoint) {
            originalPause.call(initialParser);
            return false;
          }
          return true;
        } });
        tokenizerHook = true;
        originalWrite.call(initialParser, input);
      }
      parseState = initialParser && !initialParser.tokenizer.running ? 'paused' : 'drained';
      return parseState === 'paused' ? 1 : 0;
    } catch (reason) { failParsing(reason); }
  };
  globalThis.__smallParseResume = () => {
    if (parseState !== 'paused') failParsing(new Error('initial parser has no paused work'));
    parseState = 'failed';
    checkpoint = initialParser.tokenizer.index + 16384;
    try {
      originalResume.call(initialParser);
      parseState = initialParser.tokenizer.running ? 'drained' : 'paused';
      return parseState === 'paused' ? 1 : 0;
    } catch (reason) { failParsing(reason); }
  };
  globalThis.__smallParseEnd = () => {
    if (parseState !== 'drained') failParsing(new Error('initial parser is not drained'));
    parseState = 'failed';
    try {
      if (initialParser) originalEnd.call(initialParser);
      releaseParser();
      parseState = 'ready';
      return 1;
    } catch (reason) { failParsing(reason); }
  };

  function utf8Bytes(text) {
    let bytes = 0;
    for (let index = 0; index < text.length; index++) {
      const code = charCodeAt(text, index);
      if (code < 128) bytes++;
      else if (code < 2048) bytes += 2;
      else if (code >= 0xd800 && code <= 0xdbff && index + 1 < text.length &&
               charCodeAt(text, index + 1) >= 0xdc00 && charCodeAt(text, index + 1) <= 0xdfff) {
        bytes += 4;
        index++;
      } else bytes += 3;
    }
    return bytes;
  }

  function installStorage() {
    const stored = new Map();
    const reserved = new Set(['getItem', 'setItem', 'removeItem', 'clear', 'key', 'length']);
    let bytes = 0;
    let used = false;
    const touch = () => {
      if (!used) { used = true; errors.push({ category: 'ephemeral_storage_only' }); }
    };
    const dataKey = value => {
      if (typeof value === 'symbol') throw new TypeError('storage symbol key unsupported');
      const key = asString(value);
      if (reserved.has(key)) throw new TypeError('storage API name is reserved');
      return key;
    };
    const setItem = (name, value) => {
      touch();
      const key = dataKey(name);
      const text = asString(value);
      const existing = stored.has(key);
      const proposed = bytes + utf8Bytes(text) - (existing ? utf8Bytes(stored.get(key)) : 0) +
        (existing ? 0 : utf8Bytes(key));
      if ((!existing && stored.size >= 200) || proposed > 65536) {
        violation = true;
        throw new RangeError('storage quota exceeded');
      }
      stored.set(key, text);
      bytes = proposed;
    };
    const removeItem = name => {
      touch();
      const key = dataKey(name);
      if (stored.has(key)) {
        bytes -= utf8Bytes(key) + utf8Bytes(stored.get(key));
        stored.delete(key);
      }
    };
    const target = create(null);
    const methods = {
      getItem(name) { touch(); return stored.get(dataKey(name)) ?? null; },
      setItem, removeItem,
      clear() { touch(); stored.clear(); bytes = 0; },
      key(index) {
        touch();
        const value = asNumber(index);
        const position = finiteNumber(value) ? truncate(value) : 0;
        return [...stored.keys()][position] ?? null;
      }
    };
    for (const [name, method] of entries(methods))
      defineProperty(target, name, { value: method, configurable: true });
    defineProperty(target, 'length', { configurable: true, get() { touch(); return stored.size; } });
    const unsupported = () => { touch(); throw new TypeError('storage mutation unsupported'); };
    const storage = new Proxy(target, {
      get(object, name) {
        touch();
        if (typeof name === 'symbol') throw new TypeError('storage symbol key unsupported');
        return reserved.has(name) ? object[name] : stored.get(name);
      },
      set(object, name, value) { setItem(name, value); return true; },
      deleteProperty(object, name) { removeItem(name); return true; },
      has(object, name) { touch(); return reserved.has(name) || stored.has(dataKey(name)); },
      ownKeys() { touch(); return [...stored.keys()]; },
      getOwnPropertyDescriptor(object, name) {
        touch();
        if (typeof name === 'symbol') throw new TypeError('storage symbol key unsupported');
        if (reserved.has(name)) return ownDescriptor(object, name);
        return stored.has(name) ? { value: stored.get(name), configurable: true, enumerable: true, writable: true } : undefined;
      },
      defineProperty: unsupported, setPrototypeOf: unsupported, preventExtensions: unsupported
    });
    defineProperty(globalThis, 'localStorage', { configurable: true, get() { touch(); return storage; } });
  }

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
      if (this.bodyUsed) return promiseReject(new TypeError('body already consumed'));
      this.bodyUsed = true;
      return promiseResolve(this.payload);
    }
    json() { return promiseThen(this.text(), text => parse(text)); }
    clone() {
      if (this.bodyUsed) throw new TypeError('body already consumed');
      return new Response({ status: this.status, url: this.url, headers: Object.fromEntries(this.headers),
        body: this.payload, redirected: this.redirected });
    }
  }

  function searchBodyAllowed(body) {
    for (let index = 0; index < body.length; index++) {
      const code = charCodeAt(body, index);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = charCodeAt(body, ++index);
        if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      } else if (code >= 0xdc00 && code <= 0xdfff) return false;
    }
    if (utf8Bytes(body) > 16384) { violation = true; throw new RangeError('search body byte bound'); }
    let value;
    try { value = parse(body); } catch { return true; }
    const pending = [[value, 0]];
    let count = 0;
    while (pending.length) {
      const [item, depth] = pending.pop();
      if (depth > 4) { violation = true; throw new RangeError('search body depth bound'); }
      if (isArray(item)) {
        count += item.length;
        if (count > 256) { violation = true; throw new RangeError('search array entry bound'); }
        for (const child of item) pending.push([child, depth + 1]);
      } else if (item !== null && typeof item === 'object') {
        for (const [, child] of entries(item)) pending.push([child, depth + 1]);
      }
    }
    return true;
  }

  function fetch(input, options = {}, xhr = null) {
    const id = nextId++;
    return new NativePromise((resolve, reject) => {
      if (options.signal?.aborted) { reject(error('AbortError', 'request aborted')); return; }
      const request = { id, url: String(input), method: String(options.method || 'GET').toUpperCase(),
        headers: Object.fromEntries(new Headers(options.headers)), mode: options.mode || 'cors',
        credentials: options.credentials || 'same-origin', redirect: options.redirect || 'follow' };
      request.body_present = options.body !== undefined && options.body !== null;
      if (request.method === 'POST') {
        if (xhr === null || !request.body_present || typeof options.body !== 'string' || !searchBodyAllowed(options.body)) {
          errors.push({ category: 'request_options_unsupported' });
          reject(error('TypeError', 'request_options_unsupported')); return;
        }
        request.body = options.body;
        request.xhr = true;
        request.with_credentials = xhr.with_credentials;
        request.headers_conflict = xhr.headers_conflict;
      }
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

  globalThis.__smallBoot = (url, locationJSON) => {
    if (parseState !== 'ready') failParsing(new Error('initial parser view is not ready'));
    const view = parsedView;
    parsedView = undefined;
    baseURL = url;
    installStorage();
    globalThis.window = view;
    globalThis.document = view.document;
    pageDocument = view.document;
    pageWindow = view;
    const htmlConstructor = view.HTMLElement;
    const htmlInstance = Function.call.bind(Function.prototype[Symbol.hasInstance]);
    const innerText = ownDescriptor(Element$1.prototype, 'innerText');
    const createFragment = Function.call.bind(pageDocument.createDocumentFragment);
    const createText = Function.call.bind(pageDocument.createTextNode);
    const createElement = Function.call.bind(pageDocument.createElement);
    const appendChild = Function.call.bind(ParentNode.prototype.appendChild);
    const replaceChildren = Function.call.bind(ParentNode.prototype.replaceChildren);
    const sliceText = Function.call.bind(String.prototype.slice);
    defineProperty(htmlConstructor.prototype, 'innerText', {
      configurable: true, enumerable: innerText.enumerable, get: innerText.get,
      set(value) {
        if (!htmlInstance(htmlConstructor, this)) throw new NativeTypeError('innerText requires an HTML element');
        if (typeof value === 'symbol') throw new NativeTypeError('innerText symbol unsupported');
        const text = value === null ? '' : asString(value);
        const document = this.ownerDocument;
        const fragment = createFragment(document);
        let start = 0;
        for (let index = 0; index < text.length; index++) {
          const code = charCodeAt(text, index);
          if (code !== 10 && code !== 13) continue;
          if (index > start) appendChild(fragment, createText(document, sliceText(text, start, index)));
          appendChild(fragment, createElement(document, 'br'));
          if (code === 13 && charCodeAt(text, index + 1) === 10) index++;
          start = index + 1;
        }
        if (start < text.length) appendChild(fragment, createText(document, sliceText(text, start)));
        replaceChildren(this, fragment);
      }
    });
    const descendants = Function.call.bind(ParentNode.prototype.querySelectorAll);
    const documentDescendants = Function.call.bind(view.Document.prototype.querySelectorAll);
    const namedElements = Function.call.bind(ParentNode.prototype.getElementsByTagName);
    ParentNode.prototype.getElementsByTagName = function (name) {
      if (name !== '*') return namedElements(this, name);
      return this.nodeType === 9 ? documentDescendants(this, '*') : descendants(this, '*');
    };
    const protectedRoot = Object.prototype;
    const originalToString = protectedRoot.toString;
    const defineOwn = Object.defineProperty.bind(Object);
    defineOwn(protectedRoot, 'toString', {
      configurable: true, enumerable: false,
      get() { return originalToString; },
      set(value) {
        if (this === protectedRoot) throw new TypeError('protected root mutation');
        defineOwn(this, 'toString', { value, configurable: true, writable: true, enumerable: true });
      }
    });
    snapshot = pageDocument.toString.bind(pageDocument);
    queryAll = pageDocument.querySelectorAll.bind(pageDocument);
    queryOne = pageDocument.querySelector.bind(pageDocument);
    pageEvent = view.Event;
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
    globalThis.innerWidth = 1024;
    pageDocument.write = (...parts) => {
      if (!currentScript) throw new Error('document.write outside initial classic script unsupported');
      writeBuffer += parts.join('');
      if (writeBuffer.length > 8 * 1048576) { violation = true; throw new RangeError('document.write byte bound'); }
    };
    globalThis.fetch = (input, options) => fetch(input, options);
    globalThis.Headers = Headers;
    globalThis.Response = Response;
    globalThis.queueMicrotask = callback => promiseThen(promiseResolve(), callback);
    globalThis.setTimeout = (callback, delay, ...args) => setTimer(callback, delay, false, args);
    globalThis.setInterval = (callback, delay, ...args) => setTimer(callback, delay, true, args);
    globalThis.clearTimeout = id => timers.delete(id);
    globalThis.clearInterval = globalThis.clearTimeout;
    globalThis.AbortController = class {
      constructor() { this.signal = new EventTarget(); this.signal.aborted = false; }
      abort() { this.signal.aborted = true; this.signal.dispatchEvent(new Event('abort')); }
    };
    globalThis.XMLHttpRequest = class extends EventTarget {
      #headersConflict = false;
      constructor() {
        super(); this.readyState = 0; this.status = 0; this.responseType = ''; this.headers = {};
        this.responseText = ''; this.response = ''; this.responseURL = ''; this.timeout = 0;
        this.withCredentials = false;
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
        if (entries(this.headers).some(([key]) => key.toLowerCase() === String(name).toLowerCase()))
          this.#headersConflict = true;
        this.headers[name] = value;
      }
      getResponseHeader(name) { return this.responseHeaders?.get(name) ?? null; }
      getAllResponseHeaders() {
        return this.responseHeaders ? [...this.responseHeaders.entries()].map(([name, value]) => name + ': ' + value + '\r\n').join('') : '';
      }
      send(body = null) {
        if (this.readyState !== 1) throw new TypeError('XHR not opened');
        const post = String(this.method).toUpperCase() === 'POST';
        const credentials = this.withCredentials;
        if (post && (typeof body !== 'string' || credentials !== false)) {
          errors.push({ category: 'request_options_unsupported' });
          throw new TypeError('request_options_unsupported');
        }
        if (!post && body !== null) throw new TypeError('only opened bodyless XHR supported');
        if (post && entries(this.headers).some(([, value]) => typeof value !== 'string')) {
          errors.push({ category: 'request_headers_unsupported' });
          throw new TypeError('request_headers_unsupported');
        }
        if (!['', 'text', 'json'].includes(this.responseType)) throw new TypeError('XHR responseType unsupported');
        this.controller = new AbortController();
        const timeout = this.timeout > 0 ? setTimeout(() => { this.timedOut = true; this.abort(); }, this.timeout) : null;
        const responseText = promiseThen(
          fetch(this.url, { method: this.method, headers: this.headers, signal: this.controller.signal,
            ...(post ? { body } : {}) }, post ? { with_credentials: credentials,
              headers_conflict: this.#headersConflict } : null), response => {
            this.status = response.status; this.responseURL = response.url;
            this.responseHeaders = response.headers; this.change(2);
            return response.text();
          });
        const loaded = promiseThen(responseText, text => {
            this.change(3); this.responseText = text;
            this.response = this.responseType === 'json' ? parse(text) : text;
            this.change(4); this.emit('load'); this.emit('loadend');
          });
        promiseFinally(promiseCatch(loaded, reason => {
            this.status = 0; this.change(4);
            this.emit(this.timedOut ? 'timeout' : reason.name === 'AbortError' ? 'abort' : 'error');
            this.emit('loadend');
          }), () => clearTimeout(timeout));
      }
      abort() { this.controller?.abort(); }
    };
    installPromises();
    for (const prototype of [Object.prototype, Array.prototype, Map.prototype,
      Set.prototype, WeakSet.prototype, NativePromise.prototype, Function.prototype])
      Object.freeze(prototype);
    for (const constructor of [Object, Array, Map, Set, WeakSet, NativePromise, Promise, Function, JSON])
      Object.freeze(constructor);
    for (const name of ['Node', 'Element', 'Document', 'HTMLElement', 'HTMLScriptElement', 'HTMLAnchorElement',
      'Text', 'Comment', 'DocumentType']) {
      let prototype = view[name]?.prototype;
      while (prototype && prototype !== Object.prototype) {
        Object.freeze(prototype);
        prototype = Object.getPrototypeOf(prototype);
      }
    }
    captureSnapshotDependencies();
    parseState = 'bound';
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
  globalThis.__smallErrors = () => {
    const descriptor = ownDescriptor(hostGlobal, 'Promise');
    if (!descriptor || descriptor.get !== promiseGetter || descriptor.set !== promiseSetter ||
        descriptor.configurable !== true || descriptor.enumerable !== false || currentPromise !== pagePromise)
      markPromiseUnknown();
    return controlJSON(errors);
  };
  globalThis.__smallRawRoot = () => { rawCursor = pageDocument; return rawCursor; };
  globalThis.__smallRawCurrent = () => rawCursor;
  globalThis.__smallRawAdvance = next => { rawCursor = next; };
  globalThis.__smallRawKey = index => rawKeys[index];
  globalThis.__smallRawDependency = index => rawDependencies[index];
  globalThis.__smallSnapshot = () => {
    if (parseState !== 'bound') failParsing(new Error('initial parser view is not bound'));
    return snapshot();
  };
  globalThis.__smallStartScript = index => { currentScript = scriptNodes[index]; };
  globalThis.__smallEndScript = () => {
    if (writeBuffer) currentScript.insertAdjacentHTML('afterend', writeBuffer);
    writeBuffer = ''; currentScript = null;
  };
  globalThis.__smallAbortScript = () => { writeBuffer = ''; currentScript = null; };
  globalThis.__smallTrackModule = value => {
    pendingModules++;
    promiseThen(promiseResolve(value), () => { pendingModules--; }, reason => {
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
