"""Windows resources derived from ClearPage's existing vector brand."""
from pathlib import Path
import sys
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clearpage.app_version import VERSION


def icon():
    # The same shapes/colors as static/favicon.svg, rendered at 8x for clean ICO sizes.
    scale=8
    image=Image.new('RGBA',(40*scale,40*scale),(0,0,0,0));draw=ImageDraw.Draw(image)
    points=lambda values:[(int(x*scale),int(y*scale)) for x,y in values]
    draw.rounded_rectangle((0,0,40*scale-1,40*scale-1),radius=11*scale,fill='#d92d20')
    draw.rounded_rectangle((10*scale,8*scale,28*scale,32*scale),radius=2*scale,fill='white')
    draw.polygon(points([(21,8),(28,8),(28,15)]),fill='#d92d20')
    draw.line(points([(21,8),(21,14),(22,15),(28,15)]),fill='#d92d20',width=13)
    for end,y in ((23,21),(20,25)):
        draw.line(points([(15,y),(end,y)]),fill='#d92d20',width=14)
    star=points([(29,5),(30.7,9.3),(35,11),(30.7,12.7),(29,17),(27.3,12.7),(23,11),(27.3,9.3)])
    draw.polygon(star,fill='white');draw.line(star+[star[0]],fill='#d92d20',width=10,joint='curve')
    image=image.resize((256,256),Image.Resampling.LANCZOS)
    image.save(ROOT/'assets/clearpage.ico',format='ICO',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


def version_resource(path, owner_test=False):
    parts=tuple(map(int,VERSION.split('.')));parts=(parts+(0,0,0,0))[:4]
    fields={'CompanyName':'Raysan sure','FileDescription':'ClearPage — Private PDF Cleanup'+(' (Owner Test)' if owner_test else ''),
        'FileVersion':VERSION,'InternalName':'ClearPage','OriginalFilename':'ClearPage.exe',
        'ProductName':'ClearPage','ProductVersion':VERSION,'LegalCopyright':'Copyright © 2026 Raysan sure'}
    entries=','.join(f'StringStruct({key!r},{value!r})' for key,value in fields.items())
    Path(path).write_text(f'VSVersionInfo(ffi=FixedFileInfo(filevers={parts!r},prodvers={parts!r},mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[StringFileInfo([StringTable("040904B0",[{entries}])]),VarFileInfo([VarStruct("Translation",[1033,1200])])])',encoding='utf-8')


if __name__=='__main__':icon()
