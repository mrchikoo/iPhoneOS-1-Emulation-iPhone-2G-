"""Find vtables in kext data and print slot -> function, naming slots from kernel base vtables."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from kimg import KImage

k = KImage()


def base_slots(sym):
    tv = k.byname[sym]
    out = []
    a = tv + 8
    while True:
        v = k.u32(a)
        if v is None or not (0xc0008000 <= v < 0xc0600000) or (out and v == 0):
            break
        out.append(k.describe(v) or hex(v))
        a += 4
    return out


def find_vtables(lo, hi, textlo, texthi):
    res = []
    a = lo
    while a < hi - 16:
        if k.u32(a) == 0 and k.u32(a + 4) == 0:
            n = 0
            b = a + 8
            while b < hi:
                v = k.u32(b)
                if v and 0xc0008000 <= v < 0xc0600000 and (v & 1 or textlo <= v < texthi or 0xc0222000 <= v < 0xc04da000 or k.describe(v)):
                    n += 1
                    b += 4
                else:
                    break
            if n >= 20:
                res.append((a, n))
                a = b
                continue
        a += 4
    return res


if __name__ == '__main__':
    lo, hi, tl, th = [int(x, 16) for x in sys.argv[1:5]]
    basename = sys.argv[5] if len(sys.argv) > 5 else '__ZTV9IOService'
    bs = base_slots(basename)
    print('base', basename, len(bs))
    for (a, n) in find_vtables(lo, hi, tl, th):
        print('\nVTABLE @%08x slots %d' % (a, n))
        for i in range(n):
            v = k.u32(a + 8 + 4 * i)
            if tl <= v < th:
                print('  [%3d] +0x%03x %08x  %s' % (i, 8 + 4 * i, v, ('overrides ' + bs[i]) if i < len(bs) else 'NEW'))
            elif i >= len(bs) or 0xc0222000 <= v < 0xc04da000:
                print('  [%3d] +0x%03x %08x  NEW-inherited %s' % (i, 8 + 4 * i, v, k.describe(v) or ''))
