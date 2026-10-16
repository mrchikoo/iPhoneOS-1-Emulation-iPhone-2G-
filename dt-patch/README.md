# dt-patch: WiFi-capable device tree + `?cold=1` browser cold boot

All commands run from `dt-patch`. `../mirror/` is never modified.

## a) Device tree: `dtpatch.py` -> `devicetree_m68ap_wifi.bin`
```sh
python3 -I dtpatch.py            # reads ../mirror/fw/devicetree_m68ap.bin, writes devicetree_m68ap_wifi.bin (36864 B, same size)
python3 -I dtpatch.py --mac 02:00:00:20:07:01 --pin 6.7 --force-pin --tx-cal FILE   # optional overrides
python3 -I ../re-core/dt.py devicetree_m68ap_wifi.bin    # reparse with the project parser
```
What it does to the `sdio` node (`/device-tree/arm-io/sdio`) only:
* `function-device_reset`: the original DT **already has it**: `<0x00404040 'GPIO' 0x0607 flags 01 01 01 00>` = GPIO port 6 bit 7,
  phandle = `/arm-io/gpio` `AAPL,phandle`, same 16-byte form every other node uses (checked: all 53 `function-*` GPIO props use that phandle, pin = port<<8|bit).
  Pin 0x0607 is used by no other function. Kept as is (`--force-pin` rewrites it).
* `local-mac-address`: was present but all zero (driver refuses it) -> `02:00:00:20:07:01` (locally administered, non-zero).
* `tx-calibration`: was a 1024-byte all-zero placeholder, which makes `start()` fail ("Invalid calibration data"); removed (property is optional,
  absence only logs). `--tx-cal FILE` supplies real data instead.
Checks run on every invocation: serializer round-trips the original byte-exact; output reparses; node/prop set identical except sdio; every
non-sdio node identical (props + structure); `dt.py` dump identical to the original's. Output is deterministic (verified by rerun + cmp).

## b) Node cold-boot comparison
```sh
node --max-old-space-size=4096 coldtest.mjs ../mirror/fw/devicetree_m68ap.bin s_orig.txt 9000000000
node --max-old-space-size=4096 coldtest.mjs devicetree_m68ap_wifi.bin         s_wifi.txt 9000000000
python3 cmpserial.py s_orig.txt s_wifi.txt     # normalizes 6-8 digit hex (heap addresses)
```
(uses `../re-trace/rootfs.img` = gunzip of the mirror rootfs parts, sha256 73d0d2a0...; same init sequence as `re-trace/run.mjs`, hostclock 0.)
Result (9e9 insns, reaching userland/SpringBoard): `emu_init` returns 0 for both; 112 serial lines each, 7121 bytes each; zero lines differ after
normalizing hex (the only raw differences are kernel heap addresses, shifted because the DT is 1060 bytes smaller, plus ordering of the
PMUPowerSource lines in a shorter 3e8 run). `serial_orig.txt` / `serial_wifi.txt` are these outputs.
Note: the guest still logs "Failed to perform base SDIO init sequence" in both, since no SDIO card is emulated (patch/ adds the register window);
whether the DT patch changes start() behaviour of the SDIO/MRVL kexts is NOT visible in this serial (no difference at all), so it is unverified until an SDIO device model exists.

## c) Browser: `site/` (`?cold=1`)
```sh
python3 ../mirror/serve.py 8131 site      # then open http://localhost:8131/?cold=1   (normal URL = unchanged snapshot boot)
```
`site/` = symlinks to everything in `../mirror` except real copies of `app.js`, `emu-worker.js`, `manifest.json`, and `fw/` (symlinks + `devicetree_m68ap_wifi.bin`).
Diffs vs mirror: `diff ../mirror/app.js site/app.js` (16 lines), `diff ../mirror/emu-worker.js site/emu-worker.js`, manifest adds `devicetreeWifi` + sha256/size.
With `?cold=1`: no checkpoint read, browser saving disabled (`profileOwned=false`, so a cold boot can never overwrite the saved phone), no snapshot download,
downloads kernel + wifi DT + gz rootfs (81 MB, sha256 verified), runs emu_init with rootfs. Progress: status line shows
`Cold boot Ns · G instr · MIPS · last serial line`, tab title likewise; `window.coldSerial` has the full guest serial; `window.coldReady` /
title `SpringBoard after Ns` is set when SpringBoard is lit. Extra params: `dt=orig` (original DT), `clock=rt` (real-time clock from reset, diagnostic).
**Important:** the worker keeps the guest clock instruction-driven (`emu_host_clock(0)`) until the screen is >35% lit (SpringBoard), then switches to real time.
With the real-time clock from reset the guest kernel panics reproducibly in the browser (`kernel abort ... pc 0xc043a4e0` in
AppleS5L8900XUSBWrangler::phyRegistered, "panic: We are hanging here...") with BOTH device trees, so it is not caused by the DT patch.
