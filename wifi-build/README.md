# wifi-build: browser build with WiFi (LocalNet) + internet via relay

One build of the 2007.mobile emulator where a cold-booted phone has Wi-Fi, joins **LocalNet**, gets DHCP from
`relay.py` and reaches the internet (Safari loaded http://www.google.com/ in the test below).
Nothing outside this directory is modified; inputs are copied (`vendor/`) or symlinked (`site/`).

## Commands

```sh
cd "/Users/skkat/Untitled Folder/wifi-build"
./build.sh          # = python3 build.py  -> vendor/, build/, site/   (needs python3 + wabt: wasm2wat, wat2wasm, wasm-validate)
./run.sh            # relay on ws://127.0.0.1:8008 (log: relay.log) + server on http://localhost:8200 ; Ctrl-C stops both
#   ./run.sh --host mysite.test=10.0.2.2   extra args go to relay.py
# open http://localhost:8200/?cold=1
#   wait for the tab title "SpringBoard after Ns", dismiss "Repair Needed" if shown,
#   Settings > Wi-Fi > LocalNet  -> status-bar Wi-Fi icon; relay.log shows DHCP DISCOVER/OFFER/REQUEST/ACK -> 10.0.2.15
grep DHCP relay.log
```

URL options: `?cold=1` cold boot with the WiFi device tree (required for WiFi; the plain URL restores the stock snapshot,
which predates WiFi and uses the stock DT), `?net=0` disables the bridge, `?net=ws://host:port` other relay,
`?jit=0` interpreter only, `dt=orig`, `clock=rt` (see ../dt-patch/README.md).
Console helpers: `window.iphoneNet` (connected/tx/rx/config/trace), `window.coldReady`, `window.coldSerial`,
`window.iphone.metrics` (mips, jit stats), `window.wifiBuild` (wasm hash).

## What build.py does

1. Copies `../patch/{patch.py,net_funcs.wat,sdio_model.wat,sdio_irq_stub.wat}` and `../re-irq/{irq_patch.py,irq_patch.wat}`
   into `vendor/` (same relative layout) and imports `vendor/patch/patch.py`.
2. `mirror/dist/emu.wasm` (sha256 checked, 6c1686...52c6) -> `wasm2wat` -> `patch_wat` (SDIO window, Marvell 88W8686 model,
   IRQ hook) -> `wat2wasm` -> `wasm-validate` = `build/emu-net.wasm`. Byte-identical to `../patch/out/emu-net.wasm`
   (sha256 6c45a355...8366; recorded in `build/BUILD-INFO.json`).
3. JS:
   * `dist/emu.js` = mirror emu.js + `patch_emu_js` (binds `emu_net_*`), loading `emu-net.<sha12>.wasm`.
   * `emu-worker.js` = **dt-patch** cold worker (instruction clock until SpringBoard, wifi DT) + `patch_worker`
     (`drainNet` -> `net-tx`, `net-rx` -> RX ring, `net-trace`).
   * `app.js` = **dt-patch** cold app (`?cold=1`) + `patch_app` WebSocket bridge, **net on by default**
     (`ws://localhost:8008`).
   * `manifest.json` from dt-patch (sha256 of `fw/devicetree_m68ap_wifi.bin` is checked at build time).
4. `site/` = symlinks to everything in `../mirror` plus the generated files.

### Stale wasm / service worker / caches
* The wasm file name contains its sha256 prefix, `emu.js` and the worker are loaded with `?v=<sha12>`, and `index.html`
  loads `app.js?v=<sha12>`, so a new build can never pick up an old core from the HTTP cache (serve.py also sends `no-cache`).
* `service-worker.js` is replaced by a kill switch (no fetch handler; deletes non-firmware caches, unregisters itself).
* `app.js` unregisters every service worker for the origin and deletes every Cache Storage entry except the firmware
  cache `iphone-1-assets-1a543a-v1` (its entries are sha256-verified against the manifest on every load, and the wasm is
  never stored there).

## Test results (built-in browser pane, 2026-10-16) - screenshots in `shots/`

| | JIT (default) | `?jit=0` |
|---|---|---|
| time to SpringBoard (page start -> 35% lit) | **19 s** (1.48 G instr) | 21 s (1.43 G instr) |
| boot MIPS | 95-113 | ~66 |
| Settings > Wi-Fi shows LocalNet, join works, status-bar Wi-Fi icon | yes | yes |
| relay.log DHCP DISCOVER/OFFER/REQUEST/ACK -> 10.0.2.15 | yes (`shots/relay-run1.log`) | yes (`shots/relay-run2-jit0.log`) |

* JIT run: SpringBoard (`01`), Settings shows Wi-Fi "Not Connected" (`02`), Wi-Fi Networks lists LocalNet (`03`), after the tap
  the Wi-Fi icon appears and the "Repair Needed" alert pops up (`04`), dismissed -> check mark on LocalNet (`05`).
  Then Safari > Google bookmark: DNS via 10.0.2.3, TCP to 142.251.150.119:80, the Google page rendered (`06`).
  `NN-*.png` are the phone canvas, `pane-*.jpg` the whole browser pane.
* **Seed JIT does not fall back**: `jit/abi.json` `moduleSha256` is the hash of `jit/seed.wasm` itself, not of the core.
  The core binding is the runtime ABI check (`_emu_jit_abi` revision + 16 `_emu_jit_offset` values), and the patch
  does not touch the machine struct, so the seed installs with `emu-net.wasm` (509 regions, enabled) and the WiFi path
  worked with it. `?jit=0` forces the interpreter; that also works end to end.
* Console: no errors during the runs, except `WebSocket connection to 'ws://localhost:8008/' failed` while the relay was
  stopped (expected; the bridge reconnects with backoff, frames are dropped while disconnected).

## Keyboard

`keyboard.js` (ES module, no dependencies; wired in by `build.py`, which imports it from `app.js` and calls
`initKeyboard(...)`) lets you type on your Mac keyboard. The emulator has no keyboard device, so each keystroke is
turned into one or two taps (contact down, 50 ms, contact up, queued with a 110 ms gap) on the guest's own
on-screen keyboard, switching between its letter / number / symbol layouts and using shift as needed.

- Typing mode is automatic: when the phone shows its keyboard (detected by comparing a 40x25 grayscale signature of
  the y=276..475 band of the 320x480 canvas with reference captures) Mac keys are typed into the phone.
  `Tab` forces typing mode on (or off while typing), `Esc` leaves it (re-arms when the keyboard goes away).
  While typing, H / P / M / + / - / Esc do not fire phone buttons. Status line under the phone shows the state.
- Supported: letters (shift tapped for capitals), digits, punctuation (123 / symbol layouts and back), space,
  Backspace/Delete, Enter (Go / Return / Search). Handles both the Safari URL keyboard (`. / .com`, no space key)
  and the plain keyboard (space + Return); the layout and shift state are read back from the screen before each key.
- Not typable: keys the guest keyboard lacks (URL keyboard: space and any symbol outside its number/symbol pages;
  plain keyboard: backtick; euro/pound/yen/bullet are not mapped), arrow keys; Cmd/Ctrl combos are left to the browser.
- Console helper: `iphoneKeyboard.type('http://web.test:8090\n')`, `iphoneKeyboard.state()`.
- Tested in the built-in browser (screenshots `shots/kb-*.png`): `http://web.test:8090` + Enter on the Safari URL
  bar loaded the proxy page (`kb-10-loaded.png`); uppercase, digits, backspace (`kb-5`, `kb-6`), space and
  punctuation in the Safari search field (`kb-7`, `kb-8`).
- Caveats: ~0.15 s per character (the guest redraws slowly); one swapped letter (l typed as k) was seen once in
  ~120 characters before the gap was raised from 90 to 110 ms (not re-measured); keys typed while the "Repair Needed"
  alert or any other overlay covers the keyboard are dropped (dismiss the alert first); guest autocorrect still
  applies (e.g. a "LOL" suggestion for `lll`); reference signatures are only valid for the portrait OS 1 keyboards
  measured here (Safari URL + search field); other apps' keyboards that match one of them work, anything else is
  treated as "no keyboard" (use Tab to force, but the layout then cannot be read).

## Known issues / honest notes
* The first tap on Settings right after "SpringBoard after Ns" was ignored once; a second tap worked.
* "Repair Needed" (no baseband) pops up periodically; it has to be dismissed.
* After the first join the guest ran a second full DHCP DISCOVER..ACK ~60 s later (after Safari was opened), although the
  lease is 24 h; connectivity was not lost (the Safari TCP connection continued). Cause not investigated.
* In run 1 relay.log showed two "guest connected" lines 35 s apart for a single page load (no disconnect log existed yet;
  `relay_logged.py` now logs "connection closed"). In run 2 the only extra connection was the old page reconnecting.
* Card/SDIO model state is not in snapshots, and browser saving is disabled in cold mode, so every visit is a ~20 s cold boot.
* Tested only in the built-in browser pane (Chromium). Not tested: Safari/Firefox, HTTPS sites, long sessions.

## Files
* `build.py`, `build.sh` - build; `run.sh` - relay + server
* `relay_logged.py` - imports `../wifi-design/relay.py` unchanged and logs every frame (decoded, incl. DHCP types) to `relay.log`
* `tools/shot_sink.py` - test helper (POST a PNG to 127.0.0.1:8201 -> `shots/`), used to save the phone canvas
* `build/BUILD-INFO.json` - hashes of the wasm and vendored sources
