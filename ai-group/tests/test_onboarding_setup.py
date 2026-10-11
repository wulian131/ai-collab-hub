import contextlib
import io
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
import test_devices
import pair_device

class SetupTest(unittest.TestCase):
    setUp=test_devices.DeviceTest.setUp
    tearDown=test_devices.DeviceTest.tearDown
    call=test_devices.DeviceTest.call
    invite=test_devices.DeviceTest.invite
    def config_for(self):
        kind='codex-macos' if sys.platform=='darwin' else 'codex-linux'
        inv=self.invite(type=kind)
        conf=dict(protocol=1,url=self.url,device_id=inv['device_id'],invitation_id=inv['invitation_id'],type=kind,permissions=dict(read=True,execute=False))
        return inv,conf,Path(self.tmp.name)/'device'
    def test_setup_and_resume_without_redeeming_again(self):
        inv,conf,root=self.config_for();out=io.StringIO()
        with contextlib.redirect_stdout(out),patch('getpass.getpass',return_value=inv['pairing_code']):pair_device.setup(root,conf)
        before=(root/'credentials.json').read_bytes()
        self.assertTrue((root/'connector.example.json').is_file());self.assertFalse((root/'worker.py').exists())
        with contextlib.redirect_stdout(out),patch('getpass.getpass',side_effect=AssertionError('must not pair twice')):pair_device.setup(root,conf)
        self.assertEqual((root/'credentials.json').read_bytes(),before)
        self.assertNotIn(inv['pairing_code'],out.getvalue());self.assertNotIn('"token"',out.getvalue())
        self.assertEqual(len(self.call('/api/jobs')['jobs']),0)
    def test_failed_download_can_resume_with_same_identity(self):
        inv,conf,root=self.config_for();real=pair_device.request
        def fail(url,*args,**kwargs):
            if url.endswith('/downloads/client.py'):raise RuntimeError('temporary download failure')
            return real(url,*args,**kwargs)
        with contextlib.redirect_stdout(io.StringIO()),patch('getpass.getpass',return_value=inv['pairing_code']),patch.object(pair_device,'request',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'temporary'):pair_device.setup(root,conf)
        before=(root/'credentials.json').read_bytes()
        with contextlib.redirect_stdout(io.StringIO()),patch('getpass.getpass',side_effect=AssertionError('no second pairing')):pair_device.setup(root,conf)
        self.assertEqual(before,(root/'credentials.json').read_bytes());self.assertTrue((root/'connector.example.json').is_file())
    def test_invalid_pair_stops_without_preparing(self):
        inv,conf,root=self.config_for()
        with contextlib.redirect_stdout(io.StringIO()),patch('getpass.getpass',return_value='invalid'),patch.object(pair_device,'prepare') as prep:
            with self.assertRaises(RuntimeError):pair_device.setup(root,conf)
            prep.assert_not_called()
        self.assertFalse((root/'credentials.json').exists())
    def test_existing_insecure_credentials_are_not_replaced(self):
        inv,conf,root=self.config_for();root.mkdir();f=root/'credentials.json';f.write_text('{}');f.chmod(0o644)
        with contextlib.redirect_stdout(io.StringIO()),patch('getpass.getpass') as ask:
            with self.assertRaisesRegex(ValueError,'权限过宽'):pair_device.setup(root,conf)
            ask.assert_not_called()
        self.assertEqual(f.read_text(),'{}')
