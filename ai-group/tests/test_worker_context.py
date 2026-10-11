"""Exercise actual tick -> execute -> fake CLI stdin, without real API credentials."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worker import tick


class ContextFallbackTest(unittest.TestCase):
    def check_case(self, conversation, provided=None, omit=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / 'fake-cli'
            cli.write_text('#!' + sys.executable + '\n'
                'import json,pathlib,sys\n'
                'prompt=sys.stdin.read()\n'
                'pathlib.Path(' + repr(str(root / 'prompt')) + ').write_text(prompt)\n'
                'pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps({"text":"ok","completed":True,"blocked":False,"delegates":[]}))\n')
            cli.chmod(0o700)
            message = dict(id=1, target='codex-fixture-01', author='owner', text='synthetic')
            if conversation is not None:
                message['conversation_id'] = conversation
            job = dict(id=1, lease='synthetic-lease', message=message)
            if not omit:
                job['context'] = provided
            paths = []
            expected = conversation if conversation is not None else 'main'
            def call(method, path, payload=None):
                paths.append(path)
                if path == '/api/jobs/claim': return {'job': job}
                if path == '/api/members': return {'members': []}
                if path.startswith('/api/messages?'):
                    query = parse_qs(urlsplit(path).query)
                    self.assertEqual(query, {'limit': ['20'], 'conversation_id': [expected]})
                    return {'messages': [{'text': 'CONTEXT:' + expected}]}
                return {'ok': True}
            args = SimpleNamespace(codex=str(cli), workspace=tmp, state_dir=str(root / 'state'),
                sandbox='workspace-write', network_access=False, timeout=0)
            self.assertTrue(tick(SimpleNamespace(call=call), args, root / 'outbox.json'))
            prompt = (root / 'prompt').read_text()
            data = json.loads(prompt.split('\n')[1])
            self.assertEqual(data['conversation_id'], expected)
            if not omit and provided is not None:
                self.assertEqual(data['shared_group_context'], provided)
                self.assertFalse(any(p.startswith('/api/messages?') for p in paths))
            else:
                self.assertEqual(data['shared_group_context'], [{'text': 'CONTEXT:' + expected}])
            self.assertFalse((root / 'outbox.json').exists())

    def test_special_characters_cannot_change_query(self):
        self.check_case('sess/A B&conversation_id=main#中文+?', omit=True)

    def test_a_and_b_are_isolated(self):
        for conversation in ['alpha', 'beta']:
            with self.subTest(conversation=conversation): self.check_case(conversation, omit=True)

    def test_legacy_main(self): self.check_case(None, omit=True)
    def test_empty_context_is_authoritative(self): self.check_case('alpha', [])
    def test_supplied_context_is_authoritative(self): self.check_case('beta', [{'text': 'B_ONLY'}])


if __name__ == '__main__': unittest.main()
