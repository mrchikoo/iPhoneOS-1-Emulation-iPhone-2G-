# IRQ 42 (VIC1 bit 10, SDIO): how to assert it from a device

Tags: [c] confirmed (read in emu.wat and/or seen in a test run), [i] inferred, [u] unknown.

## Result

- [c] A device can raise and lower line 42 by setting **VIC1 RAW bit 10** with the core's own setter (func 20), then recomputing the CPU IRQ/FIQ bytes. In a cold boot the guest's IRQ handler took the exception and read `VECTADDR = 0x8000002a` (line 42). All 15 checks in `irq-test.mjs` pass.
- [c] **The guest enables VIC1 bit 10 by itself.** VIC1 INTENABLE goes from 0x40183 to 0x40583 at about 170.81M instructions, in the instruction at PC 0xc02c3af0. This happens right after the serial line `AppleS5L8900XSDIO: registers @ vaddr 0xf1909000, paddr 0x38d00000` and before `setPowerStateGated`. The bit is still set at the end of boot, even though SDIO init fails (`Unable to setup and scan the SDIO bus`). So a device model does not need to force the mask.
  - [i] The write is done by AppleS5L8900XSDIO::start through its interrupt event source, using the PL192 VIC driver's enable routine (0xc02c3af0 sits next to the IRQ handler at 0xc02c3acc). Strictly it is the AppleS5L8900XSDIO host driver, not IOSDIOFamily itself. Evidence is the timing against the serial log and the `Unable to get interrupt source.` string in that driver. No call-stack proof.
- [c] The polled table at data 22576 (func 208, func 160) is **not needed** for delivery and is not touched. See the next section.
- [c] With the level at 0 the patched core matches the original exactly over 400M cold-boot instructions: same ticks, cycles, serial, framebuffer, CPU struct and VIC bytes (`equiv-test.mjs`).

## How the core delivers interrupts (all [c] unless marked)

| What | Where |
|---|---|
| machine struct | static at 23560 (func 233 passes `i32.const 23560`) |
| VIC0 / VIC1 state | machine+84200 / +84216, 16 B each: `+0 RAW`, `+4 INTENABLE`, `+8 INTSELECT (1=FIQ)`, `+12 SOFTINT` |
| set one RAW bit | func 20 `(vic, bit, level)`. This is the only writer of RAW (apart from snapshot load, func 155) |
| guest VIC writes | func 144 (from func 86, 32-bit only): 0x0C SEL=, 0x10 EN\|=, 0x14 EN&=~, 0x18 SOFT\|=, 0x1C SOFT&=~. The guest cannot write RAW |
| guest VIC reads | func 145. 0xF00 VECTADDR is func 97: lowest pending `EN&(RAW\|SOFT)&~SEL` bit, then `\|0x80000000`. VIC0's VECTADDR falls through to VIC1 (+32) |
| device tick | func 19 `(machine, cycles)`. It drives every built-in device line through func 20 (level-triggered, every call), then computes `machine+272` (abs 23832) **IRQ** = any(EN&(RAW\|SOFT)&~SEL) over both VICs, and `machine+273` (23833) **FIQ** = same with `&SEL` |
| CPU takes IRQ | func 80 (interpreter step), at an instruction boundary: if byte 23832 is set and CPSR.I is clear, it switches to mode 0x12 with LR=PC+4 and jumps to 0x18 or 0xFFFF0018. func 233 (emu_run) checks the same bytes |
| re-evaluation timing | func 19 runs many times per emu_run slice (func 233 / func 54 / func 26). func 208 also calls `func 19(m,0)` on entry |
| line 42 in the original | nothing drives VIC1 bit 10. Built-in lines are 7, 9-13, 16, 17, 21, 22, 28, plus the GPIO groups 0-3 and 31-33 (table at data 17184) |

**Why the polled table (data 22576) is not the IRQ path:** func 208 walks those 15 rows (timer, clcd, tvout, spi0/1, gpio-group0-6, uart4-rx, dmac0/1). For each row whose line is enabled in the VIC it calls the handler, which returns the *time until that device's next event*. func 208 takes the minimum and uses it to fast-forward an idle CPU. It never drives a line. A device whose interrupts come from host-side or MMIO actions (as here) needs no row there. [i] A row would only be needed if the SDIO model scheduled interrupts for a future guest time while the CPU idles. In that case, raise from the host instead, or keep the run slices short.

## The patch (`irq_patch.wat`, `irq_patch.py`)

- `$sdio_irq_level` is a new global, placed in the text right after original global 0 (the module has only global 0), so no index changes.
- `$sdio_raise_irq` / `$sdio_lower_irq` call `$sdio_set_irq(level)`. That sets the global, runs `call 20 (107776 = VIC1 RAW, 10, level)`, then `$vic_irq_reeval`.
- `$vic_irq_reeval` is a copy of func 19's line formula. It writes only bytes 23832 and 23833, depends only on VIC state, and func 19 rewrites the same values on its next tick. So it is safe to call at any point: from the host between `emu_run` calls, or from inside an SDIO MMIO hook. In the hook case the CPU sees the line at the next instruction boundary.
- The func 19 hook is one call, `func 20(VIC1, 10, $sdio_irq_level)`, placed before the IRQ/FIQ computation. Like the built-in devices, it re-drives the bit every tick, so RAW can never be stale (for example after loading a snapshot taken while the line was high). It is not needed for delivery itself. `apply_func19_hook(wat)` is importable on its own; `patch/patch.py` already imports it.
- `$sdio_irq_state(idx)` returns, by idx: 0 level, 1-4 VIC1 RAW/EN/SEL/SOFT, 5 IRQ byte, 6 FIQ byte, 7-8 VIC0 RAW/EN, 9 version `0x49525131`.
- Exports: `sdio_raise_irq`, `sdio_lower_irq`, `vic_irq_reeval`, `sdio_irq_state`. No original function, global or export changes. The original sha256 is checked, the build uses wat2wasm with default features, and the result passes wasm-validate.
- Snapshots: [c] the level global is not saved. RAW is saved, but func 19 re-drives it from the global. After a restore the device model must re-raise the line if it is still asserted.

Build:
```sh
cd "re-irq"
python3 irq_patch.py                                                     # out/emu-irq.wasm
python3 irq_patch.py --wat ../patch/out/emu-net.wat --out-name emu-net-irq   # stack on an emu-net build (strips its empty placeholder stubs)
node --max-old-space-size=6000 irq-test.mjs        # cold boot 1.5e9 insns + IRQ tests, ~26 s; out/irq-test.json, out/irq-test.log
node --max-old-space-size=6000 vic-trace.mjs       # exact instruction that enables line 42
node --max-old-space-size=8000 equiv-test.mjs      # level 0 == original (400M insns)
```
`patch/patch.py` (maintained separately) now includes `irq_patch.wat` verbatim, moves its `(global $...` line after global 0, and calls `apply_func19_hook`. I checked that it builds and validates (output went to a scratch dir; nothing in `patch/` was touched).

## Test evidence (`irq-test.mjs`, out/emu-irq.wasm, 1.5e9-instruction cold boot)

- A. Before the guest runs: raise sets RAW to 0x400, the CPU line stays 0 because EN1=0 (masked), and lower clears it. [c]
- B. Boot: the SDIO probe runs, and RAW bit 10 is never set while the level is 0. The VIC enable log is in out/irq-test.json. At the end, EN0=0x821c389f, EN1=0x40583. [c]
- C. Raise after boot (guest-enabled, not forced): the IRQ byte is 1 immediately (it was 0 before the raise). After 633 single steps the CPU went from svc (CPSR 0x80000113, PC 0xc02c3af4) to IRQ mode (CPSR 0x192) at vector 0xFFFF0018, with LR_irq 0xc02c3af8. The kernel's first VECTADDR read returned `0x8000002a` (`[mmio] ... R 0x38e00f00 = 0x8000002a pc=0xc02c3acc`). [c]
- Holding the level high with no device-side acknowledge gives an interrupt storm: about 4500 vector-42 reads in 5M instructions. At the end of the 1.5e9 run the guest had cleared EN1 bit 10 (0x40183). The 600M run did not show that. [c] [i] This matches IOInterruptEventSource's disable-at-dispatch, re-enable-after-action behaviour. Either way, the device model must drop the line when the guest acknowledges it (for example when it writes the SDIO IRQ status register, 0x38).
- D. After lower, the guest runs another 100M instructions with no panic and ticks advancing. [c]
- E. The func 19 hook re-drives RAW bit 10 high or low from the level after RAW was tampered with directly. [c]
- The same 15/15 pass on `out/emu-net-irq.wasm` (stacked on the then-current emu-net build with its SDIO model, 600M boot). [c]

## Open points

- [u] When the real SDIO host asserts line 42: for which IRQ/IRQMASK (0x38/0x3c) bits, and whether card interrupts (DAT1) share it. This has to be taken from AppleS5L8900XSDIO. The `patch/sdio_model.wat` work in progress drives `$sdio_raise_irq` from `IRQ & IRQMASK`.
- [u] Not tested with the seed JIT. The JIT is bound to the original module hash, so the patched module runs in the interpreter. Not tested in the browser worker.
- [i] Idle (WFI) case: a host raise between slices is picked up because func 208 calls func 19 first and returns when the IRQ byte is set. This was not separately exercised with the CPU parked in WFI.
