import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_group
from worker import execute

class LeaseRenewalTest(unittest.TestCase):
    setUp = test_group.GroupTest.setUp
    tearDown = test_group.GroupTest.tearDown
    api = test_group.GroupTest.api
    assert_status = test_group.GroupTest.assert_status

    def claim(self):
        self.api('/api/messages', {'text':'long task','target':'codex-fixture-01'})
        return self.api('/api/jobs/claim', {}, 'codex-token')['job']

    def test_authenticated_renewal_extends_lease(self):
        job=self.claim()
        with self.room.db() as db:
            db.execute('UPDATE jobs SET lease_until=lease_until-500 WHERE id=?',(job['id'],))
        payload={'job_id':job['id'],'lease':job['lease']}
        renewed=self.api('/api/jobs/renew',payload,'codex-token')
        self.assertGreaterEqual(renewed['lease_until'],job['lease_until'])
        self.assertIsNone(self.api('/api/jobs/claim',{},'codex-token')['job'])
        self.api('/api/jobs/finish',dict(payload,text='done'),'codex-token')
        self.assert_status(409,'/api/jobs/renew',payload,'codex-token')

    def test_cannot_renew_other_or_expired_lease(self):
        job=self.claim();payload={'job_id':job['id'],'lease':job['lease']}
        self.assert_status(403,'/api/jobs/renew',payload,'muse-token')
        self.assert_status(409,'/api/jobs/renew',dict(payload,lease='wrong'),'codex-token')
        with self.room.db() as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(job['id'],))
        self.assert_status(409,'/api/jobs/renew',payload,'codex-token')

class DependencyTest(unittest.TestCase):
    setUp = test_group.GroupTest.setUp
    tearDown = test_group.GroupTest.tearDown
    api = test_group.GroupTest.api

    def test_dependent_job_waits_for_successful_prior_team(self):
        first=self.api('/api/messages',{'text':'first','target':'codex-fixture-01'})['message']
        running=self.api('/api/jobs/claim',{},'codex-token')['job']
        second=self.api('/api/messages',{'text':'depends','target':'codex-fixture-01'})['message']
        with self.room.db() as db:
            jid=db.execute('select id from jobs where message_id=?',(second['id'],)).fetchone()[0]
            db.execute('insert into job_dependencies values(?,?)',(jid,first['id']))
        self.assertIsNone(self.api('/api/jobs/claim',{},'codex-token')['job'])
        self.api('/api/jobs/finish',{'job_id':running['id'],'lease':running['lease'],'text':'done','completed':True},'codex-token')
        self.assertEqual(self.api('/api/jobs/claim',{},'codex-token')['job']['message']['id'],second['id'])

class UnlimitedExecutionTest(unittest.TestCase):
    def test_zero_timeout_passes_none_to_subprocess(self):
        def run(command, **kwargs):
            self.assertIsNone(kwargs['timeout'])
            Path(command[command.index('--output-last-message')+1]).write_text(json.dumps({'text':'done','delegates':[],'completed':True,'blocked':False}))
            return SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as tmp, patch('worker.run_cli',side_effect=run):
            args=SimpleNamespace(members=[],context=[],codex='codex',sandbox='workspace-write',workspace=tmp,timeout=0,network_access=False)
            text,failed,_=execute({'message':{'id':1,'author':'owner','target':'codex-fixture-01','text':'test'}},args)
            self.assertEqual(text,'done');self.assertFalse(failed)

if __name__=='__main__':unittest.main()
