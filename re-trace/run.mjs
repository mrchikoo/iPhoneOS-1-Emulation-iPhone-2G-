// Headless cold-boot harness for the ORIGINAL 2007.mobile emu.wasm (unmodified emu.js + emu.wasm).
// Usage: node run.mjs [--insns N] [--trace 0x38D00000] [--hostclock 0|1] [--env K=V ...] [--out DIR]
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const MIRROR = path.resolve(here, '../mirror');
const require = createRequire(import.meta.url);
const createEmu = require(path.join(MIRROR, 'dist/emu.js'));

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const TOTAL = Number(opt('--insns', '2000000000'));
const TRACE_PAGE = Number(opt('--trace', '0x38D00000')) >>> 0;
const HOSTCLOCK = Number(opt('--hostclock', '0'));
const OUT = path.resolve(here, opt('--out', 'out'));
const RING = !args.includes('--no-ring');
const envPairs = []; args.forEach((a, i) => { if (a === '--env') envPairs.push(args[i + 1]); });
fs.mkdirSync(OUT, { recursive: true });

const MACHINE = 23560;              // static machine struct (verified: 387 `i32.const 23560` in emu.wat)
const OFF_TRACE_PAGE = 119408;      // func 85/86/17: if nonzero and (addr & ~0xfff)==value -> fprintf "[mmio] ..."
const OFF_RING_EN = 119404;         // u8: ==1 enables the access ring
const OFF_RING_ADDR = 119412, OFF_RING_VAL = 120436, OFF_RING_FLAG = 121460, OFF_RING_N = 121716;
const OFF_UNMAPPED_N = 119400, OFF_UNMAPPED_PAGES = 119272; // func 129: up to 32 distinct unmapped pages
const OFF_UNMAPPED_READS = 119256;  // u64 counter incremented on unmapped reads (func 85)

const logLines = [];
const mmio = fs.createWriteStream(path.join(OUT, 'mmio.log'));
const stderrLog = fs.createWriteStream(path.join(OUT, 'emu-stderr.log'));
let mmioCount = 0;
const onText = text => {
  if (text.startsWith('[mmio]')) { mmioCount++; mmio.write(text + '\n'); }
  else stderrLog.write(text + '\n');
};

let memRef = null;
const envStrings = ['USER=web_user', 'PATH=/', 'HOME=/home/web_user', 'LANG=C.UTF-8', ...envPairs];
const module = await createEmu({
  locateFile: p => path.join(MIRROR, 'dist', p),
  print: onText, printErr: onText,
  // Replace the environ imports so S5LBOX_* env vars can be passed (emu.js keeps ENV private).
  instantiateWasm(info, receive) {
    info.a.e = (envp, buf) => { const u8 = new Uint8Array(memRef.buffer), dv = new DataView(memRef.buffer);
      let off = 0; envStrings.forEach((s, i) => { const b = Buffer.from(s + '\0'); u8.set(b, buf + off); dv.setUint32(envp + 4 * i, buf + off, true); off += b.length; }); return 0; };
    info.a.f = (pc, ps) => { const dv = new DataView(memRef.buffer); dv.setUint32(pc, envStrings.length, true);
      dv.setUint32(ps, envStrings.reduce((n, s) => n + Buffer.byteLength(s) + 1, 0), true); return 0; };
    const bin = fs.readFileSync(path.join(MIRROR, 'dist/emu.wasm'));
    WebAssembly.instantiate(bin, info).then(r => { memRef = r.instance.exports.i; receive(r.instance, r.module); });
    return {};
  },
});
const H8 = () => module.HEAPU8, H32 = () => module.HEAPU32;
const err = () => module.ccall('emu_error', 'string', [], []);
const check = r => { if (r < 0) throw new Error(err() || `error ${r}`); return r; };
const alloc = d => { const p = module._malloc(d.byteLength); if (!p) throw new Error('oom'); H8().set(d, p); return p; };

const kernel = fs.readFileSync(path.join(MIRROR, 'fw/kernel_1A543a.macho'));
const tree = fs.readFileSync(path.join(MIRROR, 'fw/devicetree_m68ap.bin'));
const rootfs = fs.readFileSync(path.join(here, 'rootfs.img')); // gunzip of part0+part1
const k = alloc(kernel), d = alloc(tree), r = alloc(rootfs);
check(module.ccall('emu_init', 'number', Array(7).fill('number'), [k, kernel.length, d, tree.length, r, rootfs.length, 256]));
module._free(k); module._free(d); module._free(r);
check(module.ccall('emu_set_cpi', 'number', ['number'], [1]));
const api = Object.fromEntries([
  ['hostClock','emu_host_clock'], ['clockDelay','emu_clock_delay'], ['run','emu_run'], ['status','emu_status'],
  ['frame','emu_frame'], ['ticks','emu_ticks'], ['cycles','emu_cycles'], ['serial','emu_serial_read'], ['release','emu_release_inputs'],
].map(([a, n]) => [a, module['_' + n]]));
check(api.release());
check(api.hostClock(HOSTCLOCK));

// Enable tracing AFTER emu_init (init may clear the struct).
if (TRACE_PAGE) H32()[(MACHINE + OFF_TRACE_PAGE) >>> 2] = TRACE_PAGE;
if (RING) { H8()[MACHINE + OFF_RING_EN] = 1; H32()[(MACHINE + OFF_RING_N) >>> 2] = 0; }

const serialPtr = module._malloc(8192), fb = module._malloc(320 * 480 * 4), wp = module._malloc(4), hp = module._malloc(4);
let serial = '';
const drain = () => { for (;;) { const n = api.serial(serialPtr, 8192); if (!n) break;
  serial += Buffer.from(H8().subarray(serialPtr, serialPtr + n)).toString('latin1'); } };
const savePng = file => {
  const lit = check(api.frame(fb, wp, hp)); const w = 320, h = 480;
  const px = H8().subarray(fb, fb + w * h * 4); const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 4 + 1)] = 0; for (let x = 0; x < w; x++) { const s = (y * w + x) * 4, o = y * (w * 4 + 1) + 1 + x * 4;
    // HEAPU32 pixels are 0xAARRGGBB little-endian -> bytes B,G,R,A
    raw[o] = px[s + 2]; raw[o + 1] = px[s + 1]; raw[o + 2] = px[s]; raw[o + 3] = 255; } }
  const crc = b => { let c, t = []; for (let n = 0; n < 256; n++) { c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    c = 0xffffffff; for (const x of b) c = t[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => { const l = Buffer.alloc(4); l.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]);
    const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6;
  fs.writeFileSync(file, Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]));
  return lit;
};

const t0 = Date.now(); let done = 0, lastReport = 0, shot = 0, stopped = null;
const progress = fs.createWriteStream(path.join(OUT, 'progress.log'));
while (done < TOTAL) {
  if (HOSTCLOCK && api.clockDelay() > 0) await new Promise(res => setTimeout(res, Math.ceil(api.clockDelay())));
  const ran = api.run(1_000_000);
  if (!ran) { stopped = `status ${api.status()} err=${err()}`; break; }
  done += ran;
  if (done - lastReport >= 100_000_000) {
    lastReport = done; drain();
    const lit = savePng(path.join(OUT, `frame_${String(shot++).padStart(3, '0')}.png`));
    const line = `insns=${done} ticks=${api.ticks()} guest_s=${(api.ticks() / 6e6).toFixed(2)} wall_s=${((Date.now() - t0) / 1000).toFixed(1)} lit=${lit} mmio_lines=${mmioCount} serial_bytes=${serial.length}`;
    progress.write(line + '\n'); console.log(line);
    fs.writeFileSync(path.join(OUT, 'serial.txt'), serial);
  }
}
drain();
fs.writeFileSync(path.join(OUT, 'serial.txt'), serial);
savePng(path.join(OUT, 'final.png'));

// Dump access ring and unmapped-page list.
const m = MACHINE, U32 = H32(), U8 = H8();
const n = U32[(m + OFF_RING_N) >>> 2];
const ring = [];
for (let i = 0; i < Math.min(n, 256); i++) ring.push(`${i}\t${U8[m + OFF_RING_FLAG + i] ? 'W' : 'R'}\t0x${(U32[(m + OFF_RING_ADDR + 4 * i) >>> 2] >>> 0).toString(16).padStart(8, '0')}\t0x${(U32[(m + OFF_RING_VAL + 4 * i) >>> 2] >>> 0).toString(16).padStart(8, '0')}`);
fs.writeFileSync(path.join(OUT, 'ring.tsv'), ring.join('\n') + '\n');
const un = U32[(m + OFF_UNMAPPED_N) >>> 2];
const pages = []; for (let i = 0; i < Math.min(un, 32); i++) pages.push('0x' + (U32[(m + OFF_UNMAPPED_PAGES + 4 * i) >>> 2] >>> 0).toString(16));
const dv = new DataView(U8.buffer);
const summary = { insns: done, ticks: api.ticks(), stopped, status: api.status(), mmioLines: mmioCount, ringCount: n,
  unmappedReads: dv.getBigUint64(m + OFF_UNMAPPED_READS, true).toString(), unmappedPages: pages, wallSeconds: (Date.now() - t0) / 1000 };
fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
mmio.end(); stderrLog.end(); progress.end();
