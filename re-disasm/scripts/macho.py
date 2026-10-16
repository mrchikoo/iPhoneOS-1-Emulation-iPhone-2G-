"""Minimal 32-bit Mach-O parser (little endian) for the 1A543a kernel."""
import struct, sys

LC_SEGMENT = 1
LC_SYMTAB = 2
LC_UNIXTHREAD = 5


class MachO:
    def __init__(self, data, base=0):
        self.data = data
        self.base = base  # file offset of the header
        magic, cpu, sub, ftype, ncmds, sizecmds, flags = struct.unpack_from('<7I', data, base)
        assert magic == 0xFEEDFACE, hex(magic)
        self.ftype = ftype
        self.segments = []
        self.symtab = None
        off = base + 28
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<2I', data, off)
            if cmd == LC_SEGMENT:
                segname = data[off + 8:off + 24].split(b'\0')[0].decode()
                vmaddr, vmsize, fileoff, filesize, maxp, initp, nsects, sflags = struct.unpack_from('<8I', data, off + 24)
                sects = []
                so = off + 56
                for i in range(nsects):
                    sn = data[so:so + 16].split(b'\0')[0].decode()
                    sg = data[so + 16:so + 32].split(b'\0')[0].decode()
                    addr, ssize, soff, align, reloff, nrel, fl, r1, r2 = struct.unpack_from('<9I', data, so + 32)
                    sects.append(dict(sect=sn, seg=sg, addr=addr, size=ssize, offset=soff, flags=fl))
                    so += 68
                self.segments.append(dict(name=segname, vmaddr=vmaddr, vmsize=vmsize, fileoff=fileoff,
                                          filesize=filesize, sects=sects))
            elif cmd == LC_SYMTAB:
                self.symtab = struct.unpack_from('<4I', data, off + 8)
            off += size

    def symbols(self):
        if not self.symtab:
            return []
        symoff, nsyms, stroff, strsize = self.symtab
        out = []
        for i in range(nsyms):
            strx, ntype, nsect, ndesc, nval = struct.unpack_from('<IBBhI', self.data, symoff + i * 12)
            name = self.data[stroff + strx:self.data.index(b'\0', stroff + strx)].decode('latin1')
            out.append((nval, ntype, nsect, ndesc, name))
        return out


if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    m = MachO(d)
    print('ftype', m.ftype)
    for s in m.segments:
        print('%-16s vm %08x-%08x file %08x+%08x' % (s['name'], s['vmaddr'], s['vmaddr'] + s['vmsize'], s['fileoff'], s['filesize']))
        for c in s['sects']:
            print('   %-16s %-10s %08x size %08x off %08x' % (c['sect'], c['seg'], c['addr'], c['size'], c['offset']))
    print('symtab', m.symtab)
    syms = m.symbols()
    print('nsyms', len(syms))
    # embedded mach-o headers
    i = 0
    while True:
        i = d.find(b'\xce\xfa\xed\xfe', i + 1)
        if i < 0:
            break
        try:
            ft = struct.unpack_from('<I', d, i + 12)[0]
            nc = struct.unpack_from('<I', d, i + 16)[0]
            if nc < 40 and ft < 12:
                print('embedded MH at file %08x ftype %d ncmds %d' % (i, ft, nc))
        except Exception:
            pass
