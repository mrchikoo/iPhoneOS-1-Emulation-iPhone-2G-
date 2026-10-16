// node test.mjs [orig.wasm] [emu-net.wasm] [dist/emu.js]
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const origPath = process.argv[2] || path.join(here, '../re-core/emu.wasm');
const netPath = process.argv[3] || path.join(here, 'out/emu-net.wasm');
const jsPath = process.argv[4] || path.join(here, 'out/dist/emu.js');

// ---- minimal wasm reader: export name -> {kind, sig} ------------------
function leb(b, o) { let r = 0, s = 0, x; do { x = b[o.p++]; r |= (x & 0x7f) << s; s += 7; } while (x & 0x80); return r >>> 0; }
function exportsWithSigs(buf) {
  const b = new Uint8Array(buf), o = {p: 8};
  const types = [], funcTypes = [], exps = [];
  let importedFuncs = 0;
  while (o.p < b.length) {
    const id = b[o.p++], size = leb(b, o), end = o.p + size;
    if (id === 1) {
      const n = leb(b, o);
      for (let i = 0; i < n; i++) {
        assert.equal(b[o.p++], 0x60);
        const pc = leb(b, o), ps = [...b.subarray(o.p, o.p + pc)]; o.p += pc;
        const rc = leb(b, o), rs = [...b.subarray(o.p, o.p + rc)]; o.p += rc;
        types.push(`(${ps.join(',')})->(${rs.join(',')})`);
      }
    } else if (id === 2) {
      const n = leb(b, o);
      for (let i = 0; i < n; i++) {
        { let l = leb(b, o); o.p += l; l = leb(b, o); o.p += l; }
        const k = b[o.p++];
        if (k === 0) { funcTypes.push(types[leb(b, o)]); importedFuncs++; }
        else if (k === 1) { o.p++; const f = leb(b, o); leb(b, o); if (f & 1) leb(b, o); }
        else if (k === 2) { const f = leb(b, o); leb(b, o); if (f & 1) leb(b, o); }
        else { o.p += 2; }
      }
    } else if (id === 3) {
      const n = leb(b, o);
      for (let i = 0; i < n; i++) funcTypes.push(types[leb(b, o)]);
    } else if (id === 7) {
      const n = leb(b, o);
      for (let i = 0; i < n; i++) {
        const l = leb(b, o), name = Buffer.from(b.subarray(o.p, o.p + l)).toString(); o.p += l;
        const kind = b[o.p++], idx = leb(b, o);
        exps.push([name, kind, kind === 0 ? funcTypes[idx] : '']);
      }
    }
    o.p = end;
  }
  return new Map(exps.map(([n, k, s]) => [n, `${k}:${s}`]));
}
const origBytes = fs.readFileSync(origPath), netBytes = fs.readFileSync(netPath);
const eo = exportsWithSigs(origBytes), en = exportsWithSigs(netBytes);
for (const [name, sig] of eo) assert.equal(en.get(name), sig, `export ${name} changed or missing`);
console.log(`ok: all ${eo.size} original exports present with identical kind/signature`);
for (const n of ['emu_net_tx_pop', 'emu_net_rx_push', 'emu_net_state'])
  assert.ok(en.has(n), n + ' missing');
assert.equal(en.get('emu_net_tx_pop'), '0:(127,127)->(127)');
assert.equal(en.get('emu_net_rx_push'), '0:(127,127)->(127)');
assert.equal(en.get('emu_net_state'), '0:(127)->(127)');
console.log(`ok: ${en.size - eo.size} new exports`);
assert.ok(WebAssembly.validate(netBytes));

// ---- instantiate through the (patched) Emscripten loader --------------
const require = createRequire(import.meta.url);
const createEmu = require(jsPath);
const M = await createEmu({locateFile: p => path.join(path.dirname(jsPath), p), printErr() {}});
const put = (u8) => { const p = M._malloc(u8.length); M.HEAPU8.set(u8, p); return p; };
const frame = (n, seed) => Uint8Array.from({length: n}, (_, i) => (i * 7 + seed) & 255);
const get = (p, n) => M.HEAPU8.slice(p, p + n);
const out = M._malloc(4096);
const S = i => M._emu_net_state(i);

assert.equal(S(10), 1);
assert.equal(M._emu_net_tx_pop(out, 4096), 0, 'empty ring pops 0');
assert.equal(S(1), 0); assert.equal(S(2), 0);
const base = S(0);
assert.ok(base > 0 && S(0) === base, 'ring block allocated once, outside the machine struct');
assert.ok(base < 23560 || base >= 23560 + 135000, 'block not inside machine struct');   // struct end > 126264+... (heap alloc)
assert.equal(new DataView(M.HEAPU8.buffer).getUint32(base, true), 0x3154454e);

// TX: device side pushes, host pops (what drainNet does)
const sizes = [60, 1514, 1, 2044, 100];
sizes.forEach((n, i) => assert.equal(M._emu_net_tx_push(put(frame(n, i)), n), 1));
assert.equal(S(1), sizes.length);
assert.equal(M._emu_net_tx_pop(out, 10), -1, 'too-small buffer reports -1');
assert.equal(S(1), sizes.length, 'frame retained after -1');
sizes.forEach((n, i) => {
  assert.equal(M._emu_net_tx_pop(out, 4096), n);
  assert.deepEqual(get(out, n), frame(n, i));
});
assert.equal(M._emu_net_tx_pop(out, 4096), 0);
assert.equal(M._emu_net_tx_push(put(frame(10, 0)), 0), 0, 'zero length rejected');
assert.equal(M._emu_net_tx_push(put(frame(2045, 0)), 2045), 0, 'oversize rejected');

// RX: host pushes (net-rx), device pops
for (let i = 0; i < 128; i++) assert.equal(M._emu_net_rx_push(put(frame(64 + i, i)), 64 + i), 1);
assert.equal(S(2), 128);
assert.equal(M._emu_net_rx_push(put(frame(64, 0)), 64), 0, 'full ring refuses');
assert.equal(S(4), 1, 'rx drop counted');
for (let i = 0; i < 128; i++) {
  assert.equal(M._emu_net_rx_pop(out, 4096), 64 + i);
  assert.deepEqual(get(out, 64 + i), frame(64 + i, i));
}
assert.equal(M._emu_net_rx_pop(out, 4096), 0);
// wraparound: 300 frames through a 128-slot ring
for (let i = 0; i < 300; i++) {
  assert.equal(M._emu_net_rx_push(put(frame(90, i)), 90), 1);
  assert.equal(M._emu_net_rx_pop(out, 4096), 90);
  assert.deepEqual(get(out, 90), frame(90, i));
}
// rings are independent
M._emu_net_tx_push(put(frame(5, 1)), 5);
assert.equal(S(2), 0); assert.equal(S(1), 1);
assert.equal(M._emu_net_tx_pop(out, 4096), 5);
console.log('ok: TX/RX rings round-trip, order, full, oversize, wraparound');

// ---- SDIO register file + trace via the same path the guest uses -------
assert.equal(S(7), 0, 'default mode is log-only');
M._emu_net_sdio_write(0x0c, 4, 0xdeadbeef);
assert.equal(M._emu_net_sdio_read(0x0c, 4), 0, 'mode 0 reads 0 like unpatched core');
assert.equal(M._emu_net_sdio_mode(1), 0);
assert.equal(M._emu_net_sdio_read(0x0c, 4) >>> 0, 0xdeadbeef);
assert.equal(M._emu_net_sdio_read(0x0d, 1), 0xbe);
assert.equal(M._emu_net_sdio_read(0x0e, 2), 0xdead);
M._emu_net_sdio_write(0x0d, 1, 0x11);
assert.equal(M._emu_net_sdio_read(0x0c, 4) >>> 0, 0xdead11ef);
assert.equal(M._emu_net_sdio_read(0xffe, 4) >>> 0, 0, 'reads past 4 KiB are clipped');
M._emu_net_sdio_mode(0);
const tr = M._malloc(16 * 64);
const n = M._emu_net_trace_pop(tr, 64);
assert.equal(n, 8 + 0 * 0, 'trace entries');
const w = new Uint32Array(M.HEAPU8.buffer, tr, n * 4);
assert.equal(w[0] >>> 31, 1); assert.equal((w[0] >>> 16) & 255, 4); assert.equal(w[0] & 0xffff, 0x0c); assert.equal(w[1] >>> 0, 0xdeadbeef);
assert.equal(w[4] >>> 31, 0); assert.equal(w[5], 0);
assert.equal(w[3 * 4 + 2], 4, 'seq increments');
assert.equal(M._emu_net_trace_pop(tr, 64), 0);
// identical consecutive accesses (polling) merge into one entry with a repeat count
for (let i = 0; i < 1000; i++) M._emu_net_sdio_read(0x18, 4);
assert.equal(S(5), 1, 'polling merged');
assert.equal(M._emu_net_trace_pop(tr, 64), 1);
assert.equal(new Uint32Array(M.HEAPU8.buffer, tr, 4)[3], 999, 'repeat count');
for (let i = 0; i < 1500; i++) M._emu_net_sdio_write(0x10, 4, i);
assert.equal(S(5), 1024, 'trace ring keeps newest 1024');
console.log('ok: SDIO register file / trace');
console.log('ALL PASS');
