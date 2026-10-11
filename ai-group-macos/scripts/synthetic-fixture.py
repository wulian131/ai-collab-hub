#!/usr/bin/env python3
"""Standalone synthetic contract fixture, in-memory only. Never accesses production."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
import json,time,uuid,base64,hashlib,struct,io,zipfile,threading,os
lock=threading.RLock()
rooms={'main':dict(id='main',title='桌面协作验收',created=time.time(),updated=time.time(),participants=['codex-fixture-01','test-cloud-01'])}
messages=[];jobs=[];devices={};invitations={};requests={};uploads={};sockets=[];counter={'sends':0,'invites':0}
base_members=[dict(id='owner',name='测试用户',role='human'),dict(id='codex-fixture-01',name='测试 Codex',role='agent',enabled=True,online=True),dict(id='test-cloud-01',name='测试 Muse',role='agent',enabled=True,online=False)]
for id,name,kind in [('test-cloud-01','测试 Muse','muse'),('codex-fixture-01','测试 Codex','codex-macos')]:
 devices[id]=dict(id=id,name=name,type=kind,machine='synthetic-only',tags=[],permissions=dict(read=True,execute=True,delegate=False),state='active',status='connected',legacy=True,generation=1)
# A visible latest message exercises scrolling, code and references with synthetic data.
for i in range(1,31):messages.append(dict(id=i,author='codex-fixture-01' if i%2 else 'owner',text='合成验收消息 '+str(i)+ ('\n```swift\nlet fixture = true\n```' if i==30 else ''),created=time.time(),conversation_id='main',reply_to=1 if i==30 else None,targets=[],attachments=[]))
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def reply(self,v,status=200,raw=False):
  data=v if raw else json.dumps(v,ensure_ascii=False).encode()
  self.send_response(status);self.send_header('Content-Type','application/octet-stream' if raw else 'application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def auth(self):
  token=self.headers.get('Authorization','').removeprefix('Bearer ')
  if token not in ['ios-owner-fixture','ios-owner-replacement','ios-read-fixture','ios-codex-fixture','ios-muse-fixture']:
   self.reply({'error':'凭证无效（合成401）'},401);return None
  return token
 def websocket(self):
  key=self.headers.get('Sec-WebSocket-Key');accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  def send():
   body=b'{"type":"state","state":"synthetic"}';self.wfile.write(bytes([129,len(body)])+body);self.wfile.flush()
  try:
   a=self.rfile.read(2);length=a[1]&127
   if length==126:length=struct.unpack('>H',self.rfile.read(2))[0]
   mask=self.rfile.read(4);data=self.rfile.read(length);decoded=bytes(x^mask[i%4] for i,x in enumerate(data))
   if json.loads(decoded).get('token') not in ['ios-owner-fixture','ios-owner-replacement']:return
   sockets.append(send);send()
   while self.rfile.read(1):pass
  except (OSError,ValueError,IndexError):pass
  finally:
   if send in sockets:sockets.remove(send)
 def do_GET(self):
  u=urlparse(self.path);p=u.path;query=parse_qs(u.query)
  if p.endswith('/notifications'):self.websocket();return
  token=self.auth()
  if not token:return
  q=lambda name,default='':query.get(name,[default])[0]
  with lock:
   if p.endswith('/members'):
    members=base_members+[dict(id=d['id'],name=d['name'],role='agent',state=d['state'],enabled=d['state'] not in ['disabled','deleted','archived'],permissions=d['permissions']) for d in devices.values() if d["id"] not in [m["id"] for m in base_members]]
    self.reply(dict(me='codex-fixture-01' if token=='ios-codex-fixture' else 'owner',readonly=token=='ios-read-fixture',members=members))
   elif p.endswith('/conversations'):
    rows=[r for r in rooms.values() if q('q').lower() in (r['title']+' '+r.get('summary','')).lower()];offset=int(q('offset','0'));self.reply(dict(conversations=rows[offset:offset+20],has_more=len(rows)>offset+20))
   elif p.endswith('/messages'):
    id=q('conversation_id','main');after=int(q('after','0'));before=int(q('before','0'));rows=[m for m in messages if m['conversation_id']==id and (not after or m['id']>after) and (not before or m['id']<before)]
    self.reply(dict(conversation=rooms.get(id),conversation_id=id,messages=rows[-100:],has_more=len(rows)>100))
   elif p.endswith('/jobs'):
    rows=[j for j in jobs if j['conversation_id']==q('conversation_id','main')];self.reply(dict(jobs=rows,has_more=False))
   elif p.endswith('/devices'):
    if token!='ios-owner-fixture' and token!='ios-owner-replacement':self.reply({'error':'需要管理员'},403)
    else:self.reply(dict(devices=[d for d in devices.values() if d['state']!='deleted']))
   elif '/attachments/' in p:
    id=p.rsplit('/',1)[1];self.reply(uploads[id]['data'],raw=True)
   elif p.endswith('/test/counters'):self.reply(counter)
   else:self.reply({},404)
 def do_POST(self):
  token=self.auth()
  if not token:return
  p=urlparse(self.path).path;query=parse_qs(urlparse(self.path).query);data=self.rfile.read(int(self.headers.get('Content-Length','0')))
  if '/attachments/' in p and not p.endswith('/remove'):
   id=p.rsplit('/',1)[1];uploads[id]['data']+=data;self.reply(dict(attachment=uploads[id]['attachment'],offset=len(uploads[id]['data'])));return
  b=json.loads(data or b'{}')
  if token not in ['ios-owner-fixture','ios-owner-replacement']:self.reply({'error':'需要用户写权限'},403);return
  with lock:
   if p.endswith('/conversations'):
    id=b.get('id',str(uuid.uuid4()));r=rooms.get(id,dict(id=id,created=time.time(),updated=time.time()))
    r.update(title=b['title'],updated=time.time());
    if not b.get('rename') and 'participants' in b:r['participants']=sorted(set(b['participants']))
    rooms[id]=r;self.reply(dict(conversation=r))
   elif p.endswith('/messages'):
    key=b['key'];existing=next((m for m in messages if m.get('key')==key),None)
    if existing is None:
     counter['sends']+=1;id=max((m['id'] for m in messages),default=0)+1
     existing=dict(id=id,author='owner',text=b['text'],created=time.time(),conversation_id=b['conversation_id'],reply_to=b.get('reply_to'),targets=b.get('targets',[]),attachments=[uploads[a]['attachment'] for a in b.get('attachments',[])],key=key)
     messages.append(existing);rooms[b['conversation_id']]['summary']=b['text']
     if b.get('dispatch'):
      for target in ([b['leader']] if b.get('leader') else b['targets']):jobs.append(dict(id=len(jobs)+1,message_id=id,target=target,status='pending',attempts=0,conversation_id=b['conversation_id'],events=[]))
     for send in sockets[:]:
      try:send()
      except OSError:pass
     if b['text']=='simulate-lost-response':self.reply({'error':'合成：保存成功但响应丢失'},503);return
    receipt=dict(text='收到',task_id=existing['id'],conversation_id=b['conversation_id'],jobs=[dict(id=j['id'],target=j['target']) for j in jobs if j['message_id']==existing['id']])
    self.reply(dict(message=existing,receipt=receipt))
   elif p.endswith('/attachments'):
    a=dict(id=b['id'],name=b['name'],size=b['size'],conversation_id=b['conversation_id']);uploads[b['id']]=dict(attachment=a,data=b'');self.reply(dict(attachment=a,offset=0))
   elif p.endswith('/devices/invite'):
    key=b['key']
    if key in requests:
     r=dict(requests[key]);r.update(secret_available=False);r.pop('pairing_code',None);self.reply(r);return
    counter['invites']+=1
    id=b.get('device_id') or 'synthetic-'+str(uuid.uuid4())
    if id not in devices:devices[id]=dict(id=id,name=b['name'],type=b['type'],machine=b.get('machine',''),tags=[],permissions=b['permissions'],state='active',status='invited',generation=1)
    inv=dict(device_id=id,invitation_id=str(uuid.uuid4()),pairing_code='synthetic-only-'+str(uuid.uuid4()),expires=time.time()+600,secret_available=True)
    invitations[id]=inv;requests[key]=inv;self.reply(inv)
   elif p.endswith('/devices/pair'):self.reply({'error':'old synthetic code'},401)
   elif p.endswith('/devices/action'):
    d=devices[b['device_id']];action=b['action'];d['generation']+=1
    if action=='permissions':d['permissions']=b['permissions']
    else:d['state']={'enable':'active','revoke':'revoked','archive':'archived','delete':'deleted'}[action];d['status']=d['state']
    self.reply(dict(ok=True))
   elif p.endswith('/devices/cloud-credential'):
    d=devices[b['device_id']];d['generation']+=1;self.reply(dict(member_id=d['id'],token='synthetic-cloud-only',permissions=d['permissions'],secret_available=True,display_until=time.time()+300))
   elif p.endswith('/devices/bundle'):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('README.txt','synthetic, no credentials')
    self.reply(out.getvalue(),raw=True)
   elif p.endswith('/remove') or p.endswith('/jobs/retry'):self.reply(dict(ok=True))
   else:self.reply({},404)
print('synthetic chat fixture ready on loopback 18917',flush=True)
ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('AI_FIXTURE_PORT','18917'))),Handler).serve_forever()
