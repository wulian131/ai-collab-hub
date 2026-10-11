#!/usr/bin/env python3
"""Temporary isolated real chat server. No production records or credentials used."""
from pathlib import Path
import sys,tempfile,hashlib,json,signal,time,os
source=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[2]/'ai-group'
sys.path.insert(0,str(source))
from server import Room,make_server,APIError
with tempfile.TemporaryDirectory() as directory:
 members=[{'id':id,'name':id,'role':role,'token_hash':hashlib.sha256(token.encode()).hexdigest()} for id,role,token in [('owner','human','ios-owner-fixture'),('codex-fixture-01','agent','ios-codex-fixture'),('test-cloud-01','agent','ios-muse-fixture')]]
 members[0]['read_token_hash']=hashlib.sha256(b'ios-read-fixture').hexdigest()
 room=Room(Path(directory)/'fixture.sqlite3',{'members':members})
 network_flag=os.environ.get('AI_GROUP_TEST_NETWORK_FLAG')
 original_members=room.member_list
 def member_list():
  if network_flag and Path(network_flag).exists():raise APIError(503,'测试：网络不可用')
  return original_members()
 room.member_list=member_list
 original=room.message; lost=set()
 def message(author,payload):
  result=original(author,payload)
  if payload.get('text')=='simulate-lost-response' and payload.get('key') not in lost:
   lost.add(payload.get('key'));raise APIError(503,'测试：保存成功但响应丢失')
  return result
 room.message=message
 original_invite=room.devices.invite; lost_invites=set()
 def invite(author,payload):
  if payload.get('name')=='simulate-background-invite':time.sleep(0.2)
  result=original_invite(author,payload)
  if payload.get('name')=='simulate-lost-invite-response' and payload.get('key') not in lost_invites:
   lost_invites.add(payload.get('key'));raise APIError(503,'测试：邀请保存成功但响应丢失')
  return result
 room.devices.invite=invite
 http=make_server(room,'127.0.0.1',int(os.environ.get('AI_FIXTURE_PORT','18917')))
 print('AI_GROUP_TEST_URL=http://127.0.0.1:'+str(http.server_port),flush=True)
 try:http.serve_forever()
 except KeyboardInterrupt:pass
 finally:http.server_close();room.close()
