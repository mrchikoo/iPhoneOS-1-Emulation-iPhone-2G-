// Boot regression: restore the mirror's snapshot into the ORIGINAL core and the PATCHED core,
// run the same number of instructions, and compare cycles/ticks/framebuffer hash/serial.
//   node boot-test.mjs [slices=400] [mirror=../mirror]
import fs from 'node:fs';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const slices = Number(process.argv[2] || 400);
const mirror = path.resolve(process.argv[3] || path.join(here, '../mirror'));
const manifest = JSON.parse(fs.readFileSync(path.join(mirror, 'manifest.json')));
const cat = names => Buffer.concat([names].flat().map(n => fs.readFileSync(path.join(mirror, n))));
const gunzipIf = b => (b[0] === 0x1f && b[1] === 0x8b) ? zlib.gunzipSync(b) : b;
const kernel = cat(manifest.kernel), tree = cat(manifest.devicetree);
// FRESH=1: cold boot from the rootfs instead of restoring the snapshot (exercises the SDIO probe).
// ONLY=net skips the original core.  Usage e.g.  FRESH=1 ONLY=net node boot-test.mjs 60000
const FRESH = Boolean(process.env.FRESH);
const snapshot = FRESH ? null : gunzipIf(cat(manifest.snapshot));
const rootfs = FRESH ? gunzipIf(cat(manifest.rootfs)) : null;
console.log(`kernel ${kernel.length} dt ${tree.length} ` + (FRESH ? `rootfs ${rootfs.length}` : `snapshot ${snapshot.length}`));
const require = createRequire(import.meta.url);

async function boot(label, jsPath) {
  const createEmu = require(jsPath);
  const log = [];
  const M = await createEmu({locateFile: p => path.join(path.dirname(jsPath), p), printErr: t => log.push(t)});
  const put = b => { const p = M._malloc(b.length); M.HEAPU8.set(b, p); return p; };
  const call = (n, ...a) => M['_' + n](...a);
  const chk = (r, what) => { if (r < 0) throw new Error(`${label}: ${what} -> ${r}: ${M.ccall('emu_error', 'string', [], [])}`); return r; };
  const k = put(kernel), d = put(tree);
  const rp = rootfs ? put(rootfs) : 0;
  chk(call('emu_init', k, kernel.length, d, tree.length, rp, rootfs ? rootfs.length : 0, 256), 'emu_init');
  console.log(`${label}: emu_init ok`);
  if (snapshot) {
    const s = put(snapshot);
    chk(call('emu_load_snapshot', s, snapshot.length), 'load_snapshot');
    M._free(s);
  }
  chk(call('emu_set_cpi', 1), 'set_cpi');
  chk(call('emu_release_inputs'), 'release');
  console.log(`${label}: snapshot restored; running ${slices} x 50000`);
  let ran = 0;
  const allTrace = [], tp = M._malloc(16 * 4096);
  const t0 = Date.now();
  for (let i = 0; i < slices; i++) {
    const n = call('emu_run', 50000);
    if (!n) throw new Error(`${label}: guest stopped, status ${call('emu_status')}`);
    ran += n;
    if (M._emu_net_trace_pop) {            // drain every slice so nothing is overwritten
      const m = M._emu_net_trace_pop(tp, 4096), w = new Uint32Array(M.HEAPU8.buffer, tp, m * 4);
      for (let j = 0; j < m; j++) allTrace.push([w[j * 4] >>> 31, (w[j * 4] >>> 16) & 255, w[j * 4] & 0xffff, w[j * 4 + 1] >>> 0, i, w[j * 4 + 3] + 1]);
    }
  }
  const fp = M._malloc(320 * 480 * 4), wp = M._malloc(4), hp = M._malloc(4);
  const lit = call('emu_frame', fp, wp, hp);
  const h = crypto.createHash('sha256').update(M.HEAPU8.subarray(fp, fp + 320 * 480 * 4)).digest('hex');
  const r = {ran, cycles: String(call('emu_cycles')), ticks: String(call('emu_ticks')), lit, frame: h.slice(0, 16),
             status: call('emu_status'), ms: Date.now() - t0};
  if (M._emu_net_state) {
    r.net = {sdioReads: M._emu_net_state(8), sdioWrites: M._emu_net_state(9), trace: M._emu_net_state(5), tx: M._emu_net_state(1)};
    // run-length compress identical consecutive accesses (status polling)
    const lines = [];
    for (const t of allTrace) {
      const text = `${t[0] ? 'W' : 'R'}${t[1]} +0x${t[2].toString(16).padStart(2, '0')} = 0x${t[3].toString(16)}`;
      const last = lines[lines.length - 1];
      if (last && last.text === text) last.n += t[5]; else lines.push({text, n: t[5], slice: t[4]});
    }
    r.sdioDistinct = lines.length;
    r.sdioTraceHead = lines.slice(0, 40).map(l => `${l.text}${l.n > 1 ? ' x' + l.n : ''} (slice ${l.slice})`);
  }
  r.serialTail = log.slice(-3);
  if (process.env.SERIAL) { const sp = M._malloc(8192); let n; while ((n = call('emu_serial_read', sp, 8192)) > 0) process.stdout.write(Buffer.from(M.HEAPU8.slice(sp, sp + n))); }
  return r;
}
const orig = process.env.ONLY === 'net' ? null : await boot('orig', path.join(here, '../mirror/dist/emu.js'));
if (orig) console.log(orig);
const net = await boot('net ', path.join(here, 'out/dist/emu.js'));
console.log(net);
if (!orig) process.exit(0);
const same = ['ran', 'cycles', 'ticks', 'lit', 'frame', 'status'].every(k => orig[k] === net[k]);
console.log(same ? 'IDENTICAL: patched core matches original after the same instruction count' : 'DIFFERENT');
process.exit(same ? 0 : 1);
