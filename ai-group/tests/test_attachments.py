import json
import sqlite3
import urllib.error
import urllib.request
import uuid
import unittest
import test_group
from attachments import CHUNK, MAX_FILE

class AttachmentTest(unittest.TestCase):
    setUp = test_group.GroupTest.setUp
    tearDown = test_group.GroupTest.tearDown
    api = test_group.GroupTest.api
    assert_status = test_group.GroupTest.assert_status
    def upload(self, data=b'hello\x00world', name='示例.txt', conversation='main', token='human-token'):
        identity=str(uuid.uuid4())
        self.api('/api/attachments',dict(id=identity,name=name,size=len(data),conversation_id=conversation),token)
        for offset in range(0,len(data),CHUNK):
            self.chunk(identity,offset,data[offset:offset+CHUNK],token)
        return identity

    def chunk(self,identity,offset,data,token='human-token'):
        request=urllib.request.Request(self.url+'/api/attachments/'+identity+'?offset='+str(offset),data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/octet-stream'})
        with urllib.request.urlopen(request) as response:return json.load(response)

    def download(self,identity,token='human-token'):
        request=urllib.request.Request(self.url+'/api/attachments/'+identity,headers={'Authorization':'Bearer '+token})
        return urllib.request.urlopen(request)

    def test_attachment_roundtrip_empty_text_and_retry(self):
        data=b'\x00hello\xff'+b'x'*(CHUNK+12)
        identity=self.upload(data,'中文 <img onerror=x>.html')
        payload=dict(text='',attachments=[identity],key='attachment-send',broadcast=True)
        sent=self.api('/api/messages',payload)
        self.assertEqual(sent,self.api('/api/messages',payload))
        self.assertEqual(sent['receipt']['jobs'],[])
        with self.download(identity,'muse-read-token') as response:
            self.assertEqual(response.read(),data)
            self.assertEqual(response.headers['Content-Type'],'application/octet-stream')
            self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
            self.assertNotIn('\n',response.headers['Content-Disposition'])
        self.assertEqual(self.api('/api/messages')['messages'][0]['attachments'][0]['id'],identity)
        self.assert_status(409,'/api/messages',dict(text='again',attachments=[identity]))
        self.assert_status(401,'/api/attachments/'+identity,token='wrong')

    def test_attachment_scope_ownership_and_readonly(self):
        identity=self.upload()
        self.assert_status(404,'/api/attachments/'+identity,token='codex-token')
        self.assert_status(404,'/api/messages',dict(text='steal',attachments=[identity]),'codex-token')
        self.assert_status(403,'/api/attachments',{},'muse-read-token')
        self.api('/api/conversations',dict(id='conversation-b',title='B'))
        self.assert_status(409,'/api/messages',dict(text='cross',conversation_id='conversation-b',attachments=[identity]))
        self.assertEqual(self.api('/api/messages')['messages'],[])
        self.api('/api/messages',dict(text='task',target='codex-fixture-01',attachments=[identity]))
        self.assertEqual(self.api('/api/jobs/claim',{},'codex-token')['job']['message']['attachments'][0]['id'],identity)
        self.assertEqual(self.api('/api/messages?conversation_id=conversation-b')['messages'],[])

    def test_attachment_partial_retry_and_atomic_failure(self):
        identity=str(uuid.uuid4());info=dict(id=identity,name='a.bin',size=6)
        self.api('/api/attachments',info)
        self.chunk(identity,0,b'abc')
        self.assertEqual(self.chunk(identity,0,b'abc')['offset'],3)
        self.assertEqual(self.api('/api/attachments',info)['offset'],3)
        self.assert_status(409,'/api/messages',dict(text='partial',attachments=[identity]))
        self.assert_status(409,'/api/attachments/'+identity)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.chunk(identity,0,b'xyz')
        self.assertEqual(caught.exception.code,409)
        self.chunk(identity,3,b'def')
        self.assert_status(404,'/api/messages',dict(text='atomic',attachments=[identity,'missing']))
        self.api('/api/messages',dict(text='ready',attachments=[identity]))
        with self.download(identity) as response:self.assertEqual(response.read(),b'abcdef')
        with self.assertRaises(urllib.error.HTTPError) as caught:self.chunk(identity,0,b'abc')
        self.assertEqual(caught.exception.code,409)

    def test_attachment_limits_remove_and_expiry(self):
        self.assert_status(413,'/api/attachments',dict(id=str(uuid.uuid4()),name='big',size=MAX_FILE+1))
        ids=[self.upload(b'') for _ in range(6)]
        self.assert_status(400,'/api/messages',dict(text='many',attachments=ids))
        self.assert_status(400,'/api/messages',dict(text='duplicate',attachments=ids[:1]*2))
        self.assert_status(400,'/api/messages',dict(text='bad',attachments=[{}]))
        self.api('/api/attachments/'+ids[0]+'/remove',{},'codex-token')
        with self.download(ids[0]) as r:self.assertEqual(r.read(),b'')
        self.api('/api/attachments/'+ids[0]+'/remove',{})
        self.assert_status(404,'/api/attachments/'+ids[0])
        with self.room.db() as db:db.execute('UPDATE attachments SET created=0 WHERE id=?',(ids[1],))
        self.upload(b'new')
        self.assert_status(404,'/api/attachments/'+ids[1])

    def test_attachment_origin_and_headers(self):
        payload=json.dumps(dict(id=str(uuid.uuid4()),name='x',size=0)).encode()
        request=urllib.request.Request(self.url+'/api/attachments',data=payload,headers={'Authorization':'Bearer human-token','Origin':'https://evil.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code,403)
        identity=self.upload(b'<script>alert(1)</script>','x\r\nX-Test: yes/evil.html')
        self.api('/api/messages',dict(text='file',attachments=[identity]))
        with self.download(identity) as response:
            self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
            self.assertIsNone(response.headers.get('X-Test'))


    def test_attachment_storage_and_pending_quotas(self):
        from unittest.mock import patch
        with patch('attachments.MAX_TOTAL', 4):
            self.assert_status(413,'/api/attachments',dict(id=str(uuid.uuid4()),name='quota',size=5))
        with patch('attachments.MAX_PENDING', 4):
            self.assert_status(413,'/api/attachments',dict(id=str(uuid.uuid4()),name='quota',size=5))

    def test_attachment_download_concurrency_is_bounded(self):
        identity=self.upload(b'bounded')
        slots=self.room.attachments.download_slots
        slots.acquire();slots.acquire()
        try:self.assert_status(429,'/api/attachments/'+identity)
        finally:slots.release();slots.release()
        with self.download(identity) as response:self.assertEqual(response.read(),b'bounded')
