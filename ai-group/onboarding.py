"""Owner-only, secret-free Python zipapp generation. Nothing is stored on disk."""
import io
import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

def device_bundle(room, payload, public_origin, host, error):
    from pair_device import validate_config
    identity=payload.get('device_id')
    if not isinstance(identity,str) or not re.fullmatch(r'(codex|muse)-device-[a-f0-9]{16}',identity):
        raise error(400,'设备ID无效')
    if not isinstance(payload.get('url'),str) or (payload.get('invitation_id') is not None and not isinstance(payload['invitation_id'],str)):
        raise error(400,'接入参数类型无效')
    d=room.devices.get(identity)
    if not d or d['state'] in ('revoked','archived','deleted'):
        raise error(404,'设备不可接入')
    url=payload.get('url','')
    parsed=urlsplit(url)
    expected=urlsplit(public_origin) if public_origin else None
    if (expected and (parsed.scheme,parsed.netloc)!=(expected.scheme,expected.netloc)) or (not expected and parsed.netloc!=host):
        raise error(400,'接入地址必须指向本站')
    if '//' in parsed.path or any(part in ('.','..') for part in parsed.path.split('/')) or '%' in parsed.path:
        raise error(400,'接入地址路径无效')
    invitation=payload.get('invitation_id')
    with room.db() as db:
        inv=db.execute('SELECT * FROM device_invitations WHERE id=? AND device_id=?',(invitation,identity)).fetchone()
    if d['state']=='invited' and (not inv or inv['revoked'] or inv['consumed'] or inv['expires']<time.time()):
        raise error(409,'邀请已失效，请重发邀请')
    c=dict(protocol=1,url=url,device_id=identity,type=d['type'],invitation_id=invitation,
           permissions=json.loads(d['permissions']))
    try:c=validate_config(c)
    except ValueError as e:raise error(400,str(e))
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('__main__.py','import sys\nfrom pair_device import bundle_main\nsys.exit(bundle_main())\n')
        z.writestr('pair_device.py',Path(__file__).with_name('pair_device.py').read_bytes())
        z.writestr('device-config.json',json.dumps(c,ensure_ascii=False,indent=2))
    return output.getvalue()
