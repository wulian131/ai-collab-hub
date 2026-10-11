#!/usr/bin/env python3
"""Reproducible local startup using only the Python standard library."""
import argparse
import json
import os
from pathlib import Path
import sys
from server import initialize,Room,make_server

def prepare(directory):
    directory=directory.resolve()
    if not (directory/'members.json').exists():
        if directory.exists() and any(directory.iterdir()):
            raise ValueError('数据目录非空但未初始化，拒绝覆盖；请检查路径。')
        initialize(directory,roster=[dict(id='owner',name='管理员',role='human')])
    if os.name=='posix' and (directory/'members.json').stat().st_mode & 0o077:
        raise ValueError('成员配置权限过宽；请设为仅当前用户可读写。')
    return directory

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('start','init','login'))
    p.add_argument('--data-dir',type=Path,default=Path('private-data'))
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8787)
    args=p.parse_args();os.umask(0o077)
    if args.action=='login':
        if not sys.stdout.isatty():raise ValueError('管理员凭证仅在交互终端显示，不写管道或日志。')
        path=args.data_dir/'enrollment.json'
        if path.is_symlink() or path.stat().st_mode & 0o077:raise ValueError('凭证文件不安全。')
        print('请将下面的管理员凭证粘贴到网页登录框，不要交给模型或发进群聊：')
        print(json.loads(path.read_text())['owner']);return
    directory=prepare(args.data_dir)
    if args.action=='init':return
    room=Room(directory/'room.db',json.loads((directory/'members.json').read_text()))
    http=make_server(room,args.host,args.port)
    print('群聊已启动：http://'+('localhost' if args.host=='0.0.0.0' else args.host)+':'+str(args.port)+'/',flush=True)
    print('在另一个本机终端运行 python3 manage.py login 查看管理员凭证。',flush=True)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close()
if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,json.JSONDecodeError):
        print('启动未完成：检查数据目录、权限、端口和初始化状态。已有数据未被覆盖。',file=sys.stderr);sys.exit(1)
