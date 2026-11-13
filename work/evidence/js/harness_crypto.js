// Harness: load the REAL SkyTree common-bundle modules 1247 (crypto), 1244 (case),
// 1245 (stringifyRequestData), 1250 (Nonce) and expose their exports, so we can
// compare the Python port against the shipped JS byte for byte.
const fs = require('fs');
const crypto = require('crypto');

const BUNDLE = process.argv[2] || 'D:/workspace/github/mi-fits/work/lead/skytree/common__index.android.bundle';
const src = fs.readFileSync(BUNDLE, 'utf8');

// ---- locate modules by their end marker ",ID,[deps]);" -------------------
function extractModule(id) {
  const marker = `,${id},[`;
  let from = 0;
  for (;;) {
    const i = src.indexOf(marker, from);
    if (i < 0) return null;
    const close = src.indexOf(']);', i);
    const depsText = src.slice(i + marker.length, close);
    if (/^[\d,]*$/.test(depsText)) {
      const start = src.lastIndexOf('__d(', i);
      const body = src.slice(start, close + 3);
      if (body.startsWith('__d(')) return body;
    }
    from = i + 1;
  }
}

// ---- crypto-js compatible shim (only the pieces the module uses) ---------
function bytesToWords(bytes, sigBytes) {
  const words = [];
  const n = Math.ceil(bytes.length / 4);
  for (let i = 0; i < n; i++) {
    words.push(
      (((bytes[i * 4] || 0) & 0xff) << 24) |
        (((bytes[i * 4 + 1] || 0) & 0xff) << 16) |
        (((bytes[i * 4 + 2] || 0) & 0xff) << 8) |
        ((bytes[i * 4 + 3] || 0) & 0xff)
    );
  }
  return { words, sigBytes: sigBytes === undefined ? bytes.length : sigBytes };
}
function wordsToBytes(wa) {
  const out = Buffer.alloc(wa.sigBytes);
  for (let i = 0; i < wa.sigBytes; i++) {
    out[i] = (wa.words[i >>> 2] >>> (24 - (i % 4) * 8)) & 0xff;
  }
  return out;
}
const Utf8 = {
  parse(str) {
    const buf = Buffer.from(str, 'utf8');
    return bytesToWords(buf, buf.length);
  },
};
const CryptoJS = {
  enc: {
    Base64: {
      parse(b64) {
        const buf = Buffer.from(b64, 'base64');
        return bytesToWords(buf, buf.length);
      },
      stringify(wa) {
        return wordsToBytes(wa).toString('base64');
      },
    },
    Utf8,
  },
  lib: {
    WordArray: {
      create(words, sigBytes) {
        return { words, sigBytes };
      },
    },
  },
  SHA256(wa) {
    const buf = crypto.createHash('sha256').update(wordsToBytes(wa)).digest();
    return bytesToWords(buf, buf.length);
  },
  SHA1(x) {
    const data = typeof x === 'string' ? Buffer.from(x, 'utf8') : wordsToBytes(x);
    const buf = crypto.createHash('sha1').update(data).digest();
    return bytesToWords(buf, buf.length);
  },
};

// ---- babel / RN helper stubs, keyed by real module id -------------------
function interop(m) {
  return m && m.__esModule ? m : { default: m };
}
const STUBS = {
  3: interop,
  54: { __esModule: true, default: (obj, key, value) => Object.defineProperty(obj, key, { value, enumerable: true, configurable: true, writable: true }) },
  4: { __esModule: true, default: (instance, ctor) => { if (!(instance instanceof ctor)) throw new TypeError('classCallCheck'); } },
  5: {
    __esModule: true,
    default: (ctor, protoProps) => {
      protoProps.forEach((p) => {
        if ('value' in p) {
          Object.defineProperty(ctor.prototype, p.key, { value: p.value, writable: true, configurable: true, enumerable: false });
        } else {
          Object.defineProperty(ctor.prototype, p.key, { get: p.get, set: p.set, configurable: true });
        }
      });
      return ctor;
    },
  },
  1082: { Buffer },
  1018: CryptoJS, // plain object -> interop wraps it as { default: CryptoJS }
};

function instantiate(moduleBody) {
  const captured = { fn: null, deps: null };
  const moduleCache = {};
  function __d(fn, id, deps) {
    if (captured.fn === null) {
      captured.fn = fn;
      captured.deps = deps || [];
    }
  }
  // eslint-disable-next-line no-eval
  const __evalFn = new Function('__d', moduleBody + '\n//# no source map'); __evalFn(__d);
  if (!captured.fn) throw new Error('module not captured');
  const exportsObj = {};
  const req = (depId) => {
    if (!(depId in STUBS)) throw new Error('unexpected dep ' + depId);
    return STUBS[depId];
  };
  captured.fn(globalThis, req, undefined, undefined, undefined, exportsObj, captured.deps);
  return exportsObj;
}

const modules = {};
for (const id of [1244, 1245, 1247, 1250]) {
  const body = extractModule(id);
  if (!body) throw new Error('cannot find module ' + id);
  const exp = instantiate(body);
  modules[id] = exp;
  STUBS[id] = exp;
}

const out = {};

// --- encryptParameters -----------------------------------------------------
const nonce = process.argv[4] || 'AAAAAAAAAAAAAAAA';
const ssecurity = process.argv[5] || 'q1w2e3r4t5y6u7i8o9p0aS1d2f3g4h5j6=';
const payload = { eco_api: 'eco/scale/record/pull', params: '{"uid":1987654321,"did":"0","pageSize":200,"createTime":1731234567890}' };
const urlPath = '/app/v1/eco/api_proxy';

const caseMod = modules[1244];
const stringifyMod = modules[1245];
const cryptoMod = modules[1247];

const outer = { data: stringifyMod.stringifyRequestData(payload, undefined) };
out.stringifyRequestData = outer;
out.encrypted = cryptoMod.encryptParameters(outer, urlPath, 'post', nonce, ssecurity);
const enc = out.encrypted;
out.decrypted = cryptoMod.decryptResponse(
  cryptoMod.encryptParameters({ data: 'hello 世界' }, urlPath, 'post', nonce, ssecurity)['data'],
  nonce,
  ssecurity
);
// round trip: decrypt the encrypted "data" field
out.decryptedData = cryptoMod.decryptResponse(enc.data, nonce, ssecurity);
// case conversion
out.snake = caseMod.convertToSnakeCase({ ecoApi: 'x', paramsJson: 'y', list: [{ aB: 1 }] });
out.camel = caseMod.convertToCamelCase({ eco_api: 'x', rc4_hash_: 'y' });

// urlencoded body, the way qs.stringify would emit it
out.form = Object.keys(enc)
  .map((k) => encodeURIComponent(k) + '=' + encodeURIComponent(enc[k]))
  .join('&');

// Nonce shape check (random parts, but timestamp part must be floor(sec/60))
const NonceCls = modules[1250].Nonce;
const n = new NonceCls(0);
out.nonce_len = Buffer.from(n.base64Nonce, 'base64').length;
out.nonce_tail = Buffer.from(n.base64Nonce, 'base64').readUInt32BE(8);

console.log(JSON.stringify(out, null, 2));
