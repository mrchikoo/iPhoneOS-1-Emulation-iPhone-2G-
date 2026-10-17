<div align="center">

# iPhone OS 1.0 (iPhone 2G) Emulation

**Run the original iPhone OS 1.0 in your browser, locally, with working Wi-Fi, a modern-web browsing proxy and Mac keyboard typing.**

![Python](https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white)
![JavaScript](https://img.shields.io/badge/Node.js-%E2%89%A520-339933?logo=nodedotjs&logoColor=white)
![WebAssembly](https://img.shields.io/badge/WebAssembly-wabt-654FF0?logo=webassembly&logoColor=white)
![Platform](https://img.shields.io/badge/tested-macOS%20%2F%20Chromium-lightgrey)

</div>

## Overview

This project adds networking to a browser-based iPhone OS 1.0 emulator. It patches the emulator core with an emulated SDIO host and a Marvell 88W8686 Wi-Fi card model, provides a host-side Ethernet relay for internet access, and adds a stripped-down web proxy so the 2007-era Safari can load modern pages.

## Credits

- **[Kelindi](https://kelindi.ca)** made [2007.mobile](https://2007.mobile/), the WebAssembly emulator that boots genuine iPhone OS 1.0 in a browser. Everything here builds on that work: the emulator core, the phone UI and the firmware loader are theirs. This repository does not contain or redistribute any of it.
- **Wi-Fi, browser and keyboard support** by [mrchikoo](https://shazil-katchhi.vercel.app).
- iPhone OS 1.0 and its firmware are Apple's; the Wi-Fi chip firmware inside it is Marvell's.

## What is implemented

| Feature | Status |
|---|---|
| Local mirror of 2007.mobile (boots to the lock screen, offline after the first download) | works |
| Emulated SDIO host + Marvell 88W8686 card, IRQ 42 delivery, firmware download handshake | works (guest driver attaches, firmware loads) |
| Wi-Fi in Settings: scan, join a fake open AP "LocalNet", Wi-Fi icon in the status bar | works |
| Internet via `relay.py` (DHCP, DNS, ARP, TCP/UDP NAT, retransmit and window handling) | works for http; 22 relay tests pass |
| `browser/proxy.py`: host-side stripped-down browser (modern HTTPS using your Mac's certificates, plain HTML and small JPEGs for the phone) | works |
| Mac keyboard typing into the on-screen keyboard (`wifi-build/keyboard.js`) | works in Safari's URL and search fields (tested); other apps untested |
| Footer credits for Kelindi and mrchikoo on the page | works |

## Tech stack

Python 3 (relay, proxy, patchers, build), Node.js 20+ (headless tests and traces), WebAssembly text format patched with [wabt](https://github.com/WebAssembly/wabt), and vanilla JavaScript for the keyboard bridge.

## Quick start (macOS)

Needs `python3`, `node` >= 20, and wabt (`brew install wabt`).

```sh
python3 scripts/fetch.py          # download site files + ~182 MB firmware into mirror/ (hash-verified)
cd wifi-build && ./build.sh       # patch the emulator core + build the site
./run.sh                          # relay (ws://127.0.0.1:8008) + web proxy (8090) + site (8200)
```

Open <http://localhost:8200/?cold=1> (the `?cold=1` is required; the stock snapshot predates Wi-Fi). Stop everything with `Ctrl+C`.

On the phone:

1. Wait about 20 s for SpringBoard. Click the Home button if the screen is black.
2. Dismiss the "Repair Needed" alert if it shows (the phone has no baseband).
3. Open Settings, then Wi-Fi, then tap **LocalNet**.
4. In Safari open `http://web.test:8090/` (or `http://10.0.2.2:8090/`) and type a URL or a search.

Plain mirror without Wi-Fi: `python3 mirror/serve.py 8007`, then open <http://localhost:8007/>.

## Browsing the modern web

OS 1.0 Safari cannot do modern TLS, HTML, CSS or JavaScript, and sites like Google return 403 to it. `browser/proxy.py` fetches pages with your Mac's own `curl` and certificate store, reduces them to plain HTML, converts images to small JPEGs, and serves plain HTTP. Search uses DuckDuckGo.

Limits: no JavaScript or CSS, POST forms are sent as GET, no logins or cookies, and the proxy listens on 127.0.0.1 only (the guest reaches it through the relay, which maps 10.0.2.2 to your host).

## Keyboard

`wifi-build/keyboard.js` turns Mac keystrokes into taps on the phone's on-screen keyboard (the emulator has no keyboard device). It reads the canvas to see which layout and shift state is showing, switches layouts as needed, and queues keys (about 0.15 s per character).

- Typing mode turns on automatically when the phone's keyboard appears; **Tab** forces it on, and Tab or Esc turns it off. While on, the H/P/M/+/- phone shortcuts are disabled.
- Letters, digits, punctuation, Backspace and Enter (Go) work; typing `http://web.test:8090` into Safari loaded the proxy page in testing.
- Console helpers: `iphoneKeyboard.type('...')`, `iphoneKeyboard.state()`.
- Limits: keys are dropped while an alert (e.g. "Repair Needed") covers the keyboard; one swapped letter was seen in about 120 characters; the URL keyboard has no space key; only Safari's URL and search keyboards in portrait are mapped; the guest's own autocorrect still applies.

Details in `wifi-build/README.md`.

## Project structure

```
.
├── scripts/fetch.py     # download, hash-check and localize the site and firmware
├── mirror/              # serve.py (tracked); everything else is fetched
├── wifi-build/          # build.py / run.sh / relay_logged.py / keyboard.js: one-command Wi-Fi build
├── patch/               # patch.py + net_funcs.wat + sdio_model.wat: SDIO window, Wi-Fi card model, packet rings; tests
├── re-irq/              # VIC1 bit 10 (IRQ 42) delivery patch, tests, notes
├── dt-patch/            # device-tree patcher (MAC address, reset GPIO) and ?cold=1 notes
├── wifi-design/         # relay.py host-side Ethernet relay and test_relay.py
├── tcp-test/            # Safari/TCP harness, relay fixes, results
├── browser/             # proxy.py stripped web proxy
├── re-core/ re-disasm/ re-trace/   # reverse-engineering helpers (device-tree dumper, kernel disassembly, headless trace harness)
└── docs/                # emulator-core report, SDIO register map, early Wi-Fi design, TCP results
```

## Known limits

- `?cold=1` only: no saving in cold mode, so every visit is a ~20 s cold boot. The stock snapshot predates Wi-Fi.
- The "Repair Needed" (no baseband) alert appears once per boot.
- Browser cold boot with a real-time clock hits a guest kernel panic (USB wrangler null dereference); the build works around it by driving the guest clock by instruction count until SpringBoard is up (a heuristic, not a root-cause fix).
- https sites opened directly in Safari fail (2007-era TLS); use the proxy.
- One open fake AP only: no WPA/WEP, power-save or multiple networks.
- Relay: IPv4 only, no out-of-order buffering, no congestion window, no ICMP to the internet.
- Tested only in Chromium (the Claude desktop built-in browser), not Safari or Firefox, and not for long sessions.

## Not included, on purpose

The emulator build, the site code, Apple's firmware and the Marvell blobs extracted from it are never committed (`.gitignore` blocks them); `scripts/fetch.py` downloads them from their original hosts. Check each owner's terms before redistributing anything you fetch. This repo has no license grant for third-party material; the original tooling here is provided as-is for personal and research use.
