import concurrent.futures
import json
import sqlite3
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Room, APIError

class ConversationMembersTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'room.db'
        self.config={'members':[dict(id=i,name=i,role=r,enabled=e) for i,r,e in [('owner','human',True),('a','agent',True),('b','agent',True),('reader','agent',True),('disabled','agent',False)]]}
        self.config['members'][3]['permissions']={'read':True,'execute':False,'delegate':False}
        self.room=Room(self.path,self.config)
    def tearDown(self):self.room.close();self.tmp.cleanup()
    def create(self,identity='new-room-1',participants=None):
        return self.room.save_conversation('owner',dict(id=identity,title='Team',participants=participants))['conversation']
    def test_persistence_rename_read_metadata_and_no_tasks(self):
        c=self.create(participants=['b','a']);self.assertEqual(c['participants'],['a','b'])
        self.assertEqual(self.room.jobs(c['id']),[])
        renamed=self.room.save_conversation('owner',dict(id=c['id'],title='Renamed',rename=True))['conversation'];self.assertEqual(renamed['participants'],['a','b'])
        self.room.close();self.room=Room(self.path,self.config)
        self.assertEqual(next(c for c in self.room.conversations()['conversations'] if c['id']=='new-room-1')['participants'],['a','b'])
    def test_concurrent_retry_freezes_participants(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            result=list(pool.map(lambda _:self.create(participants=['b','a']),range(8)))
        self.assertEqual(len({c['id'] for c in result}),1)
        for changed in [dict(id='new-room-1',title='Team',participants=['b']),dict(id='new-room-1',title='Changed',participants=['a','b'])]:
            with self.assertRaises(APIError) as e:self.room.save_conversation('owner',changed)
            self.assertEqual(e.exception.status,409)
    def test_validation_readonly_and_nonhuman(self):
        for participants in [[],['owner'],['missing'],['disabled'],'a',[True],['a']*21]:
            with self.assertRaises(APIError):self.create(participants=participants)
        c=self.create(participants=['reader']);self.assertEqual(c['participants'],['reader'])
        with self.assertRaises(APIError):self.room.save_conversation('a',dict(id='agent-room',title='Team',participants=['a']))
    def test_scope_rejects_outside_targets_and_leader(self):
        c=self.create(participants=['a'])
        for payload in [dict(targets=['b']),dict(targets=['a'],leader='b')]:
            with self.assertRaises(APIError) as e:self.room.message('owner',dict(text='task',conversation_id=c['id'],**payload))
            self.assertEqual(e.exception.status,403)
        self.room.message('owner',dict(text='task',conversation_id=c['id'],targets=['a']))
        self.assertEqual(self.room.jobs(c['id'])[0]['target'],'a')
    def test_existing_scope_never_changes_after_member_disabled(self):
        c=self.create(participants=['a']);self.room.members['a']['enabled']=False
        again=self.create(participants=['a']);self.assertEqual(again['id'],c['id'])
        with self.assertRaises(APIError):self.create(identity='another-room',participants=['a'])
    def test_old_four_column_database_migrates_and_old_calls_still_work(self):
        self.room.close()
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE conversations DROP COLUMN participants')
        self.room=Room(self.path,self.config)
        main=next(c for c in self.room.conversations()['conversations'] if c['id']=='main');self.assertIsNone(main['participants'])
        c=self.create();self.assertIsNone(c['participants'])
        self.room.message('owner',dict(text='old client task',conversation_id=c['id'],targets=['a','b']))
        self.assertEqual(len(self.room.jobs(c['id'])),2)

if __name__=='__main__':unittest.main()
