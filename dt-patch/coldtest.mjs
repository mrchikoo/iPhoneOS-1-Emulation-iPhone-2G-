// Usage: node coldtest.mjs <devicetree.bin> <outfile> [insns]  -- cold boot via ../mirror/dist/emu.js, dump serial
import { createRequire } from 'node:module'; import fs from 'node:fs'; import path from 'node:path'; import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url)), M = path.resolve(here, '../mirror');
const createEmu = createRequire(import.meta.url)(path.join(M, 'dist/emu.js'));
const [dtPath, outPath, n = '300000000'] = process.argv.slice(2);
const log = [];
const mod = await createEmu({ locateFile: p => path.join(M, 'dist', p), print: t => log.push(t), printErr: t => log.push(t) });
const alloc = b => { const p = mod._malloc(b.length); mod.HEAPU8.set(b, p); return p; };
const kernel = fs.readFileSync(path.join(M, 'fw/kernel_1A543a.macho')), tree = fs.readFileSync(dtPath);
const rootfs = fs.readFileSync(path.join(here, '../re-trace/rootfs.img'));
const k = alloc(kernel), d = alloc(tree), r = alloc(rootfs);
const rc = mod.ccall('emu_init', 'number', Array(7).fill('number'), [k, kernel.length, d, tree.length, r, rootfs.length, 256]);
console.log('emu_init ->', rc, rc < 0 ? mod.ccall('emu_error', 'string', [], []) : 'ok');
if (rc < 0) process.exit(1);
mod._free(k); mod._free(d); mod._free(r);
mod._emu_set_cpi(1); mod._emu_release_inputs(); mod._emu_host_clock(0);
const sp = mod._malloc(8192); let serial = '', done = 0; const t0 = Date.now();
while (done < Number(n)) { const ran = mod._emu_run(1000000); if (!ran) { console.log('stopped', mod._emu_status()); break; } done += ran;
  for (;;) { const c = mod._emu_serial_read(sp, 8192); if (!c) break; serial += Buffer.from(mod.HEAPU8.subarray(sp, sp + c)).toString('latin1'); } }
fs.writeFileSync(outPath, serial);
console.log(`insns=${done} ticks=${mod._emu_ticks()} wall=${((Date.now()-t0)/1000).toFixed(1)}s serial_bytes=${serial.length}`);
