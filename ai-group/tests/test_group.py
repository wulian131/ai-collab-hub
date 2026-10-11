import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Room, make_server


class GroupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'members': [
            {'id': name, 'name': name, 'role': role,
             'token_hash': hashlib.sha256(token.encode()).hexdigest()}
            for name, role, token in [('owner', 'human', 'human-token'),
                                      ('codex-fixture-01', 'agent', 'codex-token'),
                                      ('test-cloud-01', 'agent', 'muse-token')]]}
        self.room = Room(pathlib.Path(self.tmp.name) / 'room.db', self.config)
        self.room.members['test-cloud-01']['read_token_hash'] = hashlib.sha256(b'muse-read-token').hexdigest()
        self.http = make_server(self.room, '127.0.0.1', 0)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.room.close()
        self.tmp.cleanup()

    def api(self, path, data=None, token='human-token'):
        req = urllib.request.Request(self.url + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            return json.load(response)

    def assert_status(self, status, path, data=None, token='human-token'):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.api(path, data, token)
        self.assertEqual(caught.exception.code, status)

    def test_auth_and_immutable_author(self):
        self.assert_status(401, '/api/messages', token='wrong')
        result = self.api('/api/messages', {'text': 'hello', 'author': 'owner'}, 'codex-token')
        self.assertEqual(result['message']['author'], 'codex-fixture-01')

    def test_human_routing_and_explicit_broadcast(self):
        self.room.coordinator = 'codex-fixture-01'
        self.room.members['test-cloud-01']['aliases'] = ['示例云成员']
        for text in ['示例云成员 请回复', '请一起解决这个问题', '@test-cloud-01 @codex-fixture-01 一起协作']:
            self.assertIsNone(self.room.message('owner', {'text': text})['message']['target'])
        self.assertEqual(self.room.message('owner', {'text': '@test-cloud-01 @codex-fixture-01 一起协作', 'dispatch': True})['message']['targets'], ['test-cloud-01', 'codex-fixture-01'])
        self.assertIsNone(self.room.message('owner', {'text': '今天的公告', 'broadcast': True})['message']['target'])
        self.assertIsNone(self.room.message('test-cloud-01', {'text': '普通讨论 @codex-fixture-01'})['message']['target'])

    def test_disabled_member_cannot_receive_or_claim(self):
        self.room.members['test-cloud-01']['enabled'] = False
        self.assert_status(409, '/api/messages', {'text': 'test', 'target': 'test-cloud-01'})
        self.assert_status(409, '/api/jobs/claim', {}, 'muse-token')
        self.room.ping('test-cloud-01', {})
        member = next(m for m in self.room.member_list() if m['id'] == 'test-cloud-01')
        self.assertFalse(member['online'])
        self.assertNotIn('token_hash', str(self.api('/api/members')))

    def test_target_claim_finish_and_idempotency(self):
        self.api('/api/messages', {'text': 'broadcast'})
        self.assertIsNone(self.api('/api/jobs/claim', {}, 'codex-token')['job'])
        sent = self.api('/api/messages', {'text': 'reply', 'target': 'codex-fixture-01', 'key': 'send-1'})
        repeated = self.api('/api/messages', {'text': 'reply', 'target': 'codex-fixture-01', 'key': 'send-1'})
        self.assertEqual(sent, repeated)
        job = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.assertEqual(job['message']['text'], 'reply')
        self.assertIsNone(self.api('/api/jobs/claim', {}, 'codex-token')['job'])
        finish = {'job_id': job['id'], 'lease': job['lease'], 'text': 'done'}
        self.assert_status(403, '/api/jobs/finish', finish, 'muse-token')
        reply = self.api('/api/jobs/finish', finish, 'codex-token')
        self.assertEqual(reply, self.api('/api/jobs/finish', finish, 'codex-token'))
        self.assertIsNone(self.api('/api/jobs/claim', {}, 'codex-token')['job'])
        self.assertEqual(len(self.api('/api/messages')['messages']), 3)
        self.assertEqual(self.api('/api/jobs')['jobs'][0]['status'], 'done')

    def test_delegation_limits_and_human_context(self):
        first = self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})['message']
        self.assert_status(400, '/api/messages', {'text': 'task', 'target': 'test-cloud-01'}, 'codex-token')
        for index in range(7):
            child = self.api('/api/messages', {'text': 'delegate', 'target': 'test-cloud-01',
                              'reply_to': first['id']}, 'codex-token')['message']
        self.assert_status(409, '/api/messages', {'text': 'request leader instead', 'target': 'codex-fixture-01',
                            'reply_to': child['id']}, 'muse-token')
        self.assert_status(400, '/api/messages', {'text': '', 'target': 'unknown'})
        self.assert_status(400, '/api/messages', {'text': 'x', 'reply_to': 9999})

    def test_restart_and_lease_expiration(self):
        self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})
        old = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        with self.room.db() as db:
            db.execute('UPDATE jobs SET lease_until=0')
        new = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.assertNotEqual(old['lease'], new['lease'])
        self.assert_status(409, '/api/jobs/finish', {'job_id': old['id'],
                'lease': old['lease'], 'text': 'old'}, 'codex-token')
        reopened = Room(pathlib.Path(self.tmp.name) / 'room.db', self.config)
        self.assertEqual(len(reopened.messages()), 1)
        reopened.close()

    def test_concurrent_claim(self):
        self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            self.api('/api/jobs/claim', {}, 'codex-token')['job'])) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(job is not None for job in results), 1)

    def test_mcp_protocol(self):
        env = dict(os.environ, AICOLLABHUB_URL=self.url, AICOLLABHUB_TOKEN='codex-token')
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '2024-11-05'}},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                    {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                     'params': {'name': 'group_send', 'arguments': {'text': 'MCP hello'}}}]
        result = subprocess.run([sys.executable, str(ROOT / 'client.py'), 'mcp'],
            input=''.join(json.dumps(r) + '\n' for r in requests), env=env,
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), 3)
        self.assertIn('tools', replies[1]['result'])
        self.assertFalse(replies[2]['result'].get('isError', False))
        self.assertEqual(self.api('/api/messages')['messages'][0]['text'], 'MCP hello')

    def test_worker_with_fake_cli(self):
        fake = pathlib.Path(self.tmp.name) / 'fake-codex'
        fake.write_text('#!' + sys.executable + '\nimport pathlib,sys\n'
            'assert "--sandbox" in sys.argv\nassert sys.argv[sys.argv.index("--sandbox")+1]=="read-only"\n'
            'assert "AICOLLABHUB_TOKEN" not in __import__("os").environ\n'
            'assert "task" in sys.stdin.read()\n'
            'pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(\'{"text":"worker reply","delegates":[]}\')\n')
        fake.chmod(0o700)
        self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})
        env = dict(os.environ, AICOLLABHUB_URL=self.url, AICOLLABHUB_TOKEN='codex-token')
        result = subprocess.run([sys.executable, str(ROOT / 'worker.py'), '--enable-execution', '--once', '--sandbox', 'read-only',
            '--codex', str(fake), '--workspace', self.tmp.name,
            '--state-dir', str(pathlib.Path(self.tmp.name) / 'state')], env=env,
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.api('/api/messages')['messages'][-1]['text'], 'worker reply')
        self.assertEqual(self.api('/api/jobs')['jobs'][0]['status'], 'done')

    def test_unrestricted_worker_mode_is_rejected_without_claim(self):
        self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})
        result = subprocess.run([sys.executable, str(ROOT / 'worker.py'), '--enable-execution', '--once', '--sandbox', 'danger-full-access',
            '--workspace', self.tmp.name], env=dict(os.environ, AICOLLABHUB_URL=self.url, AICOLLABHUB_TOKEN='codex-token'), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)
        self.assertEqual(self.api('/api/jobs')['jobs'][0]['status'], 'pending')

    def test_delegate_then_automatic_summary(self):
        self.api('/api/messages', {'text': 'work together', 'target': 'codex-fixture-01'})
        parent = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.api('/api/jobs/finish', {'job_id': parent['id'], 'lease': parent['lease'],
            'text': 'inviting Muse', 'delegates': [{'target': 'test-cloud-01', 'text': 'review'}]}, 'codex-token')
        self.assertEqual(self.api('/api/jobs')['jobs'][-1]['status'], 'waiting')
        child = self.api('/api/jobs/claim', {}, 'muse-token')['job']
        self.api('/api/jobs/finish', {'job_id': child['id'], 'lease': child['lease'], 'text': 'reviewed'}, 'muse-token')
        summary = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.assertEqual(summary['kind'], 'summary')
        self.assertIn('reviewed', summary['message']['text'])
        self.api('/api/jobs/finish', {'job_id': summary['id'], 'lease': summary['lease'], 'text': 'final'}, 'codex-token')
        self.assertTrue(all(j['status'] == 'done' for j in self.api('/api/jobs')['jobs']))
        self.assertIsNone(self.api('/api/jobs/claim', {}, 'codex-token')['job'])

    def test_invalid_delegation_rolls_back_entire_finish(self):
        self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})
        job = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.assert_status(400, '/api/jobs/finish', {'job_id': job['id'], 'lease': job['lease'],
            'text': 'stage', 'delegates': [{'target': 'unknown', 'text': 'work'}]}, 'codex-token')
        self.assertEqual(len(self.api('/api/messages')['messages']), 1)
        self.assertEqual(self.api('/api/jobs')['jobs'][0]['status'], 'running')

    def test_summary_waits_for_all_collaborators(self):
        self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})
        job = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.api('/api/jobs/finish', {'job_id': job['id'], 'lease': job['lease'], 'text': 'stage',
            'delegates': [{'target': 'test-cloud-01', 'text': 'A'}, {'target': 'test-cloud-01', 'text': 'B'}]}, 'codex-token')
        first = self.api('/api/jobs/claim', {}, 'muse-token')['job']
        self.api('/api/jobs/finish', {'job_id': first['id'], 'lease': first['lease'], 'text': 'A done'}, 'muse-token')
        self.assertIsNone(self.api('/api/jobs/claim', {}, 'codex-token')['job'])
        second = self.api('/api/jobs/claim', {}, 'muse-token')['job']
        self.api('/api/jobs/finish', {'job_id': second['id'], 'lease': second['lease'], 'text': 'B failed', 'failed': True}, 'muse-token')
        summary = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.assertIn('A done', summary['message']['text'])
        self.assertIn('B failed', summary['message']['text'])
        self.assertEqual(sum(j['kind'] == 'summary' for j in self.api('/api/jobs')['jobs']), 1)

    def test_task_budget_and_presence(self):
        task = self.api('/api/messages', {'text': 'task', 'target': 'codex-fixture-01'})['message']
        for index in range(19):
            self.api('/api/messages', {'text': 'work ' + str(index), 'target': 'test-cloud-01', 'reply_to': task['id']}, 'codex-token')
        self.api('/api/messages', {'text': 'continue beyond former cap', 'target': 'test-cloud-01', 'reply_to': task['id']}, 'codex-token')
        self.api('/api/presence', {'detail': 'test'}, 'muse-token')
        self.assertTrue(next(m for m in self.api('/api/members')['members'] if m['id'] == 'test-cloud-01')['online'])

    def test_reader_cannot_write_or_claim(self):
        self.assertTrue(self.api('/api/members', token='muse-read-token')['readonly'])
        self.assertNotIn('read_token_hash', str(self.api('/api/members')))
        for path, payload in [('/api/messages', {'text': 'wrong'}), ('/api/jobs/claim', {}),
                              ('/api/jobs/finish', {}), ('/api/presence', {})]:
            self.assert_status(403, path, payload, 'muse-read-token')


if __name__ == '__main__':
    unittest.main()
