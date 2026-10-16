# iphone-os1-local

Run the browser-based iPhone OS 1.0 emulator from [2007.mobile](https://2007.mobile/)
locally, plus reverse-engineering notes and a work-in-progress WiFi (SDIO / Marvell 88W8686) patch.

**Status:** the local mirror boots to the lock screen. **WiFi does not work yet** (see below).

## Quick start
```sh
python3 scripts/fetch.py            # downloads site files + ~182 MB firmware into mirror/ and verifies SHA-256
python3 mirror/serve.py 8007        # then open http://localhost:8007/ (click Home if the screen is black)
```
`mirror/serve.py` is stdlib-only and binds to 127.0.0.1.

## Not included (fetched, never committed)
The emulator build, site code and firmware belong to their owners (the site is by Kelindi; the
firmware is Apple's iPhone OS 1.0 build 1A543a; the Marvell blobs are extracted from it). This repo
contains only original tooling and notes. Check licensing before redistributing any fetched file.

## Layout
| Path | Contents |
|---|---|
| `scripts/` | `fetch.py`: download, hash-check, localize |
| `mirror/` | `serve.py` (tracked); everything else is fetched |
| `re-core/` | `dt.py` device-tree dumper (`emu.wasm` fetched here) |
| `re-disasm/scripts/` | Mach-O / kernel-image / vtable helpers (capstone venv not included) |
| `re-trace/` | `run.mjs` headless cold-boot harness with SDIO tracing |
| `dt-patch/` | device-tree patcher (WiFi properties) and `?cold=1` notes |
| `patch/` | `patch.py` + `net_funcs.wat`: adds SDIO window and packet rings to `emu.wasm`; tests |
| `wifi-design/` | `relay.py` host-side Ethernet relay (DHCP/DNS/ARP/NAT), `test_relay.py` |
| `docs/` | emulator-core report, SDIO register map from the guest kernel, WiFi design |

## WiFi status
Works in the built-in browser (Chromium), per agent-run tests with screenshots (not committed):
cold boot with the patched core + Wi-Fi device tree -> Settings > Wi-Fi lists "LocalNet" -> join ->
DHCP (10.0.2.15) -> Safari loads http pages through `relay.py` (local server and http://example.com).

```sh
python3 scripts/fetch.py
cd wifi-build && ./build.sh && ./run.sh      # needs wabt; then open http://localhost:8200/?cold=1
```
Known limits: `?cold=1` required (the stock snapshot predates Wi-Fi; no saving in cold mode); the
"Repair Needed" (no baseband) alert reappears; https sites fail (2007-era TLS); only one open AP,
no WPA/WEP/power-save; relay is IPv4-only with no out-of-order buffering or congestion window;
tested only in Chromium. See `docs/` and `tcp-test/RESULTS.md` for evidence and details.
`docs/wifi-design.md` is an early design; `docs/sdio-map.md` (from the guest kernel) is authoritative.
