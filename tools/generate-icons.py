#!/usr/bin/env python3
"""Original AI Collab Hub geometric asset. Standard library; no external artwork."""
from pathlib import Path
import struct,zlib
root=Path(__file__).resolve().parents[1]
size=1024
nodes=[(310,350),(715,350),(512,700)]
def near_segment(x,y,a,b):
    ax,ay=a;bx,by=b;vx,vy=bx-ax,by-ay
    t=max(0,min(1,((x-ax)*vx+(y-ay)*vy)/(vx*vx+vy*vy)))
    return (x-ax-t*vx)**2+(y-ay-t*vy)**2<24**2
pixels=bytearray()
for y in range(size):
    pixels.append(0)
    for x in range(size):
        color=(22,87,105)
        if any(near_segment(x,y,a,b) for a,b in zip(nodes,nodes[1:]+nodes[:1])):color=(137,224,204)
        if any((x-a)**2+(y-b)**2<88**2 for a,b in nodes):color=(242,250,248)
        if any((x-a)**2+(y-b)**2<42**2 for a,b in nodes):color=(38,143,147)
        pixels.extend(color)
def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels,9))+chunk(b'IEND',b'')
for project in ('ai-group-ios','ai-group-macos'):
    path=root/project/'AIGroup/Assets.xcassets/AppIcon.appiconset/AppIcon.png'
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(png)
print('Generated two original geometric hub assets')
