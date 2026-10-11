#!/usr/bin/env python3
"""Room HTTP client, CLI and newline-delimited stdio MCP adapter."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TOOLS = [
    ('group_members', '查看群成员和当前身份', {}),
    ('group_read', '查看最近群消息，after 用于增量读取', {'after': {'type': 'integer', 'minimum': 0}}),
    ('group_send', '向群发消息。普通消息不派单；显式 target/targets 才派单。AI 派发需关联 reply_to；重试沿用 key。',
     {'source': {'type':'string','enum':['user','codex','chatgpt'],'description':'仅管理员身份填写：本人输入=user，Codex代发=codex，ChatGPT代发=chatgpt；AI成员不可设置'}, 'text': {'type': 'string'}, 'target': {'type': 'string'},
      'leader': {'type': 'string'}, 'targets': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string'}}, 'broadcast': {'type': 'boolean'},
      'execution': {'type': 'object', 'description': '仅用户可指定：成员 ID 或 * 对应 {model, reasoning_effort}，后续协作继承', 'additionalProperties': {'type': 'object', 'properties': {'model': {'type': 'string'}, 'reasoning_effort': {'type': 'string', 'enum': ['none','minimal','low','medium','high','xhigh','max','ultra']}}, 'additionalProperties': False}},
      'reply_to': {'type': 'integer'}, 'key': {'type': 'string'}}),
    ('group_jobs', '查看任务状态', {}),
    ('group_claim', '领取派给当前身份的下一个任务。仅在准备执行时调用。', {}),
    ('group_progress', '开始实际执行前报告处理中；重复调用不重复回执。', {'job_id': {'type': 'integer'}, 'lease': {'type': 'string'}}),
    ('group_renew', '续租当前活跃任务，必须携带本任务的 job_id 和 lease，不在日志或群消息中输出 lease。', {'job_id': {'type': 'integer'}, 'lease': {'type': 'string'}}),
    ('group_finish', '结束自己领取的任务并回群；需要领取结果中的 job_id 和 lease。',
     {'job_id': {'type': 'integer'}, 'lease': {'type': 'string'},
      'text': {'type': 'string'}, 'failed': {'type': 'boolean'}, 'completed': {'type': 'boolean'}, 'blocked': {'type': 'boolean'},
      'delegates': {'type': 'array', 'maxItems': 20, 'items': {'type': 'object',
                    'properties': {'target': {'type': 'string'}, 'text': {'type': 'string'}},
                    'required': ['target', 'text'], 'additionalProperties': False}}}),
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Client:
    def __init__(self):
        credentials = {}
        credential_file = os.environ.get('AICOLLABHUB_CREDENTIAL_FILE')
        if credential_file:
            credentials = json.loads(Path(credential_file).read_text())
        self.url = os.environ.get('AICOLLABHUB_URL', credentials.get('url', '')).rstrip('/')
        self.token = os.environ.get('AICOLLABHUB_TOKEN', credentials.get('token', ''))
        if not self.url or not self.token:
            raise ValueError('设置 AICOLLABHUB_URL/AICOLLABHUB_TOKEN，或 AICOLLABHUB_CREDENTIAL_FILE 指向凭证 JSON')
        parsed = urlsplit(self.url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('群聊 URL 格式无效')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('远程连接需使用 HTTPS；无域名时请使用 SSH 隧道到 localhost')


    def call(self, method, path, payload=None):
        req = urllib.request.Request(self.url + path, method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        # No proxy is needed for SSH loopback tunnels.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()) if urlsplit(self.url).hostname in ('localhost', '127.0.0.1', '::1') else urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(req, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get('error', '请求失败')
            except ValueError:
                message = '请求失败'
            raise RuntimeError(str(error.code) + ': ' + message) from None

    def tool(self, name, arguments):
        if name == 'group_members':
            return self.call('GET', '/api/members')
        if name == 'group_read':
            return self.call('GET', '/api/messages?after=' + str(int(arguments.get('after', 0))))
        if name == 'group_jobs':
            return self.call('GET', '/api/jobs')
        if name == 'group_send':
            return self.call('POST', '/api/messages', arguments)
        if name == 'group_claim':
            return self.call('POST', '/api/jobs/claim', {})
        if name == 'group_progress':
            return self.call('POST', '/api/jobs/progress', arguments)
        if name == 'group_renew':
            return self.call('POST', '/api/jobs/renew', arguments)
        if name == 'group_finish':
            return self.call('POST', '/api/jobs/finish', arguments)
        raise ValueError('未知工具: ' + name)


def mcp():
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('JSON-RPC request must be an object')
            if 'id' not in request:
                continue
            method = request.get('method')
            params = request.get('params', {})
            if method == 'initialize':
                supported = ['2024-11-05', '2025-03-26', '2025-06-18']
                version = params.get('protocolVersion')
                result = {'protocolVersion': version if version in supported else supported[-1],
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'ai-group', 'version': '0.1.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': [{'name': name, 'description': description,
                    'inputSchema': {'type': 'object', 'properties': props,
                        'required': ['text'] if name == 'group_send' else
                            ['job_id', 'lease', 'text'] if name == 'group_finish' else ['job_id','lease'] if name in ('group_progress','group_renew') else ['file_id'] if 'file_id' in props else [],
                        'additionalProperties': False}} for name, description, props in TOOLS]}
            elif method == 'tools/call':
                try:
                    value = Client().tool(params['name'], params.get('arguments', {}))
                    result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
                except (ValueError, RuntimeError, KeyError, OSError) as error:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                                  'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}, ensure_ascii=False), flush=True)
        except (ValueError, TypeError, KeyError) as error:
            print(json.dumps({'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) else None,
                              'error': {'code': -32600, 'message': str(error)}}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['mcp', 'members', 'read', 'send', 'jobs', 'claim', 'progress', 'renew', 'finish'])
    parser.add_argument('--text')
    parser.add_argument('--target')
    parser.add_argument('--leader')
    parser.add_argument('--execution', help='模型设置 JSON 对象：{"*":{"model":"模型ID","reasoning_effort":"high"}}')
    parser.add_argument('--incomplete', action='store_true')
    parser.add_argument('--completed', choices=['true','false'])
    parser.add_argument('--blocked', action='store_true')
    parser.add_argument('--targets', nargs='+')
    parser.add_argument('--broadcast', action='store_true')
    parser.add_argument('--reply-to', type=int)
    parser.add_argument('--after', type=int, default=0)
    parser.add_argument('--key')
    parser.add_argument('--job-id', type=int)
    parser.add_argument('--lease')
    parser.add_argument('--failed', action='store_true')
    parser.add_argument('--delegates', '--delegates-json', dest='delegates', help='JSON 数组，例如 [{"target":"test-cloud-01","text":"请评审"}]')
    args = parser.parse_args()
    if args.command == 'mcp':
        mcp()
        return
    payload = {k: v for k, v in vars(args).items() if k != 'command' and v is not None}
    payload.pop('incomplete', None)
    if args.command == 'finish':
        payload['completed'] = args.completed == 'true' if args.completed is not None else not args.incomplete
    if args.delegates:
        payload['delegates'] = json.loads(args.delegates)
    if args.execution:
        payload['execution'] = json.loads(args.execution)
    if args.command in ('send', 'finish'):
        payload['text'] = args.text if args.text is not None else sys.stdin.read()
    try:
        print(json.dumps(Client().tool('group_' + args.command, payload), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
