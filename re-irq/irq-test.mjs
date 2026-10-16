// IRQ 42 (SDIO, VIC1 bit 10) delivery test for out/emu-irq.wasm (built by irq_patch.py).
// Cold boot from rootfs (re-trace/run.mjs approach), trace VIC enable changes, then raise IRQ 42.
//   node --max-old-space-size=6000 irq-test.mjs [--wasm out/emu-irq.wasm] [--boot 1500000000]
// Exit code 0 = all checks passed.  Writes out/irq-test.json and out/irq-serial.txt.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..');
const MIRROR = path.join(ROOT, 'mirror');
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const WASM = path.resolve(here, opt('--wasm', 'out/emu-irq.wasm'));
const BOOT = Number(opt('--boot', '1500000000'));
const SLICE = Number(opt('--slice', '200000'));
const OUT = path.join(here, 'out');
const require = createRequire(import.meta.url);
const createEmu = require(path.join(MIRROR, 'dist/emu.js'));

// machine struct (static, 23560) offsets
const M = 23560, VIC0 = M + 84200, VIC1 = M + 84216;            // +0 RAW +4 EN +8 SEL +12 SOFT
const IRQLINE = M + 272, PC = 23620, CPSR = 23628, LR_IRQ = 23616, SPSR_IRQ = 23704;
const OFF_TRACE_PAGE = 119408;
const LINE = 42, BIT = 1 << (LINE - 32);

const results = { wasm: WASM, checks: [], vicEnableLog: [], notes: [] };
const check = (name, ok, detail) => { results.checks.push({ name, ok: !!ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail !== undefined ? ' :: ' + JSON.stringify(detail) : ''}`); };

let memRef = null, X = null;
const mmio = [];
const onText = t => { if (t.startsWith('[mmio]')) { if (mmio.length < 200000) mmio.push(t); } };
const envStrings = ['USER=web_user', 'PATH=/', 'HOME=/home/web_user', 'LANG=C.UTF-8'];
const module = await createEmu({
  locateFile: p => path.join(MIRROR, 'dist', p), print: onText, printErr: onText,
  instantiateWasm(info, receive) {
    info.a.e = (envp, buf) => { const u8 = new Uint8Array(memRef.buffer), dv = new DataView(memRef.buffer);
      let off = 0; envStrings.forEach((s, i) => { const b = Buffer.from(s + '\0'); u8.set(b, buf + off); dv.setUint32(envp + 4 * i, buf + off, true); off += b.length; }); return 0; };
    info.a.f = (pc, ps) => { const dv = new DataView(memRef.buffer); dv.setUint32(pc, envStrings.length, true);
      dv.setUint32(ps, envStrings.reduce((n, s) => n + Buffer.byteLength(s) + 1, 0), true); return 0; };
    WebAssembly.instantiate(fs.readFileSync(WASM), info).then(r => { memRef = r.instance.exports.i; X = r.instance.exports; receive(r.instance, r.module); });
    return {};
  },
});
const U32 = () => module.HEAPU32, U8 = () => module.HEAPU8;
const r32 = a => U32()[a >>> 2] >>> 0, w32 = (a, v) => { U32()[a >>> 2] = v >>> 0; };
const hex = v => '0x' + (v >>> 0).toString(16);
const err = () => module.ccall('emu_error', 'string', [], []);
const ok = r => { if (r < 0) throw new Error(err() || `error ${r}`); return r; };
const alloc = d => { const p = module._malloc(d.byteLength); U8().set(d, p); return p; };

check('exports present', ['sdio_raise_irq', 'sdio_lower_irq', 'vic_irq_reeval', 'sdio_irq_state'].every(n => typeof X[n] === 'function'));
check('state version', X.sdio_irq_state(9) === 0x49525131, hex(X.sdio_irq_state(9)));

const kernel = fs.readFileSync(path.join(MIRROR, 'fw/kernel_1A543a.macho'));
const tree = fs.readFileSync(path.join(MIRROR, 'fw/devicetree_m68ap.bin'));
const rootfs = fs.readFileSync(path.join(ROOT, 're-trace/rootfs.img'));
const k = alloc(kernel), d = alloc(tree), rf = alloc(rootfs);
ok(module.ccall('emu_init', 'number', Array(7).fill('number'), [k, kernel.length, d, tree.length, rf, rootfs.length, 256]));
module._free(k); module._free(d); module._free(rf);
ok(module._emu_set_cpi(1)); ok(module._emu_release_inputs()); ok(module._emu_host_clock(0));
const serialPtr = module._malloc(8192); let serial = '';
const drain = () => { for (;;) { const n = module._emu_serial_read(serialPtr, 8192); if (!n) break; serial += Buffer.from(U8().subarray(serialPtr, serialPtr + n)).toString('latin1'); } };

// ---- phase A: masked raise/lower right after init (guest has not run) -----------------
X.sdio_raise_irq();
check('A raise sets VIC1 RAW bit 10', (r32(VIC1) & BIT) !== 0 && X.sdio_irq_state(0) === 1, hex(r32(VIC1)));
check('A masked line does not reach CPU', U8()[IRQLINE] === 0 && r32(VIC1 + 4) === 0, { en1: hex(r32(VIC1 + 4)), line: U8()[IRQLINE] });
X.sdio_lower_irq();
check('A lower clears RAW bit 10', (r32(VIC1) & BIT) === 0 && X.sdio_irq_state(0) === 0, hex(r32(VIC1)));

// ---- phase B: cold boot, log every VIC enable/select change, RAW bit 10 must stay 0 -----
let done = 0, last = { en0: r32(VIC0 + 4), en1: r32(VIC1 + 4), sel0: r32(VIC0 + 8), sel1: r32(VIC1 + 8) }, raw10 = 0;
const t0 = Date.now();
while (done < BOOT) {
  const ran = module._emu_run(SLICE); if (!ran) throw new Error('emu_run stopped: ' + err()); done += ran;
  const now = { en0: r32(VIC0 + 4), en1: r32(VIC1 + 4), sel0: r32(VIC0 + 8), sel1: r32(VIC1 + 8) };
  for (const key of Object.keys(now)) if (now[key] !== last[key])
    results.vicEnableLog.push({ insns: done, reg: key, from: hex(last[key]), to: hex(now[key]), pc: hex(r32(PC)) });
  last = now;
  if (r32(VIC1) & BIT) raw10++;
  if (done % 100_000_000 < SLICE) { drain(); console.log(`boot insns=${done} ticks=${module._emu_ticks()} en0=${hex(now.en0)} en1=${hex(now.en1)} wall=${((Date.now() - t0) / 1000).toFixed(1)}s`); }
}
drain();
const enabled42 = results.vicEnableLog.some(e => e.reg === 'en1' && (parseInt(e.to, 16) & BIT));
results.guestEnabledLine42 = enabled42;
results.finalVic = { raw0: hex(r32(VIC0)), en0: hex(r32(VIC0 + 4)), sel0: hex(r32(VIC0 + 8)), raw1: hex(r32(VIC1)), en1: hex(r32(VIC1 + 4)), sel1: hex(r32(VIC1 + 8)) };
check('B SDIO probe happened during boot', /AppleS5L8900XSDIO/.test(serial), (serial.match(/.*SDIO.*/g) || []).slice(0, 4));
check('B VIC1 RAW bit 10 never set while level=0 (nothing else drives line 42)', raw10 === 0, raw10);
check('B guest installed handlers (VIC0/VIC1 enables nonzero)', r32(VIC0 + 4) !== 0, results.finalVic);
console.log(`guest enabled line 42 during boot: ${enabled42}`);

// ---- phase C1: raise while masked -> no IRQ attributable to line 42 -------------------
U32()[(M + OFF_TRACE_PAGE) >>> 2] = 0x38E00000;               // [mmio] trace of VIC0 page reads (VECTADDR)
const vect42 = () => mmio.filter(l => / R 0x38e00f00 = 0x8000002a /.test(l)).length;
mmio.length = 0;
X.sdio_raise_irq();
for (let i = 0; i < 50; i++) module._emu_run(SLICE);
const c1 = vect42();
const en1now = r32(VIC1 + 4);
if (!(en1now & BIT)) check('C1 masked: guest never sees vector 42', c1 === 0, { vect42: c1, vic0Reads: mmio.length });
else results.notes.push('line 42 already enabled by guest; C1 is not a masked test');
X.sdio_lower_irq();

// ---- phase C2: unmask line 42 (what the guest's INTENABLE write would do) and raise ------
const forced = !(r32(VIC1 + 4) & BIT);
if (forced) w32(VIC1 + 4, r32(VIC1 + 4) | BIT);               // == guest write of BIT to VIC1+0x10 (func 144: EN |= val)
mmio.length = 0;
results.lineBeforeRaise = U8()[IRQLINE];
X.sdio_raise_irq();
check('C2 raise drives CPU IRQ line immediately (vic_irq_reeval)', U8()[IRQLINE] === 1, { line: U8()[IRQLINE], vic1: { raw: hex(r32(VIC1)), en: hex(r32(VIC1 + 4)), sel: hex(r32(VIC1 + 8)) } });
// single-step until the CPU takes the IRQ exception (CPSR.I may be set for a while)
let taken = null;
for (let i = 0; i < 2_000_000 && !taken; i++) {
  const before = r32(PC), cpsrB = r32(CPSR);
  module._emu_run(1);
  const pc = r32(PC), cpsr = r32(CPSR);
  if ((cpsr & 31) === 0x12 && (pc === 0x18 || pc === 0xffff0018) && (cpsrB & 31) !== 0x12)
    taken = { step: i, fromPc: hex(before), fromCpsr: hex(cpsrB), vector: hex(pc), cpsr: hex(cpsr), lr_irq: hex(r32(LR_IRQ)), spsr_irq: hex(r32(SPSR_IRQ)) };
}
check('C2 CPU took the IRQ exception (mode 0x12 at vector)', taken, taken);
// let the kernel handler run; it reads VECTADDR from VIC0 (+0xF00)
for (let i = 0; i < 20 && vect42() === 0; i++) module._emu_run(10000);
const c2 = vect42();
const firstVect = mmio.find(l => / R 0x38e00f00 = /.test(l));
check('C2 guest IRQ handler read VECTADDR = 0x8000002a (line 42)', c2 > 0 && / = 0x8000002a /.test(firstVect), { vect42: c2, lineBeforeRaise: results.lineBeforeRaise, firstVectAddrRead: firstVect });
// what does the kernel do with an IRQ nobody claimed?  run a bit with the level held
const serialBefore = serial.length; drain();
for (let i = 0; i < 25; i++) module._emu_run(SLICE);
drain();
results.c2 = { vect42Reads: vect42(), en1After: hex(r32(VIC1 + 4)), guestMaskedIt: !(r32(VIC1 + 4) & BIT), serialDuring: serial.slice(serialBefore).slice(0, 2000) };
console.log('C2 kernel reaction:', JSON.stringify(results.c2).slice(0, 600));

// ---- phase D: lower, restore mask, keep running: guest must stay alive ------------------
X.sdio_lower_irq();
check('D lower drops CPU IRQ line contribution', (r32(VIC1) & BIT) === 0, hex(r32(VIC1)));
if (forced) w32(VIC1 + 4, r32(VIC1 + 4) & ~BIT);
U32()[(M + OFF_TRACE_PAGE) >>> 2] = 0;
const ticks0 = module._emu_ticks(), pcs = new Set();
let dRan = 0; for (let i = 0; i < 500; i++) { const n = module._emu_run(SLICE); dRan += n; pcs.add(r32(PC) >>> 12); if (!n) break; }
drain();
check('D guest keeps running after lower (ticks advance, many PCs)', dRan === 500 * SLICE && module._emu_ticks() > ticks0 && pcs.size > 3, { ran: dRan, pages: pcs.size, status: module._emu_status() });
check('D no kernel panic in serial', !/panic/i.test(serial.slice(serialBefore)), serial.slice(serialBefore).match(/.*panic.*/i)?.[0]);

fs.mkdirSync(OUT, { recursive: true });
results.lineReraisedByFunc19 = null;
// ---- phase E: func 19 hook re-drives RAW from the level (stale-bit protection) ------------
X.sdio_raise_irq(); w32(VIC1, r32(VIC1) & ~BIT); module._emu_run(1000);
const e1 = (r32(VIC1) & BIT) !== 0;
X.sdio_lower_irq(); w32(VIC1, r32(VIC1) | BIT); module._emu_run(1000);
const e2 = (r32(VIC1) & BIT) === 0;
check('E func 19 re-drives RAW bit 10 from the level each tick', e1 && e2, { reassertedHigh: e1, reassertedLow: e2 });

fs.writeFileSync(path.join(OUT, 'irq-serial.txt'), serial);
results.insns = done; results.wallSeconds = (Date.now() - t0) / 1000;
fs.writeFileSync(path.join(OUT, 'irq-test.json'), JSON.stringify(results, null, 2));
const failed = results.checks.filter(c => !c.ok).length;
console.log(`${results.checks.length - failed}/${results.checks.length} checks passed`);
process.exit(failed ? 1 : 0);
