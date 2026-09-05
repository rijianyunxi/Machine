"""Summarize raw measurements, calculate opaque color contrast, render contact sheets.
Run using a Python environment with Pillow. No network or application writes.
"""
from pathlib import Path
import json
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parent / 'results'
r = json.loads((ROOT/'results.json').read_text())
def luminance(h):
    c=[int(h.lstrip('#')[i:i+2],16)/255 for i in (0,2,4)]
    c=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in c]
    return sum(a*b for a,b in zip(c,[.2126,.7152,.0722]))
def contrast(a,b):
    x,y=sorted([luminance(a),luminance(b)])
    return round((y+.05)/(x+.05),3)
pairs=[
 ('Light label / white','#85898e','#ffffff'),
 ('Light muted / background','#85898e','#f7f7f8'),
 ('Light muted / input surface','#85898e','#f2f2f2'),
 ('Light blue link / white','#0a7aff','#ffffff'),
 ('Light primary button','#ffffff','#232425'),
 ('Dark CTA gradient endpoint A','#ffffff','#4d9fff'),
 ('Dark CTA gradient endpoint B','#ffffff','#3b82f6'),
 ('Dark log INFO','#64748b','#0b101a'),
 ('Light log INFO','#94a3b8','#0b101a'),
 ('Dark muted / surface','#7a8aa0','#10151f'),
 ('Proposed Light muted / input','#526176','#f2f2f2'),
 ('Proposed Light primary','#ffffff','#2563eb'),
 ('Proposed Light primary hover','#ffffff','#1d4ed8'),
 ('Proposed Dark primary','#0f172a','#60a5fa'),
 ('Proposed Dark primary hover','#0f172a','#93c5fd'),
 ('Proposed Dark secondary','#94a3b8','#1c2534'),
]
summary={
 'primaryCases':len(r['matrix']),
 'unexpectedPageErrors':sum(len(x['errors']) for x in r['matrix']),
 'unmappedRequests':sorted(set(v for x in r['matrix'] for v in x['unmapped'])),
 'pageOverflow':[{'theme':x['theme'],'route':x['route'],'viewport':x['width'],'scrollWidth':x['scrollWidth']} for x in r['matrix'] if x['scrollWidth']>x['width']],
 'contrast':[{'name':n,'foreground':a,'background':b,'ratio':contrast(a,b)} for n,a,b in pairs]
}
(ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
for theme in ['light','dark']:
 for width in [1440,390]:
  files=sorted(ROOT.glob(f'{theme}-{width}-*.png'))
  # Main 13 routes only; supplementary screens are separate evidence.
  files=[f for f in files if f.stem.split(f'{theme}-{width}-')[1] in {x['route'] for x in r['matrix']}]
  tw,th=(360,270) if width==1440 else (195,440)
  sheet=Image.new('RGB',(tw*4,(th+30)*((len(files)+3)//4)),'#dadde2');d=ImageDraw.Draw(sheet)
  for i,f in enumerate(files):
   im=Image.open(f);im=im.crop((0,0,min(width,im.width),min(im.height,1080 if width==1440 else 880)));im.thumbnail((tw,th))
   x=(i%4)*tw;y=(i//4)*(th+30);sheet.paste(im,(x,y+26));d.text((x+5,y+6),f.stem,fill='black')
  sheet.save(ROOT/f'contact-{theme}-{width}.jpg')
