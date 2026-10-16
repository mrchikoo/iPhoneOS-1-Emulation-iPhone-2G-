import struct,sys
d=open(sys.argv[1],'rb').read()
def parse(o):
    n,c=struct.unpack_from('<II',d,o); o+=8
    props={}
    for _ in range(n):
        name=d[o:o+32].split(b'\0')[0].decode(); l=struct.unpack_from('<I',d,o+32)[0]&0x7fffffff; o+=36
        props[name]=d[o:o+l]; o+=(l+3)&~3
    kids=[]
    for _ in range(c):
        k,o=parse(o); kids.append(k)
    return (props,kids),o
t,_=parse(0)
def show(node,depth=0):
    p,k=node
    nm=p.get('name',b'?').split(b'\0')[0].decode()
    r=p.get('reg',b'')
    regs=['%08x'%x for x in struct.unpack('<%dI'%(len(r)//4),r[:len(r)//4*4])]
    ints=struct.unpack('<%dI'%(len(p.get('interrupts',b''))//4),p.get('interrupts',b'')) if 'interrupts' in p else ()
    print('  '*depth+nm, regs, 'irq',list(ints), p.get('compatible',b'').replace(b'\0',b',').decode())
    for c in k: show(c,depth+1)
show(t)
