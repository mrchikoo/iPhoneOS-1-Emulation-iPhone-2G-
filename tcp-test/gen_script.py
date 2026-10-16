#!/usr/bin/env python3
"""gen_script.py URL_SPEC OUT_PREFIX > script
URL_SPEC tokens: plain chars; '[123]' = switch to number keyboard; '[abc]' = back; '[.com]' = .com key.
Generates the full cold-boot -> join LocalNet -> Safari -> type URL -> Go -> screenshots script."""
import sys
row1='qwertyuiop'; row2='asdfghjkl'; row3='zxcvbnm'
X1=[18,50,80,112,144,176,208,240,272,303]; X2=[34,65,97,129,160,192,224,256,288]; X3=[65,97,129,160,192,224,256]
K={}
for i,c in enumerate(row1): K[c]=(X1[i],297)
for i,c in enumerate(row2): K[c]=(X2[i],350)
for i,c in enumerate(row3): K[c]=(X3[i],402)
N={c:(x,297) for c,x in zip('1234567890',[18,48,79,112,145,176,208,240,272,303])}
N.update({'.':(107,457),'/':(160,457),':':(137,402),'-':(192,402)})
spec=sys.argv[1]; pre=sys.argv[2]; page2=float(sys.argv[3]) if len(sys.argv)>3 else 1500
num=False; keys=[]; i=0
while i<len(spec):
    if spec[i]=='[':
        j=spec.index(']',i); tok=spec[i+1:j]; i=j+1
        if tok=='123': keys.append((45,457)); num=True
        elif tok=='abc': keys.append((45,457)); num=False
        elif tok=='.com': keys.append((212,457))
        continue
    c=spec[i]; i+=1
    keys.append(N[c] if (num or c in './') and c in N else K[c])
typing=''.join(f"tap {x} {y}\nwait 15\n" for x,y in keys)
print(f"""wait 2250
tap 277 232
wait 250
tap 160 140
wait 300
tap 100 185
wait 600
shot {pre}_joined
home
wait 100
tap 205 425
wait 300
tap 288 42
wait 100
tap 160 56
wait 150
{typing}wait 100
shot {pre}_typed
tap 280 457
wait 300
shot {pre}_load1
wait {page2:.0f}
shot {pre}_load2
wait 500
shot {pre}_load3
wait 200
quit""")
