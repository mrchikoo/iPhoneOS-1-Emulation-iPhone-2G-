"""Split an annotated listing into functions (heuristic: push {...lr}) and summarise each."""
import sys, re

lines = open(sys.argv[1]).read().splitlines()
funcs = []
cur = None
prev_term = True
for l in lines:
    m = re.match(r'([0-9a-f]{8}): ([0-9a-f]{8})  (\S+)\s*(.*)', l)
    if not m:
        continue
    addr, word, mn, ops = m.groups()
    is_push = (mn in ('push', 'stmdb') and 'lr' in ops)
    if is_push and prev_term:
        cur = dict(addr=addr, strs=[], vcalls=[], calls=[], n=0)
        funcs.append(cur)
    if cur is not None:
        cur['n'] += 1
        s = re.search(r"; =0x[0-9a-f]+ ('.*'|\".*\")", l)
        if s:
            cur['strs'].append(s.group(1)[:70])
        s = re.search(r'ldr\s+pc, \[\w+, #(0x[0-9a-f]+)\]', l)
        if s:
            cur['vcalls'].append(s.group(1))
        s = re.search(r'; =0x[0-9a-f]+ (_\S+)', l)
        if s:
            cur['calls'].append(s.group(1))
    prev_term = mn.startswith('pop') or mn in ('bx', 'b') or (mn == 'ldm' and 'pc' in ops) or word.startswith('c0') or word.startswith('0000') or 'pc}' in ops
    if mn == 'push' and not prev_term:
        pass

for f in funcs:
    print('%s n=%d' % (f['addr'], f['n']))
    if f['strs']:
        print('    S:', ' | '.join(dict.fromkeys(f['strs'])))
    if f['calls']:
        print('    C:', ' '.join(dict.fromkeys(f['calls'])))
    if f['vcalls']:
        print('    V:', ' '.join(f['vcalls']))
