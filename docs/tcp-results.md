# TCP through the emulated Wi-Fi: results (2026-10-16)

All work is in this directory; patch and wifi-design were not modified.
Inputs are read from ../patch (emu core `out/dist`, DT, rootfs). No Apple firmware file was touched.

Files: `coldboot.mjs` (harness copy), `relay.py` (fixed), `test_relay.py` (22 tests), `run.sh`, `gen_script.py`,
`*.script` (scripted UI runs), `alert_btn.rgb` (alert detector template), `www/` (test site), `frames/` (evidence), `out/` (all run logs/frames).

## 1. "Repair Needed" alert

Evidence: `out/base/shot_*.png` (baseline cold boot, no dismissal): home screen at ~1000M insns, status bar says
"Searching...", the alert (`frames/repair_needed_alert.png`) appears at ~2000-2100M insns (guest clock 4:01 PM) and is
still on screen in every later frame up to 2600M, so any tap after that point lands on the dimmed alert, not the app.
In every run below (9 cold boots) it appeared exactly once, at the same instant; it did not "reappear" after being dismissed
(`out/ad`: frames every 200M from 2000M to 4400M are clean after one dismissal).
Root cause (not fixed, only characterised): the baseband/modem never answers CommCenter (no modem emulated behind AppleBaseband/UART4;
rootfs strings show CommCenter/`/dev/h5.baseband`, "baseband not responding"), so SpringBoard raises the alert after ~100 guest seconds.
I did not find a DT/guest-property/launch-order way to avoid it; modifying the DT or emulating a modem was out of scope.
Fix in harness: `AUTODISMISS` (default on in `coldboot.mjs`): every 10M insns compare the 240x30 Dismiss-button crop of the live
framebuffer against `alert_btn.rgb` (mean abs diff < 12; observed diff 0.0 on the real alert) and tap (160,298). Log line
`dismiss #1 @2100M diff=0.0` in out/run*.log. `AUTODISMISS=0` disables it.
Other harness findings: a tap held 3M insns on the Wi-Fi list row did NOT select LocalNet, a short hold (0.2M insns, what cold10 used via HOLD=200000)
did; the new `tap` command defaults to 0.2M. Tapping Settings -> Wi-Fi needs ~250M insns for the Settings app to load before the next tap.
New harness features: `CTL=file` live command queue (`tap x y [holdM]`, `home`, `shot name`, `wait M`, `drag`, `quit`), `SHOTS=N` periodic frames,
relay.log written every 100M insns with insn timestamps and TCP seq/ack/win/payload decode. Whole run is ~90 s wall for ~4.5G insns.

## 2. Safari page load (proved)

Commands:
```
cd "tcp-test"
(cd www && python3 -m http.server 8099 --bind 127.0.0.1 &)     # port 8080 was already taken by an unrelated vite server
python3 relay.py --listen 127.0.0.1:8008 --host mysite.test=10.0.2.2 &
python3 gen_script.py "10.0.2.2[123]:8099" x > x.script   # equivalent to safari.script
./run.sh out/run5 safari.script      # cold boot, join LocalNet, Safari, type URL, Go, frames
```
Page `www/index.html` (red "TCP WORKS", marker ZEBRA-4417, plus a 300x300 PNG `pic.png`, 255,092 bytes).
Frames: `frames/wifi_joined.png` (check mark on LocalNet + Wi-Fi icon), `frames/url_typed.png`, `frames/new_relay_index_with_png.png`
(page text and the full image rendered; `out/run5/w_load1..2.png`). http.log: `GET /` and `GET /pic.png` 200.
Relay stderr (RELAY_DEBUG=1): `tcp close 10.0.2.15:49153->10.0.2.2:8099 {'retx': 0,'fast': 0,'probes': 0,'sent': 186,'zero_win': 1}`.
Hostname path: `mysite.test:8099` typed (`out/run7_name`), guest DNS query 10.0.2.15 -> 10.0.2.3:53 answered from `--host`, page + image rendered
(`frames/mysite_test_hostname.png`).

## 3. relay.py problems found with the real iPhone TCP stack, and fixes

Observed with the ORIGINAL relay (`out/run3/relay.log` = `frames/relay_run3_oldrelay_window_overrun.log`, run6 = same scenario with the old relay):
the guest's advertised window shrank while Safari was slow to read: `ack=3110551043 win=1196`, `ack=3110552179 win=60`.
The old relay checked only "is there any room" and then sent a full 1400-byte segment (seq 3110552179). The guest accepted just 60 bytes
(`ack=3110552239` repeated as dup ACKs) and the relay, which has no retransmit, had already advanced `sent`, sent seq 3110553579 next and
never resent the gap -> dup-ACK storm, transfer stalled forever. Visible symptom: Safari spinner/"server stopped responding" (first manual run:
"Safari could not open the page because the server stopped responding") and, in the A/B run with the same page, the image never finishing
(`frames/old_relay_image_stuck.png`, vs `frames/new_relay_index_with_png.png`). Same-page run with the old relay served both GETs to the host,
so the failure is purely guest-bound TCP.
Fixes in `relay.py` (TcpConn rewritten):
* sends only within the guest's advertised window (never a segment larger than remaining window room; old test even allowed window+MSS);
* send buffer kept until ACKed; RTO retransmit (0.4 s, doubling to 6 s, 12 tries then RST, go-back-N) and fast retransmit on 3 dup ACKs;
* zero-window persist probe (1 byte after RTO) and resume; verified live: `TX ... ack=268478964 win=0` followed by win 36000/65535 and the relay resumed exactly at seq 268478964 (`frames/relay_run5.log` lines 252-255);
* FIN is sequenced after the last data byte, retransmitted until ACKed; connection closes only when both FINs are acked;
* SYN-ACK is retransmitted (RTO and on duplicate guest SYN), peer MSS option honoured, no window scale / SACK offered (so the unscaled window field is exact), guest window/option parsing safe against bad options;
* advertised receive window shrinks with the host-socket write backlog; in-flight capped at 16 segments so bursts stay below the card's 16-slot upload queue / 128-slot RX ring;
* unaffected: handling of duplicate/overlapping guest data (tested).
Tests (`python3 -W ignore -m unittest test_relay`): 22 tests, all pass (12 original + 10 new in `TcpStack`: window never exceeded incl. 60 B window and mid-stream shrink,
zero-window persist/resume byte exact, RTO retransmit, fast retransmit, 200 kB transfer with every 7th segment lost byte-exact, SYN retransmit, MSS/no-wscale/no-SACK,
FIN ordering+retransmit, guest duplicate/overlap). Run against the ORIGINAL relay.py the same test file gives 5 failures + 5 errors (one error is only the renamed `acked` attribute).
Not done / known gaps: no out-of-order buffering of guest->host data (guest retransmits; fine in practice), no congestion window (fixed 16-segment flight cap), no ICMP to internet, no IPv6, no IP fragments, no TCP checksum verification of guest segments.
Retransmits did not trigger in the live runs after the fix (`retx: 0`), so RTO/fast retransmit are verified by unit tests only; the window/zero-window path was exercised live.

## 4. Real internet (host has internet, DNS upstream = macOS resolver 192.168.178.1; /etc/resolv.conf lists it)

* `http://example.com` (typed as `example` + `.com` key; `./run.sh out/run8_example example.script`): rendered "Example Domain" text in Safari
  (`frames/example_com_http.png`); relay stderr `tcp close 10.0.2.15:49152->104.20.23.154:80 {... 'sent': 5 ...}`; DNS query/answer visible in `out/run8_example/relay.log`.
* `https://example.com` (`./run.sh out/run9_https https.script`): FAILS in Safari: "could not establish a secure connection to the server"
  (`frames/https_example_com_fail.png`). The TCP layer is fine: SYN/SYN-ACK, 74-byte ClientHello-sized guest payload to 104.20.23.154:443, server replied 7 bytes then FIN
  (relay.log lines 45-54), i.e. the server rejected the iPhone OS 1 TLS handshake (my inference: a TLS alert; payload not decoded). Not a relay fault; modern sites
  need TLS 1.2+/SNI features this 2007 Safari lacks.

## Reproduce quickly
```
cd "tcp-test"
python3 -W ignore -m unittest test_relay
(cd www && python3 -m http.server 8099 --bind 127.0.0.1 &) ; RELAY_DEBUG=1 python3 relay.py --listen 127.0.0.1:8008 --host mysite.test=10.0.2.2 &
./run.sh out/myrun safari.script        # needs: node >= 20 and ../patch/out/dist built
```
Caveat: `safari.script` types 10.0.2.2:8099 using hard-coded keyboard coordinates measured from screenshots (number page: digits y=297, '.' (107,457), ':' (137,402), Go (280,457)).
