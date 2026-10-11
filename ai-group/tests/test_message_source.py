import unittest
import test_group


class MessageSourceTest(unittest.TestCase):
    setUp = test_group.GroupTest.setUp
    tearDown = test_group.GroupTest.tearDown
    api = test_group.GroupTest.api
    assert_status = test_group.GroupTest.assert_status

    def test_user_and_assistant_source_survives_history_and_claim(self):
        user = self.api('/api/messages', {'text': 'user task', 'target': 'codex-fixture-01', 'source': 'user'})['message']
        assistant = self.api('/api/messages', {'text': 'assistant note', 'broadcast': True, 'source': 'codex'})['message']
        history = self.api('/api/messages')['messages']
        self.assertEqual([m['source'] for m in history], ['user', 'codex'])
        self.assertEqual(user['author'], assistant['author'])
        job = self.api('/api/jobs/claim', {}, 'codex-token')['job']
        self.assertEqual(job['message']['source'], 'user')
        self.assertEqual(self.room.compact_context(job['message'])['source'], 'user')

    def test_agent_cannot_claim_user_source(self):
        self.assert_status(403, '/api/messages', {'text': 'pretend user', 'broadcast': True, 'source': 'user'}, 'codex-token')
        self.assert_status(400, '/api/messages', {'text': 'invalid', 'broadcast': True, 'source': 'invalid'})
        actual = self.api('/api/messages', {'text': 'agent reply', 'broadcast': True}, 'codex-token')['message']
        self.assertEqual(actual['source'], 'agent')

    def test_older_client_is_not_assumed_to_be_user(self):
        message = self.api('/api/messages', {'text': 'unmarked', 'broadcast': True})['message']
        self.assertEqual(message['source'], 'legacy')
