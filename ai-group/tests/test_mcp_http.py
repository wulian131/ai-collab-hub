import json
import os
import re
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch
import test_group
from mcp_http import MCPBridge, BridgeError, challenge, digest

class MCPTest(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,AICOLLABHUB_PUBLIC_ORIGIN='https://group.example')
        self.env.start()
        test_group.GroupTest.setUp(self)
        self.bridge=MCPBridge(self.room,'https://group.example')
        self.redirect='https://chatgpt.com/connector_platform_oauth_redirect'
        self.client=self.bridge.register({'redirect_uris':[self.redirect]})['client_id']
        self.verifier='a'*43
    def tearDown(self):
        test_group.GroupTest.tearDown(self)
        self.env.stop()

    def issue(self,read_only=False):
        page,nonce=self.bridge.authorization({'client_id':self.client,'redirect_uri':self.redirect,'response_type':'code','code_challenge_method':'S256','code_challenge':challenge(self.verifier),'resource':self.bridge.resource,'state':'state123'})
        location=self.bridge.consent({'request':nonce,'credential':'human-token','approve':'yes',**({'read_only':'yes'} if read_only else {})},nonce)
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        self.assertEqual(query['state'],['state123'])
        self.assertEqual(query['iss'],[self.bridge.origin])
        data={'grant_type':'authorization_code','client_id':self.client,'redirect_uri':self.redirect,'code':query['code'][0],'code_verifier':self.verifier,'resource':self.bridge.resource}
        return self.bridge.token(data),data

    def request_http(self,path,data=None,token='',headers=None):
        req=urllib.request.Request(self.url+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','Authorization':'Bearer '+token,**(headers or {})})
        with urllib.request.urlopen(req) as response:
            raw=response.read()
            return response.status,response.headers,json.loads(raw) if raw else None

    def test_discovery_and_no_anonymous_or_owner_token_mcp(self):
        for token in ('','human-token','codex-token'):
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request_http('/mcp',{'jsonrpc':'2.0','id':1,'method':'initialize'},token)
            self.assertEqual(caught.exception.code,401)
            self.assertIn('resource_metadata',caught.exception.headers['WWW-Authenticate'])
        _,_,metadata=self.request_http('/.well-known/oauth-protected-resource')
        self.assertEqual(metadata['resource'],'https://group.example/mcp')
        _,_,metadata=self.request_http('/.well-known/oauth-authorization-server')
        self.assertEqual(metadata['code_challenge_methods_supported'],['S256'])

    def test_pkce_binding_replay_and_wrong_resource(self):
        tokens,data=self.issue()
        with self.assertRaises(BridgeError):self.bridge.token(data)
        with self.room.db() as db:
            row=db.execute('SELECT * FROM oauth_tokens WHERE hash=?',(digest(tokens['access_token']),)).fetchone()
            self.assertEqual(row['member'],'owner')
            self.assertEqual(row['resource'],self.bridge.resource)
        self.assertEqual(self.bridge.authenticate(tokens['access_token'])['member'],'owner')
        with self.assertRaises(BridgeError):self.bridge.token(dict(data,resource='https://evil.example/mcp'))
        page,nonce=self.bridge.authorization({'client_id':self.client,'redirect_uri':self.redirect,'response_type':'code','code_challenge_method':'S256','code_challenge':challenge(self.verifier),'resource':self.bridge.resource})
        location=self.bridge.consent({'request':nonce,'credential':'human-token','approve':'yes'},nonce)
        code=urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)['code'][0]
        with self.assertRaises(BridgeError):self.bridge.token(dict(data,code=code,code_verifier='b'*43))
        self.bridge.token(dict(data,code=code))

    def test_redirect_csrf_and_agent_consent_are_rejected(self):
        for uri in ['https://evil.example/connector_platform_oauth_redirect','https://chatgpt.com.evil/connector/oauth/abc','https://chatgpt.com/connector_platform_oauth_redirect?evil=yes']:
            with self.assertRaises(BridgeError):self.bridge.register({'redirect_uris':[uri]})
        args={'client_id':self.client,'redirect_uri':self.redirect,'response_type':'code','code_challenge_method':'S256','code_challenge':challenge(self.verifier),'resource':self.bridge.resource}
        _,nonce=self.bridge.authorization(args)
        with self.assertRaises(BridgeError):self.bridge.consent({'request':nonce,'credential':'human-token','approve':'yes'},'wrongcookie')
        with self.assertRaises(BridgeError):self.bridge.consent({'request':nonce,'credential':'codex-token','approve':'yes'},nonce)
        with self.assertRaises(BridgeError):self.bridge.authorization(dict(args,redirect_uri=self.redirect+'/different'))
        with self.assertRaises(BridgeError):self.bridge.authorization(dict(args,code_challenge_method='plain'))

    def test_refresh_rotation_scope_narrowing_revocation_and_expiry(self):
        tokens,_=self.issue()
        data={'grant_type':'refresh_token','client_id':self.client,'refresh_token':tokens['refresh_token'],'resource':self.bridge.resource,'scope':'ai-group:read'}
        fresh=self.bridge.token(data)
        self.assertEqual(fresh['scope'],'ai-group:read')
        with self.assertRaises(BridgeError):self.bridge.token(data)
        with self.assertRaises(BridgeError):self.bridge.authenticate(tokens['access_token'])
        self.request_http('/oauth/revoke',{'client_id':self.client,'token':fresh['refresh_token']})
        with self.assertRaises(BridgeError):self.bridge.authenticate(fresh['access_token'])
        other,_=self.issue()
        with self.room.db() as db:db.execute('UPDATE oauth_tokens SET expires=0 WHERE hash=?',(digest(other['access_token']),))
        with self.assertRaises(BridgeError):self.bridge.authenticate(other['access_token'])

    def test_read_only_cannot_dispatch_and_token_cannot_call_rest(self):
        tokens,_=self.issue(True)
        auth=self.bridge.authenticate(tokens['access_token'])
        result=self.bridge.rpc(auth,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'dispatch_task','arguments':{'text':'task','targets':['codex-fixture-01'],'request_id':'deny'}}})
        self.assertTrue(result['result']['isError'])
        self.assertEqual(self.room.jobs(),[])
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request_http('/api/jobs/claim',{},tokens['access_token'])
        self.assertEqual(caught.exception.code,401)

    def test_mcp_transport_tools_and_cross_request_status(self):
        tokens,_=self.issue();token=tokens['access_token']
        _,_,result=self.request_http('/mcp',{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},token)
        self.assertEqual(result['result']['protocolVersion'],'2025-06-18')
        status,_,body=self.request_http('/mcp',{'jsonrpc':'2.0','method':'notifications/initialized'},token)
        self.assertEqual((status,body),(202,None))
        _,_,result=self.request_http('/mcp',{'jsonrpc':'2.0','id':2,'method':'tools/list'},token)
        self.assertEqual(len(result['result']['tools']),5)
        self.assertNotIn('group_claim',[t['name'] for t in result['result']['tools']])
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request_http('/mcp',token=token)
        self.assertEqual(caught.exception.code,405)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request_http('/mcp',{'jsonrpc':'2.0','id':3,'method':'ping'},token,{'Origin':'https://evil.example'})
        self.assertEqual(caught.exception.code,403)
        auth=self.bridge.authenticate(token)
        args={'text':'协作任务','targets':['codex-fixture-01','test-cloud-01'],'leader':'codex-fixture-01','execution':{'*':{'reasoning_effort':'low'}},'request_id':'unique'}
        root=self.bridge.call_tool(auth,'dispatch_task',args)
        duplicate=self.bridge.call_tool(auth,'dispatch_task',args)
        self.assertEqual(root['task_id'],duplicate['task_id'])
        self.assertEqual(len(self.room.jobs()),1)
        later=self.bridge.call_tool(auth,'get_task_status',{'task_id':root['task_id']})
        self.assertEqual(later['participants'],['codex-fixture-01','test-cloud-01'])
        self.assertNotIn('lease',later['jobs'][0])
        self.assertEqual(self.bridge.call_tool(auth,'read_messages',{'message_id':root['task_id']})['messages'][0]['text'],'协作任务')
        self.assertEqual(self.bridge.call_tool(auth,'list_tasks',{})['tasks'][0]['task_id'],root['task_id'])
        job=self.room.claim('codex-fixture-01')['job']
        self.room.finish('codex-fixture-01',{'job_id':job['id'],'lease':job['lease'],'text':'完成','completed':True})
        self.assertEqual(self.bridge.call_tool(auth,'get_task_status',{'task_id':root['task_id']})['status'],'done')

    def test_invalid_arguments_cannot_create_jobs(self):
        tokens,_=self.issue();auth=self.bridge.authenticate(tokens['access_token'])
        for args in [{'text':'x','targets':[],'request_id':'x'},{'text':'x','targets':['unknown'],'request_id':'x'},{'text':'x','targets':['codex-fixture-01'],'request_id':'x','author':'owner'}]:
            with self.assertRaises(ValueError):self.bridge.call_tool(auth,'dispatch_task',args)
        self.assertEqual(self.room.jobs(),[])
        result=self.bridge.rpc(auth,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'list_tasks','arguments':{'limit':True}}})
        self.assertTrue(result['result']['isError'])

    def test_http_oauth_flow_and_bounded_results(self):
        _,_,registered=self.request_http('/oauth/register',{'redirect_uris':[self.redirect],'token_endpoint_auth_method':'none'})
        client_id=registered['client_id']
        query=urllib.parse.urlencode({'client_id':client_id,'redirect_uri':self.redirect,'response_type':'code','code_challenge_method':'S256','code_challenge':challenge(self.verifier),'resource':self.bridge.resource,'state':'http-state'})
        with urllib.request.urlopen(self.url+'/oauth/authorize?'+query) as response:
            page=response.read().decode();cookie=response.headers['Set-Cookie'].split(';')[0]
        nonce=re.search(r'name="request" value="([^\"]+)"',page).group(1)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args):return None
        data=urllib.parse.urlencode({'request':nonce,'credential':'human-token','approve':'yes'}).encode()
        req=urllib.request.Request(self.url+'/oauth/authorize',data=data,headers={'Content-Type':'application/x-www-form-urlencoded','Cookie':cookie})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.build_opener(NoRedirect()).open(req)
        self.assertEqual(caught.exception.code,303)
        code=urllib.parse.parse_qs(urllib.parse.urlsplit(caught.exception.headers['Location']).query)['code'][0]
        data=urllib.parse.urlencode({'client_id':client_id,'grant_type':'authorization_code','code':code,'redirect_uri':self.redirect,'code_verifier':self.verifier,'resource':self.bridge.resource}).encode()
        req=urllib.request.Request(self.url+'/oauth/token',data=data,headers={'Content-Type':'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req) as response:tokens=json.load(response)
        auth=self.bridge.authenticate(tokens['access_token'])
        message=self.room.message('owner',{'text':'long '+('x'*5000),'broadcast':True})['message']
        short=self.bridge.call_tool(auth,'read_messages',{})['messages'][-1]
        self.assertTrue(short['truncated']);self.assertEqual(len(short['text']),4000)
        full=self.bridge.call_tool(auth,'read_messages',{'message_id':message['id']})['messages'][0]
        self.assertEqual(len(full['text']),5005)

    def test_queue_bound_and_disabled_member(self):
        tokens,_=self.issue();auth=self.bridge.authenticate(tokens['access_token'])
        self.room.members['test-cloud-01']['enabled']=False
        with self.assertRaises(ValueError):self.bridge.call_tool(auth,'dispatch_task',{'text':'task','targets':['test-cloud-01'],'request_id':'disabled'})
        for i in range(100):self.room.message('owner',{'text':'pending','target':'codex-fixture-01'})
        with self.assertRaises(ValueError):self.bridge.call_tool(auth,'dispatch_task',{'text':'task','targets':['codex-fixture-01'],'request_id':'full'})
        self.assertEqual(len(self.room.jobs()),100)
