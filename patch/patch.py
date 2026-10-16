#!/usr/bin/env python3
"""Reproducible patch pipeline: emu.wasm -> emu-net.wasm (+ JS bridge copies).

  python3 patch.py [--wasm ../re-core/emu.wasm] [--mirror ../mirror] [--out out]

Steps (any failed assertion aborts, nothing is written half-way):
  1. verify sha256 of the original emu.wasm
  2. wasm2wat
  3. scripted text edits (each anchor must match EXACTLY once):
       a) func 85 (bus read)  : branch for [0x38D00000,0x38D01000) -> $sdio_read
          func 86 (bus write) : same -> $sdio_write
          func 87 (overlap)   : add the SDIO window to the static overlap list
          append net_funcs.wat (register file, trace ring, TX/RX rings)
       b) new exports emu_net_*  (see README)
  4. wat2wasm (default feature set only: no --enable-all), wasm-validate
  5. write out/emu-net.wasm, out/emu-net.wat
  6. JS: out/dist/emu.js (loads emu-net.wasm, binds emu_net_*),
         out/emu-worker.js (drainNet / 'net-tx' / 'net-rx'),
         out/app.js (WebSocket bridge)
  7. out/site/ : a symlink overlay of the mirror with the patched files, so
     `python3 mirror/serve.py 8000 out/site` serves the patched build.

Edit (c), relocating the IRQ-42 polled table, is NOT implemented; see README.
"""
import argparse, hashlib, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORIG_SHA256 = "6c168674625059f974a11ef808bcf06185ddbb11a1ce553f9bd22f3bc29d52c6"
SDIO_BASE, SDIO_SIZE = 0x38D00000, 0x1000   # 953155584

EXPORTS = [
    ("emu_net_tx_pop", "emu_net_tx_pop"),
    ("emu_net_rx_push", "emu_net_rx_push"),
    ("emu_net_state", "emu_net_state"),
    ("emu_net_tx_push", "emu_net_tx_push"),
    ("emu_net_rx_pop", "emu_net_rx_pop"),
    ("emu_net_trace_pop", "emu_net_trace_pop"),
    ("emu_net_sdio_mode", "emu_net_sdio_mode"),
    ("emu_net_sdio_read", "emu_net_sdio_read"),
    ("emu_net_sdio_write", "emu_net_sdio_write"),
]


def die(msg):
    sys.exit("patch.py: " + msg)


def run(*cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        die("%s failed:\n%s%s" % (cmd[0], p.stdout, p.stderr))
    return p.stdout


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        die("anchor for %s matched %d times (expected 1)" % (what, n))
    return text.replace(old, new)


def func_span(wat, idx):
    """(start, end) of `(func (;idx;)` ... up to the next top-level func/table."""
    m = re.search(r"^  \(func \(;%d;\)" % idx, wat, re.M)
    if not m:
        die("func %d not found" % idx)
    n = re.compile(r"^  \((?:func|table)\b", re.M).search(wat, m.end())
    return m.start(), n.start()


def edit_func(wat, idx, old, new, what):
    s, e = func_span(wat, idx)
    body = replace_once(wat[s:e], old, new, what)
    return wat[:s] + body + wat[e:]


# ---------------------------------------------------------------- wasm
def patch_wat(wat):
    # sanity: malloc is func 16 / export "V" (used by $net_init)
    if '(export "V" (func 16))' not in wat:
        die('export "V" is not func 16; malloc assumption broken')
    if not re.search(r"^  \(func \(;16;\) \(type 3\)", wat, re.M):
        die("func 16 is not (i32)->i32")
    base = SDIO_BASE
    # --- a1) func 85: bus read(machine, addr, size) -> value ------------
    anchor85 = ("      local.get 0\n      i32.const 1\n      i32.store8 offset=121736\n"
                "      block  ;; label = @2\n")
    read_branch = (
        "        local.get 1\n        i32.const %d\n        i32.sub\n        i32.const %d\n        i32.lt_u\n"
        "        if  ;; emu-net: SDIO window [0x38D00000,+0x1000)\n"
        "          local.get 1\n          i32.const %d\n          i32.sub\n"
        "          local.get 2\n          call $sdio_read\n          local.set 3\n"
        "          br 1\n        end\n" % (base, SDIO_SIZE, base))
    wat = edit_func(wat, 85, anchor85, anchor85 + read_branch, "func85 read branch")
    # --- a2) func 86: bus write(machine, addr, val, size) ---------------
    anchor86 = ("      local.get 0\n      i32.const 1\n      i32.store8 offset=121736\n"
                "      block  ;; label = @2\n        block  ;; label = @3\n")
    write_branch = (
        "      local.get 1\n      i32.const %d\n      i32.sub\n      i32.const %d\n      i32.lt_u\n"
        "      if  ;; emu-net: SDIO window\n"
        "        local.get 1\n        i32.const %d\n        i32.sub\n"
        "        local.get 3\n        local.get 2\n        call $sdio_write\n"
        "        br 1\n      end\n" % (base, SDIO_SIZE, base))
    new86 = ("      local.get 0\n      i32.const 1\n      i32.store8 offset=121736\n" + write_branch +
             "      block  ;; label = @2\n        block  ;; label = @3\n")
    wat = edit_func(wat, 86, anchor86, new86, "func86 write branch")
    # --- a3) func 87: static overlap list --------------------------------
    last = ("      local.get 0\n      i32.const 965742592\n      i32.lt_u\n      local.get 2\n"
            "      i64.const 965738497\n      i64.ge_u\n      i32.and\n      br_if 0 (;@1;)\n")
    entry = ("      local.get 0\n      i32.const %d\n      i32.lt_u\n      local.get 2\n"
             "      i64.const %d\n      i64.ge_u\n      i32.and\n      br_if 0 (;@1;)\n"
             % (base + SDIO_SIZE, base + 1))
    wat = edit_func(wat, 87, last, last + entry, "func87 overlap entry")
    # --- new functions: before the table (after the last original func) --
    funcs = open(os.path.join(HERE, "net_funcs.wat")).read()
    wat = replace_once(wat, "  (table (;0;) 58 funcref)\n",
                       funcs + "\n  (table (;0;) 58 funcref)\n", "net funcs insertion")
    # new global must come AFTER the original global 0 (stack pointer) in the
    # text so existing `global.get 0` keep their meaning.
    wat = replace_once(wat, "  (global (;0;) (mut i32) (i32.const 5247344))\n",
                       "  (global (;0;) (mut i32) (i32.const 5247344))\n"
                       "  (global $net_base (mut i32) (i32.const 0))\n", "net global")
    # --- b) exports: before the elem segment -----------------------------
    exp = "".join('  (export "%s" (func $%s))\n' % (e, f) for e, f in EXPORTS)
    m = re.search(r"^  \(elem \(;0;\)", wat, re.M)
    if not m:
        die("elem segment not found")
    wat = wat[:m.start()] + exp + wat[m.start():]
    return wat


# ---------------------------------------------------------------- JS
def patch_emu_js(js):
    js = replace_once(js, 'locateFile("emu.wasm")', 'locateFile("emu-net.wasm")', "emu.js wasm name")
    bind = "".join('Module["_%s"]=wasmExports["%s"];' % (e, e) for e, _ in EXPORTS)
    return replace_once(js, 'Module["_emu_ticks"]=wasmExports["I"];',
                        'Module["_emu_ticks"]=wasmExports["I"];' + bind, "emu.js bindings")


def patch_worker(js):
    js = replace_once(
        js, "let framePointer, widthPointer, heightPointer, serialPointer, contactsPointer, audioPointer;",
        "let framePointer, widthPointer, heightPointer, serialPointer, contactsPointer, audioPointer;\n"
        "// emu-net: Ethernet frames cross here as whole messages (<= 2044 bytes, ring slot size).\n"
        "let netPointer, tracePointer, hasNet = false;\nconst NET_BUF = 2048, TRACE_MAX = 256;", "worker decl")
    js = replace_once(js, "function tick() {", """// Guest->host Ethernet frames leave in guest order; the page forwards them to the relay.
function drainNet() {
  if (!hasNet) return;
  for (;;) {
    const n = api.netTxPop(netPointer, NET_BUF);
    if (n <= 0) return;
    const frame = module.HEAPU8.slice(netPointer, netPointer + n).buffer;
    send('net-tx', {buffer:frame}, [frame]);
  }
}
// Host->guest frame from the relay (page 'net-rx' message).  Dropped when the ring is full.
function receiveNet(buffer) {
  if (!hasNet || !(buffer instanceof ArrayBuffer) || buffer.byteLength === 0 || buffer.byteLength > 2044) return;
  module.HEAPU8.set(new Uint8Array(buffer), netPointer);
  api.netRxPush(netPointer, buffer.byteLength);
}
// SDIO register trace (16-byte entries), for working out the guest driver's protocol.
function drainNetTrace() {
  if (!hasNet) return;
  const n = api.netTracePop(tracePointer, TRACE_MAX);
  if (n > 0) {
    const trace = module.HEAPU8.slice(tracePointer, tracePointer + n * 16).buffer;
    send('net-trace', {buffer:trace}, [trace]);
  }
}
function tick() {""", "worker drainNet")
    js = replace_once(js, "    jit?.pump();\n    drainAudio();\n", "    jit?.pump();\n    drainAudio();\n    drainNet();\n", "worker tick")
    js = replace_once(js, "      const elapsed = (now - lastStats) / 1000;\n",
                      "      const elapsed = (now - lastStats) / 1000;\n      drainNetTrace();\n", "worker trace")
    js = replace_once(js, "memory:module.HEAPU8.byteLength, jit:jit?.stats(),",
                      "memory:module.HEAPU8.byteLength, jit:jit?.stats(), net:hasNet,", "worker metrics")
    js = replace_once(
        js, "['size','emu_snapshot_size',[]], ['freeSave','emu_free_snapshot',[]]\n  ]",
        "['size','emu_snapshot_size',[]], ['freeSave','emu_free_snapshot',[]],\n"
        "    ...(module._emu_net_tx_pop ? [['netTxPop','emu_net_tx_pop',['number','number']],\n"
        "      ['netRxPush','emu_net_rx_push',['number','number']],\n"
        "      ['netTracePop','emu_net_trace_pop',['number','number']]] : [])\n  ]", "worker api")
    js = replace_once(
        js, "  audioPointer = module._malloc(AUDIO_FRAMES * 4);\n",
        "  audioPointer = module._malloc(AUDIO_FRAMES * 4);\n"
        "  hasNet = Boolean(api.netTxPop);\n"
        "  if (hasNet) { netPointer = module._malloc(NET_BUF); tracePointer = module._malloc(TRACE_MAX * 16); hasNet = Boolean(netPointer && tracePointer); }\n",
        "worker alloc")
    js = replace_once(js, "  if (message.type === 'recycle') { frameBuffer = message.buffer; return; }\n",
                      "  if (message.type === 'recycle') { frameBuffer = message.buffer; return; }\n"
                      "  if (message.type === 'net-rx') { receiveNet(message.buffer); return; }\n", "worker net-rx")
    return js


APP_BRIDGE = r"""
// ---- emu-net bridge: guest Ethernet frames <-> relay.py over WebSocket ----
// Opt-in: ?net=1 uses ws://localhost:8008, ?net=ws://host:port overrides it.
// One binary WebSocket message == one Ethernet frame (no FCS).  The relay's
// first message is a JSON text "config" (gateway/dns/mask); it is kept in
// iphoneNet.config for the future guest-side DHCP/ARP glue.
const netParam = new URLSearchParams(location.search).get('net');
const NET_URL = !netParam ? null : /^wss?:\/\//.test(netParam) ? netParam : 'ws://localhost:8008';
let netSocket = null, netTimer = 0, netAttempt = 0, netWanted = false;
const iphoneNet = window.iphoneNet = {url:NET_URL, config:null, connected:false, tx:0, rx:0, txDropped:0, rxDropped:0, trace:[]};
function netConnect() {
  if (!NET_URL || netSocket) return;
  netWanted = true;
  let socket;
  try { socket = new WebSocket(NET_URL); } catch { return; }
  socket.binaryType = 'arraybuffer';
  netSocket = socket;
  socket.onopen = () => { netAttempt = 0; iphoneNet.connected = true; };
  socket.onmessage = ({data}) => {
    if (typeof data === 'string') { try { iphoneNet.config = JSON.parse(data); } catch {} return; }
    if (!worker || !ready) { iphoneNet.rxDropped++; return; }
    iphoneNet.rx++;
    worker.postMessage({type:'net-rx', buffer:data}, [data]);
  };
  socket.onclose = () => {
    if (netSocket === socket) netSocket = null;
    iphoneNet.connected = false;
    if (!netWanted) return;
    clearTimeout(netTimer);
    netTimer = setTimeout(netConnect, Math.min(10000, 500 * 2 ** Math.min(netAttempt++, 5)));
  };
  socket.onerror = () => {};
}
function netClose() {
  netWanted = false; clearTimeout(netTimer);
  const socket = netSocket; netSocket = null; iphoneNet.connected = false;
  try { socket?.close(); } catch {}
}
function netTx(buffer) {
  if (netSocket?.readyState === WebSocket.OPEN) { iphoneNet.tx++; netSocket.send(buffer); }
  else iphoneNet.txDropped++;
}
function netTrace(buffer) {
  const w = new Uint32Array(buffer);
  for (let i = 0; i + 3 < w.length; i += 4)
    iphoneNet.trace.push({write:Boolean(w[i] >>> 31), size:(w[i] >>> 16) & 0xff, off:w[i] & 0xffff, value:w[i + 1] >>> 0, seq:w[i + 2]});
  if (iphoneNet.trace.length > 20000) iphoneNet.trace.splice(0, iphoneNet.trace.length - 20000);
}
"""


def patch_app(js):
    js = replace_once(js, "let audio = null, audioTime = 0;\n", "let audio = null, audioTime = 0;\n" + APP_BRIDGE, "app bridge")
    js = replace_once(js, "    if (message.type === 'audio') playAudio(message.rate, message.buffer);\n",
                      "    if (message.type === 'net-tx') netTx(message.buffer);\n"
                      "    else if (message.type === 'net-trace') netTrace(message.buffer);\n"
                      "    else if (message.type === 'audio') playAudio(message.rate, message.buffer);\n", "app dispatch")
    js = replace_once(js, "      setProgress(100);\n      ready = true;", "      setProgress(100);\n      netConnect();\n      ready = true;", "app ready")
    js = replace_once(js, "      metrics = message; serial = (serial", "      metrics = message; iphoneNet.hasNet = message.net; serial = (serial", "app metrics")
    js = replace_once(js, "  setStatus(describeError(error));\n  worker?.terminate();",
                      "  setStatus(describeError(error));\n  netClose();\n  worker?.terminate();", "app fail")
    return js


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s, mode="w"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, mode) as f:
        f.write(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wasm", default=os.path.join(HERE, "..", "re-core", "emu.wasm"))
    ap.add_argument("--mirror", default=os.path.join(HERE, "..", "mirror"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--no-js", action="store_true", help="only build the wasm")
    a = ap.parse_args()
    raw = open(a.wasm, "rb").read()
    got = hashlib.sha256(raw).hexdigest()
    if got != ORIG_SHA256:
        die("sha256 mismatch: got %s expected %s" % (got, ORIG_SHA256))
    print("sha256 ok", got)
    wat = run("wasm2wat", a.wasm)
    new = patch_wat(wat)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    wat_path, wasm_path = os.path.join(out, "emu-net.wat"), os.path.join(out, "emu-net.wasm")
    write(wat_path, new)
    run("wat2wasm", wat_path, "-o", wasm_path)
    run("wasm-validate", wasm_path)
    print("emu-net.wasm: %d bytes, validated" % os.path.getsize(wasm_path))
    if a.no_js:
        return
    m = os.path.abspath(a.mirror)
    write(os.path.join(out, "dist", "emu.js"), patch_emu_js(read(os.path.join(m, "dist", "emu.js"))))
    shutil.copy(wasm_path, os.path.join(out, "dist", "emu-net.wasm"))
    write(os.path.join(out, "emu-worker.js"), patch_worker(read(os.path.join(m, "emu-worker.js"))))
    write(os.path.join(out, "app.js"), patch_app(read(os.path.join(m, "app.js"))))
    # symlink overlay of the mirror (never touches the mirror itself)
    site = os.path.join(out, "site")
    shutil.rmtree(site, ignore_errors=True)
    os.makedirs(os.path.join(site, "dist"))
    for name in os.listdir(m):
        if name in ("dist", "emu-worker.js", "app.js"):
            continue
        os.symlink(os.path.join(m, name), os.path.join(site, name))
    for name in ("emu-worker.js", "app.js"):
        os.symlink(os.path.join(out, name), os.path.join(site, name))
    os.symlink(os.path.join(out, "dist", "emu.js"), os.path.join(site, "dist", "emu.js"))
    os.symlink(os.path.join(out, "dist", "emu-net.wasm"), os.path.join(site, "dist", "emu-net.wasm"))
    print("wrote", out)


if __name__ == "__main__":
    main()
