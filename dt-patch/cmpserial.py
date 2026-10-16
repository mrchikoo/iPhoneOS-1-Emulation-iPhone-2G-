import re,sys,collections
n=lambda f:collections.Counter(re.sub(r'(0x)?[0-9a-f]{6,8}\b','H',l) for l in open(f,errors='replace').read().splitlines() if l.strip())
a,b=n(sys.argv[1]),n(sys.argv[2])
print('lines',sum(a.values()),sum(b.values()),'only-orig',list((a-b).elements()),'only-wifi',list((b-a).elements()))
