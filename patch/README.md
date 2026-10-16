# emu-net patch pipeline (SDIO/WiFi hooks for the 2007.mobile core)

Inputs are never modified: `../re-core/emu.wasm` (sha256 `6c168674...52c6`, verified), `../mirror/*` (copied/patched into `out/`).
Needs: python3, node >= 20 (tested v24), wabt (`wasm2wat`, `wat2wasm`, `wasm-validate`; tested 1.0.42, **default feature set only** - `--enable-all` turns on compact-imports and produces a module with a different import encoding).

## Commands

```sh
cd patch
python3 patch.py                      # -> out/emu-net.wasm, out/emu-net.wat, out/dist/{emu.js,emu-net.wasm}, out/{emu-worker,app}.js, out/site/
node test.mjs                         # exports unchanged + ring/SDIO unit tests (fast)
node --max-old-space-size=4096 boot-test.mjs 20000          # snapshot restore, 1e9 instrs, orig vs patched (~35 s, needs mirror/fw)
FRESH=1 node --max-old-space-size=6000 boot-test.mjs 6000   # cold boot from rootfs: guest really probes SDIO (~10 s)
FRESH=1 ONLY=net SERIAL=1 node --max-old-space-size=6000 boot-test.mjs 6000   # + guest serial log
# browser
python3 ../wifi-design/relay.py --listen 127.0.0.1:8008 --host mysite.test=10.0.2.2 &
python3 ../mirror/serve.py 8124 out/site          # symlink overlay of mirror + patched files
#   open http://localhost:8124/?net=1   (or ?net=ws://host:port); window.iphoneNet shows state/trace
node bridge-check.mjs                 # protocol check against a running relay (ARP request -> reply)
```
Note: relay.py defaults to port 8765; the bridge defaults to 8008, so pass `--listen 127.0.0.1:8008`.

## What the patch does

* func 85 (bus read) / func 86 (bus write): new first branch for phys `[0x38D00000,+0x1000)` calling `$sdio_read(off,size)` / `$sdio_write(off,size,val)` (set the "non-RAM access" flag first, as before; RAM fast path untouched). func 87 overlap list gets the same window.
* New functions are appended after the last original function and the new global after original global 0, so **no original function/global index changes**; all 43 original exports keep name, kind and signature (test.mjs checks by parsing both binaries).
* One block (544,832 B) is `malloc`'d lazily (original func 16) on first use: header, 4 KiB SDIO register file, 1024-entry access trace, TX ring and RX ring (128 slots x 2048 B, frames up to 2044 B). It is outside the machine struct, so snapshots and the seed-JIT ABI are unaffected (it is NOT saved in snapshots).
* SDIO register file mode 0 (default) = log only, reads return 0 exactly like the unpatched core (boot is bit-identical). Mode 1 (`emu_net_sdio_mode(1)`) = register readback. `$sdio_read_hook` / `$sdio_write_hook` in `net_funcs.wat` are the places for real behavior; they can call `$emu_net_tx_push`, `$emu_net_rx_pop`, and set the IRQ-pending word (header +36).
* Trace entries are 16 B `{rw<<31|size<<16|off, value, seq, extra_repeats}`; identical consecutive accesses (status polling) merge into one entry with a repeat count.

New exports (also bound as `Module._name` by the patched `dist/emu.js`):

| export | signature | |
|---|---|---|
| `emu_net_tx_pop` | (ptr,max)->len | pop one guest->host frame; 0 empty; -1 buffer too small (frame kept) |
| `emu_net_rx_push` | (ptr,len)->ok | host->guest frame; 0 if full / len 0 / len > 2044 (counts rx drop) |
| `emu_net_state` | (idx)->i32 | 0 block ptr, 1 tx queued, 2 rx queued, 3 tx drops, 4 rx drops, 5 trace queued, 6 irq pending, 7 mode, 8 reg reads, 9 reg writes, 10 version |
| `emu_net_tx_push` / `emu_net_rx_pop` | (ptr,len)->ok / (ptr,max)->len | device-side ends of the rings (future SDIO model; tests) |
| `emu_net_trace_pop` | (dst,maxEntries)->n | |
| `emu_net_sdio_mode` | (m)->old | |
| `emu_net_sdio_read/write` | (off,size[,val]) | poke the register file through the guest path |

JS: `out/emu-worker.js` adds `drainNet()` (after `drainAudio()` in `tick`; posts `{type:'net-tx', buffer}`), handles `{type:'net-rx', buffer}` (no reply), and posts `net-trace` once a second. `out/app.js` bridge is opt-in (`?net=1` -> `ws://localhost:8008`, or `?net=ws://...`): one binary message = one Ethernet frame, first text message from relay = JSON config (kept in `iphoneNet.config`), reconnects with backoff, drops frames while disconnected. With the original `emu.wasm` the worker code is inert (`hasNet` false).

## Edit (c): IRQ 42 polled table - NOT implemented

Looked at func 208: the 15 rows at data 22576 are 12-byte `{?, bit index, handler call_indirect(type 1)}` entries and the handlers return a *time of next event* (min is taken), not an interrupt line level, so adding IRQ 42 means relocating a table whose neighbours/extents in static data are unknown, patching the literal 15 (func 208) and the validation in func 160, and understanding the handler contract. Not safe to do blind; left for a session that can test with a guest that actually unmasks VIC1 bit 10. Until then there is no interrupt delivery (the driver polls DSTA first anyway, see below).

## Verification results (honest)

* `node test.mjs`: PASS (exports identical; rings round-trip incl. full/oversize/wrap; register file; trace merge).
* Snapshot boot (mirror snapshot) in node, original vs patched, 1,000,000,000 instructions: identical cycles, ticks, status and framebuffer hash. Guest never touches SDIO in that state (snapshot was taken after probing).
* Cold boot from rootfs, 300M instructions: patched == original (same hash), and the guest hits the new branch for real. Observed (this is the first real trace of the driver; DESIGN.md's register names):
  `CLKDIV(0x30)=4; DCTRL(0x04)=3,0; CTRL(0x00)=1; STAC(0x14)=ffffffff; IRQ(0x38)=ffffffff; IRQMASK(0x3c)=3; CSR(0x34)=2; ARGU(0x0c)=0; CMD(0x08)=0x00040045` (CMD5) then ~100,000 polls of DSTA(0x18) reading 0 -> guest logs `Timeout waiting for CMDRDY! (cmd 5, cmdReg 0x00040045...)`, `Unable to setup and scan the SDIO bus`. So the first thing a model must do is set DSTA bit0 (cmd ready) and a CMD5 response in RESP0 (0x20).
* Real browser (Browser pane, snapshot restore, `?net=1`): boots to "Ready", guest runs at 1.00x, worker reports the patched core, WebSocket connected and relay config received. `bridge-check.mjs` confirms the relay protocol with an ARP exchange. Not tested: any frame actually leaving the guest (nothing emits TX yet), `net-rx` delivery to a real guest consumer, Safari/Firefox.
* Caveats: seed JIT is bound to the original module hash (`jit/abi.json` moduleSha256), so it should fall back to the interpreter with `emu-net.wasm` (observed ~13 MIPS in the browser pane; not compared against a JIT run). A snapshot taken before the SDIO probe will not re-probe; use a fresh boot to exercise the driver. Ring trace is dropped-oldest at 1024 unread entries; the worker drains 256 entries/s, so use `boot-test.mjs`-style per-slice draining (or raise the rate) for long traces. The MMIO logging in f85/f86 (`[mmio]` printf, flag at +119404) is skipped for SDIO accesses on the write side.
