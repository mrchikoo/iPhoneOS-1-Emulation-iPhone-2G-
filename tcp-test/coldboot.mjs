// Cold boot of the PATCHED core (out/dist/emu.js) with the WiFi device tree; prints SDIO/Marvell
// card state and checks captured helper/firmware against re-trace/fw_extract (sha256).
//   node --max-old-space-size=6000 coldboot.mjs [insns=1500000000] [outdir=out/cold]
// env DT=path overrides the device tree (default ../dt-patch/devicetree_m68ap_wifi.bin if present).
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const here = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../patch');  // tcp-test copy: inputs live in ../patch
const N = Number(process.argv[2] || 1.5e9), OUT = path.resolve(process.argv[3] || 'out/cold');
fs.mkdirSync(OUT, {recursive: true});
const wifiDt = path.join(here, '../dt-patch/devicetree_m68ap_wifi.bin');
const dtPath = process.env.DT || (fs.existsSync(wifiDt) ? wifiDt : path.join(here, '../mirror/fw/devicetree_m68ap.bin'));
const kernel = fs.readFileSync(path.join(here, '../mirror/fw/kernel_1A543a.macho'));
const tree = fs.readFileSync(dtPath), rootfs = fs.readFileSync(path.join(here, '../re-trace/rootfs.img'));
const M = await createRequire(import.meta.url)(path.join(here, 'out/dist/emu.js'))({
  locateFile: p => path.join(here, 'out/dist', p), print() {}, printErr() {}});
const put = b => { const p = M._malloc(b.length); M.HEAPU8.set(b, p); return p; };
const rc = M._emu_init(put(kernel), kernel.length, put(tree), tree.length, put(rootfs), rootfs.length, 256);
if (rc < 0) throw new Error('emu_init ' + M.ccall('emu_error', 'string', [], []));
M._emu_set_cpi(1); M._emu_release_inputs(); M._emu_host_clock(0);
console.log('dt', dtPath);
import zlib from 'node:zlib';
function savePng(file) { const w = 320, h = 480, fb = savePng.fb ||= M._malloc(w * h * 4), wp = M._malloc(4), hp = M._malloc(4);
  const lit = M._emu_frame(fb, wp, hp); const px = M.HEAPU8.subarray(fb, fb + w * h * 4), raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const s = (y * w + x) * 4, o = y * (w * 4 + 1) + 1 + x * 4; raw[o] = px[s + 2]; raw[o + 1] = px[s + 1]; raw[o + 2] = px[s]; raw[o + 3] = 255; }
  const t = []; for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  const crc = b => { let c = 0xffffffff; for (const x of b) c = t[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (ty, d) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(ty), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]); };
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(h, 4); ih[8] = 8; ih[9] = 6;
  fs.writeFileSync(file, Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ih), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]));
  return lit; }
// AUTODISMISS: detect the baseband 'Repair Needed' alert (opaque Dismiss button crop, x40..280,y283..313) every 10M insns and tap Dismiss
const ALERT_REF = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'alert_btn.rgb'));
const AUTODISMISS = process.env.AUTODISMISS !== '0'; let adState = 0, adAt = 0, adLast = 0, adCount = 0; const adLog = [];
function alertVisible() { const w = 320, h = 480, fb = alertVisible.fb ||= M._malloc(w * h * 4), wp = M._malloc(4), hp = M._malloc(4); M._emu_frame(fb, wp, hp); M._free(wp); M._free(hp);
  const px = M.HEAPU8.subarray(fb, fb + w * h * 4); let d = 0, i = 0; for (let y = 283; y < 313; y++) for (let x = 40; x < 280; x++) { const s = (y * w + x) * 4; d += Math.abs(px[s + 2] - ALERT_REF[i]) + Math.abs(px[s + 1] - ALERT_REF[i + 1]) + Math.abs(px[s] - ALERT_REF[i + 2]); i += 3; } return d / (240 * 30 * 3); }
// TAPS="2500:160,302;2700:277,232"  -> tap (x,y) at N million instructions (held 20M), screenshot tap_N.png 150M later
const taps = (process.env.TAPS || '').split(';').filter(Boolean).map(t => { const [at, xy] = t.split(':'); const home = xy === 'home'; const [x, y] = home ? [0, 0] : xy.split(',').map(Number); return {at: Number(at) * 1e6, x, y, home, state: 0}; });
const SHOTS = Number(process.env.SHOTS || 0) * 1e6; let lastShot = 0;
const cp = M._malloc(5 * 6 * 4);
let lastXY = [0, 0];
const touch = (x, y, down) => { if (down) lastXY = [x, y]; const r = process.env.CONTACTS ? (down && M.HEAPU32.set([1, x, y, 255, 8, 8], cp >>> 2), M._emu_touch_contacts(cp, down ? 1 : 0))
  : M._emu_touch(down ? (Number(process.env.DOWN) || 1) : (Number(process.env.UP) || 0), lastXY[0], lastXY[1]); if (r < 0) console.log('touch error', M.ccall('emu_error', 'string', [], [])); };

const C = i => M._emu_net_card(i) | 0;
const KIND = {1: 'cmd', 2: 'tx', 3: 'rx', 4: 'helper-done', 5: 'fw-done', 6: 'sleep', 7: 'event', 8: 'fn1cfg', 9: 'scan-hit', 10: 'assoc', 0x7d: 'dma-bad-addr', 0x7e: 'short-pkt', 0x7f: 'queue-full'};
let logTail = 0; const events = [];
const drainLog = () => { const h = C(6) >>> 0, lp = C(7) >>> 0, u = new Uint32Array(M.HEAPU8.buffer);
  for (; logTail < h; logTail++) { const e = u[(lp >>> 2) + (logTail & 255)]; events.push(`${KIND[e >>> 24] || e >>> 24}:0x${(e & 0xffffff).toString(16)}`); } };
const sp = M._malloc(8192); let serial = '', done = 0, last = 0; const t0 = Date.now();
const drainSerial = () => { for (;;) { const c = M._emu_serial_read(sp, 8192); if (!c) break; serial += Buffer.from(M.HEAPU8.subarray(sp, sp + c)).toString('latin1'); } };
const state = () => `stage=${C(1)} helper=${C(2)} fw=${C(3)} irq=${C(9)} mask=${C(10)} line=${C(16)} raises=${C(8)} hint=${C(11)} up=${C(12)} resets=${C(13)} cmds=${C(14)} dmas=${C(15)}`;
// RELAY=ws://127.0.0.1:8008 : forward guest TX frames to relay.py, push its frames into the RX ring
const relayLog = []; let ws = null, rxCount = 0, txCount = 0;
const decode = f => { const ip = a => [...a].join('.'); const et = f.readUInt16BE(12); let d = `len=${f.length} ${f.subarray(6, 12).toString('hex')}->${f.subarray(0, 6).toString('hex')} type=0x${et.toString(16)}`;
  if (et === 0x800) { const pr = f[23]; d += ` ${ip(f.subarray(26, 30))}->${ip(f.subarray(30, 34))} proto=${pr}`; if (pr === 17 || pr === 6) d += ` ${f.readUInt16BE(34)}->${f.readUInt16BE(36)}`;
    if (pr === 17 && (f.readUInt16BE(36) === 67 || f.readUInt16BE(36) === 68)) { const k = f.indexOf(Buffer.from([0x63, 0x82, 0x53, 0x63]), 42); if (k > 0) { const m = f.indexOf(Buffer.from([53, 1]), k + 4); d += ` DHCP type=${m > 0 ? f[m + 2] : '?'} yiaddr=${ip(f.subarray(58, 62))}`; } }
    if (pr === 6) d += ` flags=0x${f[47].toString(16)} seq=${f.readUInt32BE(38)} ack=${f.readUInt32BE(42)} win=${f.readUInt16BE(48)} paylen=${f.length - 34 - (f[46] >> 4) * 4}`; if (pr === 1) d += ` icmp type=${f[34]}`; }
  if (et === 0x806) d += ` ARP op=${f.readUInt16BE(20)} ${ip(f.subarray(28, 32))} asks ${ip(f.subarray(38, 42))}`; return d; };
if (process.env.RELAY) { ws = new WebSocket(process.env.RELAY); ws.binaryType = 'arraybuffer';
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ({data}) => { if (typeof data === 'string') { relayLog.push('config ' + data); return; }
    const f = Buffer.from(data); const p = M._malloc(f.length); M.HEAPU8.set(f, p); const ok = M._emu_net_rx_push(p, f.length); M._free(p); rxCount++; relayLog.push(`RX @${Math.round(done / 1e5) / 10}M ${ok ? '' : '(dropped) '}${decode(f)}`); }; }
const txBuf = M._malloc(2048);
// PING_AT=N (million insns): from then on inject an ICMP echo request 10.0.2.2 -> 10.0.2.15 every 20M insns
const PING_AT = Number(process.env.PING_AT || 0) * 1e6; let pingSeq = 0, lastPing = 0;
const csum = b => { let s = 0; for (let i = 0; i < b.length; i += 2) s += (b[i] << 8) | (b[i + 1] || 0); while (s >> 16) s = (s & 0xffff) + (s >> 16); return ~s & 0xffff; };
const pingFrame = seq => { const f = Buffer.alloc(14 + 20 + 8 + 32); Buffer.from('020000200701525400123502', 'hex').copy(f, 0); f.writeUInt16BE(0x800, 12);
  const ip = f.subarray(14, 34); ip[0] = 0x45; ip.writeUInt16BE(60, 2); ip.writeUInt16BE(seq, 4); ip[8] = 64; ip[9] = 1; Buffer.from([10, 0, 2, 2, 10, 0, 2, 15]).copy(ip, 12); ip.writeUInt16BE(csum(ip), 10);
  const ic = f.subarray(34); ic[0] = 8; ic.writeUInt16BE(0x1234, 4); ic.writeUInt16BE(seq, 6); for (let i = 8; i < ic.length; i++) ic[i] = i; ic.writeUInt16BE(csum(ic), 2); return f; };
const pumpTx = () => { if (!ws) return; let n; while ((n = M._emu_net_tx_pop(txBuf, 2048)) > 0) { const f = Buffer.from(M.HEAPU8.subarray(txBuf, txBuf + n)); ws.send(f); txCount++; relayLog.push(`TX @${Math.round(done / 1e5) / 10}M ` + decode(f)); } };
// CTL=file : live command queue (append lines): `tap x y [holdM]`, `home`, `shot name`, `wait M`, `quit`, `drag x1 y1 x2 y2`
const CTL = process.env.CTL; let ctlPos = 0, ctlQ = [], ctlBusy = null, quit = false;
const ctlPoll = () => { if (!CTL || !fs.existsSync(CTL)) return; const b = fs.readFileSync(CTL); if (b.length > ctlPos) { ctlQ.push(...b.subarray(ctlPos).toString().split('\n').filter(l => l.trim())); ctlPos = b.length; } };
const ctlStep = () => { const t = done;
  if (ctlBusy) { if (t >= ctlBusy.until) { ctlBusy.end?.(); ctlBusy = null; } else { ctlBusy.each?.(t); return; } }
  if (!ctlQ.length) return; const [c, ...a] = ctlQ.shift().trim().split(/\s+/); const n = a.map(Number); console.log('ctl>', c, a.join(' '));
  if (c === 'wait') ctlBusy = {until: t + n[0] * 1e6};
  else if (c === 'tap') { touch(n[0], n[1], true); ctlBusy = {until: t + (n[2] || 0.2) * 1e6, end: () => { touch(0, 0, false); }}; }
  else if (c === 'drag') { touch(n[0], n[1], true); const T = 40e6; ctlBusy = {until: t + T + 5e6, each: tt => { const f = Math.min(1, (tt - t) / T); M._emu_touch_contacts && (process.env.CONTACTS ? (M.HEAPU32.set([1, Math.round(n[0] + (n[2] - n[0]) * f), Math.round(n[1] + (n[3] - n[1]) * f), 255, 8, 8], cp >>> 2), M._emu_touch_contacts(cp, 1)) : M._emu_touch(1, Math.round(n[0] + (n[2] - n[0]) * f), Math.round(n[1] + (n[3] - n[1]) * f))); }, end: () => touch(0, 0, false)}; }
  else if (c === 'home') { M._emu_button(1, 1); ctlBusy = {until: t + 3e6, end: () => M._emu_button(1, 0)}; }
  else if (c === 'shot') { savePng(path.join(OUT, a[0] + '.png')); }
  else if (c === 'quit') quit = true; };
while (done < N && !quit) {
  if (CTL && (ctlPoll(), ctlStep(), 0)) break;
  const ran = M._emu_run(1_000_000); if (!ran) { console.log('stopped status', M._emu_status()); break; }
  done += ran; drainSerial(); drainLog();
  if (PING_AT && done >= PING_AT && done - lastPing >= 20e6) { lastPing = done; const f = pingFrame(++pingSeq); const p = M._malloc(f.length); M.HEAPU8.set(f, p);
    const ok = M._emu_net_rx_push(p, f.length); M._free(p); relayLog.push(`RX(ping ${pingSeq}) ${ok ? '' : '(dropped) '}${decode(f)}`); if (!ws) { let n; while ((n = M._emu_net_tx_pop(txBuf, 2048)) > 0) relayLog.push('TX ' + decode(Buffer.from(M.HEAPU8.subarray(txBuf, txBuf + n)))); } }
  if (ws) { pumpTx(); await new Promise(r => setImmediate(r)); }
  if (AUTODISMISS) { if (adState === 0 && done - adLast >= 10e6) { adLast = done; const dd = alertVisible(); if (dd < 12) { adState = 1; adAt = done; touch(160, 298, true); adCount++; adLog.push(`dismiss #${adCount} @${Math.round(done / 1e6)}M diff=${dd.toFixed(1)}`); console.log(adLog.at(-1)); } }
    else if (adState === 1 && done - adAt >= 3e6) { touch(0, 0, false); adState = 2; } else if (adState === 2 && done - adAt >= 30e6) adState = 0; }
  for (const t of taps) {
    if (t.state === 0 && done >= t.at) { t.home ? M._emu_button(1, 1) : touch(t.x, t.y, true); t.state = 1; }
    else if (t.state === 1 && done >= t.at + (Number(process.env.HOLD) || 2e6)) { t.home ? M._emu_button(1, 0) : touch(0, 0, false); t.state = 2; }
    else if (t.state === 2 && done >= t.at + 150e6) { savePng(path.join(OUT, `tap_${t.at / 1e6}.png`)); console.log(`tap ${t.x},${t.y} @${t.at / 1e6}M done`); t.state = 3; }
  }
  if (SHOTS && done - lastShot >= SHOTS) { lastShot = done; savePng(path.join(OUT, `shot_${Math.round(done / 1e6)}.png`)); }
  if (done - last >= 100_000_000) { last = done;
    console.log(`insns=${done / 1e6}M guest_s=${(M._emu_ticks() / 6e6).toFixed(2)} wall=${((Date.now() - t0) / 1000).toFixed(0)}s ${state()}`);
    fs.writeFileSync(path.join(OUT, 'serial.txt'), serial); if (ws || PING_AT) fs.writeFileSync(path.join(OUT, 'relay.log'), relayLog.join('\n') + '\n'); }
}
fs.writeFileSync(path.join(OUT, 'serial.txt'), serial);
fs.writeFileSync(path.join(OUT, 'card-events.txt'), events.join('\n') + '\n');
console.log('final', state());
if (ws || PING_AT) { fs.writeFileSync(path.join(OUT, 'relay.log'), relayLog.join('\n') + '\n'); console.log(`relay: tx ${txCount} rx ${rxCount}`); console.log(relayLog.slice(0, 40).join('\n')); ws?.close(); }
{ const b = M._malloc(2048); let n, i = 0; const ip = a => [...a].join('.');
  while ((n = M._emu_net_tx_pop(b, 2048)) > 0) { const f = Buffer.from(M.HEAPU8.subarray(b, b + n)); const et = f.readUInt16BE(12); let d = `len=${n} dst=${f.subarray(0, 6).toString('hex')} src=${f.subarray(6, 12).toString('hex')} type=0x${et.toString(16)}`;
    if (et === 0x800) { const pr = f[23], sp = f.readUInt16BE(34), dp = f.readUInt16BE(36); d += ` ip ${ip(f.subarray(26, 30))}->${ip(f.subarray(30, 34))} proto=${pr} ${sp}->${dp}`; if (dp === 67) d += ` DHCP op=${f[42]} msgtype=${f.indexOf(Buffer.from([53, 1]), 282) > 0 ? f[f.indexOf(Buffer.from([53, 1]), 282) + 2] : '?'}`; }
    if (et === 0x806) d += ` ARP op=${f.readUInt16BE(20)} ${ip(f.subarray(28, 32))} asks ${ip(f.subarray(38, 42))}`;
    if (i++ < 30) console.log('TX', d); }
  console.log('TX frames total', i); }
{ const p = C(18) >>> 0; console.log('last SCAN cmd:', Buffer.from(M.HEAPU8.subarray(p, p + 120)).toString('hex').replace(/(..)/g, '$1 ')); console.log('last other cmd:', Buffer.from(M.HEAPU8.subarray(p + 256, p + 256 + 120)).toString('hex').replace(/(..)/g, '$1 ')); }
const sha = (p, n) => crypto.createHash('sha256').update(M.HEAPU8.subarray(p, p + n)).digest('hex');
for (const [name, lenIdx, ptrIdx] of [['helper', 2, 4], ['firmware', 3, 5]]) {
  const ref = fs.readFileSync(path.join(here, `../re-trace/fw_extract/sd8686_${name}_from_kernel.bin`));
  const n = C(lenIdx), got = n ? sha(C(ptrIdx) >>> 0, Math.min(n, ref.length)) : '-';
  const want = crypto.createHash('sha256').update(ref).digest('hex');
  console.log(`${name}: captured ${n} / ${ref.length} bytes, sha256 ${got === want && n === ref.length ? 'MATCH' : 'mismatch'} (${got.slice(0, 16)} vs ${want.slice(0, 16)})`);
}
console.log('card events (last 40):', events.slice(-40).join(' '));
console.log('--- serial lines mentioning SDIO/MRVL/WiFi ---');
console.log(serial.split('\n').filter(l => /sdio|mrvl|marvell|868|wifi|airport|firmware|helper|scratch|en0|80211|cmd/i.test(l)).slice(0, 120).join('\n'));
console.log('final.png lit', savePng(path.join(OUT, 'final.png')));
