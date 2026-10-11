#!/usr/bin/env python3
"""Export the chat product for review. Never publish or include private extensions."""
from pathlib import Path
import argparse
import re
import zipfile
FILES=('LICENSE','server.py','devices.py','onboarding.py','manage.py','client.py','worker.py','notifications.py','mcp_http.py',
       'resource_governor.py','pair_device.py','attachments.py','attachments.js','attachments.css',
       'index.html','devices.js','appearance.css','appearance.js','.gitignore','AGENTS.md','README.md','MUSE.md',
       'owner.example.json','config.example.json','.env.example','export_source.py','Dockerfile','compose.yaml','.dockerignore',
       'docs/chatgpt-mcp.md','docs/orchestration.md','docs/costs.md','docs/installation.md','docs/add-device.md','docs/security.md','docs/architecture.md','docs/release.md',
       'deploy/aicollabhub.service','deploy/Caddyfile')
TESTS=('conversation_members','cloud_credential','member_controls','agent_retirement','attachments','collaboration','conversations','device_worker','devices','execution','group','identity',
       'mcp_http','message_source','notifications','onboarding_setup','parallel','parallel_integration','receipts',
       'recovery','resource_governor','unlimited','worker','worker_context','portable','safe_defaults')

def public_text(text):
    text=re.sub(r'^[ \t]*# BEGIN PRIVATE EXTENSION\n.*?^[ \t]*# END PRIVATE EXTENSION\n','',text,flags=re.M|re.S)
    text=re.sub(r'/\* BEGIN PRIVATE EXTENSION \*/.*?/\* END PRIVATE EXTENSION \*/','',text,flags=re.S)
    text=re.sub(r'<!-- BEGIN PRIVATE EXTENSION -->.*?<!-- END PRIVATE EXTENSION -->','',text,flags=re.S)
    if 'PRIVATE EXTENSION' in text:raise ValueError('Unclosed private boundary')
    return text

def export(destination):
    root=Path(__file__).resolve().parent
    names=list(FILES)+['tests/test_'+n+'.py' for n in TESTS]
    files={}
    for name in names:
        p=root/name
        if not p.is_file() or p.is_symlink():raise ValueError('Missing/unsafe allowlisted file: '+name)
        data=p.read_text(encoding='utf-8')
        if name in ('server.py', 'index.html', 'client.py'):data=public_text(data)
        if name != 'export_source.py' and re.search(r'^\s*(?:#|/\*|<!--)\s*BEGIN PRIVATE EXTENSION', data, re.M):
            raise ValueError('Unexpected extension in allowlisted source: '+name)
        if name.endswith('.py'):compile(data,name,'exec')
        files[name]=data
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():z.writestr('ai-group/'+name,data)
    return len(files)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);args=p.parse_args();print('Review archive:',export(args.destination),'files')
