// Level 0 must be behaviour-neutral: original emu.wasm vs out/emu-irq.wasm, cold boot, N instructions.
//   node --max-old-space-size=8000 equiv-test.mjs [N=400000000]
import { createRequire } from 'node:module';
import fs from 'node:fs'; import path from 'node:path'; import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(here, '..'), MIRROR = path.join(ROOT, 'mirror');
const N = Number(process.argv[2] || 400000000);
const require = createRequire(import.meta.url), createEmu = require(path.join(MIRROR, 'dist/emu.js'));
async function run(wasm) {
  let memRef; const env = ['USER=web_user', 'PATH=/', 'HOME=/home/web_user', 'LANG=C.UTF-8'];
  const m = await createEmu({ locateFile: p => path.join(MIRROR, 'dist', p), print() {}, printErr() {},
    instantiateWasm(info, receive) {
      info.a.e = (envp, buf) => { const u8 = new Uint8Array(memRef.buffer), dv = new DataView(memRef.buffer); let off = 0;
        env.forEach((s, i) => { const b = Buffer.from(s + '\0'); u8.set(b, buf + off); dv.setUint32(envp + 4 * i, buf + off, true); off += b.length; }); return 0; };
      info.a.f = (pc, ps) => { const dv = new DataView(memRef.buffer); dv.setUint32(pc, env.length, true); dv.setUint32(ps, env.reduce((n, s) => n + s.length + 1, 0), true); return 0; };
      WebAssembly.instantiate(fs.readFileSync(wasm), info).then(r => { memRef = r.instance.exports.i; receive(r.instance, r.module); }); return {}; } });
  const alloc = d => { const p = m._malloc(d.byteLength); m.HEAPU8.set(d, p); return p; };
  const f = n => fs.readFileSync(n), K = f(path.join(MIRROR, 'fw/kernel_1A543a.macho')), D = f(path.join(MIRROR, 'fw/devicetree_m68ap.bin')), R = f(path.join(ROOT, 're-trace/rootfs.img'));
  if (m.ccall('emu_init', 'number', Array(7).fill('number'), [alloc(K), K.length, alloc(D), D.length, alloc(R), R.length, 256]) < 0) throw new Error('init');
  m._emu_set_cpi(1); m._emu_release_inputs(); m._emu_host_clock(0);
  const sp = m._malloc(8192); let serial = '', done = 0;
  while (done < N) { done += m._emu_run(1_000_000); for (;;) { const n = m._emu_serial_read(sp, 8192); if (!n) break; serial += Buffer.from(m.HEAPU8.subarray(sp, sp + n)).toString('latin1'); } }
  const fb = m._malloc(320 * 480 * 4), w = m._malloc(4), h = m._malloc(4); m._emu_frame(fb, w, h);
  const sha = b => crypto.createHash('sha256').update(b).digest('hex').slice(0, 16);
  return { done, ticks: String(m._emu_ticks()), cycles: String(m._emu_cycles()), serial: sha(serial), serialLen: serial.length,
    fb: sha(m.HEAPU8.subarray(fb, fb + 320 * 480 * 4)), cpu: sha(m.HEAPU8.subarray(23560, 23560 + 512)), vic: sha(m.HEAPU8.subarray(107760, 107792)) };
}
const a = await run(path.join(ROOT, 're-core/emu.wasm')), b = await run(path.join(here, 'out/emu-irq.wasm'));
console.log('orig   ', JSON.stringify(a)); console.log('patched', JSON.stringify(b));
const same = JSON.stringify(a) === JSON.stringify(b); console.log(same ? 'PASS identical' : 'FAIL differs'); process.exit(same ? 0 : 1);
