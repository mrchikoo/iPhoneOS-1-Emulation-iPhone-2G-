import sys
from PIL import Image
out=sys.argv[1]; files=sys.argv[2:]
ims=[Image.open(f).convert('RGB').resize((160,240)) for f in files]
cols=min(len(ims),8); rows=(len(ims)+cols-1)//cols
S=Image.new('RGB',(cols*160,rows*240))
for i,im in enumerate(ims): S.paste(im,((i%cols)*160,(i//cols)*240))
S.save(out)
