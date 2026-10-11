import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import export_source
import manage
import onboarding
import pair_device
import test_devices

class BundleTest(unittest.TestCase):
    setUp=test_devices.DeviceTest.setUp
    tearDown=test_devices.DeviceTest.tearDown
    call=test_devices.DeviceTest.call
    invite=test_devices.DeviceTest.invite
    denied=test_devices.DeviceTest.denied
    def bundle(self,inv,**extra):
        payload=dict(device_id=inv['device_id'],invitation_id=inv['invitation_id'],url='https://'+self.url.split('://')[1]);payload.update(extra)
        req=urllib.request.Request(self.url+'/api/devices/bundle',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer owner-test','Content-Type':'application/json'})
        with urllib.request.urlopen(req) as r:return r.read(),r.headers
    def test_bundle_runs_checks_and_contains_no_secret_or_permission_escalation(self):
        kind='codex-macos' if sys.platform=='darwin' else 'codex-linux'
        inv=self.invite(type=kind);data,headers=self.bundle(inv,permissions=dict(read=True,execute=True,delegate=True))
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertIn('attachment',headers['Content-Disposition'])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(set(z.namelist()),{'__main__.py','pair_device.py','device-config.json'})
            conf=json.loads(z.read('device-config.json'));self.assertFalse(conf['permissions']['execute'])
            self.assertNotIn(inv['pairing_code'].encode(),b'\n'.join(z.read(n) for n in z.namelist()))
        path=Path(self.tmp.name)/'connect.pyz';path.write_bytes(data)
        result=subprocess.run([sys.executable,str(path),'--check'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((path.parent/inv['device_id']).exists())
    def test_rejects_wrong_origin_expired_and_revoked_invites(self):
        inv=self.invite();payload=dict(device_id=inv['device_id'],invitation_id=inv['invitation_id'],url='https://elsewhere.example')
        self.denied(400,'/api/devices/bundle',payload)
        payload['url']='https://'+self.url.split('://')[1]
        self.denied(403,'/api/devices/bundle',payload,token='agent-test')
        self.denied(403,'/api/devices/bundle',payload,headers={'Origin':'https://evil.example'})
        payload['invitation_id']='wrong';self.denied(409,'/api/devices/bundle',payload)
        payload['invitation_id']=inv['invitation_id']
        with self.room.db() as db:db.execute('UPDATE device_invitations SET expires=0')
        self.denied(409,'/api/devices/bundle',payload)
        self.call('/api/devices/action',{'device_id':inv['device_id'],'action':'revoke'})
        self.denied(404,'/api/devices/bundle',payload)
    def test_pair_refuses_echoing_terminal(self):
        import getpass
        inv=self.invite();c=dict(protocol=1,url=self.url,device_id=inv['device_id'],invitation_id=inv['invitation_id'],type='codex-linux',permissions=dict(read=True,execute=False))
        root=Path(self.tmp.name)/'hidden-input'
        with patch('getpass.getpass',side_effect=getpass.GetPassWarning('no hidden input')):
            with self.assertRaisesRegex(ValueError,'隐藏输入'):pair_device.pair(root,c)
        self.assertFalse((root/'credentials.json').exists())
    def test_invalid_bundle_parameter_types_are_rejected(self):
        inv=self.invite();self.denied(400,'/api/devices/bundle',dict(device_id=[],url='https://invalid.example'))
        self.denied(400,'/api/devices/bundle',dict(device_id=inv['device_id'],url=[],invitation_id=inv['invitation_id']))
    def test_combined_connect_never_starts_execution(self):
        with patch.object(pair_device,'setup') as setup,patch.object(pair_device,'run',return_value=0) as run,contextlib.redirect_stdout(io.StringIO()):
            for kind,execute in [('codex-linux',False),('muse',True),('codex-linux',True)]:
                run.reset_mock();c=dict(type=kind,permissions=dict(execute=execute));self.assertEqual(pair_device.connect(Path('/tmp/unused'),c),0)
                self.assertEqual(run.call_count,0)
            self.assertEqual(setup.call_count,3)

class PortableSourceTest(unittest.TestCase):
    def test_export_cleanly_bootstraps_without_private_extension(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'review.zip';export_source.export(out)
            with zipfile.ZipFile(out) as z:
                names=z.namelist()
                self.assertEqual(set(names), {'ai-group/'+n for n in list(export_source.FILES)+['tests/test_'+t+'.py' for t in export_source.TESTS]})
                for filename in ('server.py','index.html'):
                    data=z.read('ai-group/'+filename).decode()
                    self.assertNotIn('PRIVATE EXTENSION',data)
                self.assertIn('ai-group/attachments.py',names);self.assertIn('ai-group/onboarding.py',names)
                z.extractall(td)
            root=Path(td)/'ai-group';subprocess.run([sys.executable,'manage.py','init'],cwd=root,check=True,capture_output=True)
            before=(root/'private-data/enrollment.json').read_bytes()
            subprocess.run([sys.executable,'manage.py','init'],cwd=root,check=True,capture_output=True)
            self.assertEqual(before,(root/'private-data/enrollment.json').read_bytes())
            conf=json.loads((root/'private-data/members.json').read_text());self.assertEqual([m['id'] for m in conf['members']],['owner'])
            result=subprocess.run([sys.executable,'manage.py','login'],cwd=root,capture_output=True,text=True)
            self.assertEqual(result.returncode,1);self.assertNotIn(json.loads(before)['owner'],result.stdout+result.stderr)
            code="from pathlib import Path;import json;from server import Room,make_server;r=Room(Path('private-data/room.db'),json.loads(Path('private-data/members.json').read_text()));h=make_server(r,'127.0.0.1',0);h.server_close()"
            subprocess.run([sys.executable,'-c',code],cwd=root,check=True,capture_output=True)
    def test_bootstrap_refuses_nonempty_uninitialized_directory(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);(d/'keep').write_text('unchanged')
            with self.assertRaises(ValueError):manage.prepare(d)
            self.assertEqual((d/'keep').read_text(),'unchanged');self.assertFalse((d/'members.json').exists())
