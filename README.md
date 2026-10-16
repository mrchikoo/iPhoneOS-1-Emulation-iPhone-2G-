# iphone-os1-local

Run the browser-based iPhone OS 1.0 emulator
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
| `dt-patch/` | device-tree patcher for WiFi properties (untested in browser) |
| `patch/` | `patch.py` + `net_funcs.wat`: adds SDIO window and packet rings to `emu.wasm`; tests |
| `wifi-design/` | `relay.py` host-side Ethernet relay (DHCP/DNS/ARP/NAT), `test_relay.py` |
| `docs/` | emulator-core report, SDIO register map from the guest kernel, WiFi design |

## WiFi status
Done: chip identified, SDIO register map and firmware protocol recovered from the kernel, patch
pipeline and packet bridge (boots identically), relay with passing tests.
Not done: the SDIO host/card responses (guest times out on CMD5), interrupt 42 delivery, device-tree
properties, end-to-end traffic. `docs/wifi-design.md` is an earlier design; where it conflicts with
`docs/sdio-map.md`, the latter is authoritative. Several agent-written READMEs mention absolute
paths from the original machine; commands run from the repo root work with the layout above.
