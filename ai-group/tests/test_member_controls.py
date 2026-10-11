import hashlib
import test_devices as helper
from server import Room, APIError

class MemberControls(helper.unittest.TestCase):
    setUp=helper.DeviceTest.setUp
    tearDown=helper.DeviceTest.tearDown
    call=helper.DeviceTest.call
    denied=helper.DeviceTest.denied
    invite=helper.DeviceTest.invite
    pair=helper.DeviceTest.pair
    pairbody=helper.DeviceTest.pairbody
    def item(self, identity):return next(d for d in self.call('/api/devices')['devices'] if d['id']==identity)
    def payload(self, identity, permissions, generation=None):
        return dict(device_id=identity,action='permissions',generation=self.item(identity)['generation'] if generation is None else generation,permissions=permissions)
    def change(self, identity, permissions, generation=None):return self.call('/api/devices/action',self.payload(identity,permissions,generation))
    def test_legacy_permissions_enforced_persisted_and_replayed(self):
        p=dict(read=True,execute=False,delegate=False)
        self.call('/api/messages',dict(text='pending before readonly',target='agent'))
        self.change('agent',p,0)
        self.assertFalse(self.room.has_job('agent'))
        self.denied(403,'/api/jobs/claim',{},token='agent-test')
        self.denied(403,'/api/messages',dict(text='task',target='agent'))
        self.denied(403,'/api/messages',dict(text='leader task',leader='agent'))
        self.assertFalse(next(m for m in self.call('/api/members')['members'] if m['id']=='agent')['permissions']['execute'])
        self.assertTrue(self.change('agent',p,0)['replayed'])
        with self.room.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM device_audit WHERE action='permissions'").fetchone()[0],1)
        other=Room(self.room.database,self.config)
        try:
            other.authenticate('agent-test')
            with self.assertRaises(APIError):other.devices.permit('agent','execute')
        finally:other.close()
    def test_enable_disabled_legacy_preserves_permissions_and_identity(self):
        self.room.devices.base_members['agent']['enabled']=False
        self.assertEqual(self.item('agent')['state'],'disabled')
        self.denied(409,'/api/members',token='agent-test')
        payload=dict(device_id='agent',action='enable',generation=0)
        self.call('/api/devices/action',payload)
        self.assertEqual(self.call('/api/members',token='agent-test')['me'],'agent')
        self.assertEqual(self.item('agent')['permissions'],dict(read=True,execute=False,delegate=False))
        self.assertTrue(self.call('/api/devices/action',payload)['replayed'])
        other=Room(self.room.database,self.config)
        try:self.assertTrue(other.members['agent']['enabled']);self.assertFalse(other.members['agent']['permissions']['execute'])
        finally:other.close()
    def test_dynamic_permissions_upgrade_and_read_removal(self):
        invitation=self.invite();token=self.pair(invitation)['token'];identity=invitation['device_id']
        self.change(identity,dict(read=True,execute=True,delegate=False))
        self.call('/api/messages',dict(text='executable',target=identity))
        self.assertIsNotNone(self.call('/api/jobs/claim',{},token=token)['job'])
        with self.room.db() as db:db.execute("UPDATE jobs SET status='done' WHERE target=?",(identity,))
        self.change(identity,dict(read=False,execute=False,delegate=False))
        self.denied(403,'/api/members',token=token)
        self.change(identity,dict(read=True,execute=False,delegate=False))
        self.assertEqual(self.call('/api/members',token=token)['me'],identity)
    def test_invited_permissions_and_revoked_requires_fresh_pair(self):
        i=self.invite();identity=i['device_id'];self.change(identity,dict(read=True,execute=True,delegate=False))
        token=self.pair(i)['token']
        self.call('/api/devices/action',dict(action='revoke',device_id=identity))
        self.denied(409,'/api/devices/action',dict(action='enable',device_id=identity,generation=self.item(identity)['generation']))
        self.denied(401,'/api/members',token=token)
        self.change(identity,dict(read=True,execute=False,delegate=False))
        self.denied(401,'/api/members',token=token)
        fresh=self.call('/api/devices/invite',dict(action='reinvite',device_id=identity,key='enable-new-invitation'))
        self.denied(403,'/api/jobs/claim',{},token=self.pair(fresh)['token'])
    def test_stale_edits_cannot_overwrite(self):
        self.change('agent',dict(read=True,execute=False,delegate=False),0)
        self.denied(409,'/api/devices/action',self.payload('agent',dict(read=True,execute=True,delegate=False),0))
        self.change('agent',dict(read=True,execute=True,delegate=False),1)
        self.denied(409,'/api/devices/action',self.payload('agent',dict(read=True,execute=False,delegate=False),0))
    def test_running_job_survives_rejected_downgrade(self):
        self.call('/api/messages',dict(text='running',target='agent'))
        job=self.call('/api/jobs/claim',{},token='agent-test')['job']
        self.denied(409,'/api/devices/action',self.payload('agent',dict(read=True,execute=False,delegate=False),0))
        self.call('/api/jobs/renew',dict(job_id=job['id'],lease=job['lease']),token='agent-test')
        self.call('/api/jobs/finish',dict(job_id=job['id'],lease=job['lease'],text='finished',completed=True),token='agent-test')
        self.change('agent',dict(read=True,execute=False,delegate=False),0)
    def test_authorization_exact_bools_and_hierarchy(self):
        data=self.payload('agent',dict(read=True,execute=False,delegate=False),0)
        self.denied(403,'/api/devices/action',data,token='agent-test')
        self.room.members['owner']['read_token_hash']=hashlib.sha256(b'read').hexdigest()
        self.denied(403,'/api/devices/action',data,token='read')
        self.denied(403,'/api/devices/action',dict(data,device_id='owner'))
        for p in [dict(read=True,execute=1,delegate=False),dict(read=False,execute=True,delegate=False),dict(read=True,execute=False,delegate=True),dict(read=True),dict(read=True,execute=False,delegate=False,other=True)]:self.denied(400,'/api/devices/action',dict(data,permissions=p))
        for g in [None,True,-1,'0']:self.denied(400,'/api/devices/action',dict(data,generation=g))
    def test_retired_stays_retired_and_name_available(self):
        for identity in ['agent',self.invite()['device_id']]:
            self.call('/api/devices/action',dict(action='archive',device_id=identity,confirm=True))
            self.denied(409,'/api/devices/action',dict(action='enable',device_id=identity,generation=0))
            self.denied(409,'/api/devices/action',self.payload(identity,dict(read=True,execute=True,delegate=True),0))
        self.invite(name='agent',key='reuse-archived-name')
    def test_notification_read_permission_reauth_and_no_secrets(self):
        from notifications import NotificationClient
        i=self.invite();identity=i['device_id'];token=self.pair(i)['token']
        with NotificationClient(self.url,token) as channel:
            self.change(identity,dict(read=False,execute=False,delegate=False))
            with self.assertRaises((RuntimeError,EOFError,OSError)):
                for _ in range(4):channel.receive()
        with self.room.db() as db:dump='\n'.join(db.iterdump())
        self.assertNotIn(token,dump);self.assertNotIn(i['pairing_code'],dump)
