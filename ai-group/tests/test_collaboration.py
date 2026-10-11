import unittest
import test_notifications

class CollaborationTest(unittest.TestCase):
    setUp = test_notifications.NotificationTest.setUp
    tearDown = test_notifications.NotificationTest.tearDown
    def finish_job(self, identity, completed=True, delegates=None, blocked=False):
        job = self.room.claim(identity)['job']
        self.assertIsNotNone(job)
        result = self.room.finish(identity, {'job_id': job['id'], 'lease': job['lease'], 'text': identity+' result', 'completed': completed, 'blocked': blocked, 'delegates': delegates or []})
        return job, result

    def test_leader_receives_root_and_collaborator_results(self):
        root=self.room.message('owner', {'text':'team', 'targets':['codex-fixture-01','test-cloud-01'],'leader':'codex-fixture-01'})['message']
        self.assertIsNone(self.room.claim('test-cloud-01')['job'])
        job=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(job['collaboration']['participants'],['codex-fixture-01','test-cloud-01'])
        self.room.finish('codex-fixture-01',{'job_id':job['id'],'lease':job['lease'],'text':'分发','completed':False,'delegates':[{'target':'test-cloud-01','text':'核验'}]})
        _,result=self.finish_job('test-cloud-01')
        self.assertEqual(result['message']['targets'],['codex-fixture-01'])
        summary=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(summary['kind'],'summary')
        self.room.finish('codex-fixture-01',{'job_id':summary['id'],'lease':summary['lease'],'text':'继续处理','completed':False})
        next_job=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(next_job['kind'],'continuation')
        self.assertIsNone(self.room.claim('test-cloud-01')['job'])
        self.room.finish('codex-fixture-01',{'job_id':next_job['id'],'lease':next_job['lease'],'text':'核验完成','completed':True})
        self.assertIsNone(self.room.claim('codex-fixture-01')['job'])
        with self.room.db() as db:self.assertEqual(db.execute('SELECT status FROM collaborations WHERE id=?',(root['id'],)).fetchone()[0],'done')

    def test_peer_round_waits_for_everyone_and_stops_after_done(self):
        self.room.message('owner',{'text':'team','targets':['codex-fixture-01','test-cloud-01']})
        _,result=self.finish_job('codex-fixture-01',False)
        self.assertEqual(result['message']['targets'],['codex-fixture-01','test-cloud-01'])
        self.assertIsNone(self.room.claim('codex-fixture-01')['job'])
        self.finish_job('test-cloud-01',True)
        for identity in ['codex-fixture-01','test-cloud-01']:
            job,result=self.finish_job(identity,True)
            self.assertEqual(job['kind'],'continuation')
            self.assertIn('results',job['message']['text'])
        for identity in ['codex-fixture-01','test-cloud-01']:self.assertIsNone(self.room.claim(identity)['job'])

    def test_summary_can_redelegate_and_propagate_completion(self):
        self.room.message('owner',{'text':'team','target':'codex-fixture-01','leader':'codex-fixture-01'})
        self.finish_job('codex-fixture-01',False,[{'target':'test-cloud-01','text':'first'}])
        self.finish_job('test-cloud-01')
        self.finish_job('codex-fixture-01',False,[{'target':'test-cloud-01','text':'second'}])
        self.finish_job('test-cloud-01')
        self.finish_job('codex-fixture-01',True)
        self.assertTrue(all(j['status']=='done' for j in self.room.jobs()))
        self.assertIsNone(self.room.claim('codex-fixture-01')['job'])

    def test_blocked_round_waits_and_duplicate_finish_does_not_continue(self):
        self.room.message('owner',{'text':'team','target':'codex-fixture-01'})
        job,result=self.finish_job('codex-fixture-01',False,blocked=True)
        self.assertEqual(self.room.finish('codex-fixture-01',{'job_id':job['id'],'lease':job['lease'],'text':'duplicate'}),result)
        self.assertIsNone(self.room.claim('codex-fixture-01')['job'])
        with self.room.db() as db:self.assertEqual(db.execute('SELECT status FROM collaborations').fetchone()[0],'blocked')

    def test_manual_dispatch_is_linked_and_returns_to_leader(self):
        self.room.message('owner',{'text':'team','targets':['codex-fixture-01','test-cloud-01'],'leader':'codex-fixture-01'})
        parent=self.room.claim('codex-fixture-01')['job']
        self.room.message('codex-fixture-01',{'text':'manual child','target':'test-cloud-01','reply_to':parent['message_id']})
        self.room.finish('codex-fixture-01',{'job_id':parent['id'],'lease':parent['lease'],'text':'已分派','completed':False})
        _,reply=self.finish_job('test-cloud-01')
        self.assertEqual(reply['message']['targets'],['codex-fixture-01'])
        summary=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(summary['kind'],'summary')
        self.room.finish('codex-fixture-01',{'job_id':summary['id'],'lease':summary['lease'],'text':'done','completed':True})
        self.assertIsNone(self.room.claim('codex-fixture-01')['job'])

    def test_dispatcher_automatically_becomes_leader(self):
        self.room.message('owner',{'text':'team','targets':['codex-fixture-01','test-cloud-01']})
        self.finish_job('codex-fixture-01',False,[{'target':'test-cloud-01','text':'核验'}])
        job,reply=self.finish_job('test-cloud-01',True)
        self.assertEqual(job['collaboration']['leader'],'codex-fixture-01')
        self.assertEqual(reply['message']['targets'],['codex-fixture-01'])
        self.finish_job('test-cloud-01',True)
        self.finish_job('codex-fixture-01',True)
        self.assertIsNone(self.room.claim('test-cloud-01')['job'])
        self.assertIsNone(self.room.claim('codex-fixture-01')['job'])

    def test_legacy_cli_can_report_incomplete_without_script_changes(self):
        import json
        self.room.message('owner',{'text':'task','target':'codex-fixture-01'})
        job=self.room.claim('codex-fixture-01')['job']
        with self.assertRaises(test_notifications.APIError):
            self.room.finish('codex-fixture-01',{'job_id':job['id'],'lease':job['lease'],'text':'@@GROUP_RESULT_V1@@'+json.dumps({'text':'invalid','completed':False,'blocked':False,'lease':'override'})})
        result=self.room.finish('codex-fixture-01',{'job_id':job['id'],'lease':job['lease'],'text':'@@GROUP_RESULT_V1@@'+json.dumps({'text':'还有剩余工作','completed':False,'blocked':False})})
        self.assertEqual(result['message']['text'],'还有剩余工作')
        continuation=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(continuation['kind'],'continuation')
        self.room.finish('codex-fixture-01',{'job_id':continuation['id'],'lease':continuation['lease'],'text':'@@GROUP_RESULT_V1@@'+json.dumps({'text':'需要用户确认','completed':False,'blocked':True})})
        self.assertIsNone(self.room.claim('codex-fixture-01')['job'])

    def test_non_leader_requests_go_to_leader_without_nested_dispatch(self):
        self.room.message('owner',{'text':'team','targets':['codex-fixture-01','test-cloud-01'],'leader':'codex-fixture-01'})
        self.finish_job('codex-fixture-01',False,[{'target':'test-cloud-01','text':'check'}])
        job,reply=self.finish_job('test-cloud-01',False,[{'target':'codex-fixture-01','text':'需要进一步确认'}])
        self.assertEqual(reply['message']['targets'],['codex-fixture-01'])
        self.assertIn('需要负责人继续分派',reply['message']['text'])
        next_job=self.room.claim('codex-fixture-01')['job']
        self.assertEqual(next_job['kind'],'summary')
        self.assertIsNone(self.room.claim('test-cloud-01')['job'])
        self.room.finish('codex-fixture-01',{'job_id':next_job['id'],'lease':next_job['lease'],'text':'finished','completed':True})
