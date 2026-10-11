import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import APIError
from test_devices import DeviceTest

class RecoveryTest(DeviceTest):
    def test_retry_preserves_failed_message_and_same_task(self):
        sent=self.call('/api/messages',dict(text='task',target='agent'))
        job=self.call('/api/jobs/claim',{},token='agent-test')['job']
        fail=self.call('/api/jobs/finish',dict(job_id=job['id'],lease=job['lease'],text='startup failed',failed=True,completed=False,blocked=True),token='agent-test')
        self.denied(403,'/api/jobs/retry',dict(job_id=job['id'],reason='retry'),token='agent-test')
        self.call('/api/jobs/retry',dict(job_id=job['id'],reason='fault repaired'))
        self.denied(409,'/api/jobs/retry',dict(job_id=job['id'],reason='double click'))
        nextjob=self.call('/api/jobs/claim',{},token='agent-test')['job']
        self.assertEqual(nextjob['id'],job['id']);self.assertEqual(nextjob['attempts'],2)
        self.assertEqual(len([m for m in self.call('/api/messages')['messages'] if m['id']==fail['message']['id']]),1)
        self.denied(409,'/api/jobs/renew',dict(job_id=job['id'],lease=job['lease']),token='agent-test')
        result=self.call('/api/jobs/finish',dict(job_id=nextjob['id'],lease=nextjob['lease'],text='recovered successfully',completed=True),token='agent-test')
        self.assertNotEqual(result['message']['id'],fail['message']['id'])
        self.assertEqual(self.call('/api/jobs')['jobs'][0]['status'],'done')
        duplicate=self.call('/api/jobs/finish',dict(job_id=nextjob['id'],lease=nextjob['lease'],text='recovered successfully',completed=True),token='agent-test')
        self.assertEqual(result['message']['id'],duplicate['message']['id'])
    def test_pause_does_not_interrupt_existing_lease(self):
        self.call('/api/messages',dict(text='one',target='agent'))
        job=self.call('/api/jobs/claim',{},token='agent-test')['job']
        self.call('/api/workers/control',dict(paused=True),token='agent-test')
        self.denied(400,'/api/workers/control',dict(member='owner',paused=True),token='agent-test')
        self.call('/api/messages',dict(text='two',target='agent'))
        self.assertFalse(self.room.has_job('agent'));self.assertIsNone(self.room.claim('agent')['job'])
        self.call('/api/jobs/renew',dict(job_id=job['id'],lease=job['lease']),token='agent-test')
        self.call('/api/workers/control',dict(member='agent',paused=False))
        self.assertTrue(self.room.has_job('agent'))
