# RE summary: 2007.mobile emulator core ("S5LBox")

Condensed from the reverse-engineering agent's report. [c]=confirmed from binary, [i]=inferred, [u]=unknown.

- emu.wasm: Emscripten C, all symbols stripped, 255,172 bytes, 8 JS imports (no network, no device callbacks). [c]
- Machine = one static struct at linear address 23560. Changing its layout breaks the seed-JIT ABI (jit/abi.json). [c]
- CPU: ARM1176JZF-S (ARMv6, VFPv2, MMU, ARM+Thumb). Board: iPhone1,1 / M68AP / S5L8900, iPhone OS 1.0 build 1A543a, rootfs is a RAM disk. [c]
- No SDIO device: SDIO is at phys 0x38D00000, IRQ 42 (VIC1 bit 10), in the device tree with no children. Unmapped reads return 0, writes dropped. [c]
- Guest kernel has IOSDIOFamily, AppleS5L8900XSDIO, AppleMRVL868x (SD8686 helper/firmware strings). So the guest expects WiFi. [c]
- Bus read = func 85, bus write = func 86. Add a branch for [0x38D00000,0x38D01000) before the stub scan. Also register in func 87 overlap list. [c]
- IRQs are polled via a 15-row table at data addr 22576 (loop bound literal 15 in func 208, validated in func 160). Adding IRQ 42 means relocating the table. [i]
- Bridge: add exports emu_net_tx_pop / emu_net_rx_push (pull rings, like serial/audio) and drainNet() in emu-worker.js. [i]
- Patch method: wasm2wat -> edit -> wat2wasm validated (wabt installed via brew). Patched module not yet run. [c]
- Risks: existing snapshot (taken when SDIO read as 0) likely won't re-probe WiFi, needs fresh boot; snapshot format changes invalidate saves; seed JIT can't do MMIO (falls back to interpreter, fine). [i]
- Unknown: SDIO host register map (must be taken from AppleS5L8900XSDIO in the kernel), whether driver needs DT child/GPIO/PMU power, where Marvell firmware lives. [u]

Files in this directory: emu.wat, f85/f86/f87/f160.wat, data.bin, dt.py, kstr.txt, kernel and devicetree.
