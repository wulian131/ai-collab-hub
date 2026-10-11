import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import worker
from client import Client
from server import initialize
import test_devices

class SafeDefaultsTest(unittest.TestCase):
    def test_legacy_environment_cannot_reuse_a_private_connection(self):
        with tempfile.TemporaryDirectory() as td:
            credential = Path(td) / 'legacy.json'
            credential.write_text(json.dumps({'url':'https://legacy.example.invalid','token':'synthetic-legacy-token'}))
            legacy = {'GROUP_URL':'https://legacy.example.invalid', 'GROUP_TOKEN':'synthetic-legacy-token', 'GROUP_CREDENTIAL_FILE':str(credential)}
            with patch.dict(os.environ, legacy, clear=True):
                with self.assertRaisesRegex(ValueError, 'AICOLLABHUB'):
                    Client()
    def test_wire_device_types_match_all_supported_clients(self):
        fixture=test_devices.DeviceTest()
        fixture.setUp()
        try:
            for index,kind in enumerate(('codex-linux','codex-macos','codex-windows','muse')):
                inv=fixture.invite(name='Synthetic device '+str(index),key='protocol-type-'+str(index),type=kind)
                body=fixture.pairbody(inv);body['type']=kind
                paired=fixture.call('/api/devices/pair',body,token='')
                records=fixture.call('/api/devices')['devices']
                device=next(d for d in records if d['id']==inv['device_id'])
                self.assertEqual(device['type'],kind)
                self.assertFalse(device['permissions']['execute'])
                self.assertFalse(device['permissions']['delegate'])
                self.assertEqual(fixture.call('/api/members',token=paired['token'])['me'],inv['device_id'])
        finally:fixture.tearDown()
    def test_initialization_has_only_admin_by_default(self):
        with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
            initialize(Path(td))
            members=json.loads((Path(td)/'members.json').read_text())['members']
            self.assertEqual([(m['id'],m['role']) for m in members],[('owner','human')])
    def test_explicit_legacy_agents_still_default_to_read_only(self):
        with tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
            initialize(Path(td),codex_count=1,muse_count=1)
            agents=[m for m in json.loads((Path(td)/'members.json').read_text())['members'] if m['role']=='agent']
            self.assertEqual(len(agents),2)
            self.assertTrue(all(m['permissions']==dict(read=True,execute=False,delegate=False) for m in agents))
    def test_worker_without_opt_in_never_authenticates_or_claims(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(sys,'argv',['worker','--workspace',td]),patch.object(worker,'Client') as client,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as e:worker.main()
                self.assertEqual(e.exception.code,2);client.assert_not_called()
    def test_opted_in_worker_rejects_root(self):
        with patch.object(sys,'argv',['worker','--workspace','.','--enable-execution']),patch.object(worker.os,'geteuid',return_value=0,create=True),patch.object(worker,'Client') as client,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):worker.main()
            client.assert_not_called()
    def test_default_opt_in_execution_is_read_only_and_single_slot(self):
        with tempfile.TemporaryDirectory() as td:
            seen=[]
            def tick(client,args,outbox):seen.append((args.sandbox,args.concurrency,args.network_access,args.timeout))
            argv=['worker','--workspace',td,'--state-dir',td+'/state','--enable-execution','--once']
            with patch.object(sys,'argv',argv),patch.object(worker.os,'geteuid',return_value=1000,create=True),patch.object(worker,'Client'),patch.object(worker,'ActivityWindow'),patch.object(worker,'tick',side_effect=tick):worker.main()
            self.assertEqual(seen,[('read-only',1,False,900)])
    def test_redirect_does_not_forward_authentication(self):
        received=[]
        class Target(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):received.append(self.headers.get('Authorization'));self.send_response(200);self.end_headers();self.wfile.write(b'{}')
        target=ThreadingHTTPServer(('127.0.0.1',0),Target)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):self.send_response(302);self.send_header('Location','http://127.0.0.1:'+str(target.server_port)+'/capture');self.end_headers()
        redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (target,redirect)]
        for t in threads:t.start()
        try:
            with patch.dict(os.environ,{'AICOLLABHUB_URL':'http://127.0.0.1:'+str(redirect.server_port),'AICOLLABHUB_TOKEN':'synthetic-fixture-token','AICOLLABHUB_CREDENTIAL_FILE':''}):
                with self.assertRaisesRegex(RuntimeError,'302'):Client().call('GET','/api/members')
            self.assertEqual(received,[])
        finally:
            for s in (target,redirect):s.shutdown();s.server_close()
            for t in threads:t.join()
