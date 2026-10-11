import concurrent.futures
import hashlib
import json
import urllib.error
import test_devices as helper
from server import Room, APIError

class CloudCredentialTests(helper.unittest.TestCase):
    setUp=helper.DeviceTest.setUp
    tearDown=helper.DeviceTest.tearDown
    call=helper.DeviceTest.call
    denied=helper.DeviceTest.denied
    invite=helper.DeviceTest.invite
    def muse(self, **kwargs):return self.invite(type='muse',**kwargs)
    def pair(self,i):return self.call('/api/devices/pair',dict(device_id=i['device_id'],invitation_id=i['invitation_id'],pairing_code=i['pairing_code'],type='muse',protocol=1),token='')
    def item(self,identity):return next(d for d in self.call('/api/devices')['devices'] if d['id']==identity)
    def payload(self, i, key='cloud-request-unique'):
        return dict(device_id=i['device_id'],generation=self.item(i['device_id'])['generation'],key=key,confirm=True)
    def issue(self,payload):return self.call('/api/devices/cloud-credential',payload)
    def legacy_muse(self, enabled=True):
        member=dict(id='muse-legacy',name='Legacy Muse',role='agent',enabled=enabled,
                    token_hash=hashlib.sha256(b'legacy-full-fixture').hexdigest(),
                    read_token_hash=hashlib.sha256(b'legacy-read-fixture').hexdigest())
        self.config['members'].append(member)
        self.room.devices.base_members[member['id']]=member
        self.room.devices.refresh()
        return {'device_id':member['id']}
    def test_legacy_muse_recovery_revokes_both_old_keys_and_survives_restart(self):
        i=self.legacy_muse()
        item=self.item(i['device_id'])
        self.call('/api/devices/action',dict(device_id=i['device_id'],action='permissions',
                  generation=item['generation'],permissions=dict(read=True,execute=False,delegate=False)))
        history=self.call('/api/messages',dict(text='preserved fixture history'))['message']['id']
        new=self.issue(self.payload(i))
        self.assertEqual(new['member_id'],i['device_id'])
        self.assertEqual(new['permissions'],dict(read=True,execute=False,delegate=False))
        self.assertEqual(self.call('/api/members',token=new['token'])['me'],i['device_id'])
        for old in ['legacy-full-fixture','legacy-read-fixture']:
            self.denied(401,'/api/members',token=old)
        other=Room(self.room.database,self.config)
        try:
            self.assertEqual(other.authenticate(new['token'])[0],i['device_id'])
            for old in ['legacy-full-fixture','legacy-read-fixture']:
                with self.assertRaises(APIError):other.authenticate(old)
        finally:other.close()
        self.assertEqual(self.item(i['device_id'])['name'],'Legacy Muse')
        with self.room.db() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM messages WHERE id=?',(history,)).fetchone())
            dump='\n'.join(db.iterdump())
        public=json.dumps(self.call('/api/members'))+json.dumps(self.call('/api/devices'))
        self.assertNotIn(new['token'],dump+public)
        self.assertNotIn(hashlib.sha256(new['token'].encode()).hexdigest(),public)
    def test_legacy_muse_idempotency_and_stale_settings(self):
        i=self.legacy_muse();payload=self.payload(i)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.issue(payload),range(8)))
        self.assertEqual(sum(bool(r.get('token')) for r in results),1)
        self.assertEqual(self.item(i['device_id'])['generation'],1)
        self.denied(409,'/api/devices/cloud-credential',dict(payload,key='new-stale-fixture-key'))
    def test_legacy_disabled_and_retired_are_not_silently_enabled(self):
        i=self.legacy_muse(enabled=False)
        self.denied(409,'/api/devices/cloud-credential',self.payload(i))
        self.call('/api/devices/action',dict(device_id=i['device_id'],action='enable',generation=0))
        self.issue(self.payload(i))
        self.call('/api/devices/action',dict(device_id=i['device_id'],action='archive',confirm=True))
        self.denied(409,'/api/devices/cloud-credential',self.payload(i,'retired-fixture-key'))
    def test_legacy_running_task_cannot_have_credential_rotated(self):
        i=self.legacy_muse()
        self.call('/api/messages',dict(text='legacy fixture task',target=i['device_id']))
        job=self.call('/api/jobs/claim',{},token='legacy-full-fixture')['job']
        self.denied(409,'/api/devices/cloud-credential',self.payload(i))
        self.call('/api/jobs/renew',dict(job_id=job['id'],lease=job['lease']),token='legacy-full-fixture')
    def test_reproduce_lost_paired_token_and_recover_same_member(self):
        i=self.muse();old=self.pair(i)['token'];self.denied(401,'/api/members',token=i['pairing_code'])
        new=self.issue(self.payload(i));identity=i['device_id']
        self.assertEqual(new['member_id'],identity);self.assertTrue(new['secret_available'])
        self.assertEqual(new['permissions'],dict(read=True,execute=False,delegate=False))
        self.assertEqual(self.call('/api/members',token=new['token'])['me'],identity)
        self.denied(401,'/api/members',token=old)
        self.denied(403,'/api/jobs/claim',{},token=new['token'])
        self.assertEqual(self.item(identity)['status'],'waiting')
        self.call('/api/presence',dict(detail='cloud verified'),token=new['token']);self.assertEqual(self.item(identity)['status'],'connected')
        other=Room(self.room.database,self.config)
        try:self.assertEqual(other.authenticate(new['token'])[0],identity)
        finally:other.close()
    def test_invitation_revoked_and_no_plaintext_anywhere_else(self):
        i=self.muse();new=self.issue(self.payload(i));self.denied(401,'/api/devices/pair',dict(device_id=i['device_id'],invitation_id=i['invitation_id'],type='muse',protocol=1,pairing_code=i['pairing_code']),token='')
        with self.room.db() as db:dump='\n'.join(db.iterdump())
        public=json.dumps(self.call('/api/devices'))+json.dumps(self.call('/api/members'))
        self.assertNotIn(new['token'],dump+public);self.assertNotIn(i['pairing_code'],dump+public)
        self.assertEqual(self.item(i['device_id'])['permissions'],new['permissions'])
        self.assertIsNone(self.item(i['device_id'])['invitation_expires'])
    def test_concurrent_same_key_shows_secret_exactly_once(self):
        i=self.muse();payload=self.payload(i)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:self.issue(payload),range(8)))
        self.assertEqual(sum(bool(r.get('token')) for r in results),1)
        self.assertEqual(sum(r['secret_available'] for r in results),1)
        with self.room.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM device_audit WHERE action='cloud_credential'").fetchone()[0],1)
    def test_lost_response_replay_and_explicit_regeneration(self):
        i=self.muse();payload=self.payload(i);lost=self.issue(payload)
        replay=self.issue(payload);self.assertFalse(replay['secret_available']);self.assertNotIn('token',replay)
        replacement=self.issue(self.payload(i,'cloud-new-explicit-key'))
        self.denied(401,'/api/members',token=lost['token']);self.assertEqual(self.call('/api/members',token=replacement['token'])['me'],i['device_id'])
    def test_admin_confirmation_and_type_restrictions(self):
        i=self.muse();data=self.payload(i)
        self.denied(403,'/api/devices/cloud-credential',data,token='agent-test')
        self.room.members['owner']['read_token_hash']=hashlib.sha256(b'read-only-fixture').hexdigest()
        self.denied(403,'/api/devices/cloud-credential',data,token='read-only-fixture')
        self.denied(403,'/api/devices/cloud-credential',data,headers={'Origin':'https://other.example'})
        self.denied(415,'/api/devices/cloud-credential',data,headers={'Content-Type':'text/plain'})
        for confirm in [False,None,1]:self.denied(400,'/api/devices/cloud-credential',dict(data,confirm=confirm))
        for generation in [None,'0',True,-1]:self.denied(400,'/api/devices/cloud-credential',dict(data,generation=generation))
        linux=self.invite(name='linux fixture',key='different-invite-key');self.denied(400,'/api/devices/cloud-credential',self.payload(linux))
        self.denied(400,'/api/devices/cloud-credential',dict(data,device_id='agent'));self.denied(400,'/api/devices/cloud-credential',dict(data,device_id='owner'))
    def test_stale_revision_and_same_key_different_request(self):
        i=self.muse();payload=self.payload(i);self.issue(payload)
        self.denied(409,'/api/devices/cloud-credential',dict(payload,key='stale-generation-key'))
        self.denied(409,'/api/devices/cloud-credential',dict(payload,generation=1))
        self.denied(409,'/api/devices/cloud-credential',dict(payload,key='request-unique'))
    def test_retired_and_no_read_permission_not_reactivated(self):
        i=self.muse(permissions=dict(read=False,execute=False,delegate=False));self.denied(409,'/api/devices/cloud-credential',self.payload(i))
        self.assertEqual(self.item(i['device_id'])['state'],'invited')
        for action in ['archive','delete']:
            j=self.muse(name=action,key=action+'-invite-key');p=self.payload(j);self.call('/api/devices/action',dict(device_id=j['device_id'],action=action,confirm=True));self.denied(409,'/api/devices/cloud-credential',p)
    def test_running_lease_protects_current_credential(self):
        i=self.muse(permissions=dict(read=True,execute=True,delegate=False));token=self.issue(self.payload(i))['token']
        self.call('/api/messages',dict(text='active cloud task',target=i['device_id']))
        job=self.call('/api/jobs/claim',{},token=token)['job']
        payload=self.payload(i,'rotate-running-key');self.denied(409,'/api/devices/cloud-credential',payload)
        self.call('/api/jobs/renew',dict(job_id=job['id'],lease=job['lease']),token=token)
        self.call('/api/jobs/finish',dict(job_id=job['id'],lease=job['lease'],text='done',completed=True),token=token)
        self.issue(payload);self.denied(401,'/api/members',token=token)
    def test_rotation_reauthenticates_notifications(self):
        from notifications import NotificationClient
        i=self.muse();old=self.issue(self.payload(i))['token']
        with NotificationClient(self.url,old) as channel:
            self.issue(self.payload(i,'rotation-notification-key'))
            with self.assertRaises((RuntimeError,EOFError,OSError)):
                for _ in range(4):channel.receive()
    def test_secret_response_is_not_cacheable(self):
        import urllib.request
        i=self.muse();data=json.dumps(self.payload(i)).encode()
        req=urllib.request.Request(self.url+'/api/devices/cloud-credential',data=data,headers={'Authorization':'Bearer owner-test','Content-Type':'application/json'})
        with urllib.request.urlopen(req) as response:self.assertEqual(response.headers['Cache-Control'],'no-store')
