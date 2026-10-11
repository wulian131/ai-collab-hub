"""Local fake jobs only: bounded concurrency, context, durable recovery and presence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker


def until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('condition timed out')
        time.sleep(.01)


class FakeHub:
    def __init__(self, count=0):
        self.lock = threading.Lock()
        self.jobs = [dict(id=i, lease='fake-'+str(i), context=[{'text': 'context-'+str(i)}],
            message=dict(id=i, author='owner', target='codex-fixture-01', text='task-'+str(i), conversation_id='conv-'+str(i))) for i in range(count)]
        self.calls, self.results = [], []
        self.finish_fail = set()
        self.claims = 0
    def call(self, method, path, payload=None):
        with self.lock:
            self.calls.append((path, copy.deepcopy(payload)))
            if path == '/api/jobs/claim':
                self.claims += 1
                return {'job': self.jobs.pop(0) if self.jobs else None}
            if path == '/api/members': return {'members': []}
            if path == '/api/jobs/finish':
                if payload['job_id'] in self.finish_fail: raise OSError('synthetic unavailable')
                self.results.append(copy.deepcopy(payload))
            return {'ok': True}


class ParallelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.args = SimpleNamespace(concurrency=2, interval=.03, workspace=self.tmp.name,
            state_dir=self.tmp.name, timeout=0, sandbox='workspace-write', network_access=False)
        self.release = threading.Event()
        self.runner = None
    def tearDown(self):
        self.release.set()
        if self.runner: self.runner.close()
        self.tmp.cleanup()
    def start(self, hub):
        self.runner = worker.ParallelRunner(hub, self.args, self.root)
        return self.runner

    def test_bounded_parallel_context_and_flags_no_idle_polling(self):
        hub = FakeHub(5)
        active, started = set(), []
        lock = threading.Lock()
        maximum = [0]
        def execute(job, args):
            with lock:
                active.add(job['id']); started.append(job['id']); maximum[0] = max(maximum[0], len(active))
            if job['id'] < 2: self.release.wait(5)
            self.assertEqual(args.context, [{'text': 'context-'+str(job['id'])}])
            args.completed = job['id'] % 2 == 0
            args.blocked = job['id'] % 2 != 0
            with lock: active.remove(job['id'])
            return str(job['id']), False, []
        with patch.object(worker, 'execute', side_effect=execute):
            runner = self.start(hub)
            time.sleep(.06); self.assertEqual(hub.claims, 0)
            runner.notify()
            until(lambda: len(started) == 2)
            self.assertEqual(len(hub.jobs), 3)
            self.assertFalse(hub.results)
            self.release.set()
            until(lambda: len(hub.results) == 5)
            until(lambda: hub.claims >= 7)
            count = hub.claims; time.sleep(.1); self.assertEqual(hub.claims, count)
            self.assertEqual(maximum[0], 2)
            self.assertEqual(sorted(started), list(range(5)))
            for result in hub.results:
                self.assertEqual(result['completed'], result['job_id'] % 2 == 0)
                self.assertEqual(result['blocked'], result['job_id'] % 2 != 0)
            self.assertFalse(hasattr(self.args, 'context'))
            self.assertFalse(hasattr(self.args, 'completed'))
            progress = [p for path,p in hub.calls if path == '/api/jobs/progress']
            self.assertEqual({p['job_id'] for p in progress}, set(range(5)))
            self.assertTrue(all(p['lease'] == 'fake-'+str(p['job_id']) for p in progress))

    def test_new_notification_starts_task_while_first_still_running(self):
        hub = FakeHub(1); started=[]
        def execute(job, args):
            started.append(job['id'])
            if job['id']==0: self.release.wait(5)
            args.completed=True
            return 'done', False, []
        with patch.object(worker,'execute',side_effect=execute):
            runner=self.start(hub); runner.notify()
            until(lambda: started == [0])
            until(lambda: hub.claims >= 2)
            hub.jobs.append(FakeHub(2).jobs[1]); runner.notify()
            until(lambda: len(hub.results)==1)
            self.assertEqual(hub.results[0]['job_id'],1)
            self.assertFalse(self.release.is_set())
            self.release.set(); until(lambda:len(hub.results)==2)

    def test_finish_retry_does_not_block_other_slots_or_repeat_model(self):
        hub=FakeHub(3);hub.finish_fail.add(0); executed=[]
        def execute(job,args):
            executed.append(job['id']);args.completed=True
            return 'result-'+str(job['id']),False,[]
        with patch.object(worker,'execute',side_effect=execute):
            self.start(hub).notify()
            until(lambda: len(hub.results)==2)
            pending=list(self.root.glob('outbox*.json'))
            self.assertEqual(len(pending),1)
            self.assertEqual(json.loads(pending[0].read_text())['job_id'],0)
            self.assertTrue(any(path=='/api/jobs/renew' and p['job_id']==0 for path,p in hub.calls))
            hub.finish_fail.clear();until(lambda:len(hub.results)==3)
            self.assertEqual(sorted(executed),[0,1,2])
            self.assertEqual(list(self.root.glob('outbox*.json')),[])

    def test_legacy_and_removed_slot_results_recovered_without_execute(self):
        self.args.concurrency=1;hub=FakeHub()
        for filename, jid in [('outbox.json',10),('outbox-2.json',12),('outbox-7.json',17)]:
            worker.save_outbox(self.root/filename,dict(job_id=jid,lease='fake',text='saved'))
        with patch.object(worker,'execute') as execute:
            self.start(hub);until(lambda:len(hub.results)==3)
            execute.assert_not_called()
            self.assertEqual({r['job_id'] for r in hub.results},{10,12,17})
            until(lambda: not list(self.root.glob('outbox*.json')))

    def test_rejected_outbox_quarantined_per_slot_without_stalling(self):
        hub=FakeHub(1); original=hub.call
        def call(method,path,payload=None):
            if path=='/api/jobs/finish' and payload['job_id']==42:raise RuntimeError('409: fake')
            return original(method,path,payload)
        hub.call=call
        worker.save_outbox(self.root/'outbox.json',dict(job_id=42,lease='fake',text='saved'))
        with patch.object(worker,'execute',return_value=('new',False,[])):
            self.start(hub);until(lambda:len(hub.results)==1)
            self.assertEqual(len(list(self.root.glob('rejected-outbox-*.json'))),1)

    def test_presence_stays_processing_until_last_task_completes(self):
        hub=FakeHub();activity=worker.ActivityWindow(hub,.03)
        try:
            activity.begin();activity.begin();activity.completed()
            time.sleep(.06)
            self.assertIn('processing',hub.calls[-1][1]['detail'])
            self.assertEqual(activity.active,1)
            activity.completed();until(lambda:'standby' in hub.calls[-1][1]['detail'])
        finally:activity.close()

    def test_preparation_failure_finishes_failed_job_and_slot_continues(self):
        hub=FakeHub(2);original=hub.call; failures=[1]
        def call(method,path,payload=None):
            if path=='/api/members' and failures[0]:
                failures[0]-=1;raise OSError('private error')
            return original(method,path,payload)
        hub.call=call
        with patch.object(worker,'execute',return_value=('ok',False,[])):
            self.start(hub).notify();until(lambda:len(hub.results)==2)
            self.assertEqual(sum(r['failed'] for r in hub.results),1)
            self.assertNotIn('private error',str(hub.results))

    def test_renewals_are_per_job_and_stop_before_finish(self):
        hub=FakeHub(2)
        def execute(job,args):
            until(lambda:sum(path=='/api/jobs/renew' and p['job_id']==job['id'] for path,p in hub.calls)>=2)
            return 'ok',False,[]
        with patch.object(worker,'execute',side_effect=execute):
            runner=self.start(hub)
            with patch.object(worker,'HEARTBEAT_INTERVAL',.015):
                runner.notify();until(lambda:len(hub.results)==2)
            for jid in [0,1]:
                indexed=[(idx,path) for idx,(path,p) in enumerate(hub.calls) if p and p.get('job_id')==jid]
                finish=next(idx for idx,path in indexed if path=='/api/jobs/finish')
                self.assertTrue(all(idx<finish for idx,path in indexed if path=='/api/jobs/renew'))

if __name__=='__main__':unittest.main()
