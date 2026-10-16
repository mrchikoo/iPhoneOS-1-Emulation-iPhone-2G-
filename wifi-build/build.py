#!/usr/bin/env python3
"""One-command WiFi browser build.

  python3 build.py            # -> vendor/, build/, site/

Inputs (read only, never modified):
  ../mirror           site, dist/emu.js, dist/emu.wasm (sha256 6c1686...52c6), fw/
  ../patch            patch.py + net_funcs.wat + sdio_model.wat + sdio_irq_stub.wat
  ../re-irq           irq_patch.py + irq_patch.wat (SDIO interrupt delivery)
  ../dt-patch         devicetree_m68ap_wifi.bin, site/{app.js,emu-worker.js,manifest.json} (?cold=1 mode)

Steps:
  1. copy the patch sources into vendor/ (same relative layout, so patch.py finds ../re-irq)
  2. wasm2wat -> patch.patch_wat -> wat2wasm -> wasm-validate  => build/emu-net.wasm
  3. JS: dist/emu.js (patch.patch_emu_js, loads a content-hashed emu-net.<h>.wasm),
         emu-worker.js = dt-patch cold worker + patch.patch_worker (drainNet, net-tx/net-rx)
         app.js        = dt-patch cold app   + patch.patch_app (WebSocket bridge), net DEFAULT ON
                         (ws://localhost:8008; ?net=0 disables, ?net=ws://h:p overrides),
                         + service-worker/cache cleanup, ?v=<h> cache-busting of the worker
         index.html    = mirror index.html with app.js?v=<h>
         service-worker.js = self-unregistering kill switch (no fetch handler)
  4. site/ : symlink overlay of the mirror with the patched/generated files
"""
import hashlib, importlib.util, json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.dirname(HERE)
MIRROR = os.path.join(TOP, "mirror")
PATCH = os.path.join(TOP, "patch")
IRQ = os.path.join(TOP, "re-irq")
DT = os.path.join(TOP, "dt-patch")
VENDOR, BUILD, SITE = (os.path.join(HERE, d) for d in ("vendor", "build", "site"))
RELAY_URL = "ws://localhost:8008"


def die(msg):
    sys.exit("build.py: " + msg)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        die("anchor for %s matched %d times (expected 1)" % (what, n))
    return text.replace(old, new)


def run(*cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        die("%s failed:\n%s%s" % (cmd[0], p.stdout, p.stderr))
    return p.stdout


# ------------------------------------------------------------------ 1. vendor
def vendor():
    shutil.rmtree(VENDOR, ignore_errors=True)
    files = {os.path.join(PATCH, f): os.path.join(VENDOR, "patch", f)
             for f in ("patch.py", "net_funcs.wat", "sdio_model.wat", "sdio_irq_stub.wat")}
    files.update({os.path.join(IRQ, f): os.path.join(VENDOR, "re-irq", f) for f in ("irq_patch.py", "irq_patch.wat")})
    info = {}
    for src, dst in files.items():
        if not os.path.isfile(src):
            die("missing input " + src)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        info[os.path.relpath(src, TOP)] = sha(dst)
    spec = importlib.util.spec_from_file_location("patch", os.path.join(VENDOR, "patch", "patch.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, info


# ------------------------------------------------------------------ 2. wasm
def build_wasm(patch):
    src = os.path.join(MIRROR, "dist", "emu.wasm")
    if sha(src) != patch.ORIG_SHA256:
        die("mirror/dist/emu.wasm sha256 mismatch")
    for tool in ("wasm2wat", "wat2wasm", "wasm-validate"):
        if not shutil.which(tool):
            die(tool + " not found (install wabt)")
    wat = patch.patch_wat(run("wasm2wat", src))       # prints "IRQ: using .../vendor/re-irq/irq_patch.wat"
    if not any(e[0] == "sdio_irq_state" for e in patch.EXPORTS_EXTRA):
        die("IRQ patch was not applied")
    wat_path, wasm_path = os.path.join(BUILD, "emu-net.wat"), os.path.join(BUILD, "emu-net.wasm")
    write(wat_path, wat)
    run("wat2wasm", wat_path, "-o", wasm_path)        # default feature set only (see patch/README.md)
    run("wasm-validate", wasm_path)
    return wasm_path


# ------------------------------------------------------------------ 3. JS
APP_EXTRA = r"""
// ---- wifi-build: no service worker / no stale caches for this build ----
// The mirror's offline mode would register service-worker.js; this build ships a self-unregistering one and
// removes every registration and every cache except the sha256-verified firmware cache on each load, so a
// previously cached core can never shadow the content-hashed emu-net wasm.
window.wifiBuild = {hash:'__HASH__', wasm:'__WASM__'};
(async () => {
  try { for (const r of await navigator.serviceWorker?.getRegistrations() || []) await r.unregister(); } catch {}
  try { for (const k of await caches.keys()) if (k !== 'iphone-1-assets-1a543a-v1') await caches.delete(k); } catch {}
})();
"""

SW = """// wifi-build: kill-switch service worker. No fetch handler; unregisters itself and drops non-firmware caches.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== 'iphone-1-assets-1a543a-v1') await caches.delete(k);
  await self.registration.unregister();
})()));
"""


def build_js(patch, h, wasm_name):
    # dist/emu.js: patched bindings + content-hashed wasm file name
    js = patch.patch_emu_js(read(os.path.join(MIRROR, "dist", "emu.js")))
    js = once(js, 'locateFile("emu-net.wasm")', 'locateFile("%s")' % wasm_name, "emu.js hashed wasm")
    write(os.path.join(BUILD, "dist", "emu.js"), js)

    # worker: dt-patch cold worker + net
    w = patch.patch_worker(read(os.path.join(DT, "site", "emu-worker.js")))
    w = once(w, "importScripts('dist/emu.js', 'seed-jit.js');",
             "importScripts('dist/emu.js?v=%s', 'seed-jit.js');" % h, "worker importScripts")
    write(os.path.join(BUILD, "emu-worker.js"), w)

    # app: dt-patch cold app + net bridge, default on
    a = patch.patch_app(read(os.path.join(DT, "site", "app.js")))
    a = once(a, "const NET_URL = !netParam ? null : /^wss?:\\/\\//.test(netParam) ? netParam : 'ws://localhost:8008';",
             "// wifi-build: bridge is ON by default (%s); ?net=0 disables, ?net=ws://host:port overrides.\n"
             "const NET_URL = netParam === '0' ? null : netParam && /^wss?:\\/\\//.test(netParam) ? netParam : '%s';"
             % (RELAY_URL, RELAY_URL), "app net default-on")
    a = once(a, "let audio = null, audioTime = 0;\n",
             "let audio = null, audioTime = 0;\n" + APP_EXTRA.replace("__HASH__", h).replace("__WASM__", wasm_name),
             "app sw cleanup")
    a = once(a, "  const workerURL = new URL('./emu-worker.js', import.meta.url);\n",
             "  const workerURL = new URL('./emu-worker.js', import.meta.url);\n"
             "  workerURL.searchParams.set('v', '%s');\n" % h, "app worker version")
    # keyboard.js: Mac keyboard -> taps on the guest's on-screen keyboard (ES module, no deps)
    a = once(a, "const contacts = new Map(), keys = new Map(), pending = new Map();\n",
             "import {initKeyboard} from './keyboard.js?v=%s';\n" % sha(os.path.join(HERE, "keyboard.js"))[:12] +
             "const contacts = new Map(), keys = new Map(), pending = new Map();\n", "app keyboard import")
    a = once(a, "canvas.addEventListener('pointerdown', event => {\n  if (!ready || paused ||",
             "initKeyboard({canvas, context, setStatus, status:$('status'),\n"
             "  isReady: () => ready && !paused,\n"
             "  send: list => call('contacts', {contacts:list}).catch(inputError),\n"
             "  others: () => [...contacts.values()], touched: () => { lastInput = performance.now(); }});\n"
             "canvas.addEventListener('pointerdown', event => {\n  if (!ready || paused ||", "app keyboard init")
    write(os.path.join(BUILD, "app.js"), a)
    shutil.copy2(os.path.join(HERE, "keyboard.js"), os.path.join(BUILD, "keyboard.js"))

    idx = once(read(os.path.join(MIRROR, "index.html")), '<script type="module" src="app.js"></script>',
               '<script type="module" src="app.js?v=%s"></script>' % h, "index app.js")
    idx = once(idx, '<footer class="site-footer">Made by <a href="https://kelindi.ca">Kelindi</a></footer>',
               '<footer class="site-footer">Made by <a href="https://kelindi.ca">Kelindi</a> &middot; '
               'Wi-Fi and browser support by <a href="https://shazil-katchhi.vercel.app">mrchikoo</a></footer>', "index footer")
    write(os.path.join(BUILD, "index.html"), idx)
    write(os.path.join(BUILD, "service-worker.js"), SW)

    # manifest: dt-patch's (adds devicetreeWifi + sha256/size); check the DT it points to
    man = json.loads(read(os.path.join(DT, "site", "manifest.json")))
    dt_src = os.path.join(DT, "devicetree_m68ap_wifi.bin")
    if man["assetSha256"].get("fw/devicetree_m68ap_wifi.bin") != sha(dt_src):
        die("dt-patch manifest sha256 does not match devicetree_m68ap_wifi.bin")
    shutil.copy2(dt_src, os.path.join(BUILD, "fw", "devicetree_m68ap_wifi.bin"))
    write(os.path.join(BUILD, "manifest.json"), json.dumps(man, indent=2) + "\n")


# ------------------------------------------------------------------ 4. site overlay
OWN = {"app.js", "keyboard.js", "emu-worker.js", "index.html", "manifest.json", "service-worker.js", "dist", "fw"}


def build_site(wasm_name):
    shutil.rmtree(SITE, ignore_errors=True)
    os.makedirs(os.path.join(SITE, "dist"))
    os.makedirs(os.path.join(SITE, "fw"))
    for name in sorted(os.listdir(MIRROR)):
        if name not in OWN and not name.startswith("."):
            os.symlink(os.path.join(MIRROR, name), os.path.join(SITE, name))
    for name in sorted(os.listdir(os.path.join(MIRROR, "fw"))):
        os.symlink(os.path.join(MIRROR, "fw", name), os.path.join(SITE, "fw", name))
    for name in ("app.js", "keyboard.js", "emu-worker.js", "index.html", "manifest.json", "service-worker.js",
                 "dist/emu.js", "fw/devicetree_m68ap_wifi.bin"):
        os.symlink(os.path.join(BUILD, name), os.path.join(SITE, name))
    os.symlink(os.path.join(BUILD, "emu-net.wasm"), os.path.join(SITE, "dist", wasm_name))


def main():
    os.makedirs(os.path.join(BUILD, "fw"), exist_ok=True)
    patch, vinfo = vendor()
    wasm = build_wasm(patch)
    h = sha(wasm)
    wasm_name = "emu-net.%s.wasm" % h[:12]
    build_js(patch, h[:12], wasm_name)
    build_site(wasm_name)
    ref = os.path.join(PATCH, "out", "emu-net.wasm")
    info = {"emu-net.wasm": {"sha256": h, "bytes": os.path.getsize(wasm), "served_as": "dist/" + wasm_name,
                             "same_as_patch_out": os.path.isfile(ref) and sha(ref) == h},
            "orig_emu.wasm_sha256": patch.ORIG_SHA256, "vendored": vinfo, "relay": RELAY_URL,
            "seed_jit_note": "jit/abi.json moduleSha256 is the hash of jit/seed.wasm itself; the binding to the core "
                             "is the runtime ABI check (_emu_jit_abi revision + 16 _emu_jit_offset values)."}
    write(os.path.join(BUILD, "BUILD-INFO.json"), json.dumps(info, indent=2) + "\n")
    print("emu-net.wasm %d bytes sha256 %s -> site/dist/%s (identical to patch/out: %s)"
          % (info["emu-net.wasm"]["bytes"], h, wasm_name, info["emu-net.wasm"]["same_as_patch_out"]))
    print("site ready:", SITE)


if __name__ == "__main__":
    main()
