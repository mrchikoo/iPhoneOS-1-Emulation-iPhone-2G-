// Find the exact guest instruction that sets VIC1 INTENABLE bit 10 (line 42) during a cold boot.
//   node --max-old-space-size=6000 vic-trace.mjs [--wasm out/emu-irq.wasm] [--max 400000000]
// Slices of 100k instructions until the bit appears, then replays from scratch to that slice
// and single-steps it.  Prints PC/LR/CPSR of the writing instruction and the serial tail.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..'), MIRROR = path.join(ROOT, 'mirror');
const args = process.argv.slice(2), opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const WASM = path.resolve(here, opt('--wasm', 'out/emu-irq.wasm')), MAX = Number(opt('--max', '400000000'));
const require = createRequire(import.meta.url), createEmu = require(path.join(MIRROR, 'dist/emu.js'));
const M = 23560, VIC1EN = M + 84220, PC = 23620, CPSR = 23628, BIT = 1 << 10;
const hex = v => '0x' + (v >>> 0).toString(16);
async function boot() {
  let memRef; const env = ['USER=web_user', 'PATH=/', 'HOME=/home/web_user', 'LANG=C.UTF-8'];
  const m = await createEmu({ locateFile: p => path.join(MIRROR, 'dist', p), print() {}, printErr() {},
    instantiateWasm(info, receive) {
      info.a.e = (envp, buf) => { const u8 = new Uint8Array(memRef.buffer), dv = new DataView(memRef.buffer); let off = 0;
        env.forEach((s, i) => { const b = Buffer.from(s + '\0'); u8.set(b, buf + off); dv.setUint32(envp + 4 * i, buf + off, true); off += b.length; }); return 0; };
      info.a.f = (pc, ps) => { const dv = new DataView(memRef.buffer); dv.setUint32(pc, env.length, true); dv.setUint32(ps, env.reduce((n, s) => n + s.length + 1, 0), true); return 0; };
      WebAssembly.instantiate(fs.readFileSync(WASM), info).then(r => { memRef = r.instance.exports.i; receive(r.instance, r.module); }); return {}; } });
  const alloc = d => { const p = m._malloc(d.byteLength); m.HEAPU8.set(d, p); return p; };
  const f = n => fs.readFileSync(n), K = f(path.join(MIRROR, 'fw/kernel_1A543a.macho')), D = f(path.join(MIRROR, 'fw/devicetree_m68ap.bin')), R = f(path.join(ROOT, 're-trace/rootfs.img'));
  if (m.ccall('emu_init', 'number', Array(7).fill('number'), [alloc(K), K.length, alloc(D), D.length, alloc(R), R.length, 256]) < 0) throw new Error('init');
  m._emu_set_cpi(1); m._emu_release_inputs(); m._emu_host_clock(0);
  const sp = m._malloc(8192); m.serial = '';
  m.drain = () => { for (;;) { const n = m._emu_serial_read(sp, 8192); if (!n) break; m.serial += Buffer.from(m.HEAPU8.subarray(sp, sp + n)).toString('latin1'); } };
  m.r32 = a => m.HEAPU32[a >>> 2] >>> 0;
  return m;
}
const SL = 100000;
let m = await boot(), done = 0, slices = 0;
while (done < MAX && !(m.r32(VIC1EN) & BIT)) { done += m._emu_run(SL); slices++; }
if (!(m.r32(VIC1EN) & BIT)) { console.log(JSON.stringify({ enabled: false, insns: done })); process.exit(0); }
const target = done;
m = await boot(); done = 0;
for (let i = 0; i < slices - 1; i++) done += m._emu_run(SL);
let hit = null, steps = 0;
while (!hit && steps < 4 * SL) {
  const pc = m.r32(PC), cpsr = m.r32(CPSR), en = m.r32(VIC1EN);
  m._emu_run(1); steps++;
  if (!(en & BIT) && (m.r32(VIC1EN) & BIT)) hit = { insnsApprox: done + steps, pc: hex(pc), cpsr: hex(cpsr), en1Before: hex(en), en1After: hex(m.r32(VIC1EN)), lr_svc: hex(m.r32(23616)) };
}
m.drain();
const lines = m.serial.trimEnd().split('\n');
console.log(JSON.stringify({ enabled: true, firstSeenAfterSlice: target, hit, serialLinesSoFar: lines.length, serialTail: lines.slice(-6) }, null, 2));
