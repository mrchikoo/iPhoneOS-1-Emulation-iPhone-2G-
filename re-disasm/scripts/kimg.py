"""Kernel image helper: VA<->file mapping, symbols, strings, annotated disassembly."""
import struct, sys, os, re
sys.path.insert(0, os.path.dirname(__file__))
from macho import MachO
import capstone
from capstone import arm as carm

KPATH = os.path.join(os.path.dirname(__file__), '..', 'input', 'kernel_1A543a.macho')


class KImage:
    def __init__(self, path=KPATH):
        self.d = open(path, 'rb').read()
        self.m = MachO(self.d)
        self.ranges = []  # (va, size, fileoff)
        for s in self.m.segments:
            for c in s['sects']:
                if c['offset']:
                    self.ranges.append((c['addr'], c['size'], c['offset']))
            if s['filesize']:
                self.ranges.append((s['vmaddr'], s['filesize'], s['fileoff']))
        self.sym = {}
        for (val, t, sect, desc, name) in self.m.symbols():
            if val and (t & 0x0e) == 0x0e or (t & 0x0e) == 0x02 or (t & 0xe) == 0xe:
                self.sym.setdefault(val, name)
        # symtab images inside __PRELINK,__symtab
        base = 0x4b3000
        seg = self.d[base:base + 0x5c000]
        i = -1
        while True:
            i = seg.find(b'\xce\xfa\xed\xfe', i + 1)
            if i < 0:
                break
            try:
                mm = MachO(self.d, base + i)
                if mm.symtab:
                    so, n, st, ss = mm.symtab
                    for k in range(n):
                        strx, nt, ns, nd, nv = struct.unpack_from('<IBBhI', self.d, base + i + so + k * 12)
                        e = self.d.index(b'\0', base + i + st + strx)
                        nm = self.d[base + i + st + strx:e].decode('latin1')
                        if nv:
                            self.sym.setdefault(nv, nm)
            except Exception as ex:
                pass
        self.byname = {v: k for k, v in self.sym.items()}

    def off(self, va):
        for a, sz, fo in self.ranges:
            if a <= va < a + sz:
                return fo + va - a
        if 0xc0222000 <= va < 0xc04da000:
            return va - 0xc0027000
        return None

    def u32(self, va):
        o = self.off(va)
        return None if o is None else struct.unpack_from('<I', self.d, o)[0]

    def u16(self, va):
        o = self.off(va)
        return None if o is None else struct.unpack_from('<H', self.d, o)[0]

    def bytes(self, va, n):
        o = self.off(va)
        return self.d[o:o + n]

    def cstr(self, va, maxlen=200):
        o = self.off(va)
        if o is None:
            return None
        e = self.d.find(b'\0', o, o + maxlen)
        if e < 0:
            return None
        s = self.d[o:e]
        if len(s) >= 3 and all(32 <= b < 127 or b in (9, 10) for b in s):
            return s.decode()
        return None

    def describe(self, v):
        if v in self.sym:
            return self.sym[v]
        if v & 1 and (v & ~1) in self.sym:
            return self.sym[v & ~1] + ' (T)'
        s = self.cstr(v) if v and 0xc0000000 <= v < 0xc0600000 else None
        if s:
            return repr(s[:80])
        return None


def disasm(img, start, end, thumb=False, extra_names=None):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB if thumb else capstone.CS_MODE_ARM)
    md.detail = True
    code = img.bytes(start, end - start)
    out = []
    names = extra_names or {}
    addr = start
    while addr < end:
        got = False
        for ins in md.disasm(code[addr - start:], addr):
            got = True
            ann = ''
            if ins.id == carm.ARM_INS_LDR and len(ins.operands) == 2:
                mem = ins.operands[1]
                if mem.type == carm.ARM_OP_MEM and mem.mem.base == carm.ARM_REG_PC and mem.mem.index == 0:
                    pa = (ins.address + (4 if thumb else 8)) & ~3
                    pa += mem.mem.disp
                    v = img.u32(pa)
                    if v is not None:
                        d = names.get(v) or img.describe(v)
                        ann = '  ; =0x%08x %s' % (v, d or '')
            elif ins.id in (carm.ARM_INS_BL, carm.ARM_INS_B, carm.ARM_INS_BLX) and ins.operands and ins.operands[0].type == carm.ARM_OP_IMM:
                t = ins.operands[0].imm
                d = names.get(t) or img.sym.get(t)
                if d:
                    ann = '  ; ' + d
            if ins.address in names:
                out.append('\n%08x <%s>:' % (ins.address, names[ins.address]))
            out.append('%08x: %08x  %-8s %s%s' % (ins.address, struct.unpack('<I', ins.bytes)[0] if len(ins.bytes) == 4 else struct.unpack('<H', ins.bytes)[0], ins.mnemonic, ins.op_str, ann))
            addr = ins.address + ins.size
        if not got or addr < end:
            out.append('%08x: %08x  .word' % (addr, img.u32(addr) or 0))
            addr += 2 if thumb else 4
    return out


if __name__ == '__main__':
    img = KImage()
    s, e = int(sys.argv[1], 16), int(sys.argv[2], 16)
    for l in disasm(img, s, e, thumb=len(sys.argv) > 3):
        print(l)
