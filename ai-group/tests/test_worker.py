import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker import execute


class WorkerDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cli = self.root / 'fake-cli'
        self.args = SimpleNamespace(codex=str(self.cli), workspace=str(self.root),
            state_dir=str(self.root / 'state'), sandbox='workspace-write',
            network_access=True, timeout=2, members=[], context=[])
        self.job = {'id': 42, 'message': {'id': 1, 'author': 'owner',
            'target': 'codex-fixture-01', 'text': 'diagnostic'}}

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, body):
        self.cli.write_text('#!' + sys.executable + '\n' + body)
        self.cli.chmod(0o700)

    def test_failure_logs_private_and_not_sent_to_room(self):
        self.fake('import sys\nsys.stdin.read()\nprint("private diagnostic", file=sys.stderr)\nsys.exit(1)\n')
        text, failed, delegates = execute(self.job, self.args)
        self.assertTrue(failed)
        self.assertNotIn('private diagnostic', text)
        self.assertNotIn('登录', text)
        log = next((self.root / 'state/logs').glob('*.log'))
        self.assertIn(log.name, text)
        self.assertIn('private diagnostic', log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_timeout_stops_descendant(self):
        marker = self.root / 'child-survived'
        child = 'import time,pathlib;time.sleep(2);pathlib.Path(' + repr(str(marker)) + ').touch()'
        self.fake('import subprocess,sys,time\nsys.stdin.read()\nsubprocess.Popen([sys.executable,"-c",' + repr(child) + '])\ntime.sleep(30)\n')
        self.args.timeout = 0.25
        text, failed, _ = execute(self.job, self.args)
        self.assertTrue(failed)
        self.assertIn('超时', text)
        time.sleep(2.1)
        self.assertFalse(marker.exists())

    def test_success_and_log_retention(self):
        self.fake('import sys,pathlib,json\nsys.stdin.read()\npathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps({"text":"OK","delegates":[]}))\n')
        logs = self.root / 'state/logs'
        logs.mkdir(parents=True, mode=0o700)
        for i in range(24):
            (logs / ('job-old-' + str(i) + '.log')).touch()
        self.assertEqual(execute(self.job, self.args), ('OK', False, []))
        self.assertEqual(len(list(logs.glob('job-*.log'))), 20)

    def test_diagnostic_tail_is_bounded(self):
        self.fake('import sys\nsys.stdin.read()\nsys.stderr.write("x" * 200000 + "END")\nsys.exit(1)\n')
        text, failed, _ = execute(self.job, self.args)
        self.assertTrue(failed)
        log = next((self.root / 'state/logs').glob('*.log'))
        self.assertEqual(log.stat().st_size, 65536)
        self.assertTrue(log.read_text().endswith('END'))
        self.assertNotIn('END', text)


if __name__ == '__main__':
    unittest.main()
