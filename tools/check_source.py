#!/usr/bin/env python3
"""Offline source checks; no network calls, runtime credentials or private data."""
from pathlib import Path
import ast,json,re,subprocess,plistlib,sys
import xml.etree.ElementTree as ET
root=Path(__file__).resolve().parents[1]
counts={'python':0,'javascript':0,'json':0,'plist':0,'scheme':0,'png':0}
errors=[]
for p in sorted(root.rglob('*')):
    rel=p.relative_to(root)
    if any(x in ('.git','__pycache__','.build','DerivedData') for x in rel.parts):continue
    if p.is_symlink():errors.append(str(rel)+': symlink');continue
    if not p.is_file():continue
    if '.git' in rel.parts:errors.append(str(rel)+': git metadata')
    if p.suffix in ('.db','.sqlite','.log','.key','.pem','.p12','.pfx') or p.name in ('credentials.json','enrollment.json','members.json','.env'):errors.append(str(rel)+': runtime file')
    try:
        if p.suffix=='.png':
            import struct
            b=p.read_bytes();assert b[:8]==b'\x89PNG\r\n\x1a\n';i=8;chunks=[]
            while i<len(b):n=struct.unpack('>I',b[i:i+4])[0];chunks.append(b[i+4:i+8]);i+=n+12
            assert chunks[0]==b'IHDR' and chunks[-1]==b'IEND' and len(chunks)>=3
            assert all(c==b'IDAT' for c in chunks[1:-1]);counts['png']+=1;continue
        text=p.read_text(encoding='utf-8')
        if p.suffix=='.py':ast.parse(text,filename=str(rel));counts['python']+=1
        if p.suffix=='.json':json.loads(text);counts['json']+=1
        if p.suffix in ('.plist','.xcprivacy'):plistlib.loads(p.read_bytes());counts['plist']+=1
        if p.suffix=='.xcscheme':ET.fromstring(text);counts['scheme']+=1
        js=[text] if p.suffix=='.js' else re.findall(r'<script\b[^>]*>(.*?)</script>',text,re.S|re.I) if p.suffix=='.html' else []
        for code in js:
            if not code.strip():continue
            checked=subprocess.run(['node','--check','--input-type=commonjs'],input=code,text=True,capture_output=True)
            if checked.returncode:raise ValueError('JavaScript syntax check failed')
            counts['javascript']+=1
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,}',text):errors.append(str(rel)+': potential secret')
    except (ValueError,AssertionError,SyntaxError,OSError) as e:errors.append(str(rel)+': '+type(e).__name__)
print(json.dumps({'checks':counts,'errors':errors},ensure_ascii=False,indent=2))
sys.exit(bool(errors))
