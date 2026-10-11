import concurrent.futures
import json
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from server import Room, APIError
from test_group import GroupTest
from mcp_http import MCPBridge

class ConversationTest(GroupTest):
    def setUp(self):
        super().setUp()
        for c in ('test-aaa','test-bbb'):
            self.api('/api/conversations',{'id':c,'title':c})

    def send(self,c='test-aaa',**kw):
        return self.api('/api/messages',dict(text='unique '+c,conversation_id=c,target='codex-fixture-01',**kw))

    def test_isolation_receipt_context_and_finish(self):
        a=self.send(key='once'); b=self.send('test-bbb',key='twice')
        self.assertEqual(a['receipt']['text'],'收到')
        self.assertEqual(a['receipt']['jobs'][0]['target'],'codex-fixture-01')
        self.assertEqual(self.api('/api/messages')['messages'],[])
        j=self.api('/api/jobs/claim',{},'codex-token')['job']
        self.assertEqual(j['conversation_id'],'test-aaa')
        self.assertEqual({m['conversation_id'] for m in j['context']},{'test-aaa'})
        self.api('/api/presence',{'detail':'processing'},'codex-token')
        self.api('/api/jobs/finish',{'job_id':j['id'],'lease':j['lease'],'text':'result A','completed':True},'codex-token')
        rows=self.api('/api/messages?conversation_id=test-aaa')['messages']
        self.assertEqual([m['text'] for m in rows],['unique test-aaa','result A'])
        self.assertEqual(rows[1]['author'],'codex-fixture-01')
        self.assertEqual(rows[1]['reply_to'],a['message']['id'])
        self.assertEqual(len(self.api('/api/messages?conversation_id=test-bbb')['messages']),1)
        job=self.api('/api/jobs?conversation_id=test-aaa')['jobs'][0]
        self.assertEqual([e['state'] for e in job['events']],['queued','claimed','processing','done'])
        self.assertLessEqual(job['events'][0]['created'],rows[1]['created'])
        self.assert_status(409,'/api/messages',{'text':'cross','conversation_id':'test-bbb','reply_to':a['message']['id'],'broadcast':True})

    def test_concurrent_retry_and_conflict(self):
        payload={'text':'repeat','conversation_id':'test-aaa','target':'codex-fixture-01','key':'retry-key'}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.api('/api/messages',payload),range(12)))
        self.assertEqual(len({r['message']['id'] for r in results}),1)
        self.assertEqual(len(self.api('/api/jobs?conversation_id=test-aaa')['jobs']),1)
        self.assert_status(409,'/api/messages',dict(payload,text='changed'))
        self.assert_status(409,'/api/messages',dict(payload,conversation_id='test-bbb'))
        self.assertEqual(len(self.api('/api/messages?conversation_id=test-bbb')['messages']),0)

    def test_broadcast_mentions_never_dispatch(self):
        self.api('/api/messages',{'text':'@codex-fixture-01 ordinary message','conversation_id':'test-aaa','broadcast':True})
        self.assertEqual(self.api('/api/jobs?conversation_id=test-aaa')['jobs'],[])
        self.assert_status(401,'/api/messages',{'text':'bad','target':'codex-fixture-01'},token='invalid')
        self.assert_status(403,'/api/conversations',{'id':'forbidden','title':'x'},token='muse-read-token')

    def test_pagination_rename_restart(self):
        for n in range(65):
            self.room.message('owner',{'text':'message '+str(n),'conversation_id':'test-aaa','broadcast':True})
        one=self.api('/api/messages?conversation_id=test-aaa&limit=50')
        two=self.api('/api/messages?conversation_id=test-aaa&limit=50&before='+str(one['messages'][0]['id']))
        self.assertTrue(one['has_more']);self.assertFalse(two['has_more'])
        self.assertEqual(len({m['id'] for m in one['messages']+two['messages']}),65)
        self.api('/api/conversations',{'id':'test-aaa','title':'renamed','rename':True})
        self.assertEqual(self.api('/api/conversations?q=renamed')['conversations'][0]['id'],'test-aaa')
        self.assertEqual(self.api('/api/conversations?q=message%2064')['conversations'][0]['id'],'test-aaa')
        other=Room(self.room.database,self.config)
        self.assertEqual(len(other.messages(conversation_id='test-aaa')),65);other.close()

    def test_mcp_defaults_and_explicit_conversation(self):
        bridge=MCPBridge(self.room,'https://example.test');auth={'member':'owner','scope':'ai-group:read ai-group:dispatch'}
        base=bridge.call_tool(auth,'dispatch_task',{'text':'main task','targets':['codex-fixture-01'],'request_id':'mcp-main'})
        other=bridge.call_tool(auth,'dispatch_task',{'text':'A task','targets':['codex-fixture-01'],'request_id':'mcp-A','conversation_id':'test-aaa'})
        self.assertEqual(base['receipt']['text'],'收到')
        self.assertEqual(base['receipt']['jobs'][0]['target'],'codex-fixture-01')
        self.assertEqual(len(bridge.call_tool(auth,'list_tasks',{})['tasks']),1)
        self.assertEqual(bridge.call_tool(auth,'read_messages',{})['messages'][0]['text'],'main task')
        self.assertEqual(bridge.call_tool(auth,'read_messages',{'message_id':other['task_id']})['messages'],[])
        with self.assertRaises(ValueError):bridge.call_tool(auth,'get_task_status',{'task_id':other['task_id']})
        self.assertEqual(bridge.call_tool(auth,'get_task_status',{'task_id':other['task_id'],'conversation_id':'test-aaa'})['task_id'],other['task_id'])
        self.assertEqual(bridge.call_tool(auth,'get_task_status',{'task_id':base['task_id']})['task_id'],base['task_id'])
        self.assertEqual(len(bridge.call_tool(auth,'list_members',{})['members']),3)

    def test_delegation_continuation_stay_in_conversation(self):
        root=self.send()['message'];j=self.room.claim('codex-fixture-01')['job']
        self.room.finish('codex-fixture-01',{'job_id':j['id'],'lease':j['lease'],'text':'invite','delegates':[{'target':'test-cloud-01','text':'subtask'}]})
        child=self.room.claim('test-cloud-01')['job']
        self.assertEqual(child['conversation_id'],'test-aaa')
        self.room.finish('test-cloud-01',{'job_id':child['id'],'lease':child['lease'],'text':'child result'})
        summary=self.room.claim('codex-fixture-01')['job'];self.assertEqual(summary['kind'],'summary');self.assertEqual(summary['conversation_id'],'test-aaa')
        self.room.finish('codex-fixture-01',{'job_id':summary['id'],'lease':summary['lease'],'text':'need next round','completed':False})
        continuation=self.room.claim('codex-fixture-01')['job'];self.assertEqual(continuation['kind'],'continuation');self.assertEqual(continuation['conversation_id'],'test-aaa')
        self.assertEqual(self.room.messages(),[])
        self.room.finish('codex-fixture-01',{'job_id':continuation['id'],'lease':continuation['lease'],'text':'complete','completed':True})

if __name__=='__main__':unittest.main()
