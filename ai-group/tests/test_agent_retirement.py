"""Agent retirement preserves history, releases names and revokes access permanently."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
import urllib.error
sys.path.insert(0,str(Path(__file__).parent))
import test_devices as helper
from server import Room,APIError

class AgentRetirementTest(unittest.TestCase):
    setUp=helper.DeviceTest.setUp
    tearDown=helper.DeviceTest.tearDown
    call=helper.DeviceTest.call
    denied=helper.DeviceTest.denied
    invite=helper.DeviceTest.invite
    pairbody=helper.DeviceTest.pairbody
    pair=helper.DeviceTest.pair
    def action(self,identity,action='delete'):
        return self.call('/api/devices/action',dict(device_id=identity,action=action,confirm=True))
    def test_connected_delete_preserves_history_and_fails_unfinished_jobs(self):
        inv=self.invite(permissions=dict(read=True,execute=True,delegate=False));old=inv['device_id'];token=self.pair(inv)['token']
        original=self.call('/api/messages',dict(text='historical message',broadcast=True),token=token)['message']['id']
        first=self.call('/api/messages',dict(text='running',target=old))['receipt']['jobs'][0]['id']
        second=self.call('/api/messages',dict(text='pending',target=old))['receipt']['jobs'][0]['id']
        lease=self.call('/api/jobs/claim',{},token=token)['job']
        self.call('/api/presence',dict(detail='connected'),token=token)
        self.assertEqual(self.action(old)['state'],'deleted')
        self.denied(401,'/api/members',token=token)
        self.denied(401,'/api/jobs/renew',dict(job_id=first,lease=lease['lease']),token=token)
        self.assertNotIn(old,[d['id'] for d in self.call('/api/devices')['devices']])
        self.denied(404,'/api/devices/bundle',dict(device_id=old,url=self.url.replace('http:','https:')))
        messages=self.call('/api/messages')['messages'];self.assertEqual(next(m for m in messages if m['id']==original)['author'],old)
        with self.room.db() as db:
            jobs=list(db.execute('SELECT * FROM jobs WHERE target=?',(old,)))
            self.assertEqual({j['id'] for j in jobs},{first,second})
            self.assertTrue(all(j['status']=='failed' and not j['completed'] and j['blocked'] and not j['lease'] for j in jobs))
            count=db.execute('SELECT count(*) FROM messages').fetchone()[0]
        self.assertTrue(self.action(old)['replayed'])
        with self.room.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],count)
        self.denied(409,'/api/jobs/retry',dict(job_id=first,reason='must not revive retired identity'))
        new=self.invite(name='Device',key='fresh-identity',permissions=dict(read=True,execute=True,delegate=False));self.assertNotEqual(old,new['device_id'])
        newtoken=self.pair(new)['token'];self.assertIsNone(self.call('/api/jobs/claim',{},token=newtoken)['job'])
    def test_archive_releases_normalized_name_but_keeps_history_label(self):
        inv=self.invite(name='Ｍｙ Ｍａｃ');old=inv['device_id'];self.action(old,'archive')
        new=self.invite(name='my mac',key='normalized-reuse');self.assertNotEqual(old,new['device_id'])
        entries=self.call('/api/devices')['devices'];self.assertEqual(next(d for d in entries if d['id']==old)['name'],'Ｍｙ Ｍａｃ')
        self.assertEqual(self.room.members[old]['state'],'archived')
        self.assertEqual(self.room.mentioned('@my mac '),[new['device_id']])
        self.denied(409,'/api/devices/invite',dict(action='reinvite',device_id=old,key='never-restore'))
        self.denied(401,'/api/devices/pair',self.pairbody(inv),token='')
        self.action(old);self.assertNotIn(old,[d['id'] for d in self.call('/api/devices')['devices']])
    def test_legacy_agent_delete_survives_restart_and_invalidates_both_credentials(self):
        self.room.devices.base_members['agent']['read_token_hash']=hashlib.sha256(b'old-read').hexdigest()
        self.assertTrue(next(d for d in self.call('/api/devices')['devices'] if d['id']=='agent')['legacy'])
        self.action('agent');self.denied(401,'/api/members',token='agent-test');self.denied(401,'/api/members',token='old-read')
        self.assertNotIn('agent',[d['id'] for d in self.call('/api/devices')['devices']])
        second=Room(self.room.database,self.config)
        try:
            with self.assertRaises(APIError):second.authenticate('agent-test')
            self.assertEqual(second.members['agent']['name'],'agent')
            self.assertFalse(second.members['agent']['enabled'])
        finally:second.close()
        self.assertNotEqual(self.invite(name='agent')['device_id'],'agent')
    def test_cross_instance_archive_race_and_repeated_delete_never_restores(self):
        inv=self.invite();second=Room(self.room.database,self.config)
        try:
            self.action(inv['device_id'],'archive')
            created=second.devices.invite('owner',dict(name='Device',type='codex-linux',key='second-room-reuse'))
            self.assertNotEqual(created['device_id'],inv['device_id'])
            self.action(inv['device_id']);self.assertEqual(self.action(inv['device_id'],'archive')['state'],'deleted')
            self.action('agent');self.assertNotIn('agent',[d['id'] for d in second.devices.listing('owner')['devices']])
        finally:second.close()
    def test_preexisting_archived_names_are_released_on_upgrade(self):
        inv=self.invite();old=inv['device_id']
        with self.room.db() as db:db.execute("UPDATE devices SET state='archived' WHERE id=?",(old,))
        upgraded=Room(self.room.database,self.config)
        try:
            new=upgraded.devices.invite('owner',dict(name='Device',type='muse',key='upgrade-new-name'))
            self.assertNotEqual(old,new['device_id'])
            with upgraded.db() as db:self.assertEqual(db.execute('SELECT name_key FROM devices WHERE id=?',(old,)).fetchone()[0],'\0retired:'+old)
        finally:upgraded.close()
    def test_name_reuse_is_atomic_under_concurrent_creation(self):
        inv=self.invite();self.action(inv['device_id'],'archive')
        def create(i):
            try:return self.invite(name='Device',key='concurrent-'+str(i))['device_id']
            except urllib.error.HTTPError as e:return e.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(create,range(8)))
        self.assertEqual(sum(isinstance(r,str) for r in results),1);self.assertEqual(results.count(409),7)
    def test_admin_confirmation_and_owner_protection(self):
        inv=self.invite();payload=dict(device_id=inv['device_id'],action='delete')
        self.denied(400,'/api/devices/action',payload)
        self.denied(403,'/api/devices/action',{**payload,'confirm':True},token='agent-test')
        self.room.members['owner']['read_token_hash']=hashlib.sha256(b'read-only').hexdigest()
        self.denied(403,'/api/devices/action',{**payload,'confirm':True},token='read-only')
        self.denied(403,'/api/devices/action',dict(device_id='owner',action='delete',confirm=True))
        self.assertEqual(self.call('/api/members')['me'],'owner')
    def test_history_limit_counts_active_devices_only(self):
        with self.room.db() as db:
            for i in range(200):
                db.execute('INSERT INTO devices(id,name,name_key,type,machine,tags,permissions,state,created) VALUES(?,?,?,?,?,?,?,?,?)',
                           ('old-'+str(i),'Old '+str(i),'\0retired:old-'+str(i),'muse','','[]',json.dumps(dict(read=True,execute=False,delegate=False)),'archived',time.time()))
        self.assertTrue(self.invite()['device_id'])

if __name__=='__main__':unittest.main()
