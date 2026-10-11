import concurrent.futures
import sqlite3
import unittest
from test_group import GroupTest

class ReceiptTest(GroupTest):
    def test_progress_authorized_and_idempotent(self):
        r=self.api('/api/messages',{'text':'task','target':'test-cloud-01','key':'one'})
        self.assertEqual(r['receipt']['text'],'收到')
        j=self.api('/api/jobs/claim',{},'muse-token')['job']
        p={'job_id':j['id'],'lease':j['lease']}
        self.assert_status(403,'/api/jobs/progress',p,'codex-token')
        self.assert_status(403,'/api/jobs/progress',p,'muse-read-token')
        self.assert_status(409,'/api/jobs/progress',dict(p,lease='wrong'),'muse-token')
        for _ in range(3):self.api('/api/jobs/progress',p,'muse-token')
        self.api('/api/jobs/finish',dict(p,text='failed test',failed=True),'muse-token')
        j=next(row for row in self.api('/api/jobs')['jobs'] if row['id']==p['job_id'])
        self.assertEqual([e['state'] for e in j['events']],['queued','claimed','processing','failed'])
        self.assert_status(409,'/api/jobs/progress',p,'muse-token')
        self.assertIsNone(self.api('/api/jobs/claim',{},'muse-token')['job'])
        with self.room.db() as db:self.assertEqual(db.execute('SELECT status FROM collaborations').fetchone()[0],'failed')

    def test_failed_queue_rolls_back_message_and_receipt(self):
        with self.room.db() as db:
            db.execute("CREATE TRIGGER fail_enqueue BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.assert_status(503,'/api/messages',{'text':'not saved','target':'codex-fixture-01','key':'rollback'})
        self.assertEqual(self.api('/api/messages')['messages'],[])
        self.assertEqual(self.api('/api/jobs')['jobs'],[])
        with self.room.db() as db:db.execute('DROP TRIGGER fail_enqueue')
        r=self.api('/api/messages',{'text':'not saved','target':'codex-fixture-01','key':'rollback'})
        self.assertEqual(r['receipt']['text'],'收到')

    def test_all_member_types_receive_only_one_durable_receipt(self):
        r=self.api('/api/messages',{'text':'all','targets':['codex-fixture-01','test-cloud-01'],'key':'all'})
        self.assertEqual({j['target'] for j in r['receipt']['jobs']},{'codex-fixture-01','test-cloud-01'})
        again=self.api('/api/messages',{'text':'all','targets':['codex-fixture-01','test-cloud-01'],'key':'all'})
        self.assertEqual(r,again)
        self.assertEqual(len(self.api('/api/messages')['messages']),1)
        with self.room.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM job_events WHERE state='queued'").fetchone()[0],2)

if __name__=='__main__':unittest.main()
