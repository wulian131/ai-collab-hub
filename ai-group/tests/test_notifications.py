import hashlib
import io
import json
import pathlib
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Room, make_server, APIError
from notifications import NotificationClient, frame, read_frame


class NotificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'coordinator': 'codex-fixture-01', 'members': [
            {'id': identity, 'name': name, 'role': role, 'aliases': aliases,
             'token_hash': hashlib.sha256(token.encode()).hexdigest()}
            for identity, name, role, token, aliases in [
                ('owner', '你', 'human', 'owner-token', []),
                ('codex-fixture-01', '示例执行者', 'agent', 'codex-token', ['示例 CLI']),
                ('test-cloud-01', '示例云端成员', 'agent', 'muse-token', ['示例云成员'])]]}
        self.room = Room(pathlib.Path(self.tmp.name) / 'room.db', self.config)
        self.server = make_server(self.room, '127.0.0.1', 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.connections = []

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.room.close()
        self.tmp.cleanup()

    def connect(self, token='codex-token'):
        connection = NotificationClient(self.url, token)
        self.connections.append(connection)
        connection.socket.settimeout(2)
        return connection

    def test_worker_waits_without_claiming_until_notification(self):
        import os, subprocess
        claims = []
        original = self.room.claim
        def observe(identity):
            claims.append(time.monotonic())
            return original(identity)
        self.room.claim = observe
        fake = pathlib.Path(self.tmp.name) / 'fake-codex'
        fake.write_text('#!' + sys.executable + '\nimport pathlib,sys\n'
            'pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(\'{"text":"notified reply","delegates":[]}\')\n')
        fake.chmod(0o700)
        env = dict(os.environ, AICOLLABHUB_URL=self.url, AICOLLABHUB_TOKEN='codex-token')
        process = subprocess.Popen([sys.executable, str(ROOT / 'worker.py'), '--enable-execution',
            '--codex', str(fake), '--workspace', self.tmp.name,
            '--state-dir', str(pathlib.Path(self.tmp.name) / 'state'), '--activity-window', '1', '--concurrency', '1'],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not self.room._listeners and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(self.room._listeners)
            time.sleep(.3)
            self.assertEqual(claims, [])
            started = time.monotonic()
            self.room.message('owner', {'text': 'task', 'target': 'codex-fixture-01'})
            while (not self.room.jobs() or self.room.jobs()[0]['status'] != 'done') and time.monotonic() < started + 5:
                time.sleep(.02)
            self.assertEqual(self.room.jobs()[0]['status'], 'done')
            self.assertLess(claims[0] - started, 1)
            while len(claims) < 2 and time.monotonic() < started + 5:
                time.sleep(.02)
            count = len(claims)
            time.sleep(1.3)
            self.assertEqual(len(claims), count)
            with self.room.db() as db:
                detail = db.execute("SELECT detail FROM presence WHERE member='codex-fixture-01'").fetchone()[0]
            self.assertIn('standby', detail)
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_multi_mention_independent_claim_and_completion(self):
        result = self.room.message('owner', {'text': '@codex-fixture-01 @示例云成员 请分别回复', 'key': 'multi', 'dispatch': True})
        message = result['message']
        self.assertEqual(message['targets'], ['codex-fixture-01', 'test-cloud-01'])
        self.assertEqual(len(self.room.jobs()), 2)
        self.assertEqual(self.room.message('owner', {'text': '@codex-fixture-01 @示例云成员 请分别回复', 'key': 'multi', 'dispatch': True}), result)
        jobs = [self.room.claim(identity)['job'] for identity in ['codex-fixture-01', 'test-cloud-01']]
        self.assertEqual(jobs[0]['message_id'], jobs[1]['message_id'])
        for identity, job in zip(['codex-fixture-01', 'test-cloud-01'], jobs):
            self.assertEqual(job['message']['target'], identity)
            self.room.finish(identity, {'job_id': job['id'], 'lease': job['lease'], 'text': 'done'})
        self.assertTrue(all(j['status'] == 'done' for j in self.room.jobs()))

    def test_second_recipient_can_delegate_and_summary_notifies(self):
        self.room.message('owner', {'text': 'multi', 'targets': ['codex-fixture-01', 'test-cloud-01']})
        parent = self.room.claim('test-cloud-01')['job']
        # Finish the first independent branch before the second delegates to it.
        first = self.room.claim('codex-fixture-01')['job']
        self.room.finish('codex-fixture-01', {'job_id': first['id'], 'lease': first['lease'], 'text': 'first'})
        muse = self.connect('muse-token')
        codex = self.connect()
        self.assertFalse(codex.initial['ready'])
        self.room.finish('test-cloud-01', {'job_id': parent['id'], 'lease': parent['lease'],
            'text': '邀请', 'delegates': [{'target': 'codex-fixture-01', 'text': '核对'}]})
        self.assertTrue(codex.receive()['ready'])
        child = self.room.claim('codex-fixture-01')['job']
        self.room.finish('codex-fixture-01', {'job_id': child['id'], 'lease': child['lease'], 'text': '核对完成'})
        while not muse.receive()['ready']:
            pass
        summary = self.room.claim('test-cloud-01')['job']
        self.assertEqual(summary['kind'], 'summary')

    def test_disabled_target_rolls_back_all_recipients_and_exact_mentions(self):
        self.room.members['test-cloud-01']['enabled'] = False
        with self.assertRaises(APIError):
            self.room.message('owner', {'text': '@codex-fixture-01 @示例云成员 test', 'dispatch': True})
        self.assertEqual(self.room.messages(), [])
        with self.assertRaises(APIError):
            self.room.message('owner', {'text': '@codex-fixture-010 test', 'dispatch': True})
        self.room.message('owner', {'text': '@codex-fixture-01 test', 'broadcast': True})
        self.assertEqual(self.room.jobs(), [])

    def test_notification_auth_initial_replay_and_no_periodic_events(self):
        with self.assertRaises(PermissionError):
            self.connect('wrong')
        connection = self.connect()
        self.assertFalse(connection.initial['ready'])
        received = []
        ready = threading.Event()
        def receive():
            received.append(connection.receive())
            ready.set()
        listener = threading.Thread(target=receive, daemon=True)
        listener.start()
        self.assertFalse(ready.wait(.15))
        sent = time.monotonic()
        self.room.message('owner', {'text': 'wake', 'target': 'codex-fixture-01'})
        self.assertTrue(ready.wait(1))
        self.assertTrue(received[0]['ready'])
        self.assertLess(time.monotonic() - sent, 1)
        listener.join()
        connection.close()
        replay = self.connect()
        self.assertTrue(replay.initial['ready'])

    def test_frame_masking_and_bounds(self):
        self.assertEqual(read_frame(io.BytesIO(frame('hello', masked=True)), True), (1, b'hello'))
        with self.assertRaises(ValueError):
            read_frame(io.BytesIO(frame('hello', masked=True)), False)
        with self.assertRaises(ValueError):
            read_frame(io.BytesIO(bytes([0x81, 126]) + struct.pack('!H', 9000)), False)

    def test_legacy_database_migration_preserves_ids_and_leases(self):
        path = pathlib.Path(self.tmp.name) / 'legacy.db'
        with sqlite3.connect(path) as db:
            db.executescript('''CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,author TEXT NOT NULL,
              text TEXT NOT NULL,target TEXT,reply_to INTEGER,depth INTEGER NOT NULL,created REAL NOT NULL,key TEXT,UNIQUE(author,key));
              CREATE TABLE jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,message_id INTEGER UNIQUE NOT NULL,target TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',lease TEXT,lease_until REAL,attempts INTEGER NOT NULL DEFAULT 0,
              reply_id INTEGER,finished REAL,parent_job INTEGER,kind TEXT NOT NULL DEFAULT 'task');
              INSERT INTO messages VALUES(7,'owner','old','codex-fixture-01',NULL,0,1,'old');
              INSERT INTO jobs VALUES(9,7,'codex-fixture-01','running','old-lease',9999999999,1,NULL,NULL,NULL,'task');''')
        room = Room(path, self.config)
        self.assertEqual(room.jobs()[0]['id'], 9)
        with room.db() as db:
            self.assertEqual(db.execute('SELECT lease FROM jobs WHERE id=9').fetchone()[0], 'old-lease')
        room.message('owner', {'text': 'new', 'targets': ['codex-fixture-01', 'test-cloud-01']})
        self.assertEqual(len(room.jobs()), 3)
        room.close()


if __name__ == '__main__':
    unittest.main()
