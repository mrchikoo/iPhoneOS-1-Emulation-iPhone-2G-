# emu-net patch pipeline (SDIO/WiFi hooks for the 2007.mobile core)

Inputs are never modified: `../re-core/emu.wasm` (sha256 `6c168674...52c6`, verified), `../mirror/*` (copied/patched into `out/`).
Needs: python3, node >= 20 (tested v24), wabt (`wasm2wat`, `wat2wasm`, `wasm-validate`; tested 1.0.42, **default feature set only** - `--enable-all` turns on compact-imports and produces a module with a different import encoding).

## Commands

```sh
cd "patch"
python3 patch.py                      # -> out/emu-net.wasm, out/emu-net.wat, out/dist/{emu.js,emu-net.wasm}, out/{emu-worker,app}.js, out/site/
node test.mjs                         # exports unchanged + ring/SDIO unit tests (fast)
node --max-old-space-size=4096 boot-test.mjs 20000          # snapshot restore, 1e9 instrs, orig vs patched (~35 s, needs mirror/fw)
FRESH=1 node --max-old-space-size=6000 boot-test.mjs 6000   # cold boot from rootfs: guest really probes SDIO (~10 s)
FRESH=1 ONLY=net SERIAL=1 node --max-old-space-size=6000 boot-test.mjs 6000   # + guest serial log
# cold boot with the WiFi device tree + SDIO/Marvell model (the main test; see "SDIO host + 88W8686 model")
node --max-old-space-size=6000 coldboot.mjs 2500000000 out/cold      # serial.txt, card-events.txt, final.png, fw sha256 check
#   UI script: dismiss "Repair Needed", open Settings > Wi-Fi, tap LocalNet (joins), then ping the guest
python3 ../wifi-design/relay.py --listen 127.0.0.1:8008 &
RELAY=ws://127.0.0.1:8008 PING_AT=2600 HOLD=200000 CONTACTS=1 \
  TAPS="1800:160,302;1810:277,232;1900:160,140;2000:100,185" \
  node --max-old-space-size=6000 coldboot.mjs 3800000000 out/cold10   # + relay.log (decoded frames)
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
* SDIO register file modes: **2 (default) = SDIO host + Marvell card model** (next section); 0 = log only, reads return 0 like the unpatched core; 1 = register readback. Snapshot boots are still bit-identical to the original core (the restored guest never touches SDIO; re-checked 2026-10-16 with the model and IRQ patch in).
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

## SDIO host + 88W8686 model (`sdio_model.wat`, mode 2 = default)

`patch.py` appends `net_funcs.wat` + `sdio_model.wat` + the IRQ code, and hoists every `(global $...` line of the
appended files to just after original global 0 (so `global.get 0` stays the stack pointer). Register semantics
follow `../re-disasm/SDIO_MAP.md`. Mode 0/1 (`emu_net_sdio_mode`) still give the old log-only / readback behaviour.

* **IRQ**: if `../re-irq/irq_patch.wat` exists it is included (and `irq_patch.apply_func19_hook` is applied, plus
  export `sdio_irq_state`); otherwise `sdio_irq_stub.wat` (no delivery; `NO_IRQ=1` forces the stub). The model calls
  `$sdio_raise_irq`/`$sdio_lower_irq` on edges of `(IRQ & IRQMASK) != 0` (line 42 = VIC1 bit 10). Interrupts are
  required: CMD53 DMA completion is only signalled through IRQ bit0.
* **Host**: DSTA = latched bits | bit0 (CMDRDY always set); a CMD write with bit31 executes the command, puts the
  response in RESP0 (RESP1-3 = 0) and sets DSTA bit4; STAC and IRQ (0x38) writes are W1C; IRQMASK is an enable mask;
  CMD53 is recorded and the DMA runs on `DCTRL` bit4 (0x10): `BLKLEN*NUMBLK` bytes at guest phys `BADDR` (guest RAM via
  machine+67644/67648/67652), then IRQ bit0. Card interrupt = rising edge of `(fn1 H_INT_STATUS & H_INT_MASK & 3) &&
  CCCR IEN bits0+1`, latched into IRQ bit1 (re-latched when the driver's partial ack leaves it high).
* **Card**: CMD5 -> OCR 0x90FF8000 (arg 0 also resets the card model), CMD3 -> RCA 1, CMD7 -> 0x900, CMD52 R5 =
  0x1000|data. CCCR rev 0x32, caps 0x12, CIS 0x1000, IOR mirrors IOE, CCCR5 = card-irq level, FBR1 interface code 7.
  FN1: IO port 0x010000, status 0x20 = 0x0F, 0x05 write = `status &= value`, RD_BASE 0x10/0x11, scratch 0x34/0x35.
* **Firmware download**: helper = 64-byte CMD53s `{u32 len, data}` until a zero length; then RD_BASE asks for 256-byte
  chunks of the 122608-byte image (last 240), then RD_BASE 0 and scratch 0xFEDC. Both images are captured
  (`emu_net_card(4/5)`) and `coldboot.mjs` checks their sha256 against `../re-trace/fw_extract/*.bin`.
* **Runtime protocol**: host packets `{u16 len, u16 type}`; after each download H_INT bit1 (DNLD ready). Uploads are
  queued (16 slots) and announced with scratch = length + H_INT bit0. Commands: response = echo with `cmd|0x8000`,
  result 0 (unknown commands succeed and are logged), except:
  * 0x03 GET_HW_SPEC: if 2, hw 0x8686, MAC (from the earlier 0x4D), region 0x10, fw 0x09460318.
  * 0x3E DEEP_SLEEP: **no response and no DNLD interrupt** (card asleep). Host write FN1 0x03 bit1 wakes it: event
    **0x10** (DEEP_SLEEP_AWAKE, handler c0387768 cancels the timer and sets fPSState=0) + DNLD ready.
    (SDIO_MAP lists 0x10 under RSSI/SNR; in this driver 0x10 is deep-sleep-awake, and 0x0A is only honoured in
    IEEE-PS states 2/3.)
  * 0x06 SCAN: if the channel TLV (0x0101) contains channel 6, one BSS: open AP **"LocalNet"**, BSSID
    02:4c:4f:43:41:4c, rssi 40, beacon 100, capab 0x0001, rates + DS(6) + ext rates IEs.
  * 0x50 ASSOCIATE (and 0x12): answered with code **0x8012** (the driver's handler c03883e8 is in the 0x8012 slot;
    0x8050 is unhandled), capab 1, status 0, aid 0xC001. Marks the card associated (enables RX delivery).
  * Data: TxPD offset/len -> `emu_net_tx_push` (TX ring); RX ring frames become `{len, type 0, RxPD(24, offset 24),
    Ethernet II}` uploads. `emu_net_rx_push` kicks the card itself (so the worker's `net-rx` path works as is).
* Exports added: `emu_net_card(idx)` (0 block, 1 fw stage, 2/3 helper/fw bytes, 4/5 capture ptrs, 6/7 event log
  head/ptr, 8 irq raises, 9 IRQ, 10 IRQMASK, 11 H_INT status, 12 pending upload, 13 card resets, 14 host cmds,
  15 DMAs, 16 line, 17 associated, 18 last-command debug copies) and `emu_net_card_kick()`.
* Not modelled: card state is not in snapshots (see open issues), IEEE power save (PS_MODE 0x21 is just echoed; no
  0x0B sleep / 0x0A awake events are generated), WPA/WEP, multiple APs, CRC/error injection, byte-mode CMD53.

## Verification results (honest)

* `node test.mjs`: PASS (exports identical; rings round-trip incl. full/oversize/wrap; model host regs and
  CMD5/3/7/52, CCCR/FBR/fn1; register file; trace merge).
* **Cold boot with the model** (`coldboot.mjs`, DT `../dt-patch/devicetree_m68ap_wifi.bin`, IRQ from `../re-irq`),
  2026-10-16. Serial (excerpt, `out/cold10/serial.txt`):
  ```
  SDIODeviceNub::init(0xcba4ab00, 1)
  AppleMRVL868x: Starting
  AppleMRVL868x: Ethernet address 02:00:00:20:07:01
  AppleMRVL868x: Loading Bootstrapper
  AppleMRVL868x: Loading Main Program
  AppleMRVL868x: Firmware loadded.
    Firmware Version: 0x09460318 / Hardware Version: 0x8686 / Mac Address: 02:00:00:20:07:01
  virtual SInt32 AppleMRVL868x::setPOWER(IO80211Interface*, apple80211_power_data*): 1
  virtual SInt32 AppleMRVL868x::setASSOCIATE(...): lower auth = APPLE80211_AUTHTYPE_OPEN (1), upper auth = ..._NONE (0)
  AppleMRVL868x: Joining Infrastructure (AP) network: BSSID = 02:4c:4f:43:41:4c, rssi =  51, channel =  6, ... ssid = "LocalNet"
  void AppleMRVL868x::authenticateWithNetwork(BSSBeacon*): authenticating with LocalNet
  void AppleMRVL868x::associateWithNetwork(): joining LocalNet
  AppleMRVL868x: Successfully joined network:  BSSID = 02:4c:4f:43:41:4c, ... ssid = "LocalNet"
  ```
  Helper 2140/2140 and firmware 122608/122608 bytes captured, **sha256 MATCH** with the extracted blobs. Init
  commands seen: 0x4D 0x03 0x28 0x1C 0x1B x2 0x19 x9 0x16 0x67 0x75 0x1E 0x43 0x3E, then deep sleep / wake (0x10)
  cycles only when needed; no command timeouts, no "hand of god" resets (card resets = 2 = power-on probe + first
  load) over 280 guest seconds. Screens: Settings > Wi-Fi shows Wi-Fi ON and **LocalNet** (open, full signal);
  after tapping it the status bar shows the WiFi icon (`out/cold10/final.png`, `out/cold7/final.png`).
  With relay.py: guest DHCP DISCOVER/OFFER/REQUEST/ACK -> 10.0.2.15, ARP for 10.0.2.2/10.0.2.3 answered, DNS
  query/answer, mDNS/IGMP; 61 injected ICMP echo requests (from 2600M insns, i.e. well after association) -> 60
  echo replies (last one in flight at the end of the run) (`out/cold10/relay.log`).
* Bugs found on the way (for the next person): the first version echoed a response to DEEP_SLEEP and raised DNLD
  after it -> "Command (0x003e) Timeout! - invoking hand of god" every ~6 s, or a wake/HOST_SLEEP_CFG/sleep loop at
  ~180 commands per guest second; fixed as described above.
* Snapshot boot (mirror snapshot) in node, original vs patched, 1,000,000,000 instructions: identical cycles, ticks, status and framebuffer hash. Guest never touches SDIO in that state (snapshot was taken after probing).
* Cold boot from rootfs, 300M instructions: patched == original (same hash), and the guest hits the new branch for real. Observed (this is the first real trace of the driver; DESIGN.md's register names):
  `CLKDIV(0x30)=4; DCTRL(0x04)=3,0; CTRL(0x00)=1; STAC(0x14)=ffffffff; IRQ(0x38)=ffffffff; IRQMASK(0x3c)=3; CSR(0x34)=2; ARGU(0x0c)=0; CMD(0x08)=0x00040045` (CMD5) then ~100,000 polls of DSTA(0x18) reading 0 -> guest logs `Timeout waiting for CMDRDY! (cmd 5, cmdReg 0x00040045...)`, `Unable to setup and scan the SDIO bus`. So the first thing a model must do is set DSTA bit0 (cmd ready) and a CMD5 response in RESP0 (0x20).
* Real browser (Browser pane, snapshot restore, `?net=1`): boots to "Ready", guest runs at 1.00x, worker reports the patched core, WebSocket connected and relay config received. `bridge-check.mjs` confirms the relay protocol with an ARP exchange. Not tested: any frame actually leaving the guest (nothing emits TX yet), `net-rx` delivery to a real guest consumer, Safari/Firefox.
* Open issues: (1) the browser build restores `home_1A543a.snap`, taken before WiFi existed, and uses the stock DT,
  so WiFi needs a cold boot with the WiFi DT (or a new snapshot); card/host model state is NOT saved in snapshots, so
  a snapshot taken after firmware load would come back with a reset card (driver would time out and reload). (2) UI
  automation in node fights the periodic baseband "Repair Needed" alert; a Safari page load was not demonstrated
  (TCP through the relay untested from the guest). (3) IEEE power save (0x21) is only echoed.
* Caveats: seed JIT is bound to the original module hash (`jit/abi.json` moduleSha256), so it should fall back to the interpreter with `emu-net.wasm` (observed ~13 MIPS in the browser pane; not compared against a JIT run). A snapshot taken before the SDIO probe will not re-probe; use a fresh boot to exercise the driver. Ring trace is dropped-oldest at 1024 unread entries; the worker drains 256 entries/s, so use `boot-test.mjs`-style per-slice draining (or raise the rate) for long traces. The MMIO logging in f85/f86 (`[mmio]` printf, flag at +119404) is skipped for SDIO accesses on the write side.
