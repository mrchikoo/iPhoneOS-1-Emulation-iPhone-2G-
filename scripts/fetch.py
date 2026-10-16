#!/usr/bin/env python3
"""Download the 2007.mobile site files and iPhone OS 1.0 firmware into ./mirror
and rewrite them to run from localhost. Nothing here is redistributed by this repo.
Usage: python3 scripts/fetch.py [--no-firmware]"""
import hashlib, json, os, re, shutil, sys, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MIRROR = os.path.join(ROOT, 'mirror')
SITE = 'https://2007.mobile/'
FILES = ['index.html', 'app.js', 'assets.js', 'emu-worker.js', 'storage.js', 'phone.css',
         'manifest.json', 'checkpoint-worker.js', 'sha256.js', 'seed-jit.js', 'icon.svg',
         'og-image.png', 'dist/emu.js', 'dist/emu.wasm', 'jit/seed.wasm',
         'jit/regions.json', 'jit/abi.json']

def get(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print('GET', url)
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, 'wb') as f:
        shutil.copyfileobj(r, f)

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    for name in FILES:
        get(SITE + name, os.path.join(MIRROR, name))
    mpath = os.path.join(MIRROR, 'manifest.json')
    manifest = json.load(open(mpath))
    urls = [manifest['kernel'], manifest['devicetree'], *manifest['rootfs'], *manifest['snapshot']]
    if '--no-firmware' not in sys.argv:
        for url in urls:
            dest = os.path.join(MIRROR, 'fw', os.path.basename(url))
            if not (os.path.exists(dest) and sha(dest) == manifest['assetSha256'][url]):
                get(url, dest)
            if sha(dest) != manifest['assetSha256'][url]:
                sys.exit('hash mismatch: ' + dest)
    # localize: same-origin firmware paths, no analytics, trivial service worker
    local = lambda u: 'fw/' + os.path.basename(u)
    out = {k: ([local(u) for u in v] if isinstance(v, list) else local(v)) if k in
           ('kernel', 'devicetree', 'rootfs', 'snapshot') else v for k, v in manifest.items()}
    for k in ('assetSha256', 'assetBytes'):
        out[k] = {local(u): v for u, v in manifest[k].items()}
    json.dump(out, open(mpath, 'w'), indent=2)
    p = os.path.join(MIRROR, 'assets.js')
    s = open(p).read()
    s = re.sub(r"new Set\(\['https://iphone\.files\.garden'\]\)", 'new Set()', s)
    open(p, 'w').write(s)
    open(os.path.join(MIRROR, 'analytics.js'), 'w').write('export function track() {}\n')
    open(os.path.join(MIRROR, 'service-worker.js'), 'w').write(
        "self.addEventListener('install', () => self.skipWaiting());\n"
        "self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));\n")
    shutil.copy(os.path.join(MIRROR, 'dist/emu.wasm'), os.path.join(ROOT, 're-core', 'emu.wasm'))
    print('Done. Run: python3 mirror/serve.py 8007   then open http://localhost:8007/')

main()
