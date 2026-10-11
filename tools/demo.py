#!/usr/bin/env python3
"""Disposable local demonstration. All names, messages and credentials are synthetic.

Never point this at another server. Binds loopback and creates fresh temporary data;
no model or CLI executes, no external account or background service is used.
"""
from pathlib import Path
import argparse,hashlib,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ai-group'))
from server import Room,make_server

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18918)
    args=parser.parse_args()
    roster=[('owner','Demo administrator','human','demo-local-only'),('demo-coordinator','Coordinator','agent','demo-coordinator-only'),('demo-worker-a','Worker A','agent','demo-worker-a-only'),('demo-worker-b','Worker B','agent','demo-worker-b-only')]
    members=[dict(id=i,name=n,role=r,enabled=True,token_hash=hashlib.sha256(t.encode()).hexdigest(),permissions=dict(read=True,execute=r=='agent',delegate=i=='demo-coordinator')) for i,n,r,t in roster]
    with tempfile.TemporaryDirectory(prefix='ai-collab-demo-') as tmp:
        room=Room(Path(tmp)/'demo.db',dict(members=members))
        room.save_conversation('owner',dict(id='demo-room',title='产品设计讨论',participants=['demo-coordinator','demo-worker-a','demo-worker-b']))
        room.message('owner',dict(text='【合成演示】请整理任务面板设计方案。Coordinator 汇总，Worker A 和 Worker B 分别提供界面与测试建议。',source='user',targets=['demo-coordinator','demo-worker-a','demo-worker-b'],leader='demo-coordinator',conversation_id='demo-room',key='demo-request'))
        first=room.claim('demo-coordinator')['job']
        assert first is not None
        room.progress('demo-coordinator',dict(job_id=first['id'],lease=first['lease'],state='processing'))
        room.finish('demo-coordinator',dict(job_id=first['id'],lease=first['lease'],text='我会分配两项合成任务，并在回执齐全后汇总。',completed=False,delegates=[dict(target='demo-worker-a',text='给出任务面板的界面建议。'),dict(target='demo-worker-b',text='给出任务面板的测试建议。')]))
        for identity,text in [('demo-worker-a','界面建议：优先展示当前任务、负责人和进度，详细记录按需展开。'),('demo-worker-b','测试建议：检查键盘操作、手机布局、断网草稿以及同 key 重试。'),('demo-coordinator','已汇总建议。下一步可在隔离环境制作草图，再由用户审查。')]:
            job=room.claim(identity)['job']
            if job:
                room.progress(identity,dict(job_id=job['id'],lease=job['lease'],state='processing'))
                room.finish(identity,dict(job_id=job['id'],lease=job['lease'],text=text,completed=True,delegates=[]))
        http=make_server(room,'127.0.0.1',args.port)
        print('Synthetic local demo: http://127.0.0.1:'+str(http.server_port)+'/?conversation=demo-room',flush=True)
        print('Demonstration credential (public fixture, not a real secret): demo-local-only',flush=True)
        try:http.serve_forever()
        except KeyboardInterrupt:pass
        finally:http.server_close();room.close()

if __name__=='__main__':main()
