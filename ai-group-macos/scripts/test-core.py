#!/usr/bin/env python3
"""Runs the real packaged XCTest suite with an isolated synthetic loopback fixture.
The explicit runner checks executed test count; it cannot turn an Xcode loader error into a pass.
"""
from pathlib import Path
import subprocess,os,tempfile,socket
root=Path(__file__).resolve().parents[1]
xcode=Path(subprocess.check_output(['xcode-select','-p'],text=True).strip())
platform=xcode/'Platforms/MacOSX.platform/Developer'
frameworks=platform/'Library/Frameworks';libraries=platform/'usr/lib'
with tempfile.TemporaryDirectory(prefix='mac-core-tests-') as scratch:
 scratch=Path(scratch)
 subprocess.run(['swift','build','--build-tests','--package-path',str(root),'--scratch-path',str(scratch/'build')],check=True)
 runner=scratch/'runner'
 subprocess.run(['xcrun','clang',str(root/'scripts/test-runner.m'),'-F',str(frameworks),'-framework','XCTest','-framework','Foundation','-Wl,-rpath,'+str(frameworks),'-Wl,-rpath,'+str(libraries),'-o',str(runner)],check=True)
 bundles=list((scratch/'build').glob('**/AIGroupCoreTests.xctest/Contents/MacOS/AIGroupCoreTests'))
 if not bundles:bundles=list((scratch/'build').glob('**/AIGroupCorePackageTests.xctest/Contents/MacOS/AIGroupCorePackageTests'))
 if not bundles:raise RuntimeError('No compiled XCTest bundle found')
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 env=dict(os.environ,AI_FIXTURE_PORT=str(port),DYLD_FRAMEWORK_PATH=str(frameworks),DYLD_LIBRARY_PATH=str(libraries));env['AI_GROUP_TEST_URL']="http://127.0.0.1:"+str(port)
 fixture=subprocess.Popen(['python3',str(root/'scripts/synthetic-fixture.py')],env=env,stdout=subprocess.PIPE,text=True)
 try:
  if not fixture.stdout.readline():raise RuntimeError('Fixture failed to start')
  subprocess.run([str(runner),str(bundles[0])],env=env,check=True)
 finally:fixture.terminate();fixture.wait(timeout=5)
