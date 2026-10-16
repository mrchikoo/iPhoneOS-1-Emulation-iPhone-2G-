# S5L8900 SDIO host + Marvell 88W8686 driver map (from the 1A543a kernel)

I got everything here by statically disassembling `input/kernel_1A543a.macho`, a read-only copy of
`mirror/fw/kernel_1A543a.macho` (Darwin 9.0.0d1, `RELEASE_ARM_S5L8900XRB`). I did not run anything on hardware.

Legend:
* **[C @addr]**: confirmed. You can see it in the disassembly at that kernel VA.
* **[I]**: inferred. It is consistent with the code, but the meaning or name is my interpretation.
* **[U]**: unknown. The kernel does not say, usually because the value comes from the device tree.

Tooling (all under `re-disasm/`): `scripts/macho.py` (Mach-O parser), `scripts/kimg.py` (VA mapping,
kernel + PRELINK symbol tables, annotated capstone disassembly, ARM and Thumb), `scripts/vtables.py`
(vtable recovery, with slots named from `__ZTV9IOService`), `scripts/funcs.py`. Listings are in `out/`:
`sdio_raw.s`, `family_raw.s`, `mrvl_raw.s`, `s5l_raw.s`. The venv has capstone 5.0.7, and every script runs with `python -I`.

---------------------------------------------------------------------------------------------------

## 0. Image layout / where the code is

* Main kernel: MH_EXECUTE with a symtab (10463 syms). The kernel itself is mostly **Thumb**. Kext code is
  **ARM**. Kexts call kernel functions through literal-pool pointers with the Thumb bit set.
* `__PRELINK,__text` vm `c0222000` maps linearly to file `0x1fb000` (file = va - 0xc0027000). Each kext is an
  MH_OBJECT whose header sits at text-0x1000. The kexts' own symtabs are stripped. `__PRELINK,__symtab` holds only
  kernel/KPI symbol images. `__PRELINK,__info` is the XML plist with personalities and `OSBundlePrelink`.

| kext | __text | __const (vtables) | __data | kmod_info |
|---|---|---|---|---|
| com.apple.iokit.IOSDIOFamily | c0379000-c037bf58 | c037c540.. | c037d6d8 | c037d71c |
| com.apple.driver.AppleS5L8900XSDIO | c0431000-c04324d0 | c0432a5c.. | c0432ff0 | c043305c |
| com.apple.driver.AppleMRVL868x | c037f000-c038ce04 | c0391edc.. | c0392d1c (0x1e884 bytes, contains fw blobs) | c03b14f8 |

Personalities (plist, [C]):
* `AppleS5L8900XSDIO`: IOProviderClass `AppleARMIODevice`, IONameMatch `sdio,s5l8900x`, **IOResourceMatch `IOBSD`**.
* `AppleMRVL868xSDIO`: IOProviderClass **`IOSDIODevice`** only. It has no CIS, vendor or product matching. IONetworkRootType `airport`.
* IOSDIOFamily: no personalities (library).

Recovered vtables / classes ([C], vtable ptr = start of table with 2 leading zero words; slot byte offset = `ldr pc,[rX,#off]`):

* `IOSDIOController` vt c037c540 (base) / `AppleS5L8900XSDIO` vt c0432a5c (subclass). IOSDIOController virtuals:

| off | family impl | S5L impl | name (from strings / behaviour) |
|---|---|---|---|
| 0x4a4 | c037a26c | - | executeCommand(IOSDIOCommand*) via command gate ("Can't turn issueCommandAction...") |
| 0x4a8 | c0379778 | - | getCISAddress() (returns +0x70) |
| 0x4ac | c0379a38 | - | resetFunction(UInt8) ("gatedResetFunction") -> slot 0x4b0 on gate |
| 0x4b0 | c0379a0c | - | gatedReset: resetDevices(); setupAndScanBus() |
| 0x4b4 | c03796c8 | - | enqueue/processCommand(IOSDIOCommand*) ("arg1 != IOSDIOCommand") |
| 0x4b8 | stub c03794ac | **c043134c** | sendCommand(UInt8 idx, UInt32 arg, UInt32 resp[4]) |
| 0x4bc | c03794d0 | - | CMD52 read(fn, addr, UInt8*) |
| 0x4c0 | c0379528 | - | CMD52 write(fn, addr, UInt8) |
| 0x4c4 | c0379cf4 | - | CMD53 block transfer(IOSDIOCommand*) |
| 0x4c8 | stub | **c04319bc** | prepareForDMA(cmd) |
| 0x4cc | stub | **c0431ae8** | startDMA(cmd) |
| 0x4d0 | stub | **c0431b4c** | completeDMA(cmd) |
| 0x4d4 | c037963c | - | completeCurrentCommand() ("Invoking processCommand() from completeCurrentCommand()") |
| 0x4d8 | c0379b54 | - | scanFunctions / create nubs |
| 0x4dc | c03798bc | - | setupAndScanBus (card init) |
| 0x4e0 | stub c03799ec | **c043188c** | resetDevices() (GPIO reset pulse) |
| 0x4e4 | c0379c0c | **c0431d1c** | setBusWidth(UInt8) |
| 0x4e8/0x4ec | c0379c6c / c0379c74 | - | getBusWidth / setBlockSize(fn, UInt16) |
| 0x4f0 | c0379adc | - | handleCardInterrupt() |
| 0x4f4 | c0379cc8 | - | getWorkLoop() |
| 0x4f8 | - | **c0432108** | interrupt handler (IOInterruptEventSource action, pmf c0432fe0 -> slot 0x4f8) |

* `IOSDIOCommand` vt c037cab0: init c037a0fc. Fields: +0xc type (1=CMD52 rd, 2=CMD52 wr, 3=CMD53 rd, 4=CMD53 wr),
  +0x10 fn, +0x14 addr, +0x18 data byte, +0x1a block size (default 4), +0x1c fixed-address flag,
  +0x20 IOMemoryDescriptor. [C c037a0fc-c037a1d8, dispatch jump table c03795b4]
* `IOSDIODevice` vt c037cc18 / `SDIODeviceNub` vt c037d16c. IOSDIODevice API (used by AppleMRVL868x):

| off | impl | meaning |
|---|---|---|
| 0x4a4 | c037b3e0 | init(controller, fn) |
| 0x4a8 | c037addc | enableFunction(bool): RMW CCCR 0x02 bit fn, then poll CCCR 0x03 bit fn (<=1,000,000 CMD52s), "Failed to enabled device!" |
| 0x4ac | c037afd4 | enableInterrupt(bool): RMW CCCR 0x04 bit fn; stores flag +0x70 that gates causeInterrupt |
| 0x4b0 | c037b4b0 | readByte(addr, UInt8*) = CMD52 read on own fn |
| 0x4b4 | c037b538 | writeByte(addr, UInt8) = CMD52 write on own fn |
| 0x4b8 | c037b5b4 | readBlocks(addr, bool fixedAddr, IOMemoryDescriptor*) = CMD53 read |
| 0x4bc | c037b63c | writeBlocks(addr, bool fixedAddr, IOMemoryDescriptor*) = CMD53 write |
| 0x4c0 / 0x4c4 | c037b6c4 / c037b6d8 | setBlockSize(UInt16) / getBlockSize() (stored in the command only) |
| 0x4c8 | c037b6f4 | reset(): controller->resetFunction(fn) (full card reset + rescan) |
| 0x4cc | c037b150 | testBlockRead() (debug) |
| 0x4d0 | c037b78c | readFn0Byte(addr, UInt8*) = CMD52 read fn0 |
| IOService 0x36c/0x374/0x380 | c037b740/c037b72c/c037b758 | registerInterrupt / getInterruptType / causeInterrupt (calls handler only if +0x70 set) |

---------------------------------------------------------------------------------------------------

## 1. S5L8900 SDIO host register map (AppleS5L8900XSDIO)

The base comes from DT `reg` (provider->mapDeviceMemoryWithIndex(0), [C c0432020]). The driver logs
"registers @ vaddr, paddr". Register pointers are cached in the object at c04315f4 [C]:

| off | obj field | DESIGN name | accesses (all 32-bit) | evidence |
|---|---|---|---|---|
| 0x00 | +0x88 | CTRL | W `1` or `0x41` at init. RMW bit15 per CMD53. RMW bits 2/3 for bus width | [C c04317a4-c04317bc, c0431390-c04313ac, c0431d34-c0431d4c] |
| 0x04 | +0x8c | DCTRL | W 3 then W 0 (init and every prepareForDMA). W 0x10 in startDMA | [C c0431788-c04317a0, c0431ab4-c0431ac8, c0431af8-c0431b00] |
| 0x08 | +0x90 | CMD | W cmdword (no bit31), then RMW `|=0x80000000` once CMDRDY is set | [C c04314b8-c04314bc, c0431518-c0431524] |
| 0x0C | +0x94 | ARGU | W argument (before CMD) | [C c04314b0-c04314b4] |
| 0x10 | - | STATE | **never accessed** | [C, no pointer is made for it] |
| 0x14 | +0x98 | STAC | W 0xFFFFFFFF at init. W (value read from DSTA) after every command and in completeDMA | [C c04317c0-c04317c8, c0431564-c0431568, c0431c20-c0431c34] |
| 0x18 | +0x9c | DSTA | R polled. See bits below | [C c04314c8-c0431560, c0431c2c] |
| 0x1C | - | FSTA | **never accessed** (no PIO path) | [C] |
| 0x20-0x2C | +0xa0..+0xac | RESP0-3 | R all four words after a good command, copied to resp[0..3] | [C c0431590-c04315bc] |
| 0x30 | +0xb4 | CLKDIV | W `div>>1` once (init) | [C c0431758-c0431760] |
| 0x34 | +0xb0 | CSR | W 2 at init | [C c04317f0-c04317f8] |
| 0x38 | +0xb8 | IRQ | W 0xFFFFFFFF at init. ISR reads it and writes the same value back (W1C ack) | [C c04317cc-c04317d0, c0432110-c0432130] |
| 0x3C | +0xbc | IRQMASK (**enable** mask) | W 3 at init. startDMA clears bit1, completeDMA sets bit1. ISR tests `IRQ & IRQMASK` | [C c04317e0-c04317ec, c0431ae8-c0431af4, c0431c48-c0431c58, c0432114-c0432128] |
| 0x40 | - | - | never accessed | [C] |
| 0x44 | +0xc0 | BADDR | W physical address of the (single, contiguous) DMA segment | [C c0431aac-c0431ab0] |
| 0x48 | +0xc4 | BLKLEN | W IOSDIOCommand blockSize | [C c0431a68-c0431a7c] |
| 0x4C | +0xc8 | NUMBLK | W `length / blockSize` | [C c0431a80-c0431aa8] |
| 0x50 | - | REMBLK | never accessed | [C] |

Globals c0433110/c043310c/c0433108 keep copies of &DSTA/&STAC/&IRQMASK, and completeDMA uses them to detect
corruption of the fields ("fRegisters.dsta(%x) is corrupt") [C c0431b98-c0431c1c].

### 1.1 Bits

**CTRL (0x00)**
* bit0: set by init (`1` or `0x41`) [C]. Controller enable [I].
* bit2: set iff bus width == 4. Bits 2 and 3 are both cleared first (mask 0xC) [C c0431d44-c0431d48]. Bit3 = 8-bit mode? [I]
* bit6 (0x40): set at init only when DT `unterminated-clock` exists and its first word == 1. Otherwise CTRL=1 [C c0431eec-c0431f34, c04317a4-c04317bc]. Meaning [U] (clock/pad option).
* bit15 (0x8000): set before CMD53 if arg bit27 (block mode) else cleared. **This is only done when DT
  `chip-revision` exists and is nonzero** (flag +0xe6==0). If chip-revision is missing or 0, bit15 is never touched [C c0431e64-c0431eb4, c0431384-c04313ac]. Block-vs-byte mode [I].

**CMD (0x08)**, built in sendCommand c043134c [C]:
* bits[5:0] = command index.
* bit6 = **always set** (0x40), including commands with no response such as CMD0 and CMD52.
* bit7 = set only for CMD53 (data phase).
* bit8 = set for CMD53 when arg bit31 (R/W flag) = 1 → write.
* bits[18:16] = response type, from a jump table on idx-3 (c04313b8):
  CMD3 → 6 (R6), CMD5 → 4 (R4), CMD7 → 1 (R1b), CMD52/CMD53 → 5 (R5), anything else → 0.
* bit31 = go. It is written separately by RMW after DSTA.bit0 is seen.

**DSTA (0x18)**, from sendCommand and completeDMA [C]:
* bit0 CMDRDY: polled (<=100000 reads, no delay) **before** the go bit. On timeout it logs "Timeout waiting for CMDRDY! (cmd, cmdReg, arg)" and the command fails [C c04314c8-c0431508].
* bit4 = command complete (response received). The success exit of the post-go poll [C c043155c].
* bit15 (0x8000) = error, **no retry**: the command fails (lr=-2) [C c0431530-c0431534, c043150c].
* bit16-17 (0x30000) = error, retried (lr=-3; ARGU/CMD rewritten, up to 100 attempts) [C c0431548, c0431580].
* bit18 (0x40000) = error, retried, **ignored for CMD5** (R4 has no CRC → response-CRC error) [C c0431538-c0431544] [I CRC].
* bit22 (0x400000) = data CRC error on block **write** ("CRC Error on SDIO Block Write") [C c0431c4c].
* bit23 (0x800000) = data CRC error on block **read** [C c0431c98].
* With boot-arg `sdio-crc-panic=N` (PE_parse_boot_arg [C c0431244]), CRC errors panic.
* bit2 is never tested.

**STAC (0x14)**: the driver writes back exactly the DSTA value it read, so the semantics are write-1-to-clear [I].
An emulator should keep DSTA.bit0 asserted while idle, even after a W1C of 0xFFFFFFFF, because it is polled before every command.

**IRQ (0x38) / IRQMASK (0x3C)**, ISR c0432108 [C]:
* pending = IRQ. If (IRQ & IRQMASK)==0 → return. Else write IRQ back (ack).
* bit0 → completeCurrentCommand() (vt 0x4d4): the **DMA / data-transfer-done** interrupt.
* bit1 → handleCardInterrupt() (vt 0x4f0): the **SDIO card interrupt (DAT1)**.
* IRQMASK is an enable mask: init 3. During a DMA the card interrupt is disabled (bit1 cleared in startDMA, set again in completeDMA).

**CLKDIV (0x30)** [C c0431708]: `f = provider->getClockFrequency("hclk")`. Starting with d=2, double d while
`f/d > 25,000,000`. Then write `d>>1`. **No 400 kHz identification clock is ever programmed.** The card is
initialised at <=25 MHz. (If hclk is 0 or unknown, the value written is 1.)

### 1.2 Command issue sequence (sendCommand c043134c) [C]
1. Compose cmdword (above). If CMD53 and chip-revision!=0, adjust CTRL bit15.
2. `ARGU = arg; CMD = cmdword` (go bit clear).
3. Poll DSTA.bit0 (<=100000).
4. `CMD |= 0x80000000`.
5. Poll DSTA until bit4 (done) or an error bit (15 / 16-17 / 18 except CMD5). There is no timeout on this loop.
6. `STAC = DSTA`.
7. If an error is retryable, go back to step 2 (<=100 tries). On success copy RESP0..3 → resp[0..3].
Everything is polled. Command completion never uses the interrupt.

### 1.3 Data transfer (CMD53), DMA only, no PIO [C]
Order (family c0379cf4 → S5L overrides):
1. `prepareForDMA` c04319bc: md = cmd->getMemoryDescriptor(). It requires **one physically contiguous segment**
   ("PANIC - Cannot prepare non-contiguous DMA yet." otherwise). md->prepare(), md->performOperation(1 (flush), 0, len),
   `BLKLEN = cmd.blockSize; NUMBLK = len / blockSize; BADDR = phys;` then `DCTRL = 3; DCTRL = 0`.
   (A 4 KB bounce buffer is allocated in start() but is never used for transfers [I].)
2. If blockSize > 1: setBlockSize(fn, blockSize) = **two CMD52 writes to FBR (fn*0x100+0x10, +0x11) before every block CMD53**.
3. CMD53 arg = `W<<31 | fn<<28 | (blk>1)<<27 | (!fixed)<<26 | (addr&0x1ffff)<<9 | (count&0x1ff)` with
   count = len/blk (block mode) or len (byte mode, blk<=1). Sent with sendCommand (waits for DSTA.bit4).
4. `startDMA` c0431ae8: `IRQMASK &= ~2; DCTRL = 0x10;` sets flag +0xe7. Returns, so the transfer is asynchronous.
5. IRQ bit0 → completeCurrentCommand → `completeDMA` c0431b4c: `STAC = DSTA`, md->complete(),
   `IRQMASK |= 2`, check DSTA bits 22/23. Then commandWakeup on the command gate.

The Marvell driver only ever uses **block mode**: blk 32 during firmware download, 320 at runtime. Byte mode
(blk<=1 → BLKLEN=1, NUMBLK=len) exists in the code but is unused by AppleMRVL868x [C/I].

### 1.4 Host init / power-on (setPowerStateGated c043219c → hwinit c0431778) [C]
Power-on: `provider->enableDeviceClock(1,0)` (AppleARMIODevice vt 0x4b8, i.e. the DT clock gate), then:
1. CLKDIV (1.1).
2. `DCTRL=3; DCTRL=0`.
3. `CTRL = unterminated-clock==1 ? 0x41 : 0x01`.
4. `STAC = 0xFFFFFFFF; IRQ = 0xFFFFFFFF`.
5. resetDevices() c043188c (card reset pulse, 1.5).
6. `IRQMASK = 3; CSR = 2`.
7. IOSDIOController::setupAndScanBus() (section 2). On failure it logs "Unable to setup and scan the SDIO bus."
8. Enable the controller's IOInterruptEventSource (DT interrupt index 0).
Power-off: if a command is in flight it defers (returns 1 s). Else enableDeviceClock(0,0) and the interrupt source is disabled.
start() c0431e34 does not touch the hardware. It maps registers, finds the reset function, and registers PM with 2 power states.

### 1.5 What the controller needs from the device tree [C unless noted]
* name/compatible `sdio,s5l8900x`, provider AppleARMIODevice.
* `reg` (index 0, >=0x50 bytes) and `interrupts` (index 0). The VIC line is **not in the kernel** [U]. DESIGN's 0x2A is from openiBoot.
* A clock gate usable by `enableDeviceClock`, and a clock named `hclk` for getClockFrequency [U where it lives in DT].
* **`function-device_reset` (mandatory**: "Unable to find reset function phandle." makes start fail). It is parsed by
  AppleARMFunction::withProvider c0159470: data >= 8 bytes, word0 = phandle of a function parent. For the GPIO
  controller (AppleS5L8900XGPIOIC registers itself as a function parent, c040c54c) the data is
  `<phandle, 'GPIO'(0x4750494f), pin (port<<8|bit, port<=0x18, bit<=7), byte flags[4]>` [C c040d42c, c040cf20-c040cf38].
  callFunction(&v): output = v XOR flags[1](polarity) XOR 1 [C c040ce1c-c040ce40]. So "1" means logically asserted.
  The actual WiFi reset GPIO number is only in the DT [U].
* Reset pulse c043188c [C]: call(0); IOSleep(1); interrupts off + spinlock; call(1); IODelay(5); call(0);
  unlock; IOSleep(100). That is: assert reset for ~5 µs, then wait 100 ms.
* Optional `chip-revision` (OSData u32, nonzero → CTRL bit15 management) and `unterminated-clock` (u32 == 1 → CTRL bit6).
* There are **no PMU/LDO/"power" GPIO calls** in AppleS5L8900XSDIO or AppleMRVL868x. Card power is assumed on,
  or handled by DT/firmware elsewhere [C for absence in these kexts. Whether something else powers WiFi: U].

---------------------------------------------------------------------------------------------------

## 2. Card init (IOSDIOController::setupAndScanBus c03798bc) [C]

Every step is synchronous via sendCommand:
1. **CMD5 arg 0** (R4).
2. **CMD5 arg 0x00060000** (OCR bits 17,18 = 2.9-3.1 V). This is sent **once**. There is **no loop on the OCR ready (C) bit**
   and the response contents are not checked.
3. **CMD3 arg 0** (R6). RCA = RESP0 & 0xFFFF0000.
4. **CMD7 arg RCA** (R1b).
   If any of these fails: "Failed to perform base SDIO init sequence." **CMD0 is never sent** (all 7 sendCommand call
   sites were enumerated: CMD52 x2, CMD5, CMD5, CMD3, CMD7, CMD53).
5. readCCCR c0379780: CMD52 reads of fn0 0x00 (CCCR/SDIO rev nibbles), 0x08 (card caps), 0x09/0x0A/0x0B (CIS ptr,
   assembled **big-endian** `b9<<16|bA<<8|bB`, a bug, but it is only printed). Only logged.
6. setBusWidth(4) → CMD52 write fn0 **0x07 = 0x82** (4-bit + CD-disable). Then host CTRL bit2 set. (Other widths: 0x80 and 1-bit.)
7. setBlockSize(fn0, 4) → CMD52 write fn0 **0x10 = 0x04, 0x11 = 0x00**.
8. CMD52 write fn0 **0x04 (IEN) = 0x01** (master enable only).
9. scanFunctions c0379b54 (only if no nubs exist yet): for fn = 1..7 CMD52 read fn0 **fn*0x100 + 0x00** (FBR standard
   interface code). **If (value & 0x0F) != 0, create `SDIODeviceNub(ctrl, fn)`**, attach it to the controller, and call registerService().

**What makes an IOSDIODevice appear:** only step 9. The nub is not a device-tree child, and no CIS or MANFID tuple is ever read
by the family. AppleMRVL868x then matches on IOProviderClass alone. **An emulated card therefore needs FBR1[0x100]
low nibble != 0** (e.g. 0x07 "WLAN" or any nonzero value) [C].

Card interrupt path [C c0379adc]: IRQ bit1 → CMD52 read fn0 0x05 (INT pending). For each fn bit set,
nub->causeInterrupt(0), which reaches the driver's IOInterruptEventSource only if the device's enableInterrupt(true) flag is set.
Nothing is written back to the CCCR. Clearing the source is the function driver's job.

`/dev/sdio` char device (cdevsw_add + devfs "sdio", c0379318) offers ioctl CMD52/CMD53 access for debugging (c037a598).
That ioctl path contains its own enable sequence (CCCR2 |= 2, poll CCCR3) [C]. It is not used at boot.

---------------------------------------------------------------------------------------------------

## 3. AppleMRVL868x (Marvell 88W8686) expectations

### 3.1 start() c0381b74 / init c037f90c [C]
* Provider must cast to IOSDIODevice (stored +0x2e0).
* Properties come from the provider via `getProperty(name, gIOServicePlane, 3)`, which searches recursively up through the parents. The nub has
  no DT node, so in practice they come from the **sdio DT node** (or above) [I]:
  * **`local-mac-address`** (OSData, 6 bytes, must not be all zero: "MAC Address is all 00's." makes start fail). Mandatory.
  * **`tx-calibration`** (OSData). Optional ("No Calibration Data Found!" only logs). If present, the first 128 bytes must not
    be all 0x00 or all 0xFF ("Invalid calibration data in device tree." makes start fail) [C c03809e8]. Later **0x400 bytes**
    are copied from it into host command 0x006D [C c0383bd8], so provide >= 1024 bytes.
* `marvel-debug`, `marvell-feature-flags`, `marvell-retry-limit`, `marvell-inactivity-timeout`,
  `marvell-powersave-timeout`, `marvell-powersave-listenint`, `marvell-sdio-pulldowns`, `marvell-ps-confirm-delay`,
  `WTE` are **boot-args** (PE_parse_boot_arg), **not device-tree properties** [C c037f9a8-c037fac4].
* Firmware blobs are **embedded in the kext's __data** (the root FS is not touched):
  SD8686_HELPER 0x85C = 2140 bytes @ va c03b0c0c (file 0x389c0c);
  SD8686_FIRMWARE 0x1DEF0 = 122608 bytes @ va c0392d1c (file 0x36bd1c) [C c037f948-c037f958, c03802f0, c038a80c].
* Buffers: 0x404-byte contiguous FW buffer, used at +4 (`+0x2f4` virt, `+0x2f8` phys). RX buffer max 0x1000 (`+0x2f0`).
* Interrupt sources: +0x31c on the IOSDIODevice → interruptOccurred c038a418. +0x320 has no provider and is
  triggered by setPowerStateGated(on) → **powerOnSequence c0382640** (firmware load runs on the workloop). Timers +0x324/+0x328.

### 3.2 FN1 register usage (all CMD52 on function 1) [C]
Names come from Apple's own dump c0380a98 where it has one:

| addr | Apple name / use | access |
|---|---|---|
| 0x00-0x02 | IO port address (24-bit LE) | R once before download; stored +0x2e4 [C c038a9f8-c038aa2c] |
| 0x03 | "Configuration Register" | W 0x02 before download and on wake from deep sleep [C c038ab70, c0382350]. On a status-wait failure: R, W |0x04, W orig [C c0382cac-c0382cf4]. (mwifiex names: bit1 HOST_POWER_UP, bit2 HOST_TERM_CMD53 [I]) |
| 0x04 | "Host Interrupt Mask" | W 0 before download, W **0x03** after FW ready [C c038ab48, c038abc0] |
| 0x05 | "Host Interrupt Status" | R in ISR. Ack by **W (~status & 3)** [C c038a450-c038a4e8] |
| 0x06 | - | R once after SCRATCH==0xFEDC (value discarded) [C c038020c] |
| 0x10-0x11 | RD_BASE / "transmit length from helper" (16-bit LE) | R (helper-done poll, per-chunk length) [C c038a724-c038a760, c038a888-c038a8c4] |
| 0x20 | "Card Status Register": bit3 IO ready, bit2 CIS ready, bit1 UL ready, bit0 DL ready [C c0381b24-c0381b4c] | R polled |
| 0x24 / 0x28 | "Card Interrupt Mask" / "Card Interrupt Status" | debug dump only |
| 0x30-0x33 | "Register 0x30..0x33" | debug dump only [U] |
| 0x34-0x35 | SCRATCH (16-bit LE) | **0xFEDC = firmware running** [C c03801dc], and **at runtime = RX packet length** [C c038a1b8] |

CCCR (fn0) used by the driver: 0x04 (IEN, via enableInterrupt and the debug dump), 0x05 (INT pending, debug dump).

### 3.3 Firmware load (loadFirmware c038aaf4, called from powerOnSequence) [C]
Retry loop: on failure log "Failed to load firmware, trying again." and call provider->reset() (full GPIO reset + rescan) [C c038ab14-c038ab28].
1. enableFunction(true) (CCCR2 |= 0x02, poll CCCR3 bit1).
2. FN1 W 0x04 = 0 (host int mask).
3. enableInterrupt(false) (CCCR4 &= ~0x02).
4. FN1 W 0x03 = 0x02.
5. c038a9ac: two dummy reads FN1 0x10, 0x11. IOport = R 0x00 | R 0x01<<8 | R 0x02<<16.
   R 0x20: **error only if (status & 8)==0 && (status & 1)** ("device is not ready"). Otherwise continue.
6. **Helper ("Bootstrapper")** c0380234: setBlockSize(32). For off = 0 .. 2139 step 60: buf = `u32 LE chunkLen (<=60)`
   + chunk bytes, written as **one CMD53 write of 64 bytes = 2 blocks x 32, fixed address, to IOport**. There is
   **no card-status polling between chunks**. The last chunk is 40 bytes. Then a **single 32-byte all-zero block**, then
   **IOSleep(1000 ms)**.
7. **Main program** c038a70c:
   * Poll RD_BASE (0x10/0x11) up to **100 times without delay** until nonzero, else "No transmit length from helper image.". So it must be
     nonzero immediately after the 1 s sleep.
   * setBlockSize(32). Loop while `offset <= 0x1DEEF` and `1 <= len <= 0x800` (len==0 or len>0x800 ends the loop as **success**):
     * wait STATUS & 0x09 == 0x09 (100 reads, 1 ms sleep every 20). Failure is only logged.
     * copy `len` bytes from the image at offset (no clamp to the remaining size), then **one CMD53 write of
       roundup(len,32) bytes in 32-byte blocks**, fixed address, IOport. offset += len.
     * poll STATUS until bit3 && bit0 (<=1000 reads) ("Card did not become ready").
     * len = RD_BASE. **len & 1 → "Helper image reports CRC error"** → fail.
8. Verify c03801b4: poll SCRATCH (0x34/0x35) == **0xFEDC**, up to 100 x IOSleep(1). Then R FN1 0x06.
9. After success: enable the interrupt source and timers. `enableInterrupt(true)` (CCCR4 |= 0x02).
   FN1 W **0x04 = 0x03**. **setBlockSize(0x140 = 320)**.
   Then (c038ac10): if boot-arg marvell-sdio-pulldowns, SDIO_PULL_CTRL. configureHostSleep(6). Start the command queue.
powerOnSequence then continues: IOMACAddress from local-mac-address, setHardwareAddress (0x4D),
GET_HW_SPEC (0x03), CAL data (0x6D, if tx-calibration), MAC_CONTROL (0x28, 3), RADIO_CONTROL (0x1C, 5),
RF reg writes (0x1B) (0x50←0x50, 0x6B←0xAE), several MAC reg writes (0x19) (0xA240, 0xA5AC, 0xA5B0, 0xA5A8, 0xA5B4,
0xA5A4, 0xA58C, 0xA5A0, 0xA5F0), SNMP_MIB (0x16), INACTIVITY_TIMEOUT (0x67), subscribe events, TX_POWER (0x1E) [C c0382640-c0382834].
Command codes are the first u16 written to the command body [C for 0x03/0x28/0x1C/0x19/0x1B/0x4D/0x6D; others from a
heuristic constant scan, I].

### 3.4 Runtime transport [C]
* **Card→host**: ISR c038a418: R FN1 0x05. If 0, sleep 100 ms, re-read, and log "Pointless Interrupt". Otherwise W 0x05 = ~s & 3.
  * bit0 (UPLD) → receiveResponse c038a1a8: len = SCRATCH16 (0x34). len < 4 means invalid. xfer = ceil(len/320)*320
    (capped to 4096). Wait STATUS bit3. setBlockSize(320). **CMD53 read, fixed address, IOport, 320-byte blocks** into an mbuf.
    Packet = `u16 len, u16 type, payload`. type **0 = data** (RxPD), **1 = command response**, **3 = event**, other → "Unknown packet type".
  * bit1 (DNLD) → "download ready" flag (+0x627). The next queued TX may go.
* **Host→card**: issueCommand c0382d34: wait STATUS bit3 (IO ready), clear the DNLD flag, **CMD53 write, fixed, IOport,
  length = ceil((len+4)/320)*320**, header `u16 len+4, u16 type (0 data / 1 cmd)` built in c038075c.
  Command timeout: 5 s (ASSOCIATE 2 s).
* Command header `u16 cmd, u16 size, u16 seq, u16 result`. The response is matched as a command packet (handleCommandPacket c0387ca4).
* TxPD (24 bytes, c03856f4): u32 status=0, u32 control=0, **u32 pkt offset = 0x18**, u16 pkt len, dest MAC[6] (copied
  from the frame), then the **Ethernet II frame as given by the stack** (no SNAP conversion on TX).
* RxPD (c0380448): u16 status @0, u8 snr @2, u8 control @3, u16 pkt_len @4, u8 nf @6, u8 rx_rate @7,
  **u32 pkt offset @8 (relative to RxPD)**, priority @0x10. If the frame at the offset has AA AA 03 at bytes 14..16 (802.3+LLC/SNAP),
  the driver converts it to Ethernet II. Otherwise it passes the frame through, so an emulator can deliver plain Ethernet II.
* Events (handleEventPacket c0387404): code = **payload[0] (u8)**. Handled codes: 3 link lost, 4 ad-hoc link sense,
  8/9 deauth/disassoc, 0xA awake, 0xB sleep, 0xD/0xE MIC error (group/pairwise), 0x10/0x19/0x1A/0x1C/0x1D RSSI/SNR,
  0x11 ad-hoc beacon lost, 0x12 host awake, 0x18 BG scan report. Others (including 48) log "Unhandled event type".
* Watchdog: timerHandler / invokeTheHandOfGod re-reads the interrupt status on a missed interrupt. On a chip failure it calls provider->reset().

---------------------------------------------------------------------------------------------------

## 4. Cross-check against wifi-design/DESIGN.md

| DESIGN claim | kernel says | verdict |
|---|---|---|
| Base 0x38D00000, clk gate 0x0B, IRQ 0x2A | Not in the kernel. Everything comes from DT `reg`/`interrupts`/clock gate | **unverifiable here [U]** |
| CTRL bit0 enable, bit2 4-bit, bit15 block mode | bit0/bit2 OK. bit15 only managed when DT `chip-revision`!=0. Also bit6 (unterminated-clock) and bit3 (cleared with bit2) | partly agrees |
| DCTRL 3 then 0 = FIFO reset, 0x10 = start | Same sequence (init + every prepareForDMA; 0x10 in startDMA) | agrees (meaning [I]) |
| CMD: bit6 resp present, bits7-8 resp-len code, bit8 write, [18:16] resp type, bit31 exec | bit6 is **always** set (even CMD0-style/none); bit7 = data command (CMD53); bit8 = write; [18:16] = R-type number (R1=1, R4=4, R5=5, R6=6, none=0); bit31 = go, set by RMW after CMDRDY | **disagrees on bits 6/7** |
| STATE 0x10 cmd FSM busy | Never read | n/a (unused) |
| DSTA bit0 cmd-ready, bit2 cmd-sent, [18:15] error code | bit0 = CMDRDY (polled before go) OK; **bit4 = command done** (bit2 unused); bit15 fatal, bits16-17 retryable, bit18 resp-CRC (ignored on CMD5); **bits22/23 = write/read data CRC** | **disagrees** |
| STAC write DSTA (or ~0) to ack | Same | agrees |
| FSTA unused | Unused | agrees |
| CLKDIV 1<<7 ~400 kHz init, 1<<1 ~25 MHz | Only one write: `d>>1` with hclk/d <= 25 MHz. **No 400 kHz phase** | **disagrees** (no slow init clock) |
| CSR written 2 | Written 2 after the reset pulse | agrees |
| IRQ bit0 data done, bit1 card irq, write-back ack | Same | agrees |
| IRQMASK init 3 | 3. It is an enable mask, and bit1 is toggled off during each DMA | agrees + detail |
| BADDR/BLKLEN/NUMBLK, REMBLK | BADDR/BLKLEN/NUMBLK written in prepareForDMA (NUMBLK = len/blk). REMBLK never read | agrees |
| Data via DMA, no FIFO | DMA only, single contiguous segment, completion by IRQ bit0 (not polling) | agrees; **OS uses interrupt-driven completion** |
| CMD52/53 arg layout | Same. RAW bit never used. bit26 set unless fixed address. Marvell uses fixed address | agrees |
| Init: CMD5 probe, voltage loop, CMD3, CMD7, CCCR reads, bus width, CIS read, per-function block size | CMD5(0) then CMD5(0x60000) **once (no loop)**; CMD3; CMD7; CCCR 0/8/9-B reads; CCCR7=0x82; **fn0 blocksize 4**; IEN=1; **no CIS read**; FBR scan by interface code. No CMD0 | **partly disagrees** |
| SDIO nub matching via CIS manufacturer/function | Nub created when FBR[fn].0 & 0xF != 0. Driver matches IOProviderClass only | **disagrees** |
| F1 map (IOPORT 0-2, 0x04/0x05, 0x10-11, 0x20 bits, 0x24/0x28, 0x34 SCRATCH) | All confirmed. Also 0x03 config reg and 0x06 are used | agrees + additions |
| "For 8686 the received length is the header length" | RX length is read from **SCRATCH 0x34/0x35** before every upload | **disagrees** |
| H_INT ack: host writes ~cause | Writes `~cause & 3` | agrees |
| Helper: wait IO_RDY\|DL_RDY per 60-byte chunk, 64-byte writes, finish with 64-byte zero block, poll RD_BASE | 64-byte writes (2x32 blocks) of `len32+60 bytes` **without any status polling**. Finish with a **32-byte** zero block, then **sleep 1 s**. RD_BASE then polled 100x with no delay | **partly disagrees** |
| Main FW: n==0 abort, n&1 error, min(n,remaining) padded to 32 in <=512-byte writes | n&1 = CRC error (fail). n==0 or n>0x800 **ends the loop as success**. Sends n bytes (no min) padded to 32 in **one** CMD53 (up to 2048 bytes = 64 blocks). Status 0x09 checked before and after each chunk | **disagrees on 0 and on chunking** |
| Then poll SCRATCH == 0xFEDC | 100 x 1 ms, then read reg 0x06 | agrees |
| Block size 256, H_INT_MASK = 0x0F | **Block size 320, H_INT_MASK = 0x03** | **disagrees** |
| Framing `u16 len, u16 type`, padded to block size | Same, padded to 320. Types 0 data, 1 cmd, 3 event | agrees |
| Blobs in root FS? [?] | **Embedded in the kext** (helper 2140 B, fw 122608 B) | resolved |
| Send FIRMWARE_READY(48) | Not handled (would log "Unhandled event type") | unnecessary |
| Event `u32` code | Driver reads only payload[0] (u8) | compatible |
| TX descriptor 24 B, RX 20 B with pkt_ptr | TxPD 24 B with offset 0x18. RxPD with u32 offset @8. SNAP→EthII conversion on RX only | agrees |
| Power/reset GPIO unknown | Reset via DT `function-device_reset` (GPIO platform function: phandle,'GPIO',pin,flags). Pulse 5 µs, then 100 ms wait. No power GPIO in these kexts | pin still DT-only [U] |

## 5. Emulator checklist distilled from the above
* DSTA: keep bit0 set when idle. Set bit4 when a command completes. Never set error bits unless you mean it.
  For CMD5 return any OCR (no ready loop); for CMD3 RCA in RESP0[31:16]; for CMD52 data in RESP0[7:0].
* Execute only on the CMD write that has bit31 set.
* CMD53: the transfer can be done at the CMD53 or at `DCTRL=0x10`, but raise IRQ bit0 only after `DCTRL=0x10`
  (IRQMASK bit0 enabled). Direction = CMD bit8 / arg bit31. Size = BLKLEN*NUMBLK. Address = BADDR (physical).
* Card IRQ: assert IRQ bit1 when the card's H_INT_STATUS & H_INT_MASK != 0, CCCR IEN has bit0 and bit1, and IRQMASK bit1
  is set. Note IRQMASK bit1 is cleared during every DMA, so re-check when it is set again [I].
* FBR1 byte 0x100 low nibble nonzero. CCCR2/3 enable handshake (IOR bit1 must follow IOE).
* FN1: IOport at 0-2. STATUS 0x20 = 0x0F (or 0x09+) during download. RD_BASE nonzero right after the helper zero block,
  even values <= 0x800 for each request, 0 when done. SCRATCH 0xFEDC after download, then SCRATCH = pending RX length at runtime.
* DT: `sdio,s5l8900x` node with reg/interrupts/clock, `function-device_reset` (GPIO form), `local-mac-address` (nonzero),
  optional `tx-calibration` (>=1024 B, not all 00/FF), optional `chip-revision`/`unterminated-clock`.
