import json
import os
import pathlib
import subprocess
import sys
import test_notifications
import unittest
from types import SimpleNamespace
from worker import execute

class ExecutionTest(unittest.TestCase):
    setUp = test_notifications.NotificationTest.setUp
    tearDown = test_notifications.NotificationTest.tearDown

    def test_settings_survive_delegation_summary_and_continuation(self):
        settings={'*':{'model':'gpt-6.1-sol','reasoning_effort':'low'},'codex-fixture-01':{'reasoning_effort':'high'}}
        message=self.room.message('owner',{'text':'task','target':'codex-fixture-01','execution':settings})['message']
        self.assertEqual(message['execution'],settings)
        job=self.room.claim('codex-fixture-01')['job']
        expected={'model':'gpt-6.1-sol','reasoning_effort':'high'}
        self.assertEqual(job['execution'],expected)
        self.room.finish('codex-fixture-01',{'job_id':job['id'],'lease':job['lease'],'text':'delegate','completed':False,'delegates':[{'target':'test-cloud-01','text':'help'}]})
        child=self.room.claim('test-cloud-01')['job']
        self.assertNotIn('execution',child)
        self.assertEqual(child['collaboration']['execution'],settings)
        self.room.finish('test-cloud-01',{'job_id':child['id'],'lease':child['lease'],'text':'done'})
        summary=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(summary['execution'],expected)
        self.room.finish('codex-fixture-01',{'job_id':summary['id'],'lease':summary['lease'],'text':'continue','completed':False})
        continuation=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(continuation['execution'],expected)

    def test_invalid_settings_and_agent_override_rejected(self):
        invalid=[[],{'test-cloud-01':{'model':'x'}},{'*':{'model':'x; touch /tmp/foo'}},{'*':{'reasoning_effort':'bogus'}},{'*':{'permissions':'full'}},{'*':{'model':None}}]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(test_notifications.APIError):
                self.room.message('owner',{'text':'task','target':'codex-fixture-01','execution':value})
        with self.assertRaises(test_notifications.APIError):
            self.room.message('codex-fixture-01',{'text':'override','broadcast':True,'execution':{'*':{'model':'x'}}})
        self.assertEqual(self.room.jobs(),[])

    def test_worker_passes_overrides_and_retains_native_session(self):
        fake=pathlib.Path(self.tmp.name)/'fake-codex'
        fake.write_text('#!'+sys.executable+'\nimport sys,pathlib,json\n'
            'assert "--ephemeral" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--model")+1]=="test-model"\n'
            'assert \'model_reasoning_effort="high"\' in sys.argv\n'
            'pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps({"text":"ok","delegates":[],"completed":True,"blocked":False}))\n')
        fake.chmod(0o700)
        args=SimpleNamespace(codex=str(fake),sandbox='workspace-write',workspace=self.tmp.name,timeout=10,members=[],context=[])
        answer,failed,delegates=execute({'message':{'id':1,'author':'owner','text':'task','target':'codex-fixture-01'},'execution':{'model':'test-model','reasoning_effort':'high'}},args)
        self.assertFalse(failed,answer)
        self.assertEqual(answer,'ok')

    def test_default_settings_do_not_force_model(self):
        self.room.message('owner',{'text':'task','target':'codex-fixture-01'})
        self.assertEqual(self.room.claim('codex-fixture-01')['job']['execution'],{})
