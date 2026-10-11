#!/usr/bin/env python3
"""Inspect this file first. Runs only explicitly selected onboarding actions."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import warnings
from urllib.parse import urlsplit

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('接入请求不允许跳转，请核对服务地址')

def request(url, data=None, token=None, raw=False):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as response:
            body=response.read(2_000_000)
            return body if raw else json.loads(body)
    except urllib.error.HTTPError as e:
        # No response body, request or credentials are printed.
        raise RuntimeError('服务器拒绝请求（HTTP %s）；检查邀请、权限与版本。' % e.code) from None
    except urllib.error.URLError:
        raise RuntimeError('无法连接服务，请核对地址、网络与证书；未修改其他服务') from None

def config(path):
    return validate_config(json.loads(path.read_text()))

def validate_config(c):
    if c.get('protocol')!=1:raise ValueError('接入配置版本不兼容，请重新下载')
    p=urlsplit(c.get('url',''))
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('配置必须使用 HTTPS 地址，且不能含凭证、查询或片段')
    if not re.fullmatch(r'(codex|muse)-device-[a-f0-9]{16}',c.get('device_id','')):
        raise ValueError('设备ID无效')
    if c.get('type') not in ('codex-linux','codex-macos','codex-windows','muse'):
        raise ValueError('设备类型不支持')
    c['url']=c['url'].rstrip('/')
    return c

def private_write(path, data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)

def check(c):
    if sys.version_info < (3,10):raise ValueError('需要 Python 3.10 或更新版本')
    kind=c['type']
    if kind=='codex-macos' and sys.platform!='darwin':raise ValueError('此配置用于 macOS')
    if kind=='codex-linux' and sys.platform!='linux':raise ValueError('此配置用于 Linux')
    if kind=='codex-windows' and (sys.platform!='linux' or 'microsoft' not in platform.release().lower()):
        raise ValueError('Windows worker 必须在 WSL2 内运行；不支持原生 Windows 常驻执行')
    if kind.startswith('codex') and c.get('permissions',{}).get('execute') and not shutil.which('codex'):
        raise ValueError('未找到 Codex CLI；请由本机用户安装并登录后重试')
    print('依赖检查通过；不会自动安装软件或修改系统服务。')

def credential(root,c):
    file=root/'credentials.json'
    if not file.is_file() or file.is_symlink():raise ValueError('未找到安全凭证文件，请先配对')
    if os.name=='posix' and file.stat().st_mode & 0o077:raise ValueError('凭证文件权限过宽，请设置为仅当前用户可读写')
    cr=json.loads(file.read_text())
    if cr.get('url')!=c['url'] or cr.get('member_id')!=c['device_id']:raise ValueError('凭证与设备配置不匹配')
    return cr

def pair(root,c):
    if os.name!='posix':raise ValueError('当前配对客户端要求 POSIX 文件权限。Windows 请在 WSL2 中执行；Muse 云端请使用平台安全凭证存储。')
    if (root/'credentials.json').exists():raise ValueError('已有设备凭证，拒绝覆盖。轮换时请在新的独立目录配对。')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.is_symlink():raise ValueError('接入目录不能为符号链接')
    os.chmod(root,0o700)
    if not c.get('invitation_id'):raise ValueError('缺少有效邀请，请管理员重发并下载新配置')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try:code=getpass.getpass('一次性配对码（隐藏输入，不要交给模型）：')
        except getpass.GetPassWarning:raise ValueError('需要支持隐藏输入的交互终端，未读取配对码。') from None
    result=request(c['url']+'/api/devices/pair',{'device_id':c['device_id'],'invitation_id':c['invitation_id'],
        'type':c['type'],'protocol':1,'pairing_code':code})
    if result.get('protocol')!=1 or result.get('member_id')!=c['device_id']:raise ValueError('服务器身份响应不匹配')
    private_write(root/'credentials.json',json.dumps({'url':c['url'],'token':result['token'],'member_id':result['member_id']}).encode())
    print('配对成功。机器凭证已保存至独立目录受限文件，未输出。')

def status(root,c):
    cr=credential(root,c)
    r=request(c['url']+'/api/members',token=cr['token'])
    if r.get('me')!=c['device_id']:raise ValueError('认证身份不匹配')
    request(c['url']+'/api/presence',{'detail':'onboarding · 只读连接测试通过'},cr['token'])
    print('连接与身份测试通过；未领取任务。')

def prepare(root,c):
    check(c);cr=credential(root,c)
    info=request(c['url']+'/api/members',token=cr['token'])
    member=next((m for m in info['members'] if m['id']==c['device_id']),None)
    if not member:raise ValueError('未找到设备身份')
    execute=member.get('permissions',{}).get('execute',False)
    manifest=request(c['url']+'/downloads/manifest.json')
    if manifest.get('protocol')!=1:raise ValueError('客户端版本不兼容')
    names=['client.py','notifications.py']+(['worker.py'] if c['type'].startswith('codex') and execute else [])
    bodies={}
    for name in names:
        data=request(c['url']+'/downloads/'+name,raw=True)
        if hashlib.sha256(data).hexdigest()!=manifest['files'].get(name):raise ValueError('客户端校验不匹配，请重试下载')
        compile(data,name,'exec');bodies[name]=data
    for name,data in bodies.items():
        target=root/name
        if target.exists():
            if target.read_bytes()!=data:raise ValueError('已有不同版本客户端，拒绝覆盖，请使用新的接入目录')
        else:private_write(target,data)
    connector={'command':sys.executable,'args':[str(root/'client.py'),'mcp'],'env':{'AICOLLABHUB_CREDENTIAL_FILE':str(root/'credentials.json')}}
    if not (root/'connector.example.json').exists():private_write(root/'connector.example.json',json.dumps(connector,ensure_ascii=False,indent=2).encode())
    print('客户端下载与校验通过；已生成不含密钥的 connector.example.json。')
    if 'worker.py' not in names:
        print('当前只读或 Muse 接入：可使用 python3 client.py mcp 启动 stdio 连接，AICOLLABHUB_CREDENTIAL_FILE 指向本目录 credentials.json。未创建执行服务。')
        return
    workspace=root/'workspace';workspace.mkdir(mode=0o700,exist_ok=True)
    (root/'state').mkdir(mode=0o700,exist_ok=True)
    args=[sys.executable,str(root/'worker.py'),'--workspace',str(workspace),'--state-dir',str(root/'state'),
          '--enable-execution','--sandbox','read-only','--timeout','900','--concurrency','1','--codex',shutil.which('codex')]
    service='aicollabhub-'+c['device_id']
    env={'AICOLLABHUB_CREDENTIAL_FILE':str(root/'credentials.json'),'PATH':os.environ.get('PATH','/usr/bin:/bin')}
    if sys.platform=='darwin':
        label='local.'+service
        filename=label+'.plist'
        data=plistlib.dumps({'Label':label,'ProgramArguments':args,'EnvironmentVariables':env,'WorkingDirectory':str(root),
             'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':15,'StandardOutPath':str(root/'worker.log'),'StandardErrorPath':str(root/'worker-error.log')})
        steps=f'检查 {filename} 后，将它复制到 ~/Library/LaunchAgents/（已存在则不要覆盖），然后执行：\nlaunchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{filename}\n停止：launchctl bootout gui/$(id -u)/{label}\n'
    else:
        def quote(s):return '"'+str(s).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
        filename=service+'.service'
        data=('[Unit]\nDescription=AI Group isolated device\nAfter=network-online.target\n[Service]\nType=simple\nWorkingDirectory='+quote(root)+'\nEnvironment='+quote('AICOLLABHUB_CREDENTIAL_FILE='+str(root/'credentials.json'))+'\nEnvironment='+quote('PATH='+env['PATH'])+'\nExecStart='+' '.join(quote(x) for x in args)+'\nRestart=on-failure\nRestartSec=10\nUMask=0077\nTasksMax=512\n[Install]\nWantedBy=default.target\n').encode()
        steps=f'检查 {filename} 后，将它复制到 ~/.config/systemd/user/（已存在则不要覆盖），然后执行：\nsystemctl --user daemon-reload\nsystemctl --user enable --now {filename}\n停止：systemctl --user disable --now {filename}\nLinux 登录退出后是否继续运行取决于用户 linger 设置，请本机用户自行决定。WSL2 需先启用 systemd；未启用时使用 --run 前台运行。\n'
    if not (root/filename).exists():private_write(root/filename,data)
    if not (root/'START.txt').exists():private_write(root/'START.txt',steps.encode())
    print('仅生成独立服务模板及 START.txt；尚未安装或启动服务。先前台验证，再由本机用户按说明启用。')

def run(root,c):
    cr=credential(root,c)
    info=request(c['url']+'/api/members',token=cr['token'])
    m=next((m for m in info['members'] if m['id']==c['device_id']),{})
    if not m.get('permissions',{}).get('execute') or c['type']=='muse':raise ValueError('此设备没有 Codex 任务执行权限')
    if not (root/'worker.py').is_file():raise ValueError('请先执行 --prepare')
    env=dict(os.environ,AICOLLABHUB_CREDENTIAL_FILE=str(root/'credentials.json'));env.pop('AICOLLABHUB_TOKEN',None);env.pop('AICOLLABHUB_URL',None)
    return subprocess.call([sys.executable,str(root/'worker.py'),'--workspace',str(root/'workspace'),'--state-dir',str(root/'state'),
        '--enable-execution','--sandbox','read-only','--timeout','900','--concurrency','1'],env=env)

def setup(root,c):
    """Resume safely: never exchange a used invitation or replace existing credentials."""
    print('1/4 检查运行环境…');check(c)
    if (root/'credentials.json').exists() or (root/'credentials.json').is_symlink():
        credential(root,c)
        print('2/4 已有本设备凭证，跳过配对。')
    else:
        print('2/4 请在此终端输入网页中的一次性配对码。')
        pair(root,c)
    print('3/4 验证身份与连接…');status(root,c)
    print('4/4 自动下载客户端并生成配置…');prepare(root,c)
    print('接入配置已完成。配置目录：'+str(root))
    print('连接器配置：'+str(root/'connector.example.json'))
    print('此检查不是后台常驻连接；需执行任务的 Codex 设备请继续按网页启动，Muse 请在对应账号添加连接器。')

def connect(root,c):
    """One explicit foreground action; never installs a background service."""
    setup(root,c)
    print('接入已准备好；不会自动执行任务。执行权限与本机启动必须分别明确启用。')
    print('可把 connector.example.json 导入支持 stdio MCP 的应用；审查后使用 --run 手动启动执行器。')
    return 0

def bundle_main():
    import zipfile
    try:
        with zipfile.ZipFile(sys.argv[0]) as z:
            c=validate_config(json.loads(z.read('device-config.json')))
        root=Path(sys.argv[0]).resolve().parent/c['device_id']
        parser=argparse.ArgumentParser(description='AI Group 接入包：隐藏输入配对码，自动完成配置。')
        selected=parser.add_mutually_exclusive_group()
        selected.add_argument('--check',action='store_true',help='只检查环境，不配对或启动')
        selected.add_argument('--run',action='store_true',help='审查权限后显式启动已配对设备的受限执行器')
        args=parser.parse_args()
        if args.check:check(c);return 0
        if args.run:return run(root,c)
        return connect(root,c)
    except (RuntimeError,ValueError) as e:
        print(str(e),file=sys.stderr);return 1
    except (OSError,KeyError,zipfile.BadZipFile):
        print('接入未完成，请检查环境、HTTPS、邀请有效期与文件权限。',file=sys.stderr);return 1

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    a=p.add_mutually_exclusive_group(required=True)
    for name in ('check','pair','status','prepare','run','setup','connect'):a.add_argument('--'+name,action='store_true')
    args=p.parse_args();c=config(args.config);root=args.config.resolve().parent/c['device_id']
    if args.check:check(c)
    elif args.pair:check(c);pair(root,c)
    elif args.status:status(root,c)
    elif args.prepare:prepare(root,c)
    elif args.setup:setup(root,c)
    elif args.connect:return connect(root,c)
    else:return run(root,c)
    return 0
if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,ValueError) as e:
        print(str(e),file=sys.stderr)
        sys.exit(1)
    except (OSError,KeyError):
        # Generic here prevents a network/filesystem exception echoing private payloads.
        print('接入未完成：请核对依赖、HTTPS、邀请有效期、文件权限及设备配置。已有服务未被修改；不要连续重复兑换同一配对码。',file=sys.stderr)
        sys.exit(1)
