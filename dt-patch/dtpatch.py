#!/usr/bin/env python3
"""Patch the Apple flattened device tree so AppleS5L8900XSDIO / AppleMRVL868x can start.
Run:  python -I dtpatch.py [--in IN] [--out OUT] [--mac 02:00:00:20:07:01] [--pin PORT.BIT] [--force-pin]
Format: node = u32 nprops, u32 nchildren, props{char name[32]; u32 len (bit31 flag); data padded to 4}, children.
"""
import struct, sys, os, argparse, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

def parse(d, o=0):
    n, c = struct.unpack_from('<II', d, o); o += 8
    props = []                      # [name, rawlen(with flag bits), data]
    for _ in range(n):
        name = d[o:o+32].split(b'\0')[0].decode()
        raw = struct.unpack_from('<I', d, o+32)[0]; l = raw & 0x7fffffff; o += 36
        props.append([name, raw, bytes(d[o:o+l])]); o += (l + 3) & ~3
    kids = []
    for _ in range(c):
        k, o = parse(d, o); kids.append(k)
    return {'props': props, 'kids': kids}, o

def ser(node):
    out = bytearray(struct.pack('<II', len(node['props']), len(node['kids'])))
    for name, raw, data in node['props']:
        out += name.encode().ljust(32, b'\0') + struct.pack('<I', raw)
        out += data + b'\0' * (-len(data) & 3)
    for k in node['kids']: out += ser(k)
    return bytes(out)

def pget(node, name):
    for p in node['props']:
        if p[0] == name: return p
def nname(node):
    p = pget(node, 'name'); return p[2].split(b'\0')[0].decode() if p else '?'
def walk(node, path=''):
    path = path + '/' + nname(node)
    yield path, node
    for k in node['kids']: yield from walk(k, path)
def find(root, name):
    for path, n in walk(root):
        if nname(n) == name: return path, n

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', default=os.path.join(HERE, '../mirror/fw/devicetree_m68ap.bin'))
    ap.add_argument('--out', default=os.path.join(HERE, 'devicetree_m68ap_wifi.bin'))
    ap.add_argument('--mac', default='02:00:00:20:07:01')
    ap.add_argument('--pin', help='reset GPIO as PORT.BIT (hex port, bit 0-7); only used if function-device_reset absent or --force-pin')
    ap.add_argument('--force-pin', action='store_true')
    ap.add_argument('--tx-cal', help='file (>=1024 bytes) to use as tx-calibration; default: drop the all-zero placeholder')
    a = ap.parse_args()
    d = open(a.inp, 'rb').read()
    root, end = parse(d)
    assert ser(root) == d[:end], 'serializer does not round-trip'
    orig = parse(d)[0]
    mac = bytes(int(x, 16) for x in a.mac.split(':'))
    assert len(mac) == 6 and any(mac), 'MAC must be 6 non-zero bytes'
    spath, sdio = find(root, 'sdio')
    _, gpio = find(root, 'gpio')
    gph = pget(gpio, 'AAPL,phandle')[2]
    # phandle used by every other 'GPIO' function: confirm convention
    users = [(p, pn[0], pn[2]) for p, n in walk(root) for pn in n['props']
             if pn[0].startswith('function-') and len(pn[2]) == 16 and pn[2][4:8] == b'OIPG']
    assert users and all(u[2][:4] == gph for u in users), 'GPIO phandle convention mismatch'
    used = {struct.unpack_from('<I', u[2], 8)[0] & 0xffff for u in users if u[2][:4] == gph and u[1] != 'function-device_reset'}
    log = []
    # local-mac-address
    p = pget(sdio, 'local-mac-address')
    if p: log.append('local-mac-address: %s -> %s' % (p[2].hex(), mac.hex())); p[2] = mac; p[1] = 6
    else: sdio['props'].append(['local-mac-address', 6, mac]); log.append('local-mac-address: added ' + a.mac)
    # function-device_reset  <gpio phandle, 'GPIO', pin(port<<8|bit), flags[4]>
    p = pget(sdio, 'function-device_reset')
    if a.pin:
        port, bit = a.pin.split('.'); pin = (int(port, 16) << 8) | int(bit)
    else:
        # sensible default: port 6 bit 7 (0x0607) -- matches Apple's own value for this board; verify unused
        pin = 0x0607
    assert (pin >> 8) <= 0x18 and (pin & 0xff) <= 7, 'pin out of range'
    if pin in used: sys.exit('pin 0x%04x already used by another function' % pin)
    new = gph + b'OIPG' + struct.pack('<I', pin) + bytes([1, 1, 1, 0])   # flags: [1]=polarity-xor 1
    if p is None: sdio['props'].append(['function-device_reset', 16, new]); log.append('function-device_reset: added pin 0x%04x' % pin)
    elif a.force_pin: log.append('function-device_reset: %s -> %s' % (p[2].hex(), new.hex())); p[2] = new; p[1] = 16
    else: log.append('function-device_reset: already present (%s), kept' % p[2].hex())
    # tx-calibration: all-zero first 128 bytes makes start() fail ("Invalid calibration data")
    p = pget(sdio, 'tx-calibration')
    if a.tx_cal:
        cal = open(a.tx_cal, 'rb').read(); assert len(cal) >= 1024
        if p: p[2] = cal; p[1] = len(cal)
        else: sdio['props'].append(['tx-calibration', len(cal), cal])
        log.append('tx-calibration: replaced with %s' % a.tx_cal)
    elif p and not any(p[2][:128]) or (p and all(b == 0xff for b in p[2][:128])):
        sdio['props'].remove(p); log.append('tx-calibration: removed invalid all-%02x placeholder (%d bytes); driver treats absence as non-fatal' % (p[2][0], len(p[2])))
    out = ser(root)
    if len(out) <= len(d): out += b'\0' * (len(d) - len(out))   # keep file size (zero pad)
    open(a.out, 'wb').write(out)
    # ---- verification ----
    nr, nend = parse(out)
    assert ser(nr) == out[:nend] and not any(out[nend:]), 'reparse mismatch'
    A = dict(walk(orig)); B = dict(walk(nr))
    assert list(A) == list(B), 'node set changed'
    for path in A:
        if path == spath: continue
        assert A[path]['props'] == B[path]['props'], 'node differs: ' + path
        assert len(A[path]['kids']) == len(B[path]['kids'])
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '../re-core/dt.py'), a.out], capture_output=True, text=True)
    o = subprocess.run([sys.executable, '-I', os.path.join(HERE, '../re-core/dt.py'), a.inp], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == o.stdout, 'dt.py dump differs/failed: ' + r.stderr
    print('\n'.join(log)); print('wrote %s (%d bytes, tree %d, was %d)' % (a.out, len(out), nend, end))
    print('verified: reparse ok; %d nodes, all non-sdio nodes identical (props+structure); dt.py dump identical to original' % len(A))
    dpos = [k for k in B if k == spath]
    print('sdio props:', [(p[0], len(p[2])) for p in B[spath]['props']])
main()
