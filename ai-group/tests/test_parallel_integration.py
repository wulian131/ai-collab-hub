"""Actual local HTTP/WebSocket + worker + fake CLI; no production API or models."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Room, make_server
from test_parallel import until

ROOT = Path(__file__).resolve().parents[1]


class ParallelIntegrationTest(unittest.TestCase):
    def test_notification_keeps_working_during_model_and_dependencies_remain_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            room=Room(root/'room.db',{'members':[
                {'id':'owner','role':'human','name':'owner','token_hash':hashlib.sha256(b'fake-owner').hexdigest()},
                {'id':'codex-fixture-01','role':'agent','name':'test-jp','token_hash':hashlib.sha256(b'fake-agent').hexdigest()}]})
            server=make_server(room,'127.0.0.1',0)
            server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
            cli=root/'fake-cli'
            cli.write_text('#!'+sys.executable+'\n'+'''
import json,os,pathlib,sys,time
assert not any(k.startswith(('GROUP_', 'AICOLLABHUB_')) for k in os.environ)
prompt=sys.stdin.read();data=json.loads(prompt.split('\\n')[1]);name=data['text']
root=pathlib.Path('''+repr(tmp)+''')
(root/('started-'+name)).write_text(json.dumps(data))
if name in ['A','B']:
    deadline=time.monotonic()+12
    while not (root/('release-'+name)).exists():
        if time.monotonic()>deadline:sys.exit(9)
        time.sleep(.01)
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps({'text':'done-'+name,'completed':True,'blocked':False,'delegates':[]}))
''');cli.chmod(0o700)
            env={k:v for k,v in os.environ.items() if not k.startswith(('GROUP_', 'AICOLLABHUB_'))}
            env.update(AICOLLABHUB_URL='http://127.0.0.1:'+str(server.server_port),AICOLLABHUB_TOKEN='fake-agent')
            process=subprocess.Popen([sys.executable,str(ROOT/'worker.py'),'--enable-execution','--codex',str(cli),
                '--workspace',tmp,'--state-dir',str(root/'state'),'--concurrency','2','--interval','1'],
                env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                until(lambda:bool(room._listeners))
                def send(name):return room.message('owner',{'text':name,'target':'codex-fixture-01'})['message']['id']
                first=send('A');until(lambda:(root/'started-A').exists())
                # Insert dependency atomically with creation; no window to claim D.
                with room.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    dependent=room._insert(db,'owner',{'text':'D','target':'codex-fixture-01'})
                    jid=db.execute('SELECT id FROM jobs WHERE message_id=?',(dependent['id'],)).fetchone()[0]
                    db.execute('INSERT INTO job_dependencies VALUES(?,?)',(jid,first))
                send('B');until(lambda:(root/'started-B').exists())
                self.assertFalse((root/'started-D').exists())
                running=[j for j in room.jobs() if j['status']=='running']
                self.assertEqual(len(running),2)
                for job in running:
                    with room.db() as db:
                        self.assertEqual(db.execute("SELECT count(*) FROM job_events WHERE job_id=? AND state='processing'",(job['id'],)).fetchone()[0],1)
                send('C');time.sleep(.15)
                self.assertFalse((root/'started-C').exists())
                (root/'release-B').touch();until(lambda:(root/'started-C').exists())
                self.assertFalse((root/'started-D').exists())
                self.assertFalse((root/'release-A').exists())
                (root/'release-A').touch()
                until(lambda:len(room.jobs())==4 and all(j['status']=='done' for j in room.jobs()))
                self.assertTrue((root/'started-D').exists())
                self.assertTrue(all(j['attempts']==1 for j in room.jobs()))
                for name in ['A','B','C','D']:
                    data=json.loads((root/('started-'+name)).read_text())
                    self.assertEqual(data['self']['id'],'codex-fixture-01')
                until(lambda:not list((root/'state').glob('outbox*.json')))
            finally:
                (root/'release-A').touch();(root/'release-B').touch()
                process.terminate();process.wait(timeout=5)
                server.shutdown();server.server_close();server_thread.join();room.close()

    def test_invalid_concurrency_rejected_before_credentials(self):
        for count in ['0','9','-1']:
            result=subprocess.run([sys.executable,str(ROOT/'worker.py'),'--enable-execution','--workspace','/tmp','--concurrency',count],
                env={k:v for k,v in os.environ.items() if not k.startswith(('GROUP_', 'AICOLLABHUB_'))},capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn('concurrency',result.stderr)

if __name__=='__main__':unittest.main()
