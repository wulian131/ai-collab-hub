#!/usr/bin/env python3
"""Runs macOS state and AppKit checks using only isolated synthetic data."""
from pathlib import Path
import subprocess,os,tempfile,socket,time
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='mac-native-tests-') as temporary:
 scratch=Path(temporary)
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 source=scratch/'Smoke.swift';source.write_text((root/'DesktopTests/Smoke.swift').read_text().replace('18917',str(port)))
 files=list((root/'AIGroup/Core').glob('*.swift'))+[root/'AIGroup/Native'/f for f in ['RoomStore.swift', 'SecureStorage.swift', 'DevicesModel.swift', 'MessageEditor.swift', 'DesktopSupport.swift']]
 subprocess.run(['xcrun','swiftc','-swift-version','5','-D','DEBUG','-parse-as-library',*[str(f) for f in files],str(source),'-o',str(scratch/'native-tests')],check=True)
 env=dict(os.environ,AI_FIXTURE_PORT=str(port),MAC_SYNTHETIC_TEST='1',MAC_FIXTURE_DIRECTORY=str(scratch/'state'))
 (scratch/'state').mkdir()
 fixture=subprocess.Popen(['python3',str(root/'scripts/synthetic-fixture.py')],env=env,stdout=subprocess.PIPE,text=True)
 try:
  if not fixture.stdout.readline():raise RuntimeError('Fixture failed to start')
  time.sleep(0.1)
  subprocess.run([str(scratch/'native-tests')],env=env,check=True)
 finally:fixture.terminate();fixture.wait(timeout=5)
