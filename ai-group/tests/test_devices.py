import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Room, make_server, APIError
import pair_device

class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.config={'members':[dict(id=i,name=i,role=r,token_hash=hashlib.sha256(t.encode()).hexdigest()) for i,r,t in [('owner','human','owner-test'),('agent','agent','agent-test')]]}
        self.room=Room(Path(self.tmp.name)/'room.db',self.config)
        self.http=make_server(self.room,'127.0.0.1',0);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.http.server_port)
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();self.tmp.cleanup()
    def call(self,path,data=None,token='owner-test',headers=None):
        req=urllib.request.Request(self.url+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',**(headers or {})})
        with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
    def denied(self,code,path,data=None,**kw):
        with self.assertRaises(urllib.error.HTTPError) as e:self.call(path,data,**kw)
        self.assertEqual(e.exception.code,code)
    def invite(self,**kwargs):
        data=dict(name='Device',type='codex-linux',machine='test',key='request-unique',permissions=dict(read=True,execute=False,delegate=False));data.update(kwargs)
        return self.call('/api/devices/invite',data)
    def pairbody(self,i):return dict(device_id=i['device_id'],invitation_id=i['invitation_id'],pairing_code=i['pairing_code'],type='codex-linux',protocol=1)
    def pair(self,i):return self.call('/api/devices/pair',self.pairbody(i),token='')
    def test_admin_csrf_json_and_roles(self):
        self.denied(401,'/api/devices',token='invalid');self.denied(403,'/api/devices',token='agent-test')
        self.denied(403,'/api/devices/invite',dict(name='bad'),token='agent-test')
        self.denied(403,'/api/devices/invite',{},headers={'Origin':'https://evil.example'})
        self.denied(415,'/api/devices/invite',{},headers={'Content-Type':'text/plain'})
        self.room.members['owner']['read_token_hash']=hashlib.sha256(b'read').hexdigest()
        self.denied(403,'/api/devices',token='read')
    def test_one_use_concurrent_and_no_secret_storage(self):
        inv=self.invite();body=self.pairbody(inv)
        def exchange(_):
            try:return self.call('/api/devices/pair',body,token='')
            except urllib.error.HTTPError as e:return e.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:r=list(pool.map(exchange,range(8)))
        winners=[x for x in r if isinstance(x,dict)];self.assertEqual(len(winners),1);self.assertEqual(r.count(401),7)
        with self.room.db() as db:
            dump='\n'.join(db.iterdump())
        self.assertNotIn(inv['pairing_code'],dump);self.assertNotIn(winners[0]['token'],dump)
        public=json.dumps(self.call('/api/devices'))+json.dumps(self.call('/api/members'))
        self.assertNotIn(inv['pairing_code'],public);self.assertNotIn('credential_hash',public);self.assertNotIn('secret_hash',public)
    def test_expiration_type_binding_and_revocation(self):
        i=self.invite();b=self.pairbody(i);b['type']='muse';self.denied(409,'/api/devices/pair',b,token='')
        b=self.pairbody(i);b['protocol']=9;self.denied(409,'/api/devices/pair',b,token='')
        with self.room.db() as db:db.execute('UPDATE device_invitations SET expires=?',(time.time()-1,))
        self.denied(401,'/api/devices/pair',self.pairbody(i),token='')
        j=self.call('/api/devices/invite',dict(action='reinvite',device_id=i['device_id'],key='retry-expired'))
        self.call('/api/devices/action',dict(device_id=i['device_id'],action='revoke'))
        self.denied(401,'/api/devices/pair',self.pairbody(j),token='')
    def test_permission_separation_legacy_and_conversations(self):
        i=self.invite();r=self.pair(i);token=r['token']
        self.assertEqual(self.call('/api/members',token=token)['me'],i['device_id'])
        self.denied(403,'/api/jobs/claim',{},token=token)
        self.denied(403,'/api/messages',dict(text='not allowed',broadcast=True),token=token)
        self.denied(403,'/api/messages',dict(text='task',target=i['device_id']))
        self.call('/api/presence',dict(detail='connected'),token=token)
        self.assertEqual(self.call('/api/devices')['devices'][0]['status'],'connected')
        with self.room.db() as db:db.execute('UPDATE presence SET seen=? WHERE member=?',(time.time()-200,i['device_id']))
        self.assertEqual(self.call('/api/devices')['devices'][0]['status'],'offline')
        self.call('/api/presence',dict(detail='connected'),token=token)
        self.assertEqual(self.call('/api/devices')['devices'][0]['status'],'connected')
        self.denied(403,'/api/devices',token=token)
        j=self.invite(name='Executable',key='another-request',permissions=dict(read=True,execute=True,delegate=False));ex=self.pair(j)['token']
        self.call('/api/conversations',dict(id='convo-aa',title='A'));self.call('/api/conversations',dict(id='convo-bb',title='B'))
        m=self.call('/api/messages',dict(text='A task',target=j['device_id'],conversation_id='convo-aa'))
        self.call('/api/messages',dict(text='B secret',broadcast=True,conversation_id='convo-bb'))
        job=self.call('/api/jobs/claim',{},token=ex)['job']
        self.assertEqual({m['conversation_id'] for m in job['context']},{'convo-aa'})
        finish=dict(job_id=job['id'],lease=job['lease'],text='done',completed=True,delegates=[dict(target='agent',text='delegate')])
        self.denied(403,'/api/jobs/finish',finish,token=ex)
        finish['delegates']=[];self.call('/api/jobs/finish',finish,token=ex)
        self.assertEqual(self.call('/api/messages?conversation_id=convo-aa')['messages'][-1]['author'],j['device_id'])
        self.assertEqual(self.call('/api/members',token='agent-test')['me'],'agent')
    def test_idempotency_duplicate_names_rotation_and_restart(self):
        i=self.invite();again=self.invite();self.assertEqual(i['device_id'],again['device_id']);self.assertNotIn('pairing_code',again)
        self.denied(409,'/api/devices/invite',dict(name='different',type='codex-linux',key='request-unique'))
        self.denied(409,'/api/devices/invite',dict(name='Device',type='codex-linux',key='request-duplicate'))
        old=self.pair(i)['token']
        room2=Room(self.room.database,self.config)
        self.assertEqual(room2.authenticate(old)[0],i['device_id'])
        new=self.call('/api/devices/invite',dict(action='reinvite',device_id=i['device_id'],key='rotate-request'))
        self.denied(401,'/api/members',token=old)
        with self.assertRaises(APIError):room2.authenticate(old)
        fresh=self.pair(new)['token'];self.assertEqual(self.call('/api/members',token=fresh)['me'],i['device_id'])
        self.denied(400,'/api/devices/action',dict(action='archive',device_id=i['device_id']))
        self.call('/api/devices/action',dict(action='archive',device_id=i['device_id'],confirm=True))
        self.denied(401,'/api/members',token=fresh)
        room2.close()
    def test_client_restricted_storage_and_no_overwrite(self):
        i=self.invite();c=dict(url=self.url,device_id=i['device_id'],type='codex-linux',protocol=1,invitation_id=i['invitation_id'])
        root=Path(self.tmp.name)/'device'
        with patch('getpass.getpass',return_value=i['pairing_code']):pair_device.pair(root,c)
        self.assertEqual(root.stat().st_mode&0o777,0o700)
        self.assertEqual((root/'credentials.json').stat().st_mode&0o777,0o600)
        pair_device.status(root,c)
        with self.assertRaises(ValueError):pair_device.pair(root,c)
    def test_revoked_notification_reauth(self):
        # The connection must no longer receive readiness signals after revoke.
        from notifications import NotificationClient
        i=self.invite();token=self.pair(i)['token']
        with NotificationClient(self.url,token) as channel:
            self.call('/api/devices/action',dict(action='revoke',device_id=i['device_id']))
            with self.assertRaises((RuntimeError,EOFError,OSError)):
                for _ in range(4):channel.receive()

if __name__=='__main__':unittest.main()
