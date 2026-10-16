# WiFi emulation for iPhone 2G (S5L8900, iPhone OS 1.0 / 1A543a)

Status: design + working host-side relay prototype (`relay.py`, 12 unit tests passing,
`python3 -m unittest -v test_relay` in this directory). The guest-side pieces (a, b) are
designed but NOT implemented or tested; no OS 1.0 kernel was available to this research.

## 0. Confidence legend

* [S] seen in a fetched primary source (cited in section 9)
* [M] from my memory of the Linux libertas headers, not re-verified this session
* [?] unknown / guess; must be established empirically by tracing the real OS 1.0 kernel

## 1. What the hardware is

* iPhone 2G WiFi chip: Marvell 88W8686, 802.11b/g [S: TechInfoDepot hardware page]. The chip
  supports SDIO and SPI host interfaces [S: Marvell product description via search result].
  That the iPhone wires it to the S5L8900 SDIO controller is confirmed by openiBoot containing an
  S5L8900 SDIO driver and a `wlan.c` that talks to function 1 [S: openiBoot].
* The 88W8686 is "SD8686" in Linux libertas (`MODEL_8686 = 0x0b`) [S: if_sdio.c].
* The Bluetooth (CSR BC4) and baseband are separate UART paths; out of scope.

## 2. S5L8900 SDIO host controller (what the guest driver programs)

Base `0x38D00000`, clock gate 0x0B, IRQ 0x2A (VIC) [S: openiBoot plat-s5l8900/sdio.c + hardware/sdio.h].
This is a Samsung-style SD/MMC host (not SDHCI). Registers (offset from base):

| off | name | known behaviour (all from openiBoot usage; bit meanings partly inferred) |
|---|---|---|
| 0x00 | CTRL | bit0 enable, bit2 4-bit bus, bit15 (0x8000) block mode vs byte mode |
| 0x04 | DCTRL | write 3 then 0 = FIFO reset; write 0x10 = start data phase |
| 0x08 | CMD | [5:0] index, bit6 resp present, bits7-8 resp-len code (`2<<6`,`3<<6` seen), bit8 = write for CMD53 (`1<<8`), bits[18:16] response type, bit31 = execute |
| 0x0C | ARGU | command argument |
| 0x10 | STATE | 0 = idle; bits4-6 = cmd FSM busy |
| 0x14 | STAC | status clear; write DSTA (or 0xFFFFFFFF) to ack |
| 0x18 | DSTA | bit0 cmd-ready, bit2 cmd-sent, bits[18:15] SD error/status code (0 = OK) |
| 0x1C | FSTA | FIFO status (unused by openiBoot) |
| 0x20-0x2C | RESP0-3 | response words (short resp in RESP0) |
| 0x30 | CLKDIV | 1<<7 ~ 400 kHz init, 1<<1 ~ 25 MHz |
| 0x34 | CSR | written 2 at init (meaning [?]) |
| 0x38 | IRQ | bit0 data/block done, bit1 SDIO card interrupt; write-back to ack |
| 0x3C | IRQMASK | init value 3 |
| 0x44 | BADDR | DMA buffer physical address (4-byte aligned) |
| 0x48 | BLKLEN | block size (block mode) or byte count (byte mode) |
| 0x4C | NUMBLK | block count |
| 0x50 | REMBLK | remaining blocks (read-only) |

Data movement is DMA-style via BADDR; there is no FIFO bulk access in openiBoot. CMD52 arg:
`rw<<31 | func<<28 | raw<<27 | addr<<9 | data`; CMD53 arg adds `block<<27`, `incr<<26`,
`count`. The init sequence (CMD5 probe, voltage loop, CMD3, CMD7, CCCR reads, bus width switch, CIS
read, per-function block size) is in openiBoot `sdio_setup` [S].

**Unknown [?]:** exact bit layout of DSTA error field, CSR, FSTA, any interrupt-status bits
beyond 0/1, whether OS 1.0's `AppleSDIO`-like kext uses interrupt-driven DMA completion instead of
openiBoot's polling, and bus-power GPIO numbers (openiBoot only defines `0x1701`/`0x607` for the
iPod touch 1G / other builds; the iPhone 2G WiFi power/reset GPIO is not known to me). The
emulator must therefore log every access to unknown registers and implement "reads return last
written / 0, writes accepted" for unnamed offsets so the kext does not hang.

## 3. 88W8686 as seen over SDIO (libertas `if_sdio`)

Function 1 register map (byte-wide CMD52, 0x00-based "SDIO scratch" space) [S: if_sdio.h]:

```
0x00-02 IOPORT   (24-bit address of the data port, read by host)
0x04 H_INT_MASK   0x05 H_INT_STATUS (bits: UPLD=1 DNLD=2 UFLOW=4 OFLOW=8; host clears by writing ~cause)
0x06 H_INT_RSR    0x07 H_INT_STATUS2
0x10-11 RD_BASE   (16-bit "bytes the firmware wants next" during fw download)
0x20 STATUS       (DL_RDY=1 UL_RDY=2 CIS_RDY=4 IO_RDY=8)
0x24 C_INT_MASK  0x28 C_INT_STATUS  0x2C C_INT_RSR
0x34 SCRATCH (16-bit; 0xFEDC = firmware running)       [MODEL_8686]
0x40 FW_STATUS  0x42 RX_LEN  0x43 RX_UNIT               [8688+, not used by 8686]
```

Firmware download, as done by openiBoot `wlan.c` and Linux [S, both agree]:
1. enable F1, read IOPORT, mask H_INT, read SCRATCH (if 0xFEDC or `&0x7fff != 0` firmware is already running).
2. Helper: block size 32; for each 60-byte chunk wait STATUS&(IO_RDY|DL_RDY)==both, write 64 bytes to IOPORT (`u32 len` + data); finish with a 64-byte all-zero block; poll RD_BASE nonzero.
3. Main FW: loop { poll STATUS; `n = RD_BASE`; n==0 -> abort, n&1 -> error; send min(n, remaining) padded to 32, in <=512-byte writes } ; poll SCRATCH == 0xFEDC.
4. Set block size 256, enable H_INT_MASK=0x0F, (8686: no FUNC_INIT).

Runtime framing (`if_sdio_host_to_card`/`card_to_host`) [S]: every IOPORT transfer is
`u16 len_incl_hdr | u16 type | payload`, padded to the block size. Types: command, data, event
(numeric values `MVMS_DAT=0`, `MVMS_CMD=1`, `MVMS_EVENT=3` [M]). Upload ready is signalled by
H_INT_STATUS.UPLD; the host writes `~cause` to ack. For 8686 the received length is the header
length (no RX_LEN register).

Host command header (8 bytes LE: `cmd, size, seqnum, result`); response cmd = `0x8000|cmd`;
codes (GET_HW_SPEC=3, MAC_CONTROL=0x28, MAC_MULTICAST_ADR=0x10, SNMP_MIB=0x16, RF_CHANNEL=0x1D,
SCAN=6, ASSOCIATE=0x50, DEAUTH=0x24, MAC_ADDRESS=0x4D, RADIO_CONTROL=0x1C, etc.) [S: host.h]. TX
descriptor 24 bytes, RX descriptor 20 bytes [S: host.h field list]. Events: `u32` code,
e.g. LINK_SENSED=4, LINK_LOST=2/3, DEAUTH=8, FIRMWARE_READY=48 [S].
`CMD_GET_HW_SPEC` reply fields: hwifversion, version, nr_mcast, permanentaddr[6], regioncode,
nr_antenna, fwrelease, wcb_base, rxpd_rdptr, rxpd_wrptr, fwcapinfo [M order/widths].

**Critical unknown [?]:** libertas is a Linux driver; Apple's OS 1.0 driver talks the same
firmware but may use a different command subset (it is Apple's own "AppleWiFi"-style stack, and
likely uses the Marvell WLAN "Wireless Extensions"-less host-command API with TLV scan). I
found no public description of the OS 1.0 IOKit class names or of which commands it issues. The
emulator therefore must (i) log every unhandled host command with its payload, (ii) answer
unknown commands with `result=0` and an echoed body (libertas firmware does this for many
commands), and (iii) be iterated against the real kernel. Also unknown: whether OS 1.0 ships the
Marvell helper/firmware blobs in the root filesystem (path/name [?]); the emulated card does
not need real blobs, only that the byte stream is consumed.

## 4. Component (a): emulated SDIO host + 88W8686 device

Language: whichever emulator hosts the CPU (QEMU C device, or JS in a browser emulator). The logic
is identical; below is pseudocode.

### 4.1 Host controller (`S5L8900_SDIO`, MMIO @0x38D00000)

State: registers above, `cmd_pending`, `irq_line`.

* Write CMD with bit31 set: decode index/response type; run `sdio_card.command(index, arg)`;
  store response in RESP0-3; set DSTA bit0/bit2 and clear STATE; error field = 0.
* Write DCTRL=0x10 with a CMD53 outstanding: perform the transfer immediately (no timing
  fidelity needed): DMA `BLKLEN*NUMBLK` (block) or `BLKLEN` (byte) bytes between guest RAM[BADDR]
  and `sdio_card.io_rw_extended(func, addr, incr, write, buf)`; set IRQ bit0; raise VIC 0x2A only if
  unmasked in IRQMASK (openiBoot polls IRQ bit0, OS kernel may use the interrupt; support both).
* Card interrupt: when `card.irq_pending()` and IRQMASK allows, set IRQ bit1 and raise VIC 0x2A;
  the driver masks, reads CCCR 0x05, services, unmasks (openiBoot pattern [S]).
* Reads of FSTA/REMBLK: return 0 (idle/complete). Unknown offsets: log + RAZ/WI.
* Stub the power/reset GPIO writes (accept; model "card powered" as soon as GPIO is toggled, and
  also if never toggled, since boot ROM state is unknown).

### 4.2 SDIO card core

* CMD5 -> OCR with 1 function, ready; CMD3 -> RCA 0x0001; CMD7 select; CMD52 / CMD53 dispatch.
* CCCR (func 0): 0x00 rev (SDIO 1.10/2.0), 0x02 IOE, 0x03 IORx, 0x04 IEN, 0x05 INT pending, 0x07
  bus interface (bits1:0 width), 0x08 caps, 0x13 high-speed. CIS: minimal tuples with MANFID
  vendor 0x02DF (Marvell), device 0x9103 (SD8686) [M]. FBR for F1: standard-interface 0x00 (or
  class 0x07 vendor) [?]. Per-function block size at `func*0x100+0x10/0x11`.
* F1 register file per section 3. IOPORT: writes of any length go to `device.rx_from_host()`;
  reads drain `device.tx_queue`.

### 4.3 88W8686 device state machine

```
RESET --(F1 enable)--> WAIT_HELPER  SCRATCH=0x0000, STATUS=IO_RDY|DL_RDY|CIS_RDY
WAIT_HELPER --(64B blocks; zero-length block seen)--> HELPER_DONE; RD_BASE := 0x0100 (first main-fw request size; any nonzero even value)
HELPER_DONE: each IOPORT write consumed; RD_BASE := next request (min(512*k, remaining) unknown total -> accept until idle >N ms? see below)
FW_DONE  --> SCRATCH := 0xFEDC, then READY
```

Because the emulator does not know the firmware image length, use: after the helper, keep
requesting 256-byte (even) chunks; declare FW_DONE when the guest stops writing and polls SCRATCH, i.e.
set SCRATCH=0xFEDC after the first main-fw block *and* keep accepting further writes into a sink. Real
drivers poll SCRATCH only after sending all bytes, so setting it early is safe for libertas
(its loop checks RD_BASE==0 -> "gave up", which we must not return before data ends: return nonzero
even until the driver stops asking). If OS 1.0 sizes blocks from a header inside the blob, this
shortcut is [?]; fallback is to parse the Marvell fw image header (also unknown).

READY: STATUS=IO_RDY|DL_RDY|UL_RDY; H_INT_MASK stored; on `tx_queue` non-empty set
H_INT_STATUS.UPLD (=1) and assert card IRQ if enabled by CCCR IEN and H_INT_MASK; writing
`~cause` to H_INT_STATUS clears. After each host write (cmd or data) set DNLD (=2) and IRQ.

Host commands (header 8B) — minimum set; unknown ones are logged and acked OK:

| cmd | emulated response |
|---|---|
| 0x0003 GET_HW_SPEC | MAC (config), region 0x10, fwrelease e.g. 0x05010100, fwcapinfo, hwif 3, version 3 |
| 0x0028 MAC_CONTROL | store flags |
| 0x0010 MAC_MULTICAST_ADR, 0x0016 SNMP_MIB, 0x001C RADIO_CONTROL, 0x001D RF_CHANNEL, 0x0022 DATA_RATE, 0x001E TX_POWER, 0x0020 ANTENNA, 0x0021 PS_MODE | echo body, set result 0 (RF_CHANNEL returns requested) |
| 0x004D MAC_ADDRESS | get/set MAC |
| 0x0006 SCAN | respond with a BSS list containing one fake AP (SSID configurable, BSSID, ch 6, caps ESS, rates 1/2/5.5/11/6..54): rsp = `u16 bufsize, u8 nr_bss, ` then per-BSS `u16 len, bssid, rssi, tsf, beacon interval, caps, IEs`  [M, check against real driver]
| 0x0011 AUTHENTICATE, 0x0050 ASSOCIATE | success rsp (status 0, AID 1) then event LINK_SENSED(4) |
| 0x0024 DEAUTH | event DEAUTHENTICATED(8) |
| 0x0013/0x005E/0x002F | WEP/WPA keys: accept (open network only for v1) |

After firmware ready send event FIRMWARE_READY(48) if the driver expects it [?].

Data path: host->card packets of type DAT carry `txpd(24B) + 802.3 frame (dst6 src6 ethertype payload)`
[M: libertas TX carries an ethernet frame after txpd, dest in txpd]. Emulator strips txpd, sends the
ethernet frame to the bridge (section 5). Card->host: bridge frame arrives, prefix with a 20-byte
rxpd (`status=1, snr=40, rx_control=0, pkt_len=len, nf, rx_rate, pkt_ptr=sizeof(rxpd)=0x14,
next=0, priority=0`) and queue as type DAT (the driver reads ethernet from `pkt_ptr`) [M].
Association state gates data: before ASSOCIATE success, drop TX.

Link bring-up must be fast: emulate "already associated to SSID X" after the first successful
scan+associate; the kernel/Preferences then runs DHCP over our relay.

## 5. Component (b): JS bridge

Contract with the relay (implemented in `relay.py`):

* Transport: WebSocket `ws://127.0.0.1:8765`. First server message is text JSON
  `{"type":"config","gateway":"10.0.2.2","gwmac":"525400123502","dns":"10.0.2.3","mask":"255.255.255.0"}`.
* Every subsequent message in either direction is a binary message holding exactly one Ethernet II
  frame (no FCS). Guest MAC is whatever 88W8686 MAC the emulator reports.

JS module sketch (browser or Node):

```js
class WifiBridge {
  constructor(url, onFrameToGuest) {
    this.q = []; this.open = false;
    this.ws = new WebSocket(url); this.ws.binaryType = 'arraybuffer';
    this.ws.onopen = () => { this.open = true; this.q.forEach(f => this.ws.send(f)); this.q = []; };
    this.ws.onmessage = e => typeof e.data === 'string'
        ? (this.config = JSON.parse(e.data))
        : onFrameToGuest(new Uint8Array(e.data));
    this.ws.onclose = () => { this.open = false; setTimeout(() => this.reconnect(), 1000); };
  }
  sendFromGuest(u8) { const b = u8.slice().buffer;   // copy; guest RAM may be reused
                      this.open ? this.ws.send(b) : (this.q.length < 256 && this.q.push(b)); }
}
```

Device-side glue: `onFrameToGuest` pushes `rxpd+frame` onto the 8686 `tx_queue` and raises the
UPLD interrupt; coalesce at most 1 interrupt per event-loop turn. Backpressure: cap queue (e.g. 256
frames) and drop oldest — WiFi is lossy anyway. Browser caveat: `ws://127.0.0.1` from an https page
is blocked as mixed content in some browsers; serve the emulator from http://localhost or use Node.
Alternative transports that fit the same contract: a Node `dgram`/`net` bridge, or a Web Worker
with `postMessage` to the same module in tests.

## 6. Component (c): relay (`relay.py`)

Implemented, stdlib-only (asyncio), 12 unit tests passing:

* Minimal RFC 6455 server (handshake verified against the RFC example key, fragmented/masked
  frames, ping/pong/close, 16/64-bit lengths).
* ARP responder for 10.0.2.2/10.0.2.3; DHCP (DISCOVER/OFFER, REQUEST/ACK/NAK, ignores other
  servers; lease 10.0.2.15+; replies broadcast); ICMP echo to gateway/DNS.
* DNS on 10.0.2.2/.3 :53: static `--host name=ip` A records (use these to point a site at
  `10.0.2.2`, i.e. host loopback, e.g. the site in `site-2007`), everything else forwarded to the
  host resolver (`/etc/resolv.conf` or `--dns`).
* UDP NAT with 60 s idle expiry; TCP NAT by terminating guest TCP and re-originating
  real sockets (guest SYN -> host connect -> SYN+ACK with MSS 1400; unreachable -> RST;
  windowed segmenting honouring guest window; FIN/RST handling). 10.0.2.2 maps to host 127.0.0.1.
* Run: `python3 relay.py --listen 127.0.0.1:8765 --host mysite.test=10.0.2.2`.
* Security: binds loopback by default; the guest can reach anything the host can reach, including
  loopback services via 10.0.2.2. Do not bind to 0.0.0.0.

Known gaps: IPv4 only; no IP fragment reassembly (fragments dropped); no retransmission toward
the guest (link is reliable) and no persist timer for zero windows; host->guest backpressure on
`writer.write` is not awaited; guest TCP options (window scale, SACK) are ignored, so throughput is
bounded by a 64 KB window; ICMP to the internet is not forwarded; no TCP keepalive/idle timeout
(tcp flows end on FIN/RST/WS close); single guest per WS connection; not fuzzed.
Tests do not exercise a real OS TCP stack or sustained transfer, only a scripted guest.

## 7. What OS 1.0 kernel/IOKit is expected to do (mostly unknown)

Known/likely [?]: IOKit matches an SDIO-bus nub on the S5L8900 controller (matching via CIS
manufacturer/function), a WiFi driver family loads the Marvell helper+firmware via the
kernel (path in root FS unknown), brings up an `en0`-class network interface, `configd`
/ `wpa`-less supplicant (OS 1.0 used the Marvell host commands, not wpa_supplicant) handles
scan/associate, then `bootpd`-style DHCP client. I found no public documentation of 1A543a's WiFi
kexts and the iPhone Wiki / Apple Wiki pages were inaccessible (HTTP 403) so nothing was verified there.
Ordered bring-up plan: (1) trace SDIO register accesses in the guest to capture exactly which
CMD/offsets it uses, (2) implement the card up to firmware-ready, (3) log host commands,
(4) implement scan/associate, (5) bring up data path and run DHCP against `relay.py`.

## 8. Existing emulation work

* devos50's QEMU-iOS (`ipod_touch_1g` branch) boots iPod touch 1G / iOS 1.0 (same S5L8900) but
  explicitly does not emulate WiFi [per search results; not confirmed by me in source]. Its
  S5L8900 peripheral models are the best starting point for the SDIO MMIO device.
* iEmu / QEMU-s5l89xx (cmw) abandoned 2013; no known WiFi support.
* openiBoot has the only open S5L8900 SDIO + WLAN firmware-load code I could read; it has bugs
  (4-byte chunk buffers in wlan.c; a CCCR 0x13 read used as a write) — do not copy blindly.
* Linux libertas `if_sdio` is the reference for the 8686 protocol; Apple's driver compatibility
  with it is an assumption.

## 9. Sources

* openiBoot S5L8900 SDIO header: https://github.com/iDroid-Project/openiBoot/blob/master/plat-s5l8900/includes/hardware/sdio.h
* openiBoot SDIO driver: https://github.com/iDroid-Project/openiBoot/blob/master/plat-s5l8900/sdio.c
* openiBoot WLAN firmware loader: https://github.com/iDroid-Project/openiBoot/blob/master/plat-s5l8900/wlan.c
* Linux libertas if_sdio.c / if_sdio.h / host.h / types.h:
  https://github.com/torvalds/linux/tree/master/drivers/net/wireless/marvell/libertas
* RidgeRun 88W8686 SDIO integration guide: https://developer.ridgerun.com/wiki/index.php/Marvel_88W8686_SDIO_WiFi_Integration
* libertas-dev mailing list threads (fw load, GET_HW_SPEC): https://lists.infradead.org/pipermail/libertas-dev/2008-June/001807.html
* TechInfoDepot Apple hardware (88W8686 in iPhone): https://techinfodepot.shoutwiki.com/wiki/Apple/Apple_Hardware
* iEmu: https://www.theiphonewiki.com/wiki/IEmu ; QEMU-iOS notes via emulation.gametechwiki.com/index.php/iOS_emulators
* Not reached (403): theapplewiki.com pages for iPhone 2G and S5L8900.

Caveat on sources: page contents were obtained through a summarizing fetch tool, so register
values/code details are the summarizer's rendering of the files; spot-check against raw source
before hard-coding.
