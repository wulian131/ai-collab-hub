import json,pathlib,sys,tempfile,unittest
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from worker import execute
class WorkerPermissionsTest(unittest.TestCase):
 def test_read_execute_member_cannot_silently_lose_result_on_delegation(self):
  with tempfile.TemporaryDirectory() as td:
   fake=pathlib.Path(td)/'fake-cli'
   fake.write_text('#!'+sys.executable+'\nimport sys,pathlib,json\np=sys.stdin.read()\nassert \'"delegate": false\' in p\npathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps({"text":"work result","delegates":[{"target":"other","text":"help"}],"completed":True,"blocked":False}))\n');fake.chmod(0o700)
   args=SimpleNamespace(codex=str(fake),sandbox='workspace-write',workspace=td,timeout=0,context=[],members=[dict(id='device',name='Device',role='agent',permissions=dict(read=True,execute=True,delegate=False)),dict(id='other',role='agent')])
   text,failed,delegates=execute({'message':dict(id=1,author='owner',target='device',text='task')},args)
   self.assertFalse(failed);self.assertEqual(delegates,[]);self.assertTrue(args.blocked);self.assertFalse(args.completed);self.assertIn('未执行',text)
